import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.mail_recovery import AppleRecoveryLink, MailRecoveryError, extract_apple_recovery_link, recovery_mailboxes
from scripts.controlled_email_recovery import run_controlled_email_password_change
from scripts.controlled_security_rollback import run_controlled_security_rollback


PASSWORD = "EmailRecoveryPass1@"
RECIPIENT = "rescue@example.test"


def message(kind="password_reset", auth=True, recipient=RECIPIENT, url="https://iforgot.apple.com/reset/token",
            account=""):
    terms = {
        "password_reset": "Reset your password by opening this link.",
        "security_rollback": "Click to return to your previous security settings and turn off two-factor authentication.",
    }[kind]
    authentication = "Authentication-Results: mx.example; dkim=pass header.d=apple.com; dmarc=pass header.from=apple.com\n" if auth else ""
    account_line = f"{account}\n" if account else ""
    return (
        "From: Apple <no_reply@apple.com>\n"
        f"To: {recipient}\n"
        "Date: Mon, 01 Sep 2026 12:00:00 +0000\n"
        "Message-ID: <mail-recovery-test@example.test>\n"
        f"{authentication}"
        f'{"DKIM-Signature: v=1; d=apple.com; s=fixture;" if auth else ""}\n'
        "Content-Type: text/plain; charset=utf-8\n\n"
        f"{terms}\n{account_line}{url}\n"
    ).encode()


class FakePanel:
    def __init__(self):
        self.info = {
            "username": "fixture@example.test", "password": "CURRENT_PASSWORD", "enable": False,
            "enable_auto_update_password": False, "enable_delete_devices": False, "check_interval": 30,
            "proxy_available": True, "proxy_id": 7, "proxy_protocol": "socks5", "proxy_content": "proxy.test:1080",
            "email_recovery": {"configured": True, "email": RECIPIENT, "host": "mail.example.test", "port": 993,
                               "username": RECIPIENT, "password": "MAIL_PASSWORD", "tls": True},
        }
        self.disabled = False

    async def task_info(self, account_id):
        return dict(self.info)

    async def report(self, username, status, message, password=None):
        self.info["password"] = password

    async def finalize_recovery(self, account_id, password_sha256):
        self.info.update({"enable": True, "enable_auto_update_password": False,
                          "enable_delete_devices": False, "check_interval": 30})

    async def disable_account(self, username):
        self.disabled = True
        self.info.update({"enable": False, "enable_auto_update_password": False,
                          "enable_delete_devices": False})


class FakeAccountClient:
    def __init__(self, request_failures=0):
        self.actions = []
        self.payloads = []
        self.request_failures = request_failures

    async def run(self, action, payload):
        self.actions.append(action)
        self.payloads.append(dict(payload))
        if action == "check":
            return {"status": True, "state": "normal", "_job_id": f"job-{len(self.actions)}"}
        if action == "complete_email_reset":
            return {"status": True, "password": payload["new_password"], "password_change_confirmed": True,
                    "_job_id": f"job-{len(self.actions)}"}
        if action == "complete_security_rollback":
            return {"status": True, "security_rollback_confirmed": True,
                    "password": payload["new_password"], "password_change_confirmed": True,
                    "_job_id": f"job-{len(self.actions)}"}
        if action == "request_email_reset" and self.request_failures:
            self.request_failures -= 1
            return {"status": False, "error_code": "captcha_rejected", "retryable": True,
                    "password_submitted": False, "_job_id": f"job-{len(self.actions)}"}
        return {"status": True, "_job_id": f"job-{len(self.actions)}"}


class ExpiredRollbackAccountClient(FakeAccountClient):
    async def run(self, action, payload):
        self.actions.append(action)
        self.payloads.append(dict(payload))
        return {"status": False, "error_code": "security_rollback_link_expired", "_job_id": "job-expired"}


class PreClickRollbackFailureAccountClient(FakeAccountClient):
    async def run(self, action, payload):
        self.actions.append(action)
        self.payloads.append(dict(payload))
        return {
            "status": False, "error_code": "page_changed", "stage": "security_rollback",
            "password_submitted": False, "_job_id": "job-pre-click",
        }


