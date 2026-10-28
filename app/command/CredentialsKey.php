<?php
declare(strict_types=1);

namespace app\command;

use think\console\Command;
use think\console\Input;
use think\console\input\Option;
use think\console\Output;

class CredentialsKey extends Command
{
    protected function configure(): void
    {
        $this->setName('credentials:key')->addOption('show', null, Option::VALUE_NONE, '输出新密钥')
            ->setDescription('生成字段级 AES-256-GCM 加密主密钥');
    }

    protected function execute(Input $input, Output $output): int
    {
        if (!$input->getOption('show')) {
            $output->writeln('运行 <info>php think credentials:key --show</info>，将值保存至 .env 的 [CREDENTIALS] / CIPHER_KEY。');
            return 0;
        }
        $output->writeln('[CREDENTIALS]');
        $output->writeln('CIPHER_KEY="' . base64_encode(random_bytes(32)) . '"');
        $output->writeln('<comment>请离线备份；遗失或更换该密钥会导致已有凭据无法解密。</comment>');
        return 0;
    }
}
