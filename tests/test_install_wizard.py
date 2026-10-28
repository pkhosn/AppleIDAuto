from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallWizardStaticTest(unittest.TestCase):
    def test_wizard_is_wired_before_application_routes(self):
        routes = (ROOT / "route/app.php").read_text(encoding="utf-8")
        self.assertIn("Route::get('install', 'install/index')", routes)
        self.assertLess(routes.index("Route::get('install'"), routes.index("Route::rule('/', 'index/index')"))
        middleware = (ROOT / "app/middleware.php").read_text(encoding="utf-8")
        self.assertIn("Install::class", middleware)

    def test_form_and_local_redis_binding(self):
        page = (ROOT / "view/install/index.html").read_text(encoding="utf-8")
        for field in ("db_name", "redis_host", "redis_container_host", "admin_username", "admin_password"):
            self.assertIn(f'name="{field}"', page)
        compose = (ROOT / "automation-server/docker-compose.yml").read_text(encoding="utf-8")
        self.assertNotIn('"127.0.0.1:6379:6379"', compose)
        self.assertIn('"6379"', compose)
        redis_start = compose.rindex("\n  redis:\n")
        redis_section = compose[redis_start:compose.index("\nvolumes:\n", redis_start)]
        self.assertNotIn("ports:", redis_section)
        self.assertIn("expose:", redis_section)

    def test_installer_remains_independent_of_retired_panel(self):
        page = (ROOT / "view/install/index.html").read_text(encoding="utf-8")
        self.assertIn("{__NOLAYOUT__}", page)
        self.assertFalse((ROOT / "view/admin").exists())
        self.assertFalse((ROOT / "view/user").exists())
        self.assertFalse((ROOT / "view/index").exists())

    def test_installer_rejects_partial_schema_and_validates_defaults(self):
        service = (ROOT / "app/service/InstallService.php").read_text(encoding="utf-8")
        for table in (
            "account", "automation_task_log", "order", "payment_config", "plan",
            "proxy", "proxy_source", "redeem_code", "share", "site_config", "unlock_record", "user",
        ):
            self.assertIn(f"'{table}'", service)
        self.assertIn("数据库结构不完整，缺失表", service)
        self.assertIn("payment_config 缺少默认配置", service)

    def test_share_manual_action_checks_membership(self):
        controller = (ROOT / "app/controller/ShareController.php").read_text(encoding="utf-8")
        self.assertIn("账号不属于此分享页", controller)

    def test_payment_configuration_is_checked_before_order_creation(self):
        controller = (ROOT / "app/controller/PayController.php").read_text(encoding="utf-8")
        self.assertLess(controller.index("getConfig()"), controller.index("createOrder("))


if __name__ == "__main__":
    unittest.main()