class FakeMailbox:
    def __init__(self, kind="password_reset", error="", existing=False):
        self.kind, self.error, self.existing, self.calls = kind, error, existing, []

    async def find_link(self, config, expected_kind, not_before, expected_account=None,
                        exclude_message_ids=None):
        self.calls.append(f"lookup:{expected_kind}")
        if self.error:
            raise MailRecoveryError(self.error)
        if self.existing:
            return AppleRecoveryLink(expected_kind, "https://iforgot.apple.com/reset/token", "<fixture>")
        return None

    async def wait_for_link(self, config, expected_kind, not_before, timeout_seconds=180, poll_seconds=10,
                            expected_account=None, exclude_message_ids=None):
        self.calls.append(expected_kind)
        if self.error:
            raise MailRecoveryError(self.error)
        return AppleRecoveryLink(expected_kind, "https://iforgot.apple.com/reset/token", "<fixture>")

    async def wait_for_security_rollback_confirmation(self, config, not_before, timeout_seconds=90,
                                                       expected_account=None):
        self.calls.append("security_rollback_confirmation")


class FakeRollbackStore:
    def __init__(self):
        self.seen = set()

    def rollback_seen_message_ids(self, account_id):
        return set(self.seen)

    def mark_rollback_seen(self, account_id, message_id):
        self.seen.add(message_id)


class FakeImap:
    def __init__(self, login_error=False, select_status="OK"):
        self.login_error = login_error
        self.select_status = select_status
        self.logged_out = False

    def login(self, username, password):
        if self.login_error:
            import imaplib
            raise imaplib.IMAP4.error("invalid credentials")
        return "OK", []

    def select(self, mailbox, readonly=False):
        return self.select_status, []

    def logout(self):
        self.logged_out = True


