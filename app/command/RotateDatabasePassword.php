<?php
declare(strict_types=1);

namespace app\command;

use PDO;
use RuntimeException;
use think\console\Command;
use think\console\Input;
use think\console\Output;
use think\facade\Db;

/** Safely coordinates a database login password rotation with the panel .env. */
class RotateDatabasePassword extends Command
{
    protected function configure(): void
    {
        $this->setName('security:rotate-db-password')->setDescription('轮换当前数据库账号密码并同步 .env（不输出密码）');
    }

    protected function execute(Input $input, Output $output): int
    {
        $path = root_path() . '.env';
        $original = file_get_contents($path);
        if (!is_string($original)) throw new RuntimeException('无法读取 .env');
        $config = (array)config('database.connections.mysql');
        $oldPassword = (string)($config['password'] ?? '');
        $newPassword = bin2hex(random_bytes(32));
        $identity = (string)(Db::query('SELECT CURRENT_USER() AS identity')[0]['identity'] ?? '');
        if (!str_contains($identity, '@')) throw new RuntimeException('无法识别当前数据库账号');
        [$username, $host] = explode('@', $identity, 2);
        $account = "'" . str_replace("'", "''", $username) . "'@'" . str_replace("'", "''", $host) . "'";
        $replacement = $this->replaceSection($original, 'DATABASE', 'PASSWORD', $newPassword);
        Db::execute("ALTER USER {$account} IDENTIFIED BY '{$newPassword}'");
        try {
            $dsn = sprintf('mysql:host=%s;port=%d;dbname=%s;charset=%s',
                $config['hostname'], $config['hostport'], $config['database'], $config['charset'] ?? 'utf8mb4');
            new PDO($dsn, (string)$config['username'], $newPassword, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
            $this->atomicWrite($path, $replacement);
        } catch (\Throwable $exception) {
            Db::execute("ALTER USER {$account} IDENTIFIED BY '" . str_replace("'", "''", $oldPassword) . "'");
            throw new RuntimeException('数据库密码轮换失败，旧凭据已恢复', 0, $exception);
        }
        $output->writeln('<info>数据库密码已轮换并同步至 .env；请重新加载 PHP-FPM。</info>');
        return 0;
    }

    private function replaceSection(string $content, string $section, string $key, string $value): string
    {
        $pattern = '/(^\[' . preg_quote($section, '/') . '\]\s*(?:(?!^\[)[\s\S])*?^)' . preg_quote($key, '/') . '\s*=.*$/m';
        if (!preg_match($pattern, $content)) throw new RuntimeException("环境变量 {$section}.{$key} 不存在");
        return (string)preg_replace($pattern, '$1' . $key . ' = ' . $value, $content, 1);
    }

    private function atomicWrite(string $path, string $content): void
    {
        $temporary = $path . '.db-rotate-' . bin2hex(random_bytes(6));
        $owner = fileowner($path); $group = filegroup($path);
        if (file_put_contents($temporary, $content, LOCK_EX) === false
            || ($owner !== false && !chown($temporary, $owner)) || ($group !== false && !chgrp($temporary, $group))
            || !chmod($temporary, 0600) || !rename($temporary, $path)) {
            @unlink($temporary);
            throw new RuntimeException('无法安全更新 .env');
        }
    }
}
