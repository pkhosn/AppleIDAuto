<?php
declare (strict_types=1);

namespace app\service;

use think\facade\Db;
use think\Service;

class EntitlementService extends Service
{
    public function register(): void
    {
        $this->app->bind('entitlementService', EntitlementService::class);
    }

    /** Serialize quota decisions with inserts; callers must keep network work outside. */
    public function createWithinQuota(int $owner, string $kind, int $quantity, bool $check, callable $create)
    {
        if (!in_array($kind, ['account', 'share'], true) || $quantity < 1) {
            throw new \InvalidArgumentException('资源配额参数无效');
        }
        $connection = Db::connect();
        $pdo = $connection->getPdo();
        $nested = $pdo && $pdo->inTransaction();
        if (!$nested) {
            // User locks serialize one tenant; READ COMMITTED avoids locking
            // empty index gaps shared by unrelated tenants during quota counts.
            $connection->execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED');
        }
        return Db::transaction(function () use ($owner, $kind, $quantity, $check, $create, $nested) {
            $user = Db::name('user')->where('id', $owner)->lock(true)->find();
            if (!$user) throw new \InvalidArgumentException('所属用户不存在');
            if ($check) {
                if (empty($user['status']) || empty($user['vip_expire_at']) || strtotime($user['vip_expire_at']) < time()) {
                    throw new \InvalidArgumentException('请先购买有效订阅');
                }
                $query = Db::name($kind)->where('owner', $owner);
                // Nested callers may already have a REPEATABLE READ snapshot.
                $current = $nested ? count($query->field('id')->lock(true)->select()) : $query->count();
                $limit = (int)$user[$kind === 'account' ? 'max_accounts' : 'max_share_pages'];
                if ($current + $quantity > $limit) throw new \InvalidArgumentException('资源数量将超过当前套餐上限');
            }
            return $create();
        });
    }

    public function checkActive(int $userId): array
    {
        $user = Db::name('user')->where('id', $userId)->find();
        if (!$user || !$user['status']) {
            return ['status' => false, 'msg' => '用户不可用'];
        }
        if (!empty($user['vip_expire_at']) && strtotime($user['vip_expire_at']) < time()) {
            return ['status' => false, 'msg' => '套餐已到期，请先续费'];
        }
        return ['status' => true, 'msg' => ''];
    }

    public function canAddAccount(int $userId): array
    {
        if (!$this->hasSubscription($userId)) {
            return ['status' => false, 'msg' => '请先购买订阅后再添加 Apple 账号'];
        }
        $active = $this->checkActive($userId);
        if (!$active['status']) {
            return $active;
        }
        $user = Db::name('user')->where('id', $userId)->find();
        $count = Db::name('account')->where('owner', $userId)->count();
        if ($count >= (int)$user['max_accounts']) {
            return ['status' => false, 'msg' => '账号数量已达到当前套餐上限'];
        }
        return ['status' => true, 'msg' => ''];
    }

    public function canAddShare(int $userId): array
    {
        if (!$this->hasSubscription($userId)) {
            return ['status' => false, 'msg' => '请先购买订阅后再使用分享页'];
        }
        $active = $this->checkActive($userId);
        if (!$active['status']) {
            return $active;
        }
        $user = Db::name('user')->where('id', $userId)->find();
        if ((int)$user['max_share_pages'] <= 0) {
            return ['status' => false, 'msg' => '当前套餐未开启分享页'];
        }
        $count = Db::name('share')->where('owner', $userId)->count();
        if ($count >= (int)$user['max_share_pages']) {
            return ['status' => false, 'msg' => '分享页数量已达到当前套餐上限'];
        }
        return ['status' => true, 'msg' => ''];
    }

    /** Check capacity for an all-or-nothing batch of share pages. */
    public function canAddShares(int $userId, int $quantity): array
    {
        if ($quantity < 1) {
            return ['status' => false, 'msg' => '生成数量必须大于 0'];
        }
        if (!$this->hasSubscription($userId)) {
            return ['status' => false, 'msg' => '请先购买订阅后再使用分享页'];
        }
        $active = $this->checkActive($userId);
        if (!$active['status']) {
            return $active;
        }
        $user = Db::name('user')->where('id', $userId)->find();
        $current = Db::name('share')->where('owner', $userId)->count();
        if ((int)$user['max_share_pages'] <= 0 || $current + $quantity > (int)$user['max_share_pages']) {
            return ['status' => false, 'msg' => '分享页数量将超过当前套餐上限'];
        }
        return ['status' => true, 'msg' => ''];
    }

    public function canManualUnlock(int $userId, int $accountId): array
    {
        $active = $this->checkActive($userId);
        if (!$active['status']) {
            return $active;
        }
        $user = Db::name('user')->where('id', $userId)->find();
        $lastManual = (string)(Db::name('account')->where('id', $accountId)->value('last_manual_unlock_at') ?? '');
        if ($lastManual !== '' && (time() - strtotime($lastManual)) < ((int)$user['min_manual_interval'] * 60)) {
            return ['status' => false, 'msg' => '距离上次手动任务时间过短'];
        }
        return ['status' => true, 'msg' => ''];
    }

    /** 是否持有未过期的订阅（vip_expire_at 非空且未过期）。 */
    public function hasSubscription(int $userId): bool
    {
        $user = Db::name('user')->where('id', $userId)->find();
        return is_array($user) && !empty($user['vip_expire_at']) && strtotime($user['vip_expire_at']) >= time();
    }
}
