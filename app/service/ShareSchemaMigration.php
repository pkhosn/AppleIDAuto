<?php
declare(strict_types=1);

namespace app\service;

use PDO;

/** Additive, repeatable upgrade shared by the installer and CLI. */
final class ShareSchemaMigration
{
    public static function apply(PDO $pdo): void
    {
        $pdo->exec("CREATE TABLE IF NOT EXISTS share_group (
            id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            owner INT UNSIGNED NOT NULL, name VARCHAR(100) NOT NULL,
            template_id INT UNSIGNED NULL, account_list TEXT NOT NULL,
            password VARCHAR(255) NULL, duration_minutes INT UNSIGNED NOT NULL DEFAULT 0,
            expire DATETIME NULL, base_url VARCHAR(255) NULL,
            access_tracking TINYINT(1) NOT NULL DEFAULT 1,
            create_time DATETIME NULL, update_time DATETIME NULL,
            KEY idx_share_group_owner(owner), KEY idx_share_group_template(template_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
        foreach (['share', 'share_group'] as $table) {
            $fields = array_column($pdo->query("SHOW COLUMNS FROM {$table}")->fetchAll(PDO::FETCH_ASSOC), 'Field');
            if (!in_array('password_cipher', $fields, true)) $pdo->exec("ALTER TABLE {$table} ADD COLUMN password_cipher TEXT NULL");
        }
        $columns = array_column($pdo->query('SHOW COLUMNS FROM share')->fetchAll(PDO::FETCH_ASSOC), 'Field');
        foreach (['enabled' => 'TINYINT(1) NOT NULL DEFAULT 1', 'embed_enabled' => 'TINYINT(1) NOT NULL DEFAULT 0', 'embed_origins' => 'TEXT NULL'] as $column => $type) {
            if (!in_array($column, $columns, true)) {
                $pdo->exec("ALTER TABLE share ADD COLUMN {$column} {$type}");
            }
        }
        $userColumns = array_column($pdo->query('SHOW COLUMNS FROM user')->fetchAll(PDO::FETCH_ASSOC), 'Field');
        foreach (['share_closed_text' => "VARCHAR(200) NOT NULL DEFAULT '暂时停止分享'", 'share_watermark_mode' => "VARCHAR(16) NOT NULL DEFAULT 'inherit'", 'share_watermark_text' => "VARCHAR(160) NOT NULL DEFAULT ''"] as $column => $type) {
            if (!in_array($column, $userColumns, true)) $pdo->exec("ALTER TABLE user ADD COLUMN {$column} {$type}");
        }
        foreach (['group_id', 'template_id'] as $column) {
            if (!in_array($column, $columns, true)) {
                $pdo->exec("ALTER TABLE share ADD COLUMN {$column} INT UNSIGNED NULL");
            }
            $indexes = array_column($pdo->query('SHOW INDEX FROM share')->fetchAll(PDO::FETCH_ASSOC), 'Key_name');
            if (!in_array('idx_share_' . $column, $indexes, true)) {
                $pdo->exec("ALTER TABLE share ADD INDEX idx_share_{$column} ({$column})");
            }
        }
    }
}
