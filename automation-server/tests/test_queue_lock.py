import asyncio
import unittest

from app.queue import TaskQueue


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script, count, key, *args):
        token = args[0]
        if self.values.get(key) != token:
            return 0
        if "expire" in script:
            return 1
        self.values.pop(key, None)
        return 1


class QueueLockTest(unittest.TestCase):
    def test_account_lock_uses_owner_token(self):
        async def scenario():
            redis = FakeRedis()
            queue = TaskQueue(redis)
            token = await queue.acquire_lock(7, 600)
            self.assertTrue(token)
            self.assertIsNone(await queue.acquire_lock(7, 600))
            self.assertFalse(await queue.extend_lock(7, "wrong", 600))
            await queue.release_lock(7, "wrong")
            self.assertIn("automation:lock:7", redis.values)
            self.assertTrue(await queue.extend_lock(7, token, 600))
            await queue.release_lock(7, token)
            self.assertNotIn("automation:lock:7", redis.values)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
