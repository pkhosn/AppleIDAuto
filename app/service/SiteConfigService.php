<?php
declare (strict_types=1);

namespace app\service;

use RuntimeException;
use think\facade\Db;
use think\Service;

class SiteConfigService extends Service
{
    private const DEFAULTS = [
        'registration_enabled' => '0',
        'turnstile_enabled' => '0',
        'turnstile_site_key' => '',
        'turnstile_secret_key' => '',
        'turnstile_hostname' => '',
        'site_name' => 'AppleID 自动化管理',
        'site_description' => '统一管理账号、分享页与自动化任务。',
        'site_logo' => '',
        'admin_path' => 'admin',
    ];

    public function register(): void
    {
        $this->app->bind('siteConfigService', self::class);
    }

    public function registration(): array
    {
        $settings = $this->all();
        $enabled = $this->bool($settings['registration_enabled']);
        $turnstileEnabled = $this->bool($settings['turnstile_enabled']);
        $siteKey = trim($settings['turnstile_site_key']);
        $secretKey = trim($settings['turnstile_secret_key']);
        $hostnames = TurnstileService::normalizeAllowedHostnames($settings['turnstile_hostname']);
        return [
            'registration_enabled' => $enabled && $turnstileEnabled && $siteKey !== '' && $secretKey !== '',
            'turnstile_enabled' => $turnstileEnabled && $siteKey !== '' && $secretKey !== '',
            'turnstile_site_key' => $siteKey,
            'turnstile_secret_key' => $secretKey,
            // Keep malformed legacy data restrictive instead of silently treating it as an empty allow-list.
            'turnstile_hostname' => $hostnames ?? strtolower(trim($settings['turnstile_hostname'])),
            'turnstile_secret_configured' => $secretKey !== '',
        ];
    }

    public function saveRegistration(array $data): array
    {
        $current = $this->all();
        $enabled = !empty($data['registration_enabled']);
        $turnstileEnabled = !empty($data['turnstile_enabled']);
        $siteKey = trim((string)($data['turnstile_site_key'] ?? ''));
        $secretKey = trim((string)($data['turnstile_secret_key'] ?? ''));
        $hostname = TurnstileService::normalizeAllowedHostnames((string)($data['turnstile_hostname'] ?? ''));

        if ($siteKey === '') {
            $siteKey = $current['turnstile_site_key'];
        }
        if ($secretKey === '') {
            $secretKey = $current['turnstile_secret_key'];
        }
        if ($hostname === null) {
            return ['status' => false, 'msg' => '允许域名格式无效，多个域名请使用英文逗号（,）分隔'];
        }
        if (strlen($siteKey) > 256 || strlen($secretKey) > 256) {
            return ['status' => false, 'msg' => 'Turnstile 配置格式无效'];
        }
        if ($enabled && (!$turnstileEnabled || $siteKey === '' || $secretKey === '')) {
            return ['status' => false, 'msg' => '开放注册前必须启用并填写 Cloudflare Turnstile Site Key 与 Secret Key'];
        }

        $this->replace([
            'registration_enabled' => $enabled ? '1' : '0',
            'turnstile_enabled' => $turnstileEnabled ? '1' : '0',
            'turnstile_site_key' => $siteKey,
            'turnstile_secret_key' => $secretKey,
            'turnstile_hostname' => $hostname,
        ]);
        return ['status' => true, 'msg' => '登录、注册与人机验证配置已保存'];
    }

    public function branding(): array
    {
        $settings = $this->all();
        return [
            'site_name' => $settings['site_name'],
            'site_description' => $settings['site_description'],
            'site_logo' => $settings['site_logo'],
            'admin_path' => $settings['admin_path'],
            'admin_base' => '/' . $settings['admin_path'],
        ];
    }

    public function adminPath(): string
    {
        return $this->branding()['admin_path'];
    }

    public function saveBranding(array $data): array
    {
        $name = trim((string)($data['site_name'] ?? ''));
        $description = trim((string)($data['site_description'] ?? ''));
        $logo = trim((string)($data['site_logo'] ?? ''));
        $adminPath = strtolower(trim((string)($data['admin_path'] ?? '')));
        if ($name === '' || mb_strlen($name) > 80 || mb_strlen($description) > 255
            || !preg_match('/^[a-z][a-z0-9-]{2,39}$/', $adminPath)
            || in_array($adminPath, ['api', 'user', 'share', 'pay', 'install', 'index'], true)
        ) {
            return ['status' => false, 'msg' => '站点名称、描述或管理端路径格式无效'];
        }
        if ($logo !== '' && (!filter_var($logo, FILTER_VALIDATE_URL) || !preg_match('#^https?://#i', $logo) || strlen($logo) > 1024)) {
            return ['status' => false, 'msg' => 'Logo 必须是 http 或 https 图片地址'];
        }
        $this->replace([
            'site_name' => $name,
            'site_description' => $description,
            'site_logo' => $logo,
            'admin_path' => $adminPath,
        ]);
        return ['status' => true, 'msg' => '站点与管理端入口配置已保存'];
    }

    public function resetAdminPath(): void
    {
        $this->replace(['admin_path' => 'admin']);
    }

    private function all(): array
    {
        $rows = Db::name('site_config')->whereIn('config_key', array_keys(self::DEFAULTS))->column('config_value', 'config_key');
        return array_merge(self::DEFAULTS, array_map(static fn($value) => (string)$value, $rows));
    }

    private function replace(array $settings): void
    {
        Db::transaction(function () use ($settings) {
            foreach ($settings as $key => $value) {
                $exists = Db::name('site_config')->where('config_key', $key)->find();
                if ($exists) {
                    Db::name('site_config')->where('config_key', $key)->update(['config_value' => $value]);
                } elseif (Db::name('site_config')->insert(['config_key' => $key, 'config_value' => $value]) < 1) {
                    throw new RuntimeException('站点配置保存失败');
                }
            }
        });
    }

    private function bool(string $value): bool
    {
        return in_array(strtolower($value), ['1', 'true', 'yes', 'on'], true);
    }
}
