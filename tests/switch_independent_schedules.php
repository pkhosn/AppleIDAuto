<?php
// Operational feature flag. Does not enable accounts or restart tasks.
if (PHP_SAPI !== 'cli') exit(1);
require dirname(__DIR__) . '/vendor/autoload.php';
$app = new think\App(dirname(__DIR__));
$app->initialize();
use think\facade\Db;
$mode = $argv[1] ?? '';
if (!in_array($mode, ['on', 'off', 'status'], true)) { fwrite(STDERR, "Usage: php tests/switch_independent_schedules.php on|off|status\n"); exit(1); }
try {
    if ($mode === 'status') {
        echo json_encode(app\service\AutomationScheduleConfig::globals(), JSON_PRETTY_PRINT) . "\n";
        exit(0);
    }
    foreach (Db::name('account')->field('id')->select() as $row) {
        $result = $app->backendService->setCurl((string)env('backend.api_url'), (string)env('backend.token'), 'scheduleStatus', (int)$row['id']);
        if (!$result['status'] || !array_key_exists('features', $result['data'] ?? [])) throw new RuntimeException('Install compatible scheduler before switching');
        if (($result['data']['status'] ?? '') === 'running') throw new RuntimeException('Wait for running tasks to finish before switching');
        foreach ($result['data']['features'] as $feature) {
            if (in_array($feature['result'] ?? '', ['running', 'uncertain'], true)) throw new RuntimeException('Resolve running/uncertain feature results before switching');
        }
    }
    $key = 'automation_independent_enabled';
    $value = $mode === 'on' ? '1' : '0';
    if (Db::name('site_config')->where('config_key', $key)->find()) Db::name('site_config')->where('config_key', $key)->update(['config_value' => $value]);
    else Db::name('site_config')->insert(['config_key' => $key, 'config_value' => $value]);
    echo "Independent scheduling: $mode. Sync within 60 seconds; account enable flags unchanged.\n";
} catch (Throwable $e) {
    fwrite(STDERR, $e->getMessage() . "\n");
    exit(1);
}
