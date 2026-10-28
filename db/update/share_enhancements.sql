-- Share-page activation countdown and site branding. Safe to execute repeatedly.
SET @share_duration_exists := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'share' AND column_name = 'duration_minutes');
SET @share_duration_sql := IF(@share_duration_exists = 0, 'ALTER TABLE `share` ADD COLUMN `duration_minutes` INT UNSIGNED NOT NULL DEFAULT 0 AFTER `expire`', 'SELECT 1');
PREPARE share_duration_statement FROM @share_duration_sql;
EXECUTE share_duration_statement;
DEALLOCATE PREPARE share_duration_statement;

SET @share_activated_exists := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'share' AND column_name = 'activated_at');
SET @share_activated_sql := IF(@share_activated_exists = 0, 'ALTER TABLE `share` ADD COLUMN `activated_at` DATETIME NULL AFTER `duration_minutes`', 'SELECT 1');
PREPARE share_activated_statement FROM @share_activated_sql;
EXECUTE share_activated_statement;
DEALLOCATE PREPARE share_activated_statement;

CREATE TABLE IF NOT EXISTS `site_config` (
  `config_key` varchar(64) NOT NULL,
  `config_value` text NOT NULL,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`config_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO `site_config` (`config_key`, `config_value`) VALUES
('site_name', 'AppleID 自动化管理'),
('site_description', '统一管理账号、分享页与自动化任务。'),
('site_logo', ''),
('admin_path', 'admin');
