-- 自动检测并关闭双重认证：账号级开关字段
SET @column_exists := (
  SELECT COUNT(*) FROM information_schema.columns
  WHERE table_schema = DATABASE() AND table_name = 'account'
    AND column_name = 'enable_auto_disable_two_factor'
);
SET @column_sql := IF(@column_exists = 0,
  'ALTER TABLE `account` ADD COLUMN `enable_auto_disable_two_factor` tinyint(1) NOT NULL DEFAULT 0 AFTER `enable_delete_devices`',
  'SELECT 1');
PREPARE stmt FROM @column_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
