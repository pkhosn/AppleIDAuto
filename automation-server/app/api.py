import asyncio
import secrets
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from .config import settings
from .account_client import AccountClient
from .mail_recovery import BillionMailClient, MailRecoveryError
from .models import TaskStore
from .panel_client import PanelClient
from .queue import TaskQueue
from .notifier import notify_account_event
from .feature_schedule import FeatureSchedule
from .proxy_scheduler import ProxyScheduler
from scripts.controlled_email_recovery import run_controlled_email_password_change
from scripts.controlled_security_rollback import run_controlled_security_rollback

router = APIRouter()
store = TaskStore(settings.database_path)
queue = TaskQueue()
manual_recovery_runs: dict[str, dict] = {}
manual_recovery_accounts: set[int] = set()


class TaskRequest(BaseModel):
    id: int = Field(gt=0)


class RestartTaskRequest(TaskRequest):
    mode: str = Field(default="all", pattern=r"^(all|due|feature|reconcile)$")
    feature: str | None = Field(default=None, pattern=r"^(two_factor|password_check|profile_restore|device_cleanup|password_update)$")
    confirmation: str = Field(default="", max_length=96)


class MailboxConfigRequest(BaseModel):
    id: int = 0
    email: str = Field(min_length=3, max_length=320)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=2048)
    tls: bool = True


class ManualRecoveryRequest(TaskRequest):
    confirmation: str = Field(min_length=16, max_length=96)
    resume_existing_link: bool = False


MAILBOX_TEST_MESSAGES = {
    "mailbox_config_invalid": "救援邮箱配置不完整或无法解密",
    "mailbox_connection_failed": "无法连接 IMAPS 服务器",
    "mailbox_address_blocked": "IMAPS 主机解析到了私网或保留地址",
    "mailbox_tls_failed": "IMAPS TLS 证书或加密握手失败",
    "mailbox_auth_failed": "IMAP 用户名或密码验证失败",
    "mailbox_inbox_unavailable": "已连接邮箱，但无法以只读方式打开收件箱",
}

PROXY_TEST_MESSAGES = {
    "invalid_proxy": "静态 IP 格式无效",
    "proxy_auth_failed": "静态 IP 认证失败",
    "proxy_target_forbidden": "静态 IP 无法访问 Apple 服务",
    "proxy_handshake_failed": "静态 IP 握手失败",
    "proxy_protocol_error": "静态 IP 协议响应异常",
    "proxy_connect_failed": "静态 IP 连接失败或超时",
    "target_unreachable": "Apple 服务通过该静态 IP 不可达",
}


def auth(token: str | None):
    configured = settings.token.strip()
    if len(configured) < 32 or configured in {"change-me", "123456"}:
        raise HTTPException(status_code=503, detail="automation_token_not_configured")
    if not token or not secrets.compare_digest(token, configured):
        raise HTTPException(status_code=401, detail="invalid token")


@router.post("/addTask")
async def add_or_restart(payload: TaskRequest, token: str | None = Header(default=None)):
    auth(token)
    info = await PanelClient().task_info(payload.id)
    if info.get('schedule', {}).get('active'):
        features = FeatureSchedule(store)
        row = store.get(payload.id)
        features.sync(payload.id, info['schedule'], bool(info.get('enable')), dict(row) if row else None)
        if row and row['status'] == 'running':
            return {"status": True, "msg": "配置已同步，当前执行完成后生效"}
        if features.due(payload.id):
            store.upsert(payload.id, 'pending', '等待执行', features.next_run(payload.id))
            await queue.put(payload.id)
        else:
            await queue.remove(payload.id)
            status = ('failed' if row and row['status'] == 'failed' else 'success') if info.get('enable') else 'disabled'
            store.upsert(payload.id, status, '配置已同步', features.next_run(payload.id))
        return {"status": True, "msg": "周期配置已同步，不强制执行任务"}
    store.upsert(payload.id, "pending", "任务已进入队列")
    await queue.put(payload.id)
    store.log(payload.id, "restart", "success", "任务已进入队列")
    return {"status": True, "msg": "任务已提交"}


