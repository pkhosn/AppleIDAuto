<?php
declare(strict_types=1);

namespace app\controller;

use app\BaseController;
use app\model\Account;
use app\model\SharePage;
use app\model\User;
use think\facade\Db;
use think\facade\Session;
use think\response\Json;

/** Same-origin JSON boundary for the React control panel. */
class FrontendApiController extends BaseController
{
    private function ok($data = null, string $message = '获取成功', array $meta = []): Json
    { return json(['success' => true, 'data' => $data, 'message' => $message, 'errors' => [], 'meta' => $meta]); }

    private function fail(string $message, int $status = 422, array $errors = []): Json
    { return json(['success' => false, 'data' => null, 'message' => $message, 'errors' => $errors, 'meta' => []], $status); }

    private function user(): ?array
    {
        $row = Db::name('user')->where('id', (int)Session::get('user_id', 0))->find();
        if (!$row || empty($row['status'])) return null;
        unset($row['password'], $row['mailbox_password_cipher'], $row['tg_bot_token'], $row['tg_chat_id'],
            $row['tg_notify_mode'], $row['wx_pusher_id'], $row['webhook']);
        return $row;
    }

    private function isAdmin(): bool { return !empty(($this->user() ?? [])['is_admin']); }

    private function csrf(): string
    {
        $token = (string)Session::get('frontend_csrf', '');
        if ($token === '') { $token = bin2hex(random_bytes(32)); Session::set('frontend_csrf', $token); }
        return $token;
    }

    private function page($paginator, callable $map = null): array
    {
        $raw = $paginator->toArray();
        $items = array_values((array)($raw['data'] ?? []));
        if ($map) $items = array_map($map, $items);
        return [$items, ['page' => (int)($raw['current_page'] ?? 1), 'per_page' => (int)($raw['per_page'] ?? 25),
            'total' => (int)($raw['total'] ?? count($items)), 'last_page' => (int)($raw['last_page'] ?? 1),
            'total_pages' => (int)($raw['last_page'] ?? 1)]];
    }

    /** Page size requested by the SPA list pages, clamped to a safe range. */
    private function perPage(int $default = 25): int
    {
        return max(1, min(100, (int)$this->request->param('per_page', $default)));
    }

    private function safeAccount(array $row): array
    {
        $row['password_configured'] = !empty($row['password']);
        unset($row['password'], $row['mailbox_password_cipher'], $row['answer1'], $row['answer2'], $row['answer3']);
        $row['mailbox_password_configured'] = !empty($row['enable_email_recovery']) && !empty($row['mailbox_username']);
        $methods = $row['recovery_methods'] ?? [];
        if (!is_array($methods)) $methods = explode(',', (string)$methods);
        $row['recovery_methods'] = array_values(array_filter(array_map(
            static fn($method) => trim((string)$method),
            $methods
        )));
        return $row;
    }

    private function adminScope(): bool { return $this->isAdmin() && $this->request->param('scope') === 'admin'; }

    private function scopedOwner(): int
    {
        if ($this->adminScope()) return (int)$this->request->param('owner', 0);
        return (int)$this->user()['id'];
    }

    private function publicRegistration(): array
    {
        $registration = $this->app->siteConfigService->registration();
        unset($registration['turnstile_secret_key']);
        return $registration;
    }

    public function bootstrap(): Json
    {
        $user = $this->user(); $branding = $this->app->siteConfigService->branding();
        return $this->ok(['authenticated' => (bool)$user, 'user' => $user, 'csrf_token' => $this->csrf(),
            'admin_base' => $branding['admin_base'] ?? '/admin', 'branding' => $branding,
            'registration' => $this->publicRegistration()]);
    }

    public function login(): Json
    {
        $turnstile = $this->app->siteConfigService->registration();
        if (!empty($turnstile['turnstile_enabled'])) {
            $verified = $this->app->turnstileService->verify(
                (string)$this->request->param('cf-turnstile-response', ''), $turnstile, 'login'
            );
            if (!$verified['status']) return $this->fail($verified['msg'], 422);
        }
        $result = $this->app->authService->userLogin((string)$this->request->param('username', ''), (string)$this->request->param('password', ''));
        if (!$result['status']) return $this->fail($result['msg'], (int)($result['http_status'] ?? 401));
        Session::set('frontend_csrf', bin2hex(random_bytes(32)));
        return $this->ok(['user' => $this->user(), 'csrf_token' => $this->csrf()], $result['msg']);
    }

    public function register(): Json
    {
        // AuthService delegates to RegistrationService, which performs the
        // single-use Turnstile verification. Do not consume the token here.
        $result = $this->app->authService->userRegister((string)$this->request->param('username', ''), (string)$this->request->param('password', ''));
        return $result['status'] ? $this->ok(null, $result['msg']) : $this->fail($result['msg']);
    }

    public function logout(): Json
    { Session::delete('user_id'); Session::delete('frontend_csrf'); Session::regenerate(true); return $this->ok(null, '已安全退出'); }

    public function dashboard(): Json
    {
        $owner = $this->adminScope() ? 0 : (int)$this->user()['id'];
        $tasks = Db::name('account'); if ($owner) $tasks->where('owner', $owner);
        $failed = Db::name('account'); if ($owner) $failed->where('owner', $owner);
        return $this->ok(['accounts' => $this->app->accountService->countAll($owner),
            'shares' => $this->app->shareService->countAll($owner), 'users' => $this->adminScope() ? $this->app->userService->countAll() : null,
            'running_tasks' => $tasks->where('task_status', 'running')->count(), 'failed_tasks' => $failed->where('task_status', 'failed')->count()]);
    }

    public function profile(): Json { return $this->ok($this->user()); }

    public function updateProfile(): Json
    {
        $user = (new User())->fetch((int)Session::get('user_id'));
        if (!$user) return $this->fail('用户不存在', 404);
        $username = trim((string)$this->request->param('username', ''));
        if ($username === '') return $this->fail('用户名不能为空', 422, ['username' => '请输入用户名']);
        $result = $user->updateUser([
            'id' => $user->id, 'username' => $username,
            'password' => (string)$this->request->param('password', ''),
        ]);
        return $result['status'] ? $this->ok($this->user(), $result['msg']) : $this->fail($result['msg']);
    }

    public function notifications(): Json
    {
        $user = $this->user();
        if (!$user) return $this->fail('用户不存在', 404);
        $settings = $this->app->userService->getNotifyMethods((int)$user['id']);
        return $this->ok([
            'tg_bot_token' => (string)($settings['tg_bot_token'] ?? ''),
            'tg_chat_id' => (string)($settings['tg_chat_id'] ?? ''),
            'tg_notify_mode' => (string)($settings['tg_notify_mode'] ?? 'all'),
        ]);
    }

    public function updateNotifications(): Json
    {
        $user = (new User())->fetch((int)Session::get('user_id'));
        if (!$user) return $this->fail('用户不存在', 404);
        $mode = (string)$this->request->param('tg_notify_mode', 'all');
        if (!in_array($mode, ['success', 'failure', 'all'], true)) {
            return $this->fail('Telegram 推送策略无效', 422, ['tg_notify_mode' => '请选择有效的推送策略']);
        }
        $result = $user->updateUser([
            'id' => $user->id,
            'tg_bot_token' => trim((string)$this->request->param('tg_bot_token', '')),
            'tg_chat_id' => trim((string)$this->request->param('tg_chat_id', '')),
            'tg_notify_mode' => $mode,
        ]);
        return $result['status'] ? $this->ok(null, '消息推送设置已保存') : $this->fail($result['msg']);
    }

