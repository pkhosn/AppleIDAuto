import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from scripts.controlled_recover import (
    generate_recovery_password, run_controlled_password_change, valid_recovery_password,
)


TEST_PASSWORD = "ControlledPass1@"


def task_info(password="CURRENT_PASSWORD"):
    return {
        "username": "fixture@example.test", "password": password, "dob": "2000-01-02",
        "q1": "Question one?", "a1": "Answer one", "q2": "Question two?", "a2": "Answer two",
        "q3": "Question three?", "a3": "Answer three", "enable": False,
        "enable_auto_update_password": False, "enable_delete_devices": False, "check_interval": 30,
        "proxy_available": True, "proxy_required": True, "proxy_id": 7,
        "proxy_protocol": "socks5", "proxy_content": "proxy.test:1080",
    }


class FakePanel:
    def __init__(self, report_fails=False, finalize_fails=False):
        self.info = task_info()
        self.report_fails = report_fails
        self.finalize_fails = finalize_fails

    async def task_info(self, account_id):
        return dict(self.info)

    async def report(self, username, status, message, password=None):
        if self.report_fails:
            raise RuntimeError("writeback failed")
        self.info["password"] = password

    async def finalize_recovery(self, account_id, password_sha256):
        if self.finalize_fails:
            raise RuntimeError("finalize failed")
        self.info.update({
            "enable": True, "enable_auto_update_password": False,
            "enable_delete_devices": False, "check_interval": 30,
        })

    async def disable_account(self, username):
        self.info.update({
            "enable": False, "enable_auto_update_password": False,
            "enable_delete_devices": False,
        })


class FakeAccountClient:
    def __init__(self, initial_state="normal", fresh_state="normal", change_error="", technical_failures=0):
        self.initial_state = initial_state
        self.fresh_state = fresh_state
        self.change_error = change_error
        self.technical_failures = technical_failures
        self.actions = []
        self.check_count = 0

    async def run(self, action, payload):
        self.actions.append(action)
        job_id = f"{action}-job-{len(self.actions)}"
        if self.technical_failures:
            self.technical_failures -= 1
            return {
                "status": False, "error_code": "page_changed", "retryable": True,
                "password_submitted": False, "stage": "login", "_job_id": job_id,
            }
        if action == "check":
            self.check_count += 1
            state = self.initial_state if self.check_count == 1 else self.fresh_state
            return {"status": True, "state": state, "_job_id": job_id}
        if self.change_error:
            return {
                "status": False, "error_code": self.change_error, "retryable": False,
                "password_submitted": self.change_error == "password_reset_unconfirmed", "_job_id": job_id,
            }
        return {
            "status": True, "password": payload["new_password"],
            "password_change_confirmed": True, "_job_id": job_id,
        }


class ControlledRecoverTest(unittest.TestCase):
    def test_generated_passwords_follow_apple_consecutive_character_rule(self):
        for _ in range(250):
            password = generate_recovery_password()
            self.assertTrue(valid_recovery_password(password))
            self.assertNotRegex(password, r"(.)\1\1")
        self.assertFalse(valid_recovery_password("SafePasssword1@"))

    def run_case(self, panel, client, preexisting=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            if preexisting:
                escrow = path / "account-1-existing.secret"
                escrow.write_text(TEST_PASSWORD, encoding="utf-8")
                os.chmod(escrow, 0o600)
            sleeps = []

            async def no_sleep(delay):
                sleeps.append(delay)

            result = asyncio.run(run_controlled_password_change(
                1, path, panel=panel, account_client=client,
                password_factory=lambda: TEST_PASSWORD, retry_delays=(60, 300), sleeper=no_sleep,
            ))
            files = list(path.glob("*.secret"))
            self.assertNotIn(TEST_PASSWORD, repr(result))
            return result, files, sleeps

    def test_normal_login_uses_update_password_then_enables_checks(self):
        panel = FakePanel()
        client = FakeAccountClient(initial_state="normal")
        result, files, _ = self.run_case(panel, client, preexisting=True)
        self.assertEqual("success", result["status"])
        self.assertEqual("update_password", result["change_path"])
        self.assertEqual(["check", "update_password", "check"], client.actions)
        self.assertEqual([], files)
        self.assertTrue(panel.info["enable"])
        self.assertEqual(TEST_PASSWORD, panel.info["password"])

    def test_password_invalid_uses_recovery(self):
        client = FakeAccountClient(initial_state="password_invalid")
        result, files, _ = self.run_case(FakePanel(), client)
        self.assertEqual("success", result["status"])
        self.assertEqual("recover", result["change_path"])
        self.assertEqual(["check", "recover", "check"], client.actions)
        self.assertEqual([], files)

    def test_recovery_wait_retains_escrow_and_stops(self):
        client = FakeAccountClient(initial_state="password_invalid", change_error="recovery_wait_required")
        result, files, _ = self.run_case(FakePanel(), client)
        self.assertEqual("recovery_wait_required", result["error_code"])
        self.assertEqual(1, len(files))
        self.assertEqual(["check", "recover"], client.actions)

    def test_retryable_technical_errors_retry_three_times_with_delays(self):
        client = FakeAccountClient(initial_state="normal", technical_failures=2)
        result, files, sleeps = self.run_case(FakePanel(), client)
        self.assertEqual("success", result["status"])
        self.assertEqual([60, 300], sleeps)
        self.assertEqual(5, len(client.actions))
        self.assertEqual([], files)

    def test_password_submitted_uncertainty_is_never_retried(self):
        client = FakeAccountClient(initial_state="password_invalid", change_error="password_reset_unconfirmed")
        result, files, sleeps = self.run_case(FakePanel(), client)
        self.assertEqual("password_reset_unconfirmed", result["error_code"])
        self.assertEqual([], sleeps)
        self.assertEqual(["check", "recover"], client.actions)
        self.assertEqual(1, len(files))

    def test_panel_writeback_and_fresh_check_failures_retain_escrow(self):
        result, files, _ = self.run_case(FakePanel(report_fails=True), FakeAccountClient())
        self.assertEqual("panel_writeback", result["stage"])
        self.assertEqual(1, len(files))

        panel = FakePanel()
        result, files, _ = self.run_case(panel, FakeAccountClient(fresh_state="password_invalid"))
        self.assertEqual("fresh_check", result["stage"])
        self.assertEqual("password_invalid", result["error_code"])
        self.assertEqual(1, len(files))
        self.assertFalse(panel.info["enable"])
        self.assertFalse(panel.info["enable_auto_update_password"])

    def test_finalize_failure_and_preflight_failure_are_safe(self):
        result, files, _ = self.run_case(FakePanel(finalize_fails=True), FakeAccountClient())
        self.assertEqual("finalize", result["stage"])
        self.assertEqual(1, len(files))

        panel = FakePanel()
        panel.info["enable"] = True
        result, files, _ = self.run_case(panel, FakeAccountClient())
        self.assertEqual("account_not_frozen", result["error_code"])
        self.assertEqual([], files)


if __name__ == "__main__":
    unittest.main()
