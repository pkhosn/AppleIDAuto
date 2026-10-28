SET @tg_notify_mode_exists := (
  SELECT COUNT(*) FROM information_schema.columns
  WHERE table_schema = DATABASE() AND table_name = 'user' AND column_name = 'tg_notify_mode'
);
SET @tg_notify_mode_sql := IF(@tg_notify_mode_exists = 0,
  'ALTER TABLE `user` ADD COLUMN `tg_notify_mode` varchar(16) NOT NULL DEFAULT ''all'' AFTER `tg_chat_id`',
  'SELECT 1'
);
PREPARE tg_notify_mode_statement FROM @tg_notify_mode_sql;
EXECUTE tg_notify_mode_statement;
DEALLOCATE PREPARE tg_notify_mode_statement;
