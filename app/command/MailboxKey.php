<?php
declare(strict_types=1);

namespace app\command;

use think\console\Command;
use think\console\Input;
use think\console\input\Option;
use think\console\Output;
use think\facade\Db;

class MailboxKey extends Command
{
    protected function configure(): void
    {
        $this->setName('mailbox:key')
            ->addOption('show', null, Option::VALUE_NONE, '输出一个新的 32 字节 Base64 密钥')
            ->addOption('rotate', null, Option::VALUE_NONE, '轮换密钥并重加密已有邮箱凭据（不会输出密钥）')
            ->setDescription('生成 BillionMail 救援邮箱凭据加密密钥');
    }

    protected function execute(Input $input, Output $output): int
    {
        if ($input->getOption('rotate')) return $this->rotate($output);
        if (!$input->getOption('show')) {
            $output->writeln('运行 <info>php think mailbox:key --show</info> 生成密钥。');
            $output->writeln('将输出写入根目录 .env 的 [MAILBOX] / CIPHER_KEY，随后重启 PHP-FPM。');
            return 0;
        }
        $output->writeln('[MAILBOX]');
        $output->writeln('CIPHER_KEY="' . base64_encode(random_bytes(32)) . '"');
        $output->writeln('<comment>请妥善保存该值；更换密钥后，已有邮箱凭据将无法解密。</comment>');
        return 0;
    }

    private function rotate(Output $output): int
    {
        $path = root_path() . '.env';
        $original = file_get_contents($path);
        if (!is_string($original)) throw new \RuntimeException('无法读取 .env');
        $newKey = base64_encode(random_bytes(32));
        $pattern = '/(^\[MAILBOX\]\s*(?:(?!^\[)[\s\S])*?^)CIPHER_KEY\s*=.*$/m';
        if (!preg_match($pattern, $original)) throw new \RuntimeException('缺少 MAILBOX.CIPHER_KEY');
        $updatedEnv = (string)preg_replace($pattern, '$1CIPHER_KEY = "' . $newKey . '"', $original, 1);
        $owner = fileowner($path); $group = filegroup($path);
        try {
            Db::transaction(function () use ($newKey, $path, $updatedEnv, $owner, $group): void {
                foreach (Db::name('account')->field('id,mailbox_password_cipher')->cursor() as $row) {
                    $value = (string)($row['mailbox_password_cipher'] ?? '');
                    if ($value !== '') Db::name('account')->where('id', $row['id'])->update([
                        'mailbox_password_cipher' => app()->mailboxCredentialService->rewrap($value, $newKey),
                    ]);
                }
                $this->atomicWrite($path, $updatedEnv, $owner, $group);
            });
        } catch (\Throwable $exception) {
            $this->atomicWrite($path, $original, $owner, $group);
            throw $exception;
        }
        $output->writeln('<info>救援邮箱密钥已轮换，历史凭据已重加密；请重新加载 PHP-FPM。</info>');
        return 0;
    }

    private function atomicWrite(string $path, string $content, $owner, $group): void
    {
        $temporary = $path . '.mailbox-' . bin2hex(random_bytes(6));
        if (file_put_contents($temporary, $content, LOCK_EX) === false
            || ($owner !== false && !chown($temporary, $owner)) || ($group !== false && !chgrp($temporary, $group))
            || !chmod($temporary, 0600) || !rename($temporary, $path)) {
            @unlink($temporary);
            throw new \RuntimeException('无法安全更新 .env');
        }
    }
}
