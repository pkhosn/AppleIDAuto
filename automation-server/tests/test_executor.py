import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.executor import AccountExecutor


class FakeClient:
    def __init__(self, state="normal"):
        self.state = state
        self.actions = []
        self.payloads = []

    async def run(self, action, payload):
        self.actions.append(action)
        self.payloads.append(dict(payload))
        if action == "check":
            return {"status": True, "state": self.state, "message": "checked"}
        if action in {"recover", "update_password"}:
            return {"status": True, "message": action, "password": "NEW_PASSWORD"}
        return {"status": True, "message": action}


class AccountExecutorTest(unittest.TestCase):
    def test_composite_workflow_is_one_driver_call_with_ordered_features(self):
        class WorkflowClient(FakeClient):
            async def run_workflow(self, payload, authorization_probe=None, progress_callback=None):
                self.actions.append("workflow")
                self.payloads.append(dict(payload))
                approvals = [await authorization_probe(feature) for feature in (
                    "profile_restore", "device_cleanup", "password_update",
                )]
                self.approvals = approvals
                return {
                    "status": True, "state": "normal", "password": payload["new_password"],
                    "step_results": {
                        "password_check": {"status": "success"},
                        "profile_restore": {"status": "success", "message": "资料处理完成"},
                        "device_cleanup": {"status": "success", "removed_count": 1},
                        "password_update": {"status": "success"},
                    },
                }

        client = WorkflowClient()
        outcomes, messages = {}, {}
        result = asyncio.run(AccountExecutor(client).run({
            "username": "fixture@example.test", "password": "OLD_PASSWORD", "enable": True,
            "_use_workflow": True, "_run_id": "run-fixture",
            "_due_features": ["password_check", "profile_restore", "device_cleanup", "password_update"],
            "_feature_outcomes": outcomes, "_feature_messages": messages,
            "_prepare_password": lambda: "FixturePass1@",
            "_authorize_feature": AsyncMock(return_value=True),
            "_workflow_progress": AsyncMock(),
            "profile_restore": {"name": "Fixture"},
        }))
        self.assertTrue(result[0])
        self.assertEqual(["workflow"], client.actions)
        self.assertEqual(
            ["password_check", "profile_restore", "device_cleanup", "password_update"],
            client.payloads[0]["features"],
        )
        self.assertEqual([True, True, True], client.approvals)
        self.assertEqual("success", outcomes["device_cleanup"])
        self.assertEqual("FixturePass1@", result[2])

    def test_composite_unknown_disconnect_marks_only_submitted_feature_uncertain(self):
        class WorkflowClient(FakeClient):
            async def run_workflow(self, payload, authorization_probe=None, progress_callback=None):
                return {
                    "status": False, "state": "normal", "error_code": "driver_session_lost",
                    "message": "浏览器会话异常", "workflow_mutation_started": True,
                    "workflow_mutation_feature": "profile_restore", "step_results": {},
                }

        outcomes = {}
        result = asyncio.run(AccountExecutor(WorkflowClient()).run({
            "username": "fixture@example.test", "password": "OLD_PASSWORD", "enable": True,
            "_use_workflow": True, "_run_id": "run-fixture",
            "_due_features": ["profile_restore", "password_update"],
            "_feature_outcomes": outcomes, "_feature_messages": {},
            "_prepare_password": lambda: "FixturePass1@",
            "_authorize_feature": AsyncMock(return_value=True),
            "_workflow_progress": AsyncMock(),
            "profile_restore": {"name": "Fixture"},
        }))
        self.assertFalse(result[0])
        self.assertEqual("uncertain", outcomes["profile_restore"])
        self.assertNotIn("password_update", outcomes)

    def test_device_cleanup_runs_after_login_and_before_password_update(self):
        client = FakeClient("normal")
        result = asyncio.run(AccountExecutor(client).run({
            "username": "fixture@example.test",
            "password": "OLD_PASSWORD",
            "enable": True,
            "enable_auto_update_password": True,
            "enable_delete_devices": True,
        }))
        self.assertTrue(result[0])
        self.assertEqual(["check", "delete_devices", "update_password"], client.actions)
        self.assertIn("账号当前没有可移除设备", result[1])

    def test_device_cleanup_uncertain_result_stops_password_update(self):
        class UncertainClient(FakeClient):
            async def run(self, action, payload):
                self.actions.append(action)
                if action == "check":
                    return {"status": True, "state": "normal", "message": "checked"}
                if action == "delete_devices":
                    return {
                        "status": False, "error_code": "device_removal_unconfirmed",
                        "message": "Apple 未明确确认设备已移除",
                        "device_removal_started": True,
                    }
                return {"status": True, "password": "SHOULD_NOT_RUN"}

        info = {
            "username": "fixture@example.test", "password": "OLD_PASSWORD", "enable": True,
            "enable_delete_devices": True, "enable_auto_update_password": True,
            "_feature_outcomes": {}, "_feature_messages": {},
        }
        client = UncertainClient()
        result = asyncio.run(AccountExecutor(client).run(info))
        self.assertFalse(result[0])
        self.assertEqual("device_removal_unconfirmed", result[3])
        self.assertEqual(["check", "delete_devices"], client.actions)
        self.assertEqual("uncertain", info["_feature_outcomes"]["device_cleanup"])
        self.assertTrue(info["_device_mutation_started"])

    def test_device_transport_failure_only_marks_device_uncertain(self):
        class DisconnectedClient(FakeClient):
            async def run(self, action, payload):
                self.actions.append(action)
                if action == "check":
                    return {"status": True, "state": "normal", "message": "checked"}
                if action == "delete_devices":
                    raise TimeoutError("driver disconnected")
                return {"status": True, "password": "SHOULD_NOT_RUN"}

        info = {
            "username": "fixture@example.test", "password": "OLD_PASSWORD", "enable": True,
            "enable_delete_devices": True, "enable_auto_update_password": True,
            "_feature_outcomes": {}, "_feature_messages": {},
        }
        client = DisconnectedClient()
        result = asyncio.run(AccountExecutor(client).run(info))
        self.assertFalse(result[0])
        self.assertEqual("device_removal_unconfirmed", result[3])
        self.assertEqual(["check", "delete_devices"], client.actions)
        self.assertEqual("uncertain", info["_feature_outcomes"]["device_cleanup"])
        self.assertNotIn("password_update", info["_feature_outcomes"])

    def test_invalid_password_stops_operations(self):
        client = FakeClient("password_invalid")
        result = asyncio.run(AccountExecutor(client).run({
            "username": "fixture@example.test", "password": "BAD_PASSWORD", "enable": True
        }))
        self.assertFalse(result[0])
        self.assertEqual(["check"], client.actions)

    def test_locked_and_two_factor_never_trigger_recovery(self):
        for state in ("locked", "two_factor"):
            client = FakeClient(state)
            result = asyncio.run(AccountExecutor(client).run({
                "username": "fixture@example.test", "password": "PASSWORD", "enable": True,
            }))
            self.assertFalse(result[0])
            self.assertEqual(state, result[3])
            self.assertEqual(["check"], client.actions)

    def test_required_proxy_fails_closed_before_driver_call(self):
        client = FakeClient("normal")
        result = asyncio.run(AccountExecutor(client).run({
            "username": "fixture@example.test", "password": "PASSWORD",
            "enable": True, "proxy_required": True, "proxy_available": False,
        }))
        self.assertEqual("proxy_unavailable", result[3])
        self.assertEqual([], client.actions)

    def test_all_operations_keep_the_same_concrete_proxy(self):
        client = FakeClient("normal")
        result = asyncio.run(AccountExecutor(client).run({
            "username": "fixture@example.test", "password": "OLD_PASSWORD", "enable": True,
            "enable_auto_update_password": True,
            "proxy_required": True, "proxy_available": True, "proxy_id": 9,
            "proxy_protocol": "socks5", "proxy_content": "proxy.test:1080",
        }))
        self.assertTrue(result[0])
        self.assertEqual(["proxy.test:1080", "proxy.test:1080"],
                         [payload["proxy_content"] for payload in client.payloads])

    def test_success_message_names_every_executed_function(self):
        client = ProfileClient(
            profile={"name": "Sam Chen", "country": "中国", "language": "简体中文"},
            restored=True,
        )
        result = asyncio.run(AccountExecutor(client).run({
            "username": "fixture@example.test", "password": "OLD_PASSWORD", "enable": True,
            "check_password_correct": True, "enable_auto_disable_two_factor": True,
            "enable_auto_restore_profile": True,
            "profile_restore": {"name": "Sam Chen"},
            "enable_auto_update_password": True,
        }))
        self.assertTrue(result[0])
        for text in ("账号密码正确性检测成功", "双重认证检测成功",
                     "账号资料恢复成功", "自动更新密码成功"):
            self.assertIn(text, result[1])

    def test_check_retries_only_retryable_technical_errors(self):
        class RetryClient(FakeClient):
            def __init__(self):
                super().__init__("normal")
                self.check_count = 0

            async def run(self, action, payload):
                self.actions.append(action)
                self.payloads.append(dict(payload))
                if action == "check":
                    self.check_count += 1
                    if self.check_count == 1:
                        return {
                            "status": False, "error_code": "page_unready",
                            "message": "Apple 页面加载超时", "retryable": True,
                            "password_submitted": False,
                        }
                    return {"status": True, "state": "normal", "message": "checked"}
                return {"status": True, "message": action}

        client = RetryClient()
        with patch("app.executor.settings", type("Settings", (), {
            "max_retries": 3, "technical_retry_delays": (1, 1),
        })()), patch("app.executor.asyncio.sleep", new=AsyncMock()):
            result = asyncio.run(AccountExecutor(client).run({
                "username": "fixture@example.test", "password": "PASSWORD", "enable": True,
            }))
        self.assertTrue(result[0])
        self.assertEqual(["check", "check"], client.actions)

    def test_check_returns_proxy_error_immediately_for_outer_rotation(self):
        class ProxyFailureClient(FakeClient):
            async def run(self, action, payload):
                self.actions.append(action)
                return {
                    "status": False, "error_code": "proxy_error",
                    "message": "代理连接失败", "retryable": True,
                    "password_submitted": False,
                }

        client = ProxyFailureClient("normal")
        with patch("app.executor.settings", type("Settings", (), {
            "max_retries": 3, "technical_retry_delays": (1, 1),
        })()), patch("app.executor.asyncio.sleep", new=AsyncMock()):
            result = asyncio.run(AccountExecutor(client).run({
                "username": "fixture@example.test", "password": "PASSWORD", "enable": True,
            }))
        self.assertFalse(result[0])
        self.assertEqual("proxy_error", result[3])
        self.assertEqual(["check"], client.actions)

    def test_check_does_not_retry_after_sensitive_submission(self):
        class SubmittedClient(FakeClient):
            async def run(self, action, payload):
                self.actions.append(action)
                return {
                    "status": False, "error_code": "page_unready",
                    "message": "Apple 页面加载超时", "retryable": True,
                    "password_submitted": True,
                }

        client = SubmittedClient()
        with patch("app.executor.settings", type("Settings", (), {
            "max_retries": 3, "technical_retry_delays": (1, 1),
        })()), patch("app.executor.asyncio.sleep", new=AsyncMock()):
            result = asyncio.run(AccountExecutor(client).run({
                "username": "fixture@example.test", "password": "PASSWORD", "enable": True,
            }))
        self.assertFalse(result[0])
        self.assertEqual(["check"], client.actions)

    def test_update_login_network_failure_is_not_marked_as_password_mutation(self):
        class UpdateFailureClient(FakeClient):
            async def run(self, action, payload):
                self.actions.append(action)
                if action == "check":
                    return {"status": True, "state": "normal", "message": "checked"}
                return {
                    "status": False, "error_code": "proxy_error",
                    "message": "密保问题验证请求失败（网络或代理异常）",
                    "stage": "login", "password_submitted": True,
                }

        info = {
            "username": "fixture@example.test", "password": "OLD_PASSWORD",
            "enable": True, "enable_auto_update_password": True,
        }
        result = asyncio.run(AccountExecutor(UpdateFailureClient()).run(info))
        self.assertFalse(result[0])
        self.assertEqual("proxy_error", result[3])
        self.assertFalse(info.get("_mutation_started"))

    def test_update_failure_after_new_password_submission_is_uncertain(self):
        class SubmittedUpdateClient(FakeClient):
            async def run(self, action, payload):
                self.actions.append(action)
                if action == "check":
                    return {"status": True, "state": "normal", "message": "checked"}
                return {
                    "status": False, "error_code": "password_update_unconfirmed",
                    "message": "密码修改结果待确认", "stage": "password_submitted",
                    "password_submitted": True,
                }

        info = {
            "username": "fixture@example.test", "password": "OLD_PASSWORD",
            "enable": True, "enable_auto_update_password": True,
        }
        result = asyncio.run(AccountExecutor(SubmittedUpdateClient()).run(info))
        self.assertFalse(result[0])
        self.assertTrue(info.get("_mutation_started"))


