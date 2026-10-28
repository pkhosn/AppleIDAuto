"""Best-effort Telegram status notices with no credential disclosure."""
from __future__ import annotations

import re
import httpx

from .config import settings


def _masked_account(value: str) -> str:
    """Keep interactive CAPTCHA prompts privacy-preserving."""
    name, marker, domain = str(value or "").partition("@")
    if not marker:
        return "账号#已隐藏"
    return (name[:2] + "***" if name else "***") + "@" + domain


def _safe_account(value: str) -> str:
    """Keep the full account visible while removing Telegram-breaking controls."""
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))[:254]


def _safe_text(value: str, limit: int = 300) -> str:
    # Never forward accidental credentials, URLs with one-time keys, or tokens.
    text = re.sub(r"https?://\S+", "[链接已隐藏]", str(value or ""))
    text = re.sub(r"(?i)(password|token|secret|proxy)\s*[:=]\s*\S+", r"\1=[已隐藏]", text)
    return text.replace("\x00", " ")[:limit]


async def notify_account_event(account: str, event: str, ok: bool, message: str,
                               state: str = "", job_id: str = "", bot_token: str = "",
                               chat_id: str = "", notify_mode: str = "all") -> str:
    """Send a status notification and return a non-sensitive delivery result."""
    mode = notify_mode if notify_mode in {"success", "failure", "all"} else "all"
    if (ok and mode == "failure") or (not ok and mode == "success"):
        return "filtered"
    if not bot_token or not chat_id:
        return "not_configured"
    status = "成功" if ok else "失败"
    fields = [
        "Apple 自动化通知", f"账号：{_safe_account(account)}", f"事件：{_safe_text(event, 80)}",
        f"结果：{status}", f"说明：{_safe_text(message)}",
    ]
    if state:
        fields.append(f"状态：{_safe_text(state, 80)}")
    if job_id:
        fields.append(f"任务：{_safe_text(job_id, 80)}")
    try:
        async with httpx.AsyncClient(timeout=settings.telegram_notify_timeout) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": "\n".join(fields), "disable_web_page_preview": True},
            )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.is_success and payload.get("ok") is True:
            return "sent"
        description = str(payload.get("description") or "").lower()
        if "chat not found" in description:
            return "chat_not_found"
        if "bot was blocked" in description:
            return "bot_blocked"
        if "user is deactivated" in description:
            return "user_deactivated"
        return f"telegram_http_{response.status_code}"
    except httpx.TimeoutException:
        return "telegram_timeout"
    except httpx.HTTPError:
        return "telegram_network_error"
    except Exception:
        return "telegram_unexpected_error"
