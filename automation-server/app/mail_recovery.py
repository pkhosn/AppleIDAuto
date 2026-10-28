"""Fail-closed BillionMail IMAPS retrieval for Apple recovery messages.

This module never logs message bodies, credentials, or recovery URLs.  A URL
is returned only to the internal controlled job that immediately opens it in
the already-proxied account driver.
"""

import asyncio
import email
import imaplib
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlparse


APPLE_HOSTS = {
    "apple.com", "iforgot.apple.com", "appleid.apple.com", "account.apple.com", "idmsa.apple.com",
}
URL_RE = re.compile(r"https://[^\s<>\"']+", re.I)
MAILBOX_NAME_RE = re.compile(r'(?:"((?:[^"\\]|\\.)*)"|(\S+))\s*$')
DKIM_DOMAIN_RE = re.compile(r"(?:^|;)\s*d=([^;\s]+)", re.I)
RECOVERY_KIND_TERMS = {
    "password_reset": ("reset your password", "password reset", "重设密码", "重置密码"),
    "security_rollback": (
        "previous security settings", "return to previous security settings",
        "turn off two-factor authentication", "lower your account security",
        "恢复以前的安全设置", "返回之前的安全设置", "返回以前的安全设置", "关闭双重认证",
    ),
}


class _AnchorCollector(HTMLParser):
    """Collect visible text and href pairs without retaining message bodies."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "a" or self._href:
            return
        self._href = next((str(value or "") for key, value in attrs if key.lower() == "href"), "")
        self._parts = []

    def handle_data(self, data: str):
        if self._href:
            self._parts.append(data)

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and self._href:
            self.links.append((" ".join(self._parts).strip(), self._href))
            self._href, self._parts = "", []


class MailRecoveryError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AppleRecoveryLink:
    kind: str
    url: str
    message_id: str
    # IMAP UID is kept in memory for the current job only; it is never logged.
    message_uid: str = ""


def _address_domains(value: str) -> set[str]:
    return {address.rsplit("@", 1)[-1].lower() for _, address in getaddresses([value]) if "@" in address}


def _apple_domain(value: str) -> bool:
    domain = value.strip().lower().rstrip(".")
    return domain == "apple.com" or domain.endswith(".apple.com")


def _verify_dkim(raw_message: bytes, index: int = 0) -> bool:
    """Verify the selected signature, never an untrusted authentication header."""
    try:
        import dkim
        return bool(dkim.DKIM(raw_message).verify(idx=index, dnsfunc=dkim.get_txt))
    except Exception:
        return False


def _trusted_apple_sender(raw_message: bytes, message) -> bool:
    senders = getaddresses(message.get_all("From", []))
    if len(senders) != 1 or "@" not in senders[0][1]:
        return False
    if not _apple_domain(senders[0][1].rsplit("@", 1)[-1]):
        return False
    for index, signature in enumerate(message.get_all("DKIM-Signature", [])):
        match = DKIM_DOMAIN_RE.search(str(signature))
        if match and _apple_domain(match.group(1)) and _verify_dkim(raw_message, index):
            return True
    return False


def _recipient_matches(message, expected: str) -> bool:
    expected = expected.strip().lower()
    headers = ["To", "Delivered-To", "X-Original-To", "X-Envelope-To"]
    return any(expected in {address.lower() for _, address in getaddresses([str(message.get(header, ""))])}
               for header in headers)


def _message_text(message) -> str:
    chunks = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart" or part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            chunks.append(part.get_content())
        except Exception:
            continue
    return unescape("\n".join(str(item) for item in chunks))


def _apple_url(value: str) -> str | None:
    candidate = value.rstrip(".,;:!?)]}\"'")
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or not (host in APPLE_HOSTS or host.endswith(".apple.com")):
        return None
    return candidate


def _message_html_links(message) -> list[tuple[str, str]]:
    """Return Apple HTML links paired with their visible anchor text.

    Apple notification mails include footer and sign-in links before the real
    recovery action.  Pairing URLs with the button text prevents a generic
    apple.com landing page from being treated as a recovery link.
    """
    collector = _AnchorCollector()
    for part in message.walk():
        if part.get_content_maintype() == "multipart" or part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() != "text/html":
            continue
        try:
            collector.feed(str(part.get_content()))
        except Exception:
            continue
    return [(label, url) for label, href in collector.links if (url := _apple_url(href))]


def _fallback_apple_urls(text: str) -> list[str]:
    """Rank non-anchor links so action-shaped URLs beat a generic homepage."""
    urls = []
    for match in URL_RE.findall(text):
        url = _apple_url(match)
        if url and url not in urls:
            urls.append(url)

    def score(url: str) -> tuple[int, int]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.rstrip("/")
        # Do not inspect or log query/path values: recovery URLs may carry
        # one-time secrets.  Only rank their non-sensitive shape.
        return (
            int(host == "iforgot.apple.com") * 4 + int(bool(path)) * 2 + int(bool(parsed.query)),
            -urls.index(url),
        )

    return sorted(urls, key=score, reverse=True)


def recovery_mailboxes(listing) -> list[str]:
    """Return only INBOX and server-advertised Junk/Spam folders.

    Apple recovery mail can be filtered to a Junk folder by a local mail
    server.  Do not enumerate arbitrary folders such as Sent or Trash.
    """
    result = ["INBOX"]
    for item in listing or []:
        line = item.decode("utf-8", errors="ignore") if isinstance(item, bytes) else str(item)
        lowered = line.lower()
        if r"\junk" not in lowered and r"\spam" not in lowered:
            continue
        match = MAILBOX_NAME_RE.search(line)
        name = (match.group(1) or match.group(2)) if match else ""
        name = name.replace('\\"', '"').strip()
        if name and name not in result:
            result.append(name)
    return result


def _account_identifier_matches(text: str, expected_account: str | None) -> bool:
    expected = " ".join(str(expected_account or "").casefold().split())
    if not expected:
        return False
    return expected in " ".join(text.casefold().split())


def extract_apple_recovery_link(raw_message: bytes, expected_recipient: str, expected_kind: str,
                                not_before: datetime | None = None,
                                expected_account: str | None = None) -> AppleRecoveryLink | None:
    message = email.message_from_bytes(raw_message, policy=policy.default)
    if not _trusted_apple_sender(raw_message, message) or not _recipient_matches(message, expected_recipient):
        return None
    if not_before:
        try:
            received = parsedate_to_datetime(str(message.get("Date", "")))
            if received.tzinfo is None:
                received = received.replace(tzinfo=timezone.utc)
            if received < not_before - timedelta(minutes=5):
                return None
        except (TypeError, ValueError, IndexError):
            return None
    text = _message_text(message)
    if expected_account is not None and not _account_identifier_matches(
        f"{message.get('Subject', '')}\n{text}", expected_account
    ):
        return None
    normalized = text.lower()
    terms = RECOVERY_KIND_TERMS.get(expected_kind)
    if not terms or not any(term in normalized for term in terms):
        return None
    for label, url in _message_html_links(message):
        if any(term in label.lower() for term in terms):
            return AppleRecoveryLink(expected_kind, url, str(message.get("Message-ID", ""))[:255])
    # A two-factor rollback is irreversible enough that an unlabelled Apple
    # URL is never acceptable: security-notification mails commonly include
    # Apple Support and sign-in links, but neither changes account security.
    # Password-reset mails may legitimately be plain text, so retain the
    # guarded fallback only for that flow.
    if expected_kind == "security_rollback":
        return None
    for url in _fallback_apple_urls(text):
        return AppleRecoveryLink(expected_kind, url, str(message.get("Message-ID", ""))[:255])
    return None


def is_apple_security_rollback_confirmation(raw_message: bytes, expected_recipient: str,
                                             not_before: datetime | None = None,
                                             expected_account: str | None = None) -> bool:
    """Verify Apple’s post-withdrawal mail without exposing its body or URLs."""
    message = email.message_from_bytes(raw_message, policy=policy.default)
    if not _trusted_apple_sender(raw_message, message) or not _recipient_matches(message, expected_recipient):
        return False
    if not_before:
        try:
            received = parsedate_to_datetime(str(message.get("Date", "")))
            received = received.replace(tzinfo=timezone.utc) if received.tzinfo is None else received
            if received < not_before - timedelta(minutes=5):
                return False
        except (TypeError, ValueError, IndexError):
            return False
    text = _message_text(message)
    if expected_account is not None and not _account_identifier_matches(
        f"{message.get('Subject', '')}\n{text}", expected_account
    ):
        return False
    normalized = " ".join(text.lower().split())
    return any(term in normalized for term in (
        "two-factor authentication has been turned off", "two-factor authentication is turned off",
        "apple 账户双重认证已关闭", "双重认证已关闭", "关闭双重认证并恢复之前的安全设置",
    ))


class BillionMailClient:
    def __init__(self, timeout: float = 12, max_messages: int = 30):
        self.timeout = timeout
        self.max_messages = max_messages

    async def wait_for_link(self, config: dict, expected_kind: str, not_before: datetime,
                            timeout_seconds: int = 180, poll_seconds: int = 10,
                            expected_account: str | None = None,
                            exclude_message_ids: set[str] | None = None) -> AppleRecoveryLink:
        deadline = asyncio.get_running_loop().time() + max(timeout_seconds, 1)
        while True:
            result = await asyncio.to_thread(
                self._find_link, config, expected_kind, not_before, expected_account, exclude_message_ids,
            )
            if result:
                return result
            if asyncio.get_running_loop().time() >= deadline:
                raise MailRecoveryError("recovery_email_timeout")
            await asyncio.sleep(max(poll_seconds, 1))

    async def find_link(self, config: dict, expected_kind: str,
                        not_before: datetime, expected_account: str | None = None,
                        exclude_message_ids: set[str] | None = None) -> AppleRecoveryLink | None:
        """Look up the newest eligible Apple link once, without waiting.

        Recovery jobs use this before asking Apple to send anything.  This
        avoids needlessly invalidating a valid message that is already in the
        owned mailbox.  The result remains internal and is never logged.
        Message-IDs in ``exclude_message_ids`` are already processed and are
        skipped, so a consumed one-time link is never re-used on a later cycle.
        """
        return await asyncio.to_thread(
            self._find_link, config, expected_kind, not_before, expected_account, exclude_message_ids,
        )

    async def wait_for_security_rollback_confirmation(self, config: dict, not_before: datetime,
                                                       timeout_seconds: int = 90,
                                                       expected_account: str | None = None) -> None:
        deadline = asyncio.get_running_loop().time() + max(timeout_seconds, 1)
        while True:
            if await asyncio.to_thread(self._find_security_rollback_confirmation, config, not_before, expected_account):
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise MailRecoveryError("security_rollback_confirmation_timeout")
            await asyncio.sleep(5)

    async def test_connection(self, config: dict) -> None:
        """Verify encrypted saved credentials can log into the inbox.

        This deliberately performs no message search, fetch, mutation, or
        recovery action.  It is safe to invoke from the administrator UI.
        """
        await asyncio.to_thread(self._test_connection, config)

    @staticmethod
    def _validate_config(config: dict) -> None:
        required = ("email", "host", "port", "username", "password")
        if not isinstance(config, dict) or not all(config.get(key) for key in required) or not config.get("tls"):
            raise MailRecoveryError("mailbox_config_invalid")

    def _connect_inbox(self, config: dict):
        self._validate_config(config)
        client = None
        try:
            infos = socket.getaddrinfo(str(config["host"]), int(config["port"]), type=socket.SOCK_STREAM)
            if not infos or any(not ipaddress.ip_address(info[4][0]).is_global for info in infos):
                raise MailRecoveryError("mailbox_address_blocked")
            context = ssl.create_default_context()
            client = imaplib.IMAP4_SSL(str(config["host"]), int(config["port"]), ssl_context=context,
                                       timeout=self.timeout)
            try:
                client.login(str(config["username"]), str(config["password"]))
            except imaplib.IMAP4.error as exc:
                raise MailRecoveryError("mailbox_auth_failed") from exc
            try:
                status, _ = client.select("INBOX", readonly=True)
            except imaplib.IMAP4.error as exc:
                raise MailRecoveryError("mailbox_inbox_unavailable") from exc
            if status != "OK":
                raise MailRecoveryError("mailbox_inbox_unavailable")
            return client
        except MailRecoveryError:
            raise
        except ssl.SSLError as exc:
            raise MailRecoveryError("mailbox_tls_failed") from exc
        except (OSError, ValueError) as exc:
            raise MailRecoveryError("mailbox_connection_failed") from exc

    @staticmethod
    def _logout(client) -> None:
        if client:
            try:
                client.logout()
            except Exception:
                pass

    def _test_connection(self, config: dict) -> None:
        client = None
        try:
            client = self._connect_inbox(config)
        finally:
            self._logout(client)

    def _find_link(self, config: dict, expected_kind: str, not_before: datetime,
                   expected_account: str | None = None,
                   exclude_message_ids: set[str] | None = None) -> AppleRecoveryLink | None:
        excluded = {str(item) for item in (exclude_message_ids or set())}
        client = None
        try:
            client = self._connect_inbox(config)
            try:
                list_status, listing = client.list()
            except imaplib.IMAP4.error:
                list_status, listing = "NO", []
            mailboxes = recovery_mailboxes(listing) if list_status == "OK" else ["INBOX"]
            for mailbox in mailboxes:
                if mailbox != "INBOX":
                    try:
                        status, _ = client.select(mailbox, readonly=True)
                    except imaplib.IMAP4.error:
                        continue
                    if status != "OK":
                        continue
                status, payload = client.search(None, "ALL")
                if status != "OK" or not payload:
                    continue
                identifiers = payload[0].split()[-self.max_messages:]
                for identifier in reversed(identifiers):
                    status, content = client.fetch(identifier, "(RFC822)")
                    if status != "OK" or not content:
                        continue
                    raw = next((item[1] for item in content if isinstance(item, tuple) and isinstance(item[1], bytes)), None)
                    if not raw:
                        continue
                    result = extract_apple_recovery_link(
                        raw, str(config["email"]), expected_kind, not_before,
                        expected_account=expected_account,
                    )
                    if result and result.message_id not in excluded:
                        uid = identifier.decode("ascii", errors="ignore") if isinstance(identifier, bytes) else str(identifier)
                        return AppleRecoveryLink(result.kind, result.url, result.message_id, uid)
            return None
        finally:
            self._logout(client)

    def _find_security_rollback_confirmation(self, config: dict, not_before: datetime,
                                             expected_account: str | None = None) -> bool:
        client = None
        try:
            client = self._connect_inbox(config)
            status, payload = client.search(None, "ALL")
            if status != "OK" or not payload:
                return False
            for identifier in reversed(payload[0].split()[-self.max_messages:]):
                status, content = client.fetch(identifier, "(RFC822)")
                raw = next((item[1] for item in content if isinstance(item, tuple) and isinstance(item[1], bytes)), None) if status == "OK" else None
                if raw and is_apple_security_rollback_confirmation(
                    raw, str(config["email"]), not_before, expected_account=expected_account
                ):
                    return True
            return False
        finally:
            self._logout(client)
