<?php
declare(strict_types=1);

namespace app\controller;

use app\BaseController;
use app\service\InstallService;
use think\facade\Session;

class InstallController extends BaseController
{
    public function index()
    {
        $service = new InstallService();
        $csrf = bin2hex(random_bytes(24));
        Session::set('install_csrf', $csrf);

        return view('install/index', [
            'checks' => $service->environment(),
            'ready' => $service->ready(),
            'csrf' => $csrf,
            'defaults' => $service->defaults($this->request->domain()),
        ]);
    }

    public function testConnection()
    {
        if (!$this->validCsrf()) {
            return json(['ok' => false, 'message' => '页面已过期，请刷新后重试'], 419);
        }
        return json((new InstallService())->test($this->request->post()));
    }

    public function install()
    {
        if (!$this->validCsrf()) {
            return json(['ok' => false, 'message' => '页面已过期，请刷新后重试'], 419);
        }
        try {
            (new InstallService())->install($this->request->post());
            Session::delete('install_csrf');
            return json(['ok' => true, 'message' => '安装完成', 'redirect' => '/']);
        } catch (\Throwable $e) {
            return json(['ok' => false, 'message' => mb_substr($e->getMessage(), 0, 240)], 422);
        }
    }

    private function validCsrf(): bool
    {
        $expected = (string)Session::get('install_csrf', '');
        $actual = (string)$this->request->post('_token', '');
        return $expected !== '' && $actual !== '' && hash_equals($expected, $actual);
    }
}
