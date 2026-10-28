<?php
declare(strict_types=1);

namespace app\command;

use RuntimeException;
use think\console\Command;
use think\console\Input;
use think\console\Output;

/** Atomically rotates paired service tokens without printing them to a terminal or log. */
class SecurityRotateTokens extends Command
{
    protected function configure(): void
    {
        $this->setName('security:rotate-tokens')->setDescription('轮换面板、自动化及驱动令牌');
    }

    protected function execute(Input $input, Output $output): int
    {
        $root = root_path() . '.env';
        $automation = root_path() . 'automation-server/.env';
        $panelKey = bin2hex(random_bytes(32));
        $automationToken = bin2hex(random_bytes(32));
        $driverToken = bin2hex(random_bytes(32));

        $rootData = $this->replaceGlobal($this->read($root), 'API_KEY', $panelKey);
        $rootData = $this->replaceSection($rootData, 'BACKEND', 'TOKEN', $automationToken);
        $automationData = $this->replaceGlobal($this->read($automation), 'PANEL_API_KEY', $panelKey);
        $automationData = $this->replaceGlobal($automationData, 'AUTOMATION_TOKEN', $automationToken);
        $automationData = $this->replaceGlobal($automationData, 'ACCOUNT_DRIVER_TOKEN', $driverToken);

        $this->atomicWrite($root, $rootData);
        $this->atomicWrite($automation, $automationData);
        $output->writeln('<info>令牌已轮换且环境文件权限已设为 0600；请立即重建自动化容器。</info>');
        return 0;
    }

    private function read(string $path): string
    {
        $value = file_get_contents($path);
        if (!is_string($value)) throw new RuntimeException("无法读取 {$path}");
        return $value;
    }

    private function replaceGlobal(string $content, string $key, string $value): string
    {
        $pattern = '/^' . preg_quote($key, '/') . '\s*=.*$/m';
        if (!preg_match($pattern, $content)) throw new RuntimeException("环境变量 {$key} 不存在");
        return (string)preg_replace($pattern, $key . ' = ' . $value, $content, 1);
    }

    private function replaceSection(string $content, string $section, string $key, string $value): string
    {
        $pattern = '/(^\[' . preg_quote($section, '/') . '\]\s*(?:(?!^\[)[\s\S])*?^)' . preg_quote($key, '/') . '\s*=.*$/m';
        if (!preg_match($pattern, $content)) throw new RuntimeException("环境变量 {$section}.{$key} 不存在");
        return (string)preg_replace($pattern, '$1' . $key . ' = ' . $value, $content, 1);
    }

    private function atomicWrite(string $path, string $content): void
    {
        $owner = fileowner($path);
        $group = filegroup($path);
        $temporary = $path . '.security-' . bin2hex(random_bytes(6));
        if (file_put_contents($temporary, $content, LOCK_EX) === false
            || ($owner !== false && !chown($temporary, $owner))
            || ($group !== false && !chgrp($temporary, $group))
            || !chmod($temporary, 0600)
            || !rename($temporary, $path)) {
            @unlink($temporary);
            throw new RuntimeException("无法安全写入 {$path}");
        }
    }
}
