import logging
import json
import hashlib
import hmac
import random
import re
import secrets
import socket
import string
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from dataclasses import dataclass
from itertools import permutations
from pathlib import Path
from queue import Empty, Queue
from urllib.parse import quote, unquote, urljoin, urlparse

from .config import settings


logger = logging.getLogger(__name__)


class FlowError(RuntimeError):
    def __init__(
        self, code: str, message: str, *, stage: str = "", retryable: bool = False,
        password_submitted: bool = False, device_removal_started: bool = False,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.retryable = retryable
        self.password_submitted = password_submitted
        self.device_removal_started = device_removal_started
        self.details = dict(details or {})


def classify_login_state(visible_text: str, current_url: str = "", has_account_nav: bool = False) -> str | None:
    text = visible_text.lower()
    compact_text = re.sub(r"\s+", "", text)
    # Apple can leave the previous sign-in error in a nested/transitioning
    # document while the visible security-question challenge is already ready.
    # The concrete challenge must win over that stale password message.
    if any(term in text for term in (
        "answer your security questions", "security questions to continue",
        "回答您的安全提示问题", "回答你的安全提示问题", "回答安全提示问题", "回答安全问题",
    )):
        return "security_questions"
    if any(term in text for term in (
        "incorrect password", "password is incorrect",
        "check the account information you entered and try again",
        "apple account or password was incorrect",
        "密码不正确", "密码错误", "检查您输入的账户信息并重试",
    )):
        return "password_invalid"
    # Apple shows "cannot verify your identity" both for a rejected sign-in and
    # when repeated automated attempts have triggered throttling.  Distinguish
    # it from password_invalid so the scheduler can stop cleanly instead of
    # misreporting the page as an unrendered challenge.
    if any(term in text for term in (
        "cannot verify your identity", "无法验证你的身份", "无法验证您的身份",
    )):
        return "identity_verification_failed"
    if any(term in text for term in ("account has been locked", "account is locked", "账户已锁定", "账号已锁定")):
        return "locked"
    # A real two-factor challenge always asks for a code.  Do not classify a
    # generic explanation of two-factor security as this state: Apple leaves
    # that explanatory text mounted in old iframe documents during navigation.
    if any(term in text for term in (
        "enter the verification code", "verification code sent", "enter code",
        "enter the code displayed", "enter the code from",
        "输入验证码", "验证码已发送", "输入代码", "输入显示的验证码", "输入验证码以继续",
        # Apple's localized 2FA challenge: "请输入发送到你 iPhone 的验证码。"
        "请输入发送到你", "重新发送验证码", "无法使用你的",
    )):
        return "two_factor"
    # A hidden iframe can retain the preceding Apple Account Security copy
    # after this visible enrollment page is rendered. Prefer the actionable
    # trusted-phone prompt over that stale text.
    if (
        any(term in text for term in (
            "add a trusted phone number", "添加受信任电话号码", "添加可信电话号码",
        ))
        and any(term in text for term in ("cancel", "取消"))
    ):
        return "trusted_phone_prompt"
    if (
        any(term in text for term in (
            "protect your account", "保护您的账户", "保护你的账户",
        ))
        and any(term in text for term in ("loading", "正在加载"))
    ):
        return "security_upgrade_prompt"
    if (
        any(term in text for term in (
            "protect your account", "保护您的账户", "保护你的账户",
        ))
        and any(term in text for term in (
            "don't upgrade", "do not upgrade", "暂不升级", "不升级",
        ))
    ):
        return "security_upgrade_prompt"
    # This is not a verification-code challenge.  Apple displays this optional
    # two-factor-security introduction after a password login and offers
    # "Other Options" / "其他选项".  The next screen allows the user to
    # decline the upgrade and continue into Account Home.  It must be handled
    # before the broad two-factor text below, otherwise a normal login is
    # incorrectly reported as requiring two-factor verification.
    if (
        ("apple account security" in text or "apple账户安全" in compact_text)
        and any(term in text for term in (
            "two-factor authentication", "双重认证", "双重验证",
        ))
        and any(term in text for term in (
            "other options", "其他选项", "more options", "更多选项",
            "when you sign in on any new device or web browser",
            "when signing in on a new device or web browser",
            "登录新设备", "网页浏览器",
        ))
    ):
        return "security_upgrade_prompt"
    if (
        "apple account security" in text
        and "apple uses two-factor authentication" in text
        and "when you sign in on any new device or web browser" in text
        and not any(term in text for term in (
            "enter the verification code", "verification code sent", "enter code",
            "输入验证码", "验证码已发送", "输入代码",
        ))
    ):
        return "security_upgrade_prompt"
    if any(term in text for term in (
        "answer your security questions", "security questions to continue",
        "回答您的安全提示问题", "回答你的安全提示问题", "回答安全提示问题", "回答安全问题",
    )):
        return "security_questions"
    if any(term in current_url for term in ("/account/manage", "/account/home")) or has_account_nav:
        return "normal"
    return None


def classify_recovery_state(visible_text: str) -> str | None:
    text = visible_text.lower()
    if any(term in text for term in (
        "account has been locked", "account is locked", "account is disabled",
        "账户已锁定", "账号已锁定", "账户已停用", "账号已停用",
    )):
        return "locked"
    if any(term in text for term in (
        "verification code", "trusted phone number", "trusted phone",
        "验证码", "受信任电话号码", "受信任号码",
        # Apple's localized 2FA challenge: "请输入发送到你 iPhone 的验证码。"
        "请输入发送到你", "重新发送验证码", "无法使用你的",
    )):
        return "two_factor"
    if any(term in text for term in (
        "account recovery request", "account recovery in progress", "recovery wait", "waiting period",
        "start account recovery", "begin account recovery", "request account recovery",
        "several days", "a few days", "more information will be available", "when your account is ready",
        "password reset email sent", "email has been sent to your rescue email",
        "账户恢复请求", "账号恢复请求", "账户恢复正在进行", "账号恢复正在进行", "恢复等待期",
        "开始账户恢复", "开始账号恢复", "请求账户恢复", "请求账号恢复",
        "需要等待", "数天", "几天后", "准备好恢复",
    )):
        return "recovery_wait_required"
    if AppleFlow._security_answer_rejected(visible_text):
        return "recovery_answer_invalid"
    if any(term in text for term in (
        "birthday is incorrect", "date of birth is incorrect", "incorrect birthday",
        "birthday you entered is not correct", "could not verify your birthday",
        "生日不正确", "出生日期不正确", "无法验证您的生日", "无法验证你的生日",
    )):
        return "recovery_identity_failed"
    return None


def classify_security_rollback_page(visible_text: str) -> str | None:
    """Classify only explicit states in Apple's security rollback flow.

    The iforgot shell reaches ``document.readyState=complete`` before its SPA
    paints the confirmation view. Callers must therefore poll this classifier
    rather than choosing a click path from a single early snapshot.
    """
    text = " ".join(
        str(visible_text or "").lower()
        .replace("\u2011", "-").replace("\u2010", "-").replace("\u2013", "-")
        .split()
    )
    if any(term in text for term in (
        "this link can no longer be used to complete your request",
        "this link can no longer be used", "this link has expired",
        "此链接无法再用于完成你的请求", "此链接已无法用于完成你的请求",
        "此链接已失效", "此链接已过期",
    )):
        return "expired"
    if any(term in text for term in (
        "two-factor authentication has been turned off", "two-factor authentication is turned off",
        "security settings have been restored", "your previous security settings have been restored",
        "returned to your previous security settings", "account security changes have been reverted",
        "已恢复以前的安全设置", "已恢复之前的安全设置",
        "双重认证已关闭", "双重认证已经关闭",
    )):
        return "completed"
    if any(term in text for term in (
        "are you sure you want to turn off two-factor authentication",
        "confirm you want to turn off two-factor authentication",
        "是否确定要关闭双重认证", "确认要关闭双重认证",
        "确定要关闭双重认证", "确定关闭双重认证",
    )) or (
        any(term in text for term in ("turn off two-factor authentication", "关闭双重认证"))
        and any(term in text for term in ("continue", "继续"))
        and any(term in text for term in ("cancel", "取消"))
    ):
        return "confirmation"
    if any(term in text for term in (
        "revert account security changes", "restore previous security settings",
        "return to previous security settings", "恢复以前的安全设置", "恢复以前安全设置",
        "恢复之前的安全设置", "恢复之前安全设置", "返回以前的安全设置", "返回之前的安全设置",
    )):
        return "action"
    return None


# ---------------------------------------------------------------------------
# 账号资料（姓名 · 国家/地区 · 语言）的宽松解析工具。
#
# Apple Account 网页版并未以固定 DOM 展示这些字段，不同本地化/版本甚至可能
# 不在同一页面。这里统一采用"标签 → 相邻文本"的启发式解析，宁可漏读也不误改；
# 检测/恢复一律 fail-open（读不到或改不回都只记录，不会使 check 失败）。
# 真实页面验收时只需调整 _PROFILE_LABELS 与 _PROFILE_PAGE_LABELS 即可。
# ---------------------------------------------------------------------------

_PROFILE_LABELS = {
    "name": ("姓名", "名字", "全名", "显示名称", "Name", "Full name", "Display name"),
    "dob": ("出生日期", "生日", "Date of Birth", "Birth Date", "Birthday"),
    "country": ("国家或地区", "国家/地区", "国家地区", "国家", "Country or Region", "Country/Region", "Country", "Region"),
    "language": ("语言", "首选语言", "主要语言", "语言偏好", "Language", "Preferred language"),
}

# 进入 Apple 账号“个人信息”页时点击的导航/链接文案。
_PROFILE_PAGE_LABELS = (
    "个人信息", "个人资料", "账户信息", "账号信息", "资料",
    "Personal Information", "Personal Info", "Profile",
    "Account Information", "Account Info", "Account details",
)

# 值中一旦出现这些词即截断，避免把相邻按钮/其它字段带进值。
_PROFILE_STOP_WORDS = re.compile(
    r"(\b(edit|change|save|done|cancel|update|add|remove|delete|set|sign)\b"
    r"|编辑|更改|保存|完成|取消|更新|添加|移除|删除|设置|登录|退出|邮箱|电话|Apple\s?ID)",
    re.IGNORECASE,
)


def _profile_normalize(value) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _profile_clean(value: str, preserve_punctuation: bool = False) -> str:
    value = re.sub(r"[\s\u00a0]+", " ", value or "").strip()
    value = re.split(r"\s{2,}", value, maxsplit=1)[0].strip()
    value = _PROFILE_STOP_WORDS.split(value)[0].strip()
    return value if preserve_punctuation else value.strip(" ｜|—:：,，.。·’'\"“”").strip()


def extract_profile_fields(visible_text: str) -> dict:
    """宽松地从可见文本抓取 姓名/国家地区/语言。

    返回 {"name":..., "country":..., "language":...}，抓不到的字段为空串。
    优先匹配“标签 值”同行；其次是标签行之后的第一个有内容行。
    """
    fields = {"name": "", "dob": "", "country": "", "language": ""}
    if not visible_text:
        return fields
    lines = [line.strip() for line in visible_text.splitlines() if line.strip()]
    for key, labels in _PROFILE_LABELS.items():
        for idx, line in enumerate(lines):
            hit = next((label for label in labels if label.lower() in line.lower()), None)
            if hit is None:
                continue
            after = line[len(hit):].strip(" \u00a0:：-—|")
            value = _profile_clean(after, preserve_punctuation=key == "name")
            if value in ("", hit):
                for nxt in lines[idx + 1:idx + 3]:
                    nxt_clean = _profile_clean(nxt, preserve_punctuation=key == "name")
                    if not nxt_clean or nxt_clean.lower() == hit.lower():
                        continue
                    if any(tag in nxt.lower() for tag in (
                        "name", "姓名", "edit", "编辑", "sign", "登录",
                        "birthday", "birth date", "出生日期", "生日",
                        "country", "国家", "language", "语言", "email", "邮箱",
                    )):
                        continue
                    value = nxt_clean
                    break
            if value:
                fields[key] = value
                break
    return fields


def extract_greeting_name(visible_text: str) -> str:
    """从账号主页问候语（你好，<名> / Hello, <名>…）提取姓名，抓不到返回空串。"""
    if not visible_text:
        return ""
    for pattern in (
        r"(?:你好|您好|欢迎|欢迎回来|Hello|Hi|Welcome|Welcome back)\s*[，,]\s*([^\n！!。.？?]{1,40})",
        r"(?:你好|您好|欢迎|Hello|Hi|Welcome)[，,:：]?\s*([A-Za-z\u4e00-\u9fff][^\n！!。.？?，,]{1,39})",
    ):
        match = re.search(pattern, visible_text, re.IGNORECASE)
        if match:
            value = re.split(r"\s{2,}", match.group(1).strip().strip("！!。."), maxsplit=1)[0].strip()
            if value and not _PROFILE_STOP_WORDS.search(value):
                return value
    return ""


def split_display_name(display_name: str) -> tuple[str, str]:
    """把显示姓名拆成 (名, 姓)。无空格时整体当“名”，尽量回填 Apple 名/姓两个字段。"""
    value = re.sub(r"\s+", " ", display_name or "").strip()
    if not value:
        return "", ""
    parts = value.split(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 and parts[1] else (value, "")


def profile_matches(left: str, right: str) -> bool:
    return _profile_normalize(left) == _profile_normalize(right)


def profile_option_matches(option: str, expected: str) -> bool:
    """Match Apple's language/region labels when one side omits the subtitle.

    Apple has returned ``简体中文``, ``简体中文 - 中文（简体）`` and
    ``中文（简体）`` for the same choice.  Keep the comparison strict for
    unrelated values while allowing the descriptive suffix used by the
    settings picker.
    """
    actual = _profile_normalize(option)
    target = _profile_normalize(expected)
    if not actual or not target:
        return False
    if actual == target:
        return True
    actual_base = re.split(r"[-–—|()（）]", actual, maxsplit=1)[0].strip()
    target_base = re.split(r"[-–—|()（）]", target, maxsplit=1)[0].strip()
    if actual_base and target_base and actual_base == target_base:
        return True
    # Some pickers only render the bracketed subtitle, e.g. 「中文（简体）」
    # for the baseline 「简体中文 - 中文（简体）」.
    actual_subtitle = _profile_subtitle(actual)
    target_subtitle = _profile_subtitle(target)
    return bool(actual_subtitle and target_subtitle and actual_subtitle == target_subtitle)


def _profile_subtitle(value: str) -> str:
    match = re.search(r"[（(]([^）)]+)[）)]", value or "")
    return match.group(1).strip() if match else ""


def normalize_profile_date(value: str) -> str:
    """Normalize the date formats shown by Apple to YYYY-MM-DD."""
    text = re.sub(r"\s+", " ", value or "").strip()
    match = re.search(r"(\d{4})\s*[年/.-]\s*(\d{1,2})\s*[月/.-]\s*(\d{1,2})\s*日?", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, 1):
        current = [row]
        for column, right_char in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left_char != right_char),
            ))
        previous = current
    return 1.0 - previous[-1] / max(len(left), len(right))


def match_security_answers(visible_questions: list[str], payload: dict) -> list[str]:
    pairs = []
    for index in range(1, 4):
        question = AppleFlow._normalize(str(payload.get(f"q{index}", "")))
        answer = str(payload.get(f"a{index}", ""))
        if question and answer:
            pairs.append((question, answer))
    if len(pairs) < len(visible_questions):
        raise FlowError("security_answers_missing", "管理站缺少完整密保答案")

    normalized = [AppleFlow._normalize(question) for question in visible_questions]
    if any(not question for question in normalized):
        raise FlowError("security_question_unmatched", "无法读取 Apple 密保问题")

    candidates = []
    for assignment in permutations(range(len(pairs)), len(normalized)):
        scores = [_similarity(question, pairs[pair_index][0])
                  for question, pair_index in zip(normalized, assignment)]
        candidates.append((sum(scores), scores, assignment))
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_total, best_scores, best_assignment = candidates[0]
    if any(score < 0.85 for score in best_scores):
        raise FlowError("security_question_unmatched", "Apple 密保问题与管理站记录不匹配")
    if len(candidates) > 1 and best_total - candidates[1][0] < 0.15:
        raise FlowError("security_question_unmatched", "Apple 密保问题匹配结果不唯一")
    return [pairs[pair_index][1] for pair_index in best_assignment]


def generate_password(prefix: str = settings.password_prefix) -> str:
    alphabet = string.ascii_letters + string.digits + "@#%"
    while True:
        required = [secrets.choice(group) for group in (
            string.ascii_uppercase, string.ascii_lowercase, string.digits, "@#%",
        )]
        suffix = required + [secrets.choice(alphabet) for _ in range(8)]
        random.SystemRandom().shuffle(suffix)
        candidate = prefix + "".join(suffix)
        # Apple keeps the submit control disabled when a password contains
        # three identical consecutive characters.
        if not re.search(r"(.)\1\1", candidate):
            return candidate


@dataclass(frozen=True)
class ProxySpec:
    protocol: str
    host: str
    port: int
    username: str = ""
    password: str = ""

    @property
    def url(self) -> str:
        auth = ""
        if self.username:
            auth = f"{quote(unquote(self.username), safe='')}:{quote(unquote(self.password), safe='')}@"
        return f"{self.protocol}://{auth}{self.host}:{self.port}"

    @property
    def pproxy_url(self) -> str:
        return f"{self.protocol}://{self.host}:{self.port}#{unquote(self.username)}:{unquote(self.password)}"


def normalize_proxy_endpoint(content: str) -> str | None:
    content = content.strip()
    host_pattern = r"(?:\[[0-9a-fA-F:]+\]|[A-Za-z0-9._-]+)"

    def host_port(value: str):
        match = re.fullmatch(rf"(?P<host>{host_pattern}):(?P<port>\d{{1,5}})", value)
        if not match or not 1 <= int(match.group("port")) <= 65535:
            return None
        return match.group("host"), match.group("port")

    if not content or len(content) > 512 or re.search(r"\s", content):
        return None
    if "@" not in content:
        direct = host_port(content)
        if direct:
            return f"{direct[0]}:{direct[1]}"
        parts = content.split(":")
        if len(parts) != 4 or any(not part for part in parts):
            return None
        first, second, third, fourth = parts
        host_first = bool(re.fullmatch(r"[A-Za-z0-9._-]+", first)) and second.isdigit() and 1 <= int(second) <= 65535
        auth_first = bool(re.fullmatch(r"[A-Za-z0-9._-]+", third)) and fourth.isdigit() and 1 <= int(fourth) <= 65535
        if host_first and auth_first:
            if "." in third and "." not in first:
                host_first = False
            else:
                auth_first = False
        if host_first:
            return f"{third}:{fourth}@{first}:{second}"
        if auth_first:
            return f"{first}:{second}@{third}:{fourth}"
        return None
    if content.count("@") != 1:
        return None
    left, right = content.split("@", 1)
    auth_pattern = r"(?P<user>[^:@\s]+):(?P<password>[^@\s]+)"
    left_auth, right_host = re.fullmatch(auth_pattern, left), host_port(right)
    left_host, right_auth = host_port(left), re.fullmatch(auth_pattern, right)
    if left_auth and right_host and left_host and right_auth:
        if "." in left_host[0] and "." not in right_host[0]:
            return f"{right_auth.group('user')}:{right_auth.group('password')}@{left_host[0]}:{left_host[1]}"
    if left_auth and right_host:
        return f"{left_auth.group('user')}:{left_auth.group('password')}@{right_host[0]}:{right_host[1]}"
    if left_host and right_auth:
        return f"{right_auth.group('user')}:{right_auth.group('password')}@{left_host[0]}:{left_host[1]}"
    return None


def parse_proxy(protocol: str | None, content: str | None) -> ProxySpec | None:
    if not protocol or not content:
        return None
    content = normalize_proxy_endpoint(content)
    match = re.fullmatch(
        r"(?:(?P<user>[^:@\s]+):(?P<password>[^@\s]+)@)?"
        r"(?P<host>\[[0-9a-fA-F:]+\]|[A-Za-z0-9._-]+):(?P<port>\d{1,5})", content or ""
    )
    if protocol not in {"http", "socks5"} or not match:
        raise FlowError("proxy_error", "代理配置格式错误")
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        raise FlowError("proxy_error", "代理端口无效")
    return ProxySpec(protocol, match.group("host"), port, match.group("user") or "", match.group("password") or "")


def resolve_proxy(protocol: str | None, content: str | None) -> str | None:
    spec = parse_proxy(protocol, content)
    return spec.url if spec else None


_BRIDGE_PORTS = Queue()
for _port in range(settings.proxy_bridge_port_start, settings.proxy_bridge_port_start + settings.concurrency):
    _BRIDGE_PORTS.put(_port)


