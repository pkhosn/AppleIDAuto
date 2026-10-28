<?php
declare (strict_types=1);

namespace app\service;

use app\model\Account;
use app\model\UnlockRecord;
use think\facade\Db;
use think\Service;

class ApiService extends Service
{
    public function register(): void
    {
        $this->app->bind('apiService', ApiService::class);
    }

    public function updateAccount($username, $password, $status, $message, $profile = null): bool
    {
        $account = new Account();
        $account = $account->fetchByUsername($username);
        if (!$account) {
            return false;
        }
        if ($password != null) {
            $account->password = $password;
        }
        $account->message = $message;
        $account->last_check = date('Y-m-d H:i:s');
        $record = new UnlockRecord();
        $record->addRecord([
            'account_id' => $account->id,
            'status' => $status,
            'type' => 'backend',
            'message' => $message
        ]);
        $saved = $account->save();
        // 调度端随检查回报四项观测与逐字段结果；保存的恢复目标绝不由观测自动覆盖。
        $rawProfile = is_string($profile) ? trim($profile) : '';
        if ($saved && $rawProfile !== '') {
            $decoded = json_decode($rawProfile, true);
            if (is_array($decoded)) {
                $account->applyProfileObservation($decoded);
            }
        }
        if ($saved) {
            $this->app->automationTaskService->updateFromBackend((int)$account->id, (bool)$status, (string)$message, (int)$account->check_interval);
        }
        return $saved;
    }

    public function getPassword($username): ?string
    {
        $account = new Account();
        $account = $account->fetchByUsername($username);
        if (!$account) {
            return null;
        }
        return $account->password;
    }

    public function finalizeRecovery(int $accountId, string $passwordHash): array
    {
        $account = (new Account())->fetch($accountId);
        if (!$account) {
            return ['status' => false, 'msg' => '账号不存在'];
        }
        if (!hash_equals(hash('sha256', (string)$account->password), $passwordHash)) {
            return ['status' => false, 'msg' => '密码回写状态不一致'];
        }
        $recoveryUpdate = [
            'enable' => 0,
            'enable_auto_update_password' => 0,
            'enable_delete_devices' => 0,
            'check_interval' => 30,
            'task_status' => 'disabled',
            'task_next_run_at' => null,
        ];
        if (AutomationScheduleConfig::globals()['active']) {
            unset($recoveryUpdate['enable_auto_update_password'], $recoveryUpdate['check_interval']);
        }
        Db::name('account')->where('id', $accountId)->update($recoveryUpdate);
        $result = $this->app->automationTaskService->enableTask($accountId, 0, 'system');
        if (!$result['status']) {
            $this->app->automationTaskService->disableTask($accountId, 0, 'system');
            return ['status' => false, 'msg' => '周期检查启动失败，账号已重新冻结'];
        }
        return ['status' => true, 'msg' => '密码复核完成，周期检查已启用'];
    }

    /**
     * Reconcile a password whose change was independently confirmed by Apple.
     *
     * This deliberately keeps the account frozen.  It is used only when the
     * browser verification path has failed closed after an explicit Apple
     * confirmation, so a scheduler must never be started as a side effect.
     */
    public function reconcileConfirmedRecovery(int $accountId, string $password, string $passwordHash): array
    {
        if (!hash_equals(hash('sha256', $password), $passwordHash)) {
            return ['status' => false, 'msg' => '密码校验不一致'];
        }
        $account = (new Account())->fetch($accountId);
        if (!$account) {
            return ['status' => false, 'msg' => '账号不存在'];
        }
        try {
            Db::transaction(function () use ($accountId, $password): void {
                Db::name('account')->where('id', $accountId)->update([
                    'password' => $this->app->credentialCipherService->encrypt($password, 'account.password'),
                    'enable' => 0,
                    'enable_auto_update_password' => 0,
                    'enable_delete_devices' => 0,
                    'check_interval' => 30,
                    'task_status' => 'disabled',
                    'task_message' => '密码已按 Apple 邮件确认同步，等待浏览器复核',
                    'task_next_run_at' => null,
                ]);
            });
            $this->app->automationTaskService->disableTask($accountId, 0, 'system');
        } catch (\Throwable $exception) {
            return ['status' => false, 'msg' => '密码同步失败'];
        }
        return ['status' => true, 'msg' => '密码已同步，账号保持冻结'];
    }

}
