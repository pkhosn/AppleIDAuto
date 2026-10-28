<?php
declare (strict_types=1);

namespace app\controller;

use app\BaseController;
use app\model\Account;
use app\model\Proxy;
use app\model\SharePage;
use think\facade\Db;
use think\response\Json;

class ApiController extends BaseController
{
    public function updateAutomationStates(): Json
    {
        if (!\app\service\AutomationScheduleConfig::globals()['active']) return json(['status' => true]);
        $states = json_decode((string)$this->request->param('states', '[]'), true);
        if (!is_array($states) || count($states) > 500) return json(['status' => false, 'msg' => '无效状态列表'], 422);
        foreach ($states as $state) {
            if (!is_array($state) || !in_array($state['status'] ?? '', ['pending', 'running', 'success', 'failed', 'disabled'], true)) continue;
            $id = (int)($state['account_id'] ?? 0);
            $account = Db::name('account')->field('id,enable')->where('id', $id)->find();
            if (!$account) continue;
            $next = !empty($state['next_run_at']) ? strtotime($state['next_run_at']) : false;
            Db::name('account')->where('id', $id)->update([
                'task_status' => $account['enable'] ? $state['status'] : 'disabled',
                'task_next_run_at' => $account['enable'] && $next !== false ? date('Y-m-d H:i:s', $next) : null,
                'task_last_sync_at' => date('Y-m-d H:i:s'),
            ]);
        }
        return json(['status' => true]);
    }

