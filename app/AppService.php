<?php
declare (strict_types = 1);

namespace app;

use think\Service;
use think\facade\View;

/**
 * 应用服务类
 */
class AppService extends Service
{
    public function register()
    {
        // 服务注册
    }

    public function boot()
    {
        try {
            $branding = $this->app->siteConfigService->branding();
            View::assign('site', $branding);
            View::assign('adminBase', $branding['admin_base']);
        } catch (\Throwable $exception) {
            View::assign('site', ['site_name' => 'AppleID 自动化管理', 'site_description' => '', 'site_logo' => '', 'admin_path' => 'admin', 'admin_base' => '/admin']);
            View::assign('adminBase', '/admin');
        }
    }
}