class ProfileClient(FakeClient):
    """check 返回观测资料，模拟驱动已检测/恢复账号资料。"""

    def __init__(self, state="normal", profile=None, restored=False, profile_results=None):
        super().__init__(state)
        self.profile = profile or {}
        self.restored = restored
        self.profile_results = profile_results

    async def run(self, action, payload):
        self.actions.append(action)
        self.payloads.append(dict(payload))
        if action == "check":
            result = {"status": True, "state": self.state, "message": "checked"}
            if self.profile:
                result["profile"] = dict(self.profile)
            if self.restored:
                result["profile_restored"] = True
            if self.profile_results is not None:
                result["profile_results"] = self.profile_results
            return result
        if action in {"recover", "update_password"}:
            return {"status": True, "message": action, "password": "NEW_PASSWORD"}
        return {"status": True, "message": action}


class AccountExecutorProfileTest(unittest.TestCase):
    def _info(self, **overrides):
        info = {
            "username": "fixture@example.test", "password": "PASSWORD", "enable": True,
            "enable_auto_restore_profile": True,
            "profile_restore": {"name": "Sam Chen", "country": "中国", "language": "简体中文"},
        }
        info.update(overrides)
        return info

    def test_profile_restore_sent_to_driver_and_relayed(self):
        client = ProfileClient(profile={"name": "Sam Chen", "country": "中国", "language": "简体中文"}, restored=True)
        result = asyncio.run(AccountExecutor(client).run(self._info()))
        self.assertTrue(result[0])
        check_payload = next(p for p in client.payloads if p.get("username"))
        self.assertTrue(check_payload["profile_restore"]["enabled"])
        self.assertEqual("Sam Chen", check_payload["profile_restore"]["name"])
        self.assertEqual(result[4]["name"], "Sam Chen")
        self.assertTrue(result[4].get("restored"))

    def test_no_profile_when_disabled(self):
        client = ProfileClient(profile={"name": "Sam Chen", "country": "中国", "language": "简体中文"})
        info = self._info(enable_auto_restore_profile=False)
        info.pop("profile_restore", None)
        result = asyncio.run(AccountExecutor(client).run(info))
        self.assertTrue(result[0])
        self.assertNotIn("profile_restore", client.payloads[0])
        self.assertIsNone(result[4])

    def test_profile_passthrough_without_restore_flag(self):
        # 驱动即使没触发改回也回传观测（用于主站记录首次基线与最近观测）。
        client = ProfileClient(profile={"name": "Sam Chen", "country": "中国", "language": "简体中文"})
        result = asyncio.run(AccountExecutor(client).run(self._info()))
        self.assertTrue(result[0])
        self.assertEqual("中国", result[4]["country"])
        self.assertIsNone(result[4].get("restored"))

    def test_restore_failure_is_not_reported_as_no_change(self):
        results = {
            "name": {
                "status": "restore_failed",
                "message": "姓名检测到变化：林零零 → 薛转梅，还原失败",
            },
            "dob": {"status": "unchanged", "message": "出生日期未发生变化"},
            "country": {"status": "unchanged", "message": "国家/地区未发生变化"},
            "language": {"status": "unchanged", "message": "语言未发生变化"},
        }
        client = ProfileClient(
            profile={"name": "林零零", "dob": "1984-12-17", "country": "中国", "language": "简体中文"},
            profile_results=results,
        )
        info = self._info(_feature_outcomes={})
        result = asyncio.run(AccountExecutor(client).run(info))
        self.assertTrue(result[0])
        self.assertIn("姓名检测到变化：林零零 → 薛转梅，还原失败", result[1])
        self.assertNotIn("未发现变化", result[1])
        self.assertEqual("failed", info["_feature_outcomes"]["profile_restore"])


class WorkflowPreflightTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_required_proxy_never_calls_workflow(self):
        class Client:
            async def run_workflow(self, *args, **kwargs):
                raise AssertionError('must not access driver')
        result = await AccountExecutor(Client()).run({'username':'fixture', 'password':'fixture',
            '_use_workflow':True, 'proxy_required':True, 'proxy_available':False})
        self.assertEqual('proxy_unavailable', result[3])

    async def test_unknown_login_state_does_not_hide_submitted_profile_mutation(self):
        class Client:
            async def run_workflow(self, *args, **kwargs):
                return {'status':False, 'error_code':'driver_timeout',
                    'workflow_mutation_started':True, 'workflow_mutation_feature':'profile_restore'}
        info={'username':'fixture', 'password':'fixture', '_use_workflow':True,
              '_due_features':['profile_restore'], '_feature_outcomes':{}}
        result=await AccountExecutor(Client()).run(info)
        self.assertEqual('driver_timeout',result[3])
        self.assertEqual('uncertain',info['_feature_outcomes']['profile_restore'])

    async def test_disabled_account_never_calls_workflow(self):
        class Client:
            async def run_workflow(self, *args, **kwargs):
                raise AssertionError('must not access driver')
        result = await AccountExecutor(Client()).run({'username':'fixture', 'password':'fixture',
            '_use_workflow':True, 'enable':False})
        self.assertEqual('disabled', result[3])


if __name__ == "__main__":
    unittest.main()