    public function accounts(): Json
    {
        $perPage = $this->perPage();
        $p = $this->adminScope() ? $this->app->accountService->fetchAll($perPage) : $this->app->accountService->fetchByOwner((int)$this->user()['id'], $perPage);
        [$items, $meta] = $this->page($p, fn($row) => $this->safeAccount((array)$row));
        return $this->ok($items, '获取成功', $meta);
    }

    public function account($id): Json
    {
        $account = (new Account())->fetch((int)$id);
        if (!$account) return $this->fail('账号不存在', 404);
        if (!$this->adminScope() && (int)$account->owner !== (int)$this->user()['id']) return $this->fail('无权访问', 403);
        // 登录会话 + 归属校验已经通过，详情直接回填可查看/可编辑的凭据；
        // 字段留空仍表示“保持原值”（新建或部分保存时不做覆盖）。
        $row = array_merge($this->safeAccount($account->toArray()), $this->credentialFields($account));
        $row['schedule'] = \app\service\AutomationScheduleConfig::resolve($account->toArray());
        return $this->ok($row);
    }

    /** 账号凭据（Apple 密码、密保问题与答案、救援邮箱密码）。调用前必须已校验登录会话与归属。 */
    private function credentialFields(Account $account): array
    {
        $raw = $account->toArray();
        return [
            'password' => (string)($raw['password'] ?? ''),
            'question1' => (string)($raw['question1'] ?? ''),
            'answer1' => (string)($raw['answer1'] ?? ''),
            'question2' => (string)($raw['question2'] ?? ''),
            'answer2' => (string)($raw['answer2'] ?? ''),
            'question3' => (string)($raw['question3'] ?? ''),
            'answer3' => (string)($raw['answer3'] ?? ''),
            'mailbox_password' => $this->app->mailboxCredentialService->decryptMailboxPassword($account),
        ];
    }

    public function revealAccountPassword($id): Json
    {
        $account = (new Account())->fetch((int)$id);
        if (!$account) return $this->fail('账号不存在', 404);
        if (!$this->adminScope() && (int)$account->owner !== (int)$this->user()['id']) return $this->fail('无权访问', 403);
        $actorId = (int)Session::get('user_id', 0);
        if (Db::query("SHOW TABLES LIKE 'credential_audit'")) {
            Db::name('credential_audit')->insert([
                'actor_user_id' => $actorId, 'account_id' => (int)$account->id,
                'action' => 'reveal_password', 'ip_address' => mb_substr(getUserIP(), 0, 64),
                'created_at' => date('Y-m-d H:i:s'),
            ]);
        }
        return $this->ok($this->credentialFields($account), '凭据仅在本次响应中返回');
    }

    public function mailboxTest(): Json
    {
        $id = (int)$this->request->param('account_id', 0);
        $existing = $id > 0 ? (new Account())->fetch($id) : null;
        if ($id > 0 && !$existing) return $this->fail('账号不存在', 404);
        if ($existing && !$this->adminScope() && (int)$existing->owner !== (int)$this->user()['id']) {
            return $this->fail('无权测试该账号的救援邮箱', 403);
        }
        $draft = $this->app->mailboxCredentialService->transientAutomationConfig([
            'recovery_email' => (string)$this->request->param('recovery_email', $existing->recovery_email ?? ''),
            'mailbox_host' => (string)$this->request->param('mailbox_host', $existing->mailbox_host ?? ''),
            'mailbox_port' => (int)$this->request->param('mailbox_port', $existing->mailbox_port ?? 993),
            'mailbox_username' => (string)$this->request->param('mailbox_username', $existing->mailbox_username ?? ''),
            'mailbox_password' => (string)$this->request->param('mailbox_password', ''),
            'mailbox_tls' => (bool)$this->request->param('mailbox_tls', true),
        ], $existing);
        if (empty($draft['status'])) return $this->fail((string)($draft['msg'] ?? '救援邮箱配置无效'));
        $result = $this->app->backendService->testMailboxConfig((array)$draft['data']);
        return !empty($result['status'])
            ? $this->ok(null, (string)$result['msg'])
            : $this->fail((string)($result['msg'] ?? '救援邮箱测试失败'), 503);
    }

    public function startEmailPasswordRecovery($id): Json
    {
        if (!$this->isAdmin()) return $this->fail('无权访问', 403);
        return $this->manualRecoveryResponse(
            $this->app->manualRecoveryService->start((int)$id, 'email_password')
        );
    }

    public function startSecurityRollback($id): Json
    {
        if (!$this->isAdmin()) return $this->fail('无权访问', 403);
        return $this->manualRecoveryResponse(
            $this->app->manualRecoveryService->start((int)$id, 'security_rollback')
        );
    }

    public function recoveryStatus(string $runId): Json
    {
        if (!$this->isAdmin()) return $this->fail('无权访问', 403);
        return $this->manualRecoveryResponse($this->app->manualRecoveryService->status($runId));
    }

    public function recoveryHistory($id): Json
    {
        if (!$this->isAdmin()) return $this->fail('无权访问', 403);
        return $this->manualRecoveryResponse($this->app->manualRecoveryService->history((int)$id));
    }

    private function manualRecoveryResponse(array $result): Json
    {
        if (!empty($result['status'])) {
            return $this->ok($result['data'] ?? null, (string)($result['msg'] ?? '操作成功'));
        }
        return $this->fail(
            (string)($result['msg'] ?? '邮件恢复操作失败'),
            (int)($result['http_status'] ?? 409)
        );
    }

    public function automationDefaults(): Json
    {
        return $this->ok(\app\service\AutomationScheduleConfig::globals());
    }

    public function accountSchedule($id): Json
    {
        $account = (new Account())->fetch((int)$id);
        if (!$account) return $this->fail('账号不存在', 404);
        if (!$this->adminScope() && (int)$account->owner !== (int)$this->user()['id']) return $this->fail('无权访问', 403);
        $result = $this->app->backendService->setCurl((string)env('backend.api_url'), (string)env('backend.token'), 'scheduleStatus', (int)$id);
        return $result['status'] ? $this->ok($result['data'] ?? []) : $this->fail('调度状态暂不可用', 503);
    }

