<?php
declare (strict_types=1);

namespace app\controller;

use app\BaseController;
use think\facade\Db;
use think\facade\Session;

class PayController extends BaseController
{
    public function create()
    {
        $userId = (int)Session::get('user_id');
        if (!$userId) {
            return alert("error", "请先登录", "2000", "/index");
        }
        $planId = (int)$this->request->param('plan_id');
        $payType = $this->request->param('type', '');
        $config = $this->app->paymentService->getConfig();
        if (!$config['status'] || empty($config['gateway']) || empty($config['pid']) || empty($config['merchant_key'])) {
            return alert("error", "支付配置未启用", "2000", "/user/order");
        }
        $order = $this->app->orderService->createOrder($userId, $planId, $payType);
        if (!$order['status']) {
            return alert("error", $order['msg'], "2000", "/user/plan");
        }
        $params = [
            'pid' => $config['pid'],
            'type' => $payType,
            'out_trade_no' => $order['order_no'],
            'notify_url' => $this->request->domain() . '/pay/notify',
            'return_url' => $this->request->domain() . '/pay/return',
            'name' => $order['plan']['name'],
            'money' => number_format((float)$order['plan']['price'], 2, '.', ''),
            'param' => (string)$userId,
            'sign_type' => 'MD5',
        ];
        $params['sign'] = $this->app->paymentService->sign($params, $config['merchant_key']);
        return view('/pay/create', ['gateway' => $config['gateway'], 'params' => $params]);
    }

    public function notify(): string
    {
        $params = $this->request->param();
        if (!$this->app->paymentService->verify($params)) {
            return 'fail';
        }
        if (($params['trade_status'] ?? '') !== 'TRADE_SUCCESS') {
            return 'fail';
        }
        $result = $this->app->orderService->paid(
            $params['out_trade_no'] ?? '',
            $params['trade_no'] ?? '',
            $params['money'] ?? '0.00',
            $params['type'] ?? ''
        );
        return $result['status'] ? 'success' : 'fail';
    }

    public function returnUrl(): string
    {
        return alert("success", "支付结果处理中，请稍后查看订单", "2000", "/user/order");
    }

    /**
     * 余额充值：创建充值订单后跳转易支付，到账后增加用户余额。
     */
    public function recharge()
    {
        $userId = (int)Session::get('user_id');
        if (!$userId) {
            return alert("error", "请先登录", "2000", "/index");
        }
        $config = $this->app->paymentService->getConfig();
        if (!$config['status'] || empty($config['gateway']) || empty($config['pid']) || empty($config['merchant_key'])) {
            return alert("error", "支付配置未启用", "2000", "/user/plan");
        }
        $amount = (float)$this->request->param('amount', 0);
        $payType = (string)$this->request->param('type', 'alipay');
        $order = $this->app->orderService->createRechargeOrder($userId, $amount, $payType);
        if (!$order['status']) {
            return alert("error", $order['msg'], "2000", "/user/plan");
        }
        $params = [
            'pid' => $config['pid'], 'type' => $payType, 'out_trade_no' => $order['order_no'],
            'notify_url' => $this->request->domain() . '/pay/notify',
            'return_url' => $this->request->domain() . '/pay/return',
            'name' => '余额充值', 'money' => number_format($order['amount'], 2, '.', ''),
            'param' => (string)$userId, 'sign_type' => 'MD5',
        ];
        $params['sign'] = $this->app->paymentService->sign($params, $config['merchant_key']);
        return view('/pay/create', ['gateway' => $config['gateway'], 'params' => $params]);
    }

    /**
     * 对待支付订单继续支付：复用该订单原套餐、金额与支付方式，跳转易支付。
     */
    public function repayOrder(string $orderNo)
    {
        $userId = (int)Session::get('user_id');
        if (!$userId) {
            return alert("error", "请先登录", "2000", "/index");
        }
        $config = $this->app->paymentService->getConfig();
        if (!$config['status'] || empty($config['gateway']) || empty($config['pid']) || empty($config['merchant_key'])) {
            return alert("error", "支付配置未启用", "2000", "/user/order");
        }
        $order = Db::name('order')->where('order_no', $orderNo)->find();
        if (!$order || (int)$order['user_id'] !== $userId) {
            return alert("error", "订单不存在", "2000", "/user/order");
        }
        if ($order['status'] !== 'pending') {
            return alert("error", "仅待支付订单可以继续支付", "2000", "/user/order");
        }
        $name = (string)$order['plan_name'];
        if (($order['kind'] ?? 'plan') !== 'recharge') {
            $plan = $this->app->planService->fetch((int)$order['plan_id']);
            if (!$plan || !$plan['status']) {
                return alert("error", "套餐不存在或已停用", "2000", "/user/order");
            }
        }
        $params = [
            'pid' => $config['pid'],
            'type' => (string)$order['pay_type'],
            'out_trade_no' => (string)$order['order_no'],
            'notify_url' => $this->request->domain() . '/pay/notify',
            'return_url' => $this->request->domain() . '/pay/return',
            'name' => $name,
            'money' => number_format((float)$order['amount'], 2, '.', ''),
            'param' => (string)$userId,
            'sign_type' => 'MD5',
        ];
        $params['sign'] = $this->app->paymentService->sign($params, $config['merchant_key']);
        return view('/pay/create', ['gateway' => $config['gateway'], 'params' => $params]);
    }
}
