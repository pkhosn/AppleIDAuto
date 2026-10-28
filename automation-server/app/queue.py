import json
from uuid import uuid4
import redis.asyncio as redis
from .config import settings


class TaskQueue:
    def __init__(self, redis_client=None):
        self.redis = redis_client or redis.from_url(settings.redis_url, decode_responses=True)

    async def put(self, account_id: int):
        item = json.dumps({"account_id": account_id})
        await self.redis.sadd("automation:active", account_id)
        await self.redis.lrem("automation:queue", 0, item)
        await self.redis.rpush("automation:queue", item)

    async def remove(self, account_id: int):
        await self.redis.srem("automation:active", account_id)
        await self.redis.lrem("automation:queue", 0, json.dumps({"account_id": account_id}))

    async def pop(self):
        item = await self.redis.blpop("automation:queue", timeout=1)
        return json.loads(item[1]) if item else None

    async def acquire_lock(self, account_id: int, ttl: int) -> str | None:
        token = str(uuid4())
        acquired = await self.redis.set(f"automation:lock:{account_id}", token, nx=True, ex=ttl)
        return token if acquired else None

    async def extend_lock(self, account_id: int, token: str, ttl: int) -> bool:
        result = await self.redis.eval(
            "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('expire',KEYS[1],ARGV[2]) else return 0 end",
            1, f"automation:lock:{account_id}", token, ttl,
        )
        return bool(result)

    async def owns_lock(self, account_id: int, token: str) -> bool:
        current = await self.redis.get(f"automation:lock:{account_id}")
        return bool(current and current == token)

    async def release_lock(self, account_id: int, token: str):
        await self.redis.eval(
            "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end",
            1, f"automation:lock:{account_id}", token,
        )
