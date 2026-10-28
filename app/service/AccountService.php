<?php
declare (strict_types=1);

namespace app\service;

use app\model\Account;
use think\facade\Db;
use think\Paginator;
use think\Service;

class AccountService extends Service
{
    public function register(): void
    {
        $this->app->bind('accountService', accountService::class);
    }

    public function fetchByOwner($user_id, int $perPage = 25): Paginator
    {
        return Account::where('owner', $user_id)->paginate($perPage);
    }

    public function fetchIDByOwner($user_id): array
    {
        return Db::name('account')
            ->field('id, username')
            ->where('owner', $user_id)
            ->column('username', 'id');
    }

    public function fetchAll(int $perPage = 25): Paginator
    {
        return Account::paginate($perPage);
    }

    public function countAll($id = 0): int
    {
        if ($id == 0) {
            return Db::name('account')->count();
        } else {
            return Db::name('account')->where('owner', $id)->count();
        }
    }

    public function fetchInShare($id): ?Account
    {
        $result = Account::field('id,username,password,frontend_remark,last_check,check_interval,message,min_manual_unlock,enable,task_status')
            ->where('id', $id)
            ->find();
        if (!$result) {
            return null;
        } else {
            return $result;
        }
    }

    public function disableAccount($username): array
    {
        $account = new Account();
        $account = $account->where('username', $username)->find();
        if (!$account) {
            return ['status' => false, 'msg' => '账号不存在'];
        }
        $account->enable = false;
        if (!AutomationScheduleConfig::globals()['active']) $account->enable_auto_update_password = false;
        $account->enable_delete_devices = false;
        $account->task_status = 'disabled';
        $account->task_message = '账号已冻结，等待人工复核';
        $account->task_next_run_at = null;
        if (!$account->save()) {
            return ['status' => false, 'msg' => '账号冻结失败'];
        }
        $backendResult = $this->app->automationTaskService->disableTask((int)$account->id, 0, 'system');
        return $backendResult['status']
            ? ['status' => true, 'msg' => '账号已冻结']
            : ['status' => true, 'msg' => '账号已冻结，后端任务停止待确认'];
    }

}
