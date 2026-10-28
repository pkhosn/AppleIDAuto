<?php
declare (strict_types=1);

namespace app\service;

use app\model\AutomationTaskLog;
use think\facade\Db;
use think\Service;

class AutomationTaskService extends Service
{
    public function register(): void
    {
        $this->app->bind('automationTaskService', AutomationTaskService::class);
    }

    public function fetchByOwner(int $owner, int $perPage = 25)
    {
        return Db::name('account')
            ->where('owner', $owner)
            ->order('id', 'desc')
            ->paginate($perPage);
    }

    public function fetchAll(array $filters = [], int $perPage = 25)
    {
        $query = Db::name('account')->alias('a')
            ->leftJoin('user u', 'u.id = a.owner')
            ->field('a.*,u.username owner_name')
            ->order('a.id', 'desc');
        if (!empty($filters['status'])) {
            $query->where('a.task_status', $filters['status']);
        }
        if (!empty($filters['keyword'])) {
            $keyword = '%' . $filters['keyword'] . '%';
            $query->where(function ($q) use ($keyword) {
                $q->where('a.username', 'like', $keyword)->whereOr('a.remark', 'like', $keyword);
            });
        }
        if (!empty($filters['owner'])) {
            $query->where('u.username', 'like', '%' . $filters['owner'] . '%');
        }
        return $query->paginate($perPage, false, ['query' => $filters]);
    }

    public function restartTask(int $accountId, int $operatorId, string $operatorType = 'user', bool $checkEntitlement = true, array $options = []): array
    {
        $account = Db::name('account')->where('id', $accountId)->find();
        if (!$account) {
            return ['status' => false, 'msg' => '账号不存在'];
        }
        if ($checkEntitlement) {
            $check = $this->app->entitlementService->canManualUnlock((int)$account['owner'], $accountId);
            if (!$check['status']) {
                $this->log($accountId, $operatorId, $operatorType, 'restart', false, $check['msg']);
                return $check;
            }
        }
        $backendResult = $this->app->backendService->restartTask($accountId, $options);
        if (!empty($backendResult['status']) && in_array($operatorType, ['user', 'share'], true)) {
            Db::name('account')->where('id', $accountId)->update(['last_manual_unlock_at' => date('Y-m-d H:i:s')]);
        }
        $this->markBackendResult($accountId, $backendResult, 'restart');
        $this->log($accountId, $operatorId, $operatorType, 'restart', $backendResult['status'], $backendResult['msg']);
        return $backendResult;
    }

    public function enableTask(int $accountId, int $operatorId, string $operatorType = 'admin'): array
    {
        $account = Db::name('account')->where('id', $accountId)->find();
        if (!$account) {
            return ['status' => false, 'msg' => '账号不存在'];
        }
        Db::name('account')->where('id', $accountId)->update(['enable' => 1, 'task_status' => 'pending']);
        $backendResult = $this->app->backendService->addTask($accountId);
        $this->markBackendResult($accountId, $backendResult, 'enable');
        $this->log($accountId, $operatorId, $operatorType, 'enable', $backendResult['status'], $backendResult['msg']);
        return $backendResult;
    }

    public function disableTask(int $accountId, int $operatorId, string $operatorType = 'admin'): array
    {
        $account = Db::name('account')->where('id', $accountId)->find();
        if (!$account) {
            return ['status' => false, 'msg' => '账号不存在'];
        }
        Db::name('account')->where('id', $accountId)->update([
            'enable' => 0,
            'task_status' => 'disabled',
            'task_message' => '任务已禁用',
            'task_next_run_at' => null,
        ]);
        $backendResult = $this->app->backendService->removeTask($accountId);
        $this->log($accountId, $operatorId, $operatorType, 'disable', $backendResult['status'], $backendResult['msg']);
        return $backendResult;
    }

    public function syncTask(int $accountId, int $operatorId, string $operatorType = 'admin'): array
    {
        $account = Db::name('account')->where('id', $accountId)->find();
        if (!$account) {
            return ['status' => false, 'msg' => '账号不存在'];
        }
        $backendResult = $account['enable']
            ? $this->app->backendService->addTask($accountId)
            : $this->app->backendService->removeTask($accountId);
        $this->markBackendResult($accountId, $backendResult, 'sync');
        $this->log($accountId, $operatorId, $operatorType, 'sync', $backendResult['status'], $backendResult['msg']);
        return $backendResult;
    }

    public function batch(array $ids, string $action, int $operatorId): array
    {
        $results = [];
        foreach ($ids as $id) {
            $accountId = (int)$id;
            if ($accountId <= 0) {
                continue;
            }
            switch ($action) {
                case 'enable':
                    $result = $this->enableTask($accountId, $operatorId);
                    break;
                case 'disable':
                    $result = $this->disableTask($accountId, $operatorId);
                    break;
                case 'restart':
                    $result = $this->restartTask($accountId, $operatorId, 'admin', false, ['mode' => 'due']);
                    break;
                case 'sync':
                    $result = $this->syncTask($accountId, $operatorId);
                    break;
                default:
                    $result = ['status' => false, 'msg' => '未知操作'];
            }
            $results[] = ['id' => $accountId, 'status' => $result['status'], 'msg' => $result['msg']];
        }
        $success = count(array_filter($results, fn($item) => $item['status']));
        return ['status' => true, 'msg' => '处理完成，成功 ' . $success . ' 条，失败 ' . (count($results) - $success) . ' 条', 'data' => $results];
    }

    public function updateFromBackend(int $accountId, bool $status, string $message, int $checkInterval): void
    {
        if (AutomationScheduleConfig::globals()['active']) {
            // Independent next_run is reported by the scheduler, never infer it from legacy cadence.
            Db::name('account')->where('id', $accountId)->update([
                'task_message' => $message, 'task_last_sync_at' => date('Y-m-d H:i:s'),
            ]);
            return;
        }
        Db::name('account')->where('id', $accountId)->update([
            'task_status' => $status ? 'success' : 'failed',
            'task_message' => $message,
            'task_last_sync_at' => date('Y-m-d H:i:s'),
            'task_next_run_at' => $status ? date('Y-m-d H:i:s', time() + ($checkInterval * 60)) : null,
            'task_retry_count' => $status ? 0 : Db::raw('task_retry_count + 1'),
        ]);
    }

    private function markBackendResult(int $accountId, array $result, string $action): void
    {
        $account = Db::name('account')->where('id', $accountId)->find();
        if (!$account) {
            return;
        }
        if (AutomationScheduleConfig::globals()['active']) {
            Db::name('account')->where('id', $accountId)->update([
                'task_message' => $result['msg'], 'task_last_sync_at' => date('Y-m-d H:i:s'),
            ]);
            return;
        }
        Db::name('account')->where('id', $accountId)->update([
            'task_status' => $result['status'] ? 'running' : 'failed',
            'task_message' => $result['msg'],
            'task_last_sync_at' => date('Y-m-d H:i:s'),
            'task_next_run_at' => $result['status'] && $account['enable'] ? date('Y-m-d H:i:s', time() + ((int)$account['check_interval'] * 60)) : null,
            'task_retry_count' => $result['status'] ? 0 : Db::raw('task_retry_count + 1'),
        ]);
    }

    private function log(int $accountId, int $operatorId, string $operatorType, string $action, bool $status, string $message): void
    {
        (new AutomationTaskLog())->create([
            'account_id' => $accountId,
            'operator_id' => $operatorId,
            'operator_type' => $operatorType,
            'action' => $action,
            'status' => $status ? 1 : 0,
            'message' => $message,
            'ip' => getUserIP(),
        ]);
    }
}