class MailRecoveryTest(unittest.TestCase):
    def test_mailbox_connectivity_checks_only_login_and_readonly_inbox(self):
        from app.mail_recovery import BillionMailClient

        fake = FakeImap()
        config = {"email": RECIPIENT, "host": "mail.example.test", "port": 993,
                  "username": RECIPIENT, "password": "MAIL_PASSWORD", "tls": True}
        public_dns = [(2, 1, 6, '', ('93.184.216.34', 993))]
        with patch("app.mail_recovery.socket.getaddrinfo", return_value=public_dns), \
                patch("app.mail_recovery.imaplib.IMAP4_SSL", return_value=fake):
            result = asyncio.run(BillionMailClient().test_connection(config))
        self.assertIsNone(result)
        self.assertTrue(fake.logged_out)

    def test_mailbox_connectivity_returns_safe_failure_codes(self):
        from app.mail_recovery import BillionMailClient

        config = {"email": RECIPIENT, "host": "mail.example.test", "port": 993,
                  "username": RECIPIENT, "password": "MAIL_PASSWORD", "tls": True}
        public_dns = [(2, 1, 6, '', ('93.184.216.34', 993))]
        with patch("app.mail_recovery.socket.getaddrinfo", return_value=public_dns), \
                patch("app.mail_recovery.imaplib.IMAP4_SSL", return_value=FakeImap(login_error=True)):
            with self.assertRaisesRegex(MailRecoveryError, "mailbox_auth_failed"):
                asyncio.run(BillionMailClient().test_connection(config))
        with self.assertRaisesRegex(MailRecoveryError, "mailbox_config_invalid"):
            asyncio.run(BillionMailClient().test_connection({"host": "mail.example.test"}))

    def test_mailbox_private_and_link_local_addresses_are_rejected_before_connect(self):
        from app.mail_recovery import BillionMailClient

        config = {"email": RECIPIENT, "host": "mail.example.test", "port": 993,
                  "username": RECIPIENT, "password": "MAIL_PASSWORD", "tls": True}
        for address in ("127.0.0.1", "10.0.0.8", "169.254.169.254", "::1", "fc00::1"):
            dns = [(2, 1, 6, '', (address, 993))]
            with self.subTest(address=address), patch("app.mail_recovery.socket.getaddrinfo", return_value=dns), \
                    patch("app.mail_recovery.imaplib.IMAP4_SSL") as connect:
                with self.assertRaisesRegex(MailRecoveryError, "mailbox_address_blocked"):
                    asyncio.run(BillionMailClient().test_connection(config))
                connect.assert_not_called()

    @patch("app.mail_recovery._verify_dkim", return_value=True)
    def test_parser_accepts_only_authenticated_apple_mail_for_expected_recipient_and_kind(self, _verify):
        link = extract_apple_recovery_link(
            message(), RECIPIENT, "password_reset", datetime(2026, 9, 1, 11, 59, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(link)
        self.assertEqual("password_reset", link.kind)
        self.assertIsNone(extract_apple_recovery_link(message(auth=False), RECIPIENT, "password_reset"))
        self.assertIsNone(extract_apple_recovery_link(message(recipient="other@example.test"), RECIPIENT, "password_reset"))
        self.assertIsNone(extract_apple_recovery_link(message(kind="security_rollback"), RECIPIENT, "password_reset"))
        self.assertIsNone(extract_apple_recovery_link(message(url="https://evil.example/reset"), RECIPIENT, "password_reset"))

    @patch("app.mail_recovery._verify_dkim", return_value=True)
    def test_parser_requires_exact_account_identifier_when_requested(self, _verify):
        raw = message(account="fixture@example.test")
        self.assertIsNotNone(extract_apple_recovery_link(
            raw, RECIPIENT, "password_reset", expected_account="fixture@example.test",
        ))
        self.assertIsNone(extract_apple_recovery_link(
            raw, RECIPIENT, "password_reset", expected_account="other@example.test",
        ))

    @patch("app.mail_recovery._verify_dkim", return_value=True)
    def test_parser_prefers_recovery_action_anchor_over_generic_apple_homepage(self, _verify):
        raw = message(kind="security_rollback", url="https://account.apple.com/")
        raw = raw.replace(
            b"Content-Type: text/plain; charset=utf-8",
            b"Content-Type: text/html; charset=utf-8",
        ).replace(
            b"Click to return to your previous security settings and turn off two-factor authentication.\nhttps://account.apple.com/",
            b'Click to return to your previous security settings and turn off two-factor authentication.'
            b'<a href="https://account.apple.com/">Apple Account</a>'
            b'<a href="https://iforgot.apple.com/reset/fixture-token">Turn off two-factor authentication</a>',
        )
        link = extract_apple_recovery_link(raw, RECIPIENT, "security_rollback")
        self.assertIsNotNone(link)
        self.assertEqual("https://iforgot.apple.com/reset/fixture-token", link.url)

    @patch("app.mail_recovery._verify_dkim", return_value=True)
    def test_parser_accepts_chinese_return_to_previous_security_settings_anchor(self, _verify):
        raw = message(kind="security_rollback", url="https://account.apple.com/")
        raw = raw.replace(
            b"Content-Type: text/plain; charset=utf-8",
            b"Content-Type: text/html; charset=utf-8",
        ).replace(
            b"Click to return to your previous security settings and turn off two-factor authentication.\nhttps://account.apple.com/",
            b'Click to return to your previous security settings and turn off two-factor authentication.'
            b'<a href="https://iforgot.apple.com/withdraw/fixture-token">\xe8\xbf\x94\xe5\x9b\x9e\xe4\xb9\x8b\xe5\x89\x8d\xe7\x9a\x84\xe5\xae\x89\xe5\x85\xa8\xe8\xae\xbe\xe7\xbd\xae</a>',
        )
        link = extract_apple_recovery_link(raw, RECIPIENT, "security_rollback")
        self.assertIsNotNone(link)
        self.assertEqual("https://iforgot.apple.com/withdraw/fixture-token", link.url)

    def test_security_rollback_rejects_unlabelled_support_or_sign_in_urls(self):
        raw = message(kind="security_rollback", url="https://support.apple.com/en-us/fixture")
        self.assertIsNone(extract_apple_recovery_link(raw, RECIPIENT, "security_rollback"))

    def test_parser_can_verify_apple_dkim_when_mailbox_has_no_authentication_results(self):
        raw = message(auth=False)
        signed = raw.replace(b"Date:", b"DKIM-Signature: v=1; d=apple.com; s=fixture;\nDate:")
        with patch("app.mail_recovery._verify_dkim", return_value=True):
            self.assertIsNotNone(extract_apple_recovery_link(signed, RECIPIENT, "password_reset"))
        with patch("app.mail_recovery._verify_dkim", return_value=False):
            self.assertIsNone(extract_apple_recovery_link(signed, RECIPIENT, "password_reset"))

    def test_recovery_mailboxes_only_adds_server_advertised_junk_or_spam(self):
        listing = [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\Junk) "/" "Junk"',
            b'(\\HasNoChildren) "/" "Sent"',
            b'(\\HasNoChildren \\Spam) "/" "Spam"',
        ]
        self.assertEqual(["INBOX", "Junk", "Spam"], recovery_mailboxes(listing))

    def test_email_password_recovery_changes_once_then_fresh_checks(self):
        panel, account, mailbox = FakePanel(), FakeAccountClient(), FakeMailbox()
        with tempfile.TemporaryDirectory() as directory:
            result = asyncio.run(run_controlled_email_password_change(
                1, Path(directory), panel=panel, account_client=account, mailbox_client=mailbox,
                password_factory=lambda: PASSWORD,
            ))
            self.assertEqual("success", result["status"])
            self.assertEqual(["request_email_reset", "complete_email_reset", "check"], account.actions)
            self.assertEqual(["lookup:password_reset", "password_reset"], mailbox.calls)
            self.assertNotIn(PASSWORD, repr(result))
            self.assertEqual([], list(Path(directory).glob("*.secret")))

    def test_email_request_retries_only_pre_submission_technical_failures(self):
        panel, account, mailbox = FakePanel(), FakeAccountClient(request_failures=2), FakeMailbox()
        sleeps = []

        async def no_sleep(delay):
            sleeps.append(delay)

        with tempfile.TemporaryDirectory() as directory:
            result = asyncio.run(run_controlled_email_password_change(
                1, Path(directory), panel=panel, account_client=account, mailbox_client=mailbox,
                password_factory=lambda: PASSWORD, retry_delays=(60, 300), sleeper=no_sleep,
            ))
        self.assertEqual("success", result["status"])
        self.assertEqual([60, 300], sleeps)
        self.assertEqual(["request_email_reset", "request_email_reset", "request_email_reset", "complete_email_reset", "check"], account.actions)

    def test_direct_request_probe_omits_proxy_and_stops_before_mailbox_or_password(self):
        panel, account, mailbox = FakePanel(), FakeAccountClient(), FakeMailbox()
        with tempfile.TemporaryDirectory() as directory:
            result = asyncio.run(run_controlled_email_password_change(
                1, Path(directory), panel=panel, account_client=account, mailbox_client=mailbox,
                password_factory=lambda: PASSWORD, allow_direct=True, request_only=True,
            ))
            self.assertEqual("pending", result["status"])
            self.assertEqual("email_requested", result["stage"])
            self.assertTrue(result["escrow_retained"])
            self.assertEqual(["request_email_reset"], account.actions)
            self.assertEqual(["lookup:password_reset"], mailbox.calls)
            self.assertFalse(panel.disabled)
            self.assertFalse(panel.info["enable"])
            self.assertNotIn("proxy_id", account.payloads[0])
            self.assertNotIn("proxy_protocol", account.payloads[0])
            self.assertNotIn("proxy_content", account.payloads[0])

    def test_resume_existing_link_never_requests_another_email(self):
        panel, account, mailbox = FakePanel(), FakeAccountClient(), FakeMailbox(existing=True)
        with tempfile.TemporaryDirectory() as directory:
            escrow_dir = Path(directory)
            (escrow_dir / "account-1-fixture.secret").write_text(PASSWORD, encoding="utf-8")
            (escrow_dir / "account-1-fixture.secret").chmod(0o600)
            result = asyncio.run(run_controlled_email_password_change(
                1, escrow_dir, panel=panel, account_client=account, mailbox_client=mailbox,
                resume_existing_link=True,
            ))
        self.assertEqual("success", result["status"])
        self.assertEqual(["complete_email_reset", "check"], account.actions)
        self.assertEqual(["lookup:password_reset"], mailbox.calls)

    def test_existing_recent_reset_mail_is_used_without_requesting_another(self):
        panel, account, mailbox = FakePanel(), FakeAccountClient(), FakeMailbox(existing=True)
        with tempfile.TemporaryDirectory() as directory:
            result = asyncio.run(run_controlled_email_password_change(
                1, Path(directory), panel=panel, account_client=account, mailbox_client=mailbox,
                password_factory=lambda: PASSWORD,
            ))
        self.assertEqual("success", result["status"])
        self.assertEqual(["complete_email_reset", "check"], account.actions)
        self.assertEqual(["lookup:password_reset"], mailbox.calls)

    def test_mailbox_failure_freezes_and_retains_escrow(self):
        panel, account, mailbox = FakePanel(), FakeAccountClient(), FakeMailbox(error="recovery_email_timeout")
        with tempfile.TemporaryDirectory() as directory:
            result = asyncio.run(run_controlled_email_password_change(
                1, Path(directory), panel=panel, account_client=account, mailbox_client=mailbox,
                password_factory=lambda: PASSWORD,
            ))
            self.assertEqual("recovery_email_timeout", result["error_code"])
            self.assertTrue(panel.disabled)
            self.assertEqual(1, len(list(Path(directory).glob("*.secret"))))

    def test_security_rollback_requires_confirmed_link_and_fresh_login(self):
        panel, account, mailbox = FakePanel(), FakeAccountClient(), FakeMailbox(kind="security_rollback")
        result = asyncio.run(run_controlled_security_rollback(
            1, panel=panel, account_client=account, mailbox_client=mailbox,
        ))
        self.assertEqual("success", result["status"])
        self.assertEqual(["lookup:security_rollback", "security_rollback"], mailbox.calls)
        self.assertEqual(["complete_security_rollback"], account.actions)
        self.assertNotEqual("CURRENT_PASSWORD", panel.info["password"])

    def test_security_rollback_allows_explicit_direct_mode(self):
        panel, account, mailbox = FakePanel(), FakeAccountClient(), FakeMailbox(kind="security_rollback")
        panel.info.update({
            "connection_mode": "direct", "proxy_available": True,
            "proxy_id": None, "proxy_protocol": None, "proxy_content": None,
        })
        result = asyncio.run(run_controlled_security_rollback(
            1, panel=panel, account_client=account, mailbox_client=mailbox,
        ))
        self.assertEqual("success", result["status"])
        self.assertEqual(["complete_security_rollback"], account.actions)
        self.assertNotIn("proxy_id", account.payloads[0])

    def test_security_rollback_expired_link_stops_without_waiting_for_mail_confirmation(self):
        panel, account, mailbox = FakePanel(), ExpiredRollbackAccountClient(), FakeMailbox(
            kind="security_rollback", existing=True,
        )
        result = asyncio.run(run_controlled_security_rollback(
            1, panel=panel, account_client=account, mailbox_client=mailbox,
        ))
        self.assertEqual("failed", result["status"])
        self.assertEqual("security_rollback", result["stage"])
        self.assertEqual("security_rollback_link_expired", result["error_code"])
        self.assertEqual(["lookup:security_rollback"], mailbox.calls)

    def test_incomplete_password_form_does_not_wait_for_confirmation_or_consume_link(self):
        class IncompleteForm(FakeAccountClient):
            async def run(self, action, payload):
                return {'status': False, 'error_code': 'password_form_incomplete',
                        'stage': 'password_form', 'password_submitted': False}
        mailbox = FakeMailbox(kind='security_rollback', existing=True)
        store = FakeRollbackStore()
        with tempfile.TemporaryDirectory() as directory:
            result = asyncio.run(run_controlled_security_rollback(
                1, panel=FakePanel(), account_client=IncompleteForm(), mailbox_client=mailbox,
                store=store, escrow_dir=Path(directory)))
        self.assertEqual(result['error_code'], 'password_form_incomplete')
        self.assertEqual(mailbox.calls, ['lookup:security_rollback'])
        self.assertEqual(store.seen, set())

    def test_security_rollback_pre_click_failure_preserves_driver_error_and_link(self):
        panel = FakePanel()
        account = PreClickRollbackFailureAccountClient()
        mailbox = FakeMailbox(kind="security_rollback", existing=True)
        store = FakeRollbackStore()
        with tempfile.TemporaryDirectory() as directory:
            result = asyncio.run(run_controlled_security_rollback(
                1, panel=panel, account_client=account, mailbox_client=mailbox,
                store=store, escrow_dir=Path(directory),
            ))
        self.assertEqual("failed", result["status"])
        self.assertEqual("security_rollback", result["stage"])
        self.assertEqual("page_changed", result["error_code"])
        self.assertEqual(["lookup:security_rollback"], mailbox.calls)
        self.assertEqual(set(), store.seen)


if __name__ == "__main__":
    unittest.main()
