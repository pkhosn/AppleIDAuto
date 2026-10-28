-- 邮件恢复配置。凭据由应用层 AES-256-GCM 加密后写入，禁止写入明文。
SET @account_table := DATABASE();

SET @add_recovery_email := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@account_table AND table_name='account' AND column_name='recovery_email')=0,
  'ALTER TABLE `account` ADD COLUMN `recovery_email` varchar(255) NOT NULL DEFAULT '''' AFTER `answer3`', 'SELECT 1');
PREPARE stmt FROM @add_recovery_email; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @add_mailbox_host := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@account_table AND table_name='account' AND column_name='mailbox_host')=0,
  'ALTER TABLE `account` ADD COLUMN `mailbox_host` varchar(255) NOT NULL DEFAULT '''' AFTER `recovery_email`', 'SELECT 1');
PREPARE stmt FROM @add_mailbox_host; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @add_mailbox_port := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@account_table AND table_name='account' AND column_name='mailbox_port')=0,
  'ALTER TABLE `account` ADD COLUMN `mailbox_port` smallint unsigned NOT NULL DEFAULT 993 AFTER `mailbox_host`', 'SELECT 1');
PREPARE stmt FROM @add_mailbox_port; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @add_mailbox_username := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@account_table AND table_name='account' AND column_name='mailbox_username')=0,
  'ALTER TABLE `account` ADD COLUMN `mailbox_username` varchar(255) NOT NULL DEFAULT '''' AFTER `mailbox_port`', 'SELECT 1');
PREPARE stmt FROM @add_mailbox_username; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @add_mailbox_cipher := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@account_table AND table_name='account' AND column_name='mailbox_password_cipher')=0,
  'ALTER TABLE `account` ADD COLUMN `mailbox_password_cipher` text NULL AFTER `mailbox_username`', 'SELECT 1');
PREPARE stmt FROM @add_mailbox_cipher; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @add_mailbox_tls := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@account_table AND table_name='account' AND column_name='mailbox_tls')=0,
  'ALTER TABLE `account` ADD COLUMN `mailbox_tls` tinyint(1) NOT NULL DEFAULT 1 AFTER `mailbox_password_cipher`', 'SELECT 1');
PREPARE stmt FROM @add_mailbox_tls; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @add_email_recovery := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@account_table AND table_name='account' AND column_name='enable_email_recovery')=0,
  'ALTER TABLE `account` ADD COLUMN `enable_email_recovery` tinyint(1) NOT NULL DEFAULT 0 AFTER `mailbox_tls`', 'SELECT 1');
PREPARE stmt FROM @add_email_recovery; EXECUTE stmt; DEALLOCATE PREPARE stmt;
