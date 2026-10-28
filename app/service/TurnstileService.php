<?php
declare (strict_types=1);

namespace app\service;

use think\Service;

class TurnstileService extends Service
{
    private const VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify';
    private const MAX_HOSTNAME_CONFIG_LENGTH = 2048;
    private const HOSTNAME_PATTERN = '/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$/';

    public function register(): void
    {
        $this->app->bind('turnstileService', self::class);
    }

    public static function normalizeAllowedHostnames(string $value): ?string
    {
        $value = strtolower(trim($value));
        if ($value === '') {
            return '';
        }
        if (strlen($value) > self::MAX_HOSTNAME_CONFIG_LENGTH) {
            return null;
        }

        $hostnames = [];
        foreach (explode(',', $value) as $hostname) {
            $hostname = trim($hostname);
            if ($hostname === '' || strlen($hostname) > 253 || !preg_match(self::HOSTNAME_PATTERN, $hostname)) {
                return null;
            }
            $hostnames[$hostname] = true;
        }

        return implode(',', array_keys($hostnames));
    }

    public static function hostnameAllowed(string $hostname, string $configuredHostnames): bool
    {
        $allowed = self::normalizeAllowedHostnames($configuredHostnames);
        if ($allowed === '') {
            return true;
        }
        if ($allowed === null) {
            return false;
        }

        return in_array(strtolower(trim($hostname)), explode(',', $allowed), true);
    }

    public function verify(string $token, array $config, string $expectedAction = 'register'): array
    {
        if ($token === '' || strlen($token) > 2048) {
            return ['status' => false, 'msg' => '请先完成人机验证', 'code' => 'missing-input-response'];
        }
        $handle = curl_init(self::VERIFY_URL);
        if ($handle === false) {
            return ['status' => false, 'msg' => '人机验证服务暂不可用，请稍后重试', 'code' => 'curl_init'];
        }
        curl_setopt_array($handle, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => http_build_query([
                'secret' => $config['turnstile_secret_key'],
                'response' => $token,
            ]),
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT => 3,
            CURLOPT_TIMEOUT => 6,
            CURLOPT_HTTPHEADER => ['Content-Type: application/x-www-form-urlencoded'],
        ]);
        $body = curl_exec($handle);
        $status = (int)curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
        $error = curl_error($handle);
        curl_close($handle);
        $result = is_string($body) ? json_decode($body, true) : null;
        if (!is_array($result) || $status < 200 || $status >= 300) {
            return ['status' => false, 'msg' => '人机验证服务暂不可用，请稍后重试', 'code' => $error !== '' ? 'network_error' : 'invalid_response'];
        }
        if (empty($result['success'])) {
            $codes = array_filter((array)($result['error-codes'] ?? []), 'is_string');
            return ['status' => false, 'msg' => '人机验证未通过，请刷新后重试', 'code' => implode(',', $codes) ?: 'verification_failed'];
        }
        if (($result['action'] ?? '') !== $expectedAction) {
            return ['status' => false, 'msg' => '人机验证用途不匹配，请刷新后重试', 'code' => 'action_mismatch'];
        }
        if (!self::hostnameAllowed(
            (string)($result['hostname'] ?? ''),
            (string)($config['turnstile_hostname'] ?? '')
        )) {
            return ['status' => false, 'msg' => '人机验证站点不匹配，请联系管理员', 'code' => 'hostname_mismatch'];
        }
        return ['status' => true, 'msg' => '验证通过', 'code' => 'ok'];
    }
}
