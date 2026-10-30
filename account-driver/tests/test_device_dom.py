"""Real DOM regressions. Opt in with DEVICE_DOM_SELENIUM_URL on an idle node."""
import os
import unittest
from unittest.mock import patch
from urllib.parse import quote

from app.browser import AppleFlow


@unittest.skipUnless(os.getenv("DEVICE_DOM_SELENIUM_URL"), "requires an idle Selenium node")
class DeviceDOMTest(unittest.TestCase):
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