@router.post('/restartTask')
async def restart(payload: RestartTaskRequest, token: str | None = Header(default=None)):
    auth(token)
    info = await PanelClient().task_info(payload.id)
    if not info.get('enable'):
        return {'status': False, 'msg': '账号已停用，请先明确启用账号'}
    row = store.get(payload.id)
    if row and row['status'] == 'running':
        return {'status': False, 'msg': '账号正在执行，请等待结束'}
    if info.get('schedule', {}).get('active'):
        features = FeatureSchedule(store)
        features.sync(payload.id, info['schedule'], True, dict(row) if row else None)
        selected = [payload.feature] if payload.feature else []
        if payload.mode in {'feature', 'reconcile'} and not selected:
            return {'status': False, 'msg': '请选择要执行的功能'}
        if payload.mode == 'reconcile':
            features.force_reconciliation(payload.id, payload.feature)
        elif payload.mode == 'due':
            if not features.due(payload.id):
                return {'status': False, 'msg': '当前没有已到期功能'}
        else:
            uncertain = [key for key in (selected or list(features.rows(payload.id)))
                         if features.rows(payload.id).get(key, {}).get('result') == 'uncertain']
            destructive = {'profile_restore', 'device_cleanup', 'password_update'}
            if any(key in destructive for key in uncertain) and not secrets.compare_digest(
                    payload.confirmation, 'CONFIRM_UNCERTAIN_RETRY'):
                return {'status': False, 'msg': '待确认的破坏性操作默认只能只读核验；重新提交需二次确认'}
            features.force(payload.id, selected or None, allow_uncertain=bool(uncertain))
    label = '运行全部到期功能' if payload.mode == 'due' else ('只读核验待确认结果' if payload.mode == 'reconcile' else '手动运行任务')
    store.upsert(payload.id, 'pending', label)
    await queue.put(payload.id)
    return {'status': True, 'msg': label + '已进入队列'}


@router.post('/scheduleStatus')
async def schedule_status(payload: TaskRequest, token: str | None = Header(default=None)):
    auth(token)
    features = FeatureSchedule(store)
    row = store.get(payload.id)
    return {'status': True, 'data': {'features': features.rows(payload.id),
            'status': row['status'] if row else 'unknown', 'due': features.due(payload.id),
            'active_run': store.active_run(payload.id), 'runs': store.recent_runs(payload.id, 10)}}


@router.post("/removeTask")
async def remove(payload: TaskRequest, token: str | None = Header(default=None)):
    auth(token)
    await queue.remove(payload.id)
    row = store.get(payload.id)
    # Freezing disables future scheduling; the in-flight recovery still owns its result.
    if not row or row['status'] != 'running':
        store.upsert(payload.id, "disabled", "任务已禁用")
    store.log(payload.id, "disable", "success", "任务已禁用")
    return {"status": True, "msg": "任务已禁用"}


@router.post("/testMailbox")
async def test_mailbox(payload: TaskRequest, token: str | None = Header(default=None)):
    """Test only the saved mailbox login; never fetch or alter any message."""
    auth(token)
    try:
        config = await PanelClient().mailbox_config(payload.id)
        await BillionMailClient().test_connection(config)
    except MailRecoveryError as exc:
        return {"status": False, "msg": MAILBOX_TEST_MESSAGES.get(exc.code, "救援邮箱测试失败")}
    except Exception:
        return {"status": False, "msg": "无法读取救援邮箱配置或连接自动化服务"}
    return {"status": True, "msg": "IMAPS 连接和收件箱只读访问正常"}


@router.post("/testMailboxConfig")
async def test_mailbox_config(payload: MailboxConfigRequest, token: str | None = Header(default=None)):
    """Test an unsaved form draft in memory; credentials are never persisted or logged."""
    auth(token)
    if not payload.tls:
        return {"status": False, "msg": "救援邮箱必须使用 TLS/IMAPS"}
    try:
        await BillionMailClient().test_connection(payload.model_dump(exclude={"id"}))
    except MailRecoveryError as exc:
        return {"status": False, "msg": MAILBOX_TEST_MESSAGES.get(exc.code, "救援邮箱测试失败")}
    except Exception:
        return {"status": False, "msg": "无法连接 IMAPS 服务器"}
    return {"status": True, "msg": "IMAPS 连接和收件箱只读访问正常"}


@router.post("/testProxy")
async def test_proxy(payload: TaskRequest, token: str | None = Header(default=None)):
    auth(token)
    try:
        panel = PanelClient()
        target = await panel.proxy_health_target(payload.id)
        result = await ProxyScheduler(panel)._check_one(target)
    except Exception:
        return {"status": False, "msg": "静态 IP 测试服务不可用", "data": {}}
    if result.get("status"):
        return {"status": True, "msg": f"静态 IP 可用，检测耗时 {result['latency_ms']} ms", "data": result}
    code = str(result.get("error_code") or "")
    return {"status": False, "msg": PROXY_TEST_MESSAGES.get(code, "静态 IP 连接失败"), "data": result}


