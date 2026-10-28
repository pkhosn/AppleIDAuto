"""Use Apple's short-lived enrollment-confirmation email to restore prior security settings."""

import argparse
import asyncio
import hashlib
import json
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.account_client import AccountClient
from app.mail_recovery import BillionMailClient, MailRecoveryError
from app.panel_client import PanelClient
from scripts.controlled_recover import (
    ControlledRecoveryError, build_driver_payload, generate_recovery_password,
    load_or_create_escrow, safe_result,
)


CONFIRMATION = "ONE_TIME_APPLE_SECURITY_ROLLBACK"


async def run_controlled_security_rollback(
    account_id: int,
    panel=None,
    account_client=None,
    mailbox_client=None,
    mailbox_wait_seconds: int = 180,
    maximum_link_age_days: int = 14,
    escrow_dir: Path | None = None,
    store=None,
) -> dict:
    panel = panel or PanelClient()
    account_client = account_client or AccountClient()
    mailbox_client = mailbox_client or BillionMailClient()
    job_ids: list[str] = []
    escrow_path: Path | None = None
    stage = "preflight"
    account_username = ""
    rollback_link = None
    try:
        info = await panel.task_info(account_id)
        account_username = str(info.get("username") or "")
        independent = bool(info.get('schedule', {}).get('active'))
        if bool(info.get("enable")) or (not independent and bool(info.get("enable_auto_update_password"))) or bool(info.get("enable_delete_devices")):
            raise ControlledRecoveryError("account_not_frozen", stage)
        mailbox = info.get("email_recovery") if isinstance(info.get("email_recovery"), dict) else {}
        if not mailbox.get("configured"):
            raise ControlledRecoveryError(str(mailbox.get("error_code") or "mailbox_config_invalid"), stage)
        expected_account = account_username if mailbox.get("account_match_required") else None
        # Explicitly disabled proxy nodes select direct mode, not a missing
        # proxy failure. Keep failing closed only when the panel says a proxy
        # is required but currently unavailable.
        direct_mode = str(info.get("connection_mode") or "") == "direct"
        proxy_ready = bool(info.get("proxy_available") and info.get("proxy_id") and info.get("proxy_content"))
        if not direct_mode and not proxy_ready:
            raise ControlledRecoveryError("proxy_unavailable", stage)
        escrow_path, new_password = load_or_create_escrow(
            escrow_dir or Path("/data/recovery-escrow"), account_id, generate_recovery_password,
        )

        # Apple sends the rollback message when two-factor authentication is
        # enabled; unlike password reset, there is no safe generic endpoint to
        # request another one.  Prefer the existing authenticated message and
        # only wait briefly for its delivery when it is not visible yet.
        stage = "mailbox_lookup"
        not_before = datetime.now(timezone.utc) - timedelta(days=max(maximum_link_age_days, 1))
        # 已处理过的恢复邮件（一次性链接）不再复用：邮箱里的旧邮件会保留很久，
        # 若不跳过，一轮成功关闭双重认证后，下一轮会再次命中同一封已失效邮件。
        seen_ids = store.rollback_seen_message_ids(account_id) if store else set()
        try:
            rollback_link = await mailbox_client.find_link(
                mailbox, "security_rollback", not_before, expected_account=expected_account,
                exclude_message_ids=seen_ids,
            )
            if rollback_link is None:
                stage = "mailbox_wait"
                rollback_link = await mailbox_client.wait_for_link(
                    mailbox, "security_rollback", not_before, timeout_seconds=mailbox_wait_seconds,
                    expected_account=expected_account, exclude_message_ids=seen_ids,
                )
        except MailRecoveryError as exc:
            raise ControlledRecoveryError(exc.code, stage) from exc

        stage = "security_rollback"
        operation_started = datetime.now(timezone.utc)
        result = await account_client.run("complete_security_rollback", {
            **build_driver_payload(info), "reset_url": rollback_link.url, "new_password": new_password,
        })
        job_id = str(result.get("_job_id") or "")
        if job_id:
            job_ids.append(job_id)
        driver_confirmed = bool(result.get("status") and result.get("security_rollback_confirmed") is True)
        driver_error = str(result.get("error_code") or "security_rollback_unconfirmed")
        rollback_may_have_been_submitted = (
            bool(result.get("password_submitted"))
            or driver_error in {"security_rollback_unconfirmed", "password_reset_unconfirmed"}
        )
        # Consume the message only after the browser either confirmed the
        # rollback, reached a post-submit state, or proved that Apple consumed
        # the link. A definite pre-click DOM failure leaves the one-time link
        # eligible for a repaired/manual retry with the same escrow password.
        if store and rollback_link and (
            driver_confirmed or rollback_may_have_been_submitted
            or driver_error == "security_rollback_link_expired"
        ):
            store.mark_rollback_seen(account_id, rollback_link.message_id)
        if not driver_confirmed:
            if driver_error == "security_rollback_link_expired":
                raise ControlledRecoveryError("security_rollback_link_expired", stage)
            # A confirmation email is useful only after the driver may have
            # submitted the rollback. Waiting after a pre-click DOM/navigation
            # failure hides the actionable driver error behind a misleading
            # mailbox timeout and needlessly freezes the task for three minutes.
            if not rollback_may_have_been_submitted:
                raise ControlledRecoveryError(driver_error, stage)
            stage = "mailbox_confirmation"
            try:
                await mailbox_client.wait_for_security_rollback_confirmation(
                    mailbox, operation_started, timeout_seconds=mailbox_wait_seconds,
                    expected_account=expected_account,
                )
            except MailRecoveryError as exc:
                raise ControlledRecoveryError(exc.code, stage) from exc
        elif result.get("password") != new_password or result.get("password_change_confirmed") is not True:
            raise ControlledRecoveryError("password_reset_unconfirmed", stage)

        stage = "panel_writeback"
        await panel.report(info["username"], True, "关闭双重认证并更新密码完成", new_password)
        refreshed = await panel.task_info(account_id)
        if refreshed.get("password") != new_password:
            raise ControlledRecoveryError("panel_writeback_unconfirmed", stage)

        stage = "finalize"
        await panel.finalize_recovery(account_id, hashlib.sha256(new_password.encode()).hexdigest())
        final_info = await panel.task_info(account_id)
        if (not bool(final_info.get("enable")) or (not independent and bool(final_info.get("enable_auto_update_password")))
                or bool(final_info.get("enable_delete_devices")) or (not independent and int(final_info.get("check_interval", 0)) != 30)):
            raise ControlledRecoveryError("final_state_unconfirmed", stage)
        escrow_path.unlink()
        return safe_result("success", "complete", job_ids=job_ids, escrow_retained=False, change_path="security_rollback")
    except ControlledRecoveryError as exc:
        if account_username and hasattr(panel, "disable_account"):
            try:
                await panel.disable_account(account_username)
            except Exception:
                pass
        return safe_result("failed", exc.stage, code=exc.code, job_ids=job_ids,
                           escrow_retained=bool(escrow_path and escrow_path.exists()))
    except Exception as exc:
        if account_username and hasattr(panel, "disable_account"):
            try:
                await panel.disable_account(account_username)
            except Exception:
                pass
        return safe_result("failed", stage, code=f"unexpected_{type(exc).__name__}", job_ids=job_ids,
                           escrow_retained=bool(escrow_path and escrow_path.exists()))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Apple enrollment-email security rollback")
    parser.add_argument("--account-id", type=int, required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--mailbox-wait-seconds", type=int, default=180)
    args = parser.parse_args()
    if args.account_id <= 0 or not secrets.compare_digest(args.confirm, CONFIRMATION):
        print(json.dumps(safe_result("failed", "authorization", code="confirmation_required")))
        return 2
    result = asyncio.run(run_controlled_security_rollback(
        args.account_id, mailbox_wait_seconds=max(args.mailbox_wait_seconds, 1),
    ))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
