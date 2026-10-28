import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.account_client import AccountClient


class Response:
    def __init__(self, data, status_code=200):
        self.data, self.status_code = data, status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self.data


class Client:
    def __init__(self, **kwargs):
        self.polls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json, headers):
        return Response({"status": True, "job_id": "job-1", "job_status": "queued"}, 202)

    async def get(self, url, headers):
        self.polls += 1
        if self.polls == 1:
            return Response({"status": True, "job_id": "job-1", "job_status": "running"})
        return Response({
            "status": True, "job_id": "job-1", "job_status": "succeeded",
            "result": {"status": True, "state": "normal", "message": "checked"},
        })


class AccountClientTest(unittest.TestCase):
    def test_submit_and_poll(self):
        settings = SimpleNamespace(
            account_driver_url="http://driver:3940", account_driver_token="TOKEN",
            request_timeout=1, account_driver_job_timeout=1, account_driver_queue_timeout=1,
            account_driver_poll_interval=0.001,
        )
        with patch("app.account_client.settings", settings), patch("app.account_client.httpx.AsyncClient", Client):
            result = asyncio.run(AccountClient().run("check", {"username": "fixture@example.test"}))
        self.assertEqual("normal", result["state"])
        self.assertEqual("job-1", result["_job_id"])

    def test_panel_read_calls_use_post_to_match_php_api_middleware(self):
        source = (Path(__file__).parents[1] / "app" / "panel_client.py").read_text(encoding="utf-8")
        self.assertNotIn('client.get(f"{settings.panel_url}/api/', source)


if __name__ == "__main__":
    unittest.main()
