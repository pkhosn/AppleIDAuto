"""Central Telegram reply dispatcher for interactive CAPTCHA challenges."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

import httpx

from .config import settings
from .notifier import _safe_account


class TelegramCaptchaError(RuntimeError):
    pass


def _captcha_caption(account: str, job_id: str, challenge: dict) -> str:
    """Build an identifiable prompt for a human CAPTCHA operator."""
    return (
        "Apple 人工验证码\n"
        f"账号：{_safe_account(account)}\n"
        f"人工尝试：{int(challenge.get('attempt', 1))}/{settings.captcha_human_attempts}\n"
        f"任务：{job_id[:8]}\n"
        "请直接回复此图片，只输入图片中的验证码。"
    )


@dataclass
class _BotState:
    offset: int = 0
    replies: dict[tuple[str, int], asyncio.Future] = field(default_factory=dict)
    task: asyncio.Task | None = None
    fatal_error: str | None = None


class TelegramCaptchaBroker:
    def __init__(self):
        self._bots: dict[str, _BotState] = {}
        self._lock = asyncio.Lock()

    async def solve(self, bot_token: str, chat_id: str, account: str, job_id: str,
                    challenge: dict, image: bytes) -> str:
        if not bot_token or not chat_id:
            raise TelegramCaptchaError("telegram_not_configured")
        state = await self._state(bot_token)
        if challenge.get("previous_rejected"):
            await self._send_text(bot_token, chat_id, "验证码错误，上一张图片已失效；请回复下面的新图片。")
        caption = _captcha_caption(account, job_id, challenge)
        message_id = await self._send_photo(bot_token, chat_id, image, caption)
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        key = (str(chat_id), message_id)
        state.replies[key] = future
        try:
            if state.fatal_error:
                raise TelegramCaptchaError(state.fatal_error)
            return await asyncio.wait_for(future, timeout=settings.captcha_human_timeout)
        except TimeoutError as exc:
            raise TelegramCaptchaError("captcha_human_timeout") from exc
        finally:
            state.replies.pop(key, None)

    async def _state(self, bot_token: str) -> _BotState:
        async with self._lock:
            state = self._bots.setdefault(bot_token, _BotState())
            if state.fatal_error:
                return state
            if not state.task or state.task.done():
                state.task = asyncio.create_task(self._poll(bot_token, state))
            return state

    async def _poll(self, bot_token: str, state: _BotState):
        while True:
            try:
                timeout = settings.telegram_poll_timeout
                async with httpx.AsyncClient(timeout=timeout + 10) as client:
                    response = await client.get(
                        f"https://api.telegram.org/bot{bot_token}/getUpdates",
                        params={"offset": state.offset, "timeout": timeout, "allowed_updates": '["message"]'},
                    )
                if response.status_code == 409:
                    state.fatal_error = "telegram_polling_conflict"
                    self._fail_all(state, "telegram_polling_conflict")
                    return
                response.raise_for_status()
                payload = response.json()
                if payload.get("ok") is not True:
                    await asyncio.sleep(2)
                    continue
                for update in payload.get("result", []):
                    state.offset = max(state.offset, int(update.get("update_id", 0)) + 1)
                    await self._dispatch(bot_token, state, update.get("message") or {})
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(2)

    async def _dispatch(self, bot_token: str, state: _BotState, message: dict):
        reply = message.get("reply_to_message") or {}
        reply_id = reply.get("message_id")
        chat_id = str((message.get("chat") or {}).get("id", ""))
        future = state.replies.get((chat_id, reply_id))
        if not future or future.done():
            return
        answer = str(message.get("text") or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{4,8}", answer):
            await self._send_text(
                bot_token, chat_id, "格式不正确，请继续回复同一张图片，仅输入 4–8 位英文字母或数字。",
                reply_to_message_id=message.get("message_id"),
            )
            return
        future.set_result(answer)

    @staticmethod
    def _fail_all(state: _BotState, code: str):
        for future in list(state.replies.values()):
            if not future.done():
                future.set_exception(TelegramCaptchaError(code))

    @staticmethod
    async def _send_photo(bot_token: str, chat_id: str, image: bytes, caption: str) -> int:
        try:
            async with httpx.AsyncClient(timeout=settings.telegram_notify_timeout) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendPhoto",
                    data={"chat_id": chat_id, "caption": caption},
                    files={"photo": ("captcha.png", image, "image/png")},
                )
            payload = response.json()
            if not response.is_success or payload.get("ok") is not True:
                raise TelegramCaptchaError("telegram_send_failed")
            return int(payload["result"]["message_id"])
        except TelegramCaptchaError:
            raise
        except Exception as exc:
            raise TelegramCaptchaError("telegram_send_failed") from exc

    @staticmethod
    async def _send_text(bot_token: str, chat_id: str, text: str,
                         reply_to_message_id: int | None = None):
        payload = {"chat_id": chat_id, "text": text}
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        try:
            async with httpx.AsyncClient(timeout=settings.telegram_notify_timeout) as client:
                await client.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", json=payload)
        except Exception:
            pass

    async def close(self):
        tasks = [state.task for state in self._bots.values() if state.task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for state in self._bots.values():
            self._fail_all(state, "telegram_send_failed")


telegram_captcha_broker = TelegramCaptchaBroker()
