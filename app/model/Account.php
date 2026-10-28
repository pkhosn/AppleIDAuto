<?php
declare (strict_types=1);

namespace app\model;

use think\Container;
use think\facade\Db;
use think\Model;

/**
 * @mixin Model
 */
class Account extends Model
{
    protected $table = 'account';
    protected $pk = 'id';

    public function getPasswordAttr($value): string { return $this->decryptCredential((string)$value, 'account.password'); }
    public function setPasswordAttr($value): string { return $this->encryptCredential((string)$value, 'account.password'); }
    public function getAnswer1Attr($value): string { return $this->decryptCredential((string)$value, 'account.answer1'); }
    public function setAnswer1Attr($value): string { return $this->encryptCredential((string)$value, 'account.answer1'); }
    public function getAnswer2Attr($value): string { return $this->decryptCredential((string)$value, 'account.answer2'); }
    public function setAnswer2Attr($value): string { return $this->encryptCredential((string)$value, 'account.answer2'); }
    public function getAnswer3Attr($value): string { return $this->decryptCredential((string)$value, 'account.answer3'); }
    public function setAnswer3Attr($value): string { return $this->encryptCredential((string)$value, 'account.answer3'); }

    private function decryptCredential(string $value, string $context): string
    { return Container::getInstance()->make('credentialCipherService')->decrypt($value, $context); }

    private function encryptCredential(string $value, string $context): string
    { return Container::getInstance()->make('credentialCipherService')->encrypt($value, $context); }

    function addAccount($data, bool $checkEntitlement = true): array
    {
        if (!isset($data['automation_schedule'])) {
            $features = [];
            foreach (\app\service\AutomationScheduleConfig::FEATURES as $key => $definition) {
                $features[$key] = ['source' => 'global', 'minutes' => $definition[1]];
            }
            $data['automation_schedule'] = \app\service\AutomationScheduleConfig::normalize($features);
        }
        $data = $this->normalizeInput($data);
        if ($data['username'] === '' || $data['password'] === '') {
            return ['status' => false, 'msg' => '账号和密码不能为空'];
        }
        if ($this->fetchByUsername($data['username'])) {
            return ['status' => false, 'msg' => '账号已存在'];
        }
        $data['message'] = "未执行任务";
        $data['task_status'] = !empty($data['enable']) ? 'pending' : 'disabled';
        $data['task_message'] = "未执行任务";
        try {
            $account = Container::getInstance()->make('entitlementService')->createWithinQuota(
                (int)$data['owner'], 'account', 1, $checkEntitlement,
                static fn() => (new Account())->create($data)
            );
        } catch (\InvalidArgumentException $e) {
            return ['status' => false, 'msg' => $e->getMessage()];
        }
        $automationTaskService = Container::getInstance()->make('automationTaskService');
        $backendResult = !empty($data['enable'])
            ? $automationTaskService->enableTask((int)$account->id, (int)$data['owner'], 'user')
            : ['status' => true, 'msg' => '账号已添加，任务未启用'];
        $result = [];
        if ($backendResult['status']) {
            $result['status'] = true;
            $result['msg'] = '添加成功';
        } else {
            $result['status'] = true;
            $result['msg'] = '添加成功，但后端接口调用失败：' . $backendResult['msg'];
        }
        return $result;
    }

    function fetchByUsername($username)
    {
        return $this->where('username', $username)->find();
    }

    function deleteAccount($id): array
    {
        $account = $this->fetch((int)$id);
        if (!$account) {
            return ['status' => false, 'msg' => '账号不存在'];
        }
        // Freeze first; no remote operation is performed while holding DB locks.
        $backendResult = Container::getInstance()->make('automationTaskService')->disableTask((int)$id, 0, 'system');
        $deleted = Db::transaction(function () use ($account, $id) {
            $owner = (int)$account->owner;
            Db::name('user')->where('id', $owner)->lock(true)->find();
            foreach (['share', 'share_group'] as $table) {
                $pages = Db::name($table)->field('id,account_list')->where('owner', $owner)
                    ->whereRaw('FIND_IN_SET(?, account_list) > 0', [(int)$id])->lock(true)->select();
                foreach ($pages as $page) {
                    $ids = array_values(array_diff(array_map('intval', explode(',', $page['account_list'])), [(int)$id]));
                    $query = Db::name($table)->where('id', $page['id'])->where('owner', $owner);
                    if (!$ids && $table === 'share') $query->delete();
                    else $query->update(['account_list' => implode(',', $ids)]);
                }
            }
            return Db::name('account')->where('id', (int)$id)->where('owner', $owner)->delete();
        });
        return ['status' => (bool)$deleted, 'msg' => $deleted
            ? ($backendResult['status'] ? '删除成功' : '删除成功，但后端接口错误：' . $backendResult['msg'])
            : '删除失败'];
    }

    function fetch($id)
    {
        $account = $this->where('id', (int)$id)->find();
        if (!$account) return null;
        foreach (['remark', 'frontend_remark', 'message', 'task_message', 'question1', 'answer1',
                  'question2', 'answer2', 'question3', 'answer3'] as $field) {
            if ($account->$field === null) $account->$field = '';
        }
        $account->recovery_methods = $this->parseRecoveryMethods(
            (string)($account->recovery_methods ?? ''), !empty($account->enable_email_recovery)
        );
        return $account;
    }

