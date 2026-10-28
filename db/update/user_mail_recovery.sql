-- 将救援邮箱从账号级配置迁移为用户级共享配置。
-- 可重复执行；密码密文原样迁移，不写入明文凭据。
SET @schema_name := DATABASE();

SET @add_user_enable := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@schema_name AND table_name='user' AND column_name='enable_mailbox')=0,
  'ALTER TABLE `user` ADD COLUMN `enable_mailbox` tinyint(1) NOT NULL DEFAULT 0 AFTER `webhook`', 'SELECT 1');
PREPARE stmt FROM @add_user_enable; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @add_user_email := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@schema_name AND table_name='user' AND column_name='recovery_email')=0,
  'ALTER TABLE `user` ADD COLUMN `recovery_email` varchar(255) NOT NULL DEFAULT '''' AFTER `enable_mailbox`', 'SELECT 1');
PREPARE stmt FROM @add_user_email; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @add_user_host := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@schema_name AND table_name='user' AND column_name='mailbox_host')=0,
  'ALTER TABLE `user` ADD COLUMN `mailbox_host` varchar(255) NOT NULL DEFAULT '''' AFTER `recovery_email`', 'SELECT 1');
PREPARE stmt FROM @add_user_host; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @add_user_port := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@schema_name AND table_name='user' AND column_name='mailbox_port')=0,
  'ALTER TABLE `user` ADD COLUMN `mailbox_port` smallint unsigned NOT NULL DEFAULT 993 AFTER `mailbox_host`', 'SELECT 1');
PREPARE stmt FROM @add_user_port; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @add_user_username := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@schema_name AND table_name='user' AND column_name='mailbox_username')=0,
  'ALTER TABLE `user` ADD COLUMN `mailbox_username` varchar(255) NOT NULL DEFAULT '''' AFTER `mailbox_port`', 'SELECT 1');
PREPARE stmt FROM @add_user_username; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @add_user_cipher := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@schema_name AND table_name='user' AND column_name='mailbox_password_cipher')=0,
  'ALTER TABLE `user` ADD COLUMN `mailbox_password_cipher` text NULL AFTER `mailbox_username`', 'SELECT 1');
PREPARE stmt FROM @add_user_cipher; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @add_user_tls := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@schema_name AND table_name='user' AND column_name='mailbox_tls')=0,
  'ALTER TABLE `user` ADD COLUMN `mailbox_tls` tinyint(1) NOT NULL DEFAULT 1 AFTER `mailbox_password_cipher`', 'SELECT 1');
PREPARE stmt FROM @add_user_tls; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @add_recovery_methods := IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=@schema_name AND table_name='account' AND column_name='recovery_methods')=0,
  'ALTER TABLE `account` ADD COLUMN `recovery_methods` varchar(128) NOT NULL DEFAULT ''email_password,email_security'' AFTER `enable_email_recovery`', 'SELECT 1');
PREPARE stmt FROM @add_recovery_methods; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 仅把每个用户最早一条已启用旧配置迁移到用户级，避免重复覆盖现有配置。
UPDATE `user` u
JOIN `account` a ON a.owner = u.id
SET u.enable_mailbox = 1,
    u.recovery_email = COALESCE(a.recovery_email, ''),
    u.mailbox_host = COALESCE(a.mailbox_host, ''),
    u.mailbox_port = COALESCE(a.mailbox_port, 993),
    u.mailbox_username = COALESCE(a.mailbox_username, ''),
    u.mailbox_password_cipher = a.mailbox_password_cipher,
    u.mailbox_tls = COALESCE(a.mailbox_tls, 1)
WHERE a.enable_email_recovery = 1
  AND COALESCE(u.enable_mailbox, 0) = 0
  AND COALESCE(u.recovery_email, '') = ''
  AND COALESCE(u.mailbox_host, '') = ''
  AND COALESCE(u.mailbox_username, '') = ''
  AND COALESCE(u.mailbox_password_cipher, '') = ''
  AND a.id = (SELECT MIN(a2.id) FROM `account` a2 WHERE a2.owner = u.id AND a2.enable_email_recovery = 1);

UPDATE `account`
SET recovery_methods = 'email_password,email_security'
WHERE enable_email_recovery = 1 AND (recovery_methods IS NULL OR recovery_methods = '');