    public function saveAccount($id = 0): Json
    {
        $id = (int)$id; $admin = $this->adminScope(); $currentUser = $this->user();
        $accountModel = new Account(); $existing = $id ? $accountModel->fetch($id) : null;
        if ($id && !$existing) return $this->fail('账号不存在', 404);
        if ($existing && !$admin && (int)$existing->owner !== (int)$currentUser['id']) return $this->fail('无权操作', 403);
        $owner = $existing ? (int)$existing->owner : (int)$currentUser['id'];
        if (!Db::name('user')->where('id', $owner)->find()) return $this->fail('所属用户不存在', 422, ['owner' => '请选择有效用户']);
        if (!$id && !$admin) { $check = $this->app->entitlementService->canAddAccount($owner); if (!$check['status']) return $this->fail($check['msg'], 409); }
        $password = (string)$this->request->param('password', '');
        if ($password === '' && $existing) $password = (string)$existing->password;
        $old = static fn($field, $fallback = '') => $existing ? ($existing->$field ?? $fallback) : $fallback;
        $answer = function (string $field) use ($existing, $old): string {
            $value = (string)$this->request->param($field, '');
            return $existing && $value === '' ? (string)$old($field) : $value;
        };
        $data = ['username' => trim((string)$this->request->param('username', $old('username'))), 'password' => $password,
            'remark' => (string)$this->request->param('remark', $old('remark')), 'frontend_remark' => $this->app->htmlSanitizerService->sanitize((string)$this->request->param('frontend_remark', $old('frontend_remark'))),
            'dob' => (string)$this->request->param('dob', $old('dob')), 'question1' => (string)$this->request->param('question1', $old('question1')),
            'answer1' => $answer('answer1'), 'question2' => (string)$this->request->param('question2', $old('question2')),
            'answer2' => $answer('answer2'), 'question3' => (string)$this->request->param('question3', $old('question3')),
            'answer3' => $answer('answer3'), 'share_link' => (string)$this->request->param('share_link', $old('share_link', secure_token(8))),
            'check_interval' => (int)$this->request->param('check_interval', $old('check_interval', 30)), 'min_manual_unlock' => (int)$this->request->param('min_manual_unlock', $old('min_manual_unlock', 0)),
            'enable_check_password_correct' => (bool)$this->request->param('enable_check_password_correct', $old('enable_check_password_correct', false)),
            'enable_delete_devices' => (bool)$this->request->param('enable_delete_devices', $old('enable_delete_devices', false)),
            'enable_auto_update_password' => (bool)$this->request->param('enable_auto_update_password', $old('enable_auto_update_password', false)),
            'enable_auto_disable_two_factor' => (bool)$this->request->param('enable_auto_disable_two_factor', $old('enable_auto_disable_two_factor', false)),
            'enable_auto_restore_profile' => (bool)$this->request->param('enable_auto_restore_profile', $old('enable_auto_restore_profile', false)),
            'profile_name' => (string)$this->request->param('profile_name', $old('profile_name')),
            'profile_family_name' => (string)$this->request->param('profile_family_name', $old('profile_family_name')),
            'profile_middle_name' => (string)$this->request->param('profile_middle_name', $old('profile_middle_name')),
            'profile_given_name' => (string)$this->request->param('profile_given_name', $old('profile_given_name')),
            'profile_country' => (string)$this->request->param('profile_country', $old('profile_country')),
            'profile_language' => (string)$this->request->param('profile_language', $old('profile_language')),
            'profile_baseline_confirmed_at' => $old('profile_baseline_confirmed_at', null),
            'recovery_methods' => (array)$this->request->param('recovery_methods', $old('recovery_methods', [])),
            'enable' => (bool)$this->request->param('enable', $old('enable', true)), 'proxy_id' => (int)$this->request->param('proxy_id', $old('proxy_id', 0)), 'owner' => $owner];
        $baselineChanged = $existing && array_filter([
            'dob', 'profile_family_name', 'profile_middle_name', 'profile_given_name',
            'profile_country', 'profile_language',
        ], static fn($field) => (string)($data[$field] ?? '') !== (string)($existing->$field ?? ''));
        $baselineComplete = ($data['profile_family_name'] !== '' || $data['profile_given_name'] !== '')
            && preg_match('/^\d{4}-\d{2}-\d{2}$/', $data['dob'])
            && trim($data['profile_country']) !== '' && trim($data['profile_language']) !== '';
        $profileConfigTouched = $this->request->has('enable_auto_restore_profile')
            || $this->request->has('profile_family_name') || $this->request->has('profile_given_name')
            || $this->request->has('profile_country') || $this->request->has('profile_language')
            || $this->request->has('dob');
        if ($profileConfigTouched && $data['enable_auto_restore_profile'] && !$baselineComplete) {
            return $this->fail('启用自动恢复资料前，请完整填写姓名、出生日期、国家/地区和语言', 422,
                ['profile_baseline' => '姓名、出生日期、国家/地区和语言不能为空']);
        }
        if ($baselineComplete) {
            $data['profile_name'] = trim($data['profile_family_name'] . $data['profile_middle_name'] . $data['profile_given_name']);
            $data['profile_baseline_confirmed_at'] = date('Y-m-d H:i:s');
            $data['profile_recorded_at'] = $data['profile_baseline_confirmed_at'];
        } elseif ($baselineChanged || !$existing) {
            $data['profile_baseline_confirmed_at'] = null;
        }
        if ($data['proxy_id'] > 0 && !Db::name('proxy')->where('id', $data['proxy_id'])->where(function ($query) use ($owner) { $query->where('owner', $owner)->whereOr('owner', 0); })->find()) {
            return $this->fail('代理节点不存在或不属于当前用户', 422, ['proxy_id' => '请选择有效的代理节点']);
        }
        // A cycle-only/ordinary partial save must not erase mailbox configuration.
        $mailboxInput = ['recovery_methods' => $data['recovery_methods']];
        if ($existing) {
            foreach (['recovery_email', 'mailbox_host', 'mailbox_port', 'mailbox_username', 'mailbox_tls', 'enable_email_recovery'] as $field) {
                $mailboxInput[$field] = $existing->$field;
            }
        }
        $mailboxInput = array_replace($mailboxInput, (array)$this->request->param());
        $mailbox = $this->app->mailboxCredentialService->mergeInput($mailboxInput, $existing);
        if (!$mailbox['status']) return $this->fail($mailbox['msg']);
        $data = array_merge($data, $mailbox['data']);
        if ($this->request->has('automation_schedule')) {
            $rawSchedule = (array)$this->request->param('automation_schedule');
            $rawFeatures = is_array($rawSchedule['features'] ?? null) ? $rawSchedule['features'] : $rawSchedule;
            if (!array_key_exists('device_cleanup', $rawFeatures)) {
                // A four-feature client cannot knowingly authorize the newly
                // introduced destructive operation, even if a stale DB flag
                // happens to be true.
                $data['enable_delete_devices'] = false;
            }
            try { $data['automation_schedule'] = \app\service\AutomationScheduleConfig::normalize($rawSchedule); }
            catch (\InvalidArgumentException $e) { return $this->fail($e->getMessage(), 422, ['automation_schedule' => $e->getMessage()]); }
        }
        $result = $id ? $accountModel->updateAccount($id, $data) : $accountModel->addAccount($data, !$admin);
        return $result['status'] ? $this->ok(null, $result['msg']) : $this->fail($result['msg'], 409);
    }

    public function deleteAccount($id): Json
    {
        $account = (new Account())->fetch((int)$id); if (!$account) return $this->fail('账号不存在', 404);
        if (!$this->adminScope() && (int)$account->owner !== (int)$this->user()['id']) return $this->fail('无权操作', 403);
        $result = $account->deleteAccount((int)$id);
        return $result['status'] ? $this->ok(null, $result['msg']) : $this->fail($result['msg'], 409);
    }

    /**
     * 行内快速启停：仅切换账号启用状态并同步自动化调度任务，
     * 不触碰邮箱恢复等其它配置（避免与账号全量保存的校验耦合）。
     */
    public function enableAccount($id): Json
    {
        $id = (int)$id;
        $account = (new Account())->fetch($id);
        if (!$account) return $this->fail('账号不存在', 404);
        if (!$this->adminScope() && (int)$account->owner !== (int)$this->user()['id']) return $this->fail('无权操作', 403);
        $enable = (bool)$this->request->param('enable', false);
        $service = $this->app->automationTaskService;
        $result = $enable
            ? $service->enableTask($id, (int)$account->owner, 'system')
            : $service->disableTask($id, (int)$account->owner, 'system');
        return $result['status']
            ? $this->ok(null, $enable ? '账号已启用，任务已加入调度' : '账号已停用，任务已暂停')
            : $this->fail($enable ? '启用失败：' . $result['msg'] : '停用失败：' . $result['msg'], 409);
    }

