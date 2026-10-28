import asyncio
import hashlib
import json
from datetime import datetime, timezone
from .models import TaskStore
from .panel_client import PanelClient
from .queue import TaskQueue
from .feature_schedule import FeatureSchedule


class Scheduler:
    def __init__(self, store: TaskStore, queue: TaskQueue, interval: float = 5):
        self.store, self.queue, self.interval = store, queue, interval
        self.features = FeatureSchedule(store)
        self.independent = False
        self.last_sync = 0.0
        self.last_sync_error_at = 0.0
        self._reported_states = {}
        self._config_revisions = {}

    async def sync(self):
        snapshots = await PanelClient().automation_schedules()
        self.independent = any(item['schedule'].get('active') for item in snapshots)
        if not self.independent:
            return
        known = set()
        for item in snapshots:
            account_id = int(item['id'])
            known.add(account_id)
            row = self.store.get(account_id)
            revision = str(item.get('config_revision') or hashlib.sha256(
                json.dumps({'enable': bool(item['enable']), 'schedule': item['schedule']},
                           sort_keys=True, separators=(',', ':')).encode()
            ).hexdigest())
            if self._config_revisions.get(account_id) != revision or not self.features.rows(account_id):
                self.features.sync(account_id, item['schedule'], item['enable'], dict(row) if row else None)
                self._config_revisions[account_id] = revision
            if not item['enable']:
                self.store.upsert(account_id, 'disabled', '管理站任务已停用')
                await self.queue.remove(account_id)
            elif not row or row['status'] != 'running':
                if self.features.due(account_id) or self.features.reconciliation_due(account_id):
                    self.store.upsert(account_id, 'pending', '等待执行', self.features.next_run(account_id))
                    await self.queue.put(account_id)
                else:
                    await self.queue.remove(account_id)
                    self.store.upsert(account_id, 'failed' if row and row['status'] == 'failed' else 'success',
                                      row['message'] if row and row['status'] == 'failed' else '周期配置已同步', self.features.next_run(account_id))
        for row in self.store.all():
            if row['account_id'] not in known:
                self._config_revisions.pop(row['account_id'], None)
                self.store.upsert(row['account_id'], 'disabled', '账号已移除')
                await self.queue.remove(row['account_id'])
        changed = []
        for row in self.store.all():
            state = (row['status'], row['next_run_at'])
            if self._reported_states.get(row['account_id']) != state:
                changed.append(row)
        if changed:
            await PanelClient().report_schedule_states(changed)
            for row in changed:
                self._reported_states[row['account_id']] = (row['status'], row['next_run_at'])

    async def bootstrap(self):
        self.features.recover_interrupted()
        await self.sync()
        if self.independent:
            return
        active_ids = set(await PanelClient().task_list())
        for row in self.store.all():
            if row["account_id"] not in active_ids:
                self.store.upsert(row["account_id"], "disabled", "管理站任务已停用")
                await self.queue.remove(int(row["account_id"]))
        for account_id in active_ids:
            row = self.store.get(account_id)
            if row and row['status'] in {'success', 'failed'} and row['next_run_at']:
                continue
            if row and row['status'] == 'running':
                self.store.upsert(account_id, 'failed', '服务中断，执行结果需人工确认')
                continue
            self.store.upsert(account_id, "pending", "服务启动，任务已同步")
            self.store.set_retry_count(account_id, 0)
            await self.queue.put(account_id)

    async def loop(self):
        while True:
            if asyncio.get_running_loop().time() - self.last_sync >= 30:
                try:
                    await self.sync()
                except Exception as exc:
                    now_monotonic = asyncio.get_running_loop().time()
                    if now_monotonic - self.last_sync_error_at >= 300:
                        self.store.log(0, 'schedule_sync', 'failed', type(exc).__name__)
                        self.last_sync_error_at = now_monotonic
                self.last_sync = asyncio.get_running_loop().time()
            now = datetime.now(timezone.utc)
            for row in self.store.all():
                if row["status"] not in {"success", "failed"} or not row["next_run_at"]:
                    continue
                try:
                    if self.independent and not (
                        self.features.due(row['account_id'])
                        or self.features.reconciliation_due(row['account_id'])
                    ):
                        continue
                    if datetime.fromisoformat(row["next_run_at"]) <= now:
                        self.store.upsert(row["account_id"], "pending", "任务已到执行时间")
                        self.store.set_retry_count(row["account_id"], 0)
                        await self.queue.put(int(row["account_id"]))
                except ValueError:
                    self.store.log(row["account_id"], "schedule", "failed", "下次执行时间格式无效")
            await asyncio.sleep(self.interval)
