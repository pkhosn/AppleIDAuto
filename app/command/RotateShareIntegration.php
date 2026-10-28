<?php
declare(strict_types=1);

namespace app\command;

use RuntimeException;
use think\console\Command;
use think\console\Input;
use think\console\input\Option;
use think\console\Output;
use think\facade\Db;

/** Rotates a leaked legacy share capability and updates its consuming service atomically. */
class RotateShareIntegration extends Command
{
    protected function configure(): void
    {
        $this->setName('security:rotate-share-integration')
            ->addOption('link', null, Option::VALUE_REQUIRED, '现有分享代码')
            ->addOption('env', null, Option::VALUE_REQUIRED, '消费端 .env 路径')
            ->setDescription('轮换分享代码/密码并同步消费端环境变量（不会输出凭据）');
    }

    protected function execute(Input $input, Output $output): int
    {
        $oldLink = strtolower(trim((string)$input->getOption('link')));
        $envPath = (string)$input->getOption('env');
        if ($oldLink === '' || $envPath === '' || !is_file($envPath)) throw new RuntimeException('必须提供有效的 --link 和 --env');
        $original = file_get_contents($envPath);
        if (!is_string($original)) throw new RuntimeException('无法读取消费端环境文件');
        $newLink = bin2hex(random_bytes(16));
        $newPassword = rtrim(strtr(base64_encode(random_bytes(24)), '+/', '-_'), '=');
        $updated = $this->replace($this->replace($original, 'APPLE_SHARE_LINK', $newLink), 'APPLE_SHARE_PASSWORD', $newPassword);
        $owner = fileowner($envPath); $group = filegroup($envPath);
        try {
            Db::transaction(function () use ($oldLink, $newLink, $newPassword, $envPath, $updated, $owner, $group): void {
                $row = Db::name('share')->where('share_link', $oldLink)->lock(true)->find();
                if (!$row) throw new RuntimeException('目标分享不存在');
                Db::name('share')->where('id', $row['id'])->update([
                    'share_link' => $newLink,
                    'password' => password_hash($newPassword, PASSWORD_BCRYPT),
                    'group_id' => null,
                ]);
                $this->atomicWrite($envPath, $updated, $owner, $group);
            });
        } catch (\Throwable $exception) {
            $this->atomicWrite($envPath, $original, $owner, $group);
            throw $exception;
        }
        $output->writeln('<info>分享能力已轮换并同步；新值未写入终端或日志。</info>');
        return 0;
    }

    private function replace(string $content, string $key, string $value): string
    {
        $pattern = '/^' . preg_quote($key, '/') . '\s*=.*$/m';
        if (!preg_match($pattern, $content)) throw new RuntimeException("消费端缺少 {$key}");
        return (string)preg_replace($pattern, $key . '=' . $value, $content, 1);
    }

    private function atomicWrite(string $path, string $content, $owner, $group): void
    {
        $temporary = $path . '.rotate-' . bin2hex(random_bytes(6));
        if (file_put_contents($temporary, $content, LOCK_EX) === false
            || ($owner !== false && !chown($temporary, $owner))
            || ($group !== false && !chgrp($temporary, $group))
            || !chmod($temporary, 0600) || !rename($temporary, $path)) {
            @unlink($temporary);
            throw new RuntimeException('消费端环境文件更新失败');
        }
    }
}