    public function tasks(): Json
    {
        $filters = ['status' => (string)$this->request->param('status', ''), 'keyword' => (string)$this->request->param('keyword', ''), 'owner' => (string)$this->request->param('owner', '')];
        $perPage = $this->perPage();
        $p = $this->adminScope() ? $this->app->automationTaskService->fetchAll($filters, $perPage) : $this->app->automationTaskService->fetchByOwner((int)$this->user()['id'], $perPage);
        [$items, $meta] = $this->page($p, fn($row) => $this->safeAccount((array)$row)); return $this->ok($items, '获取成功', $meta);
    }

    public function records(): Json
    { [$items, $meta] = $this->page($this->app->unlockService->fetchRecord($this->adminScope() ? 0 : (int)$this->user()['id'], $this->perPage())); return $this->ok($items, '获取成功', $meta); }

    public function shares(): Json
    {
        $filters = [
            'usage' => (string)$this->request->param('usage', ''),
            'validity' => (string)$this->request->param('validity', ''),
            'keyword' => (string)$this->request->param('keyword', ''),
        ];
        $perPage = $this->perPage();
        $p = $this->adminScope() ? $this->app->shareService->fetchAll($filters, $perPage) : $this->app->shareService->fetchByOwner((int)$this->user()['id'], $filters, $perPage);
        [$items, $meta] = $this->page($p, fn($row) => $this->safeShare((array)$row)); return $this->ok($items, '获取成功', $meta);
    }

    /** Share groups are reusable link settings. Editing one updates every linked share. */
    public function shareGroups(): Json
    {
        $uid = (int)$this->user()['id'];
        $query = Db::name('share_group')->alias('g')
            ->leftJoin('share_template t', 't.id = g.template_id')
            ->field("g.*,IF(t.owner={$uid},t.name,NULL) template_name,(SELECT COUNT(*) FROM share s WHERE s.group_id=g.id) link_count")
            ->order('g.id', 'desc');
        if (!$this->adminScope()) $query->where('g.owner', $uid);
        $rows = $query->select()->toArray();
        foreach ($rows as &$row) {
            $row['account_list'] = array_values(array_filter(array_map('intval', explode(',', (string)$row['account_list']))));
            $row['password_configured'] = (string)($row['password'] ?? '') !== '';
            unset($row['password']);
        }
        return $this->ok($rows, '获取成功');
    }

    public function saveShareGroup($id = 0): Json
    {
        $id = (int)$id; $uid = (int)$this->user()['id'];
        $existing = $id ? Db::name('share_group')->where('id', $id)->find() : null;
        if ($id && !$existing) return $this->fail('分享组不存在', 404);
        if ($existing && !$this->adminScope() && (int)$existing['owner'] !== $uid) return $this->fail('无权操作', 403);
        $owner = $existing ? (int)$existing['owner'] : $uid;
        $name = trim((string)$this->request->param('name', ''));
        if ($name === '' || mb_strlen($name) > 100) return $this->fail('组名称应为 1 至 100 个字符', 422);
        $accountIds = array_values(array_unique(array_filter(array_map('intval', (array)$this->request->param('account_list', [])))));
        if (!$accountIds) return $this->fail('请至少选择一个账号', 422);
        if (Db::name('account')->where('owner', $owner)->whereIn('id', $accountIds)->count() !== count($accountIds)) return $this->fail('包含无权使用的账号', 403);
        $templateId = max(0, (int)$this->request->param('template_id', 0));
        $template = $templateId ? $this->ownedTemplate($templateId, $owner) : null;
        if ($templateId && !$template) return $this->fail('请选择当前用户自己的分享模板', 422);
        $password = (string)$this->request->param('password', '');
        if ($password === '' && $existing) $password = (string)($existing['password'] ?? '');
        elseif ($password !== '') $password = (string)$this->app->shareAccessService->hashPassword($password);
        $duration = max(0, min(525600, (int)$this->request->param('duration_minutes', 0)));
        $expire = $duration > 0 ? null : ($this->request->param('expire') ?: null);
        $data = ['owner' => $owner, 'name' => $name, 'template_id' => $templateId ?: null,
            'account_list' => implode(',', $accountIds), 'password' => $password ?: null,
            'duration_minutes' => $duration, 'expire' => $expire,
            'base_url' => trim((string)$this->request->param('base_url', '')) ?: null,
            'access_tracking' => (int)(bool)$this->request->param('access_tracking', true),
            'update_time' => date('Y-m-d H:i:s')];
        Db::transaction(function () use ($id, $data, $template, $existing): void {
            if ($id) Db::name('share_group')->where('id', $id)->update($data);
            else { $data['create_time'] = date('Y-m-d H:i:s'); $id = Db::name('share_group')->insertGetId($data); }
            $shareData = ['account_list' => $data['account_list'], 'password' => $data['password'], 'duration_minutes' => $data['duration_minutes'],
                'base_url' => $data['base_url'], 'access_tracking' => $data['access_tracking'], 'template_id' => $data['template_id']];
            $timingChanged = !$existing || (int)$existing['duration_minutes'] !== (int)$data['duration_minutes']
                || (string)($existing['expire'] ?? '') !== (string)($data['expire'] ?? '');
            if ($timingChanged) { $shareData['expire'] = $data['expire']; $shareData['activated_at'] = null; }
            if ($template) { $shareData['html'] = $template['html']; $shareData['notice_html'] = $template['notice_html']; }
            Db::name('share')->where('group_id', $id)->update($shareData);
        });
        return $this->ok(null, $existing ? '分享组及所属链接已更新' : '分享组已创建');
    }

    public function deleteShareGroup($id): Json
    {
        $id = (int)$id; $row = Db::name('share_group')->where('id', $id)->find();
        if (!$row) return $this->fail('分享组不存在', 404);
        if (!$this->adminScope() && (int)$row['owner'] !== (int)$this->user()['id']) return $this->fail('无权操作', 403);
        Db::transaction(function () use ($id): void { Db::name('share')->where('group_id', $id)->update(['group_id' => null]); Db::name('share_group')->where('id', $id)->delete(); });
        return $this->ok(null, '分享组已删除，已有链接继续保留');
    }

    private function safeShare(array $row): array
    {
        unset($row['password'], $row['html']);
        $row['group_name'] = !empty($row['group_id']) ? (string)(Db::name('share_group')->where('id', (int)$row['group_id'])->value('name') ?? '未分组') : '未分组';
        $row['remark'] = (string)($row['remark'] ?? '');
        $row['account_count'] = count(array_values(array_filter(array_map('intval', explode(',', (string)($row['account_list'] ?? ''))))));
        $expire = (string)($row['expire'] ?? '');
        if ($expire !== '' && strtotime($expire) < time()) {
            $row['validity'] = 'expired';
            $row['expiry_label'] = '已过期（' . $expire . '）';
        } elseif (!empty($row['duration_minutes'])) {
            $row['validity'] = 'active';
            $row['expiry_label'] = '首次访问后 ' . (int)$row['duration_minutes'] . ' 分钟';
        } elseif ($expire !== '') {
            $row['validity'] = 'active';
            $row['expiry_label'] = '至 ' . $expire;
        } else {
            $row['validity'] = 'active';
            $row['expiry_label'] = '永不过期';
        }
        if (empty($row['access_tracking'])) {
            $row['access_status'] = 'unknown';
        } elseif (($row['first_access_at'] ?? '') !== '') {
            $row['access_status'] = 'used';
        } else {
            $row['access_status'] = 'unused';
        }
        return $row;
    }

    public function share($id): Json
    {
        $share = (new SharePage())->fetch((int)$id); if (!$share) return $this->fail('分享页面不存在', 404);
        if (!$this->adminScope() && (int)$share->owner !== (int)$this->user()['id']) return $this->fail('无权访问', 403);
        $data = $share->toArray(); $data['password_configured'] = $data['password'] !== ''; unset($data['password'], $data['html']);
        return $this->ok($data);
    }

