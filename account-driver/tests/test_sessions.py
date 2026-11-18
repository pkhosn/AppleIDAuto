from dataclasses import replace
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from app.browser import AppleFlow, FlowError
from app.config import settings


class ImmediateWait:
    def __init__(self, driver, timeout):
        self.driver = driver

    def until(self, predicate):
        value = predicate(self.driver)
        if not value:
            raise TimeoutError('fixture timeout')
        return value


class BrowserSessionTest(unittest.TestCase):
    def test_profiles_are_isolated_by_account_password_and_proxy(self):
        payload = {'username': 'fixture@example.test', 'password': 'FixturePass1@', 'session_account_id': 1}
        with patch('app.browser.settings', replace(settings, persistent_sessions=True)):
            base = AppleFlow._session_profile('check', payload)
            self.assertEqual(base, AppleFlow._session_profile('delete_devices', dict(payload)))
            for changed in ({'username': 'other@example.test'}, {'session_account_id': 2},
                            {'password': 'ChangedPass1@'}, {'proxy_content': 'proxy.test:1080'}):
                self.assertNotEqual(base, AppleFlow._session_profile('check', {**payload, **changed}))
            self.assertNotIn('fixture', base)
            self.assertNotIn('FixturePass', base)
            self.assertIsNone(AppleFlow._session_profile('check', {**payload, 'force_password_login': True}))
            self.assertIsNone(AppleFlow._session_profile('complete_email_reset', payload))

    def test_session_identity_is_required_before_reuse(self):
        flow = AppleFlow()
        flow._get = Mock()
        flow._login_snapshot = Mock(return_value=('normal', 'Apple 账户 fixture@example.test'))
        with patch('selenium.webdriver.support.ui.WebDriverWait', ImmediateWait):
            self.assertEqual('normal', flow._resume_session(SimpleNamespace(current_url='https://account.apple.com/account/manage'), {'username': 'fixture@example.test'}))
            self.assertTrue(flow._session_reused)
            flow._session_reused = False
            flow._login_snapshot.return_value = ('normal', 'Apple 账户 other@example.test')
            with self.assertRaises(FlowError) as caught:
                flow._resume_session(SimpleNamespace(current_url='https://account.apple.com/account/manage'), {'username': 'fixture@example.test'})
            self.assertEqual('session_identity_unconfirmed', caught.exception.code)
            self.assertFalse(flow._session_reused)

    def test_expired_session_falls_back_without_submitting_credentials(self):
        flow = AppleFlow()
        flow._get = Mock()
        flow._login_snapshot = Mock(return_value=(None, '登录'))
        flow._visible_fields_anywhere = Mock(return_value=[object()])
        with patch('selenium.webdriver.support.ui.WebDriverWait', ImmediateWait):
            self.assertIsNone(flow._resume_session(SimpleNamespace(current_url='https://account.apple.com/account/manage'), {'username': 'fixture@example.test'}))
            self.assertFalse(flow._session_reused)
            flow._login_snapshot.return_value = ('locked', '账户已锁定')
            self.assertEqual('locked', flow._resume_session(SimpleNamespace(current_url='https://account.apple.com/account/manage'), {'username': 'fixture@example.test'}))

    def test_sign_in_returns_saved_session_without_typing_credentials(self):
        flow = AppleFlow()
        flow._browser_profile = '/tmp/profile-fixture'
        flow._resume_session = Mock(return_value='normal')
        flow._type_first = Mock(side_effect=AssertionError('credentials must not be submitted'))
        self.assertEqual('normal', flow._sign_in(object(), {'username': 'fixture', 'password': 'fixture'}))
        flow._type_first.assert_not_called()

    def test_expired_session_runs_existing_login(self):
        flow = AppleFlow()
        flow._browser_profile = '/tmp/profile-fixture'
        flow._resume_session = Mock(return_value=None)
        flow._get = Mock()
        flow._switch_auth_frame = Mock()
        flow._type_first = Mock()
        flow._click_button = Mock()
        flow._login_state = Mock(return_value='normal')
        flow._collect_performance_logs = Mock()
        flow._recover_blank_public_account_page = Mock(return_value='normal')
        self.assertEqual('normal', flow._sign_in(object(), {'username': 'fixture', 'password': 'fixture'}))
        self.assertEqual(2, flow._type_first.call_count)
