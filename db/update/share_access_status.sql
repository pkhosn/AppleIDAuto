-- MySQL 5.7-compatible, repeatable migration for share access/status fields.
SET @db := DATABASE();
SET @exists := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@db AND table_name='share' AND column_name='first_access_at');
SET @sql := IF(@exists=0, 'ALTER TABLE `share` ADD COLUMN `first_access_at` datetime DEFAULT NULL AFTER `activated_at`', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@db AND table_name='share' AND column_name='access_tracking');
SET @sql := IF(@exists=0, 'ALTER TABLE `share` ADD COLUMN `access_tracking` tinyint(1) NOT NULL DEFAULT 1 AFTER `first_access_at`', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

UPDATE `share` SET `first_access_at`=`activated_at`, `access_tracking`=1
WHERE `activated_at` IS NOT NULL AND `first_access_at` IS NULL;
UPDATE `share` SET `access_tracking`=0 WHERE `first_access_at` IS NULL AND `activated_at` IS NULL;

SET @exists := (SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=@db AND table_name='share' AND index_name='idx_share_access_status');
SET @sql := IF(@exists=0, 'ALTER TABLE `share` ADD KEY `idx_share_access_status` (`owner`,`access_tracking`,`first_access_at`,`expire`)', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