    public function saveShare($id = 0): Json
    {
        $id = (int)$id; $admin = $this->adminScope(); $model = new SharePage(); $existing = $id ? $model->fetch($id) : null;
        if ($id && !$existing) return $this->fail('分享页面不存在', 404);
        if ($existing && !$admin && (int)$existing->owner !== (int)$this->user()['id']) return $this->fail('无权操作', 403);
        $owner = $existing ? (int)$existing->owner : (int)$this->user()['id'];
        if (!Db::name('user')->where('id', $owner)->find()) return $this->fail('所属用户不存在', 422, ['owner' => '请选择有效用户']);
        if (!$id && !$admin) { $check = $this->app->entitlementService->canAddShare($owner); if (!$check['status']) return $this->fail($check['msg'], 409); }
        $requestedGroupId = max(0, (int)$this->request->param('group_id', $existing ? ($existing->group_id ?? 0) : 0));
        $requestedGroup = $requestedGroupId ? Db::name('share_group')->where('id', $requestedGroupId)->where('owner', $owner)->find() : null;
        $accountIds = $requestedGroup ? array_values(array_filter(array_map('intval', explode(',', (string)$requestedGroup['account_list'])))) : array_values(array_unique(array_filter(array_map('intval', (array)$this->request->param('account_list', [])))));
        if (!$accountIds) return $this->fail('请至少选择一个账号', 422, ['account_list' => '请选择账号']);
        $ownedCount = Db::name('account')->where('owner', $owner)->whereIn('id', $accountIds)->count();
        if ($ownedCount !== count($accountIds)) return $this->fail('包含无权使用的账号', 403);
        $password = (string)$this->request->param('password', '');
        if ($password === '' && $existing) $password = (string)$existing->password;
        elseif ($password !== '') $password = (string)$this->app->shareAccessService->hashPassword($password);
        $shareLink = (string)$this->request->param('share_link', '');
        if ($shareLink === '') $shareLink = $existing ? (string)$existing->share_link : secure_token(16);
        $htmlInput = (string)$this->request->param('html', '');
        $html = $this->app->htmlSanitizerService->sanitize($htmlInput !== '' ? $htmlInput : ($existing ? (string)$existing->html : $this->defaultShareHtml((int)$owner)));
        $noticeInput = (string)$this->request->param('notice_html', '');
        $noticeHtml = $this->app->htmlSanitizerService->sanitize($noticeInput !== '' ? $noticeInput : ($existing ? (string)$existing->notice_html : $this->defaultShareNotice((int)$owner)));
        $groupId = $requestedGroupId;
        $templateId = max(0, (int)$this->request->param('template_id', $existing ? ($existing->template_id ?? 0) : 0));
        $group = $groupId ? Db::name('share_group')->where('id', $groupId)->where('owner', $owner)->find() : null;
        if ($groupId && !$group) return $this->fail('分享组不存在或不属于当前用户', 422);
        if ($group) {
            $accountIds = array_values(array_filter(array_map('intval', explode(',', (string)$group['account_list']))));
            $password = (string)($group['password'] ?? ''); $templateId = (int)($group['template_id'] ?? 0);
            $groupTemplate = $templateId ? $this->ownedTemplate($templateId, $owner) : null;
            if ($templateId && !$groupTemplate) return $this->fail('分享组关联的模板已不可用', 422);
            $html = $groupTemplate ? (string)$groupTemplate['html'] : $html;
            $noticeHtml = $groupTemplate ? (string)$groupTemplate['notice_html'] : $noticeHtml;
        } elseif ($templateId) {
            $template = $this->ownedTemplate($templateId, $owner);
            if (!$template) return $this->fail('请选择当前用户自己的分享模板', 422);
            $html = (string)$template['html']; $noticeHtml = (string)$template['notice_html'];
        }
        $data = ['share_link' => $shareLink,
            'account_list' => implode(',', $accountIds), 'password' => $password === '' ? null : $password, 'owner' => $owner,
            'html' => $html,
            'notice_html' => $noticeHtml,
            'group_id' => $groupId ?: null, 'template_id' => $templateId ?: null,
            'remark' => (string)$this->request->param('remark', ''), 'expire' => $this->request->param('expire') ?: null,
            'duration_minutes' => $group ? (int)$group['duration_minutes'] : (int)$this->request->param('duration_minutes', 0),
            'access_tracking' => $group ? (bool)$group['access_tracking'] : (bool)$this->request->param('access_tracking', true)];
        if ($group) { $data['expire'] = $group['expire']; $data['base_url'] = $group['base_url']; }
        try { $result = $id ? $model->updateSharePage($id, $data) : $model->addSharePage($data, !$admin); }
        catch (\InvalidArgumentException $e) { return $this->fail($e->getMessage()); }
        return $result['status'] ? $this->ok(['share_link' => $shareLink, 'url' => $this->request->domain() . '/share/' . $shareLink], $result['msg']) : $this->fail($result['msg'], 409);
    }

    public function deleteShare($id): Json
    {
        $share = (new SharePage())->fetch((int)$id); if (!$share) return $this->fail('分享页面不存在', 404);
        if (!$this->adminScope() && (int)$share->owner !== (int)$this->user()['id']) return $this->fail('无权操作', 403);
        $result = $share->deleteSharePage((int)$id);
        return $result['status'] ? $this->ok(null, $result['msg']) : $this->fail($result['msg'], 409);
    }

    public function shareBatchCreate(): Json
    {
        $admin = $this->adminScope(); $currentUser = $this->user();
        $owner = (int)$currentUser['id'];
        if (!Db::name('user')->where('id', $owner)->find()) return $this->fail('所属用户不存在', 422, ['owner' => '请选择有效用户']);
        $groupId = max(0, (int)$this->request->param('group_id', 0));
        $group = $groupId ? Db::name('share_group')->where('id', $groupId)->where('owner', $owner)->find() : null;
        if ($groupId && !$group) return $this->fail('分享组不存在或不属于当前用户', 422);
        $accountIds = $group
            ? array_values(array_filter(array_map('intval', explode(',', (string)$group['account_list']))))
            : array_values(array_unique(array_filter(array_map('intval', (array)$this->request->param('account_ids', [])))));
        $quantity = (int)$this->request->param('quantity', 0);
        if (!$accountIds || $quantity < 1 || $quantity > 100) return $this->fail('请选择账号，单次生成数量为 1 至 100', 422);
        $ownedCount = Db::name('account')->where('owner', $owner)->whereIn('id', $accountIds)->count();
        if ($ownedCount !== count($accountIds)) return $this->fail('包含无权使用的账号', 403);
        if (!$admin) { $check = $this->app->entitlementService->canAddShares($owner, $quantity); if (!$check['status']) return $this->fail($check['msg'], 409); }
        $baseUrl = $group ? trim((string)($group['base_url'] ?? '')) : trim((string)$this->request->param('base_url', ''));
        if ($baseUrl !== '' && !preg_match('#^https?://[^\s/]+#i', $baseUrl)) return $this->fail('自定义分享网址需以 http:// 或 https:// 开头', 422);
        if (mb_strlen($baseUrl) > 255) return $this->fail('自定义分享网址不能超过 255 个字符', 422);
        $prefix = $baseUrl !== '' ? rtrim($baseUrl, '/') : $this->request->domain();
        try {
            $expiry = $group
                ? ['expire' => $group['expire'] ?: null, 'duration_minutes' => (int)$group['duration_minutes'], 'activated_at' => null]
                : $this->app->shareService->batchExpiry($this->request->param());
            $linkDefaults = [];
            if ($group) {
                $templateId = (int)($group['template_id'] ?? 0);
                $template = $templateId ? $this->ownedTemplate($templateId, $owner) : null;
                if ($templateId && !$template) throw new \RuntimeException('分享组关联的模板已不可用');
                $linkDefaults = ['group_id' => $groupId, 'template_id' => $templateId ?: null,
                    'password' => $group['password'] ?: null, 'access_tracking' => (int)$group['access_tracking'],
                    'html' => $template ? (string)$template['html'] : $this->defaultShareHtml($owner),
                    'notice_html' => $template ? (string)$template['notice_html'] : $this->defaultShareNotice($owner)];
            }
            $links = $this->app->shareService->createBatch($owner, $accountIds, $quantity,
                $expiry, (string)$this->request->param('remark', ''), $baseUrl, $linkDefaults, !$admin);
            return $this->ok(array_map(fn($link) => $prefix . '/share/' . $link, $links), '批量生成成功');
        } catch (\Throwable $e) { return $this->fail($e->getMessage(), 422); }
    }

