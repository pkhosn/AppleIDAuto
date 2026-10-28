import asyncio
import time

import httpx

from .config import settings
from .telegram_captcha import TelegramCaptchaError, telegram_captcha_broker


class AccountClient:
    """Calls the account-operation driver used by the task orchestrator."""

    async def run(self, action: str, payload: dict) -> dict:
        return await self._run(action, payload)

    async def run_workflow(self, payload: dict, authorization_probe=None, progress_callback=None) -> dict:
        return await self._run(
            "workflow", payload, authorization_probe=authorization_probe,
            progress_callback=progress_callback,
        )

    async def _run(self, action: str, payload: dict, authorization_probe=None, progress_callback=None) -> dict:
        payload = dict(payload)
        telegram = payload.pop("_telegram", {}) or {}
        if settings.account_driver_url == "fixture://local":
            if action == "workflow":
                if progress_callback is not None:
                    now = time.time()
                    await progress_callback({
                        "job_id": "fixture-workflow", "job_status": "running",
                        "stage": "login", "current_feature": "password_check",
                        "queued_at": now, "started_at": now, "heartbeat_at": now,
                        "step_results": {},
                    })
                steps = {}
                for feature in payload.get("features", []):
                    if feature in {"profile_restore", "device_cleanup", "password_update"}:
                        approved = True if authorization_probe is None else await authorization_probe(feature)
                        if not approved:
                            steps[feature] = {"status": "cancelled", "stage": "authorization"}
                            continue
                    steps[feature] = {"status": "success", "stage": "result_confirmation"}
                    if feature == "device_cleanup":
                        steps[feature].update(removed_count=0, no_devices=True)
                result = {
                    "status": True, "state": payload.get("fixture_state", "normal"),
                    "message": "复合自动化任务完成", "step_results": steps,
                    "_job_id": "fixture-workflow", "stage": "completed",
                }
                if "password_update" in payload.get("features", []) and steps.get("password_update", {}).get("status") == "success":
                    result.update(password=payload.get("new_password", payload.get("password", "")),
                                  password_change_confirmed=True)
                return result
            if action == "check":
                return {
                    "status": True,
                    "state": payload.get("fixture_state", "normal"),
                    "message": "账号状态正常",
                }
            if action == "delete_devices":
                return {
                    "status": True, "message": "设备清理流程验证完成", "removed_count": 0,
                    "no_devices": True, "device_removal_confirmed": True,
                    "device_removal_started": False, "_job_id": "fixture-delete_devices",
                    "stage": "device_list", "retryable": False, "password_submitted": False,
                }
            result = {"status": True, "message": f"{action} 操作流程验证完成"}
            if action in {"recover", "update_password", "complete_email_reset"}:
                result["password"] = payload.get("new_password") or payload.get("password", "")
                result["password_change_confirmed"] = True
            if action == "complete_security_rollback":
                result["security_rollback_confirmed"] = True
            return {
                **result, "_job_id": f"fixture-{action}", "stage": "password_submitted",
                "retryable": False, "password_submitted": action in {"recover", "update_password", "complete_email_reset"},
            }

        base_url = settings.account_driver_url.rstrip("/")
        url = f"{base_url}/{action}"
        headers = {"Authorization": f"Bearer {settings.account_driver_token}"}
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not data.get("status") or not data.get("job_id"):
                raise RuntimeError(f"账号驱动 {action} 响应格式错误")
            job_id = data["job_id"]
            queue_deadline = time.monotonic() + settings.account_driver_queue_timeout
            execution_deadline = None
            handled_authorizations = set()
            while time.monotonic() < (execution_deadline or queue_deadline):
                await asyncio.sleep(settings.account_driver_poll_interval)
                response = await client.get(f"{base_url}/jobs/{job_id}", headers=headers)
                if response.status_code == 404:
                    raise RuntimeError("账号驱动任务已丢失")
                response.raise_for_status()
                job = response.json()
                if progress_callback is not None:
                    await progress_callback(job)
                if job.get("job_status") == "running" and execution_deadline is None:
                    execution_deadline = time.monotonic() + settings.account_driver_job_timeout
                request = job.get("authorization_request")
                if isinstance(request, dict):
                    challenge_id = str(request.get("challenge_id") or "")
                    feature = str(request.get("feature") or "")
                    if challenge_id and challenge_id not in handled_authorizations:
                        approved = False if authorization_probe is None else bool(await authorization_probe(feature))
                        decision = await client.post(
                            f"{base_url}/jobs/{job_id}/authorization/{challenge_id}",
                            json={"approved": approved}, headers=headers,
                        )
                        if decision.status_code not in {200, 409}:
                            decision.raise_for_status()
                        handled_authorizations.add(challenge_id)
                    continue
                challenge = job.get("captcha_challenge")
                if isinstance(challenge, dict):
                    challenge_id = str(challenge.get("challenge_id") or "")
                    image_response = await client.get(
                        f"{base_url}/jobs/{job_id}/captcha/{challenge_id}/image", headers=headers,
                    )
                    image_response.raise_for_status()
                    try:
                        answer = await telegram_captcha_broker.solve(
                            str(telegram.get("bot_token") or ""), str(telegram.get("chat_id") or ""),
                            str(telegram.get("account") or ""), job_id, challenge, image_response.content,
                        )
                        submit = await client.post(
                            f"{base_url}/jobs/{job_id}/captcha/{challenge_id}/answer",
                            json={"answer": answer}, headers=headers,
                        )
                        if submit.status_code not in {200, 409}:
                            submit.raise_for_status()
                    except TelegramCaptchaError as exc:
                        code = str(exc)
                        cancel_code = code if code in {
                            "telegram_not_configured", "telegram_send_failed", "telegram_polling_conflict",
                            "captcha_human_timeout",
                        } else "telegram_send_failed"
                        await client.post(
                            f"{base_url}/jobs/{job_id}/captcha/{challenge_id}/cancel",
                            json={"error_code": cancel_code}, headers=headers,
                        )
                    continue
                if job.get("job_status") in {"queued", "running"}:
                    continue
                result = job.get("result")
                if not isinstance(result, dict) or not isinstance(result.get("status"), bool):
                    raise RuntimeError(f"账号驱动 {action} 任务结果无效")
                return {**result, "_job_id": job_id}
        if execution_deadline is None:
            raise TimeoutError(f"账号驱动 {action} 排队超时")
        raise TimeoutError(f"账号驱动 {action} 执行超时")

    async def health(self) -> bool:
        if settings.account_driver_url == "fixture://local":
            return True
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.get(f"{settings.account_driver_url.rstrip('/')}/health")
            response.raise_for_status()
            return response.json().get("status") is True
