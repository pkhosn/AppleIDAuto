from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UiShellTest(unittest.TestCase):
    def read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_auth_page_uses_current_react_contracts(self):
        page = self.read("frontend/src/pages/login-page.tsx")
        routes = self.read("route/app.php")
        self.assertIn('`auth/${mode}`', page)
        self.assertIn('id="username"', page)
        self.assertIn('id="password"', page)
        self.assertIn('value="login"', page)
        self.assertIn("Turnstile", page)
        self.assertIn("Route::post('auth/login'", routes)
        self.assertIn("Route::post('auth/register'", routes)
        self.assertFalse((ROOT / "view/index").exists())

    def test_public_share_views_are_independent_of_retired_panel(self):
        for name in ("password", "error", "result"):
            page = self.read(f"view/share/{name}.html")
            self.assertIn("{__NOLAYOUT__}", page)
            self.assertIn("/static/css/panel.css", page)
        result = self.read("view/share/result.html")
        self.assertIn('data-clipboard-text="{$account->username}"', result)
        self.assertIn('data-clipboard-text="{$account->password}"', result)

    def test_react_shell_owns_both_authenticated_experiences(self):
        app = self.read("frontend/src/App.tsx")
        shell = self.read("frontend/src/components/app-shell.tsx")
        self.assertIn("BrowserRouter", app)
        self.assertIn("adminRoot", app)
        self.assertIn("<AdminOnly", app)
        self.assertIn('mail-recovery', app)
        self.assertIn('"邮件恢复"', shell)
        self.assertFalse((ROOT / "view/admin").exists())
        self.assertFalse((ROOT / "view/user").exists())

    def test_php_root_is_only_a_spa_fallback(self):
        controller = self.read("app/controller/IndexController.php")
        self.assertIn("/control-app/", controller)
        self.assertNotIn("View::fetch", controller)
        self.assertNotIn("Session::", controller)

    def test_share_batch_dialog_collects_quantity_and_expiry(self):
        editor = self.read("frontend/src/components/crud-editors.tsx")
        self.assertIn("batch-create", editor)
        self.assertIn("quantity", editor)
        self.assertIn("expiry_mode", editor)
        self.assertIn("首次访问后失效", editor)

    def test_current_settings_cover_site_security_and_payment(self):
        app = self.read("frontend/src/App.tsx")
        page = self.read("frontend/src/pages/settings-page.tsx")
        routes = self.read("route/app.php")
        self.assertIn('section="branding"', app)
        self.assertIn('section="registration"', app)
        self.assertIn('section="payment"', app)
        self.assertIn("管理端路径", page)
        self.assertIn("访问安全", page)
        self.assertIn("支付配置", page)
        self.assertIn("Route::post('admin/settings/:section'", routes)

    def test_compiled_styles_still_cover_public_views(self):
        css = self.read("public/static/css/panel.css")
        for selector in (".auth-shell", ".public-account-card", ".table-responsive"):
            self.assertIn(selector, css)
        self.assertFalse((ROOT / "public/static/css/tabler.min.css").exists())

    def test_shared_layout_does_not_load_deleted_panel_bundles(self):
        layout = self.read("view/layout.html")
        self.assertIn("/static/css/panel.css", layout)
        self.assertNotIn("tabler.min.css", layout)
        self.assertNotIn("/static/js/main.js", layout)

    def test_registration_security_is_server_verified_and_multi_domain(self):
        controller = self.read("app/controller/FrontendApiController.php")
        auth = self.read("app/service/AuthService.php")
        config = self.read("app/service/SiteConfigService.php")
        verifier = self.read("app/service/TurnstileService.php")
        settings = self.read("frontend/src/pages/settings-page.tsx")
        schema = self.read("db/update/security_settings.sql")
        self.assertIn("function updateSettings", controller)
        self.assertIn("registrationService->validate", auth)
        self.assertIn("用户名已存在", auth)
        self.assertIn("challenges.cloudflare.com/turnstile/v0/siteverify", verifier)
        self.assertIn("explode(',',", verifier)
        self.assertIn("turnstile_secret_key", config)
        self.assertIn("英文逗号", settings)
        self.assertIn("CREATE TABLE IF NOT EXISTS `site_config`", schema)
        self.assertIn("uk_user_username", schema)

    def test_turnstile_is_verified_once_and_both_auth_modes_can_retry(self):
        api = self.read("app/controller/FrontendApiController.php")
        registration = self.read("app/service/RegistrationService.php")
        react_login = self.read("frontend/src/pages/login-page.tsx")
        api_login = api.split("public function login(): Json", 1)[1].split("public function register(): Json", 1)[0]
        api_register = api.split("public function register(): Json", 1)[1].split("public function logout(): Json", 1)[0]
        self.assertEqual(api_login.count("turnstileService->verify"), 1)
        self.assertNotIn("turnstileService->verify", api_register)
        self.assertEqual(registration.count("turnstileService->verify"), 1)
        self.assertIn('action={mode}', react_login)
        self.assertIn("setTurnstileGeneration((value) => value + 1)", react_login)

    def test_alert_redirects_without_javascript_url_strings(self):
        helper = self.read("app/common.php")
        self.assertIn("window.location.assign", helper)
        self.assertNotIn("javascript:location.href", helper)

    def test_account_editor_keeps_encrypted_mailbox_field_internal(self):
        page = self.read("frontend/src/components/crud-editors.tsx")
        for method in ("email_password", "email_security", "account_login"):
            self.assertIn(method, page)
        self.assertIn("邮箱密码", page)
        self.assertIn('mailbox_password: ""', page)
        self.assertNotIn("mailbox_password_cipher", page)

    def test_notifications_are_a_separate_module_with_result_preferences(self):
        routes = self.read("route/app.php")
        app = self.read("frontend/src/App.tsx")
        notify = self.read("frontend/src/pages/notifications-page.tsx")
        profile = self.read("frontend/src/pages/profile-page.tsx")
        user_model = self.read("app/model/User.php")
        task_api = self.read("app/controller/ApiController.php")
        self.assertIn("Route::get('notifications'", routes)
        self.assertIn("<NotificationsPage", app)
        self.assertNotIn("tg_bot_token", profile)
        for mode in ('value="all"', 'value="success"', 'value="failure"'):
            self.assertIn(mode, notify)
        self.assertIn("array_key_exists($field, $data)", user_model)
        self.assertIn("$data['tg_notify_mode']", task_api)

    def test_rescue_mailbox_is_account_owned(self):
        routes = self.read("route/app.php")
        service = self.read("app/service/MailboxCredentialService.php")
        profile = self.read("frontend/src/pages/profile-page.tsx")
        account_editor = self.read("frontend/src/components/crud-editors.tsx")
        self.assertIn("Route::post('mailbox/test'", routes)
        self.assertIn("function mergeInput", service)
        self.assertIn("recovery_email", account_editor)
        self.assertIn("mailbox_password", account_editor)
        self.assertNotIn("recovery_email", profile)
        self.assertIn("recovery_methods", service)

    def test_proxy_management_has_current_information_architecture(self):
        page = self.read("frontend/src/pages/proxy-page.tsx")
        for label in ("代理网络", "静态 IP", "自动提取源"):
            self.assertIn(label, page)
        self.assertNotIn("local-proxies", page)
        self.assertNotIn("动态 IP 池", page)
        self.assertIn("proxy-sources", page)

    def test_dynamic_pool_backend_routes_and_services_are_removed(self):
        routes = self.read("route/app.php")
        services = self.read("app/service.php")
        for route in ("local-proxies", "proxy-subscription"):
            self.assertNotIn(route, routes)
        self.assertNotIn("LocalProxyPoolService", services)
        self.assertFalse((ROOT / "app/service/LocalProxyPoolService.php").exists())
        self.assertFalse((ROOT / "app/controller/ProxySubscriptionController.php").exists())

    def test_production_spa_artifact_is_present(self):
        if not (ROOT / "public/control-app/index.html").is_file():
            self.skipTest("Build the production frontend before checking generated assets")
        index = self.read("public/control-app/index.html")
        self.assertIn('<div id="root"></div>', index)
        self.assertIn("/control-app/assets/", index)


if __name__ == "__main__":
    unittest.main()
