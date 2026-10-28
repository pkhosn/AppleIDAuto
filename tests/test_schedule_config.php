<?php
declare(strict_types=1);
if (PHP_SAPI !== 'cli') exit(1);
require dirname(__DIR__) . '/vendor/autoload.php';
$app = new think\App(dirname(__DIR__));
$app->initialize();
use app\service\AutomationScheduleConfig as Cycles;
use app\controller\FrontendApiController;
use think\facade\Db;
use think\facade\Session;

$checks = 0;
function check($ok, string $name): void { global $checks; if (!$ok) throw new RuntimeException($name); $checks++; }
function controller(array $params = []): FrontendApiController {
    global $app;
    $app->instance('request', (new think\Request())->withServer(['REQUEST_METHOD' => 'POST'])->setMethod('POST')->withPost($params));
    return new FrontendApiController($app);
}
$originalSession = Session::get('user_id');
$failed = false;
Db::startTrans();
try {
    $defaults = ['active' => true, 'features' => ['password_check' => 30, 'password_update' => 1440, 'two_factor' => 30, 'profile_restore' => 360, 'device_cleanup' => 30]];
    $new = Cycles::resolve([], $defaults);
    check($new['features']['password_update']['effective_minutes'] === 1440, 'new account global default');
    $custom = [];
    foreach (Cycles::FEATURES as $key => $definition) $custom[$key] = ['source' => 'custom', 'minutes' => 17];
    $encoded = Cycles::normalize($custom);
    $resolved = Cycles::resolve(['automation_schedule' => $encoded], $defaults);
    check($resolved['features']['password_update']['effective_minutes'] === 17, 'custom isolated');
    check($resolved['mode'] === 'custom' && $resolved['version'] === 4, 'single-source mode returned');
    check($resolved['shared'] === ['enabled' => false, 'minutes' => 30, 'features' => []], 'legacy shared defaults');
    $shared = ['enabled' => true, 'minutes' => 45, 'features' => ['profile_restore', 'device_cleanup']];
    $sharedResolved = Cycles::resolve(['automation_schedule' => Cycles::normalize(['features' => $custom, 'shared' => $shared])], $defaults);
    check($sharedResolved['features']['profile_restore']['effective_minutes'] === 45, 'shared overrides selected cadence');
    check($sharedResolved['features']['password_update']['effective_minutes'] === 17, 'shared leaves other cadence unchanged');
    check(!$sharedResolved['features']['profile_restore']['enabled'], 'shared membership never enables feature');
    $shared['enabled'] = false;
    $unshared = Cycles::resolve(['automation_schedule' => Cycles::normalize(['features' => $custom, 'shared' => $shared])], $defaults);
    check($unshared['features']['profile_restore']['effective_minutes'] === 17 && count($unshared['shared']['features']) === 2, 'shared off restores values and keeps membership');
    foreach ([['enabled' => true, 'features' => []], ['features' => ['bad']], ['minutes' => 0]] as $badShared) {
        $thrown = false;
        try { Cycles::normalize(['features' => $custom, 'shared' => $badShared]); } catch (InvalidArgumentException $e) { $thrown = true; }
        check($thrown, 'invalid shared configuration rejected');
    }
    $legacyFour = $custom;
    unset($legacyFour['device_cleanup']);
    $legacyResolved = Cycles::resolve(['automation_schedule' => Cycles::normalize($legacyFour)], $defaults);
    check($legacyResolved['features']['device_cleanup']['effective_minutes'] === 30, 'legacy four-feature schedule gains device default');
    $legacyStored = json_encode(['version' => 2, 'mode' => 'custom', 'features' => $legacyFour], JSON_UNESCAPED_UNICODE);
    $legacySafe = Cycles::resolve(['automation_schedule' => $legacyStored, 'enable_delete_devices' => 1], $defaults);
    check($legacySafe['features']['device_cleanup']['enabled'] === false, 'legacy schedule cannot activate destructive device cleanup');
    $mixed = $custom;
    $mixed['password_check']['source'] = 'global';
    $mixedResolved = Cycles::resolve(['automation_schedule' => Cycles::normalize($mixed)], $defaults);
    check($mixedResolved['mode'] === 'mixed'
        && $mixedResolved['features']['password_check']['effective_minutes'] === 30
        && $mixedResolved['features']['password_update']['effective_minutes'] === 17,
        'each feature independently selects global or custom cadence');
    $mixedRoundTrip = Cycles::resolve([
        'automation_schedule' => Cycles::normalize(['mode' => 'mixed', 'features' => $mixed]),
    ], $defaults);
    check($mixedRoundTrip['mode'] === 'mixed', 'mixed schedule response can be saved unchanged');
    foreach ([0, -1, 43201, 1.5, 'bad'] as $bad) {
        $thrown = false;
        try { Cycles::minutes($bad); } catch (InvalidArgumentException $e) { $thrown = true; }
        check($thrown, 'invalid interval rejected');
    }
    $user = Db::name('user')->insertGetId(['username' => 'schedule-fixture-' . bin2hex(random_bytes(6)), 'password' => password_hash(bin2hex(random_bytes(12)), PASSWORD_DEFAULT), 'status' => 1, 'is_admin' => 0]);
    Session::set('user_id', $user);
    check(controller(['features' => $defaults['features']])->updateSettings('automation')->getCode() === 403, 'global write requires admin');
    $existing = Db::name('account')->order('id')->find();
    if ($existing) {
        check(controller()->account($existing['id'])->getCode() === 403, 'detail ownership');
        check(controller()->accountSchedule($existing['id'])->getCode() === 403, 'schedule ownership');
        check(controller(['automation_schedule' => $custom])->saveAccount($existing['id'])->getCode() === 403, 'schedule write ownership');
    }
    Session::set('frontend_csrf', 'fixture-token');
    $request = (new think\Request())->setMethod('POST');
    $csrf = new app\middleware\FrontendCsrf();
    check($csrf->handle($request, fn() => json(['ok' => true]))->getCode() === 419, 'CSRF required');
    check(Cycles::saveGlobals(['features' => ['password_check' => 0]])['status'] === false, 'invalid global config rejected atomically');
    check(Cycles::saveGlobals(['features' => $defaults['features']])['status'] === true, 'valid global config accepted');
    if ($existing) {
        $fakeBackend = new class($app) extends app\service\BackendService {
            public int $restarts = 0;
            public int $adds = 0;
            public function restartTask($id, array $options = []): array { $this->restarts++; return ['status' => true, 'msg' => 'mock']; }
            public function addTask($id): array { $this->adds++; return ['status' => true, 'msg' => 'mock']; }
            public function removeTask($id): array { return ['status' => true, 'msg' => 'mock']; }
        };
        $app->instance('backendService', $fakeBackend);
        // 邮件主机校验会做 DNS 解析；测试环境用固定公网地址替身，专注验证凭据的保存/回填/保留。
        $app->instance('networkAddressPolicy', new class($app) extends app\service\NetworkAddressPolicy {
            public function assertPublicHost(string $host): array { return ['203.0.113.10']; }
        });
        $active = Db::name('site_config')->where('config_key', 'automation_independent_enabled')->find();
        if ($active) Db::name('site_config')->where('config_key', 'automation_independent_enabled')->update(['config_value' => '1']);
        else Db::name('site_config')->insert(['config_key' => 'automation_independent_enabled', 'config_value' => '1']);
        $fixture = $existing;
        unset($fixture['id']);
        $fixture['owner'] = $user;
        $fixture['username'] = 'schedule-fixture@example.test';
        $fixture['password'] = 'fixture-only-password';
        $fixture['mailbox_password_cipher'] = null;
        $fixture['recovery_methods'] = '';
        $fixture['recovery_email'] = 'fixture-mail@example.test';
        $fixture['mailbox_host'] = 'imap.example.test';
        $fixture['mailbox_username'] = 'fixture-mail';
        $fixture['enable_email_recovery'] = 0;
        $fixture['enable'] = 1;
        $fixture['proxy_id'] = 0;
        $fixtureId = Db::name('account')->insertGetId($fixture);
        $accountList = json_decode((string)controller()->accounts()->getContent(), true);
        $listedAccount = null;
        foreach (($accountList['data'] ?? []) as $item) {
            if ((int)($item['id'] ?? 0) === $fixtureId) { $listedAccount = $item; break; }
        }
        // 列表保持脱敏，凭据只在通过归属校验的详情 / 查看密码响应中返回。
        check(!array_key_exists('password', $listedAccount) && !empty($listedAccount['password_configured']), 'account list keeps credentials out of the payload');
        $detail = json_decode((string)controller()->account($fixtureId)->getContent(), true);
        check(($detail['data']['password'] ?? '') === $fixture['password'], 'owner account detail returns the current password');
        $reveal = json_decode((string)controller()->revealAccountPassword($fixtureId)->getContent(), true);
        check(($reveal['data']['password'] ?? '') === $fixture['password']
            && !array_key_exists('current_password', $reveal['data'] ?? []), 'reveal returns credentials without a re-auth payload');
        $taskList = json_decode((string)controller()->tasks()->getContent(), true);
        $listedTask = null;
        foreach (($taskList['data'] ?? []) as $item) {
            if ((int)($item['id'] ?? 0) === $fixtureId) { $listedTask = $item; break; }
        }
        check($listedTask !== null && !array_key_exists('password', $listedTask)
            && !array_key_exists('current_password', $listedTask), 'task list does not expose password');
        $response = controller(['remark' => 'fixture edit', 'enable_delete_devices' => true, 'automation_schedule' => ['mode' => 'custom', 'features' => $custom]])->saveAccount($fixtureId);
        check($response->getCode() === 200, 'ordinary save accepted');
        check($fakeBackend->restarts === 0 && $fakeBackend->adds === 1, 'ordinary save syncs config without restarting');
        $saved = Db::name('account')->where('id', $fixtureId)->find();
        $savedIdentity = (new app\model\Account())->fetch($fixtureId);
        check($saved['username'] === $fixture['username'] && $savedIdentity->password === $fixture['password'],
            'omitted identity preserved');
        check($saved['password'] !== $fixture['password'], 'credentials are encrypted at rest');
        check($saved['recovery_email'] === $fixture['recovery_email'] && $saved['mailbox_host'] === $fixture['mailbox_host'] && $saved['mailbox_username'] === $fixture['mailbox_username'], 'cycle-only save preserves mailbox');
        $savedSchedule = json_decode($saved['automation_schedule'], true);
        check($savedSchedule['version'] === 4 && $savedSchedule['mode'] === 'custom'
            && $savedSchedule['features']['password_update']['minutes'] === 17, 'account-wide independent cycles persisted');
        check((int)$saved['enable_delete_devices'] === 1, 'device cleanup flag persisted');
        $incompleteProfile = controller([
            'enable_auto_restore_profile' => true, 'dob' => '1984-12-17',
            'profile_family_name' => '', 'profile_middle_name' => '', 'profile_given_name' => '',
            'profile_country' => '', 'profile_language' => '',
        ])->saveAccount($fixtureId);
        check($incompleteProfile->getCode() === 422, 'automatic profile restore requires a complete target');
        $profileSave = controller([
            'enable_auto_restore_profile' => true, 'dob' => '1984-12-17',
            'profile_family_name' => '测', 'profile_middle_name' => '', 'profile_given_name' => '试',
            'profile_country' => '美国', 'profile_language' => '简体中文',
        ])->saveAccount($fixtureId);
        $savedProfile = Db::name('account')->where('id', $fixtureId)->find();
        check($profileSave->getCode() === 200 && $savedProfile['profile_name'] === '测试'
            && !empty($savedProfile['profile_baseline_confirmed_at']),
            'saved profile target is immediately authorized without a confirmation checkbox');
        $legacySave = controller([
            'enable_delete_devices' => true,
            'automation_schedule' => ['mode' => 'custom', 'features' => $legacyFour],
        ])->saveAccount($fixtureId);
        check($legacySave->getCode() === 200
            && (int)Db::name('account')->where('id', $fixtureId)->value('enable_delete_devices') === 0,
            'four-feature client cannot authorize destructive device cleanup');
        check(controller(['automation_schedule' => ['password_update' => ['source' => 'custom', 'minutes' => 0]]])->saveAccount($fixtureId)->getCode() === 422, 'invalid account cycle rejected');

        // Full mailbox save must refill on the next edit. A subsequent partial
        // account save must retain every mailbox field and its encrypted secret.
        $mailboxPayload = [
            'recovery_methods' => ['email_password', 'email_security'],
            'recovery_email' => 'rescue-fixture@example.test',
            'mailbox_host' => 'imap.example.test', 'mailbox_port' => 993,
            'mailbox_username' => 'rescue-fixture', 'mailbox_password' => 'MailboxFixtureOnly!42',
            'mailbox_tls' => true,
        ];
        $mailboxResponse = controller($mailboxPayload)->saveAccount($fixtureId);
        check($mailboxResponse->getCode() === 200, 'mailbox save accepted: ' . (string)$mailboxResponse->getContent());
        $mailboxSaved = Db::name('account')->where('id', $fixtureId)->find();
        check($mailboxSaved['recovery_email'] === $mailboxPayload['recovery_email']
            && $mailboxSaved['mailbox_host'] === $mailboxPayload['mailbox_host']
            && $mailboxSaved['mailbox_username'] === $mailboxPayload['mailbox_username']
            && $mailboxSaved['mailbox_password_cipher'] !== '', 'mailbox fields persisted');
        $detail = json_decode((string)controller()->account($fixtureId)->getContent(), true);
        check(($detail['data']['recovery_email'] ?? '') === $mailboxPayload['recovery_email']
            && ($detail['data']['mailbox_host'] ?? '') === $mailboxPayload['mailbox_host']
            && ($detail['data']['mailbox_username'] ?? '') === $mailboxPayload['mailbox_username']
            && ($detail['data']['mailbox_password'] ?? '') === $mailboxPayload['mailbox_password'], 'mailbox refills on second edit');
        $cipherBefore = $mailboxSaved['mailbox_password_cipher'];
        check(controller(['remark' => 'mailbox must remain'])->saveAccount($fixtureId)->getCode() === 200, 'partial save accepted');
        $afterPartial = Db::name('account')->where('id', $fixtureId)->find();
        check($afterPartial['recovery_email'] === $mailboxPayload['recovery_email']
            && $afterPartial['mailbox_host'] === $mailboxPayload['mailbox_host']
            && $afterPartial['mailbox_username'] === $mailboxPayload['mailbox_username']
            && hash_equals($cipherBefore, $afterPartial['mailbox_password_cipher']), 'partial save retains complete mailbox');
    }
} catch (Throwable $e) {
    fwrite(STDERR, 'FAIL: ' . $e->getMessage() . "\n");
    $failed = true;
} finally {
    Db::rollback(); // No account or global config changes survive this test.
    Session::set('user_id', $originalSession);
}
if ($failed) exit(1);
echo "$checks schedule API/config checks passed (transaction rolled back).\n";
