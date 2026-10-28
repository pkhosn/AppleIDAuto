ALTER TABLE `account`
ADD COLUMN `task_status` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
ADD COLUMN `task_message` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
ADD COLUMN `task_last_sync_at` datetime DEFAULT NULL,
ADD COLUMN `task_next_run_at` datetime DEFAULT NULL,
ADD COLUMN `task_retry_count` int(10) unsigned NOT NULL DEFAULT '0';

UPDATE `account`
SET `task_status` = IF(`enable` = 1, 'running', 'disabled'),
    `task_message` = `message`,
    `task_last_sync_at` = IF(`last_check` = '2000-01-01 00:00:00', NULL, `last_check`),
    `task_next_run_at` = IF(`enable` = 1, DATE_ADD(NOW(), INTERVAL `check_interval` MINUTE), NULL);

CREATE TABLE `automation_task_log` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `account_id` int(10) unsigned NOT NULL,
  `operator_id` int(10) unsigned NOT NULL DEFAULT '0',
  `operator_type` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'system',
  `action` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` tinyint(1) NOT NULL DEFAULT '0',
  `message` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `ip` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT '',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `account_id` (`account_id`),
  KEY `operator_id` (`operator_id`),
  KEY `action` (`action`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
