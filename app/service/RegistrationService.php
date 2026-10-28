<?php
declare (strict_types=1);

namespace app\service;

use think\facade\Cache;
use think\facade\Log;
use think\Service;

class RegistrationService extends Service
{
    private const RATE_WINDOW = 3600;
    private const RATE_LIMIT = 8;

    public function register(): void
    {
        $this->app->bind('registrationService', self::class);
    }

    public function validate(string $username): array
    {
        $config = $this->app->siteConfigService->registration();
        if (!$config['registration_enabled']) {
            return ['status' => false, 'msg' => '当前暂未开放注册'];
        }
        if (!preg_match('/^[A-Za-z0-9_.-]{3,64}$/', $username)) {
            return ['status' => false, 'msg' => '用户名须为 3-64 位字母、数字、点、下划线或短横线'];
        }
        if (!$this->allowAttempt($username)) {
            return ['status' => false, 'msg' => '注册尝试过于频繁，请一小时后再试'];
        }
        $verified = $this->app->turnstileService->verify((string)$this->app->request->param('cf-turnstile-response', ''), $config);
        if (!$verified['status']) {
            Log::warning('Registration Turnstile rejected: ' . $verified['code']);
            return ['status' => false, 'msg' => $verified['msg']];
        }
        return ['status' => true, 'msg' => '验证通过'];
    }

    private function allowAttempt(string $username): bool
    {
        $ip = (string)($_SERVER['HTTP_CF_CONNECTING_IP'] ?? getUserIP());
        $key = 'registration:rate:' . hash('sha256', $ip . "\0" . strtolower($username));
        $state = Cache::get($key, ['count' => 0, 'started_at' => time()]);
        if (!is_array($state) || time() - (int)($state['started_at'] ?? 0) >= self::RATE_WINDOW) {
            $state = ['count' => 0, 'started_at' => time()];
        }
        if ((int)$state['count'] >= self::RATE_LIMIT) {
            return false;
        }
        $state['count']++;
        Cache::set($key, $state, self::RATE_WINDOW);
        return true;
    }
}
