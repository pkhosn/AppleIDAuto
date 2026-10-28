-- 账号资料（国家/地区、语言、姓名）自动恢复：基线记录 + 开关字段
SET @column_exists := (
  SELECT COUNT(*) FROM information_schema.columns
  WHERE table_schema = DATABASE() AND table_name = 'account'
    AND column_name = 'enable_auto_restore_profile'
);
SET @column_sql := IF(@column_exists = 0,
  'ALTER TABLE `account`
     ADD COLUMN `enable_auto_restore_profile` tinyint(1) NOT NULL DEFAULT 0 AFTER `enable_auto_disable_two_factor`,
     ADD COLUMN `profile_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '''' AFTER `enable_auto_restore_profile`,
     ADD COLUMN `profile_country` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '''' AFTER `profile_name`,
     ADD COLUMN `profile_language` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '''' AFTER `profile_country`,
     ADD COLUMN `profile_recorded_at` datetime DEFAULT NULL AFTER `profile_language`,
     ADD COLUMN `profile_observed` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL AFTER `profile_recorded_at`',
  'SELECT 1');
PREPARE stmt FROM @column_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
