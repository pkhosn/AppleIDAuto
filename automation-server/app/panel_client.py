import json

import httpx
from .config import settings


class PanelClient:
    @staticmethod
    def _headers() -> dict:
        return {"key": settings.panel_api_key}

    async def task_list(self) -> list[int]:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.post(f"{settings.panel_url}/api/get_task_list",
                                         headers=self._headers())
            response.raise_for_status()
            data = response.json()
            if data.get("status") is not True or not isinstance(data.get("data"), list):
                raise RuntimeError(data.get("msg", "管理站任务列表无效"))
            return [int(account_id) for account_id in data["data"]]

    async def task_info(self, account_id: int) -> dict:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.post(f"{settings.panel_url}/api/get_task_info", data={"id": account_id},
                                         headers=self._headers())
            response.raise_for_status()
            data = response.json()
            if data.get("status") is not True or not isinstance(data.get("data"), dict):
                raise RuntimeError(data.get("msg", "管理站返回数据无效"))
            return data["data"]

    async def automation_schedules(self) -> list[dict]:
        return await self._get_list('get_automation_schedules')

    async def report_schedule_states(self, rows):
        states = [{'account_id': row['account_id'], 'status': row['status'], 'next_run_at': row['next_run_at']} for row in rows]
        for start in range(0, len(states), 500):
            await self._post('update_automation_states', {'states': json.dumps(states[start:start + 500])})

    async def mailbox_config(self, account_id: int) -> dict:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.post(f"{settings.panel_url}/api/get_mailbox_config", data={"id": account_id},
                                         headers=self._headers())
            response.raise_for_status()
            data = response.json()
            if data.get("status") is not True or not isinstance(data.get("data"), dict):
                raise RuntimeError(data.get("msg", "管理站救援邮箱配置无效"))
            return data["data"]

    async def report(self, username: str, status: bool, message: str, password: str | None = None,
                     profile: dict | None = None):
        payload = {
            "username": username, "status": "True" if status else "False", "message": message
        }
        if password:
            payload["password"] = password
        if profile:
            payload["profile"] = json.dumps(profile, ensure_ascii=False)
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.post(f"{settings.panel_url}/api/update_account", data=payload,
                                         headers=self._headers())
            response.raise_for_status()
            data = response.json()
            if data.get("status") is not True:
                raise RuntimeError(data.get("msg", "管理站回报失败"))

    async def disable_account(self, username: str):
        """Freeze an account after an uncertain or terminal Apple result."""
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.post(f"{settings.panel_url}/api/disable_account",
                                         data={"username": username}, headers=self._headers())
            response.raise_for_status()
            data = response.json()
            if data.get("status") is not True:
                raise RuntimeError(data.get("msg", "管理站冻结账号失败"))

    async def finalize_recovery(self, account_id: int, password_sha256: str):
        await self._post("finalize_recovery", {
            "id": account_id,
            "password_sha256": password_sha256,
        })

    async def reconcile_confirmed_recovery(self, account_id: int, password: str, password_sha256: str):
        """Sync an Apple-confirmed password without enabling any scheduler task."""
        await self._post("reconcile_confirmed_recovery", {
            "id": account_id,
            "password": password,
            "password_sha256": password_sha256,
        })

    async def report_proxy_error(self, proxy_id: int):
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.post(f"{settings.panel_url}/api/report_proxy_error",
                                         data={"id": proxy_id}, headers=self._headers())
            response.raise_for_status()

    async def report_proxy_success(self, proxy_id: int):
        await self.update_proxy_health(proxy_id, True)

    async def proxy_sources(self) -> list[dict]:
        return await self._get_list("get_proxy_sources")

    async def sync_proxy_source(self, source_id: int, endpoints: list[str], error_code: str = ""):
        payload = {"source_id": source_id, "endpoints": json.dumps(endpoints), "error_code": error_code}
        await self._post("sync_proxy_source", payload)

    async def proxy_health_tasks(self, limit: int = 50) -> list[dict]:
        return await self._get_list("get_proxy_health_tasks", {"limit": limit})

    async def proxy_health_target(self, proxy_id: int) -> dict:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.post(f"{settings.panel_url}/api/get_proxy_health_target",
                                         data={"id": proxy_id}, headers=self._headers())
            response.raise_for_status()
            data = response.json()
            if data.get("status") is not True or not isinstance(data.get("data"), dict):
                raise RuntimeError(data.get("msg", "静态 IP 不存在"))
            return data["data"]

    async def update_proxy_health(self, proxy_id: int, success: bool, exit_ip: str = "",
                                  latency_ms: int | None = None, error_code: str = ""):
        payload = {
            "proxy_id": proxy_id, "success": success, "exit_ip": exit_ip,
            "error_code": error_code,
        }
        if latency_ms is not None:
            payload["latency_ms"] = latency_ms
        await self._post("update_proxy_health", payload)

    async def _get_list(self, action: str, params: dict | None = None) -> list[dict]:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.post(f"{settings.panel_url}/api/{action}", data=params,
                                         headers=self._headers())
            response.raise_for_status()
            data = response.json()
            if data.get("status") is not True or not isinstance(data.get("data"), list):
                raise RuntimeError(f"管理站 {action} 响应无效")
            return data["data"]

    async def _post(self, action: str, payload: dict):
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.post(f"{settings.panel_url}/api/{action}", data=payload,
                                         headers=self._headers())
            response.raise_for_status()
            data = response.json()
            if data.get("status") is not True:
                raise RuntimeError(f"管理站 {action} 更新失败")
