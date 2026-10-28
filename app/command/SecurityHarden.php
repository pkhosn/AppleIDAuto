<?php
declare(strict_types=1);

namespace app\command;

use think\console\Command;
use think\console\Input;
use think\console\Output;
use think\facade\Db;

/** Idempotent schema/data migration for the 2026 security hardening release. */
class SecurityHarden extends Command
{
    protected function configure(): void
    {
        $this->setName('security:harden')->setDescription('净化历史 HTML、哈希分享密码并加密历史凭据');
    }

    protected function execute(Input $input, Output $output): int
    {
        $cipher = app()->credentialCipherService;
        if (!$cipher->keyConfigured()) {
            $output->error('请先配置 CREDENTIALS.CIPHER_KEY，迁移已中止且未修改数据。');
            return 1;
        }
        $this->schema();
        $sanitizer = app()->htmlSanitizerService;

        foreach (Db::name('account')->field('id,password,answer1,answer2,answer3,frontend_remark')->cursor() as $row) {
            $data = ['frontend_remark' => $sanitizer->sanitize((string)$row['frontend_remark'])];
            foreach (['password', 'answer1', 'answer2', 'answer3'] as $field) {
                $data[$field] = $cipher->encrypt((string)$row[$field], 'account.' . $field);
            }
            Db::name('account')->where('id', $row['id'])->update($data);
        }
        foreach (Db::name('user')->field('id,tg_bot_token,tg_chat_id,wx_pusher_id,webhook')->cursor() as $row) {
            $data = [];
            foreach (['tg_bot_token', 'tg_chat_id', 'wx_pusher_id', 'webhook'] as $field) {
                $data[$field] = $cipher->encrypt((string)$row[$field], 'user.' . $field);
            }
            Db::name('user')->where('id', $row['id'])->update($data);
        }
        foreach (Db::name('payment_config')->field('id,merchant_key')->cursor() as $row) {
            Db::name('payment_config')->where('id', $row['id'])->update([
                'merchant_key' => $cipher->encrypt((string)$row['merchant_key'], 'payment.merchant_key'),
            ]);
        }
        foreach (Db::name('proxy')->field('id,content')->cursor() as $row) {
            $plain = $cipher->decrypt((string)$row['content'], 'proxy.content');
            Db::name('proxy')->where('id', $row['id'])->update([
                'content' => $cipher->encrypt($plain, 'proxy.content'),
                'content_hash' => $cipher->fingerprint($plain, 'proxy.content'),
            ]);
        }
        foreach (Db::name('proxy_source')->field('id,source_url')->cursor() as $row) {
            Db::name('proxy_source')->where('id', $row['id'])->update([
                'source_url' => $cipher->encrypt((string)$row['source_url'], 'proxy_source.source_url'),
            ]);
        }
        foreach (['share', 'share_template'] as $table) {
            foreach (Db::name($table)->field('id,html,notice_html')->cursor() as $row) {
                Db::name($table)->where('id', $row['id'])->update([
                    'html' => $sanitizer->sanitize((string)$row['html']),
                    'notice_html' => $sanitizer->sanitize((string)$row['notice_html']),
                ]);
            }
        }
        foreach (['share', 'share_group'] as $table) {
            if (!$this->tableExists($table)) continue;
            foreach (Db::name($table)->field('id,password')->cursor() as $row) {
                $password = (string)$row['password'];
                if ($password !== '' && !str_starts_with($password, '$2y$')) {
                    Db::name($table)->where('id', $row['id'])->update(['password' => password_hash($password, PASSWORD_BCRYPT)]);
                }
            }
        }
        $unprotected = $this->unprotectedCount();
        if ($unprotected !== 0) {
            $output->error("检测到 {$unprotected} 个未完成保护的敏感字段，迁移失败。");
            return 2;
        }
        $output->writeln('<info>安全迁移完成；数据库敏感字段明文计数为 0。</info>');
        return 0;
    }

    private function schema(): void
    {
        Db::execute('ALTER TABLE `share` MODIFY `share_link` varchar(32) NOT NULL');
        foreach (['tg_bot_token', 'tg_chat_id', 'wx_pusher_id', 'webhook'] as $field) {
            Db::execute("ALTER TABLE `user` MODIFY `{$field}` text NULL");
        }
        Db::execute('ALTER TABLE `payment_config` MODIFY `merchant_key` text NOT NULL');
        Db::execute('ALTER TABLE `proxy` MODIFY `content` text NULL');
        Db::execute('ALTER TABLE `proxy_source` MODIFY `source_url` text NOT NULL');
        if (!$this->columnExists('proxy', 'content_hash')) {
            Db::execute("ALTER TABLE `proxy` ADD `content_hash` char(64) NULL AFTER `content`, ADD INDEX `idx_proxy_content_hash` (`owner`,`protocol`,`content_hash`)");
        }
        Db::execute("CREATE TABLE IF NOT EXISTS `credential_audit` (
          `id` bigint unsigned NOT NULL AUTO_INCREMENT,
          `actor_user_id` int unsigned NOT NULL,
          `account_id` int unsigned NOT NULL,
          `action` varchar(32) NOT NULL,
          `ip_address` varchar(64) NOT NULL DEFAULT '',
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`), KEY `actor_created` (`actor_user_id`,`created_at`), KEY `account_created` (`account_id`,`created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci");
    }

    private function tableExists(string $table): bool
    {
        return (bool)Db::query('SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=?', [$table]);
    }

    private function columnExists(string $table, string $column): bool
    {
        return (bool)Db::query('SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=? AND column_name=?', [$table, $column]);
    }

    private function unprotectedCount(): int
    {
        $cipher = app()->credentialCipherService;
        $count = 0;
        $targets = [
            'account' => ['password', 'answer1', 'answer2', 'answer3'],
            'user' => ['tg_bot_token', 'tg_chat_id', 'wx_pusher_id', 'webhook'],
            'payment_config' => ['merchant_key'],
            'proxy' => ['content'],
            'proxy_source' => ['source_url'],
        ];
        foreach ($targets as $table => $fields) {
            foreach (Db::name($table)->field(array_merge(['id'], $fields))->cursor() as $row) {
                foreach ($fields as $field) {
                    $value = (string)($row[$field] ?? '');
                    if ($value !== '' && !$cipher->isEncrypted($value)) $count++;
                }
            }
        }
        foreach (['share', 'share_group'] as $table) {
            if (!$this->tableExists($table)) continue;
            foreach (Db::name($table)->field('password')->cursor() as $row) {
                $value = (string)($row['password'] ?? '');
                if ($value !== '' && !str_starts_with($value, '$2y$')) $count++;
            }
        }
        return $count;
    }
}
