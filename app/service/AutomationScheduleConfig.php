<?php
declare(strict_types=1);

namespace app\service;

use InvalidArgumentException;
use think\facade\Db;

/** Versioned, additive configuration. Legacy check_interval is never overwritten. */
class AutomationScheduleConfig
{
    public const FEATURES = [
        'two_factor' => ['enable_auto_disable_two_factor', 30],
        'password_check' => ['enable_check_password_correct', 30],
        'profile_restore' => ['enable_auto_restore_profile', 360],
        'device_cleanup' => ['enable_delete_devices', 30],
        'password_update' => ['enable_auto_update_password', 1440],
    ];

    public static function globals(): array
    {
        $rows = Db::name('site_config')->where('config_key', 'like', 'automation_%')->column('config_value', 'config_key');
        $features = [];
        foreach (self::FEATURES as $key => $definition) {
            $features[$key] = (int)($rows['automation_interval_' . $key] ?? $definition[1]);
        }
        return ['version' => 1, 'active' => ($rows['automation_independent_enabled'] ?? '0') === '1', 'features' => $features];
    }

    public static function minutes($value): int
    {
        if ((!is_int($value) && !(is_string($value) && preg_match('/^[0-9]+$/D', $value))) || (int)$value < 1 || (int)$value > 43200) {
            throw new InvalidArgumentException('周期必须为 1 至 43200 分钟的整数');
        }
        return (int)$value;
    }

    public static function normalize(array $input): string
    {
        $items = is_array($input['features'] ?? null) ? $input['features'] : $input;
        $mode = $input['mode'] ?? null;
        if ($mode !== null && !in_array($mode, ['global', 'custom', 'mixed'], true)) {
            throw new InvalidArgumentException('周期模式必须选择跟随全局、账号自定义或逐项配置');
        }
        $forcedMode = in_array($mode, ['global', 'custom'], true) ? $mode : null;
        $legacySource = $forcedMode;
        if ($legacySource === null) {
            foreach ($items as $candidate) {
                if (is_array($candidate) && in_array($candidate['source'] ?? '', ['global', 'custom'], true)) {
                    $legacySource = $candidate['source'];
                    break;
                }
            }
        }
        $legacySource = $legacySource ?? 'global';
        $features = [];
        foreach (self::FEATURES as $key => $definition) {
            $item = $items[$key] ?? null;
            // v2 schedules created before device cleanup contain four entries.
            // Accept them unchanged and seed the additive feature safely off in
            // Account::normalizeInput via its existing account-level flag.
            if ($key === 'device_cleanup' && $item === null) {
                $item = ['source' => $legacySource, 'minutes' => $definition[1]];
            }
            if (!is_array($item) || !in_array($item['source'] ?? '', ['global', 'custom'], true)) {
                throw new InvalidArgumentException('每项功能均须选择跟随全局或账号自定义周期');
            }
            $features[$key] = ['source' => $forcedMode ?? $item['source'], 'minutes' => self::minutes($item['minutes'] ?? $definition[1])];
        }
        $sources = array_unique(array_column($features, 'source'));
        $resolvedMode = count($sources) === 1 ? $sources[0] : 'mixed';
        return json_encode(['version' => 4, 'mode' => $resolvedMode, 'features' => $features,
            'shared' => self::shared($input['shared'] ?? [])], JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
    }

    public static function shared($input): array
    {
        if (!is_array($input)) throw new InvalidArgumentException('共用周期配置格式不正确');
        $enabled = $input['enabled'] ?? false;
        $members = $input['features'] ?? [];
        if (!is_bool($enabled) || !is_array($members) || !array_is_list($members)) {
            throw new InvalidArgumentException('共用周期配置格式不正确');
        }
        foreach ($members as $key) {
            if (!is_string($key) || !array_key_exists($key, self::FEATURES)) {
                throw new InvalidArgumentException('共用周期包含未知任务');
            }
        }
        if ($enabled && !$members) throw new InvalidArgumentException('请至少选择一项共用周期任务');
        return ['enabled' => $enabled, 'minutes' => self::minutes($input['minutes'] ?? 30),
            'features' => array_values(array_unique($members))];
    }

    public static function resolve(array $account, ?array $globals = null): array
    {
        $globals = $globals ?? self::globals();
        $stored = json_decode((string)($account['automation_schedule'] ?? ''), true);
        $storedFeatures = is_array($stored['features'] ?? null) ? $stored['features'] : [];
        $shared = self::shared($stored['shared'] ?? []);
        $deviceCleanupConfigured = array_key_exists('device_cleanup', $storedFeatures);
        $legacyMode = in_array($stored['mode'] ?? '', ['global', 'custom'], true) ? $stored['mode'] : 'global';
        $features = [];
        foreach (self::FEATURES as $key => [$flag, $default]) {
            $item = $stored['features'][$key] ?? ['source' => $legacyMode, 'minutes' => $default];
            $source = in_array($item['source'] ?? '', ['global', 'custom'], true) ? $item['source'] : $legacyMode;
            $globalMinutes = (int)($globals['features'][$key] ?? $default);
            $minutes = (int)($item['minutes'] ?? $default);
            $features[$key] = [
                // A stale legacy flag must not activate a newly introduced
                // destructive feature until the account is explicitly saved
                // with a five-feature schedule.
                'enabled' => !empty($account[$flag]) && ($key !== 'device_cleanup' || $deviceCleanupConfigured),
                'source' => $source,
                'minutes' => $minutes, 'global_minutes' => $globalMinutes,
                'effective_minutes' => $shared['enabled'] && in_array($key, $shared['features'], true)
                    ? $shared['minutes'] : ($source === 'custom' ? $minutes : $globalMinutes),
            ];
        }
        $sources = array_unique(array_column($features, 'source'));
        $mode = count($sources) === 1 ? $sources[0] : 'mixed';
        return ['version' => 4, 'active' => $globals['active'], 'mode' => $mode, 'features' => $features, 'shared' => $shared];
    }

    public static function saveGlobals(array $input): array
    {
        try {
            $values = [];
            $current = self::globals();
            foreach (self::FEATURES as $key => $definition) {
                $raw = $input['features'][$key] ?? null;
                // Keep pre-device-cleanup clients compatible while the new
                // frontend rolls out; all older four values remain required.
                if ($key === 'device_cleanup' && $raw === null) {
                    $raw = $current['features'][$key] ?? $definition[1];
                }
                $values[$key] = self::minutes($raw);
            }
            Db::transaction(function () use ($values) {
                foreach ($values as $key => $value) {
                    $name = 'automation_interval_' . $key;
                    if (Db::name('site_config')->where('config_key', $name)->find()) {
                        Db::name('site_config')->where('config_key', $name)->update(['config_value' => (string)$value]);
                    } else Db::name('site_config')->insert(['config_key' => $name, 'config_value' => (string)$value]);
                }
            });
            return ['status' => true, 'msg' => '自动化周期已保存，60 秒内同步到跟随全局的账号'];
        } catch (InvalidArgumentException $e) {
            return ['status' => false, 'msg' => $e->getMessage()];
        }
    }
}
