import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.telegram_captcha import TelegramCaptchaBroker, _BotState, _captcha_caption


class TelegramCaptchaBrokerTest(unittest.IsolatedAsyncioTestCase):
    async def test_captcha_caption_shows_full_account(self):
        caption = _captcha_caption(
            "fixture@example.test", "00000000-0000-4000-8000-000000000001", {"attempt": 2},
        )
        self.assertIn("账号：fixture@example.test", caption)
        self.assertNotIn("***", caption)
        self.assertIn("任务：00000000", caption)

    async def test_replies_are_routed_by_chat_and_photo_message(self):
        broker = TelegramCaptchaBroker()
        state = _BotState()
        first = asyncio.get_running_loop().create_future()
        second = asyncio.get_running_loop().create_future()
        state.replies[("100", 11)] = first
        state.replies[("100", 12)] = second

        await broker._dispatch("token", state, {
            "chat": {"id": 100}, "text": "ab12", "message_id": 20,
            "reply_to_message": {"message_id": 12},
        })
        self.assertFalse(first.done())
        self.assertEqual("AB12", second.result())

    async def test_invalid_and_unrelated_messages_do_not_complete_challenge(self):
        broker = TelegramCaptchaBroker()
        state = _BotState()
        future = asyncio.get_running_loop().create_future()
        state.replies[("100", 11)] = future
        with patch.object(broker, "_send_text", new=AsyncMock()) as notice:
            await broker._dispatch("token", state, {
                "chat": {"id": 100}, "text": "bad!", "message_id": 20,
                "reply_to_message": {"message_id": 11},
            })
            await broker._dispatch("token", state, {
                "chat": {"id": 999}, "text": "AB12", "message_id": 21,
                "reply_to_message": {"message_id": 11},
            })
        self.assertFalse(future.done())
        notice.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
