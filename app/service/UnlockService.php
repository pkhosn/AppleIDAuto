<?php
declare (strict_types=1);

namespace app\service;

use app\model\UnlockRecord;
use think\facade\Db;
use think\Paginator;
use think\Service;

class UnlockService extends Service
{
    public function register()
    {
        $this->app->bind('unlockService', UnlockService::class);
    }

    public function unlock($id)
    {
        if (!env("backend.enable_api")) {
            return ['status' => false, 'msg' => '后端API未启用'];
        }
        $account = Db::name('account')
            ->field('min_manual_unlock,last_manual_unlock_at')
            ->where('id', $id)
            ->find();
        if (count($account) == 0) {
            return ['status' => false, 'msg' => '账号不存在'];
        }
        $min_manual_unlock = $account['min_manual_unlock'];
        if ($min_manual_unlock == 0) {
            return ['status' => false, 'msg' => '该账号未启用手动解锁'];
        }
        $lastManual = (string)($account['last_manual_unlock_at'] ?? '');
        if ($lastManual !== '' && strtotime($lastManual) > time() - $min_manual_unlock * 60) {
            return ['status' => false, 'msg' => '间隔太短，暂不允许手动解锁'];
        } else {
            $backendResult = $this->app->backendService->restartTask($id, [
                'mode' => 'feature', 'feature' => 'password_check',
            ]);
            $record = new UnlockRecord();
            $record->addRecord([
                'account_id' => $id, 'type' => 'manual',
                'status' => $backendResult['status'],
                'message' => $backendResult['msg'],
                'time' => date('Y-m-d H:i:s'),
                'ip' => getUserIP()
            ]);
            if (!empty($backendResult['status'])) {
                Db::name('account')->where('id', $id)->update(['last_manual_unlock_at' => date('Y-m-d H:i:s')]);
            }
            return $backendResult;
        }
    }

    public function fetchRecord($userID = 0, int $perPage = 25): Paginator
    {
        return Db::name('unlock_record')
            ->join('account', 'unlock_record.account_id=account.id')
            ->field('unlock_record.*,account.username')
            ->order('unlock_record.id desc')
            ->where('account.owner', 'LIKE', $userID == 0 ? "%" : $userID)
            ->paginate($perPage);
    }
}
