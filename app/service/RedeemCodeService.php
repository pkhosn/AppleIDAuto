<?php
declare (strict_types=1);

namespace app\service;

use think\facade\Db;
use think\Service;

class RedeemCodeService extends Service
{
    public function register(): void
    {
        $this->app->bind('redeemCodeService', RedeemCodeService::class);
    }

    public function fetchAll(int $perPage = 25)
    {
        return Db::name('redeem_code')->alias('r')
            ->leftJoin('plan p', 'p.id = r.plan_id')
            ->leftJoin('user u', 'u.id = r.used_by')
            ->field('r.*,p.name plan_name,u.username used_username')
            ->order('r.id', 'desc')
            ->paginate($perPage);
    }

    public function generate(int $planId, int $count, int $createdBy, ?string $expireAt): array
    {
        $plan = $this->app->planService->fetch($planId);
        if (!$plan || $count < 1 || $count > 200) {
            return ['status' => false, 'msg' => '生成参数错误'];
        }
        $codes = [];
        for ($i = 0; $i < $count; $i++) {
            $codes[] = [
                'code' => strtoupper(secure_token(16)),
                'plan_id' => $planId,
                'created_by' => $createdBy,
                'expire_at' => $expireAt,
            ];
        }
        Db::name('redeem_code')->insertAll($codes);
        return ['status' => true, 'msg' => '生成成功'];
    }

    public function redeem(string $code, int $userId): array
    {
        return Db::transaction(function () use ($code, $userId) {
            if (!Db::name('user')->where('id', $userId)->lock(true)->find()) {
                return ['status' => false, 'msg' => '用户不存在'];
            }
            $row = Db::name('redeem_code')->where('code', strtoupper(trim($code)))->lock(true)->find();
            if (!$row) {
                return ['status' => false, 'msg' => '卡密不存在'];
            }
            if ($row['status'] !== 'unused') {
                return ['status' => false, 'msg' => '卡密已使用'];
            }
            if (!empty($row['expire_at']) && strtotime($row['expire_at']) < time()) {
                return ['status' => false, 'msg' => '卡密已过期'];
            }
            $plan = $this->app->planService->fetch((int)$row['plan_id']);
            if (!$plan) {
                return ['status' => false, 'msg' => '套餐不存在'];
            }
            $grant = $this->app->orderService->grantPlan($userId, $plan);
            if (!$grant['status']) {
                return $grant;
            }
            Db::name('redeem_code')->where('id', $row['id'])->update([
                'status' => 'used',
                'used_by' => $userId,
                'used_at' => date('Y-m-d H:i:s'),
            ]);
            return ['status' => true, 'msg' => '兑换成功'];
        });
    }
}
