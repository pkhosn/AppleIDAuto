-- Run through `php think security:harden`; this file documents the idempotent schema target.
ALTER TABLE `share` MODIFY `share_link` varchar(32) NOT NULL;
ALTER TABLE `user`
  MODIFY `tg_bot_token` text NULL,
  MODIFY `tg_chat_id` text NULL,
  MODIFY `wx_pusher_id` text NULL,
  MODIFY `webhook` text NULL;
ALTER TABLE `payment_config` MODIFY `merchant_key` text NOT NULL;
ALTER TABLE `proxy`
  MODIFY `content` text NULL,
  ADD COLUMN `content_hash` char(64) NULL AFTER `content`,
  ADD INDEX `idx_proxy_content_hash` (`owner`,`protocol`,`content_hash`);
ALTER TABLE `proxy_source` MODIFY `source_url` text NOT NULL;
CREATE TABLE IF NOT EXISTS `credential_audit` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `actor_user_id` int unsigned NOT NULL,
  `account_id` int unsigned NOT NULL,
  `action` varchar(32) NOT NULL,
  `ip_address` varchar(64) NOT NULL DEFAULT '',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `actor_created` (`actor_user_id`,`created_at`),
  KEY `account_created` (`account_id`,`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