    public function getAutomationSchedules(): Json
    {
        $globals = \app\service\AutomationScheduleConfig::globals();
        $rows = Db::name('account')->field('id,enable,check_interval,automation_schedule,enable_check_password_correct,enable_auto_update_password,enable_auto_disable_two_factor,enable_auto_restore_profile,enable_delete_devices')->select();
        $data = [];
        foreach ($rows as $row) {
            $schedule = \app\service\AutomationScheduleConfig::resolve($row, $globals);
            $data[] = [
                'id' => (int)$row['id'], 'enable' => (bool)$row['enable'], 'schedule' => $schedule,
                'config_revision' => hash('sha256', json_encode([
                    'enable' => (bool)$row['enable'], 'schedule' => $schedule,
                ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)),
            ];
        }
        return json(['status' => true, 'data' => $data]);
    }

    public function getTaskList(): Json
    {
        $account = new Account();
        $task_list = $account->getTaskList();
        return json(['code' => 200, 'msg' => '获取成功', 'status' => true, 'data' => $task_list]);
    }

    public function getTaskInfo(): Json
    {
        if (!$this->request->param('id')) {
            return json(['code' => 400, 'msg' => '缺少TaskID']);
        }
        $account = new Account();
        $account_info = $account->fetch($this->request->param('id'));
        if (!$account_info) {
            return json(['code' => 404, 'status' => false, 'msg' => 'TaskID不存在']);
        }
        $data = array(
            'username' => $account_info->username,
            'password' => $account_info->password,
            // dob转为字符串，格式为mmddyyyy
            'dob' => date('mdY', strtotime($account_info->dob)),
            'q1' => $account_info->question1,
            'q2' => $account_info->question2,
            'q3' => $account_info->question3,
            'a1' => $account_info->answer1,
            'a2' => $account_info->answer2,
            'a3' => $account_info->answer3,
            'check_interval' => $account_info->check_interval,
            'webdriver' => env('WEBDRIVER', ''),
            'enable' => $account_info->enable,
            'fail_retry' => env('FAIL_RETRY'),
            // Preserve the configured recovery preference order for the
            // automation service.  The worker must not infer authorization
            // for a recovery method merely from mailbox credentials existing.
            'recovery_methods' => array_values((array)$account_info->recovery_methods),
        );
        // 获取可选参数
        $data['schedule'] = \app\service\AutomationScheduleConfig::resolve($account_info->toArray());
        $data['config_revision'] = hash('sha256', json_encode([
            'enable' => (bool)$account_info->enable, 'schedule' => $data['schedule'],
        ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
        if ($account_info->enable_check_password_correct) {
            $data['check_password_correct'] = true;
        }
        if ($account_info->enable_delete_devices) {
            $data['enable_delete_devices'] = true;
        }
        if ($account_info->enable_auto_update_password) {
            $data['enable_auto_update_password'] = true;
        }
        if ($account_info->enable_auto_disable_two_factor) {
            $data['enable_auto_disable_two_factor'] = true;
        }
        // 账号资料自动恢复：账号页保存的目标资料直接作为恢复基线；检测到
        // Apple 侧发生变化后自动恢复，不再等待额外的人工确认。
        if ($account_info->enable_auto_restore_profile) {
            $data['enable_auto_restore_profile'] = true;
            $data['profile_restore'] = array(
                'name' => (string)$account_info->profile_name,
                'family_name' => (string)$account_info->profile_family_name,
                'middle_name' => (string)$account_info->profile_middle_name,
                'given_name' => (string)$account_info->profile_given_name,
                'dob' => date('Y-m-d', strtotime((string)$account_info->dob)),
                'country' => (string)$account_info->profile_country,
                'language' => (string)$account_info->profile_language,
                'confirmed' => true,
                'fields' => array('name' => true, 'dob' => true, 'country' => true, 'language' => true),
            );
        }
        $data['email_recovery'] = $this->mailboxConfigForAccount($account_info);
        if (env('task_headless', false)) {
            $data['task_headless'] = true;
        }
        // 处理通知参数
        $notify_params = $this->app->userService->getNotifyMethods($account_info->owner);
        if (!empty($notify_params)) {
            if ($notify_params['tg_bot_token'] != "") {
                $data['tg_bot_token'] = $notify_params['tg_bot_token'];
            }
            if ($notify_params['tg_chat_id'] != "") {
                $data['tg_chat_id'] = $notify_params['tg_chat_id'];
            }
            $data['tg_notify_mode'] = $notify_params['tg_notify_mode'] ?? 'all';
            if ($notify_params['wx_pusher_id'] != "") {
                $data['wx_pusher_id'] = $notify_params['wx_pusher_id'];
            }
            if ($notify_params['webhook'] != "") {
                $data['webhook'] = $notify_params['webhook'];
            }
        }
        // Disabling every applicable node explicitly selects direct mode. If
        // a node remains enabled but none is healthy, fail closed instead of
        // silently leaking to the server's direct egress.
        $poolEnabled = (bool)env('ENABLE_PROXY_POOL', false);
        $data['proxy_required'] = $poolEnabled
            && $this->app->proxyService->hasEnabledForOwner((int)$account_info->owner);
        $data['proxy_available'] = !$data['proxy_required'];
        $data['connection_mode'] = $data['proxy_required'] ? 'waiting_proxy' : 'direct';
        if ($data['proxy_required']) {
            $proxy = $this->app->proxyService->leaseForAccount((int)$account_info->id, (int)$account_info->owner);
            if (!empty($proxy)) {
                $data['proxy_available'] = true;
                $data['connection_mode'] = 'proxy';
                $data['proxy_id'] = $proxy['id'];
                $data['proxy_protocol'] = $proxy['protocol'];
                $data['proxy_content'] = $proxy['content'];
            }
        }
        return json(['code' => 200, 'msg' => '获取成功', 'status' => true, 'data' => $data]);
    }

    /** Internal automation endpoint. Never exposed to browser users. */
    public function getMailboxConfig(): Json
    {
        $id = (int)$this->request->param('id', 0);
        if ($id < 1) {
            return json(['code' => 400, 'status' => false, 'msg' => '缺少账号ID']);
        }
        $account = (new Account())->fetch($id);
        if (!$account) {
            return json(['code' => 404, 'status' => false, 'msg' => '账号不存在']);
        }
        return json([
            'code' => 200,
            'status' => true,
            'msg' => '获取成功',
            'data' => $this->mailboxConfigForAccount($account),
        ]);
    }

    /**
     * A unique rescue address identifies the account by recipient. If an
     * address is reused, the automation layer must additionally match the
     * Apple account identifier in the message body/subject.
     */
    private function mailboxConfigForAccount(Account $account): array
    {
        $config = $this->app->mailboxCredentialService->automationConfig($account);
        if (empty($config['configured'])) {
            return $config;
        }
        $duplicates = Db::name('account')
            ->where('recovery_email', (string)$account->recovery_email)
            ->where('id', '<>', (int)$account->id)
            ->count();
        $config['account_match_required'] = $duplicates > 0;
        return $config;
    }

    public function updateAccount(): json
    {
        if (!$this->request->param('username')
            || $this->request->param('status') === null
            || $this->request->param('message') === null) {
            return json(['code' => 400, 'msg' => '缺少参数', 'status' => false]);
        }
        $result = $this->app->apiService->updateAccount(
            $this->request->param('username'),
            $this->request->param('password'),
            $this->request->param('status') == 'True',
            $this->request->param('message'),
            $this->request->param('profile')
        );
        if ($result) {
            return json(['code' => 200, 'msg' => '更新成功', 'status' => true]);
        } else {
            return json(['code' => 500, 'msg' => '更新失败', 'status' => false]);
        }
    }

    public function finalizeRecovery(): json
    {
        $id = (int)$this->request->param('id', 0);
        $passwordHash = strtolower((string)$this->request->param('password_sha256', ''));
        if ($id <= 0 || !preg_match('/^[a-f0-9]{64}$/', $passwordHash)) {
            return json(['code' => 400, 'msg' => '参数错误', 'status' => false]);
        }
        $result = $this->app->apiService->finalizeRecovery($id, $passwordHash);
        return json([
            'code' => $result['status'] ? 200 : 409,
            'msg' => $result['msg'],
            'status' => $result['status'],
        ]);
    }

    /** Internal, fail-closed reconciliation after an explicit Apple email confirmation. */
    public function reconcileConfirmedRecovery(): json
    {
        $id = (int)$this->request->param('id', 0);
        $password = (string)$this->request->param('password', '');
        $passwordHash = strtolower((string)$this->request->param('password_sha256', ''));
        if ($id <= 0 || $password === '' || !preg_match('/^[a-f0-9]{64}$/', $passwordHash)) {
            return json(['code' => 400, 'msg' => '参数错误', 'status' => false]);
        }
        $result = $this->app->apiService->reconcileConfirmedRecovery($id, $password, $passwordHash);
        return json([
            'code' => $result['status'] ? 200 : 409,
            'msg' => $result['msg'],
            'status' => $result['status'],
        ]);
    }

    public function getPassword(): json
    {
        if (!$this->request->param('username')) {
            return json(['code' => 400, 'msg' => '缺少用户名', 'status' => false]);
        }
        $password = $this->app->apiService->getPassword($this->request->param('username'));
        if (!$password) {
            return json(['code' => 404, 'msg' => '用户不存在', 'status' => false]);
        }
        return json(['code' => 200, 'msg' => '获取成功', 'status' => true, 'data' => ['password' => $password]]);
    }

    public function checkApi(): json
    {
        return json(['code' => 200, 'msg' => 'API正常', 'version' => 3]);
    }

    public function randomSharePagePassword(): json
    {
        $id = $this->request->param('id');
        if (!$id) {
            return json(['code' => 400, 'msg' => '缺少分享页ID', 'status' => false]);
        }
        $share_page = new SharePage();
        $share_page = $share_page->fetch($id);
        if (!$share_page) {
            return json(['code' => 404, 'msg' => '分享页不存在', 'status' => false]);
        }
        $plainPassword = secure_token(12);
        $share_page->password = $this->app->shareAccessService->hashPassword($plainPassword);
        if ($share_page->save()) {
            return json(['code' => 200, 'msg' => '更新成功', 'status' => true, 'data' => ['password' => $plainPassword]]);
        } else {
            return json(['code' => 500, 'msg' => '更新失败', 'status' => false]);
        }
    }

    public function reportProxyError(): json
    {
        $id = $this->request->param('id');
        if (!$id) {
            return json(['code' => 400, 'msg' => '缺少代理ID', 'status' => false]);
        }
        $proxy = new Proxy();
        $proxy_info = $proxy->fetch($id);
        if (!$proxy_info) {
            return json(['code' => 404, 'msg' => '代理不存在', 'status' => false]);
        }
        if ($this->app->proxyService->recordHealth((int)$id, false, '', null, 'task_proxy_error')) {
            return json(['code' => 200, 'msg' => '报告成功', 'status' => true]);
        }
        return json(['code' => 500, 'msg' => '报告失败', 'status' => false]);
    }

    public function getProxySources(): json
    {
        return json(['code' => 200, 'msg' => '获取成功', 'status' => true,
            'data' => $this->app->proxyService->dueSources()]);
    }

    public function syncProxySource(): json
    {
        $id = (int)$this->request->param('source_id', 0);
        $endpoints = $this->request->param('endpoints', []);
        if (is_string($endpoints)) {
            $endpoints = json_decode($endpoints, true) ?: [];
        }
        $ok = $id > 0 && is_array($endpoints) && $this->app->proxyService->syncSource(
            $id, $endpoints, (string)$this->request->param('error_code', '')
        );
        return json(['code' => $ok ? 200 : 400, 'msg' => $ok ? '同步成功' : '同步失败', 'status' => $ok]);
    }

    public function getProxyHealthTasks(): json
    {
        $limit = (int)$this->request->param('limit', 50);
        return json(['code' => 200, 'msg' => '获取成功', 'status' => true,
            'data' => $this->app->proxyService->dueHealthChecks($limit)]);
    }

    public function getProxyHealthTarget(): json
    {
        $id = (int)$this->request->param('id', 0);
        $row = $id > 0 ? Db::name('proxy')->field('id,protocol,content')->where('id', $id)->find() : null;
        if ($row) $row = $this->app->proxyService->decryptEndpointRow($row);
        return json($row
            ? ['code' => 200, 'msg' => '获取成功', 'status' => true, 'data' => $row]
            : ['code' => 404, 'msg' => '静态 IP 不存在', 'status' => false, 'data' => null]);
    }

    public function updateProxyHealth(): json
    {
        $id = (int)$this->request->param('proxy_id', 0);
        $success = filter_var($this->request->param('success', false), FILTER_VALIDATE_BOOLEAN);
        $latency = $this->request->param('latency_ms');
        $ok = $id > 0 && $this->app->proxyService->recordHealth(
            $id, $success, (string)$this->request->param('exit_ip', ''),
            $latency === null || $latency === '' ? null : (int)$latency,
            (string)$this->request->param('error_code', '')
        );
        return json(['code' => $ok ? 200 : 404, 'msg' => $ok ? '更新成功' : '代理不存在', 'status' => $ok]);
    }

    public function getBackendApi(): json
    {
        $data['enable'] = env('backend.enable_api');
        if ($data['enable']) {
            $data['listen_ip'] = env('backend.listen_ip');
            $data['listen_port'] = env('backend.listen_port');
        }
        return json(['code' => 200, 'msg' => '获取成功', 'status' => true, 'data' => $data]);
    }

    public function getSharepage($link, $password = ""): json
    {
        return $this->shareApiResponse((string)$link, (string)$password, true);
    }

    /** Password and capability are carried in a POST body, never in an access-log path. */
    public function resolveSharepage(): json
    {
        return $this->shareApiResponse(
            (string)$this->request->param('link', ''),
            (string)$this->request->param('password', ''),
            false
        );
    }

    private function shareApiResponse(string $link, string $password, bool $legacy): json
    {
        if ($link === '') return json(['code' => 400, 'msg' => '缺少分享链接', 'status' => false], 400);
        $resolved = $this->app->shareAccessService->resolve($link, $password, getUserIP(), true, false);
        if (empty($resolved['status'])) {
            $response = json([
                'code' => (int)$resolved['http_status'], 'msg' => (string)$resolved['msg'], 'status' => false,
            ], (int)$resolved['http_status']);
            return $legacy ? $this->legacyShareHeaders($response) : $response;
        }
        $share_page = $resolved['share'];
        $accounts = [];
        foreach ($share_page->account_list as $accountID) {
            $account = new Account();
            $account = $account->fetch($accountID);
            if (!$account) {
                continue;
            }
            $accounts[] = [
                'id' => $account->id,
                'username' => $account->username,
                'password' => $account->password,
                'status' => $this->app->shareService->isAccountHealthy($account->toArray()),
                'last_check' => $account->last_check,
                'remark' => $this->app->htmlSanitizerService->sanitize((string)$account->frontend_remark),
            ];
        }
        if (env("share_random")) {
            shuffle($accounts);
        }
        $response = json(['code' => 200, 'msg' => '获取成功', 'status' => true, 'accounts' => $accounts]);
        return $legacy ? $this->legacyShareHeaders($response) : $response;
    }

    private function legacyShareHeaders(json $response): json
    {
        return $response->header([
            'Deprecation' => 'true',
            'Sunset' => 'Tue, 13 Oct 2026 00:00:00 GMT',
            'Link' => '</shareapi/v2/resolve>; rel="successor-version"',
        ]);
    }

    public function disableAccount(): json
    {
        $username = (string)$this->request->param('username', '');
        if (!$username) {
            return json(['code' => 400, 'msg' => '缺少用户名', 'status' => false]);
        }
        return json($this->app->accountService->disableAccount($username));
    }
}
