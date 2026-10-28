-- 粘性代理池升级。执行前请备份数据库。
CREATE TABLE IF NOT EXISTS `proxy_source` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(64) NOT NULL DEFAULT '',
  `protocol` varchar(16) NOT NULL,
  `source_url` varchar(1024) NOT NULL,
  `owner` int(10) unsigned NOT NULL DEFAULT '0',
  `refresh_interval` int(10) unsigned NOT NULL DEFAULT '600',
  `status` tinyint(1) NOT NULL DEFAULT '1',
  `last_fetch_at` datetime DEFAULT NULL,
  `last_error` varchar(255) NOT NULL DEFAULT '',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `owner_status` (`owner`,`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE `proxy`
  ADD COLUMN `name` varchar(64) NOT NULL DEFAULT '' AFTER `id`,
  ADD COLUMN `source_id` int(10) unsigned DEFAULT NULL AFTER `owner`,
  ADD COLUMN `health_status` varchar(16) NOT NULL DEFAULT 'unknown' AFTER `status`,
  ADD COLUMN `exit_ip` varchar(64) NOT NULL DEFAULT '' AFTER `health_status`,
  ADD COLUMN `latency_ms` int(10) unsigned DEFAULT NULL AFTER `exit_ip`,
  ADD COLUMN `consecutive_failures` int(10) unsigned NOT NULL DEFAULT '0' AFTER `latency_ms`,
  ADD COLUMN `success_count` int(10) unsigned NOT NULL DEFAULT '0' AFTER `consecutive_failures`,
  ADD COLUMN `failure_count` int(10) unsigned NOT NULL DEFAULT '0' AFTER `success_count`,
  ADD COLUMN `cooldown_until` datetime DEFAULT NULL AFTER `failure_count`,
  ADD COLUMN `last_check_at` datetime DEFAULT NULL AFTER `cooldown_until`,
  ADD COLUMN `last_error` varchar(255) NOT NULL DEFAULT '' AFTER `last_check_at`,
  ADD COLUMN `max_accounts` int(10) unsigned NOT NULL DEFAULT '3' AFTER `last_error`,
  ADD COLUMN `source_seen_at` datetime DEFAULT NULL AFTER `max_accounts`,
  ADD KEY `proxy_select` (`owner`,`status`,`health_status`,`cooldown_until`),
  ADD KEY `source_content` (`source_id`,`protocol`,`content`(191));

ALTER TABLE `account`
  ADD COLUMN `proxy_id` int(10) unsigned DEFAULT NULL AFTER `owner`,
  ADD KEY `proxy_id` (`proxy_id`);

-- 旧的 +url 记录迁移为提取来源；迁移后的具体端点由调度器首次同步生成。
INSERT INTO `proxy_source` (`name`,`protocol`,`source_url`,`owner`,`refresh_interval`,`status`)
SELECT CONCAT('legacy-', `id`), REPLACE(`protocol`, '+url', ''), `content`, `owner`, 600, `status`
FROM `proxy` WHERE `protocol` IN ('http+url','socks5+url') AND `content` IS NOT NULL AND `content` <> '';

DELETE FROM `proxy` WHERE `protocol` IN ('http+url','socks5+url');
