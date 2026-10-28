<?php
declare(strict_types=1);

namespace app\command;

use think\console\Command;
use think\console\Input;
use think\console\Output;
use think\facade\Cache;

class AdminPathReset extends Command
{
    protected function configure(): void
    {
        $this->setName('admin:path:reset')->setDescription('将管理端入口重置为 /admin');
    }

    protected function execute(Input $input, Output $output): int
    {
        $this->app->siteConfigService->resetAdminPath();
        Cache::clear();
        $routeCache = runtime_path('route.php');
        if (is_file($routeCache)) {
            unlink($routeCache);
        }
        $output->writeln('管理端入口已重置为 /admin');
        return 0;
    }
}
