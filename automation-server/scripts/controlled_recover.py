import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import stat
import string
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.account_client import AccountClient
from app.executor import AccountExecutor
from app.panel_client import PanelClient


CONFIRMATION = "ONE_TIME_APPLE_RECOVERY"
RETRY_DELAYS = (60, 300)


class ControlledRecoveryError(RuntimeError):
    def __init__(self, code: str, stage: str):
        super().__init__(code)
        self.code = code
        self.stage = stage


def generate_recovery_password() -> str:
    alphabet = string.ascii_letters + string.digits + "@#%"
    while True:
        required = [
            secrets.choice(string.ascii_uppercase), secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits), secrets.choice("@#%"),
        ]
        suffix = required + [secrets.choice(alphabet) for _ in range(12)]
        secrets.SystemRandom().shuffle(suffix)
        candidate = "".join(suffix)
        if not re.search(r"(.)\1\1", candidate):
            return candidate


def valid_recovery_password(password: str) -> bool:
    return (
        12 <= len(password) <= 64
        and any(character.isupper() for character in password)
        and any(character.islower() for character in password)
        and any(character.isdigit() for character in password)
        and any(not character.isalnum() for character in password)
        and not any(character.isspace() for character in password)
        and not bool(re.search(r"(.)\1\1", password))
    )


def create_escrow(directory: Path, account_id: int, password: str) -> Path:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"account-{account_id}-{timestamp}-{secrets.token_hex(4)}.secret"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(password)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    return path


def load_or_create_escrow(directory: Path, account_id: int, password_factory) -> tuple[Path, str]:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    files = sorted(directory.glob(f"account-{account_id}-*.secret"))
    if len(files) > 1:
        raise ControlledRecoveryError("multiple_escrow_files", "preflight")
    if files:
        path = files[0]
        if stat.S_IMODE(path.stat().st_mode) != 0o600 or not path.is_file():
            raise ControlledRecoveryError("invalid_escrow_permissions", "preflight")
        password = path.read_text(encoding="utf-8")
    else:
        password = password_factory()
        path = create_escrow(directory, account_id, password)
    if not valid_recovery_password(password):
        raise ControlledRecoveryError("invalid_escrow_password", "preflight")
    return path, password


def rotate_escrow(directory: Path, account_id: int, path: Path, password_factory) -> tuple[Path, Path, str]:
    """Retire a pre-submit rejected candidate and create one replacement."""
    if not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ControlledRecoveryError("invalid_escrow_permissions", "password_form")
    retired = path.with_suffix(".rejected")
    if retired.exists():
        raise ControlledRecoveryError("rejected_escrow_exists", "password_form")
    path.replace(retired)
    new_path, password = load_or_create_escrow(directory, account_id, password_factory)
    return retired, new_path, password


def safe_result(status: str, stage: str, *, code: str = "", job_ids: list[str] | None = None,
                escrow_retained: bool = False, change_path: str = "") -> dict:
    result = {
        "status": status,
        "stage": stage,
        "escrow_retained": escrow_retained,
        "job_ids": list(job_ids or []),
    }
    if code:
        result["error_code"] = code
    if change_path:
        result["change_path"] = change_path
    return result


def build_driver_payload(info: dict) -> dict:
    """Build a driver payload that keeps the Telegram CAPTCHA fallback wired.

    The panel's task info carries the owner's Telegram bot credentials.  The
    driver surfaces CAPTCHA challenges through the job API, and AccountClient
    routes those to Telegram for a human answer; without ``_telegram`` every
    CAPTCHA step fails as ``telegram_not_configured`` even when the owner has
    configured a bot.
    """
    payload = AccountExecutor._payload(info)
    payload["_telegram"] = {
        "bot_token": str(info.get("tg_bot_token") or ""),
        "chat_id": str(info.get("tg_chat_id") or ""),
        "account": str(info.get("username") or ""),
    }
    return payload


async def run_driver_stage(action: str, payload: dict, account_client, job_ids: list[str],
                           retry_delays=RETRY_DELAYS, sleeper=asyncio.sleep) -> dict:
    attempts = len(retry_delays) + 1
    result = {}
    for attempt in range(attempts):
        result = await account_client.run(action, payload)
        job_id = str(result.get("_job_id", ""))
        if job_id:
            job_ids.append(job_id)
        if result.get("status"):
            return result
        if result.get("password_submitted") or not result.get("retryable") or attempt + 1 >= attempts:
            return result
        await sleeper(retry_delays[attempt])
    return result