    /** 记录调度端观测值；账号编辑页保存的目标资料不会被观测值覆盖。 */
    function applyProfileObservation(array $observed): array
    {
        $name = trim((string)($observed['name'] ?? ''));
        $dob = trim((string)($observed['dob'] ?? ''));
        $country = trim((string)($observed['country'] ?? ''));
        $language = trim((string)($observed['language'] ?? ''));
        if ($name === '' && $dob === '' && $country === '' && $language === '') {
            return ['recorded' => false, 'observed' => ['name' => '', 'dob' => '', 'country' => '', 'language' => '']];
        }
        Db::name('account')->where('id', (int)$this->id)->update([
            'profile_observed' => json_encode(array_merge(
                ['name' => $name, 'dob' => $dob, 'country' => $country, 'language' => $language],
                $observed,
                ['observed_at' => date('Y-m-d H:i:s')]
            ), JSON_UNESCAPED_UNICODE),
        ]);
        return ['recorded' => false, 'observed' => ['name' => $name, 'dob' => $dob, 'country' => $country, 'language' => $language]];
    }

    function updateAccount($id, $data): array
    {
        $account = $this->fetch($id);
        if (!$account) {
            return ['status' => false, 'msg' => '账号不存在'];
        }
        // Partial API clients may omit the structured v2 baseline fields. Never
        // erase them merely because an update payload omitted newer controls.
        foreach ([
            'profile_name', 'profile_family_name', 'profile_middle_name', 'profile_given_name',
            'profile_country', 'profile_language',
        ] as $field) {
            if (!array_key_exists($field, $data) || $data[$field] === null) {
                $data[$field] = (string)($account->$field ?? '');
            }
        }
        $data = $this->normalizeInput($data);
        if ($data['username'] === '' || $data['password'] === '') {
            return ['status' => false, 'msg' => '账号和密码不能为空'];
        }
        if ($account->username != $data['username']) {
            if ($this->fetchByUsername($data['username'])) {
                return ['status' => false, 'msg' => '用户名已存在'];
            }
        }
        $result = [];
        $result['status'] = $account->update($data, ['id' => $id]);
        if ($result['status']) {
            $automationTaskService = Container::getInstance()->make('automationTaskService');
            if (\app\service\AutomationScheduleConfig::globals()['active']) {
                $sync = $automationTaskService->syncTask((int)$id, (int)($data['owner'] ?? $account->owner), 'system');
                return ['status' => true, 'msg' => $sync['status'] ? '账号及周期已保存；未强制执行任务' : '账号已保存，调度同步暂不可用，将自动重试同步'];
            }
            $backendResult = !empty($data['enable'])
                ? $automationTaskService->restartTask((int)$id, (int)($data['owner'] ?? $account->owner), 'system', false)
                : $automationTaskService->disableTask((int)$id, (int)($data['owner'] ?? $account->owner), 'system');
            if ($backendResult['status']) {
                $result['msg'] = '修改成功';
            } else {
                $result['msg'] = '修改成功，但后端接口错误：' . $backendResult['msg'];
            }
        } else {
            $result['msg'] = '修改失败';
        }
        return $result;
    }

    function getTaskList(): array
    {
        return $this->where('enable', 1)->column('id');
    }

    private function normalizeInput(array $data): array
    {
        foreach (['username', 'password', 'remark', 'frontend_remark', 'question1', 'answer1',
                  'question2', 'answer2', 'question3', 'answer3', 'profile_name',
                  'profile_family_name', 'profile_middle_name', 'profile_given_name',
                  'profile_country', 'profile_language'] as $field) {
            $data[$field] = trim((string)($data[$field] ?? ''));
        }
        $dob = (string)($data['dob'] ?? '');
        $data['dob'] = preg_match('/^\d{4}-\d{2}-\d{2}$/', $dob) ? $dob : '2000-01-01';
        $data['check_interval'] = max(1, (int)($data['check_interval'] ?? 30));
        $data['min_manual_unlock'] = max(0, (int)($data['min_manual_unlock'] ?? 0));
        $data['enable_delete_devices'] = (int)(bool)($data['enable_delete_devices'] ?? false);
        $data['enable_auto_disable_two_factor'] = (int)(bool)($data['enable_auto_disable_two_factor'] ?? false);
        $data['enable_auto_restore_profile'] = (int)(bool)($data['enable_auto_restore_profile'] ?? false);
        $rawMethods = $data['recovery_methods'] ?? null;
        if ($rawMethods === null) {
            $methods = !empty($data['enable_email_recovery']) ? self::defaultRecoveryMethods() : [];
        } else {
            $methods = is_array($rawMethods) ? $rawMethods : explode(',', (string)$rawMethods);
        }
        $methods = array_values(array_unique(array_intersect(
            array_map('strval', $methods), self::allowedRecoveryMethods()
        )));
        $data['recovery_methods'] = implode(',', $methods);
        $data['enable_email_recovery'] = (int)(in_array('email_password', $methods, true)
            || in_array('email_security', $methods, true));
        return $data;
    }

    public function hasRecoveryMethod(string $method): bool
    {
        $methods = $this->recovery_methods ?? [];
        if (!is_array($methods)) {
            $methods = array_filter(array_map('trim', explode(',', (string)$methods)));
        }
        return in_array($method, $methods, true);
    }

    public static function allowedRecoveryMethods(): array
    {
        return ['email_password', 'email_security', 'account_login'];
    }

    public static function defaultRecoveryMethods(): array
    {
        return ['email_password', 'email_security'];
    }

    private function parseRecoveryMethods(string $value, bool $legacyEmailEnabled): array
    {
        if ($value === '' && $legacyEmailEnabled) {
            return self::defaultRecoveryMethods();
        }
        return array_values(array_unique(array_intersect(
            array_filter(array_map('trim', explode(',', $value))), self::allowedRecoveryMethods()
        )));
    }
}
