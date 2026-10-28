"""Real DOM regressions. Opt in with DEVICE_DOM_SELENIUM_URL on an idle node."""
import os
import unittest
from urllib.parse import quote

from app.browser import AppleFlow


@unittest.skipUnless(os.getenv("DEVICE_DOM_SELENIUM_URL"), "requires an idle Selenium node")
class DeviceDOMTest(unittest.TestCase):
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