    public function shareBatchDelete(): Json
    {
        $ids = array_values(array_unique(array_filter(array_map('intval', (array)$this->request->param('ids', [])))));
        if (!$ids) return $this->fail('请选择要删除的分享链接', 422);
        $query = SharePage::whereIn('id', $ids);
        if (!$this->adminScope()) $query->where('owner', (int)$this->user()['id']);
        if ($query->count() !== count($ids)) return $this->fail('包含不存在或无权删除的分享链接', 403);
        try { $result = $this->app->shareService->deleteBatch($ids); return $this->ok(null, $result['msg']); }
        catch (\Throwable $e) { return $this->fail($e->getMessage(), 422); }
    }

    private function ensureSystemDefaultTemplate(): void
    {
        $system = Db::name('share_template')->where('owner', 0)->order('id', 'asc')->find();
        if (!$system) {
            $systemId = Db::name('share_template')->insertGetId([
                'owner' => 0, 'name' => '默认模板', 'html' => $this->app->shareService->defaultHtmlTemplate(),
                'notice_html' => $this->app->shareService->defaultNoticeHtml(),
                'is_default' => 1, 'create_time' => date('Y-m-d H:i:s'), 'update_time' => date('Y-m-d H:i:s'),
            ]);
            $system = ['id' => $systemId, 'is_default' => 1];
        } elseif ((string)($system['notice_html'] ?? '') === '') {
            Db::name('share_template')->where('id', $system['id'])->update([
                'notice_html' => $this->app->shareService->defaultNoticeHtml(), 'update_time' => date('Y-m-d H:i:s'),
            ]);
        }
        if (!Db::name('share_template')->where('owner', 0)->where('is_default', 1)->find()) {
            Db::name('share_template')->where('id', (int)$system['id'])->update(['is_default' => 1]);
        }
    }

    private function ownedTemplate(int $id, int $owner): ?array
    {
        if ($id <= 0 || $owner <= 0) return null;
        $row = Db::name('share_template')->where('id', $id)->where('owner', $owner)->find();
        return $row ?: null;
    }

    /** Ensure a user with private templates always has one private default. */
    private function ensureUserDefaultTemplate(int $owner): void
    {
        if ($owner <= 0 || Db::name('share_template')->where('owner', $owner)->where('is_default', 1)->find()) return;
        $id = Db::name('share_template')->where('owner', $owner)->order('id', 'asc')->value('id');
        if ($id) Db::name('share_template')->where('id', (int)$id)->update(['is_default' => 1]);
    }

    /**
     * 默认模板解析链：账号自己的默认模板 > 系统默认模板。
     * $field 决定用哪个字段判断“模板是否有内容”（html 为页面内容，notice_html 为使用说明）。
     */
    private function resolveDefaultTemplateId(int $owner, string $field = 'html'): int
    {
        foreach ($owner > 0 ? [$owner, 0] : [0] as $scope) {
            $row = Db::name('share_template')->where('owner', $scope)->where('is_default', 1)
                ->where($field, '<>', '')->order('id', 'asc')->find();
            if ($row) return (int)$row['id'];
        }
        return 0;
    }

    /** 新建分享时解析默认 HTML：账号自己的默认模板 > 系统默认模板 > 内置默认。 */
    private function defaultShareHtml(int $owner): string
    {
        $id = $this->resolveDefaultTemplateId($owner, 'html');
        $html = $id ? (string)(Db::name('share_template')->where('id', $id)->value('html') ?? '') : '';
        return $html !== '' ? $html : $this->app->shareService->defaultHtmlTemplate();
    }

    private function defaultShareNotice(int $owner): string
    {
        $id = $this->resolveDefaultTemplateId($owner, 'notice_html');
        $notice = $id ? (string)(Db::name('share_template')->where('id', $id)->value('notice_html') ?? '') : '';
        return $notice !== '' ? $notice : $this->app->shareService->defaultNoticeHtml();
    }

    public function templates(): Json
    {
        $this->ensureSystemDefaultTemplate();
        $currentUser = $this->user(); $uid = (int)$currentUser['id'];
        $this->ensureUserDefaultTemplate($uid);
        $rows = Db::name('share_template')->where('owner', $uid)
            ->order('is_default desc, id asc')->select()->toArray();
        // 与 defaultShareHtml/defaultShareNotice 完全一致的可选默认模板，供前端标记“默认”并预选。
        $defaultId = $this->resolveDefaultTemplateId($uid, 'html');
        foreach ($rows as &$row) {
            $row['owner_name'] = '我的模板';
            $row['effective_default'] = (int)$row['id'] === $defaultId;
        }
        unset($row);
        return $this->ok($rows, '获取成功');
    }

    public function saveTemplate($id = 0): Json
    {
        $currentUser = $this->user(); $uid = (int)$currentUser['id'];
        $name = trim((string)$this->request->param('name', ''));
        $html = $this->app->htmlSanitizerService->sanitize((string)$this->request->param('html', ''));
        if ($name === '') return $this->fail('请输入模板名称', 422);
        if ($html === '') return $this->fail('模板内容不能为空', 422);
        $id = (int)$id;
        if ($id) {
            $row = Db::name('share_template')->where('id', $id)->find();
            if (!$row) return $this->fail('模板不存在', 404);
            if ((int)$row['owner'] !== $uid) return $this->fail('无权修改', 403);
            $noticeHtml = $this->app->htmlSanitizerService->sanitize((string)$this->request->param('notice_html', ''));
            Db::transaction(function () use ($id, $uid, $name, $html, $noticeHtml): void {
                Db::name('share_template')->where('id', $id)->update(['name' => $name, 'html' => $html, 'notice_html' => $noticeHtml, 'update_time' => date('Y-m-d H:i:s')]);
                Db::name('share')->where('template_id', $id)->where('owner', $uid)->update(['html' => $html, 'notice_html' => $noticeHtml]);
            });
        } else {
            $noticeHtml = $this->app->htmlSanitizerService->sanitize((string)$this->request->param('notice_html', ''));
            Db::transaction(function () use ($uid, $name, $html, $noticeHtml): void {
                Db::name('share_template')->insert([
                    'owner' => $uid, 'name' => $name, 'html' => $html, 'notice_html' => $noticeHtml, 'is_default' => 0,
                    'create_time' => date('Y-m-d H:i:s'), 'update_time' => date('Y-m-d H:i:s'),
                ]);
                $this->ensureUserDefaultTemplate($uid);
            });
        }
        return $this->ok(null, $id ? '模板已更新' : '模板已添加');
    }

