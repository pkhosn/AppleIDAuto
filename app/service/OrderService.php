<?php
declare (strict_types=1);

namespace app\service;

use think\facade\Db;
use think\Service;

class OrderService extends Service
{
    public function register(): void
    {
        $this->app->bind('orderService', OrderService::class);
    }

    public function fetchByUser(int $userId, int $perPage = 25)
    {
        return Db::name('order')->where('user_id', $userId)->order('id', 'desc')->paginate($perPage);
    }

    public function fetchAll(int $perPage = 25)
    {
        return Db::name('order')->alias('o')
            ->leftJoin('user u', 'u.id = o.user_id')
            ->field('o.*,u.username')
            ->order('o.id', 'desc')
            ->paginate($perPage);
    }

    public function createOrder(int $userId, int $planId, string $payType): array
    {
        if (!in_array($payType, ['alipay', 'wxpay'], true)) {
            return ['status' => false, 'msg' => '支付方式无效'];
        }
        $plan = $this->app->planService->fetch($planId);
        if (!$plan || !$plan['status']) {
            return ['status' => false, 'msg' => '套餐不存在或已停用'];
        }
        $orderNo = date('YmdHis') . secure_token(8);
        Db::name('order')->insert([
            'order_no' => $orderNo,
            'user_id' => $userId,
            'plan_id' => $plan['id'],
            'plan_name' => $plan['name'],
            'amount' => $plan['price'],
            'pay_type' => $payType,
            'status' => 'pending',
        ]);
        return ['status' => true, 'msg' => '订单创建成功', 'order_no' => $orderNo, 'plan' => $plan];
    }

    public function createRechargeOrder(int $userId, float $amount, string $payType): array
    {
        if (!in_array($payType, ['alipay', 'wxpay'], true)) {
            return ['status' => false, 'msg' => '支付方式无效'];
        }
        $amount = round($amount, 2);
        if ($amount < 1 || $amount > 100000) {
            return ['status' => false, 'msg' => '充值金额应在 1 至 100000 之间'];
        }
        $orderNo = date('YmdHis') . secure_token(8);
        Db::name('order')->insert([
            'order_no' => $orderNo, 'user_id' => $userId, 'plan_id' => 0, 'plan_name' => '余额充值',
            'amount' => number_format($amount, 2, '.', ''), 'pay_type' => $payType, 'kind' => 'recharge', 'status' => 'pending',
        ]);
        return ['status' => true, 'msg' => '充值订单已创建', 'order_no' => $orderNo, 'amount' => $amount];
    }

    public function paid(string $orderNo, string $tradeNo, string $money, string $payType): array
    {
        $owner = (int)Db::name('order')->where('order_no', $orderNo)->value('user_id');
        return Db::transaction(function () use ($orderNo, $tradeNo, $money, $payType, $owner) {
            $user = Db::name('user')->where('id', $owner)->lock(true)->find();
            if (!$user) return ['status' => false, 'msg' => '用户不存在'];
            $order = Db::name('order')->where('order_no', $orderNo)->lock(true)->find();
            if (!$order || (int)$order['user_id'] !== $owner) {
                return ['status' => false, 'msg' => '订单不存在'];
            }
            if ($order['status'] === 'paid') {
                return ['status' => true, 'msg' => '订单已处理'];
            }
            if ($order['status'] === 'cancelled') {
                return ['status' => false, 'msg' => '订单已取消'];
            }
            if ($tradeNo === '' || !in_array($payType, ['alipay', 'wxpay'], true)
                || $payType !== $order['pay_type']) {
                return ['status' => false, 'msg' => '支付回调参数不匹配'];
            }
            if (number_format((float)$order['amount'], 2, '.', '') !== number_format((float)$money, 2, '.', '')) {
                return ['status' => false, 'msg' => '支付金额不匹配'];
            }
            if (($order['kind'] ?? 'plan') === 'recharge') {
                Db::name('user')->where('id', (int)$order['user_id'])->inc('balance', (float)$order['amount'])->update();
                Db::name('order')->where('id', $order['id'])->update([
                    'status' => 'paid', 'trade_no' => $tradeNo, 'pay_type' => $payType, 'paid_at' => date('Y-m-d H:i:s'),
                ]);
                return ['status' => true, 'msg' => '充值成功'];
            }
            $plan = $this->app->planService->fetch((int)$order['plan_id']);
            if (!$plan) {
                return ['status' => false, 'msg' => '套餐不存在'];
            }
            $grant = $this->grantPlan((int)$order['user_id'], $plan);
            if (!$grant['status']) {
                return $grant;
            }
            Db::name('order')->where('id', $order['id'])->update([
                'status' => 'paid',
                'trade_no' => $tradeNo,
                'pay_type' => $payType,
                'paid_at' => date('Y-m-d H:i:s'),
            ]);
            return ['status' => true, 'msg' => '支付成功'];
        });
    }

