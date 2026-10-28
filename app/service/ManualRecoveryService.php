<?php
declare(strict_types=1);

namespace app\service;

use app\model\Account;
use think\Service;

class ManualRecoveryService extends Service
{
    private const KINDS = ['email_password', 'security_rollback'];

    public function register(): void
    {
        $this->app->bind('manualRecoveryService', self::class);
    }

    public function start(int $accountId, string $kind): array
    {
        if (!in_array($kind, self::KINDS, true)) {
            return $this->failure('恢复操作类型无效', 422);
        }

        $account = (new Account())->fetch($accountId);
        if (!$account) {
            return $this->failure('账号不存在', 404);
        }
        if ($account->enable || $account->enable_auto_update_password || $account->enable_delete_devices) {
            return $this->failure('请先禁用账号、自动改密和设备操作，再发起受控邮件恢复', 409);
        }

        $requiredMethod = $kind === 'email_password' ? 'email_password' : 'email_security';
        if (!$account->hasRecoveryMethod($requiredMethod)) {
            return $this->failure('该账号未启用所选邮件解锁方式', 409);
        }
        if (!env('backend.enable_api')) {
            return $this->failure('自动化后端未启用，无法发起邮件恢复', 503);
        }

        $result = $kind === 'email_password'
            ? $this->app->backendService->startEmailPasswordRecovery($accountId)
            : $this->app->backendService->startSecurityRollback($accountId);

        return !empty($result['status'])
            ? $result
            : array_merge($result, ['http_status' => 503]);
    }

    public function status(string $runId): array
    {
        if (!preg_match('/^[a-f0-9]{32}$/', $runId)) {
            return $this->failure('恢复任务标识无效', 422);
        }
        if (!env('backend.enable_api')) {
            return $this->failure('自动化后端未启用', 503);
        }

        $result = $this->app->backendService->manualRecoveryStatus($runId);
        return !empty($result['status'])
            ? $result
            : array_merge($result, ['http_status' => 503]);
    }

    public function history(int $accountId): array
    {
        if (!(new Account())->fetch($accountId)) {
            return $this->failure('账号不存在', 404, []);
        }
        if (!env('backend.enable_api')) {
            return $this->failure('自动化后端未启用', 503, []);
        }

        $result = $this->app->backendService->manualRecoveryHistory($accountId);
        return !empty($result['status'])
            ? $result
            : array_merge($result, ['http_status' => 503]);
    }

    private function failure(string $message, int $httpStatus, ?array $data = null): array
    {
        $result = ['status' => false, 'msg' => $message, 'http_status' => $httpStatus];
        if ($data !== null) {
            $result['data'] = $data;
        }
        return $result;
    }
}
