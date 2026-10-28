<?php
// CLI only. Back up sensitive tables before the first schema change. Repeatable.
if (PHP_SAPI !== 'cli') exit(1);
$directory = rtrim(trim((string)getenv('SCHEDULE_BACKUP_DIR')), '/');
if ($directory === '' || $directory[0] !== '/') {
    fwrite(STDERR, "Set SCHEDULE_BACKUP_DIR to an absolute, private backup directory before migration.\n");
    exit(1);
}
require dirname(__DIR__) . '/vendor/autoload.php';
$app = new think\App(dirname(__DIR__));
$app->initialize();
use think\facade\Db;
use app\service\AutomationScheduleConfig as Cycles;
umask(0077);
if (!is_dir($directory)) mkdir($directory, 0700, true);
$backup = $directory . '/database-' . date('Ymd-His') . '-' . bin2hex(random_bytes(4)) . '.json';
$tables = ['account' => Db::name('account')->select()->toArray(), 'site_config' => Db::name('site_config')->select()->toArray()];
if (file_put_contents($backup, json_encode($tables, JSON_THROW_ON_ERROR)) === false) throw new RuntimeException('Backup failed');
$columns = array_column(Db::query('SHOW COLUMNS FROM account'), 'Field');
if (!in_array('automation_schedule', $columns, true)) Db::execute('ALTER TABLE account ADD COLUMN automation_schedule TEXT NULL');
$count = 0;
Db::transaction(function () use (&$count) {
    foreach (Db::name('account')->whereNull('automation_schedule')->select() as $row) {
        $features = [];
        // Never silently clamp a legacy interval and change password cadence.
        $minutes = Cycles::minutes($row['check_interval']);
        foreach (Cycles::FEATURES as $key => $definition) $features[$key] = ['source' => 'custom', 'minutes' => $minutes];
        Db::execute('UPDATE account SET automation_schedule = ? WHERE id = ? AND automation_schedule IS NULL', [Cycles::normalize($features), $row['id']]);
        $count++;
    }
});
echo "Backup: $backup\nMigrated accounts: $count; independent scheduler remains OFF.\n";
