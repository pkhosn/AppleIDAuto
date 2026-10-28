import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .api import router
from .config import settings
from .models import TaskStore
from .queue import TaskQueue
from .worker import Worker
from .scheduler import Scheduler
from .proxy_scheduler import ProxyScheduler
from .telegram_captcha import telegram_captcha_broker


@asynccontextmanager
async def lifespan(app: FastAPI):
    store, queue = TaskStore(settings.database_path), TaskQueue()
    scheduler_service = Scheduler(store, queue)
    try:
        await scheduler_service.bootstrap()
    except Exception as exc:
        store.log(0, "bootstrap", "failed", str(exc))
    workers = [asyncio.create_task(Worker(store, queue).loop()) for _ in range(settings.worker_concurrency)]
    scheduler = asyncio.create_task(scheduler_service.loop())
    proxy_scheduler = asyncio.create_task(ProxyScheduler().loop())
    yield
    for worker in workers:
        worker.cancel()
    scheduler.cancel()
    proxy_scheduler.cancel()
    await telegram_captcha_broker.close()
    await asyncio.gather(*workers, return_exceptions=True)
    try:
        await scheduler
    except asyncio.CancelledError:
        pass
    try:
        await proxy_scheduler
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Automation Task Server", version="1.1.0", lifespan=lifespan)
app.include_router(router)