    public function setDefaultTemplate($id): Json
    {
        $currentUser = $this->user(); $uid = (int)$currentUser['id'];
        $row = Db::name('share_template')->where('id', (int)$id)->find();
        if (!$row) return $this->fail('模板不存在', 404);
        $owner = (int)$row['owner'];
        if ($owner !== $uid) return $this->fail('无权操作', 403);
        Db::name('share_template')->where('owner', $owner)->update(['is_default' => 0]);
        Db::name('share_template')->where('id', (int)$id)->update(['is_default' => 1]);
        return $this->ok(null, '已设为默认模板');
    }

    public function deleteTemplate($id): Json
    {
        $currentUser = $this->user(); $uid = (int)$currentUser['id'];
        $row = Db::name('share_template')->where('id', (int)$id)->find();
        if (!$row) return $this->fail('模板不存在', 404);
        if ((int)$row['owner'] !== $uid) return $this->fail('无权删除', 403);
        Db::transaction(function () use ($id, $uid): void {
            Db::name('share')->where('template_id', (int)$id)->update(['template_id' => null]);
            Db::name('share_group')->where('template_id', (int)$id)->update(['template_id' => null]);
            Db::name('share_template')->where('id', (int)$id)->delete();
            $this->ensureUserDefaultTemplate($uid);
        });
        return $this->ok(null, '模板已删除');
    }

    public function proxies(): Json
    {
        $perPage = $this->perPage();
        $p = $this->adminScope() ? $this->app->proxyService->fetchAll($perPage) : $this->app->proxyService->fetchByOwner((int)$this->user()['id'], $perPage);
        [$items, $meta] = $this->page($p); return $this->ok(['nodes' => $items,
            'sources' => $this->adminScope() ? $this->app->proxyService->fetchAllSources() : $this->app->proxyService->fetchSourcesByOwner((int)$this->user()['id']),
            'protocols' => $this->app->proxyService->getProtocolList()], '获取成功', $meta);
    }

    public function saveProxy($id = 0): Json
    {
        $id = (int)$id; $admin = $this->adminScope(); $owner = (int)$this->user()['id'];
        if ($admin && !Db::name('user')->where('id', $owner)->find()) return $this->fail('所属用户不存在', 422, ['owner' => '请选择有效用户']);
        if ($id) {
            $existing = Db::name('proxy')->where('id', $id)->find();
            if (!$existing) return $this->fail('代理节点不存在', 404);
            if (!$admin && (int)$existing['owner'] !== $owner) return $this->fail('无权操作', 403);
            $owner = (int)$existing['owner'];
        }
        try {
            $ok = $this->app->proxyService->saveEndpoint($id, ['name' => (string)$this->request->param('name', ''),
                'protocol' => (string)$this->request->param('protocol', ''), 'content' => (string)$this->request->param('content', ''),
                'max_accounts' => (int)$this->request->param('max_accounts', 3), 'status' => (bool)$this->request->param('status', true),
                'owner' => $owner, 'source_id' => null]);
            return $ok ? $this->ok(null, $id ? '静态 IP 已更新' : '静态 IP 已添加') : $this->fail('静态 IP 保存失败');
        } catch (\InvalidArgumentException $e) { return $this->fail($e->getMessage()); }
    }

    public function deleteProxy($id): Json
    {
        $row = Db::name('proxy')->where('id', (int)$id)->find();
        if (!$row) return $this->fail('代理节点不存在', 404);
        if (!$this->adminScope() && (int)$row['owner'] !== (int)$this->user()['id']) return $this->fail('无权操作', 403);
        return $this->app->proxyService->deleteEndpoint((int)$id) ? $this->ok(null, '静态 IP 已删除') : $this->fail('静态 IP 删除失败', 409);
    }

    public function testProxy($id): Json
    {
        $row = Db::name('proxy')->where('id', (int)$id)->find();
        if (!$row) return $this->fail('静态 IP 不存在', 404);
        if (!$this->adminScope() && (int)$row['owner'] !== (int)$this->user()['id']) return $this->fail('无权测试', 403);
        $result = $this->app->backendService->testProxyConnection((int)$id);
        return !empty($result['status'])
            ? $this->ok($result['data'] ?? [], (string)$result['msg'])
            : $this->fail((string)($result['msg'] ?? '静态 IP 测试失败'), 503, $result['data'] ?? []);
    }

    public function saveProxySource($id = 0): Json
    {
        $id = (int)$id; $admin = $this->adminScope(); $owner = (int)$this->user()['id'];
        if ($id) { $row = $this->app->proxyService->fetchSource($id); if (!$row) return $this->fail('提取源不存在', 404);
            if (!$admin && (int)$row['owner'] !== $owner) return $this->fail('无权操作', 403); $owner = (int)$row['owner']; }
        try { $ok = $this->app->proxyService->saveSource($id, ['name' => (string)$this->request->param('name', ''),
            'protocol' => (string)$this->request->param('protocol', ''), 'source_url' => (string)$this->request->param('source_url', ''),
            'refresh_interval' => (int)$this->request->param('refresh_interval', 600), 'status' => (bool)$this->request->param('status', true),
            'owner' => $owner, 'last_fetch_at' => null]);
            return $ok ? $this->ok(null, $id ? '提取源已更新' : '提取源已添加') : $this->fail('提取源保存失败');
        } catch (\InvalidArgumentException $e) { return $this->fail($e->getMessage()); }
    }

    public function deleteProxySource($id): Json
    {
        $row = $this->app->proxyService->fetchSource((int)$id); if (!$row) return $this->fail('提取源不存在', 404);
        if (!$this->adminScope() && (int)$row['owner'] !== (int)$this->user()['id']) return $this->fail('无权操作', 403);
        return $this->app->proxyService->deleteSource((int)$id) ? $this->ok(null, '提取源已删除') : $this->fail('提取源删除失败', 409);
    }

    public function plans(): Json
    {
        if ($this->adminScope()) { [$items, $meta] = $this->page($this->app->planService->fetchAll($this->perPage())); return $this->ok($items, '获取成功', $meta); }
        return $this->ok($this->app->planService->fetchEnabled());
    }

    public function orders(): Json
    {
        $perPage = $this->perPage();
        $p = $this->adminScope() ? $this->app->orderService->fetchAll($perPage) : $this->app->orderService->fetchByUser((int)$this->user()['id'], $perPage);
        [$items, $meta] = $this->page($p); return $this->ok($items, '获取成功', $meta);
    }

    public function adminUsers(): Json
    {
        if (!$this->isAdmin()) return $this->fail('无权访问', 403);
        [$items, $meta] = $this->page($this->app->userService->fetchAll($this->perPage()), function ($row) {
            $row = (array)$row;
            foreach (['password', 'mailbox_password_cipher', 'tg_bot_token', 'tg_chat_id', 'wx_pusher_id', 'webhook'] as $field) unset($row[$field]);
            return $row;
        });
        return $this->ok($items, '获取成功', $meta);
    }

