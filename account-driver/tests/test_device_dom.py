"""Real DOM regressions. Opt in with DEVICE_DOM_SELENIUM_URL on an idle node."""
import os
import unittest
from unittest.mock import patch
from urllib.parse import quote

from app.browser import AppleFlow


@unittest.skipUnless(os.getenv("DEVICE_DOM_SELENIUM_URL"), "requires an idle Selenium node")
class DeviceDOMTest(unittest.TestCase):
    def test_saved_browser_profile_retains_session_cookie_and_storage(self):
        from dataclasses import replace
        from uuid import uuid4
        from app.config import settings
        import json
        import urllib.request
        from selenium.webdriver.support.ui import WebDriverWait

        def wait_for_slot():
            def idle(_):
                status = json.load(urllib.request.urlopen(os.environ['DEVICE_DOM_SELENIUM_URL'].replace('/wd/hub', '') + '/status', timeout=3))
                return not any(slot.get('session') for node in status['value']['nodes'] for slot in node['slots'])
            WebDriverWait(None, 15).until(idle)

        profile = '/tmp/appleidauto-session-test-' + uuid4().hex
        cfg = replace(settings, selenium_url=os.environ['DEVICE_DOM_SELENIUM_URL'])
        flow = AppleFlow()
        flow._browser_profile = profile
        with patch('app.browser.settings', cfg):
            wait_for_slot()
            driver = flow._new_driver()
            try:
                driver.get('http://localhost:4444/status')
                WebDriverWait(driver, 10).until(lambda d: d.execute_script('return document.readyState') == 'complete')
                driver.add_cookie({'name': 'session_fixture', 'value': 'fixture-session'})
                driver.execute_script("localStorage.setItem('account_fixture', 'fixture@example.test')")
            finally:
                driver.quit()
            wait_for_slot()
            driver = flow._new_driver()
            try:
                driver.get('http://localhost:4444/status')
                WebDriverWait(driver, 10).until(lambda d: d.execute_script('return document.readyState') == 'complete')
                self.assertEqual('fixture-session', driver.get_cookie('session_fixture')['value'])
                self.assertEqual('fixture@example.test', driver.execute_script("return localStorage.getItem('account_fixture')"))
            finally:
                driver.quit()
            flow._browser_profile = profile + '-other'
            wait_for_slot()
            driver = flow._new_driver()
            try:
                driver.get('http://localhost:4444/status')
                WebDriverWait(driver, 10).until(lambda d: d.execute_script('return document.readyState') == 'complete')
                self.assertIsNone(driver.get_cookie('session_fixture'))
                self.assertIsNone(driver.execute_script("return localStorage.getItem('account_fixture')"))
            finally:
                driver.quit()

    def test_unknown_phone_recovery_requires_explicit_identity_offer(self):
        from selenium import webdriver
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        driver = webdriver.Remote(os.environ['DEVICE_DOM_SELENIUM_URL'], options=options)
        try:
            html = """<div id='phone'><h1>确认电话号码</h1>
              <a href='#' onclick="document.getElementById('offer').hidden=false;return false">不认识这个号码？</a>
              <div id='offer' hidden>你可以在验证身份之后关闭双重认证。
                <button onclick="document.getElementById('phone').hidden=true;document.getElementById('birthday').hidden=false">关闭</button></div></div>
              <div id='birthday' hidden><h1>确认你的出生日期</h1><input></div>"""
            driver.get('data:text/html;charset=utf-8,' + quote(html))
            AppleFlow()._begin_unrecognized_phone_recovery(driver)
            self.assertFalse(driver.execute_script("return document.getElementById('birthday').hidden"))
        finally:
            driver.quit()

    def test_chinese_masked_birthday_is_set_as_a_complete_date(self):
        from selenium import webdriver
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        driver = webdriver.Remote(os.environ['DEVICE_DOM_SELENIUM_URL'], options=options)
        try:
            html = """<h1>确认你的出生日期</h1><input id='birthday' type='text'>
              <button onclick="window.observed=document.getElementById('birthday').value;document.getElementById('birthday').hidden=true">继续</button>"""
            driver.get('data:text/html;charset=utf-8,' + quote(html))
            AppleFlow()._fill_recovery_birth_date(driver, '01021970')
            self.assertEqual('1970年01月02日', driver.execute_script('return window.observed'))
        finally:
            driver.quit()

    def test_locked_heading_split_across_dom_nodes_is_recognized(self):
        from selenium import webdriver
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        driver = webdriver.Remote(os.environ['DEVICE_DOM_SELENIUM_URL'], options=options)
        try:
            driver.get('data:text/html;charset=utf-8,' + quote(
                '<h1>出于安全原因，此 Apple 账户已<br>锁定。</h1>'
                '<p>你必须解锁账户，才能登录。</p><button>取消</button><button>解锁账户</button>'))
            state, text = AppleFlow._login_snapshot(driver)
            self.assertIn('锁定', text)
            self.assertEqual('locked', state)
        finally:
            driver.quit()

    def test_email_reset_follows_unlock_forgot_link_without_submitting_current_password(self):
        from selenium import webdriver
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        driver = webdriver.Remote(os.environ["DEVICE_DOM_SELENIUM_URL"], options=options)

        class UnlockPage:
            current_url = 'https://iforgot.apple.com/password/unlock'

            def __getattr__(self, name):
                return getattr(driver, name)

        try:
            for forgot, reset in [('忘记密码？', '重设密码'), ('Forgot password?', 'Reset password')]:
                with self.subTest(language=forgot):
                    driver.get('about:blank')
                    driver.execute_script('''document.body.innerHTML = `
                      <div id="unlock"><input type="password" oninput="window.oldPasswordTouched=true">
                      <button onclick="window.unlockSubmitted=true">继续</button>
                      <a href="#" onclick="event.preventDefault();window.linkClicks=(window.linkClicks||0)+1;
                        document.getElementById('unlock').hidden=true;
                        document.getElementById('reset').hidden=false;">${arguments[0]}</a></div>
                      <div id="reset" hidden>
                        <input id="first" type="password">
                        <input id="second" type="password" oninput="if(!window.cleared){window.cleared=true;first.value='';}">
                        <button disabled id="submit" onclick="window.resetSubmitted=true">${arguments[1]}</button>
                      </div>`;
                      document.addEventListener('input',()=>{submit.disabled=!(first.value && first.value===second.value);});
                    ''', forgot, reset)
                    flow = AppleFlow()
                    def confirm(*args, **kwargs):
                        self.assertTrue(driver.execute_script('return !!window.resetSubmitted'))
                    with patch.object(flow, '_get'), patch.object(flow, '_confirm_password_change', side_effect=confirm):
                        result = flow._complete_email_reset(UnlockPage(), {
                            'reset_url': 'https://iforgot.apple.com/password/unlock',
                            'new_password': 'FixturePass1@',
                        })
                    self.assertTrue(result['password_change_confirmed'])
                    self.assertEqual(1, driver.execute_script('return window.linkClicks'))
                    self.assertFalse(driver.execute_script('return !!window.oldPasswordTouched || !!window.unlockSubmitted'))
        finally:
            driver.quit()

    def test_unresponsive_card_retry_requires_unchanged_unobscured_list(self):
        from selenium import webdriver
        from selenium.webdriver.support.ui import WebDriverWait
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        driver = webdriver.Remote(os.environ["DEVICE_DOM_SELENIUM_URL"], options=options)
        try:
            for scenario in ('ignored', 'overlay', 'changed', 'navigation', 'loading', 'persistent'):
                with self.subTest(scenario=scenario):
                    driver.get("about:blank")
                    driver.execute_script("document.write(arguments[0]); document.close()", '''<main>
                      <button class="device-card" style="width:315px;height:124px" onclick="
                        window.clicks=(window.clicks||0)+1;
                        if(window.clicks===1){
                          if(window.scenario==='overlay') document.getElementById('overlay').hidden=false;
                          if(window.scenario==='changed') this.lastElementChild.textContent='iPad Air';
                          if(window.scenario==='navigation') location.hash='changed';
                        } else if(window.scenario!=='persistent') document.getElementById('detail').hidden=false;">
                        <svg width="30" height="40"></svg><div>Fixture device</div><div>MacBook Pro</div>
                      </button></main>
                      <div id="overlay" hidden style="position:fixed;inset:0;background:white"></div>
                      <div role="dialog" id="detail" hidden><button>Remove from Account</button></div>''')
                    driver.execute_script('window.scenario=arguments[0]', scenario)
                    flow = AppleFlow()
                    cards = flow._device_cards_anywhere(driver)
                    # Shorten only the fixture wait; production retains its full wait.
                    with patch('selenium.webdriver.support.ui.WebDriverWait',
                               side_effect=lambda current, timeout: WebDriverWait(current, 1)), \
                         patch.object(flow, '_has_visible_loading_indicator', return_value=scenario == 'loading'):
                        result = flow._open_device_card_details(driver, cards[0], ('remove from account',))
                    self.assertEqual('ready' if scenario == 'ignored' else 'unopened', result)
                    self.assertEqual(2 if scenario in ('ignored', 'persistent') else 1,
                                     driver.execute_script('return window.clicks'))
        finally:
            driver.quit()

    def test_delayed_detail_content_does_not_reclick_background(self):
        from selenium import webdriver
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        driver = webdriver.Remote(os.environ["DEVICE_DOM_SELENIUM_URL"], options=options)
        try:
            driver.get("data:text/html;charset=utf-8," + quote('''<main>
              <button class="device-card" style="width:315px;height:124px" onclick="
                window.clicks=(window.clicks||0)+1;
                document.getElementById('detail').hidden=false;
                setTimeout(()=>document.getElementById('detail').innerHTML='<button>Remove from Account</button>',7000)">
                <svg width="30" height="40"></svg><div>Fixture laptop</div><div>MacBook Pro</div>
              </button></main><div role="dialog" id="detail" hidden style="width:500px;height:300px"></div>'''))
            flow = AppleFlow()
            cards = flow._device_cards_anywhere(driver)
            self.assertEqual('ready', flow._open_device_card_details(driver, cards[0], ('remove from account',)))
            self.assertEqual(1, driver.execute_script('return window.clicks'))
        finally:
            driver.quit()

    def test_profile_tile_is_not_a_device_and_click_opens_detail(self):
        from selenium import webdriver
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        driver = webdriver.Remote(os.environ["DEVICE_DOM_SELENIUM_URL"], options=options)
        try:
            for label in ("从账户中移除", "Remove from Account"):
                html = '''<main>
                <div style="width:160px;height:120px" id="profile">
                  <svg width="80" height="80"></svg><div>Fixture account</div><div>fixture@example.test</div>
                </div>
                <div id="cards">
                <div class="device-card" style="width:315px;height:124px;cursor:pointer"
                     onclick="document.getElementById('detail').hidden=false">
                  <svg width="30" height="40"></svg><div>Fixture laptop</div><div>MacBook Pro 14</div>
                </div>
                <div class="device-card" style="width:315px;height:124px;cursor:pointer">
                  <svg width="30" height="40"></svg><div>Fixture tablet</div><div>iPad Air</div>
                </div></div>
                <div id="detail" hidden><button>LABEL</button></div></main>'''.replace("LABEL", label)
                driver.get("data:text/html;charset=utf-8," + quote(html))
                flow = AppleFlow()
                cards = flow._device_cards_anywhere(driver)
                self.assertEqual(2, len(cards))
                self.assertTrue(all("device-card" in card.get_attribute("class") for card in cards))
                self.assertEqual("ready", flow._open_device_card_details(driver, cards[0], (label.lower(),)))
        finally:
            driver.quit()

    def test_recovery_password_form_that_keeps_clearing_is_not_submitted(self):
        from selenium import webdriver
        from app.browser import FlowError
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        driver = webdriver.Remote(os.environ["DEVICE_DOM_SELENIUM_URL"], options=options)
        try:
            driver.get("data:text/html;charset=utf-8," + quote('''<input type="password" oninput="this.value=''">
              <input type="password"><button onclick="window.submitted=true">继续</button>'''))
            with self.assertRaises(FlowError) as error:
                AppleFlow()._fill_recovery_passwords(driver, 'FixturePass1@')
            self.assertEqual(error.exception.code, 'password_form_incomplete')
            self.assertFalse(error.exception.password_submitted)
            self.assertFalse(driver.execute_script('return !!window.submitted'))
        finally:
            driver.quit()

    def test_recovery_password_form_repairs_reactive_clear_before_submission(self):
        from selenium import webdriver
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        driver = webdriver.Remote(os.environ["DEVICE_DOM_SELENIUM_URL"], options=options)
        try:
            driver.get("data:text/html;charset=utf-8," + quote('''<input id="first" type="password">
              <input id="second" type="password" oninput="if(!window.cleared){window.cleared=true;document.getElementById('first').value='';}">
              <button disabled id="submit">继续</button>
              <script>document.addEventListener('input',()=>{document.getElementById('submit').disabled=!(first.value && first.value===second.value);});</script>'''))
            flow = AppleFlow()
            flow._fill_recovery_passwords(driver, 'FixturePass1@')
            from selenium.webdriver.common.by import By
            fields = driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
            self.assertTrue(all(field.get_property('value') == 'FixturePass1@' for field in fields))
            self.assertTrue(driver.find_element(By.ID, 'submit').is_enabled())
        finally:
            driver.quit()
