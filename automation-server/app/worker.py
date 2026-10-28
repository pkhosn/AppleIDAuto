import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .config import settings
from .executor import AccountExecutor
from .models import TaskStore
from .panel_client import PanelClient
from .queue import TaskQueue
from .notifier import notify_account_event
from .mail_recovery import BillionMailClient
from scripts.controlled_security_rollback import run_controlled_security_rollback
from scripts.controlled_email_recovery import run_controlled_email_password_change


class Worker:
    def __init__(self, store: TaskStore, queue: TaskQueue):
        self.store, self.queue = store, queue
        self.panel, self.executor = PanelClient(), AccountExecutor()

    async def run_one(self, account_id: int):
        token = await self.queue.acquire_lock(account_id, settings.account_lock_ttl)
        if not token:
            self.store.log(account_id, "run", "skipped", "相同账号任务正在执行")
            return
        refresh = asyncio.create_task(self._refresh_lock(account_id, token))
        try:
            await self._run_locked(account_id, token)
        finally:
            refresh.cancel()
            await asyncio.gather(refresh, return_exceptions=True)
            await self.queue.release_lock(account_id, token)

    async def _run_locked(self, account_id: int, lock_token: str | None = None):
        row = self.store.get(account_id)
        if row and row["status"] == "disabled":
            return
        self.store.upsert(account_id, "running", "任务执行中")
        interval = 60
        try:
            info = await self.panel.task_info(account_id)
            if not info.get("enable", True):
                self.store.upsert(account_id, "disabled", "管理站任务已停用")
                self.store.log(account_id, "run", "skipped", "管理站任务已停用")
                return
            if info.get('schedule', {}).get('active'):
                from .feature_runner import FeatureRunner
                await FeatureRunner(self).run(account_id, info, lock_token=lock_token)
                return
            interval = max(int(info.get("check_interval", 60)), 1)
            run_info = dict(info)
            # 定时检测救援邮箱：发现 Apple 的“恢复以前的安全设置”邮件时，先于登录
            # 检查主动触发关闭双重认证，而不是依赖登录挑战的 two_factor 判定。
            mailbox_rollback = False
            if run_info.get("enable_auto_disable_two_factor"):
                mailbox_rollback = await self._mailbox_has_security_rollback(account_id, info)
            if mailbox_rollback:
                ok, message, password, error_code = await self._auto_disable_two_factor(account_id, info)
                profile_payload = None
            else:
                ok, message, password, error_code, profile_payload = await self.executor.run(run_info)
                if not ok and error_code == "two_factor" and run_info.get("enable_auto_disable_two_factor"):
                    ok, message, password, error_code = await self._auto_disable_two_factor(account_id, info)
                    profile_payload = None
                elif not ok and error_code in {"password_invalid", "authentication_challenge_unrendered"} and self._email_recovery_available(run_info):
                    ok, message, password, error_code = await self._auto_email_recovery(account_id, run_info)
                    profile_payload = None
            notification_result = await notify_account_event(
                str(info.get("username") or ""), "自动化任务", ok, message,
                state=str(error_code or ("normal" if ok else "unknown")),
                bot_token=str(info.get("tg_bot_token") or ""), chat_id=str(info.get("tg_chat_id") or ""),
                notify_mode=str(info.get("tg_notify_mode") or "all"),
            )
            if notification_result != "sent":
                status = "skipped" if notification_result == "not_configured" else "failed"
                if notification_result == "filtered":
                    status = "skipped"
                    detail = "Telegram 通知已按策略跳过"
                else:
                    detail = f"Telegram 通知未发送：{notification_result}"
                self.store.log(account_id, "notify", status, detail)
            if info.get("proxy_id"):
                try:
                    if error_code == "proxy_error":
                        await self.panel.report_proxy_error(int(info["proxy_id"]))
                    else:
                        await self.panel.report_proxy_success(int(info["proxy_id"]))
                except Exception as exc:
                    self.store.log(account_id, "proxy", "failed", type(exc).__name__)
            if error_code == "proxy_unavailable":
                next_run = (datetime.now(timezone.utc) + timedelta(seconds=settings.proxy_retry_seconds)).isoformat()
                self.store.upsert(account_id, "failed", message, next_run)
                self.store.set_retry_count(account_id, 0)
                self.store.log(account_id, "run", "skipped", message)
                return
            await self.panel.report(info["username"], ok, message, password, profile=profile_payload)
            delay = settings.proxy_retry_seconds if error_code == "proxy_error" else interval * 60
            next_run = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            self.store.upsert(account_id, "success" if ok else "failed", message, next_run)
            self.store.set_retry_count(account_id, 0 if ok else 1)
            self.store.log(account_id, "run", "success" if ok else "failed", message)
        except Exception as exc:
            row = self.store.get(account_id)
            retries = int(row["retry_count"] or 0) + 1 if row else 1
            terminal = retries >= settings.max_retries
            status = "failed" if terminal else "pending"
            next_run = (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat() if terminal else None
            self.store.upsert(account_id, status, str(exc), next_run)
            self.store.set_retry_count(account_id, retries)
            self.store.log(account_id, "run", "failed", str(exc))
            await notify_account_event("", "自动化任务异常", False, type(exc).__name__)
            if status == "pending":
                await asyncio.sleep(min(2 ** retries, 30))
                await self.queue.put(account_id)

    async def _mailbox_has_security_rollback(self, account_id: int, info: dict) -> bool:
        """Whether the rescue mailbox holds a recent, *unprocessed* Apple 2FA rollback email.

        Apple delivers the “恢复以前的安全设置” message when two-factor
        authentication is enabled.  Polling the owned mailbox lets the worker
        disable 2FA proactively on its periodic cycle without waiting for the
        login challenge to be classified.

        The rollback message stays in the mailbox long after its one-time link
        has been consumed.  Only a brand-new message (the newest eligible one
        that has not yet been processed) is a signal: once the newest message
        has been acted on, any older message is stale and must not re-trigger.
        """
        mailbox = info.get("email_recovery") if isinstance(info.get("email_recovery"), dict) else {}
        if not mailbox.get("configured"):
            return False
        expected_account = str(info.get("username") or "") if mailbox.get("account_match_required") else None
        not_before = datetime.now(timezone.utc) - timedelta(days=14)
        try:
            link = await BillionMailClient().find_link(
                mailbox, "security_rollback", not_before, expected_account=expected_account,
            )
        except Exception:
            return False
        if link is None:
            return False
        return link.message_id not in self.store.rollback_seen_message_ids(account_id)

    async def _auto_disable_two_factor(self, account_id: int, info: dict):
        """Freeze the account and restore Apple's prior security settings.

        Apple only keeps the two-factor enrollment-confirmation email in the
        rescue mailbox for a limited window.  The rollback therefore fails
        closed and leaves the account frozen when no usable link exists, so a
        two-factor account is never silently re-enabled.
        """
        username = str(info.get("username") or "")
        try:
            await self.panel.disable_account(username)
        except Exception as exc:
            self.store.log(account_id, "two_factor", "failed", f"自动关闭双重认证前冻结失败: {type(exc).__name__}")
            return False, "检测到双重认证，但冻结账号失败，未自动关闭", None, "auto_disable_freeze_failed"
        try:
            result = await run_controlled_security_rollback(account_id, store=self.store)
        except Exception as exc:
            self.store.log(account_id, "two_factor", "failed", f"自动关闭双重认证异常: {type(exc).__name__}")
            return False, "检测到双重认证，但自动关闭执行异常，账号已冻结", None, "auto_disable_two_factor_failed"
        if result.get("status") == "success":
            self.store.log(account_id, "two_factor", "success", "检测到双重认证并已自动关闭")
            return True, "检测到双重认证并已自动关闭，账号已恢复", None, None
        code = str(result.get("error_code") or result.get("stage") or "auto_disable_two_factor_failed")
        message = f"检测到双重认证，但自动关闭未完成（{code}），账号已冻结"
        self.store.log(account_id, "two_factor", "failed", message)
        return False, message, None, code

    def _email_recovery_available(self, info: dict) -> bool:
        """Whether a failed password login can be recovered through the owned mailbox."""
        methods = set(info.get("recovery_methods") or [])
        mailbox = info.get("email_recovery") if isinstance(info.get("email_recovery"), dict) else {}
        return "email_password" in methods and bool(mailbox.get("configured"))

    async def _auto_email_recovery(self, account_id: int, info: dict):
        """Freeze the account and reset the Apple password through the rescue mailbox.

        This runs fail-closed: the account is frozen before the reset starts and
        remains frozen whenever the mailbox reset cannot be confirmed, so a
        wrong-password account is never silently re-enabled.
        """
        username = str(info.get("username") or "")
        try:
            await self.panel.disable_account(username)
        except Exception as exc:
            self.store.log(account_id, "email_recovery", "failed", f"自动找回密码前冻结失败: {type(exc).__name__}")
            return False, "密码校验失败，且冻结账号失败，未自动找回密码", None, "email_recovery_freeze_failed"
        try:
            result = await run_controlled_email_password_change(
                account_id, Path("/data/recovery-escrow"),
                panel=self.panel, account_client=self.executor.client, mailbox_client=BillionMailClient(),
            )
        except Exception as exc:
            self.store.log(account_id, "email_recovery", "failed", f"自动找回密码异常: {type(exc).__name__}")
            return False, "密码校验失败，自动找回密码执行异常，账号已冻结", None, "auto_email_recovery_failed"
        if result.get("status") == "success":
            self.store.log(account_id, "email_recovery", "success", "已通过救援邮箱自动找回密码")
            return True, "密码校验失败，已通过救援邮箱自动找回密码", None, None
        code = str(result.get("error_code") or result.get("stage") or "auto_email_recovery_failed")
        message = f"密码校验失败，自动找回密码未完成（{code}），账号已冻结"
        self.store.log(account_id, "email_recovery", "failed", message)
        return False, message, None, code

    async def _refresh_lock(self, account_id: int, token: str):
        while True:
            await asyncio.sleep(max(settings.account_lock_ttl // 3, 10))
            if not await self.queue.extend_lock(account_id, token, settings.account_lock_ttl):
                return

    async def loop(self):
        while True:
            item = await self.queue.pop()
            if item:
                await self.run_one(int(item["account_id"]))