    public function cancel(int $orderId, ?int $userId = null): array
    {
        return Db::transaction(function () use ($orderId, $userId): array {
            $order = Db::name('order')->where('id', $orderId)->lock(true)->find();
            if (!$order || ($userId !== null && (int)$order['user_id'] !== $userId)) {
                return ['status' => false, 'msg' => '订单不存在或无权操作'];
            }
            if ($order['status'] !== 'pending') {
                return ['status' => false, 'msg' => '仅待支付订单可以取消'];
            }
            $updated = Db::name('order')->where('id', $orderId)->where('status', 'pending')->update(['status' => 'cancelled']);
            return ['status' => $updated === 1, 'msg' => $updated === 1 ? '订单已取消' : '订单状态已变化，请刷新后重试'];
        });
    }

    public function balancePurchase(int $userId, int $planId): array
    {
        return Db::transaction(function () use ($userId, $planId): array {
            $plan = $this->app->planService->fetch($planId);
            if (!$plan || !$plan['status']) {
                return ['status' => false, 'msg' => '套餐不存在或已停用'];
            }
            $user = Db::name('user')->where('id', $userId)->lock(true)->find();
            if (!$user) {
                return ['status' => false, 'msg' => '用户不存在'];
            }
            $price = (float)$plan['price'];
            if ((float)$user['balance'] < $price) {
                return ['status' => false, 'msg' => '余额不足，请先充值'];
            }
            Db::name('user')->where('id', $userId)->dec('balance', $price)->update();
            $grant = $this->grantPlan($userId, $plan);
            if (!$grant['status']) {
                return $grant;
            }
            Db::name('order')->insert([
                'order_no' => date('YmdHis') . secure_token(8), 'user_id' => $userId, 'plan_id' => $plan['id'],
                'plan_name' => $plan['name'], 'amount' => number_format($price, 2, '.', ''),
                'pay_type' => 'balance', 'kind' => 'plan', 'status' => 'paid',
                'trade_no' => 'balance', 'paid_at' => date('Y-m-d H:i:s'),
            ]);
            return ['status' => true, 'msg' => '余额支付成功，订阅已开通'];
        });
    }

    public function grantPlan(int $userId, array $plan): array
    {
        return Db::transaction(function () use ($userId, $plan): array {
            $user = Db::name('user')->where('id', $userId)->lock(true)->find();
            if (!$user) {
                return ['status' => false, 'msg' => '用户不存在'];
            }
            $now = time();
            $base = !empty($user['vip_expire_at']) && strtotime($user['vip_expire_at']) > $now
                ? strtotime($user['vip_expire_at'])
                : $now;
            $expire = date('Y-m-d H:i:s', $base + ((int)$plan['days'] * 86400));
            Db::name('user')->where('id', $userId)->update([
                'vip_expire_at' => $expire,
                'max_accounts' => $plan['max_accounts'],
                'min_manual_interval' => $plan['min_manual_interval'],
                'max_share_pages' => $plan['enable_share'] ? $plan['max_share_pages'] : 0,
                'status' => 1,
            ]);
            return ['status' => true, 'msg' => '套餐已发放'];
        });
    }
}
