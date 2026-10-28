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
        $columns = array_column($pdo->query('SHOW COLUMNS FROM share')->fetchAll(PDO::FETCH_ASSOC), 'Field');
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
