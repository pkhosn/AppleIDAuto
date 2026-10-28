<?php
declare (strict_types=1);

namespace app\service;

use app\model\User;
use think\facade\Db;
use think\facade\Session;
use think\Service;
use Throwable;

class AuthService extends Service
{
    public function register(): void
    {
        $this->app->bind('authService', AuthService::class);
    }

    public function userLogin($username, $password): array
    {
        $username = trim((string)$username);
        $ip = getUserIP();
        $identifier = $ip . "\0" . strtolower($username);
        $limiter = $this->app->securityRateLimitService;
        if ($limiter->blocked('login_pair', $identifier, 8, 900)
            || $limiter->blocked('login_ip', $ip, 30, 900)) {
            return ['status' => false, 'msg' => '登录尝试过于频繁，请稍后再试', 'http_status' => 429];
        }
        $user = Db::table('user')->where('username', $username)->find();
        if ($user) {
            if (password_verify($password, $user['password'])) {
                Session::regenerate(true);
                Session::set('user_id', $user['id']);
                $limiter->clear('login_pair', $identifier);
                return ['status' => true, 'msg' => '登录成功'];
            }
        }
        $limiter->failure('login_pair', $identifier, 900);
        $limiter->failure('login_ip', $ip, 900);
        return ['status' => false, 'msg' => '用户名或密码错误', 'http_status' => 401];
    }

    public function userRegister($username, $password): array
    {
        $username = trim((string)$username);
        $password = (string)$password;
        if (strlen($password) < 10 || strlen($password) > 72) {
            return ['status' => false, 'msg' => '密码长度须为 10-72 位'];
        }
        $guard = $this->app->registrationService->validate($username);
        if (!$guard['status']) {
            return $guard;
        }
        try {
            return (new User)->addUser($username, $password);
        } catch (Throwable $e) {
            if ((string)$e->getCode() === '23000') {
                return ['status' => false, 'msg' => '用户名已存在'];
            }
            return ['status' => false, 'msg' => '注册暂时不可用，请稍后重试'];
        }
    }

    public function isAdmin($user_id): bool
    {
        $user = Db::table('user')->where('id', $user_id)->find();
        if ($user) {
            return $user['is_admin'];
        }
        return false;
    }
}
