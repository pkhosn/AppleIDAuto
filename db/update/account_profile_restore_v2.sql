-- 四项个人资料保护：结构化姓名 + 人工确认的保护基线。
SET @column_exists := (
  SELECT COUNT(*) FROM information_schema.columns
  WHERE table_schema = DATABASE() AND table_name = 'account'
    AND column_name = 'profile_family_name'
);
SET @column_sql := IF(@column_exists = 0,
  'ALTER TABLE `account`
     ADD COLUMN `profile_family_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '''' AFTER `profile_name`,
     ADD COLUMN `profile_middle_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '''' AFTER `profile_family_name`,
     ADD COLUMN `profile_given_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '''' AFTER `profile_middle_name`,
     ADD COLUMN `profile_baseline_confirmed_at` datetime DEFAULT NULL AFTER `profile_language`',
  'SELECT 1');
PREPARE stmt FROM @column_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