async def run_controlled_password_change(
    account_id: int,
    escrow_dir: Path,
    panel=None,
    account_client=None,
    password_factory=generate_recovery_password,
    retry_delays=RETRY_DELAYS,
    sleeper=asyncio.sleep,
) -> dict:
    panel = panel or PanelClient()
    account_client = account_client or AccountClient()
    escrow_path = None
    job_ids = []
    stage = "preflight"
    account_username = ""
    try:
        info = await panel.task_info(account_id)
        account_username = str(info.get("username") or "")
        independent = bool(info.get('schedule', {}).get('active'))
        if bool(info.get("enable")) or (not independent and bool(info.get("enable_auto_update_password"))) or bool(info.get("enable_delete_devices")):
            raise ControlledRecoveryError("account_not_frozen", stage)
        required = ("username", "password", "dob", "q1", "a1", "q2", "a2", "q3", "a3")
        if any(not info.get(field) for field in required):
            raise ControlledRecoveryError("recovery_identity_failed", stage)
        if not info.get("proxy_available") or not info.get("proxy_id") or not info.get("proxy_content"):
            raise ControlledRecoveryError("proxy_unavailable", stage)

        escrow_path, new_password = load_or_create_escrow(escrow_dir, account_id, password_factory)
        payload = build_driver_payload(info)
        payload["new_password"] = new_password

        stage = "initial_check"
        checked = await run_driver_stage(
            "check", payload, account_client, job_ids, retry_delays=retry_delays, sleeper=sleeper,
        )
        if not checked.get("status"):
            raise ControlledRecoveryError(str(checked.get("error_code") or "check_failed"), stage)
        state = str(checked.get("state") or "unknown")
        if state == "normal":
            action = "update_password"
        elif state == "password_invalid":
            action = "recover"
        else:
            raise ControlledRecoveryError(state, stage)

        stage = action
        changed = await run_driver_stage(
            action, payload, account_client, job_ids, retry_delays=retry_delays, sleeper=sleeper,
        )
        if not changed.get("status"):
            raise ControlledRecoveryError(str(changed.get("error_code") or f"{action}_failed"), stage)
        if changed.get("password") != new_password or changed.get("password_change_confirmed") is not True:
            raise ControlledRecoveryError("invalid_password_change_result", stage)

        stage = "panel_writeback"
        await panel.report(info["username"], True, "受控密码修改完成", new_password)
        refreshed = await panel.task_info(account_id)
        if refreshed.get("password") != new_password:
            raise ControlledRecoveryError("panel_writeback_unconfirmed", stage)

        stage = "fresh_check"
        check_payload = build_driver_payload(refreshed)
        verified = await run_driver_stage(
            "check", check_payload, account_client, job_ids, retry_delays=retry_delays, sleeper=sleeper,
        )
        if not verified.get("status") or verified.get("state") != "normal":
            raise ControlledRecoveryError(
                str(verified.get("error_code") or verified.get("state") or "check_failed"), stage,
            )

        stage = "finalize"
        await panel.finalize_recovery(account_id, hashlib.sha256(new_password.encode()).hexdigest())
        final_info = await panel.task_info(account_id)
        if (
            not bool(final_info.get("enable"))
            or (not independent and bool(final_info.get("enable_auto_update_password")))
            or bool(final_info.get("enable_delete_devices"))
            or (not independent and int(final_info.get("check_interval", 0)) != 30)
        ):
            raise ControlledRecoveryError("final_state_unconfirmed", stage)

        escrow_path.unlink()
        return safe_result("success", "complete", job_ids=job_ids, escrow_retained=False, change_path=action)
    except ControlledRecoveryError as exc:
        if account_username and hasattr(panel, "disable_account"):
            try:
                await panel.disable_account(account_username)
            except Exception:
                pass
        return safe_result(
            "failed", exc.stage, code=exc.code, job_ids=job_ids,
            escrow_retained=bool(escrow_path and escrow_path.exists()),
        )
    except Exception as exc:
        if account_username and hasattr(panel, "disable_account"):
            try:
                await panel.disable_account(account_username)
            except Exception:
                pass
        return safe_result(
            "failed", stage, code=f"unexpected_{type(exc).__name__}", job_ids=job_ids,
            escrow_retained=bool(escrow_path and escrow_path.exists()),
        )


run_controlled_recovery = run_controlled_password_change


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one fail-closed Apple password change")
    parser.add_argument("--account-id", type=int, required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--escrow-dir", default="/data/recovery-escrow")
    parser.add_argument("--max-attempts", type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    if args.account_id <= 0 or not secrets.compare_digest(args.confirm, CONFIRMATION):
        print(json.dumps(safe_result("failed", "authorization", code="confirmation_required")))
        return 2
    result = asyncio.run(run_controlled_password_change(
        args.account_id, Path(args.escrow_dir), retry_delays=RETRY_DELAYS[:args.max_attempts - 1],
    ))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
