from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AdminCrudRegressionTest(unittest.TestCase):
    def read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_legacy_panel_stack_is_retired(self):
        for relative in (
            "app/controller/AdminController.php",
            "app/controller/UserController.php",
            "app/middleware/Admin.php",
            "app/middleware/Auth.php",
            "view/admin",
            "view/user",
            "view/index",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)
        routes = self.read("route/app.php")
        self.assertNotIn("admin/", routes.split("Route::group('frontend-api/v1'", 1)[0])
        self.assertNotIn("user/", routes.split("Route::group('frontend-api/v1'", 1)[0])

    def test_react_crud_routes_and_handlers_are_complete(self):
        routes = self.read("route/app.php")
        controller = self.read("app/controller/FrontendApiController.php")
        for route in (
            "accounts", "shares", "templates", "proxies", "admin/users",
            "admin/plans", "admin/redeem-codes", "admin/settings",
        ):
            self.assertIn(route, routes)
        for method in (
            "accounts", "saveAccount", "shares", "saveShare", "templates",
            "saveTemplate", "proxies", "saveProxy", "adminUsers", "saveAdminUser",
        ):
            self.assertIn(f"function {method}", controller)

    def test_device_cleanup_is_enabled_end_to_end(self):
        api = self.read("app/controller/ApiController.php")
        executor = self.read("automation-server/app/executor.py")
        editor = self.read("frontend/src/components/crud-editors.tsx")
        schedule = self.read("app/service/AutomationScheduleConfig.php")
        driver = self.read("account-driver/app/browser.py")
        self.assertIn("$account_info->enable_delete_devices", api)
        self.assertIn('self.client.run("delete_devices", payload)', executor)
        self.assertIn("enable_delete_devices: false", editor)
        self.assertIn("'device_cleanup' => ['enable_delete_devices', 30]", schedule)
        self.assertIn('def _delete_devices', driver)
        self.assertNotIn('"not_implemented"', executor)

    def test_delete_models_reload_the_requested_record(self):
        account = self.read("app/model/Account.php")
        share = self.read("app/model/SharePage.php")
        proxy = self.read("app/model/Proxy.php")
        self.assertIn("$account = $this->fetch((int)$id)", account)
        self.assertIn("$share = $this->fetch((int)$id)", share)
        self.assertIn("$proxy = $this->fetch((int)$id)", proxy)
        for source in (account, share, proxy):
            self.assertNotIn("if (!$this)", source)

    def test_arm64_uses_official_chromium_image(self):
        compose = self.read("automation-server/docker-compose.yml")
        self.assertIn("selenium/standalone-chromium:4.47.0-20260808", compose)
        self.assertNotIn("selenium/standalone-chrome:", compose)

    def test_exception_context_is_written_into_log_message(self):
        handler = self.read("app/ExceptionHandle.php")
        self.assertIn("json_encode", handler)
        self.assertIn("Unhandled request exception ' .", handler)

    def test_controlled_recovery_finalization_is_hash_guarded(self):
        routes = self.read("route/app.php")
        controller = self.read("app/controller/ApiController.php")
        service = self.read("app/service/ApiService.php")
        self.assertIn("Route::post('finalize_recovery'", routes)
        self.assertIn("function finalizeRecovery", controller)
        self.assertIn("hash_equals", service)
        self.assertIn("'enable_auto_update_password' => 0", service)
        self.assertIn("'enable_delete_devices' => 0", service)
        self.assertIn("'check_interval' => 30", service)

    def test_mail_recovery_credentials_are_encrypted_and_manual_only(self):
        credential_service = self.read("app/service/MailboxCredentialService.php")
        api = self.read("app/controller/ApiController.php")
        recovery = self.read("app/service/ManualRecoveryService.php")
        automation_api = self.read("automation-server/app/api.py")
        email_recovery = self.read("automation-server/scripts/controlled_email_recovery.py")
        self.assertIn("aes-256-gcm", credential_service)
        self.assertIn("mailbox_password_cipher", credential_service)
        self.assertIn("automationConfig", api)
        self.assertIn("startEmailPasswordRecovery", recovery)
        self.assertIn("startSecurityRollback", recovery)
        self.assertIn("startEmailPasswordRecovery", automation_api)
        self.assertIn("account_not_frozen", email_recovery)

    def test_mailbox_key_command_uses_php_secure_random_bytes(self):
        command = self.read("app/command/MailboxKey.php")
        console = self.read("config/console.php")
        self.assertIn("random_bytes(32)", command)
        self.assertIn("base64_encode", command)
        self.assertIn("mailbox:key", console)

    def test_manual_recovery_api_is_admin_only_and_uses_json_envelope(self):
        routes = self.read("route/app.php")
        controller = self.read("app/controller/FrontendApiController.php")
        for route in (
            "admin/accounts/:id/recovery/email-password",
            "admin/accounts/:id/recovery/security-rollback",
            "admin/accounts/:id/recovery-history",
            "admin/recoveries/:runId",
        ):
            self.assertIn(route, routes)
        for method in (
            "startEmailPasswordRecovery", "startSecurityRollback",
            "recoveryHistory", "recoveryStatus",
        ):
            body = controller.split(f"function {method}", 1)[1]
            self.assertIn("isAdmin()", body[:900])
            self.assertIn("403", body[:900])
        self.assertIn("manualRecoveryResponse", controller)
        self.assertIn("'success'", controller)
        self.assertIn("'message'", controller)

    def test_manual_recovery_preconditions_are_centralized(self):
        service = self.read("app/service/ManualRecoveryService.php")
        registry = self.read("app/service.php")
        self.assertIn("ManualRecoveryService::class", registry)
        self.assertIn("账号不存在", service)
        self.assertIn("$account->enable_auto_update_password", service)
        self.assertIn("$account->enable_delete_devices", service)
        self.assertIn("hasRecoveryMethod", service)
        self.assertIn("backend.enable_api", service)
        for status in ("404", "409", "503"):
            self.assertIn(status, service)

    def test_react_mail_recovery_has_actions_history_and_bounded_polling(self):
        page = self.read("frontend/src/pages/mail-recovery-page.tsx")
        self.assertIn("邮件改密", page)
        self.assertIn("安全设置回退", page)
        self.assertIn("AlertDialog", page)
        self.assertIn("恢复历史", page)
        self.assertIn("attempt < 120", page)
        self.assertIn("3000", page)
        self.assertIn("poll(runId, 1)", page)
        self.assertIn("admin/recoveries/", page)
        self.assertIn("security_rollback_link_expired", page)
        self.assertNotIn("mailbox_password_cipher", page)

    def test_saved_and_draft_mailbox_connections_use_current_api(self):
        routes = self.read("route/app.php")
        frontend = self.read("app/controller/FrontendApiController.php")
        backend = self.read("app/service/BackendService.php")
        editor = self.read("frontend/src/components/crud-editors.tsx")
        automation_api = self.read("automation-server/app/api.py")
        self.assertIn("Route::post('mailbox/test'", routes)
        self.assertIn("function mailboxTest", frontend)
        self.assertIn("transientAutomationConfig", frontend)
        self.assertIn("testMailboxConfig", backend)
        self.assertIn("mailboxTest.mutate", editor)
        self.assertIn('@router.post("/testMailboxConfig")', automation_api)

    def test_share_links_support_current_batch_operations(self):
        routes = self.read("route/app.php")
        service = self.read("app/service/ShareService.php")
        entitlement = self.read("app/service/EntitlementService.php")
        list_page = self.read("frontend/src/pages/list-page.tsx")
        editor = self.read("frontend/src/components/crud-editors.tsx")
        self.assertIn("shares/batch-create", routes)
        self.assertIn("shares/batch-delete", routes)
        self.assertIn("shares/batch-delete", list_page)
        self.assertIn("shares/batch-create", editor)
        self.assertIn("function createBatch", service)
        self.assertIn("function deleteBatch", service)
        self.assertIn("function canAddShares", entitlement)

    def test_share_list_searches_globally_instead_of_filtering_only_the_current_page(self):
        controller = self.read("app/controller/FrontendApiController.php")
        service = self.read("app/service/ShareService.php")
        list_page = self.read("frontend/src/pages/list-page.tsx")
        self.assertIn("'keyword' => (string)$this->request->param('keyword', '')", controller)
        self.assertIn("whereLike('share_link', $like)", service)
        self.assertIn("whereLike('remark', $like, 'OR')", service)
        self.assertIn("Db::name('share_group')->whereLike('name'", service)
        self.assertIn("&keyword=${encodeURIComponent(keyword)}", list_page)
        self.assertIn("全局搜索分享标识、备注或分组", list_page)

    def test_execution_records_are_limited_per_account_and_driver_artifacts_keep_five_days(self):
        model = self.read("app/model/UnlockRecord.php")
        command = self.read("app/command/DbClean.php")
        jobs = self.read("account-driver/app/jobs.py")
        config = self.read("account-driver/app/config.py")
        env = self.read("automation-server/.env.example")
        self.assertIn("const RETENTION_LIMIT = 10;", model)
        self.assertIn("public function pruneAccount(", model)
        self.assertIn("public function pruneAll(", model)
        self.assertIn("$this->pruneAccount((int)$data['account_id']);", model)
        self.assertIn("PruneRecords", command)
        # 截图、探针与诊断 JSON 都按保留期清理（原先只删 *.png）。
        self.assertIn("self.artifact_dir.iterdir()", jobs)
        self.assertNotIn('self.artifact_dir.glob("*.png")', jobs)
        self.assertIn('os.getenv("ARTIFACT_DAYS", "5")', config)
        self.assertIn("ARTIFACT_DAYS=5", env)

    def test_account_credentials_are_visible_to_the_owner(self):
        controller = self.read("app/controller/FrontendApiController.php")
        list_page = self.read("frontend/src/pages/list-page.tsx")
        editor = self.read("frontend/src/components/crud-editors.tsx")
        self.assertIn("private function credentialFields(", controller)
        self.assertIn("decryptMailboxPassword($account)", controller)
        self.assertIn("credential_audit", controller)
        # 二次输入面板密码的校验已移除：移动端 prompt 会被键盘自动大写/补空格而误报密码错误。
        self.assertNotIn("credential_reauth", controller)
        self.assertNotIn("当前登录密码错误", controller)
        self.assertIn("export function AccountCredentials(", editor)
        self.assertIn("AccountCredentials", list_page)
        self.assertNotIn("window.prompt", list_page)

    def test_page_template_picker_matches_template_management(self):
        controller = self.read("app/controller/FrontendApiController.php")
        editor = self.read("frontend/src/components/crud-editors.tsx")
        self.assertIn("private function resolveDefaultTemplateId(", controller)
        self.assertIn("$row['effective_default'] = (int)$row['id'] === $defaultId", controller)
        self.assertIn("function pageTemplateChoices(", editor)
        self.assertIn("（默认）", editor)
        # 旧实现把硬编码的“默认模板”选项和模板列表并排渲染，下拉会比模板管理多一条重复项。
        self.assertIn("pageTemplates.options.map(", editor)
        self.assertNotIn("(templates.data?.data??[]).map(", editor)

    def test_confirmation_dialogs_close_and_lists_expose_page_sizes(self):
        alert = self.read("frontend/src/components/ui/alert-dialog.tsx")
        controller = self.read("app/controller/FrontendApiController.php")
        list_page = self.read("frontend/src/pages/list-page.tsx")
        selector = self.read("frontend/src/components/per-page-select.tsx")
        # Base UI closes an alert dialog only from its `Close` part (outside press is
        # disabled), so the confirm action must render one or the dialog stays open.
        self.assertIn("AlertDialogPrimitive.Close", alert)
        self.assertIn('data-slot="alert-dialog-action"', alert)
        self.assertIn("private function perPage(", controller)
        self.assertIn("proxyService->fetchAll($perPage)", controller)
        self.assertIn("shareService->fetchAll($filters, $perPage)", controller)
        self.assertIn("shareService->fetchByOwner((int)$this->user()['id'], $filters, $perPage)", controller)
        for relative in (
            "app/service/ShareService.php", "app/service/AccountService.php",
            "app/service/AutomationTaskService.php", "app/service/UnlockService.php",
            "app/service/ProxyService.php", "app/service/OrderService.php",
            "app/service/UserService.php", "app/service/RedeemCodeService.php",
            "app/service/PlanService.php",
        ):
            self.assertIn("int $perPage = 25", self.read(relative), relative)
        self.assertIn("PerPageSelect", list_page)
        self.assertIn("per_page=${perPage}", list_page)
        self.assertIn("[10, 25, 50, 100]", selector)
        self.assertIn("panel:per-page:", selector)

    def test_orders_and_proxy_health_use_current_endpoints(self):
        routes = self.read("route/app.php")
        frontend = self.read("app/controller/FrontendApiController.php")
        list_page = self.read("frontend/src/pages/list-page.tsx")
        proxy_page = self.read("frontend/src/pages/proxy-page.tsx")
        self.assertIn("orders/:id/cancel", routes)
        self.assertIn("orders/${row.id}/cancel", list_page)
        exact = "Route::post('proxies/:id/test'"
        generic = "Route::post('proxies/:id'"
        self.assertLess(routes.index(exact), routes.index(generic))
        self.assertIn("function testProxy", frontend)
        self.assertIn("proxies/${id}/test", proxy_page)

    def test_disabled_proxy_nodes_explicitly_select_direct_mode(self):
        api = self.read("app/controller/ApiController.php")
        proxy = self.read("app/service/ProxyService.php")
        self.assertIn("hasEnabledForOwner", proxy)
        self.assertIn("$data['connection_mode'] = $data['proxy_required'] ? 'waiting_proxy' : 'direct'", api)
        self.assertIn("$data['connection_mode'] = 'proxy'", api)
        self.assertIn("where('status', 1)", proxy)

    def test_react_mailbox_and_static_ip_are_wired_safely(self):
        routes = self.read("route/app.php")
        automation = self.read("automation-server/app/api.py")
        self.assertIn("Route::post('mailbox/test'", routes)
        self.assertIn("Route::post('proxies/:id/test'", routes)
        self.assertIn('@router.post("/testMailboxConfig")', automation)
        self.assertIn('@router.post("/testProxy")', automation)

    def test_independent_scheduler_bypasses_only_the_retired_backend_switch(self):
        backend = self.read("app/service/BackendService.php")
        automation = self.read("automation-server/app/api.py")
        self.assertIn("AutomationScheduleConfig::globals()['active']", backend)
        self.assertIn("!env('backend.enable_api') && !$independent", backend)
        self.assertIn("自动化后端配置不完整", backend)
        self.assertIn("@router.post('/restartTask')", automation)
        self.assertIn("await queue.put(payload.id)", automation)

    def test_api_session_middleware_is_kept_and_returns_frontend_envelope(self):
        self.assertFalse((ROOT / "app/middleware/Admin.php").exists())
        self.assertFalse((ROOT / "app/middleware/Auth.php").exists())
        middleware = self.read("app/middleware/UserIndex.php")
        csrf = self.read("app/middleware/FrontendCsrf.php")
        self.assertIn("frontend-api/", middleware)
        self.assertIn("'success' => false", middleware)
        self.assertIn("application/json", middleware)
        self.assertIn("x-csrf-token", csrf)
        self.assertIn("hash_equals", csrf)

    def test_security_rollback_requires_an_explicit_apple_confirmation_control(self):
        browser = self.read("account-driver/app/browser.py")
        self.assertIn("submit_rollback", browser)
        self.assertIn("restore previous security settings", browser)
        self.assertIn("turn off two-factor authentication", browser)

    def test_email_recovery_retries_only_pre_submission_driver_failures(self):
        recovery = self.read("automation-server/scripts/controlled_email_recovery.py")
        self.assertIn("run_driver_stage", recovery)
        self.assertIn("retry_delays=RETRY_DELAYS", recovery)
        self.assertIn("--max-attempts", recovery)


if __name__ == "__main__":
    unittest.main()