async def _run_manual_recovery(run_id: str, account_id: int, kind: str, resume_existing_link: bool = False):
    manual_recovery_runs[run_id] = {"status": "running", "account_id": account_id, "kind": kind}
    store.log(account_id, kind, "running", "受控邮件恢复已开始")
    try:
        if kind == "email_password_recovery":
            kwargs = {"resume_existing_link": True} if resume_existing_link else {}
            result = await run_controlled_email_password_change(account_id, Path("/data/recovery-escrow"), **kwargs)
        else:
            result = await run_controlled_security_rollback(account_id, store=store)
        if result.get('status') == 'success' and isinstance(store, TaskStore):
            features = FeatureSchedule(store)
            if 'password_update' in features.rows(account_id):
                features.finish(account_id, ['password_update'], 'success', '受控邮件恢复已改密；重置定期改密周期')
        manual_recovery_runs[run_id] = {
            "status": result.get("status", "failed"), "account_id": account_id, "kind": kind,
            "stage": result.get("stage", ""), "error_code": result.get("error_code", ""),
            "job_ids": list(result.get("job_ids", [])),
        }
        stage = str(result.get("stage", ""))[:80]
        error_code = str(result.get("error_code", ""))[:120]
        message = f"阶段: {stage}" if not error_code else f"阶段: {stage}; 原因: {error_code}"
        store.log(account_id, kind, str(result.get("status", "failed")), message)
        try:
            task_info = await PanelClient().task_info(account_id)
            await notify_account_event(
                str(task_info.get("username") or ""),
                "关闭双重认证" if kind == "security_rollback" else "邮件重设密码",
                result.get("status") == "success", message, state=stage,
                job_id=(list(result.get("job_ids") or [""])[-1] if result.get("job_ids") else ""),
                bot_token=str(task_info.get("tg_bot_token") or ""), chat_id=str(task_info.get("tg_chat_id") or ""),
                notify_mode=str(task_info.get("tg_notify_mode") or "all"),
            )
        except Exception:
            # Notifications are best-effort and must never turn a completed
            # recovery into a failed one.
            pass
    except Exception as exc:
        manual_recovery_runs[run_id] = {
            "status": "failed", "account_id": account_id, "kind": kind,
            "stage": "unexpected", "error_code": type(exc).__name__, "job_ids": [],
        }
        store.log(account_id, kind, "failed", type(exc).__name__)
    finally:
        manual_recovery_accounts.discard(account_id)


async def _start_manual_recovery(payload: ManualRecoveryRequest, confirmation: str, kind: str):
    if not secrets.compare_digest(payload.confirmation, confirmation):
        raise HTTPException(status_code=400, detail="confirmation_required")
    if payload.id in manual_recovery_accounts:
        raise HTTPException(status_code=409, detail="account_recovery_in_progress")
    if payload.resume_existing_link and kind != "email_password_recovery":
        raise HTTPException(status_code=400, detail="resume_existing_link_not_supported")
    lock_token = await queue.acquire_lock(payload.id, settings.account_lock_ttl)
    if not lock_token:
        raise HTTPException(status_code=409, detail="account_task_in_progress")
    run_id = uuid.uuid4().hex
    manual_recovery_accounts.add(payload.id)
    asyncio.create_task(_run_locked_manual_recovery(run_id, payload.id, kind, payload.resume_existing_link, lock_token))
    return {"status": True, "msg": "受控恢复任务已提交", "data": {"run_id": run_id}}


async def _run_locked_manual_recovery(run_id, account_id, kind, resume_existing_link, lock_token):
    async def renew():
        while True:
            await asyncio.sleep(max(settings.account_lock_ttl // 3, 10))
            if not await queue.extend_lock(account_id, lock_token, settings.account_lock_ttl):
                return
    refresh = asyncio.create_task(renew())
    try:
        await _run_manual_recovery(run_id, account_id, kind, resume_existing_link)
    finally:
        refresh.cancel()
        await asyncio.gather(refresh, return_exceptions=True)
        await queue.release_lock(account_id, lock_token)


@router.post("/startEmailPasswordRecovery", status_code=202)
async def start_email_password_recovery(payload: ManualRecoveryRequest, token: str | None = Header(default=None)):
    auth(token)
    return await _start_manual_recovery(payload, "ONE_TIME_APPLE_EMAIL_PASSWORD_RECOVERY", "email_password_recovery")


@router.post("/startSecurityRollback", status_code=202)
async def start_security_rollback(payload: ManualRecoveryRequest, token: str | None = Header(default=None)):
    auth(token)
    return await _start_manual_recovery(payload, "ONE_TIME_APPLE_SECURITY_ROLLBACK", "security_rollback")


@router.get("/manualRecovery/{run_id}")
async def manual_recovery_status(run_id: str, token: str | None = Header(default=None)):
    auth(token)
    result = manual_recovery_runs.get(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="manual_recovery_not_found")
    return {"status": True, "data": result}


@router.get("/manualRecoveryHistory/{account_id}")
async def manual_recovery_history(account_id: int, token: str | None = Header(default=None)):
    auth(token)
    if account_id < 1:
        raise HTTPException(status_code=400, detail="invalid_account_id")
    return {"status": True, "data": store.manual_recovery_logs(account_id)}


@router.get("/get_task_list")
async def task_list(token: str | None = Header(default=None)):
    auth(token)
    return {"status": True, "msg": "获取成功", "data": [dict(row) for row in store.all()]}


@router.get("/health")
async def health(token: str | None = Header(default=None)):
    auth(token)
    await queue.redis.ping()
    try:
        driver = "ok" if await AccountClient().health() else "error"
    except Exception:
        driver = "error"
    return {"status": driver == "ok", "msg": "ok" if driver == "ok" else "账号驱动异常",
            "data": {"redis": "ok", "database": "ok", "account_driver": driver}}
