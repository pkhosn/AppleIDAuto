import asyncio
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, manager, health
from app.browser import AppleFlow, FlowError


def successful_api_job(action, payload, job_id):
    return {"status": True, "state": "normal", "message": "fixture checked"}


def authorized_api_job(action, payload, job_id):
    approved = payload['_job_context']['authorize']('device_cleanup')
    payload['_job_context']['progress']('result_confirmation', 'device_cleanup',
        {'status': 'success' if approved else 'cancelled'})
    return {'status': True, 'state': 'normal', 'approved': approved}


class ImmediateWait:
    def __init__(self, driver, timeout):
        self.driver = driver

    def until(self, predicate):
        value = predicate(self.driver)
        if not value:
            raise TimeoutError("fixture wait timed out")
        return value


class PollingWait:
    def __init__(self, driver, timeout):
        self.driver = driver

    def until(self, predicate):
        for _ in range(10):
            value = predicate(self.driver)
            if value:
                return value
        raise TimeoutError("fixture wait timed out")


class ApiTest(unittest.TestCase):
    def test_health_counts_running_jobs_before_browser_session_creation(self):
        from types import SimpleNamespace
        with patch.object(manager, "jobs", {
            "active": SimpleNamespace(status="running"),
            "waiting": SimpleNamespace(status="queued"),
            "finished": SimpleNamespace(status="succeeded"),
        }):
            result = asyncio.run(health())
        self.assertTrue(result["status"])
        self.assertEqual(1, result["running"])

    def test_profile_reconciliation_requires_all_four_fields(self):
        flow = AppleFlow()
        flow._open_profile_page = Mock(return_value=True)
        target = {'name': 'Fixture', 'dob': '2000-01-01', 'country': 'China', 'language': 'English'}
        for observed, expected in [(target, 'confirmed'), ({**target, 'name': 'Changed'}, 'not_applied'),
                                   ({'name': 'Changed'}, 'manual_required'), ({}, 'manual_required')]:
            flow._read_account_profile = Mock(return_value=observed)
            self.assertEqual(expected, flow._reconcile_profile(object(), target)['reconciliation_status'])

    @staticmethod
    def _device_flow(remaining):
        flow = AppleFlow()
        driver = type("Driver", (), {"remaining": remaining})()
        flow._sign_in = Mock(return_value="normal")
        flow._open_devices_page = Mock()
        flow._wait_device_cards_stable = lambda current: [object() for _ in range(current.remaining)]
        flow._click_visible = Mock(return_value=True)
        flow._has_exact_control_anywhere = Mock(return_value=True)
        flow.detail_clicks = 0
        def click_exact(current, labels, include_links=False):
            if "remove from account" in labels:
                flow.detail_clicks += 1
            else:
                current.remaining -= 1
        flow._click_exact_control_anywhere = click_exact
        flow._has_visible_loading_indicator = Mock(return_value=False)
        flow._device_page_ready = Mock(return_value=True)
        flow._device_cards_anywhere = lambda current: [object() for _ in range(current.remaining)]
        return flow, driver

    def test_device_cleanup_empty_list_is_success_without_clicking(self):
        flow, driver = self._device_flow(0)
        result = flow._delete_devices(driver, {"username": "fixture", "password": "secret"})
        self.assertTrue(result["status"])
        self.assertTrue(result["no_devices"])
        self.assertEqual(0, result["removed_count"])
        self.assertEqual(0, flow.detail_clicks)

    def test_device_cleanup_removes_every_card_and_reports_count(self):
        flow, driver = self._device_flow(3)
        with patch("selenium.webdriver.support.ui.WebDriverWait", PollingWait):
            result = flow._delete_devices(driver, {"username": "fixture", "password": "secret"})
        self.assertTrue(result["device_removal_confirmed"])
        self.assertEqual(3, result["removed_count"])
        self.assertFalse(result["no_devices"])
        self.assertEqual(0, driver.remaining)
        self.assertEqual(3, flow.detail_clicks)

    def test_device_cleanup_card_click_without_navigation_is_explicit_failure(self):
        flow, driver = self._device_flow(1)
        flow._has_exact_control_anywhere.return_value = False
        with patch("selenium.webdriver.support.ui.WebDriverWait", ImmediateWait):
            with self.assertRaises(FlowError) as raised:
                flow._delete_devices(driver, {"username": "fixture", "password": "secret"})
        self.assertEqual("device_detail_unopened", raised.exception.code)
        self.assertTrue(raised.exception.retryable)
        self.assertFalse(raised.exception.device_removal_started)

    def test_device_cleanup_open_detail_without_remove_button_is_explicit_failure(self):
        flow, driver = self._device_flow(1)
        flow._has_exact_control_anywhere.return_value = False
        flow._device_cards_anywhere = Mock(return_value=[])
        with patch("selenium.webdriver.support.ui.WebDriverWait", ImmediateWait):
            with self.assertRaises(FlowError) as raised:
                flow._delete_devices(driver, {"username": "fixture", "password": "secret"})
        self.assertEqual("device_remove_button_missing", raised.exception.code)
        self.assertFalse(raised.exception.device_removal_started)

    def test_visible_detail_modal_does_not_reclick_background_cards(self):
        flow, driver = self._device_flow(1)
        flow._device_detail_dialog_visible = Mock(side_effect=[False, True])
        flow._has_exact_control_anywhere.return_value = False
        with patch("selenium.webdriver.support.ui.WebDriverWait", ImmediateWait):
            with self.assertRaises(FlowError) as raised:
                flow._delete_devices(driver, {})
        self.assertEqual('device_remove_button_missing', raised.exception.code)
        self.assertEqual(1, flow._click_visible.call_count)
        self.assertFalse(raised.exception.device_removal_started)

    def test_device_card_click_prefers_actionable_outer_wrapper(self):
        child, outer = Mock(), Mock()
        child.is_displayed.return_value = child.is_enabled.return_value = True
        outer.is_displayed.return_value = outer.is_enabled.return_value = True
        driver = Mock()
        driver.execute_script.return_value = [outer, child]
        self.assertEqual([outer, child], AppleFlow._device_card_click_targets(driver, child))

    def test_device_cleanup_missing_confirmation_is_pre_mutation_failure(self):
        flow, driver = self._device_flow(1)
        flow._has_exact_control_anywhere.side_effect = lambda current, labels, include_links=False: (
            "remove from account" in labels
        )
        with patch("selenium.webdriver.support.ui.WebDriverWait", ImmediateWait):
            with self.assertRaises(FlowError) as raised:
                flow._delete_devices(driver, {"username": "fixture", "password": "secret"})
        self.assertFalse(raised.exception.device_removal_started)

    def test_device_cleanup_unconfirmed_click_is_marked_uncertain(self):
        flow, driver = self._device_flow(1)
        flow._click_exact_control_anywhere = Mock()
        with patch("selenium.webdriver.support.ui.WebDriverWait", PollingWait):
            with self.assertRaises(FlowError) as raised:
                flow._delete_devices(driver, {"username": "fixture", "password": "secret"})
        self.assertEqual("device_removal_unconfirmed", raised.exception.code)
        self.assertTrue(raised.exception.device_removal_started)

    def test_device_cleanup_limit_stops_after_one_hundred_confirmed_removals(self):
        flow, driver = self._device_flow(101)
        with patch("selenium.webdriver.support.ui.WebDriverWait", PollingWait):
            with self.assertRaises(FlowError) as raised:
                flow._delete_devices(driver, {"username": "fixture", "password": "secret"})
        self.assertEqual("device_cleanup_limit", raised.exception.code)
        # Prior removals were confirmed; no final confirmation is unresolved.
        self.assertFalse(raised.exception.device_removal_started)
        self.assertEqual(1, driver.remaining)

    def test_exact_remove_control_never_clicks_do_not_remove(self):
        class Element:
            def __init__(self, text):
                self.text, self.clicked = text, False
            def get_attribute(self, name):
                return ""
            def is_displayed(self):
                return True
            def is_enabled(self):
                return True
            def click(self):
                self.clicked = True

        negative, positive = Element("不移除"), Element("移除")
        switch = type("Switch", (), {
            "default_content": lambda self: None,
            "frame": lambda self, frame: None,
            "parent_frame": lambda self: None,
        })()
        driver = type("Driver", (), {
            "switch_to": switch,
            "find_elements": lambda self, by, selector: [negative, positive] if selector != "iframe" else [],
            "execute_script": lambda self, script, selector: [],
        })()
        self.assertTrue(AppleFlow._exact_control_anywhere(driver, ("移除",), False, True))
        self.assertFalse(negative.clicked)
        self.assertTrue(positive.clicked)

    def test_workflow_logs_in_once_and_keeps_destructive_order(self):
        flow = AppleFlow()
        driver = object()
        calls = []
        flow._sign_in = Mock(side_effect=lambda *args: calls.append("login") or "normal")
        flow._profile_guard = Mock(side_effect=lambda *args, **kwargs: (
            calls.append("profile") or {"profile": {"name": "Fixture"}, "profile_results": {}}
        ))
        flow._delete_devices_authenticated = Mock(side_effect=lambda *args, **kwargs: (
            calls.append("devices") or {"removed_count": 1, "no_devices": False}
        ))
        flow._update_password_authenticated = Mock(side_effect=lambda *args, **kwargs: (
            calls.append("password") or {"password": "FixturePass1@"}
        ))
        authorized = []
        payload = {
            "username": "fixture", "password": "secret", "new_password": "FixturePass1@",
            "features": ["password_update", "device_cleanup", "profile_restore", "password_check"],
            "profile_restore": {"enabled": True, "name": "Fixture"},
            "_job_context": {
                "progress": lambda *args: None,
                "authorize": lambda feature: authorized.append(feature) or True,
            },
        }
        result = flow._workflow(driver, payload, "workflow-fixture")
        self.assertTrue(result["status"])
        self.assertEqual(["login", "profile", "devices", "password"], calls)
        self.assertEqual(["profile_restore", "device_cleanup", "password_update"], authorized)
        flow._sign_in.assert_called_once()

    def test_workflow_never_runs_password_after_device_uncertainty(self):
        flow = AppleFlow()
        flow._sign_in = Mock(return_value="normal")
        flow._delete_devices_authenticated = Mock(side_effect=FlowError(
            "device_removal_unconfirmed", "not confirmed", stage="device_confirmation",
            device_removal_started=True, details={"device_target_hash": "hash", "device_before_count": 2},
        ))
        flow._update_password_authenticated = Mock()
        result = flow._workflow(object(), {
            "username": "fixture", "password": "secret", "new_password": "FixturePass1@",
            "features": ["device_cleanup", "password_update"],
            "_job_context": {"progress": lambda *args: None, "authorize": lambda feature: True},
        }, "workflow-fixture")
        self.assertFalse(result["status"])
        self.assertEqual("uncertain", result["step_results"]["device_cleanup"]["status"])
        self.assertEqual("hash", result["device_target_hash"])
        flow._update_password_authenticated.assert_not_called()

    def test_workflow_reconciliation_is_read_only(self):
        flow = AppleFlow()
        flow._sign_in = Mock(return_value="normal")
        flow._reconcile_devices = Mock(return_value={"reconciliation_status": "confirmed", "current_count": 0})
        flow._delete_devices_authenticated = Mock()
        result = flow._workflow(object(), {
            "username": "fixture", "password": "secret", "features": ["password_check"],
            "reconciliation": {"kind": "device_cleanup", "target_hash": "hash", "before_count": 1},
            "_job_context": {"progress": lambda *args: None, "authorize": lambda feature: True},
        }, "reconcile-fixture")
        self.assertEqual("confirmed", result["reconciliation"]["reconciliation_status"])
        flow._delete_devices_authenticated.assert_not_called()

    def test_device_reconciliation_requires_target_to_disappear(self):
        flow = AppleFlow()
        cards = [object()]
        flow._open_devices_page = Mock()
        flow._wait_device_cards_stable = Mock(return_value=cards)
        flow._device_card_fingerprint = Mock(return_value="target")
        result = flow._reconcile_devices(object(), {"target_hash": "target", "before_count": 2})
        self.assertEqual("not_applied", result["reconciliation_status"])
        self.assertTrue(result["count_changed"])

    def test_device_reconciliation_duplicate_target_needs_manual_review(self):
        flow = AppleFlow()
        flow._open_devices_page = Mock()
        flow._wait_device_cards_stable = Mock(return_value=[object(), object()])
        flow._device_card_fingerprint = Mock(return_value="duplicate")
        result = flow._reconcile_devices(object(), {"target_hash": "duplicate", "before_count": 2})
        self.assertEqual("manual_required", result["reconciliation_status"])

    def test_auth_validation_and_job_polling(self):
        original = manager.runner
        original_artifact_dir = manager.artifact_dir
        manager.runner = successful_api_job
        with tempfile.TemporaryDirectory() as directory:
            try:
                manager.artifact_dir = Path(directory)
                with TestClient(app) as client:
                    self.assertEqual(401, client.post("/check", json={}).status_code)
                    headers = {"Authorization": f"Bearer {settings.token}"}
                    self.assertEqual(422, client.post("/check", headers=headers, json={}).status_code)
                    response = client.post("/check", headers=headers, json={
                        "username": "fixture@example.test", "password": "FIXTURE_PASSWORD"
                    })
                    self.assertEqual(202, response.status_code)
                    job_id = response.json()["job_id"]
                    for _ in range(400):
                        result = client.get(f"/jobs/{job_id}", headers=headers).json()
                        if result["job_status"] == "succeeded":
                            break
                        time.sleep(0.01)
                    self.assertEqual("normal", result["result"]["state"])
                    self.assertEqual(422, client.post("/complete_email_reset", headers=headers, json={}).status_code)
                    response = client.post("/complete_email_reset", headers=headers, json={
                        "reset_url": "https://iforgot.apple.com/reset/fixture", "new_password": "FixturePass1@"
                    })
                    self.assertEqual(202, response.status_code)
                    completion_job = response.json()["job_id"]
                    for _ in range(400):
                        completion = client.get(f"/jobs/{completion_job}", headers=headers).json()
                        if completion["job_status"] == "succeeded":
                            break
                        time.sleep(0.01)
                    self.assertEqual("succeeded", completion["job_status"])

                    manager.runner = authorized_api_job
                    response = client.post("/workflow", headers=headers, json={
                        "username": "fixture@example.test", "password": "FIXTURE_PASSWORD",
                        "run_id": "run-fixture", "features": ["device_cleanup"],
                    })
                    workflow_job_id = response.json()["job_id"]
                    request = None
                    for _ in range(400):
                        workflow_job = client.get(f"/jobs/{workflow_job_id}", headers=headers).json()
                        request = workflow_job.get("authorization_request")
                        if request:
                            break
                        time.sleep(0.01)
                    self.assertIsNotNone(request, workflow_job)
                    decision = client.post(
                        f"/jobs/{workflow_job_id}/authorization/{request['challenge_id']}",
                        headers=headers, json={"approved": True},
                    )
                    self.assertEqual(200, decision.status_code)
                    for _ in range(400):
                        workflow_job = client.get(f"/jobs/{workflow_job_id}", headers=headers).json()
                        if workflow_job["job_status"] == "succeeded":
                            break
                        time.sleep(0.01)
                    self.assertTrue(workflow_job["result"]["approved"])
                    self.assertEqual("success", workflow_job["step_results"]["device_cleanup"]["status"])
                    self.assertIsNotNone(workflow_job["started_at"])
                    self.assertIsNotNone(workflow_job["heartbeat_at"])
                    self.assertEqual(404, client.get("/jobs/missing", headers=headers).status_code)
                    self.assertTrue(client.get("/health").json()["status"])
            finally:
                manager.runner = original
                manager.artifact_dir = original_artifact_dir

    def test_security_rollback_submits_only_explicit_apple_confirmation_once(self):
        flow = AppleFlow()
        driver = type("Driver", (), {"confirmed": False})()
        clicks = []

        flow._get = Mock()
        flow._raise_recovery_block = Mock()
        flow._login_snapshot = lambda current: ("", "双重认证已关闭" if current.confirmed else "确认恢复以前安全设置")

        def explicit_click(current, labels, include_links=False):
            clicks.append((labels, include_links))
            current.confirmed = True

        flow._click_button_anywhere = explicit_click
        with patch("selenium.webdriver.support.ui.WebDriverWait", ImmediateWait):
            result = flow._complete_security_rollback(driver, {
                "reset_url": "https://account.apple.com/rollback/fixture", "new_password": "FixturePass1@"
            })

        self.assertTrue(result["security_rollback_confirmed"])
        self.assertEqual(1, len(clicks))
        self.assertIn("restore previous security settings", clicks[0][0])
        self.assertNotIn("continue", clicks[0][0])

    def test_security_rollback_accepts_expected_post_click_navigation(self):
        from selenium.common.exceptions import StaleElementReferenceException

        flow = AppleFlow()
        driver = type("Driver", (), {"confirmed": False})()
        clicks = []

        flow._get = Mock()
        flow._raise_recovery_block = Mock()
        flow._login_snapshot = lambda current: (
            "", "双重认证已关闭" if current.confirmed else "确认恢复以前安全设置"
        )

        def click_then_navigate(current, labels, include_links=False):
            clicks.append((labels, include_links))
            current.confirmed = True
            raise StaleElementReferenceException("Apple replaced the confirmation page")

        flow._click_button_anywhere = click_then_navigate
        with patch("selenium.webdriver.support.ui.WebDriverWait", ImmediateWait):
            result = flow._complete_security_rollback(driver, {
                "reset_url": "https://account.apple.com/rollback/fixture", "new_password": "FixturePass1@"
            })

        self.assertTrue(result["security_rollback_confirmed"])
        self.assertEqual(1, len(clicks))

    def test_security_rollback_allows_continue_only_on_explicit_turn_off_confirmation(self):
        flow = AppleFlow()
        driver = type("Driver", (), {"confirmed": False})()
        clicks = []

        flow._get = Mock()
        flow._raise_recovery_block = Mock()
        flow._login_snapshot = lambda current: (
            "", "Two-factor authentication has been turned off" if current.confirmed
            else "Are you sure you want to turn off two-factor authentication?"
        )

        def continue_click(current, labels, include_links=False):
            clicks.append((labels, include_links))
            current.confirmed = True

        flow._click_button_anywhere = continue_click
        with patch("selenium.webdriver.support.ui.WebDriverWait", ImmediateWait):
            result = flow._complete_security_rollback(driver, {
                "reset_url": "https://iforgot.apple.com/withdraw/fixture", "new_password": "FixturePass1@"
            })

        self.assertTrue(result["security_rollback_confirmed"])
        self.assertEqual((("continue", "继续"), True), clicks[0])

    def test_security_rollback_chinese_confirmation_allows_link_control(self):
        flow = AppleFlow()
        driver = type("Driver", (), {"confirmed": False})()
        clicks = []

        flow._get = Mock()
        flow._raise_recovery_block = Mock()
        flow._login_snapshot = lambda current: (
            "", "双重认证已关闭" if current.confirmed
            else "确定要关闭双重认证？ 出于安全考虑，除非你无法识别用于设置的电话号码，请勿关闭双重认证。"
        )

        def continue_link(current, labels, include_links=False):
            clicks.append((labels, include_links))
            current.confirmed = True

        flow._click_button_anywhere = continue_link
        with patch("selenium.webdriver.support.ui.WebDriverWait", ImmediateWait):
            result = flow._complete_security_rollback(driver, {
                "reset_url": "https://iforgot.apple.com/withdraw/fixture", "new_password": "FixturePass1@"
            })

        self.assertTrue(result["security_rollback_confirmed"])
        self.assertEqual([(("continue", "继续"), True)], clicks)

    def test_security_rollback_waits_for_late_spa_confirmation(self):
        flow = AppleFlow()
        driver = type("Driver", (), {"confirmed": False, "snapshots": 0})()
        clicks = []

        flow._get = Mock()
        flow._raise_recovery_block = Mock()

        def snapshot(current):
            current.snapshots += 1
            if current.confirmed:
                return "", "双重认证已关闭"
            if current.snapshots < 4:
                return "", "Apple 账户"
            return "", "确定要关闭双重认证？ 取消 继续"

        flow._login_snapshot = snapshot

        def continue_link(current, labels, include_links=False):
            clicks.append((labels, include_links))
            current.confirmed = True

        flow._click_button_anywhere = continue_link
        with patch("selenium.webdriver.support.ui.WebDriverWait", PollingWait):
            result = flow._complete_security_rollback(driver, {
                "reset_url": "https://iforgot.apple.com/withdraw/fixture", "new_password": "FixturePass1@"
            })

        self.assertTrue(result["security_rollback_confirmed"])
        self.assertGreaterEqual(driver.snapshots, 4)
        self.assertEqual([(("continue", "继续"), True)], clicks)

    def test_security_rollback_confirmation_matches_line_wrapped_apple_heading(self):
        flow = AppleFlow()
        driver = type("Driver", (), {"confirmed": False})()
        flow._get = Mock()
        flow._raise_recovery_block = Mock()
        flow._login_snapshot = lambda current: (
            "", "Two-factor authentication has been turned off" if current.confirmed
            else "Are you sure you want to turn off\ntwo‑factor authentication?"
        )
        flow._click_button_anywhere = lambda current, labels, include_links=False: setattr(current, "confirmed", True)
        with patch("selenium.webdriver.support.ui.WebDriverWait", ImmediateWait):
            result = flow._complete_security_rollback(driver, {
                "reset_url": "https://iforgot.apple.com/withdraw/fixture", "new_password": "FixturePass1@"
            })
        self.assertTrue(result["security_rollback_confirmed"])


if __name__ == "__main__":
    unittest.main()
