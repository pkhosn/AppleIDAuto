"""One explicit, fail-closed Apple password reset through an owned IMAPS mailbox."""

import argparse
import asyncio
import hashlib
import json
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Support the documented `python scripts/controlled_email_recovery.py` form
# as well as `python -m scripts.controlled_email_recovery`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.account_client import AccountClient
from app.mail_recovery import BillionMailClient, MailRecoveryError
from app.panel_client import PanelClient
from scripts.controlled_recover import (
    RETRY_DELAYS, ControlledRecoveryError, build_driver_payload, generate_recovery_password,
    load_or_create_escrow, rotate_escrow, run_driver_stage, safe_result,
)


CONFIRMATION = "ONE_TIME_APPLE_EMAIL_PASSWORD_RECOVERY"
DIRECT_CONFIRMATION = "ONE_TIME_DIRECT_EMAIL_RESET_PROBE"


async def run_controlled_email_password_change(
    account_id: int,
    escrow_dir: Path,
    panel=None,
    account_client=None,
    mailbox_client=None,
    password_factory=generate_recovery_password,
    mailbox_wait_seconds: int = 180,
    retry_delays=RETRY_DELAYS,
    sleeper=asyncio.sleep,
    allow_direct: bool = False,
    request_only: bool = False,
    resume_existing_link: bool = False,
    link_max_age_minutes: int = 90,
) -> dict:
    panel = panel or PanelClient()
    account_client = account_client or AccountClient()
    mailbox_client = mailbox_client or BillionMailClient()
    escrow_path = None
    retired_escrow_path = None
    job_ids: list[str] = []
    stage = "preflight"
    account_username = ""
    try:
        info = await panel.task_info(account_id)
        account_username = str(info.get("username") or "")
        independent = bool(info.get('schedule', {}).get('active'))
        if bool(info.get("enable")) or (not independent and bool(info.get("enable_auto_update_password"))) or bool(info.get("enable_delete_devices")):
            raise ControlledRecoveryError("account_not_frozen", stage)
        mailbox = info.get("email_recovery") if isinstance(info.get("email_recovery"), dict) else {}
        if not mailbox.get("configured"):
            raise ControlledRecoveryError(str(mailbox.get("error_code") or "mailbox_config_invalid"), stage)
        direct_mode = str(info.get("connection_mode") or "") == "direct"
        proxy_ready = bool(info.get("proxy_available") and info.get("proxy_id") and info.get("proxy_content"))
        if not direct_mode and not proxy_ready and not allow_direct:
            raise ControlledRecoveryError("proxy_unavailable", stage)

        if resume_existing_link and not list(escrow_dir.glob(f"account-{account_id}-*.secret")):
            raise ControlledRecoveryError("escrow_missing", stage)
        escrow_path, new_password = load_or_create_escrow(escrow_dir, account_id, password_factory)
        payload = build_driver_payload(info)
        if allow_direct:
            # This is deliberately local-CLI-only.  Scheduled/API recovery
            # calls keep their proxy requirement and cannot fall back to the
            # server address.
            for key in ("proxy_id", "proxy_protocol", "proxy_content"):
                payload.pop(key, None)
        payload["new_password"] = new_password
        expected_account = account_username if mailbox.get("account_match_required") else None

        if request_only and resume_existing_link:
            raise ControlledRecoveryError("invalid_recovery_mode", stage)

        # First consume a recent, authenticated Apple reset mail already in
        # the configured mailbox.  Requesting another reset link can revoke a
        # still-valid link, so a new Apple request is the fallback only.
        stage = "mailbox_lookup"
        existing_since = datetime.now(timezone.utc) - timedelta(minutes=max(link_max_age_minutes, 1))
        try:
            reset_link = await mailbox_client.find_link(
                mailbox, "password_reset", existing_since, expected_account=expected_account
            )
        except MailRecoveryError as exc:
            raise ControlledRecoveryError(exc.code, stage) from exc

        if reset_link is None and resume_existing_link:
            raise ControlledRecoveryError("recovery_email_timeout", stage)

        if reset_link is None:
            stage = "email_request"
            requested_at = datetime.now(timezone.utc)
            requested = await run_driver_stage(
                "request_email_reset", payload, account_client, job_ids,
                retry_delays=retry_delays, sleeper=sleeper,
            )
            if not requested.get("status"):
                raise ControlledRecoveryError(str(requested.get("error_code") or "email_reset_request_failed"), stage)
            if request_only:
                # The email request has an external effect, but no password has
                # been submitted or changed.  Keep both account and escrow frozen
                # for an explicitly authorized follow-up.
                return safe_result("pending", "email_requested", job_ids=job_ids,
                                   escrow_retained=bool(escrow_path and escrow_path.exists()),
                                   change_path="email_reset_request")
            stage = "mailbox_wait"
            try:
                reset_link = await mailbox_client.wait_for_link(
                    mailbox, "password_reset", requested_at, timeout_seconds=mailbox_wait_seconds,
                    expected_account=expected_account,
                )
            except MailRecoveryError as exc:
                raise ControlledRecoveryError(exc.code, stage) from exc

        stage = "password_submitted"
        changed = await account_client.run("complete_email_reset", {**payload, "reset_url": reset_link.url})
        changed_job = str(changed.get("_job_id") or "")
        if changed_job:
            job_ids.append(changed_job)
        if (not changed.get("status") and changed.get("error_code") == "password_reused"
                and not changed.get("password_submitted")):
            retired_escrow_path, escrow_path, new_password = rotate_escrow(
                escrow_dir, account_id, escrow_path, password_factory,
            )
            payload["new_password"] = new_password
            changed = await account_client.run("complete_email_reset", {**payload, "reset_url": reset_link.url})
            changed_job = str(changed.get("_job_id") or "")
            if changed_job:
                job_ids.append(changed_job)
        if not changed.get("status"):
            raise ControlledRecoveryError(str(changed.get("error_code") or "password_reset_unconfirmed"), stage)
        if changed.get("password") != new_password or changed.get("password_change_confirmed") is not True:
            raise ControlledRecoveryError("invalid_password_change_result", stage)

        stage = "panel_writeback"
        await panel.report(info["username"], True, "受控邮件密码修改完成", new_password)
        refreshed = await panel.task_info(account_id)
        if refreshed.get("password") != new_password:
            raise ControlledRecoveryError("panel_writeback_unconfirmed", stage)

        stage = "fresh_check"
        verified = await account_client.run("check", build_driver_payload(refreshed))
        verified_job = str(verified.get("_job_id") or "")
        if verified_job:
            job_ids.append(verified_job)
        if not verified.get("status") or verified.get("state") != "normal":
            raise ControlledRecoveryError(str(verified.get("error_code") or verified.get("state") or "check_failed"), stage)

        stage = "finalize"
        await panel.finalize_recovery(account_id, hashlib.sha256(new_password.encode()).hexdigest())
        final_info = await panel.task_info(account_id)
        if (not bool(final_info.get("enable")) or (not independent and bool(final_info.get("enable_auto_update_password")))
                or bool(final_info.get("enable_delete_devices")) or (not independent and int(final_info.get("check_interval", 0)) != 30)):
            raise ControlledRecoveryError("final_state_unconfirmed", stage)
        escrow_path.unlink()
        if retired_escrow_path and retired_escrow_path.exists():
            retired_escrow_path.unlink()
        return safe_result("success", "complete", job_ids=job_ids, escrow_retained=False, change_path="email_reset")
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
    parser = argparse.ArgumentParser(description="Run one fail-closed Apple email password recovery")
    parser.add_argument("--account-id", type=int, required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--escrow-dir", default="/data/recovery-escrow")
    parser.add_argument("--mailbox-wait-seconds", type=int, default=180)
    parser.add_argument("--max-attempts", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--allow-direct", action="store_true",
                        help="仅允许本地一次性受控命令绕过代理；不会影响调度器")
    parser.add_argument("--direct-confirm", default="",
                        help="与 --allow-direct 配合的第二个确认短语")
    parser.add_argument("--request-only", action="store_true",
                        help="仅请求 Apple 重置邮件，不读取邮件或提交新密码")
    parser.add_argument("--resume-existing-link", action="store_true",
                        help="仅使用已经过验证的近期重置邮件链接；绝不再次请求邮件")
    parser.add_argument("--link-max-age-minutes", type=int, default=90)
    args = parser.parse_args()
    if args.account_id <= 0 or not secrets.compare_digest(args.confirm, CONFIRMATION):
        print(json.dumps(safe_result("failed", "authorization", code="confirmation_required")))
        return 2
    if args.allow_direct and not secrets.compare_digest(args.direct_confirm, DIRECT_CONFIRMATION):
        print(json.dumps(safe_result("failed", "authorization", code="direct_confirmation_required")))
        return 2
    if args.request_only and not args.allow_direct:
        print(json.dumps(safe_result("failed", "authorization", code="request_only_requires_direct_confirmation")))
        return 2
    if args.request_only and args.resume_existing_link:
        print(json.dumps(safe_result("failed", "authorization", code="invalid_recovery_mode")))
        return 2
    result = asyncio.run(run_controlled_email_password_change(
        args.account_id, Path(args.escrow_dir), mailbox_wait_seconds=max(args.mailbox_wait_seconds, 1),
        retry_delays=RETRY_DELAYS[:args.max_attempts - 1],
        allow_direct=args.allow_direct, request_only=args.request_only,
        resume_existing_link=args.resume_existing_link,
        link_max_age_minutes=max(args.link_max_age_minutes, 1),
    ))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    # A request-only probe intentionally stops in a safe pending state after
    # Apple confirms the email request; it is not an execution failure.
    return 0 if result["status"] in {"success", "pending"} else 1


if __name__ == "__main__":
    sys.exit(main())
