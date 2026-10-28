-- Per-account execution-record retention and private share-template ownership.
-- Safe to execute repeatedly on MySQL 5.7+.

SET @last_manual_exists := (
  SELECT COUNT(*) FROM information_schema.columns
  WHERE table_schema = DATABASE() AND table_name = 'account' AND column_name = 'last_manual_unlock_at'
);
SET @last_manual_sql := IF(
  @last_manual_exists = 0,
  'ALTER TABLE `account` ADD COLUMN `last_manual_unlock_at` DATETIME NULL AFTER `min_manual_unlock`',
  'SELECT 1'
);
PREPARE last_manual_statement FROM @last_manual_sql;
EXECUTE last_manual_statement;
DEALLOCATE PREPARE last_manual_statement;

SET @record_index_exists := (
  SELECT COUNT(*) FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'unlock_record' AND index_name = 'idx_unlock_record_account_id'
);
SET @record_index_sql := IF(
  @record_index_exists = 0,
  'ALTER TABLE `unlock_record` ADD INDEX `idx_unlock_record_account_id` (`account_id`, `id`)',
  'SELECT 1'
);
PREPARE record_index_statement FROM @record_index_sql;
EXECUTE record_index_statement;
DEALLOCATE PREPARE record_index_statement;

-- Preserve existing successful manual-run cooldown state before old records are pruned.
UPDATE `account` a
JOIN (
  SELECT `account_id`, MAX(`time`) AS `last_at`
  FROM `unlock_record`
  WHERE `type` = 'manual' AND `status` = 1
  GROUP BY `account_id`
) r ON r.`account_id` = a.`id`
SET a.`last_manual_unlock_at` = IF(
  a.`last_manual_unlock_at` IS NULL OR a.`last_manual_unlock_at` < r.`last_at`,
  r.`last_at`, a.`last_manual_unlock_at`
);

UPDATE `account` a
JOIN (
  SELECT `account_id`, MAX(`created_at`) AS `last_at`
  FROM `automation_task_log`
  WHERE `action` = 'restart' AND `status` = 1 AND `operator_type` IN ('user', 'share')
  GROUP BY `account_id`
) r ON r.`account_id` = a.`id`
SET a.`last_manual_unlock_at` = IF(
  a.`last_manual_unlock_at` IS NULL OR a.`last_manual_unlock_at` < r.`last_at`,
  r.`last_at`, a.`last_manual_unlock_at`
);

-- Keep the built-in default private from the template manager and assign the tutorial to user 1.
UPDATE `share_template` current_template
JOIN `share_template` target_template ON target_template.`id` = 1 AND target_template.`name` = '模板默认'
SET current_template.`is_default` = 0
WHERE current_template.`owner` = 0 AND current_template.`id` <> target_template.`id`;
UPDATE `share_template` SET `owner` = 0, `is_default` = 1 WHERE `id` = 1 AND `name` = '模板默认';
UPDATE `share_template` current_template
JOIN `share_template` target_template ON target_template.`id` = 3 AND target_template.`name` = '小火箭下载教程'
SET current_template.`is_default` = 0
WHERE current_template.`owner` = 1 AND current_template.`id` <> target_template.`id`;
UPDATE `share_template` SET `owner` = 1, `is_default` = 1 WHERE `id` = 3 AND `name` = '小火箭下载教程';

-- Invalid links are detached; share HTML snapshots remain unchanged.
UPDATE `share` s
LEFT JOIN `share_template` t ON t.`id` = s.`template_id`
SET s.`template_id` = NULL
WHERE s.`template_id` IS NOT NULL AND (t.`id` IS NULL OR t.`owner` <> s.`owner`);

UPDATE `share_group` g
LEFT JOIN `share_template` t ON t.`id` = g.`template_id`
SET g.`template_id` = NULL
WHERE g.`template_id` IS NOT NULL AND (t.`id` IS NULL OR t.`owner` <> g.`owner`);

-- MySQL 5.7-compatible ranking: delete every row with at least ten newer rows for the same account.
DELETE FROM `unlock_record`
WHERE `id` IN (
  SELECT `id` FROM (
    SELECT old_record.`id`
    FROM `unlock_record` old_record
    JOIN `unlock_record` newer_record
      ON newer_record.`account_id` = old_record.`account_id`
     AND newer_record.`id` > old_record.`id`
    GROUP BY old_record.`id`
    HAVING COUNT(newer_record.`id`) >= 10
  ) stale_records
);