    public function saveAdminUser($id = 0): Json
    {
        if (!$this->isAdmin()) return $this->fail('无权访问', 403);
        $id = (int)$id; $username = trim((string)$this->request->param('username', '')); $password = (string)$this->request->param('password', '');
        if ($username === '' || (!$id && $password === '')) return $this->fail('用户名和密码不能为空', 422);
        $notifyMode = (string)$this->request->param('tg_notify_mode', 'all');
        if (!in_array($notifyMode, ['success', 'failure', 'all'], true)) return $this->fail('Telegram 推送策略无效', 422);
        if ($id) $user = (new User())->fetch($id);
        else { $created = (new User())->addUser($username, $password); if (!$created['status']) return $this->fail($created['msg'], 409); $user = (new User())->where('username', $username)->find(); }
        if (!$user) return $this->fail('用户不存在', 404);
        if ($id === (int)Session::get('user_id') && !(bool)$this->request->param('is_admin', false)) return $this->fail('不能取消当前管理员的管理权限', 409);
        $result = $user->updateUser(['id' => $user->id, 'username' => $username, 'password' => $id ? $password : null,
            'tg_bot_token' => (string)$this->request->param('tg_bot_token', ''), 'tg_chat_id' => (string)$this->request->param('tg_chat_id', ''),
            'tg_notify_mode' => $notifyMode,
            'is_admin' => (bool)$this->request->param('is_admin', false) ? 1 : 0, 'vip_expire_at' => $this->request->param('vip_expire_at') ?: null,
            'max_accounts' => max(0, (int)$this->request->param('max_accounts', 5)), 'min_manual_interval' => max(0, (int)$this->request->param('min_manual_interval', 0)),
            'max_share_pages' => max(0, (int)$this->request->param('max_share_pages', 1)), 'balance' => number_format((float)$this->request->param('balance', 0), 2, '.', ''),
            'status' => (bool)$this->request->param('status', true) ? 1 : 0]);
        return $result['status'] ? $this->ok(null, $result['msg']) : $this->fail($result['msg'], 409);
    }

    public function deleteAdminUser($id): Json
    {
        if (!$this->isAdmin()) return $this->fail('无权访问', 403);
        if ((int)$id === (int)Session::get('user_id')) return $this->fail('不能删除当前登录管理员', 409);
        $result = (new User())->deleteUser((int)$id);
        return $result['status'] ? $this->ok(null, $result['msg']) : $this->fail($result['msg'], 409);
    }

    public function adminRedeemCodes(): Json
    { if (!$this->isAdmin()) return $this->fail('无权访问', 403); [$items, $meta] = $this->page($this->app->redeemCodeService->fetchAll($this->perPage())); return $this->ok($items, '获取成功', $meta); }

    public function generateRedeemCodes(): Json
    {
        if (!$this->isAdmin()) return $this->fail('无权访问', 403);
        $expire = trim((string)$this->request->param('expire_at', ''));
        $result = $this->app->redeemCodeService->generate((int)$this->request->param('plan_id', 0), (int)$this->request->param('count', 1), (int)Session::get('user_id'), $expire === '' ? null : $expire);
        return $result['status'] ? $this->ok(null, $result['msg']) : $this->fail($result['msg']);
    }

    public function savePlan($id = 0): Json
    {
        if (!$this->isAdmin()) return $this->fail('无权访问', 403);
        $result = $this->app->planService->savePlan(['name' => $this->request->param('name', ''), 'price' => $this->request->param('price', '0.00'),
            'days' => (int)$this->request->param('days', 30), 'max_accounts' => (int)$this->request->param('max_accounts', 5),
            'min_manual_interval' => (int)$this->request->param('min_manual_interval', 0), 'enable_share' => (bool)$this->request->param('enable_share', true) ? 1 : 0,
            'max_share_pages' => (int)$this->request->param('max_share_pages', 1), 'status' => (bool)$this->request->param('status', true) ? 1 : 0], (int)$id);
        return $result['status'] ? $this->ok(null, $result['msg']) : $this->fail($result['msg']);
    }

    public function deletePlan($id): Json
    {
        if (!$this->isAdmin()) return $this->fail('无权访问', 403);
        $result = $this->app->planService->deletePlan((int)$id);
        return $result['status'] ? $this->ok(null, $result['msg']) : $this->fail($result['msg'], 409);
    }

    public function settings(): Json
    {
        if (!$this->isAdmin()) return $this->fail('无权访问', 403);
        return $this->ok(['branding' => $this->app->siteConfigService->branding(), 'registration' => $this->publicRegistration(), 'payment' => $this->paymentConfig(), 'automation' => \app\service\AutomationScheduleConfig::globals()]);
    }

    private function paymentConfig(): array
    {
        $config = $this->app->paymentService->getConfig();
        return [
            'gateway' => (string)($config['gateway'] ?? ''),
            'pid' => (string)($config['pid'] ?? ''),
            'status' => (int)($config['status'] ?? 0),
            'merchant_key_configured' => !empty($config['merchant_key']),
        ];
    }

    public function updateSettings(string $section): Json
    {
        if (!$this->isAdmin()) return $this->fail('无权访问', 403);
        $payload = (array)$this->request->param();
        if ($section === 'branding') $result = $this->app->siteConfigService->saveBranding($payload);
        elseif ($section === 'registration') $result = $this->app->siteConfigService->saveRegistration($payload);
        elseif ($section === 'automation') $result = \app\service\AutomationScheduleConfig::saveGlobals($payload);
        elseif ($section === 'payment') {
            $current = $this->app->paymentService->getConfig();
            $merchantKey = (string)$this->request->param('merchant_key', '');
            $result = $this->app->paymentService->saveConfig([
                'gateway' => (string)$this->request->param('gateway', $current['gateway'] ?? ''),
                'pid' => (string)$this->request->param('pid', $current['pid'] ?? ''),
                'merchant_key' => $merchantKey !== '' ? $merchantKey : (string)($current['merchant_key'] ?? ''),
                'status' => $this->request->param('status') ? 1 : 0,
            ]);
        } else return $this->fail('未知配置区域', 404);
        return $result['status'] ? $this->ok(null, $result['msg']) : $this->fail($result['msg']);
    }

    public function cancelOrder($id): Json
    {
        $result = $this->app->orderService->cancel((int)$id, $this->isAdmin() ? null : (int)$this->user()['id']);
        return $result['status'] ? $this->ok(null, $result['msg']) : $this->fail($result['msg'], 409);
    }

    public function balancePurchase($id): Json
    {
        $result = $this->app->orderService->balancePurchase((int)$this->user()['id'], (int)$id);
        return $result['status'] ? $this->ok(null, $result['msg']) : $this->fail($result['msg'], 409);
    }

    public function redeem(): Json
    {
        $code = trim((string)$this->request->param('code', ''));
        if ($code === '') return $this->fail('请输入兑换码', 422, ['code' => '请输入兑换码']);
        $result = $this->app->redeemCodeService->redeem($code, (int)$this->user()['id']);
        return $result['status'] ? $this->ok(null, $result['msg']) : $this->fail($result['msg'], 409);
    }

    public function restartTask($id): Json
    {
        $account = Db::name('account')->where('id', (int)$id)->find(); $user = $this->user();
        if (!$account) return $this->fail('账号不存在', 404);
        if (!$this->isAdmin() && (int)$account['owner'] !== (int)$user['id']) return $this->fail('无权操作', 403);
        $mode = (string)$this->request->param('mode', 'all');
        $feature = (string)$this->request->param('feature', '');
        $confirmation = (string)$this->request->param('confirmation', '');
        if (!in_array($mode, ['all', 'due', 'feature', 'reconcile'], true)) return $this->fail('任务运行模式无效', 422);
        $allowed = ['two_factor', 'password_check', 'profile_restore', 'device_cleanup', 'password_update'];
        if ($feature !== '' && !in_array($feature, $allowed, true)) return $this->fail('任务功能无效', 422);
        $options = ['mode' => $mode];
        if ($feature !== '') $options['feature'] = $feature;
        if ($confirmation !== '') $options['confirmation'] = $confirmation;
        $result = $this->app->automationTaskService->restartTask(
            (int)$id, (int)$user['id'], $this->isAdmin() ? 'admin' : 'user', !$this->isAdmin(), $options
        );
        return $result['status'] ? $this->ok(null, $result['msg']) : $this->fail($result['msg'], 409);
    }

}
