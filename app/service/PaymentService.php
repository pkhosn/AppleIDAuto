<?php
declare (strict_types=1);

namespace app\service;

use think\facade\Db;
use think\Service;

class PaymentService extends Service
{
    public function register(): void
    {
        $this->app->bind('paymentService', PaymentService::class);
    }

    public function getConfig(): array
    {
        $config = Db::name('payment_config')->order('id', 'asc')->find();
        if ($config) {
            $config['merchant_key'] = $this->app->credentialCipherService->decrypt(
                (string)($config['merchant_key'] ?? ''), 'payment.merchant_key'
            );
        }
        return $config ?: ['gateway' => '', 'pid' => '', 'merchant_key' => '', 'status' => 0];
    }

    public function saveConfig(array $data): array
    {
        $config = Db::name('payment_config')->order('id', 'asc')->find();
        if (array_key_exists('merchant_key', $data)) {
            $data['merchant_key'] = $this->app->credentialCipherService->encrypt(
                (string)$data['merchant_key'], 'payment.merchant_key'
            );
        }
        if ($config) {
            Db::name('payment_config')->where('id', $config['id'])->update($data);
        } else {
            Db::name('payment_config')->insert($data);
        }
        return ['status' => true, 'msg' => '保存成功'];
    }

    public function sign(array $params, string $key): string
    {
        ksort($params);
        $items = [];
        foreach ($params as $name => $value) {
            if ($name === 'sign' || $name === 'sign_type' || $value === '' || $value === null) {
                continue;
            }
            $items[] = $name . '=' . $value;
        }
        return md5(implode('&', $items) . $key);
    }

    public function verify(array $params): bool
    {
        $config = $this->getConfig();
        if (empty($params['sign']) || empty($config['merchant_key'])) {
            return false;
        }
        return strtolower($params['sign']) === $this->sign($params, $config['merchant_key']);
    }
}
