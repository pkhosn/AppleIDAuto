import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.browser import (
    AppleFlow, FlowError, classify_login_state, classify_recovery_state,
    classify_security_rollback_page, generate_password,
    match_security_answers, parse_proxy, resolve_proxy,
)
from app.jobs import JobManager


def successful_job(action, payload, job_id):
    return {"status": True, "state": "normal"}


def timed_job(action, payload, job_id):
    started = time.monotonic()
    time.sleep(.3)
    return {"status": True, "started": started, "finished": time.monotonic()}


def slow_job(action, payload, job_id):
    time.sleep(2)
    return {"status": True}


class CoreTest(unittest.TestCase):
    def test_security_rollback_page_classifier_waits_for_explicit_state(self):
        self.assertIsNone(classify_security_rollback_page("Apple 账户"))
        self.assertEqual(
            "confirmation",
            classify_security_rollback_page("确定要关闭双重认证？ 取消 继续"),
        )
        self.assertEqual(
            "action",
            classify_security_rollback_page("Restore previous security settings"),
        )
        self.assertEqual(
            "completed",
            classify_security_rollback_page("Two-factor authentication has been turned off"),
        )
        self.assertEqual("expired", classify_security_rollback_page("此链接已过期"))
        self.assertNotEqual(
            "completed",
            classify_security_rollback_page("Restore previous security settings"),
        )

    def test_fixture_page_states(self):
        self.assertEqual("password_invalid", classify_login_state("Your password is incorrect"))
        self.assertEqual(
            "password_invalid",
            classify_login_state("Check the account information you entered and try again."),
        )
        self.assertEqual("locked", classify_login_state("This account has been locked"))
        self.assertEqual("two_factor", classify_login_state("Enter the verification code sent to your trusted phone"))
        self.assertEqual(
            "two_factor",
            classify_login_state("双重认证 请输入发送到你 iPhone 的验证码。重新发送验证码到 iPhone 无法使用你的 iPhone？"),
        )
        self.assertIsNone(classify_login_state("Apple Account Security uses two-factor authentication"))
        self.assertEqual(
            "security_upgrade_prompt",
            classify_login_state(
                "Apple Account Security. Apple uses two-factor authentication. "
                "When you sign in on any new device or web browser, you will verify your identity. "
                "Other Options Continue"
            ),
        )
        self.assertEqual(
            "security_upgrade_prompt",
            classify_login_state(
                "Apple 账户安全 Apple 使用双重认证来确保只有你可以登录你的 Apple 账户。 "
                "登录新设备或网页浏览器时，你需要使用你的其他设备或电话号码验证你的身份。其他选项 继续"
            ),
        )
        self.assertEqual(
            "security_upgrade_prompt",
            classify_login_state("保护你的账户 双重认证是确保你账户安全的最佳方式。 不升级 升级账户安全"),
        )
        self.assertEqual(
            "two_factor",
            classify_login_state(
                "Apple Account Security. Enter the verification code sent to your trusted phone."
            ),
        )
        self.assertEqual(
            "trusted_phone_prompt",
            classify_login_state("Add a trusted phone number. Back Cancel Continue"),
        )
        self.assertEqual(
            "security_upgrade_prompt",
            classify_login_state("Protect your account. Don't Upgrade Upgrade Account Security"),
        )
        self.assertEqual(
            "security_upgrade_prompt",
            classify_login_state("Protect your account. Two-factor authentication. Loading..."),
        )
        self.assertEqual("security_questions", classify_login_state("Answer your security questions to continue."))
        self.assertEqual("security_questions", classify_login_state("回答你的安全提示问题以继续"))
        self.assertEqual(
            "identity_verification_failed",
            classify_login_state("无法验证你的身份。重试。"),
        )
        self.assertEqual(
            "identity_verification_failed",
            classify_login_state("We cannot verify your identity. Try again."),
        )
        self.assertEqual(
            "security_questions",
            classify_login_state(
                "Your password is incorrect. Answer your security questions to continue."
            ),
        )
        self.assertEqual("normal", classify_login_state("Account", "https://account.apple.com/account/manage"))
        self.assertIsNone(classify_login_state("Sign in"))

    def test_blank_public_account_landing_is_not_a_two_factor_page(self):
        self.assertTrue(AppleFlow._is_blank_public_account_page(
            "Apple 账户\n登录\n创建你的 Apple 账户\n常见问题解答",
            "https://account.apple.com/",
        ))
        self.assertTrue(AppleFlow._is_blank_public_account_page(
            "Apple Account\nSign In\nCreate Your Apple Account",
            "https://account.apple.com/",
        ))
        self.assertFalse(AppleFlow._is_blank_public_account_page(
            "Apple Account\nSign In & Security\nDevices",
            "https://account.apple.com/account/manage",
        ))

    def test_late_security_question_challenge_is_preserved_before_navigation(self):
        browser = Path(__file__).resolve().parents[1] / "app" / "browser.py"
        source = browser.read_text(encoding="utf-8")
        late_check = source.index("late_state, _ = self._login_snapshot(driver)")
        destructive_navigation = source.index("self._get(driver, settings.account_url, password_submitted=True)")
        self.assertLess(late_check, destructive_navigation)

    def test_login_snapshot_includes_shadow_dom_challenge_text(self):
        class Element:
            text = "Apple 账户"

        class Switch:
            def default_content(self):
                pass

        class Driver:
            current_url = "https://account.apple.com/sign-in"
            switch_to = Switch()

            def find_element(self, by, selector):
                return Element()

            def find_elements(self, by, selector):
                return []

            def execute_script(self, script):
                return ""

            def execute_cdp_cmd(self, command, payload):
                if command == "DOM.getDocument":
                    return {"root": {"nodeName": "#document", "shadowRoots": [{
                        "nodeName": "DIV", "children": [{
                            "nodeName": "#text", "nodeValue": "回答你的安全提示问题以继续"
                        }]
                    }]}}
                return {}

        state, text = AppleFlow._login_snapshot(Driver())
        self.assertEqual("security_questions", state)
        self.assertIn("回答你的安全提示问题以继续", text)

    def test_security_questions_exact_and_fuzzy_matching(self):
        payload = {
            "q1": "你少年时代最好的朋友叫什么名字？", "a1": "FIRST",
            "q2": "你的理想工作是什么？", "a2": "SECOND",
            "q3": "你父母是在哪里认识的？", "a3": "THIRD",
        }
        answers = match_security_answers([
            "你的父母是在哪里认识的？", "你少年时代最好的朋友叫什么名字？",
        ], payload)
        self.assertEqual(["THIRD", "FIRST"], answers)

    def test_security_questions_fail_closed(self):
        with self.assertRaises(FlowError) as missing:
            match_security_answers(["问题一", "问题二"], {"q1": "问题一", "a1": "ANSWER"})
        self.assertEqual("security_answers_missing", missing.exception.code)

        ambiguous_payload = {
            "q1": "你父母在哪里认识？", "a1": "FIRST",
            "q2": "你父母在哪里认识？", "a2": "SECOND",
        }
        with self.assertRaises(FlowError) as ambiguous:
            match_security_answers(["你父母在哪里认识？"], ambiguous_payload)
        self.assertEqual("security_question_unmatched", ambiguous.exception.code)
        self.assertTrue(AppleFlow._security_answer_rejected("Your answer is incorrect"))

    def test_security_answer_transport_failure_is_not_answer_rejection(self):
        page = "回答你的安全提示问题以继续 Failed to fetch Failed to fetch"
        self.assertTrue(AppleFlow._security_answer_transport_failed(page))
        self.assertFalse(AppleFlow._security_answer_rejected(page))
        self.assertFalse(AppleFlow._security_answer_transport_failed("答案不正确"))

    def test_generic_answer_label_is_not_used_as_security_question(self):
        class Label:
            text = "Answer 1"

        class Driver:
            def find_elements(self, by, selector):
                return [Label()]

            def execute_script(self, script, field):
                return "你的少年时代最好的朋友叫什么名字？"

        class Field:
            def get_attribute(self, name):
                return "answer-1" if name == "id" else ""

        self.assertEqual(
            "你的少年时代最好的朋友叫什么名字？",
            AppleFlow._question_context(Driver(), Field()),
        )

    def test_visible_question_line_fallback_excludes_reset_link(self):
        lines = AppleFlow._visible_question_lines(
            "Answer your security questions to continue.\n"
            "你的少年时代最好的朋友叫什么名字？\n"
            "Answer 1\n忘记答案？ 重置安全问题\n"
            "你的父母是在哪里认识的？"
        )
        self.assertEqual(
            ["你的少年时代最好的朋友叫什么名字？", "你的父母是在哪里认识的？"],
            lines,
        )

    def test_recovery_blocking_states_are_explicit(self):
        self.assertEqual("locked", classify_recovery_state("This account has been locked"))
        self.assertEqual("two_factor", classify_recovery_state("Use your trusted phone number"))
        # The rollback confirmation page mentions 双重认证 while asking to turn
        # it off; it must not be classified as a new two-factor challenge.
        self.assertIsNone(classify_recovery_state("确定要关闭双重认证？ 取消 继续"))
        self.assertIsNone(classify_recovery_state("不使用双重认证"))
        self.assertEqual(
            "recovery_wait_required",
            classify_recovery_state("Account recovery is in progress and may take a few days"),
        )
        self.assertEqual("recovery_answer_invalid", classify_recovery_state("Your answer is incorrect"))
        self.assertEqual(
            "recovery_identity_failed",
            classify_recovery_state("The birthday you entered is not correct."),
        )
        self.assertEqual("recovery_wait_required", classify_recovery_state("Start account recovery"))
        self.assertEqual("recovery_wait_required", classify_recovery_state("Password Reset Email Sent"))
        self.assertIsNone(classify_recovery_state(
            "Select what information you want to reset. I need to reset my password. "
            "I need to reset my security questions."
        ))
        self.assertIsNone(classify_recovery_state(
            "Select how you want to reset your password: Get an email. Answer security questions."
        ))
        self.assertIsNone(classify_recovery_state("Reset your password"))

    def test_recovery_birthday_selector_supports_current_apple_page(self):
        selector = AppleFlow._recovery_birth_date_selector()
        self.assertIn("placeholder*='birthday'", selector)
        self.assertIn("aria-label*='birthday'", selector)
        self.assertTrue(AppleFlow._is_recovery_birthday_page("Verify your birthday to continue."))
        self.assertFalse(AppleFlow._is_recovery_birthday_page("Reset your password"))

    def test_shadow_field_lookup_is_fail_closed(self):
        class Driver:
            def execute_script(self, script, selector, visible_only):
                self.arguments = (selector, visible_only)
                return ["fixture-field"]

        driver = Driver()
        self.assertEqual(["fixture-field"], AppleFlow._shadow_fields(driver, "input", True))
        self.assertEqual(("input", True), driver.arguments)

    def test_click_anywhere_walks_shadow_hosts_and_accepts_links_when_requested(self):
        class Element:
            text = "继续"
            clicked = False

            def get_attribute(self, name):
                return None

            def is_displayed(self):
                return True

            def is_enabled(self):
                return True

            def click(self):
                self.clicked = True

        class Switch:
            def default_content(self):
                pass

            def parent_frame(self):
                pass

            def frame(self, frame):
                pass

        class Driver:
            switch_to = Switch()

            def __init__(self, element):
                self.element = element
                self.shadow_script = ""
                self.selector = ""

            def find_elements(self, by, selector):
                return []

            def execute_script(self, script, selector):
                self.shadow_script = script
                self.selector = selector
                return [self.element]

        class ImmediateWait:
            def __init__(self, driver, timeout):
                self.driver = driver

            def until(self, predicate):
                result = predicate(self.driver)
                if not result:
                    raise TimeoutError("fixture wait timed out")
                return result

        element = Element()
        driver = Driver(element)
        with patch("selenium.webdriver.support.ui.WebDriverWait", ImmediateWait):
            AppleFlow._click_button_anywhere(driver, ("继续",), include_links=True)

        self.assertTrue(element.clicked)
        self.assertIn("querySelectorAll('*')", driver.shadow_script)
        self.assertIn(", a", driver.selector)

    def test_cdp_finds_and_types_pierced_input(self):
        class Driver:
            def __init__(self):
                self.value = ""

            def execute_cdp_cmd(self, command, payload):
                if command == "DOM.getDocument":
                    return {"root": {"nodeName": "#document", "children": [{
                        "nodeName": "INPUT", "backendNodeId": 7,
                        "attributes": ["type", "text", "aria-label", "birthday"],
                    }]}}
                if command == "DOM.getBoxModel":
                    return {"model": {"content": [0, 0, 100, 0, 100, 30, 0, 30]}}
                if command == "DOM.resolveNode":
                    return {"object": {"objectId": "fixture"}}
                if command == "Runtime.callFunctionOn" and "function(value)" in payload["functionDeclaration"]:
                    self.value = payload["arguments"][0]["object"]["value"]
                    return {"result": {"value": self.value}}
                if command == "Input.insertText":
                    self.value = payload["text"]
                    return {}
                if command == "Runtime.callFunctionOn" and "return String" in payload["functionDeclaration"]:
                    return {"result": {"value": self.value}}
                return {}

        driver = Driver()
        nodes = AppleFlow._cdp_visible_input_nodes(driver)
        self.assertEqual(7, nodes[0]["backend_node_id"])
        self.assertTrue(AppleFlow._type_cdp_input(driver, 7, "01011990"))

    def test_controlled_recovery_password_validation(self):
        self.assertTrue(AppleFlow._valid_new_password("SafePassword1@"))
        self.assertFalse(AppleFlow._valid_new_password("short1@A"))
        self.assertFalse(AppleFlow._valid_new_password("NO_LOWERCASE_123@"))
        self.assertFalse(AppleFlow._valid_new_password("Contains space 1A@"))
        self.assertFalse(AppleFlow._valid_new_password("SafePasssword1@"))

    def test_consecutive_character_policy_error_is_pre_submission(self):
        flow = AppleFlow()
        flow._visible_page_text = lambda driver: "密码强度：100% 连续相同的字符太多"
        with self.assertRaises(FlowError) as failure:
            flow._raise_password_policy_error(object())
        self.assertEqual("password_policy_rejected", failure.exception.code)
        self.assertEqual("password_form", failure.exception.stage)
        self.assertFalse(failure.exception.password_submitted)

    def test_hidden_password_policy_copy_does_not_reject_valid_visible_form(self):
        flow = AppleFlow()
        flow._login_snapshot = lambda driver: (
            "https://iforgot.apple.com/password/reset",
            "连续相同的字符太多",
        )
        flow._visible_page_text = lambda driver: (
            "你的密码必须包含：\n至少 8 个字符\n大写与小写字母\n至少一个数字\n密码强度：强"
        )
        self.assertIsNone(flow._raise_password_policy_error(object()))

    def test_password_change_requires_explicit_success_marker(self):
        self.assertFalse(AppleFlow._has_explicit_password_success(
            "", "", "https://account.apple.com/account/manage"
        ))
        self.assertTrue(AppleFlow._has_explicit_password_success(
            "Your password has been reset successfully"
        ))
        self.assertTrue(AppleFlow._has_explicit_password_success(
            "Password Changed\nYour Apple Account password has been changed."
        ))
        self.assertTrue(AppleFlow._has_explicit_password_success(
            "", "", "https://iforgot.apple.com/password-reset-success"
        ))

    def test_cdp_ax_text_collects_closed_shadow_success_copy(self):
        class Driver:
            def execute_cdp_cmd(self, cmd, params):
                self.cmd = cmd
                return {"nodes": [
                    {"name": {"value": "你的密码已更改。"}},
                    {"role": {"value": "StaticText"}, "name": {}, "value": {"value": "Password changed"}},
                    {"name": {}, "value": {"value": ""}},
                ]}

        text = AppleFlow._cdp_ax_text(Driver())
        self.assertIn("你的密码已更改。", text)
        self.assertIn("Password changed", text)
        self.assertTrue(AppleFlow._has_explicit_password_success(text))

    def test_password_confirmation_uses_accessibility_tree_for_late_modal(self):
        browser = Path(__file__).resolve().parents[1] / "app" / "browser.py"
        source = browser.read_text(encoding="utf-8")
        confirm = source[source.index("def _confirm_password_change"):source.index("def _raise_password_policy_error")]
        self.assertIn("_cdp_ax_text", confirm)
        # The fields disappearing must not raise immediately; it must keep
        # polling so a late success modal can still confirm the change.
        self.assertIn('ax_text = self._cdp_ax_text(d) or ""', confirm)

    def test_password_confirmation_accepts_observed_chinese_success_modal(self):
        class Driver:
            title = "Apple 账户"
            current_url = "https://account.apple.com/account/manage"

        class ImmediateWait:
            def __init__(self, driver, timeout):
                self.driver = driver

            def until(self, condition):
                result = condition(self.driver)
                if not result:
                    raise TimeoutError()
                return result

        flow = AppleFlow()
        with (
            patch.object(flow, '_login_snapshot', return_value=(None, '')),
            patch.object(flow, '_visible_fields_anywhere', return_value=[]),
            patch.object(flow, '_cdp_ax_text', return_value='密码\n你的密码已更改。'),
            patch('selenium.webdriver.support.ui.WebDriverWait', ImmediateWait),
        ):
            self.assertIsNone(flow._confirm_password_change(Driver(), []))

    def test_accessible_success_wins_over_stale_visible_error(self):
        class Driver:
            title = "Apple 账户"
            current_url = "https://account.apple.com/account/manage"

        class ImmediateWait:
            def __init__(self, driver, timeout):
                self.driver = driver

            def until(self, condition):
                return condition(self.driver)

        flow = AppleFlow()
        with (
            patch.object(flow, '_login_snapshot', return_value=(None, 'dormant validation')),
            patch.object(flow, '_cdp_ax_text', return_value='你的密码已更改。'),
            patch.object(flow, '_visible_page_text', return_value='无法更改密码') as visible,
            patch('selenium.webdriver.support.ui.WebDriverWait', ImmediateWait),
        ):
            self.assertIsNone(flow._confirm_password_change(Driver(), []))
            visible.assert_not_called()

    def test_password_confirmation_ignores_hidden_error_while_update_is_loading(self):
        class Driver:
            title = "Apple 账户"
            current_url = "https://account.apple.com/account/manage"

        class SequencedWait:
            def __init__(self, driver, timeout):
                self.driver = driver

            def until(self, condition):
                self.assertion = condition(self.driver)
                if self.assertion:
                    return self.assertion
                return condition(self.driver)

        flow = AppleFlow()
        snapshots = iter(((None, "Unable to change password"), (None, "你的密码已更改。")))
        loading = iter((True, False))
        with (
            patch.object(flow, '_login_snapshot', side_effect=lambda *_: next(snapshots)),
            patch.object(flow, '_has_visible_loading_indicator', side_effect=lambda *_: next(loading)),
            patch.object(flow, '_visible_page_text', return_value='正在更新…'),
            patch('selenium.webdriver.support.ui.WebDriverWait', SequencedWait),
        ):
            self.assertIsNone(flow._confirm_password_change(Driver(), []))

    def test_password_confirmation_rejects_only_painted_validation_error(self):
        class Driver:
            title = "Apple 账户"
            current_url = "https://account.apple.com/account/manage"

        class ImmediateWait:
            def __init__(self, driver, timeout):
                self.driver = driver

            def until(self, condition):
                return condition(self.driver)

        flow = AppleFlow()
        with (
            patch.object(flow, '_login_snapshot', return_value=(None, 'dormant component copy')),
            patch.object(flow, '_has_visible_loading_indicator', return_value=False),
            patch.object(flow, '_visible_page_text', return_value='当前密码不正确'),
            patch('selenium.webdriver.support.ui.WebDriverWait', ImmediateWait),
        ):
            with self.assertRaises(FlowError) as failure:
                flow._confirm_password_change(Driver(), [])
        self.assertEqual('password_update_rejected', failure.exception.code)

    def test_security_rollback_expired_link_is_detected_before_submission(self):
        browser = Path(__file__).resolve().parents[1] / "app" / "browser.py"
        source = browser.read_text(encoding="utf-8")
        self.assertIn('"security_rollback_link_expired"', source)
        self.assertIn("此链接无法再用于完成你的请求", source)

    def test_visible_loading_indicator_defers_stale_password_error(self):
        class Element:
            def __init__(self, visible):
                self.visible = visible

            def is_displayed(self):
                return self.visible

        class Driver:
            def find_elements(self, by, selector):
                return [Element(selector == "[role='progressbar']")]

        self.assertTrue(AppleFlow._has_visible_loading_indicator(Driver()))

    def test_short_apple_shell_is_page_unready_even_if_spinner_has_no_known_class(self):
        flow = AppleFlow()
        flow._login_snapshot = lambda driver: (None, "Apple")
        flow._has_visible_loading_indicator = lambda driver: False
        driver = type("Driver", (), {"current_url": "https://account.apple.com/sign-in"})()
        self.assertTrue(flow._is_page_unready(driver))

    def test_blank_page_connection_reset_is_reported_as_proxy_error(self):
        event = json.dumps({"message": {"method": "Network.loadingFailed", "params": {
            "errorText": "net::ERR_CONNECTION_RESET",
        }}})
        flow = AppleFlow()
        flow._driver_logs = lambda driver, log_type: [{"message": event}]
        driver = type("Driver", (), {})()
        failure = flow._page_unready_error(driver)
        self.assertEqual("proxy_error", failure.code)
        self.assertTrue(failure.retryable)

    def test_proxy_error_is_returned_for_scheduler_rotation_without_same_proxy_retry(self):
        failure = FlowError("proxy_error", "fixture", retryable=True)
        self.assertFalse(AppleFlow._can_retry_navigation("check", 0, 2, failure))
        page_failure = FlowError("page_unready", "fixture", retryable=True)
        self.assertTrue(AppleFlow._can_retry_navigation("check", 0, 2, page_failure))

    def test_hidden_iframe_error_does_not_override_blank_root_page(self):
        class Frame:
            def is_displayed(self):
                return False

            def get_attribute(self, name):
                return ""

        class Switch:
            def default_content(self):
                return None

            def frame(self, frame):
                return None

            def parent_frame(self):
                return None

        class Body:
            text = ""

        class Driver:
            current_url = "https://account.apple.com/"
            switch_to = Switch()

            def find_element(self, by, selector):
                return Body()

            def find_elements(self, by, selector):
                if selector == "iframe":
                    return [Frame()]
                return []

        state, text = AppleFlow._login_snapshot(Driver())
        self.assertIsNone(state)
        self.assertEqual("", text)

    def test_selenium_capacity_distinguishes_ready_busy_and_unavailable(self):
        ready = {"value": {"ready": True, "nodes": [{"availability": "UP", "slots": [{"session": None}]}]}}
        busy = {"value": {"ready": False, "nodes": [{"availability": "UP", "slots": [{"session": {}}]}]}}
        down = {"value": {"ready": False, "nodes": [{"availability": "DOWN", "slots": []}]}}
        self.assertEqual("ready", AppleFlow._selenium_capacity(ready))
        self.assertEqual("busy", AppleFlow._selenium_capacity(busy))
        self.assertEqual("unavailable", AppleFlow._selenium_capacity(down))

    def test_navigation_document_waits_for_new_document(self):
        class Driver:
            current_url = "https://account.apple.com/sign-in"

            def __init__(self):
                self.calls = 0

            def execute_script(self, script):
                self.calls += 1
                return {
                    "ready": "loading", "hasRoot": True, "hasBody": True,
                    "token": "old-token" if self.calls == 1 else "",
                }

        driver = Driver()
        AppleFlow()._wait_for_navigation_document(
            driver, "https://account.apple.com/sign-in", "old-token"
        )
        self.assertEqual(2, driver.calls)

    def test_auth_frame_rejects_visible_tracking_iframe(self):
        class Frame:
            def __init__(self, width, height, hidden=""):
                self.size = {"width": width, "height": height}
                self.hidden = hidden

            def is_displayed(self):
                return True

            def get_attribute(self, name):
                return self.hidden if name == "aria-hidden" else ""

        self.assertFalse(AppleFlow._auth_frame_candidate(Frame(1, 1)))
        self.assertFalse(AppleFlow._auth_frame_candidate(Frame(980, 383, "true")))
        self.assertTrue(AppleFlow._auth_frame_candidate(Frame(980, 383)))

    def test_performance_logs_are_sanitized_and_grouped(self):
        entries = [
            {"message": '{"message":{"method":"Network.loadingFailed","params":{"errorText":"net::ERR_FAILED"}}}'},
            {"message": '{"message":{"method":"Network.loadingFailed","params":{"errorText":"net::ERR_FAILED"}}}'},
            {"message": '{"message":{"method":"Network.responseReceived","params":{"response":{"status":403,"url":"https://idmsa.apple.com/path?secret=value"}}}}'},
        ]
        failures, errors = AppleFlow._summarize_performance_logs(entries)
        self.assertEqual(2, failures["net::ERR_FAILED"])
        self.assertEqual(1, errors["idmsa.apple.com:403"])
        self.assertNotIn("secret", str(errors))

    def test_performance_logs_remain_available_after_destructive_read(self):
        class Driver:
            def __init__(self):
                self.calls = 0

            def get_log(self, log_type):
                self.calls += 1
                if self.calls == 1:
                    return [{
                        "message": '{"message":{"method":"Network.responseReceived","params":'
                                   '{"response":{"status":409,"url":"https://idmsa.apple.com/auth?token=secret"}}}}'
                    }]
                return []

        flow = AppleFlow()
        driver = Driver()
        self.assertTrue(flow._authentication_conflict_seen(driver))
        self.assertTrue(flow._authentication_conflict_seen(driver))
        self.assertEqual(2, driver.calls)
        self.assertNotIn("secret", str(AppleFlow._summarize_performance_logs(
            flow._collect_performance_logs(driver)
        )))

    def test_authentication_conflict_detail_only_keeps_safe_shape(self):
        class Driver:
            def execute_cdp_cmd(self, command, payload):
                self.assertEqual("Network.getResponseBody", command)
                return {"body": '{"errorCode":"AUTH_409","message":"private@example.test","profile":{"phone":"123"}}'}

            def assertEqual(self, left, right):
                CoreTest().assertEqual(left, right)

        entries = [{
            "message": '{"message":{"method":"Network.responseReceived","params":'
                       '{"requestId":"request-1","response":{"status":409,"url":"https://idmsa.apple.com/auth?x=secret"}}}}'
        }]
        detail = AppleFlow()._authentication_conflict_detail(Driver(), entries)
        self.assertTrue(detail["response_body_available"])
        self.assertIn("AUTH_409", detail["codes"])
        self.assertIn("errorCode", detail["keys"])
        self.assertNotIn("private@example.test", str(detail))
        self.assertNotIn("123", str(detail))

    def test_authentication_diagnostics_keep_only_headers_categories_and_cookie_counts(self):
        entries = [
            {"message": '{"message":{"method":"Network.responseReceived","params":{"requestId":"a","response":{"status":409,"url":"https://idmsa.apple.com/auth"}}}}'},
            {"message": '{"message":{"method":"Network.responseReceivedExtraInfo","params":{"requestId":"a","headers":{"X-Apple-ID-Session-Id":"secret","Set-Cookie":"hidden"}}}}'},
        ]
        detail = AppleFlow()._authentication_conflict_detail(type("Driver", (), {
            "execute_cdp_cmd": lambda self, *_: {"body": ""},
        })(), entries)
        self.assertIn("x-apple-id-session-id", detail["response_header_names"])
        self.assertNotIn("secret", str(detail))
        categories = AppleFlow._browser_error_categories([
            {"message": "Access blocked by CORS and third-party cookie policy"},
            {"message": "TypeError: load failed"},
        ])
        self.assertEqual(1, categories["cors"])
        self.assertEqual(1, categories["cookie"])
        self.assertEqual(1, categories["javascript"])

    def test_authentication_conflict_location_uses_only_trusted_apple_url(self):
        class Flow(AppleFlow):
            def _collect_performance_logs(self, driver):
                return driver.entries

        def entry(method, params):
            import json
            return {"message": json.dumps({"message": {"method": method, "params": params}})}

        valid = type("Driver", (), {"entries": [
            entry("Network.responseReceived", {"requestId": "a", "response": {
                "status": 409, "url": "https://idmsa.apple.com/appleauth/auth/signin",
            }}),
            entry("Network.responseReceivedExtraInfo", {"requestId": "a", "headers": {
                "Location": "/appleauth/auth/verify/trusteddevice?opaque=secret",
            }}),
        ]})()
        location = Flow()._authentication_conflict_location(valid)
        self.assertEqual("idmsa.apple.com", __import__("urllib.parse", fromlist=["urlparse"]).urlparse(location).hostname)
        self.assertNotIn("secret", location.split("?")[0])

        untrusted = type("Driver", (), {"entries": [
            entry("Network.responseReceived", {"requestId": "b", "response": {
                "status": 409, "url": "https://idmsa.apple.com/appleauth/auth/signin",
            }}),
            entry("Network.responseReceivedExtraInfo", {"requestId": "b", "headers": {
                "Location": "https://example.test/continue",
            }}),
        ]})()
        self.assertIsNone(Flow()._authentication_conflict_location(untrusted))

    def test_authentication_continuation_diagnostics_exclude_query_parameters(self):
        browser = Path(__file__).resolve().parents[1] / "app" / "browser.py"
        source = browser.read_text(encoding="utf-8")
        self.assertIn('"_apple_flow_continuation_path"', source)
        self.assertIn("authentication-challenge", source)
        self.assertIn('"url_path": continuation_path', source)

    def test_unrendered_authentication_challenge_fails_closed(self):
        browser = Path(__file__).resolve().parents[1] / "app" / "browser.py"
        source = browser.read_text(encoding="utf-8")
        self.assertIn('"apple_authentication_challenge_required"', source)
        self.assertIn('retryable=False, password_submitted=True', source)

    def test_webdriver_transport_timeout_is_not_a_proxy_error(self):
        self.assertTrue(AppleFlow._is_webdriver_timeout(TimeoutError("read timed out")))

    def test_post_login_loading_reopens_home_without_resubmitting_password(self):
        flow = AppleFlow()
        driver = object()
        flow._login_snapshot = lambda current: ("normal", "Account Home")
        flow._get = lambda *args, **kwargs: self.fail("home should not reopen after state settles")
        self.assertEqual("normal", flow._recover_post_login_loading(driver))

    def test_auth_iframe_storage_compatibility_options_are_enabled(self):
        browser = Path(__file__).resolve().parents[1] / "app" / "browser.py"
        source = browser.read_text(encoding="utf-8")
        self.assertIn('"profile.default_content_setting_values.cookies": 1', source)
        self.assertIn('"profile.block_third_party_cookies": False', source)
        self.assertIn("ThirdPartyStoragePartitioning", source)

    def test_short_shell_recovery_is_checked_at_final_login_exit(self):
        browser = Path(__file__).resolve().parents[1] / "app" / "browser.py"
        source = browser.read_text(encoding="utf-8")
        marker = 'if state in {"unknown", "page_unready"} and self._is_page_unready(driver):'
        self.assertIn(marker, source)

    def test_409_conflict_waits_for_late_challenge_before_state_handlers(self):
        browser = Path(__file__).resolve().parents[1] / "app" / "browser.py"
        source = browser.read_text(encoding="utf-8")
        sign_in = source[source.index("def _sign_in"):source.index("def _recover_post_login_loading")]
        # A pending idmsa 409 must trigger the bounded post-login recovery while
        # the page has settled (not unready) so a security-question challenge
        # that materializes after the render deadline is answered.
        conflict_wait = sign_in.index("self._authentication_conflict_seen(driver)")
        security_interstitial = sign_in.index('if state == "security_interstitial":')
        self.assertLess(conflict_wait, security_interstitial)
        self.assertIn("and not self._is_page_unready(driver)", sign_in)
        self.assertIn("state = self._recover_post_login_loading(driver, job_id)", sign_in)

    def test_cdp_document_text_keeps_generous_budget_for_large_spa(self):
        browser = Path(__file__).resolve().parents[1] / "app" / "browser.py"
        source = browser.read_text(encoding="utf-8")
        cdp_text = source[source.index("def _cdp_document_text"):source.index("def _cdp_visible_input_nodes")]
        # Apple SPA pages are large once iframes/Shadow DOM are pierced; a low
        # cap can truncate before the late security-challenge copy.
        self.assertIn("[:300000]", cdp_text)

    def test_security_upgrade_prompt_is_detected_after_answers(self):
        self.assertTrue(AppleFlow._is_security_upgrade_prompt_text(
            "Apple 账户安全 Apple 使用双重认证来确保只有你可以登录你的 Apple 账户。"
            "登录新设备或网页浏览器时，你需要使用你的其他设备或电话号码验证你的身份。其他选项 继续"
        ))
        self.assertTrue(AppleFlow._is_security_upgrade_prompt_text(
            "保护你的账户 双重认证是确保你账户安全的最佳方式。不升级 升级账户安全"
        ))
        self.assertFalse(AppleFlow._is_security_upgrade_prompt_text(
            "回答你的安全提示问题以继续 你的理想工作是什么？答案 1"
        ))

    def test_answer_completion_recognizes_security_upgrade_page(self):
        browser = Path(__file__).resolve().parents[1] / "app" / "browser.py"
        source = browser.read_text(encoding="utf-8")
        answer = source[source.index("def _answer_security_questions"):source.index("def _question_context")]
        # After the answers are submitted, a stale security-question iframe must
        # not hide the security-upgrade page; completion returns that state so
        # the flow can decline the upgrade instead of timing out.
        self.assertIn("_is_security_upgrade_prompt_text(text)", answer)
        self.assertIn('return "security_upgrade_prompt"', answer)
        self.assertIn('self._capture_error_page(d, job_id, "security-answer-rejected")', answer)
        self.assertIn('_security_answer_transport_failed(text)', answer)
        self.assertIn('"proxy_error", "密保问题验证请求失败（网络或代理异常）"', answer)

    def test_post_answer_transitions_ignore_stale_security_questions(self):
        browser = Path(__file__).resolve().parents[1] / "app" / "browser.py"
        source = browser.read_text(encoding="utf-8")
        interstitial = source[source.index("def _continue_security_interstitial"):source.index("def _decline_security_upgrade")]
        self.assertIn('if next_state in {"security_interstitial", "security_questions"}:', interstitial)
        decline = source[source.index("def _decline_security_upgrade"):source.index("def _cancel_trusted_phone_prompt")]
        self.assertIn('if state in {"security_upgrade_prompt", "security_questions"}:', decline)
        cancel = source[source.index("def _cancel_trusted_phone_prompt"):source.index("def _answer_security_questions")]
        self.assertIn('if state in {"trusted_phone_prompt", "security_questions"}:', cancel)

    def test_two_factor_challenge_requires_actionable_code_prompt(self):
        self.assertTrue(AppleFlow._is_two_factor_challenge("输入验证码以继续"))
        self.assertTrue(AppleFlow._is_two_factor_challenge("Enter the verification code sent to your phone"))
        self.assertFalse(AppleFlow._is_two_factor_challenge("账户安全 不使用双重认证"))
        self.assertFalse(AppleFlow._is_two_factor_challenge("Apple Account Security"))

    def test_password_confirmation_ignores_benign_two_factor_copy(self):
        browser = Path(__file__).resolve().parents[1] / "app" / "browser.py"
        source = browser.read_text(encoding="utf-8")
        confirm = source[source.index("def _confirm_password_change"):source.index("def _raise_password_policy_error")]
        self.assertIn('recovery_state == "two_factor" and not self._is_two_factor_challenge(text)', confirm)

    def test_error_pages_are_snapshotted_with_clear_names(self):
        browser = Path(__file__).resolve().parents[1] / "app" / "browser.py"
        source = browser.read_text(encoding="utf-8")
        self.assertIn("_ERROR_STATES = {", source)
        self.assertIn('"identity_verification_failed"', source)
        sign_in = source[source.index("def _sign_in"):source.index("def _recover_post_login_loading")]
        self.assertIn("self._capture_error_page(driver, job_id, state)", sign_in)

    def test_blank_landing_recovery_does_not_refresh_post_password_session(self):
        browser = Path(__file__).resolve().parents[1] / "app" / "browser.py"
        source = browser.read_text(encoding="utf-8")
        start = source.index("def _recover_blank_public_account_page")
        end = source.index("def _continue_security_interstitial", start)
        recovery = source[start:end]
        self.assertNotIn("driver.refresh()", recovery)
        self.assertNotIn("settings.account_url", recovery)
        self.assertIn("max(settings.render_timeout * 3, 90)", recovery)

    def test_stale_captcha_image_is_reacquired(self):
        from io import BytesIO
        from PIL import Image as PillowImage
        from selenium.common.exceptions import StaleElementReferenceException

        stream = BytesIO()
        fixture_image = PillowImage.new("L", (80, 30), color=255)
        for position in range(10, 70, 10):
            fixture_image.putpixel((position, 15), 0)
        fixture_image.save(stream, format="PNG")

        class CaptchaImage:
            def __init__(self, stale=False):
                self.stale = stale

            @property
            def screenshot_as_png(self):
                if self.stale:
                    raise StaleElementReferenceException("fixture refresh")
                return stream.getvalue()

            def click(self):
                return None

        class Field:
            def clear(self):
                return None

            def send_keys(self, value):
                return None

            def get_attribute(self, name):
                return "AB12" if name == "value" else ""

        class Ocr:
            def __init__(self, show_ad=False):
                pass

            def classification(self, value):
                return "AB12"

        class Driver:
            def execute_script(self, script, image):
                return True

            def find_elements(self, by, selector):
                return []

        flow = AppleFlow()
        stale, fresh, field = CaptchaImage(True), CaptchaImage(False), Field()
        items = iter((stale, stale, fresh, field))
        flow._first = lambda *args, **kwargs: next(items, None)
        flow._click_button = lambda *args, **kwargs: None
        with patch("ddddocr.DdddOcr", Ocr):
            flow._submit_recovery_identity(Driver())

    def test_captcha_blank_image_and_stage_metadata_fail_closed(self):
        from io import BytesIO
        from PIL import Image

        stream = BytesIO()
        Image.new("L", (80, 30), color=255).save(stream, format="PNG")
        self.assertFalse(AppleFlow._captcha_has_detail(stream.getvalue()))
        prepared = AppleFlow._prepare_captcha_for_ocr(stream.getvalue())
        self.assertTrue(prepared.startswith(b"\x89PNG"))
        self.assertEqual("AB12", AppleFlow._normalize_captcha_text("a中b-12"))
        flow = AppleFlow()
        with self.assertRaises(FlowError) as failure:
            flow._stage("captcha", lambda: (_ for _ in ()).throw(
                FlowError("captcha_image_unavailable", "fixture")
            ), True)
        self.assertEqual("captcha", failure.exception.stage)
        self.assertTrue(failure.exception.retryable)
        self.assertFalse(failure.exception.password_submitted)

    def test_password_and_proxy(self):
        passwords = [generate_password("Pre@") for _ in range(250)]
        for password in passwords:
            self.assertTrue(password.startswith("Pre@"))
            self.assertRegex(password, r"[A-Z]")
            self.assertRegex(password, r"[a-z]")
            self.assertRegex(password, r"\d")
            self.assertRegex(password, r"[@#%]")
            self.assertNotRegex(password, r"(.)\1\1")
        self.assertEqual("socks5://127.0.0.1:1080", resolve_proxy("socks5", "127.0.0.1:1080"))
        for password in ("pass", "1234"):
            for endpoint in (
                f"proxy.test:8080:user:{password}",
                f"user:{password}:proxy.test:8080",
                f"user:{password}@proxy.test:8080",
                f"proxy.test:8080@user:{password}",
            ):
                with self.subTest(endpoint=endpoint):
                    authenticated = parse_proxy("http", endpoint)
                    self.assertEqual(("user", password, "proxy.test", 8080),
                                     (authenticated.username, authenticated.password,
                                      authenticated.host, authenticated.port))
                    self.assertEqual(f"http://proxy.test:8080#user:{password}", authenticated.pproxy_url)
        with self.assertRaises(FlowError):
            resolve_proxy("http", "bad proxy")
        self.assertTrue(AppleFlow._is_proxy_error(RuntimeError("net::ERR_TUNNEL_CONNECTION_FAILED")))

    def test_job_lifecycle(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                manager = JobManager(successful_job, 3, 60, directory, 7)
                await manager.start()
                try:
                    job = await manager.submit("check", {"username": "fixture@example.test"})
                    await asyncio.wait_for(manager.queue.join(), 8)
                    self.assertEqual("succeeded", manager.get(job.id).status)
                finally:
                    await manager.stop()
        asyncio.run(scenario())

    def test_three_workers_run_concurrently_and_results_expire(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                manager = JobManager(timed_job, 3, 60, directory, 7)
                await manager.start()
                try:
                    jobs = [await manager.submit("check", {}) for _ in range(6)]
                    await asyncio.wait_for(manager.queue.join(), 8)
                    events = sorted([(job.result['started'], 1) for job in jobs] + [(job.result['finished'], -1) for job in jobs])
                    active = maximum = 0
                    for _, delta in events:
                        active += delta
                        maximum = max(maximum, active)
                    self.assertGreater(maximum, 1)
                    self.assertLessEqual(maximum, 3)
                    jobs[0].updated_at = time.time() - 61
                    self.assertIsNone(manager.get(jobs[0].id))
                finally:
                    await manager.stop()
        asyncio.run(scenario())

    def test_old_failure_artifacts_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            stale_png = os.path.join(directory, "job.png")
            stale_json = os.path.join(directory, "job-diagnostics.json")
            fresh_png = os.path.join(directory, "recent.png")
            for path in (stale_png, stale_json):
                open(path, "wb").close()
                os.utime(path, (time.time() - 6 * 86400, time.time() - 6 * 86400))
            open(fresh_png, "wb").close()
            manager = JobManager(successful_job, 1, 60, directory, 5)
            manager._cleanup_artifacts()
            self.assertFalse(os.path.exists(stale_png))
            # 探针/诊断 JSON 必须与截图一同按保留期清理。
            self.assertFalse(os.path.exists(stale_json))
            self.assertTrue(os.path.exists(fresh_png))

    def test_job_timeout_is_reported(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                manager = JobManager(slow_job, 1, 60, directory, 7, 0.01)
                await manager.start()
                try:
                    job = await manager.submit("check", {})
                    await asyncio.wait_for(manager.queue.join(), 8)
                    result = manager.get(job.id)
                    self.assertEqual("failed", result.status)
                    self.assertEqual("driver_timeout", result.result["error_code"])
                finally:
                    await manager.stop()
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
