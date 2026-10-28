<?php
// CLI only. Repeatable schema extension for share groups and live template links.
if (PHP_SAPI !== 'cli') exit(1);
require dirname(__DIR__) . '/vendor/autoload.php';
$app = new think\App(dirname(__DIR__));
$app->initialize();
use think\facade\Db;

umask(0077);
$directory = '/root/apple-share-backups';
if (!is_dir($directory)) mkdir($directory, 0700, true);
$backup = $directory . '/database-' . date('Ymd-His') . '-' . bin2hex(random_bytes(4)) . '.json';
$tables = [
    'share' => Db::name('share')->select()->toArray(),
    'share_template' => Db::name('share_template')->select()->toArray(),
];
if (file_put_contents($backup, json_encode($tables, JSON_THROW_ON_ERROR)) === false) {
    throw new RuntimeException('Backup failed');
}

$columns = array_column(Db::query('SHOW COLUMNS FROM share'), 'Field');
if (!in_array('group_id', $columns, true)) Db::execute('ALTER TABLE share ADD COLUMN group_id INT UNSIGNED NULL, ADD INDEX idx_share_group_id (group_id)');
if (!in_array('template_id', $columns, true)) Db::execute('ALTER TABLE share ADD COLUMN template_id INT UNSIGNED NULL, ADD INDEX idx_share_template_id (template_id)');

Db::execute("CREATE TABLE IF NOT EXISTS share_group (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    owner INT UNSIGNED NOT NULL,
    name VARCHAR(100) NOT NULL,
    template_id INT UNSIGNED NULL,
    account_list TEXT NOT NULL,
    password VARCHAR(255) NULL,
    duration_minutes INT UNSIGNED NOT NULL DEFAULT 0,
    expire DATETIME NULL,
    base_url VARCHAR(255) NULL,
    access_tracking TINYINT(1) NOT NULL DEFAULT 1,
    create_time DATETIME NULL,
    update_time DATETIME NULL,
    PRIMARY KEY (id),
    KEY idx_share_group_owner (owner),
    KEY idx_share_group_template (template_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

echo "Backup: $backup\nShare group schema is ready.\n";
