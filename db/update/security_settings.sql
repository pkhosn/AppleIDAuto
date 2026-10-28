CREATE TABLE IF NOT EXISTS `site_config` (
  `config_key` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `config_value` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`config_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO `site_config` (`config_key`, `config_value`) VALUES
('registration_enabled', '0'),
('turnstile_enabled', '0'),
('turnstile_site_key', ''),
('turnstile_secret_key', ''),
('turnstile_hostname', '');

SET @username_index_exists := (
  SELECT COUNT(*) FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'user' AND index_name = 'uk_user_username'
);
SET @username_index_sql := IF(@username_index_exists = 0,
  'ALTER TABLE `user` ADD UNIQUE INDEX `uk_user_username` (`username`)',
  'SELECT 1'
);
PREPARE username_index_statement FROM @username_index_sql;
EXECUTE username_index_statement;
DEALLOCATE PREPARE username_index_statement;