class ProxyBridge:
    def __init__(self, protocol: str | None, content: str | None):
        self.spec = parse_proxy(protocol, content)
        self.port = None
        self.process = None

    def start(self) -> str | None:
        if not self.spec:
            return None
        if not self.spec.username:
            return self.spec.url
        try:
            self.port = _BRIDGE_PORTS.get(timeout=5)
        except Empty as exc:
            raise FlowError("proxy_error", "认证代理桥接端口繁忙") from exc
        self.process = subprocess.Popen(
            [sys.executable, "-m", "pproxy", "-l", f"http://0.0.0.0:{self.port}", "-r", self.spec.pproxy_url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.close()
                raise FlowError("proxy_error", "认证代理桥接启动失败")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return f"http://{settings.proxy_bridge_host}:{self.port}"
            except OSError:
                time.sleep(0.1)
        self.close()
        raise FlowError("proxy_error", "认证代理桥接启动超时")

    def close(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
        if self.port is not None:
            _BRIDGE_PORTS.put(self.port)
            self.port = None


class AppleFlow:
    _RETRYABLE_CODES = {
        "proxy_error", "page_changed", "driver_timeout", "verification_failed",
        "captcha_image_unavailable", "captcha_rejected", "page_unready",
        "device_page_unready", "driver_session_lost", "selenium_busy",
    }
    # Terminal/error login states.  Each is snapshotted under a clear file name
    # the moment it is recognised so failures can be inspected without relying
    # on the single final attempt screenshot.
    _ERROR_STATES = {
        "locked", "two_factor", "password_invalid", "identity_verification_failed",
        "page_unready", "unknown", "verification_failed",
    }

    def __init__(self, captcha_challenges=None):
        self.artifact_dir = Path(settings.artifact_dir)
        self.captcha_challenges = captcha_challenges
        # 资料还原失败的具体环节（未找到入口/未找到可选值），用于面板消息与诊断产物。
        self._profile_failure_notes: dict[str, str] = {}

    def run(self, action: str, payload: dict, job_id: str) -> dict:
        bridge = None
        try:
            bridge = ProxyBridge(payload.get("proxy_protocol"), payload.get("proxy_content"))
            proxy = bridge.start()
            attempts = (2 if action == "workflow" else settings.navigation_attempts
                        if action in {"check", "delete_devices"} else 1)
            for attempt in range(attempts):
                driver = None
                try:
                    driver = self._new_driver(proxy)
                    context = payload.get('_job_context') or {}
                    if callable(context.get('session_opened')):
                        context['session_opened'](driver.session_id)
                    result = self._run_action(action, driver, payload, job_id)
                    context = payload.get("_job_context") if isinstance(payload.get("_job_context"), dict) else {}
                    if action == "workflow" and context.get("mutation_ever_started"):
                        result["workflow_mutation_ever_started"] = True
                    if action == "check" and result.get("state") != "normal":
                        artifact_id = self._artifact_job_id(job_id, attempt, attempts)
                        self._screenshot(driver, artifact_id)
                        self._write_diagnostics(
                            driver, artifact_id, result.get("stage", "login"),
                            result.get("error_code", result.get("state", "unknown")), proxy,
                        )
                    feature = str(context.get('mutation_feature') or '')
                    if action != 'workflow' and feature and result.get('status') and callable(context.get('progress')):
                        uncertain = feature == 'profile_restore' and any(
                            item.get('status') == 'uncertain' for item in (result.get('profile_results') or {}).values()
                            if isinstance(item, dict))
                        if not uncertain:
                            context['progress']('result_confirmation', feature, {'status': 'success'})
                    return self._result(result, "login")
                except FlowError as exc:
                    context = payload.get("_job_context") if isinstance(payload.get("_job_context"), dict) else {}
                    if action == "workflow" and context.get("mutation_ever_started"):
                        exc.details["workflow_mutation_ever_started"] = True
                    if action == "workflow" and context.get("mutation_started"):
                        exc.details["workflow_mutation_started"] = True
                        exc.details["workflow_mutation_feature"] = str(context.get("mutation_feature") or "")
                    artifact_id = self._artifact_job_id(job_id, attempt, attempts)
                    self._capture_failure(driver, artifact_id, exc.stage or "login", exc.code, proxy)
                    if self._can_retry_navigation(action, attempt, attempts, exc):
                        logger.warning(
                            "Apple navigation retry action=%s attempt=%s error_code=%s",
                            action, attempt + 1, exc.code,
                        )
                        continue
                    return self._result({
                        "status": False, "error_code": exc.code, "message": str(exc),
                        "retryable": exc.retryable, "password_submitted": exc.password_submitted,
                        "device_removal_started": exc.device_removal_started,
                        **exc.details,
                    }, exc.stage or "login")
                except Exception as exc:
                    code = "proxy_error" if self._is_proxy_error(exc) else "driver_session_lost"
                    message = "代理连接失败" if code == "proxy_error" else "浏览器会话异常"
                    context = payload.get("_job_context") if isinstance(payload.get("_job_context"), dict) else {}
                    mutation_started = bool(context.get("mutation_started"))
                    mutation_feature = str(context.get("mutation_feature") or "")
                    self._capture_failure(driver, self._artifact_job_id(job_id, attempt, attempts), "login", code, proxy)
                    logger.exception("Apple driver operation failed action=%s error_code=%s", action, code)
                    if action == "workflow" and not mutation_started and attempt + 1 < attempts:
                        logger.warning("Apple workflow session retry attempt=%s error_code=%s", attempt + 1, code)
                        continue
                    return self._result({
                        "status": False, "error_code": code, "message": message,
                        "retryable": False,
                        "password_submitted": (
                            mutation_started and mutation_feature == "password_update"
                            if action == "workflow" else True
                        ),
                        "device_removal_started": bool(
                            action == "workflow" and mutation_started
                            and mutation_feature == "device_cleanup"
                        ),
                        **({
                            "workflow_mutation_started": True,
                            "workflow_mutation_feature": mutation_feature,
                        } if mutation_started else {}),
                        **({"workflow_mutation_ever_started": True}
                           if action == "workflow" and context.get("mutation_ever_started") else {}),
                    }, "login")
                finally:
                    if driver:
                        try:
                            session_id = getattr(driver, 'session_id', None)
                            driver.quit()
                            if callable(context.get('session_closed')):
                                context['session_closed'](session_id)
                        except Exception:
                            pass
            return self._result({
                "status": False, "error_code": "page_unready", "message": "Apple 页面加载超时",
                "retryable": True,
            }, "login")
        finally:
            if bridge:
                bridge.close()

    @staticmethod
    def _artifact_job_id(job_id: str, attempt: int, attempts: int) -> str:
        return job_id if attempts == 1 else f"{job_id}-attempt-{attempt + 1}"

    @staticmethod
    def _can_retry_navigation(action: str, attempt: int, attempts: int, exc: FlowError) -> bool:
        return (
            action in {"check", "delete_devices", "workflow"} and attempt + 1 < attempts and exc.retryable
            and not exc.password_submitted and not exc.device_removal_started
            and not exc.details.get("workflow_mutation_started")
            # A second browser session still uses the same broken proxy. Return
            # immediately so the scheduler can discard/rotate that endpoint.
            and exc.code != "proxy_error"
            and exc.code in AppleFlow._RETRYABLE_CODES
        )

    def _run_action(self, action: str, driver, payload: dict, job_id: str) -> dict:
        if action == "check":
            return self._check(driver, payload, job_id)
        if action == "workflow":
            return self._workflow(driver, payload, job_id)
        if action == "recover":
            return self._recover(driver, payload, job_id)
        if action == "request_email_reset":
            return self._request_email_reset(driver, payload, job_id)
        if action == "complete_email_reset":
            return self._complete_email_reset(driver, payload)
        if action == "complete_security_rollback":
            return self._complete_security_rollback(driver, payload)
        if action == "update_password":
            return self._update_password(driver, payload)
        if action == "delete_devices":
            return self._delete_devices(driver, payload, job_id)
        return {"status": False, "error_code": "unknown_action", "message": "未知操作"}

    @staticmethod
    def _result(result: dict, stage: str, password_submitted: bool = False) -> dict:
        return {
            **result,
            "stage": result.get("stage", stage),
            "retryable": bool(result.get("retryable", False)),
            "password_submitted": bool(result.get("password_submitted", password_submitted)),
        }

    def _stage(
        self, stage: str, callback, retryable: bool = False,
        password_submitted: bool = False, device_removal_started: bool = False,
    ):
        try:
            return callback()
        except FlowError as exc:
            safe_retry = retryable and exc.code in self._RETRYABLE_CODES
            # Apple can validate a disabled Reset Password control only after
            # it is clicked.  A precise "password reused" page is proof that
            # no password was accepted, unlike every other post-click error.
            submitted = exc.password_submitted or password_submitted
            if exc.code == "password_reused":
                submitted = False
            raise FlowError(
                exc.code, str(exc), stage=exc.stage or stage,
                retryable=exc.retryable or safe_retry,
                password_submitted=submitted,
                device_removal_started=exc.device_removal_started or device_removal_started,
                details=exc.details,
            ) from exc
        except Exception as exc:
            code = "proxy_error" if self._is_proxy_error(exc) else "page_changed"
            message = "代理连接失败" if code == "proxy_error" else "页面结构或浏览器状态异常"
            logger.exception("Apple flow stage failed stage=%s error_code=%s", stage, code)
            raise FlowError(
                code, message, stage=stage, retryable=retryable,
                password_submitted=password_submitted,
                device_removal_started=device_removal_started,
            ) from exc

    def _new_driver(self, proxy: str | None = None):
        from selenium import webdriver
        from selenium.webdriver.remote.client_config import ClientConfig

        self._ensure_selenium_capacity()
        options = webdriver.ChromeOptions()
        # Apple Account is a JavaScript application whose DOMContentLoaded
        # event can remain blocked after the visible shell is available. Let
        # get() return after dispatch and use bounded state machines below.
        options.page_load_strategy = "none"
        options.set_capability("goog:loggingPrefs", {"browser": "ALL", "performance": "ALL"})
        for argument in ("--window-size=1440,1200", "--disable-dev-shm-usage", "--no-sandbox"):
            options.add_argument(argument)
        # Apple follows browser language for many account/recovery pages.
        # Keep the visual flow Chinese by default; recognizers retain English
        # fallbacks because Apple can override locale for individual pages.
        options.add_argument(f"--lang={settings.ui_locale}")
        options.add_experimental_option("prefs", {
            "intl.accept_languages": f"{settings.ui_locale},zh,en-US,en",
            # account.apple.com embeds idmsa.apple.com. Both are Apple-owned,
            # but Chromium privacy experiments can still partition the
            # authentication iframe's storage before its redirect completes.
            # Explicitly allow normal browser cookie/storage behavior for this
            # non-incognito automation profile.
            "profile.default_content_setting_values.cookies": 1,
            "profile.block_third_party_cookies": False,
            "profile.cookie_controls_mode": 0,
        })
        options.add_argument(
            "--disable-features=ThirdPartyStoragePartitioning,ThirdPartyCookiePhaseout"
        )
        if proxy:
            options.add_argument(f"--proxy-server={proxy}")
        client_config = ClientConfig(
            remote_server_addr=settings.selenium_url,
            timeout=settings.command_timeout,
        )
        driver = webdriver.Remote(
            command_executor=settings.selenium_url,
            options=options,
            client_config=client_config,
        )
        driver.set_page_load_timeout(settings.page_load_timeout)
        driver.set_script_timeout(settings.page_load_timeout)
        return driver

    def _ensure_selenium_capacity(self):
        import urllib.request

        status_url = settings.selenium_url.rsplit("/wd/hub", 1)[0] + "/status"
        try:
            with urllib.request.urlopen(status_url, timeout=3) as response:
                status = json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            raise FlowError("selenium_unavailable", "浏览器服务不可用", retryable=True) from exc
        capacity = self._selenium_capacity(status)
        if capacity == "busy":
            raise FlowError("selenium_busy", "浏览器服务当前忙碌，请稍后重试", retryable=True)
        if capacity != "ready":
            raise FlowError("selenium_unavailable", "浏览器服务未就绪", retryable=True)

    @staticmethod
    def _selenium_capacity(status: dict) -> str:
        value = status.get("value", {}) if isinstance(status, dict) else {}
        nodes = value.get("nodes", []) if isinstance(value, dict) else []
        if not isinstance(nodes, list) or not nodes:
            return "unavailable"
        active = 0
        available_nodes = 0
        for node in nodes:
            if not isinstance(node, dict) or node.get("availability") != "UP":
                continue
            available_nodes += 1
            for slot in node.get("slots", []) or []:
                if isinstance(slot, dict) and slot.get("session") is not None:
                    active += 1
        if available_nodes == 0:
            return "unavailable"
        if value.get("ready") is True:
            return "ready"
        return "busy" if active else "unavailable"

    def _check(self, driver, payload: dict, job_id: str = "") -> dict:
        state = self._stage("login", lambda: self._sign_in(driver, payload, job_id), True)
        messages = {
            "normal": "账号登录状态正常", "locked": "账号已锁定", "two_factor": "账号需要二步验证",
            "trusted_phone_prompt": "Apple 要求添加受信任电话号码",
            "security_upgrade_prompt": "Apple 安全升级未完成",
            "security_interstitial": "Apple 账号安全验证未完成",
            "password_invalid": "账号密码错误", "verification_failed": "页面验证失败",
            "identity_verification_failed": "Apple 无法验证身份，可能已触发限流",
            "page_unready": "Apple 页面加载超时", "unknown": "未识别账号状态",
        }
        if state == "page_unready":
            return {
                "status": False, "state": state, "error_code": "page_unready",
                "message": messages[state], "retryable": True,
            }
        result = {"status": True, "state": state, "message": messages.get(state, "未识别账号状态")}
        profile_config = payload.get("profile_restore")
        if state == "normal" and isinstance(profile_config, dict) and profile_config.get("enabled"):
            callback = (payload.get('_job_context') or {}).get('progress')
            profile_out = self._profile_guard(driver, profile_config, job_id,
                mutation_callback=(lambda: callback('mutation_submitted', 'profile_restore')) if callable(callback) else None)
            if profile_out.get("profile"):
                result["profile"] = profile_out["profile"]
            if profile_out.get("profile_results"):
                result["profile_results"] = profile_out["profile_results"]
            if profile_out.get("profile_restored"):
                result["profile_restored"] = True
            if profile_out.get("note"):
                result["message"] = f"{result['message']}；{profile_out['note']}"
        return result

    def _profile_guard(self, driver, config: dict, job_id: str, mutation_callback=None) -> dict:
        """资料检测/恢复的安全外壳：任何异常都只记录并截图，绝不中断 check。"""
        try:
            return self._manage_account_profile(driver, config, job_id, mutation_callback=mutation_callback)
        except Exception:
            logger.exception("Apple account-profile restore failed job_id=%s", job_id)
            try:
                self._capture_error_page(driver, job_id, "profile_guard")
            except Exception:
                pass
            return {"profile": {}, "profile_restored": False, "note": "账号资料检测执行异常，已跳过"}

    def _recover(self, driver, payload: dict, job_id: str = "") -> dict:
        from selenium.webdriver.support.ui import WebDriverWait

        self._stage("identity", lambda: self._get(driver, settings.recovery_url), True)
        self._stage(
            "identity",
            lambda: self._type_first_anywhere(driver, self._account_selectors(), payload["username"]),
            True,
        )
        self._stage("captcha", lambda: self._submit_recovery_identity(driver, job_id), True)
        self._stage("identity", lambda: self._choose_reset_password_path(driver), True)
        self._stage("identity", lambda: self._raise_recovery_block(driver), True)
        # Apple presents the reset-method choice before the birthday form. Pick
        # security questions first so the default rescue-email path is never used.
        self._stage("identity", lambda: self._choose_security_question_recovery(driver), False)
        self._stage("identity", lambda: self._raise_recovery_block(driver), True)
        # Once the birthday form is submitted, even a page timeout must not cause
        # an automatic recovery retry. Apple can accept the identity value before
        # the next page becomes observable.
        self._stage(
            "identity", lambda: self._fill_recovery_birth_date(driver, str(payload.get("dob", ""))), False,
        )
        self._stage("identity", lambda: self._raise_recovery_block(driver), True)

        input_selector = (
            "input:not([type='hidden']):not([type='password']):not([type='submit']):"
            "not([type='button']):not([type='checkbox']):not([type='radio'])"
        )
        try:
            inputs = WebDriverWait(driver, 25).until(
                lambda d: self._visible_fields_anywhere(
                    d, input_selector, 2, require_enabled=False,
                )
            )
        except Exception as exc:
            self._raise_recovery_block(driver)
            raise FlowError(
                "recovery_identity_failed", "未进入 Apple 密保验证步骤", stage="identity",
            ) from exc
        inputs = inputs[:2]
        try:
            answers = match_security_answers([self._question_context(driver, field) for field in inputs], payload)
        except FlowError as exc:
            raise FlowError(
                "recovery_identity_failed", "Apple 密保问题与管理站记录无法安全匹配",
                stage="security_questions",
            ) from exc

        def submit_answers():
            for element, answer in zip(inputs, answers):
                element.clear()
                element.send_keys(answer)
            self._click_button(driver, ("continue", "继续", "下一步"))

        self._stage("security_questions", submit_answers)

        def password_form(d):
            state = self._recovery_state(d)
            if state:
                self._raise_recovery_code(state)
            return self._visible_fields_anywhere(d, "input[type='password']", 2)

        try:
            password_inputs = WebDriverWait(driver, 25).until(password_form)
        except FlowError as exc:
            raise FlowError(
                exc.code, str(exc), stage="password_form", retryable=False,
                password_submitted=False,
            ) from exc
        except Exception as exc:
            self._raise_recovery_block(driver)
            raise FlowError(
                "recovery_identity_failed", "Apple 未提供即时密码重置表单", stage="password_form",
            ) from exc

        new_password = str(payload.get("new_password") or generate_password())
        if not self._valid_new_password(new_password):
            raise FlowError(
                "recovery_identity_failed", "受控恢复密码不符合安全要求", stage="password_form",
            )

        def fill_password():
            password_inputs[-2].clear()
            password_inputs[-2].send_keys(new_password)
            password_inputs[-1].clear()
            password_inputs[-1].send_keys(new_password)

        self._stage("password_form", fill_password)
        self._stage(
            "password_submitted",
            lambda: self._click_button(driver, ("continue", "reset", "重设", "继续")),
            password_submitted=True,
        )
        self._stage(
            "password_submitted",
            lambda: self._confirm_password_change(
                driver, password_inputs[-2:],
                unconfirmed_code="password_reset_unconfirmed",
                rejected_code="password_reset_unconfirmed",
            ),
            password_submitted=True,
        )
        return {
            "status": True,
            "message": "账号恢复及密码更新完成",
            "password": new_password,
            "password_change_confirmed": True,
        }

    def _request_email_reset(self, driver, payload: dict, job_id: str = "") -> dict:
        from selenium.webdriver.support.ui import WebDriverWait

        self._stage("identity", lambda: self._get(driver, settings.recovery_url), True)
        self._stage("identity", lambda: self._type_first_anywhere(driver, self._account_selectors(), payload["username"]), True)
        self._stage("captcha", lambda: self._submit_recovery_identity(driver, job_id), True)
        self._stage("identity", lambda: self._choose_reset_password_path(driver), True)
        self._stage("identity", lambda: self._raise_recovery_block(driver), True)
        self._stage("email_request", lambda: self._choose_email_recovery(driver), False)

        def requested(current_driver):
            _, text = self._login_snapshot(current_driver)
            normalized = text.lower()
            if any(term in normalized for term in (
                "password reset email sent", "email has been sent", "check your email",
                "密码重置邮件已发送", "邮件已发送", "请查看您的电子邮件",
            )):
                return True
            self._raise_recovery_block(current_driver)
            return False

        try:
            WebDriverWait(driver, 25).until(requested)
        except FlowError:
            raise
        except Exception as exc:
            raise FlowError("email_reset_request_unconfirmed", "Apple 未确认已发送密码重置邮件", stage="email_request") from exc
        return {"status": True, "message": "Apple 密码重置邮件已请求"}

    def _complete_email_reset(self, driver, payload: dict) -> dict:
        from selenium.webdriver.support.ui import WebDriverWait

        reset_url = self._validate_apple_recovery_url(str(payload.get("reset_url", "")))
        new_password = str(payload.get("new_password") or generate_password())
        if not self._valid_new_password(new_password):
            raise FlowError("password_form_missing", "受控恢复密码不符合安全要求", stage="password_form")
        self._stage("email_link", lambda: self._get(driver, reset_url), False)

        def password_form(current_driver):
            self._raise_recovery_block(current_driver)
            return self._visible_fields_anywhere(current_driver, "input[type='password']", 2)

        try:
            inputs = WebDriverWait(driver, 25).until(password_form)[-2:]
        except FlowError:
            raise
        except Exception as exc:
            raise FlowError("password_form_missing", "Apple 邮件链接未提供密码重置表单", stage="password_form") from exc

        def fill_password():
            for element in inputs:
                element.clear()
                element.send_keys(new_password)

        self._stage("password_form", fill_password)
        self._stage("password_form", lambda: self._raise_password_policy_error(driver))
        self._stage("password_submitted", lambda: self._click_button(driver, ("continue", "reset", "重设", "继续")), password_submitted=True)
        self._stage("password_submitted", lambda: self._confirm_password_change(
            driver, inputs, unconfirmed_code="password_reset_unconfirmed", rejected_code="password_reset_unconfirmed",
        ), password_submitted=True)
        return {"status": True, "message": "邮件密码重置完成", "password": new_password,
                "password_change_confirmed": True}

    def _fill_recovery_passwords(self, driver, password: str):
        """Use real input events and re-query fields after reactive form updates."""
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.common.exceptions import StaleElementReferenceException

        def fill_and_verify(current):
            try:
                fields = self._visible_fields_anywhere(current, "input[type='password']", 2)
                if len(fields) < 2:
                    return False
                for index in range(2):
                    # Typing into one field may replace the other input node.
                    fields = self._visible_fields_anywhere(current, "input[type='password']", 2)[-2:]
                    if len(fields) < 2:
                        return False
                    field = fields[index]
                    if field.get_property("value") != password:
                        field.click()
                        field.send_keys(Keys.CONTROL, "a")
                        field.send_keys(Keys.BACKSPACE)
                        field.send_keys(password)
                        field.send_keys(Keys.TAB)
                fields = self._visible_fields_anywhere(current, "input[type='password']", 2)[-2:]
                return len(fields) == 2 and all(field.get_property("value") == password for field in fields)
            except StaleElementReferenceException:
                return False

        try:
            WebDriverWait(driver, 10).until(fill_and_verify)
        except Exception as exc:
            raise FlowError("password_form_incomplete", "新密码两栏未完整填写，未提交修改",
                            stage="password_form", password_submitted=False) from exc

    def _complete_security_rollback(self, driver, payload: dict) -> dict:
        from selenium.common.exceptions import StaleElementReferenceException
        from selenium.webdriver.support.ui import WebDriverWait

        rollback_url = self._validate_apple_recovery_url(str(payload.get("reset_url", "")))
        new_password = str(payload.get("new_password") or "")
        if not self._valid_new_password(new_password):
            raise FlowError("password_form_missing", "关闭双重认证所需的新密码不符合安全要求", stage="password_form")
        self._stage("security_rollback", lambda: self._get(driver, rollback_url), False)

        def expired_link(current_driver) -> bool:
            _, text = self._login_snapshot(current_driver)
            return classify_security_rollback_page(text) == "expired"

        if expired_link(driver):
            raise FlowError(
                "security_rollback_link_expired", "Apple 双重认证回退链接已失效",
                stage="security_rollback", password_submitted=False,
            )

        def confirmed(current_driver):
            if expired_link(current_driver):
                raise FlowError(
                    "security_rollback_link_expired", "Apple 双重认证回退链接已失效",
                    stage="security_rollback", password_submitted=False,
                )
            _, text = self._login_snapshot(current_driver)
            return classify_security_rollback_page(text) == "completed"

        def submit_rollback(current_driver):
            # Some Apple enrollment emails open a confirmation page rather than
            # applying the rollback on navigation.  Submit exactly one explicit
            # rollback control; never use a generic Continue button here.
            def entry_state(candidate_driver):
                _, text = self._login_snapshot(candidate_driver)
                state = classify_security_rollback_page(text)
                if state == "expired":
                    raise FlowError(
                        "security_rollback_link_expired", "Apple 双重认证回退链接已失效",
                        stage="security_rollback", password_submitted=False,
                    )
                if state:
                    return state
                blocked = classify_recovery_state(text)
                if blocked:
                    self._raise_recovery_code(blocked)
                return False

            try:
                state = WebDriverWait(current_driver, settings.render_timeout).until(entry_state)
            except FlowError:
                raise
            except Exception as exc:
                raise FlowError(
                    "security_rollback_page_unready", "Apple 双重认证回退页面未完成加载",
                    stage="security_rollback", password_submitted=False,
                ) from exc

            if state == "completed":
                return
            try:
                if state == "confirmation":
                    # Apple's current iforgot confirmation renders Continue as
                    # a link (and, in some builds, inside a web component).
                    # The surrounding page text has already been constrained
                    # to an explicit turn-off confirmation, so accepting a
                    # labelled anchor here cannot click a generic Apple link.
                    self._click_button_anywhere(current_driver, ("continue", "继续"), include_links=True)
                else:
                    self._click_button_anywhere(current_driver, (
                        "revert account security changes", "restore previous security settings",
                        "turn off two-factor authentication", "恢复以前的安全设置",
                        "恢复之前安全设置", "关闭双重认证",
                    ), include_links=True)
            except StaleElementReferenceException:
                # Apple often navigates away as soon as the one-time recovery
                # link is activated.  Selenium may then report its clicked
                # element as stale even though the action was sent.  Do not
                # click again; the explicit confirmation wait below remains
                # the only success signal.
                logger.info("Apple security rollback control triggered a page navigation")

        self._stage("security_rollback", lambda: submit_rollback(driver), False)

        def password_form(current_driver):
            # This Apple page explicitly describes turning off two-factor
            # authentication while asking for a replacement password.  The
            # generic recovery classifier sees those words and would confuse
            # this required password form with a new two-factor challenge.
            # Require visible password fields instead; if they do not appear,
            # the later explicit-success wait fails closed.
            return self._visible_fields_anywhere(current_driver, "input[type='password']", 2)

        try:
            inputs = WebDriverWait(driver, 25).until(password_form)[-2:]
        except FlowError:
            raise
        except Exception:
            inputs = []

        if inputs:
            self._stage("password_form", lambda: self._fill_recovery_passwords(driver, new_password))
            self._stage("password_form", lambda: self._raise_password_policy_error(driver))
            def ready_to_submit(current):
                fields = self._visible_fields_anywhere(current, "input[type='password']", 2)[-2:]
                if len(fields) != 2 or any(field.get_property("value") != new_password for field in fields):
                    return False
                return self._exact_control_anywhere(current, ("continue", "reset", "重设", "继续"), False, False)

            try:
                WebDriverWait(driver, 10).until(ready_to_submit)
            except Exception as exc:
                raise FlowError("password_form_incomplete", "新密码表单未就绪，未提交修改",
                                stage="password_form", password_submitted=False) from exc
            self._stage(
                "password_submitted", lambda: self._click_button_anywhere(driver, ("continue", "reset", "重设", "继续")),
                password_submitted=True,
            )

            def final_turn_off_confirmation(current_driver):
                _, text = self._login_snapshot(current_driver)
                normalized = " ".join(text.lower().replace("\u2011", "-").split())
                return any(term in normalized for term in (
                    "turn off two-factor authentication?", "confirm turn off two-factor authentication",
                    "确认关闭双重认证", "是否关闭双重认证", "确定要关闭双重认证", "确定关闭双重认证",
                    "关闭双重认证",
                ))

            try:
                if WebDriverWait(driver, 8).until(lambda d: final_turn_off_confirmation(d) or False):
                    self._stage(
                        "password_submitted", lambda: self._click_button_anywhere(driver, ("turn off", "关闭双重认证", "关闭")),
                        password_submitted=True,
                    )
            except Exception:
                # Some Apple locales submit directly after the password form;
                # in that case the explicit password-success check below is
                # still required before any writeback.
                pass
            def rollback_completed(current_driver):
                _, text = self._login_snapshot(current_driver)
                title = str(getattr(current_driver, "title", "") or "")
                normalized = " ".join((text + " " + title).lower().replace("\u2011", "-").split())
                if self._has_explicit_password_success(text, title, str(getattr(current_driver, "current_url", "") or "")):
                    return True
                # This is the a4 success page. It is sufficient proof that
                # the submitted password and rollback were accepted together.
                if any(term in normalized for term in (
                    "two-factor authentication has been turned off", "two-factor authentication is turned off",
                    "双重认证已关闭", "双重认证已经关闭",
                )):
                    return True
                return False

            try:
                WebDriverWait(driver, 30).until(rollback_completed)
            except Exception as exc:
                raise FlowError("password_reset_unconfirmed", "Apple 未明确确认密码与双重认证关闭结果", stage="password_submitted",
                                password_submitted=True) from exc
            return {
                "status": True,
                "message": "Apple 双重认证已关闭并完成密码更新",
                "security_rollback_confirmed": True,
                "password": new_password,
                "password_change_confirmed": True,
            }

        try:
            # The hosted confirmation view can render after the reset form has
            # disappeared.  Keep waiting for an explicit marker rather than
            # treating that transient state as a failed or successful change.
            WebDriverWait(driver, 45).until(confirmed)
        except Exception as exc:
            raise FlowError("security_rollback_unconfirmed", "Apple 未明确确认已恢复原安全设置", stage="security_rollback") from exc
        return {"status": True, "message": "Apple 双重认证已恢复为之前的安全设置", "security_rollback_confirmed": True}

    @staticmethod
    def _validate_apple_recovery_url(value: str) -> str:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or not (host == "apple.com" or host.endswith(".apple.com")):
            raise FlowError("recovery_email_invalid", "邮件中的 Apple 链接无效", stage="email_link")
        return value

    @staticmethod
    def _valid_new_password(password: str) -> bool:
        return (
            12 <= len(password) <= 64
            and bool(re.search(r"[A-Z]", password))
            and bool(re.search(r"[a-z]", password))
            and bool(re.search(r"\d", password))
            and bool(re.search(r"[^A-Za-z0-9]", password))
            and not any(character.isspace() for character in password)
            and not bool(re.search(r"(.)\1\1", password))
        )

    def _fill_recovery_birth_date(self, driver, dob: str):
        from selenium.webdriver.support.ui import WebDriverWait

        if not dob:
            raise FlowError("recovery_identity_failed", "管理站缺少生日资料")
        selector = self._recovery_birth_date_selector()
        cdp_backend_node = None
        fields = self._visible_fields_anywhere(
            driver, selector, 1,
        )
        if not fields:
            _, visible_text = self._login_snapshot(driver)
            generic_selector = (
                "input:not([type='hidden']):not([type='password']):not([type='submit']):"
                "not([type='button']):not([type='checkbox']):not([type='radio'])"
            )
            candidates = self._visible_fields_anywhere(driver, generic_selector, 1)
            if len(candidates) == 1:
                fields = candidates
            else:
                cdp_candidates = self._cdp_visible_input_nodes(driver)
                birthday_candidates = [node for node in cdp_candidates if any(
                    term in " ".join(node["attributes"].values()).lower()
                    for term in ("birthday", "birth date", "birthdate", "bday")
                )]
                selected = birthday_candidates if len(birthday_candidates) == 1 else cdp_candidates
                if len(selected) == 1:
                    cdp_backend_node = selected[0]["backend_node_id"]
            if not fields and cdp_backend_node is None:
                if not self._is_recovery_birthday_page(visible_text):
                    return
                raise FlowError(
                    "recovery_identity_failed", "无法安全定位 Apple 生日验证输入框", stage="identity",
                )
        numbers = re.findall(r"\d+", dob)
        if cdp_backend_node is not None:
            values = [dob]
        elif len(fields) == 1:
            values = [dob]
        elif len(numbers) == 3:
            year_index = next((index for index, value in enumerate(numbers) if len(value) == 4), 0)
            year = numbers[year_index]
            remaining = [value for index, value in enumerate(numbers) if index != year_index]
            by_kind = {"year": year, "month": remaining[0], "day": remaining[1]}
            values = []
            for field in fields:
                hint = " ".join(filter(None, (
                    field.get_attribute("name"), field.get_attribute("id"),
                    field.get_attribute("placeholder"), field.get_attribute("autocomplete"),
                ))).lower()
                kind = next((name for name in ("year", "month", "day") if name in hint), "")
                values.append(by_kind.get(kind, dob))
        else:
            raise FlowError("recovery_identity_failed", "管理站生日格式无法用于 Apple 验证")
        if cdp_backend_node is not None:
            if not self._type_cdp_input(driver, cdp_backend_node, values[0]):
                raise FlowError(
                    "recovery_identity_failed", "Apple 生日验证输入框未接受输入", stage="identity",
                )
        else:
            for field, value in zip(fields, values):
                field.clear()
                field.send_keys(value)
        self._click_button(driver, ("continue", "继续", "下一步"))

        def birthday_accepted(current_driver):
            state = self._recovery_state(current_driver)
            if state:
                self._raise_recovery_code(state)
            if cdp_backend_node is not None:
                return not self._cdp_node_visible(current_driver, cdp_backend_node)
            return not self._visible_fields_anywhere(current_driver, selector, 1)

        try:
            WebDriverWait(driver, 20).until(birthday_accepted)
        except FlowError:
            raise
        except Exception as exc:
            self._raise_recovery_block(driver)
            raise FlowError(
                "recovery_identity_failed", "Apple 未确认生日验证通过", stage="identity",
            ) from exc

    @staticmethod
    def _recovery_birth_date_selector() -> str:
        return (
            "input[name*='birth' i], input[id*='birth' i], input[placeholder*='birthday' i], "
            "input[aria-label*='birthday' i], input[autocomplete='bday'], "
            "input[autocomplete='bday-day'], input[autocomplete='bday-month'], "
            "input[autocomplete='bday-year']"
        )

    @staticmethod
    def _is_recovery_birthday_page(visible_text: str) -> bool:
        text = visible_text.lower()
        return any(term in text for term in (
            "verify your birthday", "verify your date of birth",
            "验证您的生日", "验证你的生日", "验证您的出生日期", "验证你的出生日期",
        ))

    @staticmethod
    def _cdp_document_text(driver) -> str:
        """Read rendered-component text from the flattened DOM tree."""
        try:
            root = driver.execute_cdp_cmd(
                "DOM.getDocument", {"depth": -1, "pierce": True},
            ).get("root", {})
        except Exception:
            return ""
        parts = []

        def visit(node, ignored=False):
            name = str(node.get("nodeName", "")).upper()
            ignored = ignored or name in {"SCRIPT", "STYLE", "NOSCRIPT"}
            value = str(node.get("nodeValue", "") or "").strip()
            if value and not ignored:
                parts.append(value)
            for key in ("children", "shadowRoots", "pseudoElements"):
                for child in node.get(key) or []:
                    visit(child, ignored)
            content_document = node.get("contentDocument")
            if content_document:
                visit(content_document, ignored)

        # Apple SPA documents can exceed 50k characters of rendered text once
        # nested iframes and Shadow DOM are pierced.  A low cap can truncate
        # before the late security-challenge copy, so keep a generous bound.
        visit(root)
        return "\n".join(parts)[:300000]

    @staticmethod
    def _cdp_visible_input_nodes(driver, include_password: bool = False) -> list[dict]:
        try:
            root = driver.execute_cdp_cmd(
                "DOM.getDocument", {"depth": -1, "pierce": True},
            ).get("root", {})
        except Exception:
            return []
        result = []
        seen = set()

        def visit(node):
            backend_node_id = node.get("backendNodeId")
            attributes_list = node.get("attributes") or []
            attributes = {
                str(attributes_list[index]).lower(): str(attributes_list[index + 1])
                for index in range(0, len(attributes_list) - 1, 2)
            }
            node_name = str(node.get("nodeName", "")).upper()
            input_type = attributes.get("type", "text").lower()
            excluded = input_type in {
                "hidden", "submit", "button", "checkbox", "radio", "file",
            } or (input_type == "password" and not include_password)
            if (
                backend_node_id and backend_node_id not in seen
                and node_name in {"INPUT", "TEXTAREA"} and not excluded
                and "disabled" not in attributes
            ):
                try:
                    model = driver.execute_cdp_cmd(
                        "DOM.getBoxModel", {"backendNodeId": backend_node_id},
                    ).get("model", {})
                    content = model.get("content") or []
                    if len(content) == 8:
                        width = max(content[0::2]) - min(content[0::2])
                        height = max(content[1::2]) - min(content[1::2])
                        if width > 0 and height > 0:
                            result.append({
                                "backend_node_id": backend_node_id,
                                "attributes": attributes,
                            })
                            seen.add(backend_node_id)
                except Exception:
                    pass
            for key in ("children", "shadowRoots", "pseudoElements"):
                for child in node.get(key) or []:
                    visit(child)
            content_document = node.get("contentDocument")
            if content_document:
                visit(content_document)

        visit(root)
        return result

    @staticmethod
    def _cdp_security_answer_nodes(driver) -> list[dict]:
        nodes = []
        labeled = []
        for node in AppleFlow._cdp_visible_input_nodes(driver, include_password=True):
            attributes = node.get("attributes") or {}
            input_type = str(attributes.get("type", "text")).lower()
            identity = " ".join(str(attributes.get(key, "")) for key in (
                "name", "id", "autocomplete", "aria-label", "placeholder",
            )).lower()
            # Apple renders the security-answer controls as type=password in
            # the current component.  They are not the account password field
            # because the identity/username controls are filtered separately.
            if input_type in {"email", "tel", "number"}:
                continue
            if any(term in identity for term in ("username", "appleid", "account name", "email")):
                continue
            nodes.append(node)
            if any(term in identity for term in ("answer", "答案")):
                labeled.append(node)
        return labeled if len(labeled) >= 2 else nodes

    @staticmethod
    def _type_cdp_input(driver, backend_node_id: int, value: str) -> bool:
        try:
            resolved = driver.execute_cdp_cmd(
                "DOM.resolveNode", {"backendNodeId": backend_node_id},
            ).get("object", {})
            object_id = resolved.get("objectId")
            if not object_id:
                return False
            set_result = driver.execute_cdp_cmd("Runtime.callFunctionOn", {
                "objectId": object_id,
                "functionDeclaration": "function(value){"
                "const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
                "setter.call(this,'');this.focus();setter.call(this,value);"
                "this.dispatchEvent(new Event('input',{bubbles:true,composed:true}));"
                "this.dispatchEvent(new Event('change',{bubbles:true,composed:true}));"
                "return String(this.value || '');}",
                "arguments": [{"object": {"value": value}}],
                "returnByValue": True,
            })
            current_value = set_result.get("result", {}).get("value")
            if not current_value:
                driver.execute_cdp_cmd("DOM.focus", {"backendNodeId": backend_node_id})
                driver.execute_cdp_cmd("Input.insertText", {"text": value})
            checked = driver.execute_cdp_cmd("Runtime.callFunctionOn", {
                "objectId": object_id,
                "functionDeclaration": "function(){return String(this.value || '');}",
                "returnByValue": True,
            })
            return bool(checked.get("result", {}).get("value"))
        except Exception:
            return False

    @staticmethod
    def _cdp_node_visible(driver, backend_node_id: int) -> bool:
        try:
            model = driver.execute_cdp_cmd(
                "DOM.getBoxModel", {"backendNodeId": backend_node_id},
            ).get("model", {})
            content = model.get("content") or []
            return len(content) == 8 and (
                max(content[0::2]) - min(content[0::2]) > 0
                and max(content[1::2]) - min(content[1::2]) > 0
            )
        except Exception:
            return False

    def _choose_security_question_recovery(self, driver):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        if self._visible_fields_anywhere(driver, "input[type='text'], input:not([type])", 2):
            return
        _, visible_text = self._login_snapshot(driver)
        text = visible_text.lower()
        security_terms = ("answer security questions", "security questions to continue",
                            "回答您的安全提示问题", "回答你的安全提示问题", "回答安全提示问题", "回答安全问题")
        method_terms = ("select how you want to reset", "选择密码重置方式", "选择要如何重置")
        if any(term in text for term in security_terms) and any(term in text for term in method_terms):
            radios = self._all_fields_anywhere(driver, "input[type='radio']")
            selected = False
            for radio in radios:
                context_parts = [driver.execute_script(
                    "const e=arguments[0]; return (e.closest('label') || e.parentElement || e).innerText || '';",
                    radio,
                ) or ""]
                radio_id = radio.get_attribute("id") or ""
                if radio_id:
                    context_parts.extend(label.text for label in driver.find_elements(
                        By.CSS_SELECTOR, f"label[for='{radio_id}']"
                    ))
                context_parts.extend(filter(None, (
                    radio.get_attribute("value"), radio.get_attribute("aria-label"),
                    radio.get_attribute("name"), radio_id,
                )))
                if any(term in " ".join(context_parts).lower() for term in security_terms):
                    driver.execute_script("arguments[0].click();", radio)
                    selected = True
                    break
            if not selected and len(radios) >= 2:
                email_position = text.find("get an email")
                security_position = min(text.find(term) for term in security_terms if term in text)
                if email_position >= 0 and email_position < security_position:
                    driver.execute_script("arguments[0].click();", radios[1])
                    selected = True
            if not selected:
                raise FlowError("recovery_identity_failed", "无法安全选择 Apple 密保恢复路径")
            self._click_button(driver, ("continue", "继续", "下一步"))
            try:
                WebDriverWait(driver, 20).until(
                    lambda d: not any(term in self._login_snapshot(d)[1].lower() for term in method_terms)
                )
            except Exception as exc:
                raise FlowError("page_changed", "Apple 密保恢复路径未继续") from exc
            return
        labels = (
            "answer security questions", "security questions", "回答安全提示问题", "回答安全问题", "密保问题",
        )
        lowered = tuple(label.lower() for label in labels)
        elements = self._visible_fields_anywhere(driver, "button, a, input[type='submit'], [role='button']", 1)
        for element in elements:
            value = (element.text or element.get_attribute("value") or element.get_attribute("aria-label") or "").lower()
            if any(label in value for label in lowered):
                element.click()
                return

    def _choose_email_recovery(self, driver):
        """Choose Apple's explicitly-labelled email reset option, never by index."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        _, visible_text = self._login_snapshot(driver)
        text = visible_text.lower()
        email_terms = (
            "get an email", "email me", "send an email", "reset by email",
            "通过电子邮件", "获取电子邮件", "发送电子邮件", "邮件重置",
        )
        method_terms = ("select how you want to reset", "选择密码重置方式", "选择要如何重置")
        if not any(term in text for term in email_terms):
            raise FlowError("recovery_email_unavailable", "Apple 未提供邮件密码重置方式", stage="email_request")
        radios = self._all_fields_anywhere(driver, "input[type='radio']")
        selected = False
        for radio in radios:
            context_parts = [driver.execute_script(
                "const e=arguments[0]; return (e.closest('label') || e.parentElement || e).innerText || '';",
                radio,
            ) or ""]
            radio_id = radio.get_attribute("id") or ""
            if radio_id:
                context_parts.extend(label.text for label in driver.find_elements(
                    By.CSS_SELECTOR, f"label[for='{radio_id}']"
                ))
            context_parts.extend(filter(None, (
                radio.get_attribute("value"), radio.get_attribute("aria-label"),
                radio.get_attribute("name"), radio_id,
            )))
            if any(term in " ".join(context_parts).lower() for term in email_terms):
                driver.execute_script("arguments[0].click();", radio)
                selected = True
                break
        if not selected:
            raise FlowError("recovery_email_unavailable", "无法安全定位 Apple 邮件重置选项", stage="email_request")
        self._click_button(driver, ("continue", "继续", "下一步"))
        try:
            WebDriverWait(driver, 20).until(
                lambda d: not any(term in self._login_snapshot(d)[1].lower() for term in method_terms)
            )
        except Exception as exc:
            raise FlowError("page_changed", "Apple 邮件重置选项未继续", stage="email_request") from exc

    def _choose_reset_password_path(self, driver):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        _, visible_text = self._login_snapshot(driver)
        text = visible_text.lower()
        password_terms = ("reset my password", "重设我的密码", "重置我的密码", "重设密码", "重置密码")
        selection_terms = (
            "select what information", "选择要重设的信息", "选择要重置的信息",
            "选择你要重设的信息", "选择你要重置的信息",
        )
        if not any(term in text for term in password_terms) or not any(term in text for term in selection_terms):
            return
        radios = self._visible_fields_anywhere(driver, "input[type='radio']", 1)
        selected = False
        for radio in radios:
            context_parts = [driver.execute_script(
                "const e=arguments[0]; return (e.closest('label') || e.parentElement || e).innerText || '';",
                radio,
            ) or ""]
            radio_id = radio.get_attribute("id") or ""
            if radio_id:
                context_parts.extend(label.text for label in driver.find_elements(
                    By.CSS_SELECTOR, f"label[for='{radio_id}']"
                ))
            context_parts.extend(filter(None, (
                radio.get_attribute("value"), radio.get_attribute("aria-label"),
                radio.get_attribute("name"), radio_id,
            )))
            context = " ".join(context_parts).lower()
            if any(term in context for term in password_terms):
                if not radio.is_selected():
                    radio.click()
                selected = True
                break
        if not selected:
            password_position = min(text.find(term) for term in password_terms if term in text)
            security_positions = [text.find(term) for term in ("security questions", "安全提示问题", "安全问题")
                                  if term in text]
            if security_positions and password_position < min(security_positions):
                if radios and not radios[0].is_selected():
                    radios[0].click()
                # Apple 当前页面使用不可见原生 radio 配合可见样式，密码选项默认被选中。
                selected = True
        if not selected:
            raise FlowError("recovery_identity_failed", "无法安全选择 Apple 密码重置路径")
        self._click_button(driver, ("continue", "继续", "下一步"))
        try:
            WebDriverWait(driver, 20).until(
                lambda d: not any(term in self._login_snapshot(d)[1].lower() for term in selection_terms)
            )
        except Exception as exc:
            raise FlowError("page_changed", "Apple 密码重置路径未继续") from exc

    def _recovery_state(self, driver) -> str | None:
        _, text = self._login_snapshot(driver)
        return classify_recovery_state(text)

    def _raise_recovery_block(self, driver):
        state = self._recovery_state(driver)
        if state:
            self._raise_recovery_code(state)

    @staticmethod
    def _raise_recovery_code(state: str):
        messages = {
            "locked": "Apple 账号已锁定，已停止恢复",
            "two_factor": "Apple 要求双重认证，已停止恢复",
            "recovery_wait_required": "Apple 要求进入账号恢复等待期，未提交恢复申请",
            "recovery_answer_invalid": "Apple 拒绝了密保答案",
            "recovery_identity_failed": "Apple 拒绝了生日身份验证",
        }
        raise FlowError(state, messages.get(state, "Apple 恢复流程无法继续"))

    def _workflow(self, driver, payload: dict, job_id: str = "") -> dict:
        """Run all due account operations in one authenticated browser session."""
        context = payload.get("_job_context") if isinstance(payload.get("_job_context"), dict) else {}
        progress = context.get("progress") if callable(context.get("progress")) else lambda *args, **kwargs: None
        authorize = context.get("authorize") if callable(context.get("authorize")) else lambda feature: True
        requested = [str(item) for item in payload.get("features", [])]
        allowed = {"two_factor", "password_check", "profile_restore", "device_cleanup", "password_update"}
        if not requested or any(item not in allowed for item in requested):
            raise FlowError("invalid_workflow", "复合任务功能列表无效", stage="preflight")

        step_results = {}
        progress("login", "password_check")
        state = self._stage("login", lambda: self._sign_in(driver, payload, job_id), True)
        login_result = {
            "status": "success" if state == "normal" else "failed",
            "stage": "login", "state": state,
        }
        for feature in ("two_factor", "password_check"):
            if feature in requested:
                step_results[feature] = dict(login_result)
                progress("login", feature, step_results[feature])
        if state != "normal":
            return {
                "status": True, "state": state,
                "message": "账号登录未完成", "step_results": step_results,
            }

        reconciliation = payload.get("reconciliation")
        if isinstance(reconciliation, dict):
            kind = str(reconciliation.get("kind") or "")
            progress("reconciling", kind)
            if kind == "device_cleanup":
                result = self._reconcile_devices(driver, reconciliation)
            elif kind == "profile_restore":
                result = self._reconcile_profile(driver, payload.get("profile_restore") or {})
            elif kind == "password_update":
                result = {"reconciliation_status": "confirmed", "credential_valid": True}
            else:
                raise FlowError("invalid_reconciliation", "待确认核验类型无效", stage="reconciling")
            return {
                "status": True, "state": "normal", "message": "只读核验完成",
                "reconciliation": {"kind": kind, **result}, "step_results": step_results,
            }

        profile = None
        profile_results = None
        if "profile_restore" in requested:
            if not authorize("profile_restore"):
                step_results["profile_restore"] = {"status": "cancelled", "stage": "authorization"}
            else:
                progress("executing", "profile_restore")
                profile_out = self._profile_guard(
                    driver, payload.get("profile_restore") or {}, job_id,
                    mutation_callback=lambda: progress("mutation_submitted", "profile_restore"),
                )
                profile = profile_out.get("profile")
                profile_results = profile_out.get("profile_results")
                states = [str(item.get("status") or "") for item in (profile_results or {}).values()
                          if isinstance(item, dict)]
                profile_status = "uncertain" if "uncertain" in states else (
                    "failed" if any(item in {"detect_failed", "restore_failed", "manual_required"} for item in states)
                    else "success"
                )
                step_results["profile_restore"] = {
                    "status": profile_status, "stage": "result_confirmation",
                    "message": str((profile or {}).get("summary") or profile_out.get("note") or "资料处理完成"),
                }
                progress("result_confirmation", "profile_restore", step_results["profile_restore"])

        if "device_cleanup" in requested:
            if not authorize("device_cleanup"):
                step_results["device_cleanup"] = {"status": "cancelled", "stage": "authorization"}
            else:
                progress("executing", "device_cleanup")
                try:
                    device = self._delete_devices_authenticated(
                        driver, mutation_callback=lambda: progress("mutation_submitted", "device_cleanup"),
                    )
                except FlowError as exc:
                    status = "uncertain" if exc.device_removal_started else "failed"
                    step_results["device_cleanup"] = {
                        "status": status, "stage": exc.stage, "error_code": exc.code,
                        "message": str(exc), **exc.details,
                    }
                    progress(exc.stage or "failed", "device_cleanup", step_results["device_cleanup"])
                    return {
                        "status": False, "state": "normal", "error_code": exc.code,
                        "message": str(exc), "step_results": step_results,
                        "device_removal_started": exc.device_removal_started, **exc.details,
                        **({"profile": profile} if profile else {}),
                        **({"profile_results": profile_results} if profile_results else {}),
                    }
                step_results["device_cleanup"] = {
                    "status": "success", "stage": "result_confirmation",
                    "removed_count": int(device.get("removed_count") or 0),
                    "no_devices": bool(device.get("no_devices")),
                }
                progress("result_confirmation", "device_cleanup", step_results["device_cleanup"])

        new_password = None
        if "password_update" in requested:
            if not authorize("password_update"):
                step_results["password_update"] = {"status": "cancelled", "stage": "authorization"}
            else:
                progress("executing", "password_update")
                try:
                    updated = self._update_password_authenticated(
                        driver, payload,
                        mutation_callback=lambda: progress("mutation_submitted", "password_update"),
                    )
                except FlowError as exc:
                    status = "uncertain" if exc.password_submitted else "failed"
                    step_results["password_update"] = {
                        "status": status, "stage": exc.stage, "error_code": exc.code,
                        "message": str(exc), "password_submitted": exc.password_submitted,
                    }
                    progress(exc.stage or "failed", "password_update", step_results["password_update"])
                    return {
                        "status": False, "state": "normal", "error_code": exc.code,
                        "message": str(exc), "step_results": step_results,
                        "password_submitted": exc.password_submitted,
                        **({"profile": profile} if profile else {}),
                        **({"profile_results": profile_results} if profile_results else {}),
                    }
                new_password = updated.get("password")
                step_results["password_update"] = {
                    "status": "success", "stage": "result_confirmation",
                    "password_change_confirmed": True,
                }
                progress("result_confirmation", "password_update", step_results["password_update"])

        return {
            "status": True, "state": "normal", "message": "复合自动化任务完成",
            "step_results": step_results,
            **({"profile": profile} if profile else {}),
            **({"profile_results": profile_results} if profile_results else {}),
            **({"password": new_password, "password_change_confirmed": True} if new_password else {}),
        }

    def _reconcile_devices(self, driver, reconciliation: dict) -> dict:
        self._stage("device_list", lambda: self._open_devices_page(driver), True)
        cards = self._stage("device_list", lambda: self._wait_device_cards_stable(driver), True)
        target = str(reconciliation.get("target_hash") or "")
        before_count = max(int(reconciliation.get("before_count") or 0), 0)
        hashes = [self._device_card_fingerprint(card) for card in cards]
        if not cards or (target and target not in hashes):
            status = "confirmed"
        elif target and hashes.count(target) == 1:
            status = "not_applied"
        else:
            status = "manual_required"
        return {
            "reconciliation_status": status, "current_count": len(cards),
            "before_count": before_count, "count_changed": bool(before_count and len(cards) != before_count),
        }

    def _reconcile_profile(self, driver, config: dict) -> dict:
        if not self._open_profile_page(driver):
            return {"reconciliation_status": "manual_required"}
        observed = self._read_account_profile(driver)
        expected = {
            "name": str(config.get("name") or "").strip(),
            "dob": normalize_profile_date(str(config.get("dob") or "")),
            "country": str(config.get("country") or "").strip(),
            "language": str(config.get("language") or "").strip(),
        }
        matches = []
        for key, value in expected.items():
            if not value or not observed.get(key):
                continue
            matches.append(
                normalize_profile_date(value) == normalize_profile_date(observed[key])
                if key == "dob" else profile_matches(value, observed[key])
            )
        status = ("confirmed" if all(matches) else "not_applied") if len(matches) == 4 else "manual_required"
        return {"reconciliation_status": status}

    def _update_password(self, driver, payload: dict) -> dict:
        state = self._stage("login", lambda: self._sign_in(driver, payload))
        if state != "normal":
            raise FlowError("login_required", "账号登录未完成，跳过改密", stage="login")
        callback = (payload.get('_job_context') or {}).get('progress')
        return self._update_password_authenticated(driver, payload,
            mutation_callback=(lambda: callback('mutation_submitted', 'password_update')) if callable(callback) else None)

    def _update_password_authenticated(self, driver, payload: dict, mutation_callback=None) -> dict:
        from selenium.webdriver.support.ui import WebDriverWait

        def open_password_form():
            self._get(driver, settings.account_url)
            # The account settings page and its password form live inside Apple
            # web components (Shadow DOM / nested iframes), so plain Selenium
            # find_elements cannot see them.  Use the piercing helpers below.
            self._click_button_anywhere(
                driver, ("sign-in & security", "登录与安全", "登录和安全"), include_links=True,
            )
            self._click_button_anywhere(driver, ("password", "密码"), include_links=True)
            return WebDriverWait(driver, settings.render_timeout).until(
                lambda d: self._visible_fields_anywhere(d, "input[type='password']", 3)
            )

        password_inputs = self._stage("password_form", open_password_form)
        if len(password_inputs) < 3:
            raise FlowError("password_form_missing", "未找到密码修改表单", stage="password_form")
        new_password = str(payload.get("new_password") or generate_password())
        if not self._valid_new_password(new_password):
            raise FlowError("password_form_missing", "受控改密密码不符合安全要求", stage="password_form")

        def fill_password():
            for element, value in zip(password_inputs[-3:], (payload["password"], new_password, new_password)):
                element.clear()
                element.send_keys(value)

        self._stage("password_form", fill_password)
        self._stage("password_form", lambda: self._raise_password_policy_error(driver))
        if mutation_callback:
            mutation_callback()
        try:
            self._stage(
                "password_form",
                lambda: self._click_button_anywhere(driver, ("change password", "更改密码", "修改密码", "save", "保存")),
                password_submitted=True,
            )
            self._stage(
                "password_submitted", lambda: self._confirm_password_change(driver, password_inputs[-3:]),
                password_submitted=True,
            )
        except FlowError as exc:
            exc.details['password_change_started'] = True
            raise
        return {
            "status": True,
            "message": "密码更新完成",
            "password": new_password,
            "password_change_confirmed": True,
        }

    def _delete_devices(self, driver, payload: dict, job_id: str = "") -> dict:
        """Remove every device card, proving progress after each confirmation."""
        state = self._stage("login", lambda: self._sign_in(driver, payload, job_id), True)
        if state != "normal":
            raise FlowError("login_required", "账号登录未完成，跳过设备清理", stage="login")
        callback = (payload.get('_job_context') or {}).get('progress')
        return self._delete_devices_authenticated(driver,
            mutation_callback=(lambda: callback('mutation_submitted', 'device_cleanup')) if callable(callback) else None)

    def _delete_devices_authenticated(self, driver, mutation_callback=None) -> dict:
        from selenium.webdriver.support.ui import WebDriverWait

        removed_count = 0
        self._stage("device_list", lambda: self._open_devices_page(driver), True)
        for removal_index in range(101):
            cards = self._stage("device_list", lambda: self._wait_device_cards_stable(driver), True)
            if not cards:
                return {
                    "status": True,
                    "message": (
                        "账号当前没有可移除设备" if removed_count == 0
                        else f"已从账户移除 {removed_count} 台设备"
                    ),
                    "removed_count": removed_count,
                    "no_devices": removed_count == 0,
                    "device_removal_confirmed": True,
                    "device_removal_started": removed_count > 0,
                }
            if removal_index >= 100:
                raise FlowError(
                    "device_cleanup_limit", "设备数量超过单次任务的 100 台安全上限",
                    stage="device_list",
                )

            before_count = len(cards)
            target_hash = self._device_card_fingerprint(cards[0])
            remove_from_account_labels = (
                "remove from account", "从账户中移除", "從帳戶中移除",
            )
            detail_state = self._open_device_card_details(
                driver, cards[0], remove_from_account_labels,
            )
            if detail_state == "click_failed":
                raise FlowError(
                    "device_card_unavailable", "无法打开 Apple 设备详情卡片", stage="device_detail",
                )
            if detail_state == "unopened":
                raise FlowError(
                    "device_detail_unopened", "点击设备卡片后仍停留在设备列表",
                    stage="device_detail", retryable=True,
                )
            if detail_state != "ready":
                raise FlowError(
                    "device_remove_button_missing", "未找到“从账户中移除”按钮",
                    stage="device_detail",
                )
            self._stage(
                "device_detail",
                lambda: self._click_exact_control_anywhere(
                    driver, remove_from_account_labels, include_links=True,
                ),
            )
            # Prove the destructive confirmation exists before entering the
            # ambiguous mutation boundary. Exact matching is mandatory:
            # substring matching "移除" could otherwise select "不移除".
            self._stage(
                "device_confirmation",
                lambda: WebDriverWait(driver, 20).until(
                    lambda d: self._has_exact_control_anywhere(
                        d, ("remove", "移除"), include_links=False,
                    )
                ),
            )
            if mutation_callback:
                mutation_callback()
            self._stage(
                "device_confirmation",
                lambda: self._click_exact_control_anywhere(
                    driver, ("remove", "移除"), include_links=False,
                ),
                device_removal_started=True,
            )

            def removal_confirmed(current_driver):
                if self._has_visible_loading_indicator(current_driver):
                    return False
                if not self._device_page_ready(current_driver):
                    return False
                current_cards = self._device_cards_anywhere(current_driver)
                return (True, current_cards) if len(current_cards) < before_count else False

            try:
                WebDriverWait(driver, settings.render_timeout).until(removal_confirmed)
            except Exception as exc:
                raise FlowError(
                    "device_removal_unconfirmed",
                    "Apple 未明确确认设备已移除，已停止自动重试",
                    stage="device_confirmation", device_removal_started=True,
                    details={"device_target_hash": target_hash, "device_before_count": before_count},
                ) from exc
            removed_count += 1

    def _open_device_card_details(self, driver, card, labels: tuple[str, ...]) -> str:
        """Open a device card and prove the detail action is visible.

        Apple loads modal contents asynchronously after the click succeeds.
        Wait for its action control instead of re-clicking the dimmed list.
        Retry one unresponsive click only after proving the same unobscured
        card is still on the unchanged, idle list page.
        """
        from selenium.webdriver.support.ui import WebDriverWait

        clicked = False
        original_url = str(getattr(driver, "current_url", "") or "")
        original_identity = self._device_card_identity(card)
        retried_unopened = False
        for attempt in range(4):
            if self._device_detail_dialog_visible(driver):
                # The device list remains behind Apple's modal. Never click
                # through the overlay or mistake those cards for failed navigation.
                try:
                    WebDriverWait(driver, max(settings.render_timeout, 60)).until(
                        lambda current: self._has_exact_control_anywhere(current, labels, include_links=True)
                    )
                    return "ready"
                except Exception:
                    return "button_missing"
            current_card = card
            if attempt:
                refreshed = self._device_cards_anywhere(driver)
                if not refreshed:
                    break
                current_card = refreshed[0]
            if retried_unopened and (
                    str(getattr(driver, "current_url", "") or "") != original_url
                    or self._device_card_identity(current_card) != original_identity
                    or self._has_visible_loading_indicator(driver)
                    or not self._device_card_unobscured(driver, current_card)):
                return "unopened"
            targets = self._device_card_click_targets(driver, current_card)
            try:
                # Structural metadata only: never persist account/device text,
                # attributes containing identifiers, or page HTML.
                driver._device_click_diagnostics = driver.execute_script("""
                    return arguments[0].map(e => {
                        const r = e.getBoundingClientRect();
                        return {tag: e.localName, role: e.getAttribute('role'),
                            width: Math.round(r.width), height: Math.round(r.height),
                            cursor: getComputedStyle(e).cursor};
                    });
                """, targets)
            except Exception:
                pass
            if not targets:
                continue
            target = targets[min(attempt, len(targets) - 1)]
            if not self._click_visible(target, driver):
                continue
            clicked = True
            try:
                WebDriverWait(driver, max(settings.render_timeout, 60)).until(
                    lambda current: self._has_exact_control_anywhere(
                        current, labels, include_links=True,
                    )
                )
                return "ready"
            except Exception:
                if self._device_detail_dialog_visible(driver):
                    return "button_missing"
                remaining = self._device_cards_anywhere(driver)
                if not remaining:
                    return "button_missing"
                if (not retried_unopened and attempt < 3 and original_identity
                        and str(getattr(driver, "current_url", "") or "") == original_url
                        and self._device_card_identity(remaining[0]) == original_identity
                        and not self._has_visible_loading_indicator(driver)
                        and self._device_card_unobscured(driver, remaining[0])):
                    retried_unopened = True
                    continue
                return "unopened"
        if not clicked:
            return "click_failed"
        if self._device_detail_dialog_visible(driver):
            return "button_missing"
        return "unopened" if self._device_cards_anywhere(driver) else "button_missing"

    @staticmethod
    def _device_card_unobscured(driver, card) -> bool:
        """Fail closed when an overlay or a detached card would receive a retry."""
        try:
            return driver.execute_script("""
                const e = arguments[0], r = e.getBoundingClientRect();
                if (!e.isConnected || r.width <= 0 || r.height <= 0) return false;
                let hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                while (hit && hit.shadowRoot) {
                    const inner = hit.shadowRoot.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                    if (!inner || inner === hit) break;
                    hit = inner;
                }
                return hit === e || e.contains(hit);
            """, card) is True
        except Exception:
            return False

    @staticmethod
    def _device_detail_dialog_visible(driver) -> bool:
        try:
            return driver.execute_script("""
                const visit = root => Array.from(root.querySelectorAll('*')).some(e => {
                    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
                    if (e.matches('[role="dialog"], [aria-modal="true"], dialog[open]')
                        && r.width > 0 && r.height > 0 && s.visibility !== 'hidden'
                        && s.display !== 'none') return true;
                    return e.shadowRoot ? visit(e.shadowRoot) : false;
                });
                return visit(document);
            """) is True
        except Exception:
            return False

    @staticmethod
    def _device_card_click_targets(driver, card):
        """Return likely click owners from outer handler to visible child."""
        try:
            targets = driver.execute_script(
                r"""
                const start = arguments[0], result = [], seen = new Set();
                const add = element => {
                    if (element && !seen.has(element)) { seen.add(element); result.push(element); }
                };
                const parentOf = element => element.parentElement || element.getRootNode?.().host || null;
                const bounds = start.getBoundingClientRect();
                let node = start, depth = 0;
                while (node && depth++ < 10) {
                    const rect = node.getBoundingClientRect();
                    if (node.matches('main, [role="main"], body, html')
                        || rect.width > Math.max(bounds.width * 1.5, 500)
                        || rect.height > Math.max(bounds.height * 1.5, 400)) break;
                    const style = getComputedStyle(node);
                    const marker = [node.className, node.id, node.getAttribute('data-testid')]
                        .filter(Boolean).join(' ').toLowerCase();
                    if (node.matches("button, a[href], [role='button'], [tabindex]:not([tabindex='-1'])")
                        || style.cursor === 'pointer'
                        || (marker.includes('device') && ['card', 'tile', 'item'].some(x => marker.includes(x)))) {
                        add(node);
                    }
                    if (node.matches('main, [role="main"]')) break;
                    node = parentOf(node);
                }
                add(start);
                return result;
                """, card,
            ) or []
            return [item for item in targets if item.is_displayed() and item.is_enabled()]
        except Exception:
            return [card]

    @staticmethod
    def _device_card_fingerprint(card) -> str:
        identity = AppleFlow._device_card_identity(card).encode("utf-8", "ignore")
        key = settings.token.encode("utf-8", "ignore")
        return hmac.new(key, identity, hashlib.sha256).hexdigest()

    def _open_devices_page(self, driver):
        from selenium.webdriver.support.ui import WebDriverWait

        self._get(driver, settings.account_url)
        self._click_button_anywhere(
            driver, ("devices", "设备", "裝置"), include_links=True,
        )
        try:
            WebDriverWait(driver, settings.render_timeout).until(self._device_page_ready)
        except Exception as exc:
            raise FlowError(
                "device_page_unready", "Apple 设备页未完成加载",
                stage="device_list", retryable=True,
            ) from exc

    def _wait_device_cards_stable(self, driver):
        """Return a stable card snapshot; an empty list is a valid result."""
        from selenium.webdriver.support.ui import WebDriverWait

        previous = None
        stable_reads = 0

        def snapshot(current_driver):
            nonlocal previous, stable_reads
            if self._has_visible_loading_indicator(current_driver) or not self._device_page_ready(current_driver):
                previous, stable_reads = None, 0
                return False
            cards = self._device_cards_anywhere(current_driver)
            identities = tuple(self._device_card_identity(card) for card in cards)
            if identities == previous:
                stable_reads += 1
            else:
                previous, stable_reads = identities, 1
            return (True, cards) if stable_reads >= 2 else False

        return WebDriverWait(driver, settings.render_timeout, poll_frequency=0.5).until(snapshot)[1]

    def _device_page_ready(self, driver) -> bool:
        try:
            text = " ".join(self._visible_page_text(driver).lower().split())
            url = str(getattr(driver, "current_url", "") or "").lower()
            cards = self._device_cards_anywhere(driver)
        except Exception:
            return False
        page_copy = any(term in text for term in (
            "与你帐户相关联设备", "与你账户相关联设备",
            "與你帳戶相關聯裝置", "devices associated with your account",
            "devices linked to your account",
        ))
        return bool(cards or page_copy or ("device" in url and any(term in text for term in ("devices", "设备", "裝置"))))

    @staticmethod
    def _device_card_identity(card) -> str:
        try:
            return " ".join(filter(None, (
                str(card.text or "").strip(),
                str(card.get_attribute("aria-label") or "").strip(),
                str(card.get_attribute("data-testid") or "").strip(),
            )))[:500]
        except Exception:
            return ""

    @staticmethod
    def _device_cards_anywhere(driver):
        """Find visible device cards in the current page, open shadows or frames."""
        from selenium.webdriver.common.by import By

        script = r"""
            const candidates = [], seen = new Set();
            const visible = e => {
                const s = getComputedStyle(e), r = e.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden'
                    && Number(s.opacity || 1) !== 0 && r.width > 80 && r.height > 40;
            };
            const value = e => [e.innerText, e.getAttribute('aria-label'), e.getAttribute('title')]
                .filter(Boolean).join('\n').trim();
            const visit = root => {
                for (const e of root.querySelectorAll('*')) {
                    if (e.shadowRoot) visit(e.shadowRoot);
                    if (!visible(e) || e.closest('nav, aside')) continue;
                    const text = value(e), lower = text.toLowerCase();
                    if (!text || ['devices', '设备', '裝置'].includes(lower)) continue;
                    if (lower.includes('remove from account') || lower.includes('从账户中移除')
                        || lower.includes('從帳戶中移除')) continue;
                    const marker = [e.className, e.id, e.getAttribute('data-testid'), e.getAttribute('href')]
                        .filter(Boolean).join(' ').toLowerCase();
                    const lines = text.split(/\n+/).map(x => x.trim()).filter(Boolean);
                    const inMain = !!e.closest('main, [role="main"]');
                    const media = !!e.querySelector('img, picture, svg');
                    const tag = (e.localName || '').toLowerCase();
                    const explicitCard = (marker.includes('device') || tag.includes('device'))
                        && ['card', 'tile', 'item'].some(term => marker.includes(term) || tag.includes(term));
                    const rect = e.getBoundingClientRect();
                    // Account avatars and navigation tiles also contain images
                    // and multiple lines. Require device model evidence for the
                    // visual fallback; explicit device-card markup wins below.
                    const deviceModel = /\b(?:iphone|ipad|ipod|macbook|imac|mac\s*(?:mini|pro|studio)|apple\s*(?:watch|tv|vision)|homepod|airpods)\b/i.test(text);
                    const visualCard = inMain && media && deviceModel && lines.length >= 2
                        && rect.height <= 400 && rect.width <= 900;
                    if (!(explicitCard || visualCard)) continue;
                    let candidate = e.closest("button, a[href], [role='button'], [tabindex]:not([tabindex='-1'])");
                    if (!candidate) {
                        const parentOf = element => element.parentElement || element.getRootNode?.().host || null;
                        let node = e, depth = 0;
                        while (node && depth++ < 8 && !node.matches('main, [role="main"]')) {
                            if (getComputedStyle(node).cursor === 'pointer') { candidate = node; break; }
                            node = parentOf(node);
                        }
                    }
                    candidate = candidate || e;
                    if (!seen.has(candidate)) { seen.add(candidate); candidates.push(candidate); }
                }
            };
            visit(document);
            candidates.sort((a, b) => {
                const ar = a.getBoundingClientRect(), br = b.getBoundingClientRect();
                return ar.width * ar.height - br.width * br.height;
            });
            const result = [];
            for (const candidate of candidates) {
                if (!result.some(selected => candidate.contains(selected))) result.push(candidate);
            }
            return result;
        """

        def search(depth=0):
            try:
                cards = list(driver.execute_script(script) or [])
                cards = [card for card in cards if card.is_displayed() and card.is_enabled()]
                if cards:
                    return cards
            except Exception:
                pass
            if depth >= 3:
                return []
            try:
                frames = driver.find_elements(By.CSS_SELECTOR, "iframe")
            except Exception:
                frames = []
            for frame in frames:
                switched = False
                found = []
                try:
                    if not frame.is_displayed():
                        continue
                    driver.switch_to.frame(frame)
                    switched = True
                    found = search(depth + 1)
                    if found:
                        return found
                except Exception:
                    pass
                finally:
                    if switched and not found:
                        try:
                            driver.switch_to.parent_frame()
                        except Exception:
                            driver.switch_to.default_content()
            return []

        try:
            driver.switch_to.default_content()
            return search()
        except Exception:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            return []

    def _sign_in(self, driver, payload: dict, job_id: str = "") -> str:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        self._stage("login", lambda: self._get(driver, settings.sign_in_url), True)
        self._stage("login", lambda: self._switch_auth_frame(driver), True)
        self._stage("login", lambda: self._type_first(driver, self._account_selectors(), payload["username"]), True)
        self._stage("login", lambda: self._click_button(driver, ("continue", "sign in", "继续", "登录")), True)
        self._stage(
            "login", lambda: self._type_first(driver, [(By.CSS_SELECTOR, "input[type='password']")], payload["password"]),
            True,
        )
        self._stage(
            "login", lambda: self._click_button(driver, ("continue", "sign in", "继续", "登录")),
            True, password_submitted=True,
        )
        try:
            state = self._login_state(driver, password_submitted=True)
        except FlowError as exc:
            if exc.code != "page_unready":
                raise
            state = self._recover_post_login_loading(driver, job_id)
        # Performance logs are a destructive read in Selenium. Cache them as
        # soon as the credential response has arrived so the final diagnostic
        # can still explain an Apple HTTP 409 without retaining request URLs,
        # headers, cookies or form payloads.
        self._collect_performance_logs(driver)
        logger.info("Apple login state after credential submission state=%s", state)
        if state == "unknown" and self._is_page_unready(driver):
            state = self._recover_post_login_loading(driver, job_id)
        state = self._recover_blank_public_account_page(driver, state)
        logger.info("Apple login state after landing recovery state=%s", state)
        # Apple can answer password submission with an idmsa.apple.com 409
        # authentication conflict and then build the security challenge through
        # a continuation that can outlast the render deadline.  The interim
        # document is neither blank nor marked with a loading spinner, so it
        # would otherwise be classified "unknown" and fail closed right as the
        # security-question page becomes visible.  When a 409 is pending but
        # the page has already settled (not unready/blank), keep waiting on the
        # same document for the concrete challenge.  This must run before the
        # state handlers below so a late security-question page is answered
        # instead of being reported as an unrendered challenge.
        if (
            state in {"unknown", "page_unready"}
            and self._authentication_conflict_seen(driver)
            and not self._is_page_unready(driver)
        ):
            state = self._recover_post_login_loading(driver, job_id)
            logger.info("Apple login state after 409 challenge wait state=%s", state)
        if state == "security_interstitial":
            state = self._continue_security_interstitial(driver)
        if state == "security_questions":
            state = self._answer_security_questions(driver, payload, job_id)
        # Apple may show the security explanation again after the security
        # questions are accepted. Apply the same transition handling there.
        if state == "security_interstitial":
            state = self._continue_security_interstitial(driver)
        if state == "security_upgrade_prompt":
            state = self._decline_security_upgrade(driver)
        if state == "trusted_phone_prompt":
            state = self._cancel_trusted_phone_prompt(driver)
        if state == "security_upgrade_prompt":
            state = self._decline_security_upgrade(driver)
        if state == "trusted_phone_prompt":
            state = self._cancel_trusted_phone_prompt(driver)
        if state in {"unknown", "page_unready"} and self._is_page_unready(driver):
            state = self._recover_post_login_loading(driver, job_id)
        if state in {"unknown", "page_unready"}:
            _, visible_text = self._login_snapshot(driver)
            public_landing = self._is_blank_public_account_page(
                visible_text, str(getattr(driver, "current_url", "") or ""),
            )
            if self._authentication_conflict_seen(driver):
                raise FlowError(
                    "apple_authentication_challenge_required",
                    "Apple 已要求安全验证，但浏览器未显示验证页面",
                    stage="login", retryable=False, password_submitted=True,
                )
            if public_landing:
                raise FlowError(
                    "authentication_challenge_unrendered",
                    "Apple 登录认证未建立，安全挑战页面未显示",
                    stage="login", retryable=False, password_submitted=True,
                )
        if state in self._ERROR_STATES:
            self._capture_error_page(driver, job_id, state)
        logger.info("Apple login final state=%s", state)
        return state

    def _recover_post_login_loading(self, driver, job_id: str = "") -> str:
        """Recover a hydrated-shell stall without submitting credentials again."""
        if job_id:
            self._screenshot(driver, f"{job_id}-post-login-loading")
        # Apple can finish establishing the authenticated session after the
        # visible page becomes blank. A 409 continuation may take noticeably
        # longer than the normal login redirect, especially immediately after
        # an account-security change. Do not navigate away before this bounded
        # post-submit wait has expired; navigation would discard the pending
        # challenge and turn a recoverable screen into the public landing page.
        deadline = time.monotonic() + settings.post_login_timeout
        while time.monotonic() < deadline:
            state, _ = self._login_snapshot(driver)
            if state:
                return state
            time.sleep(0.5)
        # A 409 includes an internal /auth continuation. It is not a standalone
        # browser URL: Apple binds it to OAuth parameters held by the embedded
        # page. Record only its safe host/path for diagnosis; navigating to it
        # directly produces Apple's "OAuth Token is empty" page and loses the
        # real security challenge.
        continuation_url = self._authentication_conflict_location(driver)
        if continuation_url:
            parsed_continuation = urlparse(continuation_url)
            try:
                setattr(driver, "_apple_flow_continuation_path", parsed_continuation.path[:240])
                setattr(driver, "_apple_flow_continuation_host", (parsed_continuation.hostname or "")[:120])
            except Exception:
                pass
            if job_id:
                self._screenshot(driver, f"{job_id}-authentication-challenge")
            # The 409 continuation and its challenge DOM are not committed at
            # exactly the same instant.  On a slow proxy the questions can
            # become visible while the diagnostic screenshot is being taken,
            # just after the main polling deadline.  Re-classify briefly here
            # before navigating away; reopening Account Home would destroy the
            # now-usable security-question challenge.
            late_deadline = time.monotonic() + max(min(settings.render_timeout, 15), 5)
            while time.monotonic() < late_deadline:
                late_state, _ = self._login_snapshot(driver)
                if late_state:
                    return late_state
                time.sleep(0.25)
        # Reopening Account Home is a GET navigation and never resubmits the
        # password form. If the session cookie was accepted, Apple will render
        # Account Home; otherwise the flow remains failed closed.
        self._get(driver, settings.account_url, password_submitted=True)
        return self._login_state(driver, password_submitted=True)

    @staticmethod
    def _is_blank_public_account_page(visible_text: str, current_url: str) -> bool:
        """Whether Apple has returned its public, empty account landing page.

        This page contains the Apple navigation and a Sign In link but no
        authenticated account controls.  It is a transient Apple SPA outcome,
        not proof that an account needs a verification code.
        """
        url = str(current_url or "").lower()
        text = str(visible_text or "").lower()
        compact = re.sub(r"\s+", "", text)
        if "account.apple.com" not in url:
            return False
        has_public_account_heading = "apple account" in text or "apple账户" in compact
        has_sign_in_link = "sign in" in text or "登录" in text
        has_authenticated_content = any(term in text for term in (
            "sign-in & security", "personal information", "devices",
            "登录与安全", "个人信息", "设备",
        ))
        return has_public_account_heading and has_sign_in_link and not has_authenticated_content

    def _recover_blank_public_account_page(self, driver, state: str) -> str:
        """Wait for Apple's post-password challenge without destroying its session."""
        current_state, current_text = self._login_snapshot(driver)
        is_blank_landing = self._is_blank_public_account_page(current_text, driver.current_url)
        if not is_blank_landing:
            state = current_state or state
            return state

        from selenium.webdriver.support.ui import WebDriverWait

        def wait_for_state_or_page_change(d):
            next_state, next_text = self._login_snapshot(d)
            is_blank = self._is_blank_public_account_page(next_text, d.current_url)
            # Ignore classifications from an old iframe while the visible root
            # document is still the public landing page.
            if next_state and not is_blank:
                return next_state
            return False

        # Apple can hold the public shell for close to a minute while the
        # authentication challenge is created. Refreshing or reopening the
        # account URL here discards that continuation and returns to Sign In.
        # Keep the original post-password document and wait for its iframe/DOM.
        try:
            return WebDriverWait(driver, max(settings.render_timeout * 3, 90)).until(wait_for_state_or_page_change)
        except Exception:
            return "page_unready"

    def _continue_security_interstitial(self, driver) -> str:
        """Advance one Apple security explanation page and classify its result."""
        from selenium.webdriver.support.ui import WebDriverWait

        self._click_button_anywhere(driver, ("continue", "继续", "下一步"))

        def after_interstitial(d):
            next_state, _ = self._login_snapshot(d)
            # The security-question copy can linger in a hidden iframe after the
            # answers were accepted.  Treat it as still transitioning here.
            if next_state in {"security_interstitial", "security_questions"}:
                return False
            return next_state or False

        try:
            return WebDriverWait(driver, settings.post_login_timeout).until(after_interstitial)
        except Exception as exc:
            raise FlowError("page_changed", "Apple 安全说明页点击后未继续") from exc

    def _decline_security_upgrade(self, driver) -> str:
        """Skip Apple's optional security upgrade and re-check the account.

        Apple presents this in two steps in both supported locales:
        ``Other Options`` / ``其他选项`` on the introductory screen, then
        ``Don't Upgrade`` / ``不升级`` on the confirmation screen.  This is
        deliberately separate from a real verification-code challenge.
        """
        from selenium.webdriver.support.ui import WebDriverWait

        decline_labels = (
            "don't upgrade", "do not upgrade", "not now",
            "暂不升级", "不升级", "暂不进行升级",
        )
        _, initial_text = self._login_snapshot(driver)
        # The confirmation page can also be reached directly.  Only click
        # Other Options when the visible page has not already offered Decline.
        if not any(label in initial_text.lower() for label in decline_labels):
            try:
                self._click_button_anywhere(
                    driver,
                    ("other options", "more options", "其他选项", "更多选项"),
                    include_links=True,
                )
            except Exception as exc:
                raise FlowError(
                    "page_changed", "未找到 Apple 安全升级页面的“其他选项”按钮",
                ) from exc

            def decline_is_ready(d):
                _, text = self._login_snapshot(d)
                lowered = text.lower()
                return any(label in lowered for label in decline_labels)

            try:
                WebDriverWait(driver, settings.post_login_timeout).until(decline_is_ready)
            except Exception as exc:
                raise FlowError(
                    "page_changed", "Apple 未显示“不升级”确认选项",
                ) from exc

        self._click_button_anywhere(driver, decline_labels, include_links=True)

        def after_decline(d):
            state, _ = self._login_snapshot(d)
            # A stale hidden iframe can keep the answered security-question
            # copy mounted while the page transitions to Account Home.  Only a
            # concrete next state ends this wait.
            if state in {"security_upgrade_prompt", "security_questions"}:
                return False
            return state or False

        try:
            return WebDriverWait(driver, settings.post_login_timeout).until(after_decline)
        except Exception as exc:
            raise FlowError("page_changed", "取消 Apple 安全升级后未进入账号页面") from exc

    def _cancel_trusted_phone_prompt(self, driver) -> str:
        """Skip Apple's optional trusted-phone enrollment and re-check state."""
        from selenium.webdriver.support.ui import WebDriverWait

        self._click_button_anywhere(driver, ("cancel", "取消"))

        def after_cancel(d):
            state, _ = self._login_snapshot(d)
            if state in {"trusted_phone_prompt", "security_questions"}:
                return False
            return state or False

        try:
            return WebDriverWait(driver, settings.post_login_timeout).until(after_cancel)
        except Exception as exc:
            raise FlowError("page_changed", "取消添加受信任电话号码后未进入账号页面") from exc

    def _answer_security_questions(self, driver, payload: dict, job_id: str = "") -> str:
        from selenium.webdriver.support.ui import WebDriverWait

        driver.switch_to.default_content()
        input_selector = (
            # Security answers are password-type inputs on some Apple pages;
            # exclude only account identifiers and submit controls below.
            "input:not([type='hidden']):not([type='email']):"
            "not([type='submit']):not([type='button']):"
            "not([type='checkbox']):not([type='radio']), textarea, [contenteditable='true']"
        )
        fields = []
        try:
            fields = WebDriverWait(driver, 20).until(
                lambda d: self._visible_fields_anywhere(d, input_selector, 2)
            )
        except Exception:
            pass
        # Keep backend-node handles as a fallback even when Selenium found
        # fields.  Apple replaces the iframe component immediately after it
        # becomes visible, which can stale Selenium WebElement references
        # between question lookup and filling.
        cdp_fields = self._cdp_security_answer_nodes(driver)
        if len(fields) < 2 and len(cdp_fields) < 2:
            self._save_security_question_probe(job_id, [], [], payload, driver)
            raise FlowError("security_question_unmatched", "未找到 Apple 密保答案输入框")
        fields = fields[:2] if len(fields) >= 2 else []
        questions = []
        for field in fields:
            try:
                questions.append(self._question_context(driver, field))
            except Exception:
                # The page may have replaced the challenge iframe.  Use the
                # visible question-line fallback and CDP node handles below.
                questions.append("")
        fallback_questions = []
        self._save_security_question_probe(job_id, questions, fallback_questions, payload, driver)
        try:
            if not questions:
                raise FlowError("security_question_unmatched", "需要使用页面问题文本匹配密保")
            answers = match_security_answers(questions, payload)
        except FlowError as primary:
            # The current Apple component may render the question text outside
            # the label/Shadow DOM root that owns the input. Use only visible
            # question lines as a conservative fallback; never infer answers
            # from field position alone.
            _, visible_text = self._login_snapshot(driver)
            fallback_questions = self._visible_question_lines(visible_text)
            self._save_security_question_probe(job_id, questions, fallback_questions, payload, driver)
            try:
                answers = match_security_answers(fallback_questions, payload)
            except FlowError:
                raise primary
        # Re-discover the controls after question matching.  Reading the
        # visible question text can switch out of the challenge iframe, and
        # Apple may replace the original WebElements during that transition.
        fresh_fields = self._visible_fields_anywhere(
            driver, input_selector, 2, require_enabled=False,
        )
        filled = False
        if len(fresh_fields) >= 2:
            try:
                for field, answer in zip(fresh_fields[:2], answers):
                    field.clear()
                    field.send_keys(answer)
                filled = all(
                    bool(field.get_attribute("value")) for field in fresh_fields[:2]
                )
            except Exception:
                filled = False
        if not filled and len(cdp_fields) >= 2:
            for node, answer in zip(cdp_fields[:2], answers):
                if not self._type_cdp_input(driver, node["backend_node_id"], answer):
                    raise FlowError("security_question_unmatched", "Apple 密保答案输入框无法填写")
            filled = True
        if not filled:
            raise FlowError("security_question_unmatched", "Apple 密保答案输入框无法填写")
        self._click_button_anywhere(driver, ("continue", "继续"))

        def completed(d):
            state, text = self._login_snapshot(d)
            # Apple renders transport failures directly below each security
            # answer field (for example "Failed to fetch") and disables the
            # Continue button.  This is not evidence that an answer is wrong:
            # classify it as a technical proxy/network failure so the caller
            # can rotate the dynamic proxy or retry later without disabling
            # the account as an unsafe password mutation.
            if self._security_answer_transport_failed(text):
                self._capture_error_page(d, job_id, "security-answer-network")
                raise FlowError(
                    "proxy_error", "密保问题验证请求失败（网络或代理异常）",
                    retryable=True, password_submitted=True,
                )
            if self._security_answer_rejected(text):
                self._capture_error_page(d, job_id, "security-answer-rejected")
                raise FlowError("security_answer_invalid", "Apple 拒绝了密保答案")
            if state == "security_questions":
                # A stale hidden iframe can keep the security-question copy
                # mounted after Apple has accepted the answers and advanced to
                # the two-factor-security upgrade page.  Treat that page as the
                # end of the question step so the sign-in flow can decline the
                # upgrade and reach Account Home.
                if self._is_security_upgrade_prompt_text(text):
                    return "security_upgrade_prompt"
                return False
            return state or False

        try:
            return WebDriverWait(driver, settings.post_login_timeout).until(completed)
        except FlowError:
            raise
        except Exception as exc:
            raise FlowError("security_answer_invalid", "密保问题提交后未通过验证") from exc

    @staticmethod
    def _question_context(driver, field) -> str:
        from selenium.webdriver.common.by import By

        field_id = field.get_attribute("id") or ""
        if field_id:
            labels = driver.find_elements(By.CSS_SELECTOR, f"label[for='{field_id}']")
            label_text = labels[0].text.strip() if labels else ""
            if label_text and not re.fullmatch(r"answer\s*\d*|答案\s*\d*", label_text, re.I):
                return label_text
        aria_label = field.get_attribute("aria-label") or ""
        if aria_label.strip() and not re.fullmatch(r"answer\s*\d*|答案\s*\d*", aria_label.strip(), re.I):
            return aria_label
        return driver.execute_script(
            "const e=arguments[0], target=e.getBoundingClientRect();"
            "let best='', distance=Number.POSITIVE_INFINITY;"
            "const roots=[], seen=new Set();"
            "const addRoot=root=>{if(!root||seen.has(root))return;seen.add(root);roots.push(root);"
            " for(const host of root.querySelectorAll('*')) if(host.shadowRoot) addRoot(host.shadowRoot);};"
            "addRoot(e.getRootNode()); addRoot(document);"
            "for(const root of roots){"
            " for(const node of root.querySelectorAll('label,h1,h2,h3,p,span,div,[role=heading]')){"
            "  const text=(node.innerText||node.textContent||'').trim();"
            "  if(!text || text.length>160 || !/[?？]$/.test(text)) continue;"
            "  const rect=node.getBoundingClientRect();"
            "  if(rect.width===0 || rect.height===0 || rect.bottom>target.top+3) continue;"
            "  const gap=target.top-rect.bottom; if(gap<distance){best=text;distance=gap;}"
            " }"
            "} return best;", field
        ) or ""

    @staticmethod
    def _visible_question_lines(visible_text: str) -> list[str]:
        excluded = ("forgot your answers", "reset security questions", "忘记答案", "重置安全问题")
        lines = []
        for line in str(visible_text or "").splitlines():
            candidate = line.strip()
            if not candidate or len(candidate) > 160 or not re.search(r"[?？]", candidate):
                continue
            if any(term in candidate.lower() for term in excluded):
                continue
            lines.append(candidate)
        return lines

    def _save_security_question_probe(
        self, job_id: str, questions: list[str], fallback_questions: list[str], payload: dict,
        driver=None,
    ):
        # This diagnostic intentionally records only lengths and similarity
        # scores. It must never persist question/answer text or credentials.
        if not job_id:
            return
        try:
            probe = {
                "question_lengths": [len(str(item or "")) for item in questions],
                "fallback_lengths": [len(str(item or "")) for item in fallback_questions],
                "context_scores": [
                    [round(_similarity(self._normalize(str(item or "")), self._normalize(str(payload.get(f"q{index}", "")))), 3)
                     for index in (1, 2, 3)]
                    for item in questions
                ],
                "fallback_scores": [
                    [round(_similarity(self._normalize(str(item or "")), self._normalize(str(payload.get(f"q{index}", "")))), 3)
                     for index in (1, 2, 3)]
                    for item in fallback_questions
                ],
            }
            if driver is not None:
                probe["dom_probe"] = self._security_dom_probe(driver)
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            path = self.artifact_dir / f"{job_id}-security-probe.json"
            path.write_text(json.dumps(probe, ensure_ascii=True), encoding="utf-8")
            path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _security_dom_probe(driver) -> dict:
        from selenium.webdriver.common.by import By

        frames = []

        def inspect(depth: int = 0):
            if depth > 3:
                return
            try:
                inputs = driver.find_elements(By.CSS_SELECTOR, "input, textarea, [contenteditable='true']")
                visible = [item for item in inputs if item.is_displayed()]
                shadow = AppleFlow._shadow_fields(
                    driver, "input, textarea, [contenteditable='true']", visible_only=False,
                )
                shadow_visible = [item for item in shadow if item.is_displayed()]
                child_frames = driver.find_elements(By.CSS_SELECTOR, "iframe")
                frames.append({
                    "depth": depth, "inputs": len(inputs), "visible_inputs": len(visible),
                    "shadow_inputs": len(shadow), "visible_shadow_inputs": len(shadow_visible),
                    "iframes": len(child_frames),
                })
            except Exception:
                return
            for frame in child_frames:
                switched = False
                try:
                    driver.switch_to.frame(frame)
                    switched = True
                    inspect(depth + 1)
                except Exception:
                    pass
                finally:
                    if switched:
                        try:
                            driver.switch_to.parent_frame()
                        except Exception:
                            driver.switch_to.default_content()

        try:
            driver.switch_to.default_content()
            inspect()
            driver.switch_to.default_content()
        except Exception:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
        return {"frames": frames}

    @staticmethod
    def _security_answer_rejected(visible_text: str) -> bool:
        text = visible_text.lower()
        return any(term in text for term in (
            "incorrect answer", "answers are incorrect", "answer is incorrect",
            "security answers do not match", "答案不正确", "回答错误", "密保答案错误",
        ))

    @staticmethod
    def _security_answer_transport_failed(visible_text: str) -> bool:
        """Recognise errors from submitting answers, not answer rejection."""
        text = visible_text.lower()
        return any(term in text for term in (
            "failed to fetch", "network request failed", "networkerror",
            "网络请求失败", "网络错误", "无法连接服务器",
        ))

    @staticmethod
    def _is_security_upgrade_prompt_text(visible_text: str) -> bool:
        """Whether the page offers the optional two-factor security upgrade.

        Apple advances from the security questions to "Apple Account Security"
        (其他选项) and then "Protect your account" (不升级).  A stale hidden
        iframe can still retain the answered security-question copy while the
        visible page has moved on, so this checks the actionable upgrade
        markers rather than the stale question heading.
        """
        text = visible_text.lower()
        compact = re.sub(r"\s+", "", text)
        heading = (
            "apple account security" in text or "apple账户安全" in compact
            or "protect your account" in text or "保护你的账户" in compact
        )
        action = any(term in text for term in (
            "other options", "更多选项", "其他选项",
            "don't upgrade", "do not upgrade", "不升级", "暂不升级",
        ))
        return heading and action

    def _confirm_password_change(
        self, driver, submitted_fields: list,
        unconfirmed_code: str = "password_update_unconfirmed",
        rejected_code: str = "password_update_rejected",
        allow_security_rollback_context: bool = False,
    ):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        failure_terms = (
            "password does not meet", "incorrect password", "unable to change", "could not change",
            "try again later", "密码不符合", "当前密码不正确", "无法更改密码", "请稍后再试",
        )
        reused_terms = (
            "haven't used in the past year", "haven’t used in the past year",
            "have not used in the past year", "过去一年未使用",
        )

        def confirmed(d):
            _, text = self._login_snapshot(d)
            title = ""
            try:
                title = str(d.title or "").lower()
            except Exception:
                pass
            current_url = str(getattr(d, "current_url", "") or "").lower()
            # Apple can retain the withdrawal modal text briefly after the
            # final Turn Off click. A localized “password changed” page is
            # authoritative and must win over stale explanatory text.
            if self._has_explicit_password_success(text, title, current_url):
                return True
            # Read the success modal before classifying any validation copy.
            # Closed Shadow DOM can hide it from the flattened snapshot while
            # the accessibility tree already exposes Apple's confirmation.
            ax_text = self._cdp_ax_text(d) or ""
            if self._has_explicit_password_success(ax_text):
                return True
            # The flattened CDP snapshot intentionally includes closed Shadow
            # DOM and iframe text, but it also contains hidden validation copy
            # from Apple's password component.  While the visible modal says
            # “Updating…” that hidden copy can include phrases such as
            # “unable to change” or “current password is incorrect”.  Do not
            # turn those dormant strings into a permanent rejection while the
            # submitted request is still visibly in flight.
            if self._has_visible_loading_indicator(d):
                return False
            recovery_state = classify_recovery_state(text)
            if recovery_state == "two_factor" and not self._is_two_factor_challenge(text):
                # The account settings page always mentions 双重认证 in its
                # security card even when two-factor is off.  Only a real
                # verification-code challenge blocks the change; otherwise the
                # password modal is still being processed, so keep polling.
                recovery_state = None
            if recovery_state:
                if recovery_state == "two_factor" and allow_security_rollback_context:
                    return False
                if recovery_state in {"locked", "two_factor", "recovery_wait_required"}:
                    self._raise_recovery_code(recovery_state)
                raise FlowError(rejected_code, "Apple 拒绝了密码重置")
            visible_lowered = self._visible_page_text(d).lower()
            if any(term in visible_lowered for term in reused_terms):
                raise FlowError("password_reused", "Apple 要求使用过去一年未使用过的新密码", stage="password_form")
            if any(term in visible_lowered for term in failure_terms):
                raise FlowError(rejected_code, "Apple 拒绝了密码更新")
            remaining = self._visible_fields_anywhere(d, "input[type='password']", 1)
            if not remaining:
                # The password fields disappearing is not proof of success, and
                # Apple often paints its "你的密码已更改" confirmation modal a
                # moment AFTER the fields close, inside a closed Shadow DOM root
                # that the regular text dump cannot pierce.  The accessibility
                # tree still exposes that text, so use it as a secondary marker.
                ax_text = self._cdp_ax_text(d) or ""
                if ax_text and self._has_explicit_password_success(ax_text):
                    return True
                # Keep polling until the explicit success marker appears; if it
                # never does, the outer wait times out and the change is reported
                # as unconfirmed (fail-closed, so the stored password is never
                # overwritten after a redirect, timeout, or silent rejection).
                return False
            return False

        try:
            WebDriverWait(driver, settings.post_login_timeout).until(confirmed)
        except FlowError:
            raise
        except Exception as exc:
            raise FlowError(unconfirmed_code, "Apple 未明确确认密码更新结果") from exc

    def _raise_password_policy_error(self, driver) -> None:
        # Apple's reset component keeps every validation message in hidden DOM
        # templates.  The flattened login snapshot therefore contains policy
        # errors even while the painted form shows all requirements as valid.
        # Only an error visible to the user is authoritative at this stage.
        lowered = self._visible_page_text(driver).lower()
        if any(term in lowered for term in (
            "haven't used in the past year", "haven’t used in the past year",
            "have not used in the past year", "过去一年未使用",
        )):
            raise FlowError("password_reused", "Apple 要求使用过去一年未使用过的新密码", stage="password_form")
        if any(term in lowered for term in (
            "too many consecutive identical characters",
            "too many consecutive repeated characters",
            "three consecutive identical characters",
            "连续相同的字符太多", "连续重复的字符太多", "不能包含三个连续相同字符",
        )):
            raise FlowError(
                "password_policy_rejected",
                "Apple 拒绝了新密码：不能包含三个连续相同字符",
                stage="password_form", password_submitted=False,
            )

    @staticmethod
    def _is_two_factor_challenge(visible_text: str) -> bool:
        """Whether the page asks for a verification code (a real 2FA step)."""
        text = visible_text.lower()
        return any(term in text for term in (
            "enter the verification code", "verification code sent", "enter code",
            "enter the code displayed", "enter the code from",
            "输入验证码", "验证码已发送", "输入代码", "输入显示的验证码", "输入验证码以继续",
        ))

    @staticmethod
    def _has_explicit_password_success(text: str, title: str = "", current_url: str = "") -> bool:
        combined = " ".join((str(text or ""), str(title or ""))).lower()
        success_terms = (
            "password changed", "password has been changed", "password was changed",
            "password updated", "password has been updated", "密码已更改", "密码修改成功", "密码已更新",
            "password reset successfully", "your password has been reset", "password reset complete",
            "密码重置成功", "密码重设成功", "密码已成功重置",
        )
        if any(term in combined for term in success_terms):
            return True
        return any(term in str(current_url or "").lower() for term in (
            "password-reset-success", "password-reset-complete", "password-changed",
        ))

    @staticmethod
    def _cdp_ax_text(driver) -> str:
        """Collect visible text from the accessibility tree.

        Apple renders its post-change confirmation inside closed Shadow DOM
        roots that ``DOM.getDocument`` cannot always pierce, but the browser's
        accessibility tree still exposes that text (it must, for screen readers).
        """
        try:
            payload = driver.execute_cdp_cmd("Accessibility.getFullAXTree", {})
            nodes = payload.get("nodes") or []
        except Exception:
            return ""
        parts: list[str] = []
        for node in nodes:
            name = (node.get("name") or {}).get("value")
            if name and str(name).strip():
                parts.append(str(name).strip())
            value = (node.get("value") or {}).get("value")
            if value and str(value).strip():
                parts.append(str(value).strip())
        return "\n".join(parts)[:100000]

    def _login_state(self, driver, password_submitted: bool = False) -> str:
        from selenium.webdriver.support.ui import WebDriverWait

        try:
            def settled(d):
                state, _ = self._login_snapshot(d)
                # During an Apple SPA transition an obsolete sign-in iframe can
                # still contain an error string.  Do not report that stale text
                # as a password failure while the visible page is loading.
                if state == "password_invalid" and self._has_visible_loading_indicator(d):
                    return False
                return state or False

            return WebDriverWait(driver, settings.render_timeout).until(settled)
        except Exception:
            if self._is_page_unready(driver):
                raise self._page_unready_error(driver, password_submitted=password_submitted)
            return "unknown"

    @staticmethod
    def _is_apple_host(current_url: str) -> bool:
        host = (urlparse(str(current_url or "")).hostname or "").lower()
        return host == "account.apple.com" or host.endswith(".apple.com")

    def _is_page_unready(self, driver) -> bool:
        try:
            # Empty Apple documents are the common proxy-failure shape. Detect
            # them with one browser command before running the much heavier
            # frame + Shadow DOM + CDP snapshot collector.
            document = self._document_readiness(driver)
            if document and self._is_apple_host(document.get("current_url", "")):
                if int(document.get("body_html_length", 0) or 0) == 0:
                    return True
            state, text = self._login_snapshot(driver)
            if state:
                return state == "password_invalid" and self._has_visible_loading_indicator(driver)
            visible_text = re.sub(r"\s+", "", text or "")
            return self._is_apple_host(driver.current_url) and (
                self._has_visible_loading_indicator(driver) or len(visible_text) < 20
            )
        except Exception:
            return False

    @staticmethod
    def _document_readiness(driver) -> dict:
        """Return cheap root-document readiness data without walking Apple DOM trees."""
        try:
            value = driver.execute_script(
                "const body=document.body; return {"
                "current_url:String(location.href||''),"
                "ready_state:String(document.readyState||''),"
                "body_html_length:body ? body.innerHTML.length : 0,"
                "body_text_length:body ? (body.innerText||'').length : 0,"
                "iframe_count:document.querySelectorAll('iframe').length"
                "};"
            )
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _page_unready_error(self, driver, *, password_submitted: bool = False) -> FlowError:
        """Distinguish a dead proxy transport from a merely slow Apple SPA."""
        if self._has_proxy_transport_failure(driver):
            return FlowError(
                "proxy_error", "代理连接失败（Apple 页面连接中断）", stage="login",
                retryable=not password_submitted, password_submitted=password_submitted,
            )
        return FlowError(
            "page_unready", "Apple 页面加载超时", stage="login",
            retryable=not password_submitted, password_submitted=password_submitted,
        )

    def _has_proxy_transport_failure(self, driver) -> bool:
        """Use Chromium network events to classify an otherwise blank page."""
        with self._temporary_command_timeout(driver, 5):
            entries = self._collect_performance_logs(driver)
        failures, _ = self._summarize_performance_logs(entries)
        return any(self._is_proxy_error(RuntimeError(error)) for error in failures)

    @staticmethod
    def _has_visible_loading_indicator(driver) -> bool:
        from selenium.webdriver.common.by import By

        selectors = (
            "[role='progressbar']", "[aria-busy='true']", "[data-testid*='loading']",
            "[class*='spinner']", "[class*='loading']",
        )
        def scan(depth: int = 0) -> bool:
            for selector in selectors:
                try:
                    if any(element.is_displayed() for element in driver.find_elements(By.CSS_SELECTOR, selector)):
                        return True
                except Exception:
                    continue
            if depth >= 3 or not hasattr(driver, "switch_to"):
                return False
            try:
                frames = driver.find_elements(By.CSS_SELECTOR, "iframe")
            except Exception:
                return False
            for frame in frames:
                switched = False
                try:
                    if not frame.is_displayed() or str(frame.get_attribute("aria-hidden") or "").lower() == "true":
                        continue
                    driver.switch_to.frame(frame)
                    switched = True
                    if scan(depth + 1):
                        return True
                except Exception:
                    pass
                finally:
                    if switched:
                        try:
                            driver.switch_to.parent_frame()
                        except Exception:
                            driver.switch_to.default_content()
            return False

        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        try:
            return scan()
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

    @staticmethod
    def _visible_page_text(driver) -> str:
        """Collect only painted page text from visible frame/shadow contexts.

        Unlike ``_cdp_document_text``, this deliberately excludes dormant DOM
        templates.  It is therefore suitable for deciding whether Apple is
        actually showing a validation error to the user.
        """
        from selenium.webdriver.common.by import By

        contexts: list[str] = []

        def collect(depth: int = 0):
            body_text = ""
            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text or ""
            except Exception:
                pass
            try:
                shadow_text = driver.execute_script(
                    "const out=[], seen=new Set();"
                    "const walk=root=>{if(!root||seen.has(root))return;seen.add(root);"
                    " for(const node of root.querySelectorAll('*')){"
                    "  if(node.shadowRoot){const t=(node.shadowRoot.innerText||'').trim();"
                    "   if(t)out.push(t);walk(node.shadowRoot);}}};"
                    "walk(document);return out.join('\\n');"
                ) or ""
                if shadow_text:
                    body_text = body_text + "\n" + str(shadow_text)
            except Exception:
                pass
            contexts.append(body_text)
            if depth >= 3:
                return
            try:
                frames = driver.find_elements(By.CSS_SELECTOR, "iframe")
            except Exception:
                return
            for frame in frames:
                switched = False
                try:
                    if not frame.is_displayed() or str(frame.get_attribute("aria-hidden") or "").lower() == "true":
                        continue
                    driver.switch_to.frame(frame)
                    switched = True
                    collect(depth + 1)
                except Exception:
                    pass
                finally:
                    if switched:
                        try:
                            driver.switch_to.parent_frame()
                        except Exception:
                            driver.switch_to.default_content()

        try:
            driver.switch_to.default_content()
            collect()
        except Exception:
            pass
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
        return "\n".join(contexts)

    @staticmethod
    def _login_snapshot(driver) -> tuple[str | None, str]:
        from selenium.webdriver.common.by import By

        contexts = []

        def collect(depth: int = 0):
            body_text = ""
            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text or ""
            except Exception:
                pass
            # Apple's current authentication challenge renders its heading and
            # questions inside nested open Shadow DOM roots. Selenium's body
            # text omits that content even though it is visibly painted.
            try:
                shadow_text = driver.execute_script(
                    "const out=[], seen=new Set();"
                    "const walk=root=>{if(!root||seen.has(root))return;seen.add(root);"
                    " for(const node of root.querySelectorAll('*')){"
                    "  if(node.shadowRoot){const t=(node.shadowRoot.innerText||'').trim();"
                    "   if(t)out.push(t);walk(node.shadowRoot);}}};"
                    "walk(document);return out.join('\\n');"
                ) or ""
                if shadow_text:
                    body_text = body_text + "\n" + str(shadow_text)
            except Exception:
                pass
            cdp_text = AppleFlow._cdp_document_text(driver)
            if cdp_text:
                body_text = body_text + "\n" + cdp_text
            contexts.append(body_text)
            if depth >= 3:
                return
            try:
                frames = driver.find_elements(By.CSS_SELECTOR, "iframe")
            except Exception:
                return
            for frame in frames:
                switched = False
                try:
                    if not frame.is_displayed() or str(frame.get_attribute("aria-hidden") or "").lower() == "true":
                        continue
                    driver.switch_to.frame(frame)
                    switched = True
                    collect(depth + 1)
                except Exception:
                    pass
                finally:
                    if switched:
                        try:
                            driver.switch_to.parent_frame()
                        except Exception:
                            driver.switch_to.default_content()

        try:
            driver.switch_to.default_content()
            collect()
            driver.switch_to.default_content()
        except Exception:
            pass
        text = "\n".join(contexts)
        try:
            root_has_nav = bool(driver.find_elements(
                By.CSS_SELECTOR, "[data-testid*='account'], [class*='account-menu'], a[href*='/account/manage']"
            ))
        except Exception:
            root_has_nav = False
        current_url = driver.current_url
        state = classify_login_state(contexts[0] if contexts else "", current_url, root_has_nav)
        if not state:
            for frame_text in contexts[1:]:
                state = classify_login_state(frame_text, current_url, False)
                if state:
                    break
        return state, text

    def _submit_recovery_identity(self, driver, job_id: str = ""):
        from ddddocr import DdddOcr
        from selenium.common.exceptions import StaleElementReferenceException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        image_selectors = [
            (By.CSS_SELECTOR, "img[src*='captcha' i]"), (By.CSS_SELECTOR, "[class*='captcha'] img"),
            (By.CSS_SELECTOR, "img[src^='data:image']"),
            (By.CSS_SELECTOR, "img[alt*='character' i], img[aria-label*='character' i]"),
            (By.CSS_SELECTOR, "[class*='captcha'] canvas, canvas[aria-label*='captcha' i]"),
        ]
        input_selectors = [
            (By.CSS_SELECTOR, "input[name*='captcha' i]"), (By.CSS_SELECTOR, "[class*='captcha'] input"),
            (By.CSS_SELECTOR, "input.captcha-input"),
        ]
        initial_image = self._first(driver, image_selectors, timeout=12)
        if not initial_image and self._first(driver, input_selectors, timeout=2):
            for _ in range(3):
                if not self._click_captcha_new_code(driver):
                    break
                initial_image = self._first(driver, image_selectors, timeout=8)
                if initial_image:
                    break
        if not initial_image:
            if self._first(driver, input_selectors, timeout=2):
                raise FlowError("captcha_image_unavailable", "Apple 验证码图片未加载")
            self._click_button(driver, ("continue", "继续", "下一步"))
            return
        def read_image():
            last_error = None
            for _ in range(3):
                image = self._first(driver, image_selectors, timeout=3)
                if not image:
                    break
                try:
                    WebDriverWait(driver, 3).until(lambda d: self._captcha_ready(d, image))
                    image_png = b""
                    detail_deadline = time.monotonic() + 5
                    while time.monotonic() < detail_deadline:
                        image_png = image.screenshot_as_png
                        if self._captcha_has_detail(image_png):
                            return image, image_png
                        time.sleep(0.25)
                except StaleElementReferenceException as exc:
                    last_error = exc
                    time.sleep(0.25)
                    continue
                except Exception as exc:
                    last_error = exc
                    break
            raise FlowError("captcha_image_unavailable", "Apple 验证码图片无法稳定读取") from last_error

        def submit(value: str) -> bool:
            field = self._first(driver, input_selectors)
            if not field or not 4 <= len(value) <= 8:
                return False
            try:
                field.clear()
                field.send_keys(value)
                if not re.fullmatch(r"[A-Za-z0-9]{4,8}", field.get_attribute("value") or ""):
                    return False
                self._click_button(driver, ("continue", "继续", "下一步"))
                WebDriverWait(driver, 8).until(
                    lambda d: not self._any_visible(d, image_selectors)
                    and not self._any_visible(d, input_selectors)
                )
                time.sleep(0.5)
                return not self._any_visible(driver, image_selectors)
            except StaleElementReferenceException:
                return not self._any_visible(driver, image_selectors)
            except Exception:
                return False

        image, image_png = read_image()
        self._save_captcha_artifact(image_png, job_id, 1)
        value = ""
        try:
            recognized = DdddOcr(show_ad=False).classification(self._prepare_captcha_for_ocr(image_png))
            value = self._normalize_captcha_text(recognized)
        except Exception:
            value = ""
        if 4 <= len(value) <= 8 and submit(value):
            return

        if not self.captcha_challenges or not job_id:
            raise FlowError("captcha_human_unavailable", "人工验证码服务不可用")

        previous_rejected = bool(value)
        previous_png = image_png
        for manual_attempt in range(1, settings.captcha_human_attempts + 1):
            current_image = self._first(driver, image_selectors, timeout=3)
            if not current_image:
                raise FlowError("captcha_image_unavailable", "Apple 验证码页面已失效")
            image, image_png = read_image()
            if previous_rejected and image_png == previous_png:
                self._refresh_captcha(driver, image)
                image, image_png = read_image()
            self._save_captcha_artifact(image_png, job_id, manual_attempt + 1)
            try:
                answer = self.captcha_challenges.wait_for_answer(
                    job_id, image_png, manual_attempt, previous_rejected,
                    settings.captcha_human_timeout,
                )
            except TimeoutError as exc:
                raise FlowError("captcha_human_timeout", "等待人工输入验证码超时") from exc
            except RuntimeError as exc:
                code = str(exc) or "captcha_human_unavailable"
                raise FlowError(code, "Telegram 人工验证码服务不可用") from exc
            previous_png = image_png
            if submit(answer):
                return
            previous_rejected = True
        raise FlowError("captcha_rejected", "人工验证码连续三次错误")

    def _refresh_captcha(self, driver, image):
        from selenium.common.exceptions import StaleElementReferenceException
        from selenium.webdriver.common.by import By

        refresh = self._first(driver, [(By.CSS_SELECTOR, ".captcha-new-code")], timeout=2)
        try:
            (refresh or image).click()
            # The old image can remain complete while Apple replaces its source.
            # Waiting briefly prevents OCR from immediately reading that stale code.
            time.sleep(1)
        except StaleElementReferenceException:
            pass
        except Exception:
            pass

    @staticmethod
    def _click_captcha_new_code(driver) -> bool:
        from selenium.webdriver.common.by import By

        try:
            elements = driver.find_elements(
                By.CSS_SELECTOR, ".captcha-new-code, button, a, [role='button']"
            )
            for element in elements:
                label = " ".join(filter(None, (
                    element.text, element.get_attribute("aria-label"), element.get_attribute("title"),
                ))).strip().lower()
                if element.is_displayed() and any(term in label for term in (
                    "new code", "refresh code", "换一张", "刷新验证码", "新验证码",
                )):
                    driver.execute_script("arguments[0].click();", element)
                    time.sleep(2)
                    return True
        except Exception:
            pass
        return False

    def _save_captcha_artifact(self, image_png: bytes, job_id: str, attempt: int):
        if not job_id:
            return
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            path = self.artifact_dir / f"{job_id}-captcha-{attempt}.png"
            path.write_bytes(image_png)
            path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _any_visible(driver, selectors) -> bool:
        try:
            return any(item.is_displayed() for by, selector in selectors
                       for item in driver.find_elements(by, selector))
        except Exception:
            return False

    @staticmethod
    def _captcha_ready(driver, image) -> bool:
        return bool(driver.execute_script(
            "const i=arguments[0], r=i.getBoundingClientRect();"
            "const loaded=i.tagName==='CANVAS' ? i.width>0&&i.height>0 : "
            "i.complete&&i.naturalWidth>0&&i.naturalHeight>0;"
            "return loaded && r.width>0 && r.height>0;",
            image,
        ))

    @staticmethod
    def _captcha_has_detail(image_png: bytes) -> bool:
        try:
            from io import BytesIO
            from PIL import Image, ImageStat

            image = Image.open(BytesIO(image_png)).convert("L")
            if image.width < 20 or image.height < 10:
                return False
            extrema = image.getextrema()
            return extrema[1] - extrema[0] >= 12 and ImageStat.Stat(image).stddev[0] >= 3
        except Exception:
            return False

    @staticmethod
    def _prepare_captcha_for_ocr(image_png: bytes) -> bytes:
        from io import BytesIO
        from PIL import Image, ImageEnhance, ImageOps

        image = Image.open(BytesIO(image_png)).convert("RGB")
        enhanced = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(2.5)
        output = BytesIO()
        enhanced.save(output, format="PNG")
        return output.getvalue()

    @staticmethod
    def _normalize_captcha_text(value: str) -> str:
        # Apple currently renders the challenge with uppercase ASCII letters and
        # digits. ddddocr often emits lowercase or a visually similar CJK glyph.
        return re.sub(r"[^A-Za-z0-9]", "", value).upper()

    @staticmethod
    def _account_selectors():
        from selenium.webdriver.common.by import By
        return [
            (By.CSS_SELECTOR, "input[type='email']"), (By.CSS_SELECTOR, "input[name='accountName']"),
            (By.CSS_SELECTOR, "input[id*='account' i]"), (By.CSS_SELECTOR, "input[name*='appleid' i]"),
            (By.CSS_SELECTOR, "input.iforgot-apple-id"),
        ]

    @staticmethod
    def _first(driver, selectors, timeout: int = 20):
        from selenium.webdriver.support.ui import WebDriverWait
        try:
            return WebDriverWait(driver, timeout).until(
                lambda d: next((item for by, value in selectors for item in d.find_elements(by, value) if item.is_displayed()), False)
            )
        except Exception:
            return None

    @staticmethod
    def _visible_fields_anywhere(
        driver, selector: str, minimum: int = 1, require_enabled: bool = True,
    ):
        from selenium.webdriver.common.by import By

        def search(depth: int = 0):
            fields = [field for field in driver.find_elements(By.CSS_SELECTOR, selector)
                      if field.is_displayed() and (not require_enabled or field.is_enabled())]
            if len(fields) < minimum:
                fields = AppleFlow._shadow_fields(driver, selector, visible_only=require_enabled)
                if not require_enabled:
                    fields = [field for field in fields if field.is_displayed()]
            if len(fields) >= minimum:
                return fields
            if depth >= 3:
                return []
            # Some Apple challenge iframes report not-displayed while their
            # embedded document is visible through a host component. Inspect
            # every frame; the field visibility check still filters hidden forms.
            frames = driver.find_elements(By.CSS_SELECTOR, "iframe")
            for frame in frames:
                switched = False
                try:
                    driver.switch_to.frame(frame)
                    switched = True
                    nested = search(depth + 1)
                    if nested:
                        return nested
                except Exception:
                    pass
                if switched:
                    try:
                        driver.switch_to.parent_frame()
                    except Exception:
                        driver.switch_to.default_content()
            return []

        try:
            driver.switch_to.default_content()
            fields = search()
            if fields:
                return fields
            driver.switch_to.default_content()
        except Exception:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
        return []

    @staticmethod
    def _all_fields_anywhere(driver, selector: str):
        from selenium.webdriver.common.by import By

        def search(depth: int = 0):
            fields = driver.find_elements(By.CSS_SELECTOR, selector)
            if not fields:
                fields = AppleFlow._shadow_fields(driver, selector, visible_only=False)
            if fields:
                return fields
            if depth >= 3:
                return []
            for frame in driver.find_elements(By.CSS_SELECTOR, "iframe"):
                switched = False
                try:
                    driver.switch_to.frame(frame)
                    switched = True
                    nested = search(depth + 1)
                    if nested:
                        return nested
                except Exception:
                    pass
                if switched:
                    try:
                        driver.switch_to.parent_frame()
                    except Exception:
                        driver.switch_to.default_content()
            return []

        try:
            driver.switch_to.default_content()
            fields = search()
            if fields:
                return fields
            driver.switch_to.default_content()
        except Exception:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
        return []

    @staticmethod
    def _shadow_fields(driver, selector: str, visible_only: bool):
        script = """
            const selector = arguments[0], visibleOnly = arguments[1], result = [];
            const visible = element => {
                const style = window.getComputedStyle(element), rect = element.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden'
                    && Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0
                    && !element.disabled;
            };
            const visit = root => {
                for (const element of root.querySelectorAll('*')) {
                    if (element.matches(selector) && (!visibleOnly || visible(element))) result.push(element);
                    if (element.shadowRoot) visit(element.shadowRoot);
                }
            };
            visit(document);
            return result;
        """
        try:
            return list(driver.execute_script(script, selector, visible_only) or [])
        except Exception:
            return []

    def _type_first_anywhere(self, driver, selectors, value: str):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        css = ", ".join(selector for by, selector in selectors if by == By.CSS_SELECTOR)
        # Apple's recovery shell can still be "interactive" when the identity
        # field is requested; wait for the input to materialise instead of
        # failing immediately on a slow hydration.
        try:
            fields = WebDriverWait(driver, settings.render_timeout).until(
                lambda current: self._visible_fields_anywhere(current, css, 1)
            )
        except Exception as exc:
            raise FlowError("recovery_identity_failed", "未找到 Apple 账号恢复输入框") from exc
        fields[0].clear()
        fields[0].send_keys(value)

    def _type_first(self, driver, selectors, value: str):
        element = self._first(driver, selectors)
        if not element:
            raise FlowError("page_changed", "未找到账号页面输入框")
        element.clear(); element.send_keys(value)

    @staticmethod
    def _click_button(driver, labels: tuple[str, ...], include_links: bool = False):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        selector = "button, input[type='submit']" + (", a" if include_links else "")
        lowered = tuple(label.lower() for label in labels)
        def find(d):
            for element in d.find_elements(By.CSS_SELECTOR, selector):
                value = (element.text or element.get_attribute("value") or element.get_attribute("aria-label") or "").strip().lower()
                if element.is_displayed() and any(label in value for label in lowered):
                    return element
            return False
        WebDriverWait(driver, 20).until(find).click()

    @staticmethod
    def _click_button_anywhere(driver, labels: tuple[str, ...], include_links: bool = False):
        """Click a labeled control in the current document, shadow roots, or iframe.

        The Apple security-question component can put its fields in one iframe
        and its Continue control in a sibling iframe or shadow root.  Searching
        only the frame left active by field discovery therefore produces a
        misleading page_changed timeout.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        selector = "button, input[type='submit'], input[type='button'], [role='button'], [role='option']" + (", a" if include_links else "")
        lowered = tuple(label.lower() for label in labels)

        def find_in_document(d):
            try:
                elements = list(d.find_elements(By.CSS_SELECTOR, selector))
                elements.extend(d.execute_script(
                    "const selector=arguments[0], result=[];"
                    "const visit=root=>{for(const e of root.querySelectorAll('*')){"
                    "if(e.matches(selector))result.push(e);"
                    "if(e.shadowRoot)visit(e.shadowRoot);}};"
                    "visit(document);return result;",
                    selector,
                ) or [])
                for element in elements:
                    try:
                        value = " ".join(filter(None, (
                            element.text, element.get_attribute("value"),
                            element.get_attribute("aria-label"), element.get_attribute("title"),
                        ))).strip().lower()
                        if element.is_displayed() and element.is_enabled() \
                                and any(label in value for label in lowered):
                            return element
                    except Exception:
                        continue
            except Exception:
                pass
            return None

        def search(d, depth=0):
            if depth >= 3:
                return find_in_document(d)
            try:
                frames = d.find_elements(By.CSS_SELECTOR, "iframe")
                # Apple keeps an old hidden auth iframe mounted during the
                # transition. Search the visible frame first so Continue or
                # Cancel cannot be sent to stale hidden markup.
                frames = sorted(frames, key=lambda frame: not frame.is_displayed())
            except Exception:
                frames = []
            for frame in frames:
                switched = False
                try:
                    if not frame.is_displayed():
                        continue
                    d.switch_to.frame(frame)
                    switched = True
                    if search(d, depth + 1):
                        return True
                except Exception:
                    pass
                finally:
                    if switched:
                        try:
                            d.switch_to.parent_frame()
                        except Exception:
                            d.switch_to.default_content()
            element = find_in_document(d)
            if element:
                try:
                    element.click()
                    return True
                except Exception:
                    return False
            for frame in frames:
                switched = False
                try:
                    if frame.is_displayed():
                        continue
                    d.switch_to.frame(frame)
                    switched = True
                    if search(d, depth + 1):
                        return True
                except Exception:
                    pass
                finally:
                    if switched:
                        try:
                            d.switch_to.parent_frame()
                        except Exception:
                            d.switch_to.default_content()
            return False

        def locate_and_click(d):
            try:
                d.switch_to.default_content()
            except Exception:
                pass
            # search() performs the click inside the same document via JS and
            # returns a boolean. Do not fetch/click a WebElement afterwards;
            # navigation can invalidate that handle immediately.
            return search(d)

        WebDriverWait(driver, 20).until(locate_and_click)

    @staticmethod
    def _exact_control_anywhere(driver, labels: tuple[str, ...], include_links: bool, click: bool) -> bool:
        """Find an exact-label control across shadow roots and visible frames."""
        from selenium.webdriver.common.by import By

        selector = "button, input[type='submit'], input[type='button'], [role='button']" + (", a" if include_links else "")
        expected = {" ".join(str(label).lower().split()) for label in labels}

        def in_document(current_driver):
            try:
                elements = list(current_driver.find_elements(By.CSS_SELECTOR, selector))
                elements.extend(current_driver.execute_script(
                    "const selector=arguments[0], result=[];"
                    "const visit=root=>{for(const e of root.querySelectorAll('*')){"
                    "if(e.matches(selector))result.push(e);if(e.shadowRoot)visit(e.shadowRoot);}};"
                    "visit(document);return result;", selector,
                ) or [])
            except Exception:
                return False
            for element in elements:
                try:
                    values = {
                        " ".join(str(raw).lower().split())
                        for raw in (
                            element.text, element.get_attribute("value"),
                            element.get_attribute("aria-label"), element.get_attribute("title"),
                        ) if raw
                    }
                    if not element.is_displayed() or not element.is_enabled() or not values.intersection(expected):
                        continue
                    if not click:
                        return True
                    return AppleFlow._click_visible(element, current_driver)
                except Exception:
                    continue
            return False

        def search(current_driver, depth=0):
            if in_document(current_driver):
                return True
            if depth >= 3:
                return False
            try:
                frames = current_driver.find_elements(By.CSS_SELECTOR, "iframe")
                frames = sorted(frames, key=lambda frame: not frame.is_displayed())
            except Exception:
                frames = []
            for frame in frames:
                switched = False
                try:
                    if not frame.is_displayed():
                        continue
                    current_driver.switch_to.frame(frame)
                    switched = True
                    if search(current_driver, depth + 1):
                        return True
                except Exception:
                    pass
                finally:
                    if switched:
                        try:
                            current_driver.switch_to.parent_frame()
                        except Exception:
                            current_driver.switch_to.default_content()
            return False

        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        return search(driver)

    @staticmethod
    def _has_exact_control_anywhere(driver, labels: tuple[str, ...], include_links: bool = False) -> bool:
        return AppleFlow._exact_control_anywhere(driver, labels, include_links, click=False)

    @staticmethod
    def _click_exact_control_anywhere(driver, labels: tuple[str, ...], include_links: bool = False):
        from selenium.webdriver.support.ui import WebDriverWait

        WebDriverWait(driver, 20).until(
            lambda current: AppleFlow._exact_control_anywhere(
                current, labels, include_links, click=True,
            )
        )

    def _switch_auth_frame(self, driver):
        from selenium.webdriver.common.by import By
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.support.ui import WebDriverWait

        def locate(d):
            try:
                d.switch_to.default_content()
            except Exception:
                return False
            try:
                if any(element.is_displayed() for by, selector in self._account_selectors()
                       for element in d.find_elements(by, selector)):
                    return True
            except Exception:
                pass
            try:
                frames = d.find_elements(By.CSS_SELECTOR, "iframe")
            except Exception:
                return False
            for frame in frames:
                try:
                    if not self._auth_frame_candidate(frame):
                        continue
                    d.switch_to.frame(frame)
                    if any(element.is_displayed() for by, selector in self._account_selectors()
                           for element in d.find_elements(by, selector)):
                        return True
                except Exception:
                    pass
                try:
                    d.switch_to.default_content()
                except Exception:
                    return False
            return False

        try:
            WebDriverWait(driver, settings.render_timeout, poll_frequency=0.25).until(locate)
        except TimeoutException as exc:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            if self._is_page_unready(driver):
                raise self._page_unready_error(driver) from exc
            raise FlowError("page_changed", "未找到 Apple 账号登录组件", retryable=True) from exc

    @staticmethod
    def _auth_frame_candidate(frame) -> bool:
        try:
            size = frame.size or {}
            return (
                frame.is_displayed()
                and str(frame.get_attribute("aria-hidden") or "").lower() != "true"
                and int(size.get("width", 0)) >= 120
                and int(size.get("height", 0)) >= 80
            )
        except Exception:
            return False

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[\s?？:：'\"“”]", "", value).lower()

    def _screenshot(self, driver, job_id: str):
        if not driver:
            return
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(self.artifact_dir / f"{job_id}.png"))
        except Exception:
            pass

    def _capture_error_page(self, driver, job_id: str, name: str):
        """Persist a clearly-named screenshot the moment an error page is seen."""
        if not driver or not job_id:
            return
        self._screenshot(driver, f"{job_id}-error-{name}")

    def _write_profile_failure(self, driver, job_id: str, key: str, expected: str, observed: str, stage: str):
        """资料还原失败时落盘关键信息，便于在截图被保留期清理后定位原因。"""
        if not job_id:
            return
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "field": key, "stage": stage, "expected": expected, "observed": observed,
                "url_path": urlparse(str(getattr(driver, "current_url", "") or "")).path[:240],
                "candidates": self._profile_candidates(driver),
            }
            (self.artifact_dir / f"{job_id}-profile-{key}-diagnostics.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    @staticmethod
    def _profile_candidates(driver) -> list[str]:
        """收集当前可见的候选文案，供人工核对 Apple 实际渲染的选项。"""
        try:
            elements = driver.find_elements("css selector", "button, [role='option'], [role='radio'], option, li")
        except Exception:
            return []
        texts: list[str] = []
        for element in elements:
            try:
                if not element.is_displayed():
                    continue
                text = " ".join(filter(None, (
                    element.text, element.get_attribute("aria-label"), element.get_attribute("value"),
                ))).strip()
            except Exception:
                continue
            if text and text not in texts:
                texts.append(text)
            if len(texts) >= 40:
                break
        return texts

    # ------------------------------------------------------------------
    # 账号资料（姓名 · 国家/地区 · 语言）检测与恢复。
    # 仅在账号开启“自动恢复账号资料”且本次登录为 normal 状态时执行；
    # 全程 fail-open：读不到/改不回都只记录，绝不使 check 失败。
    # ------------------------------------------------------------------

    def _open_profile_page(self, driver) -> bool:
        """尝试从已登录账号页点击进入“个人信息”页；已在其中则返回 True。"""
        current = str(getattr(driver, "current_url", "") or "")
        if "/sign-in" in current or "idmsa.apple.com" in current:
            return False
        if re.search(r"(personal|profile|account/info|account-info)", current, re.I):
            return True
        try:
            self._click_button_anywhere(driver, _PROFILE_PAGE_LABELS, include_links=True)
        except Exception:
            return False
        return True

    def _read_account_profile(self, driver, previous: dict | None = None) -> dict:
        """从当前文档读取可见的四项账号资料。"""
        fields = {"name": "", "dob": "", "country": "", "language": ""}
        text = ""
        try:
            text = self._cdp_document_text(driver) or ""
        except Exception:
            text = ""
        parsed = extract_profile_fields(text)
        for key in fields:
            fields[key] = parsed.get(key, "") or fields[key]
        if not fields["name"]:
            greeting = extract_greeting_name(text)
            if greeting:
                fields["name"] = greeting
        if fields["dob"]:
            fields["dob"] = normalize_profile_date(fields["dob"]) or fields["dob"]
        if previous:
            for key in fields:
                if not fields[key] and previous.get(key):
                    fields[key] = previous[key]
        return fields

    def _open_profile_card(self, driver, key: str) -> bool:
        """打开资料卡片。"""
        # 复用解析用的完整同义词表（含「首选语言」等），避免只识别「语言」而找不到入口。
        labels = tuple(label.lower() for label in _PROFILE_LABELS.get(key, (key,)))
        for attempt in (1, 2):
            try:
                self._click_button_anywhere(driver, labels, include_links=True)
                return True
            except Exception:
                if attempt == 2:
                    return False
            # Apple 可能把入口放在“个人信息”页，先进页再试一次。
            try:
                if not self._open_profile_page(driver):
                    return False
            except Exception:
                return False
        return False

    def _choose_profile_values(self, driver, values: list[str]) -> bool:
        """Choose native selects or Apple comboboxes without coordinate clicks."""
        try:
            controls = self._visible_fields_anywhere(
                driver, "select, [role='combobox']", len(values), require_enabled=False,
            )
        except Exception:
            controls = []
        if len(controls) < len(values):
            # Newer settings pages use selectable cards with no combobox.
            # Search the rendered/shadow DOM for the requested visible label.
            options = self._visible_fields_anywhere(
                driver, "button, [role='option'], [role='radio'], li", 1,
                require_enabled=False,
            )
            for value in values:
                target = next((item for item in options if profile_option_matches(
                    (item.text or item.get_attribute("aria-label") or item.get_attribute("value") or ""), value,
                )), None)
                if target is None:
                    return False
                if not self._click_visible(target, driver):
                    return False
            return True
        for control, value in zip(controls[:len(values)], values):
            try:
                if (control.tag_name or "").lower() == "select":
                    if not self._select_native_option(control, value):
                        return False
                else:
                    control.click()
                    options = self._visible_fields_anywhere(
                        driver, "[role='option'], option", 1, require_enabled=False,
                    )
                    target = next((item for item in options if profile_matches(
                        (item.text or item.get_attribute("label") or item.get_attribute("value") or ""), value,
                    ) or profile_option_matches(
                        (item.text or item.get_attribute("label") or item.get_attribute("value") or ""), value,
                    )), None)
                    if target is None:
                        # Some versions render the language picker as a list
                        # of buttons/radio cards instead of a native select.
                        options = self._visible_fields_anywhere(
                            driver, "button, [role='option'], [role='radio'], li", 1,
                            require_enabled=False,
                        )
                        target = next((item for item in options if profile_option_matches(
                            (item.text or item.get_attribute("aria-label") or item.get_attribute("value") or ""), value,
                        )), None)
                    if target is None:
                        return False
                    if not self._click_visible(target, driver):
                        return False
            except Exception:
                return False
        return True

    @staticmethod
    def _click_visible(element, driver) -> bool:
        """点击可见控件；失败时滚动到视口并退回 JS click。"""
        try:
            element.click()
            return True
        except Exception:
            pass
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        except Exception:
            pass
        try:
            element.click()
            return True
        except Exception:
            pass
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            return False

    @staticmethod
    def _select_native_option(control, value: str) -> bool:
        """按可见文本 / 取值 / 近似文本选择原生 select。

        直接点击 <option> 在 Chrome 下常被判为不可交互，因此近似匹配时改用
        select_by_index；语言/地区选项可能只显示「简体中文」或「中文（简体）」。
        """
        from selenium.webdriver.support.ui import Select

        select = Select(control)
        try:
            select.select_by_visible_text(value)
            return True
        except Exception:
            pass
        try:
            select.select_by_value(value)
            return True
        except Exception:
            pass
        for index, option in enumerate(select.options):
            label = " ".join(filter(None, (
                option.text, option.get_attribute("label"), option.get_attribute("value"),
            )))
            if profile_option_matches(label, value):
                try:
                    select.select_by_index(index)
                    return True
                except Exception:
                    return False
        return False

    def _wait_profile_value(self, driver, key: str, expected: str, previous: dict) -> bool:
        from selenium.webdriver.support.ui import WebDriverWait

        def matches(d):
            current = self._read_account_profile(d, previous=previous).get(key, "")
            if key == "dob":
                return normalize_profile_date(current) == normalize_profile_date(expected)
            matcher = profile_option_matches if key in ("language", "country") else profile_matches
            return bool(current and matcher(current, expected))
        try:
            return bool(WebDriverWait(driver, settings.post_login_timeout).until(matches))
        except Exception:
            return False

    def _restore_profile_name(self, driver, baseline: dict, job_id: str, previous: dict) -> str:
        if not self._open_profile_card(driver, "name"):
            self._profile_failure_notes["name"] = "未找到姓名设置入口"
            self._capture_error_page(driver, job_id, "profile-name-card-missing")
            self._write_profile_failure(driver, job_id, "name", baseline.get("name", ""), previous.get("name", ""), "card_missing")
            return "restore_failed"
        try:
            fields = self._visible_fields_anywhere(driver, "input[type='text']", 2)
        except Exception:
            fields = []
        if len(fields) < 2:
            self._profile_failure_notes["name"] = "未找到姓名输入框"
            self._capture_error_page(driver, job_id, "profile-name-fields-missing")
            self._write_profile_failure(driver, job_id, "name", baseline.get("name", ""), previous.get("name", ""), "fields_missing")
            return "restore_failed"
        family = baseline.get("family_name", "")
        middle = baseline.get("middle_name", "")
        given = baseline.get("given_name", "")
        try:
            values = (family, middle, given) if len(fields) >= 3 else (family, given)
            for field, value in zip(fields[:len(values)], values):
                field.clear(); field.send_keys(value)
            self._click_button_anywhere(driver, ("存储", "保存", "save", "done"))
        except Exception:
            self._capture_error_page(driver, job_id, "profile-name-submit-failed")
            return "uncertain"
        expected = baseline.get("name", "") or f"{family}{middle}{given}"
        return "restored" if self._wait_profile_value(driver, "name", expected, previous) else "uncertain"

    def _restore_profile_dob(self, driver, expected: str, job_id: str, previous: dict) -> str:
        normalized = normalize_profile_date(expected)
        if not normalized or not self._open_profile_card(driver, "dob"):
            self._profile_failure_notes["dob"] = "未找到出生日期设置入口"
            self._capture_error_page(driver, job_id, "profile-dob-card-missing")
            return "restore_failed"
        year, month, day = normalized.split("-")
        if not self._choose_profile_values(driver, [str(int(year)), str(int(month)), str(int(day))]):
            self._profile_failure_notes["dob"] = "未找到出生日期可选值"
            self._capture_error_page(driver, job_id, "profile-dob-fields-missing")
            return "restore_failed"
        try:
            self._click_button_anywhere(driver, ("存储", "保存", "save", "done"))
        except Exception:
            return "uncertain"
        return "restored" if self._wait_profile_value(driver, "dob", normalized, previous) else "uncertain"

    def _restore_profile_choice(self, driver, key: str, expected: str, job_id: str, previous: dict) -> str:
        if not expected or not self._open_profile_card(driver, key):
            self._profile_failure_notes[key] = "未找到设置入口"
            self._capture_error_page(driver, job_id, f"profile-{key}-card-missing")
            self._write_profile_failure(driver, job_id, key, expected, previous.get(key, ""), "card_missing")
            return "restore_failed"
        if not self._choose_profile_values(driver, [expected]):
            self._profile_failure_notes[key] = "未找到目标选项"
            self._capture_error_page(driver, job_id, f"profile-{key}-option-missing")
            self._write_profile_failure(driver, job_id, key, expected, previous.get(key, ""), "option_missing")
            return "restore_failed"
        try:
            labels = ("继续", "continue") if key == "country" else ("存储", "保存", "save", "done")
            self._click_button_anywhere(driver, labels)
        except Exception:
            return "uncertain"
        if key == "country":
            outcome = self._wait_country_outcome(driver, expected, previous)
            if outcome == "manual_required":
                self._capture_error_page(driver, job_id, "profile-country-manual-required")
            return outcome
        return "restored" if self._wait_profile_value(driver, key, expected, previous) else "uncertain"

    def _wait_country_outcome(self, driver, expected: str, previous: dict) -> str:
        """Wait for either a verified region change or Apple's payment/manual gate."""
        from selenium.webdriver.support.ui import WebDriverWait

        outcome = {"value": "uncertain"}

        def resolved(d):
            _, text = self._login_snapshot(d)
            if self._profile_country_requires_manual(text):
                outcome["value"] = "manual_required"
                return True
            current = self._read_account_profile(d, previous=previous).get("country", "")
            if current and profile_matches(current, expected):
                outcome["value"] = "restored"
                return True
            return False

        try:
            WebDriverWait(driver, settings.post_login_timeout).until(resolved)
        except Exception:
            pass
        return outcome["value"]

    @staticmethod
    def _profile_country_requires_manual(visible_text: str) -> bool:
        lowered = (visible_text or "").lower()
        return any(term in lowered for term in (
            "payment method", "billing address", "store credit", "account balance",
            "付款方式", "账单地址", "账户余额", "账号余额", "商店余额",
        ))

    def _manage_account_profile(self, driver, config: dict, job_id: str, mutation_callback=None) -> dict:
        """Detect all four fields first, then restore every changed configured field."""
        baseline = {
            "name": str(config.get("name") or "").strip(),
            "family_name": str(config.get("family_name") or "").strip(),
            "middle_name": str(config.get("middle_name") or "").strip(),
            "given_name": str(config.get("given_name") or "").strip(),
            "dob": normalize_profile_date(str(config.get("dob") or "")),
            "country": str(config.get("country") or "").strip(),
            "language": str(config.get("language") or "").strip(),
        }
        if not baseline["name"]:
            baseline["name"] = f'{baseline["family_name"]}{baseline["middle_name"]}{baseline["given_name"]}'
        observed = self._read_account_profile(driver)
        if not all(observed.get(key) for key in ("name", "dob", "country", "language")):
            if self._open_profile_page(driver):
                page_fields = self._read_account_profile(driver, previous=observed)
                for key in observed:
                    if page_fields.get(key):
                        observed[key] = page_fields[key]
        labels = {"name": "姓名", "dob": "出生日期", "country": "国家/地区", "language": "语言"}
        results = {}
        mismatches = []
        self._profile_failure_notes = {}
        for key in ("name", "dob", "country", "language"):
            expected, current = baseline[key], observed.get(key, "")
            same = (normalize_profile_date(expected) == normalize_profile_date(current)) if key == "dob" else profile_matches(expected, current)
            if not current:
                results[key] = {"status": "detect_failed", "expected": expected, "observed": "", "message": f"{labels[key]}读取失败"}
            elif not expected:
                results[key] = {"status": "detect_failed", "expected": "", "observed": current, "message": f"{labels[key]}保护基线未填写"}
            elif same:
                results[key] = {"status": "unchanged", "expected": expected, "observed": current, "message": f"{labels[key]}未发生变化"}
            else:
                mismatches.append(key)
                results[key] = {"status": "changed", "expected": expected, "observed": current,
                                "message": f"{labels[key]}检测到变化，准备自动恢复"}
        if mismatches and mutation_callback:
            mutation_callback()
        for key in ("name", "dob", "language", "country"):
            if key not in mismatches:
                continue
            before = observed.get(key, "")
            self._screenshot(driver, f"{job_id}-profile-{key}-before")
            if key == "name":
                status = self._restore_profile_name(driver, baseline, job_id, observed)
            elif key == "dob":
                status = self._restore_profile_dob(driver, baseline[key], job_id, observed)
            else:
                status = self._restore_profile_choice(driver, key, baseline[key], job_id, observed)
            self._screenshot(driver, f"{job_id}-profile-{key}-after")
            suffix = {
                "restored": "已还原并验证", "restore_failed": "还原失败",
                "uncertain": "保存结果待确认", "manual_required": "需要人工处理",
            }[status]
            note = self._profile_failure_notes.get(key, "")
            if status == "restore_failed" and note:
                suffix = f"还原失败（{note}）"
            results[key] = {"status": status, "expected": baseline[key], "observed": before,
                            "message": f"{labels[key]}检测到变化：{before} → {baseline[key]}，{suffix}"}
        notes = [results[key]["message"] for key in ("name", "dob", "language", "country")]
        restored = any(item["status"] == "restored" for item in results.values())
        observed["results"] = results
        observed["summary"] = "；".join(notes)
        return {"profile": observed, "profile_results": results, "profile_restored": restored,
                "note": observed["summary"]}

    @staticmethod
    @contextmanager
    def _temporary_command_timeout(driver, seconds: int):
        """Bound best-effort diagnostics when Chrome or the Grid node is wedged."""
        config = getattr(getattr(driver, "command_executor", None), "_client_config", None)
        previous = getattr(config, "timeout", None) if config is not None else None
        try:
            if config is not None:
                config.timeout = max(int(seconds), 1)
            yield
        finally:
            if config is not None and previous is not None:
                config.timeout = previous

    def _capture_failure(self, driver, job_id: str, stage: str, error_code: str, proxy: str | None):
        lightweight = error_code in {"page_unready", "proxy_error", "driver_timeout"}
        if lightweight:
            # Blank/unresponsive pages produced the 8893 incident. A full-page
            # screenshot plus browser/performance/CDP reads could each consume
            # the 35-second Grid timeout, stretching one failure by minutes.
            # Keep the useful bounded network/DOM summary and omit blank images.
            self._write_diagnostics(driver, job_id, stage, error_code, proxy, lightweight=True)
            return
        self._screenshot(driver, job_id)
        self._write_diagnostics(driver, job_id, stage, error_code, proxy)

    def _write_diagnostics(
        self, driver, job_id: str, stage: str, error_code: str, proxy: str | None,
        lightweight: bool = False,
    ):
        if not driver or not job_id:
            return
        try:
            data = {
                "stage": stage,
                "error_code": error_code,
                "url_path": "",
                "url_host": "",
                "proxy_configured": bool(proxy),
                "diagnostics_mode": "lightweight" if lightweight else "full",
            }
            click_diagnostics = getattr(driver, "_device_click_diagnostics", None)
            if isinstance(click_diagnostics, list):
                data["device_click_targets"] = click_diagnostics
            continuation_path = str(getattr(driver, "_apple_flow_continuation_path", "") or "")
            if continuation_path:
                data["authentication_continuation"] = {
                    "followed": True,
                    "url_host": str(getattr(driver, "_apple_flow_continuation_host", "") or ""),
                    "url_path": continuation_path,
                }
            try:
                with self._temporary_command_timeout(driver, 5 if lightweight else settings.command_timeout):
                    metrics = driver.execute_script(
                    """
                    const body = document.body;
                    const resources = performance.getEntriesByType('resource');
                    const pending = resources.filter(item => item.duration === 0).length;
                    const empty = resources.filter(item => item.transferSize === 0).length;
                    const selectors = '[role=' + JSON.stringify('progressbar') + '],[aria-busy=' + JSON.stringify('true') + '],[class*=' + JSON.stringify('spinner') + '],[class*=' + JSON.stringify('loading') + ']';
                    return {
                      current_url: String(location.href || ''),
                      ready_state: document.readyState || '',
                      body_text_length: body ? (body.innerText || '').length : 0,
                      body_html_length: body ? body.innerHTML.length : 0,
                      iframe_count: document.querySelectorAll('iframe').length,
                      visible_iframe_count: Array.from(document.querySelectorAll('iframe')).filter(item => {
                        const rect = item.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                      }).length,
                      loading_indicator_count: document.querySelectorAll(selectors).length,
                      resource_count: resources.length,
                      resource_pending_count: pending,
                      resource_empty_count: empty,
                      viewport_width: window.innerWidth,
                      viewport_height: window.innerHeight
                    };
                    """
                    )
                if isinstance(metrics, dict):
                    current_url = str(metrics.pop("current_url", "") or "")
                    parsed = urlparse(current_url)
                    data["url_path"] = parsed.path[:240]
                    data["url_host"] = (parsed.hostname or "")[:120]
                    data.update(metrics)
            except Exception:
                data["metrics_unavailable"] = True
            if lightweight:
                data["browser_log_counts"] = {}
                data["browser_error_categories"] = {}
            else:
                try:
                    browser_logs = self._driver_logs(driver, "browser")
                    severities = {}
                    for item in browser_logs:
                        level = str(item.get("level", "UNKNOWN"))[:20]
                        severities[level] = severities.get(level, 0) + 1
                    data["browser_log_counts"] = severities
                    data["browser_error_categories"] = self._browser_error_categories(browser_logs)
                except Exception:
                    data["browser_log_counts"] = {}
                    data["browser_error_categories"] = {}
            try:
                # The proxy classifier normally collected this once already.
                # Do not issue another potentially wedged Grid command.
                needs_fresh_logs = not lightweight or not bool(
                    getattr(driver, "_apple_flow_performance_logs_collected", False)
                )
                with self._temporary_command_timeout(driver, 5 if lightweight else settings.command_timeout):
                    performance_logs = self._collect_performance_logs(driver, fresh=needs_fresh_logs)
                failures, http_errors = self._summarize_performance_logs(performance_logs)
                data["network_failure_counts"] = failures
                data["http_error_counts"] = http_errors
                data["authentication_conflict"] = bool(
                    http_errors.get("idmsa.apple.com:409", 0)
                )
                if data["authentication_conflict"]:
                    data["authentication_conflict_detail"] = self._authentication_conflict_detail(
                        driver, performance_logs,
                    )
                    data["apple_cookie_summary"] = self._apple_cookie_summary(driver)
            except Exception:
                data["network_failure_counts"] = {}
                data["http_error_counts"] = {}
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            path = self.artifact_dir / f"{job_id}-diagnostics.json"
            path.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
            path.chmod(0o600)
            logger.warning(
                "Apple flow diagnostics stage=%s error_code=%s url_path=%s proxy_configured=%s",
                stage, error_code, data.get("url_path", ""), bool(proxy),
            )
        except OSError:
            pass

    @staticmethod
    def _driver_logs(driver, log_type: str) -> list:
        """Read Chromium logs across Selenium clients with/without get_log()."""
        getter = getattr(driver, "get_log", None)
        if callable(getter):
            return getter(log_type) or []
        from selenium.webdriver.remote.command import Command

        result = driver.execute(Command.GET_LOG, {"type": log_type})
        return result.get("value", []) if isinstance(result, dict) else []

    def _collect_performance_logs(self, driver, *, fresh: bool = True) -> list:
        """Collect performance events once and retain only bounded raw events.

        The cache lives only for the Selenium session and is never written to
        disk. Persisted diagnostics below contain aggregate host/status counts
        only, preventing query strings, headers and request bodies from being
        exposed while still allowing post-login failures to be classified.
        """
        cached = list(getattr(driver, "_apple_flow_performance_logs", []) or [])
        fresh_entries = []
        if fresh:
            try:
                fresh_entries = self._driver_logs(driver, "performance")
            except Exception:
                fresh_entries = []
            try:
                setattr(driver, "_apple_flow_performance_logs_collected", True)
            except Exception:
                pass
        if fresh_entries:
            cached.extend(fresh_entries)
            cached = cached[-4000:]
            try:
                setattr(driver, "_apple_flow_performance_logs", cached)
            except Exception:
                pass
        return cached

    def _authentication_conflict_seen(self, driver) -> bool:
        _, errors = self._summarize_performance_logs(
            self._collect_performance_logs(driver)
        )
        return bool(errors.get("idmsa.apple.com:409", 0))

    @staticmethod
    def _safe_auth_response_shape(value) -> dict:
        """Return only non-sensitive structural clues from an Apple JSON body."""
        keys: set[str] = set()
        codes: set[str] = set()

        def visit(item, depth: int = 0):
            if depth > 4:
                return
            if isinstance(item, dict):
                for key, child in item.items():
                    key_text = str(key)[:80]
                    keys.add(key_text)
                    if key_text.lower() in {"code", "errorcode", "status", "type"}:
                        candidate = str(child)
                        if re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", candidate):
                            codes.add(candidate)
                    visit(child, depth + 1)
            elif isinstance(item, list):
                for child in item[:20]:
                    visit(child, depth + 1)

        visit(value)
        return {
            "json": isinstance(value, (dict, list)),
            "keys": sorted(keys)[:40],
            "codes": sorted(codes)[:20],
        }

    def _authentication_conflict_detail(self, driver, entries: list) -> dict:
        """Diagnose the 409 structurally without persisting its message/body."""
        request_ids = []
        header_names: set[str] = set()
        response_extra = {}
        for entry in entries:
            try:
                event = json.loads(str(entry.get("message", ""))).get("message", {})
                method = event.get("method")
                params = event.get("params", {})
                if method == "Network.responseReceivedExtraInfo":
                    response_extra[str(params.get("requestId", ""))] = params.get("headers", {})
                    continue
                if method != "Network.responseReceived":
                    continue
                response = params.get("response", {})
                host = (urlparse(str(response.get("url", ""))).hostname or "").lower()
                if host == "idmsa.apple.com" and int(response.get("status", 0) or 0) == 409:
                    request_id = str(params.get("requestId", ""))
                    if request_id:
                        request_ids.append(request_id)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        if not request_ids:
            return {"response_body_available": False}
        raw_headers = response_extra.get(request_ids[-1], {})
        if isinstance(raw_headers, dict):
            header_names = {
                str(key).lower()[:80] for key in raw_headers.keys()
            }
        base = {"response_header_names": sorted(header_names)[:50]}
        try:
            response = driver.execute_cdp_cmd(
                "Network.getResponseBody", {"requestId": request_ids[-1]},
            )
            body = str(response.get("body", ""))
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                return {**base, "response_body_available": bool(body), "json": False, "body_length": len(body)}
            return {
                **base,
                "response_body_available": True,
                "body_length": len(body),
                **self._safe_auth_response_shape(parsed),
            }
        except Exception:
            return {**base, "response_body_available": False}

    def _authentication_conflict_location(self, driver) -> str | None:
        """Read one in-memory Apple continuation target from the 409 headers."""
        entries = self._collect_performance_logs(driver)
        headers_by_request = {}
        response_request_ids = []
        for entry in entries:
            try:
                event = json.loads(str(entry.get("message", ""))).get("message", {})
                method = event.get("method")
                params = event.get("params", {})
                if method == "Network.responseReceivedExtraInfo":
                    headers_by_request[str(params.get("requestId", ""))] = params.get("headers", {})
                    continue
                if method != "Network.responseReceived":
                    continue
                response = params.get("response", {})
                host = (urlparse(str(response.get("url", ""))).hostname or "").lower()
                if host == "idmsa.apple.com" and int(response.get("status", 0) or 0) == 409:
                    response_request_ids.append(str(params.get("requestId", "")))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        for request_id in reversed(response_request_ids):
            headers = headers_by_request.get(request_id, {})
            if not isinstance(headers, dict):
                continue
            raw_location = next((str(value) for key, value in headers.items()
                                 if str(key).lower() == "location"), "")
            if not raw_location:
                continue
            candidate = urljoin("https://idmsa.apple.com/", raw_location)
            parsed = urlparse(candidate)
            host = (parsed.hostname or "").lower()
            if parsed.scheme == "https" and parsed.path and (
                host == "apple.com" or host.endswith(".apple.com")
            ):
                return candidate
        return None

    @staticmethod
    def _browser_error_categories(entries: list) -> dict:
        """Classify console failures without keeping their text or URLs."""
        categories = {
            "cors": ("cors", "cross-origin"),
            "cookie": ("cookie", "samesite", "third-party"),
            "storage": ("storage", "indexeddb", "localstorage"),
            "csp": ("content security policy", "refused to load"),
            "network": ("failed to fetch", "net::", "networkerror"),
            "javascript": ("typeerror", "referenceerror", "syntaxerror"),
        }
        result = {}
        for item in entries:
            message = str(item.get("message", "")).lower()
            for name, terms in categories.items():
                if any(term in message for term in terms):
                    result[name] = result.get(name, 0) + 1
        return result

    @staticmethod
    def _apple_cookie_summary(driver) -> dict:
        """Return aggregate Apple cookie metadata only; never names or values."""
        try:
            cookies = driver.execute_cdp_cmd("Network.getAllCookies", {}).get("cookies", [])
        except Exception:
            return {"available": False}
        domains = {}
        for cookie in cookies if isinstance(cookies, list) else []:
            domain = str(cookie.get("domain", "")).lower().lstrip(".")
            if domain == "apple.com" or domain.endswith(".apple.com"):
                domains[domain] = domains.get(domain, 0) + 1
        return {"available": True, "domains": dict(sorted(domains.items())[:20]), "count": sum(domains.values())}

    @staticmethod
    def _summarize_performance_logs(entries: list) -> tuple[dict, dict]:
        failures = {}
        http_errors = {}
        for entry in entries:
            try:
                event = json.loads(str(entry.get("message", ""))).get("message", {})
                method = event.get("method", "")
                params = event.get("params", {})
                if method == "Network.loadingFailed":
                    error = re.sub(
                        r"[^A-Za-z0-9_.:-]", "",
                        str(params.get("errorText", "network_error")),
                    )[:80]
                    reason = re.sub(
                        r"[^A-Za-z0-9_.:-]", "",
                        str(params.get("blockedReason", "")),
                    )[:40]
                    key = error + ((":" + reason) if reason else "")
                    failures[key] = failures.get(key, 0) + 1
                elif method == "Network.responseReceived":
                    response = params.get("response", {})
                    status = int(response.get("status", 0) or 0)
                    if status >= 400:
                        host = (urlparse(str(response.get("url", ""))).hostname or "unknown")[:120]
                        key = f"{host}:{status}"
                        http_errors[key] = http_errors.get(key, 0) + 1
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return dict(list(failures.items())[:20]), dict(list(http_errors.items())[:20])

    @staticmethod
    def _is_proxy_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(term in message for term in (
            "err_proxy", "err_tunnel", "proxy connection", "proxy tunnel", "tunnel connection",
            "socks", "connection timed out", "connection refused", "connection reset",
            "connection closed", "err_connection_refused", "err_connection_reset",
            "err_connection_closed", "err_empty_response", "err_timed_out", "name_not_resolved",
        ))

    def _get(self, driver, url: str, password_submitted: bool = False):
        from selenium.common.exceptions import TimeoutException

        navigation_token = secrets.token_hex(8)
        try:
            driver.execute_script("window.__appleFlowNavigationToken = arguments[0];", navigation_token)
        except Exception:
            navigation_token = ""
        try:
            driver.get(url)
            self._wait_for_navigation_document(driver, url, navigation_token)
            source = (driver.page_source or "").lower()
            if any(term in source for term in (
                "err_proxy_connection_failed", "err_tunnel_connection_failed",
                "err_empty_response", "err_connection_closed", "err_timed_out",
            )):
                raise FlowError("proxy_error", "代理连接失败")
        except FlowError:
            raise
        except TimeoutException as exc:
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass
            raise self._page_unready_error(
                driver, password_submitted=password_submitted,
            ) from exc
        except Exception as exc:
            if self._is_webdriver_timeout(exc):
                raise FlowError(
                    "driver_timeout", "浏览器导航命令超时", retryable=not password_submitted,
                    password_submitted=password_submitted,
                ) from exc
            if self._is_proxy_error(exc):
                raise FlowError("proxy_error", "代理连接失败") from exc
            raise

    def _wait_for_navigation_document(self, driver, target_url: str, navigation_token: str):
        from selenium.webdriver.support.ui import WebDriverWait

        target_host = (urlparse(str(target_url or "")).hostname or "").lower()

        def committed(d):
            try:
                state = d.execute_script(
                    "return {"
                    "ready: document.readyState || '',"
                    "hasRoot: !!document.documentElement,"
                    "hasBody: !!document.body,"
                    "token: window.__appleFlowNavigationToken || ''"
                    "};"
                )
                current = urlparse(str(getattr(d, "current_url", "") or ""))
                current_host = (current.hostname or "").lower()
                if not current_host or current.scheme not in {"http", "https"}:
                    return False
                if target_host and current_host != target_host and not current_host.endswith(".apple.com"):
                    return False
                if navigation_token and str(state.get("token", "")) == navigation_token:
                    return False
                return bool(state.get("hasRoot") and state.get("hasBody"))
            except Exception:
                return False

        WebDriverWait(driver, settings.page_load_timeout, poll_frequency=0.25).until(committed)

    @staticmethod
    def _is_webdriver_timeout(exc: Exception) -> bool:
        name = exc.__class__.__name__.lower()
        message = str(exc).lower()
        return "timeout" in name or any(term in message for term in (
            "read timed out", "read timeout", "command timed out", "timed out receiving message",
        ))
