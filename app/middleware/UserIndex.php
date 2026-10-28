<?php
declare (strict_types=1);

namespace app\middleware;

use Closure;
use app\model\User;
use think\facade\Session;

class UserIndex
{

    public function handle($request, Closure $next)
    {
        if (!Session::get('user_id')) {
            return $this->reject($request, '请先登录', 401);
        }
        $user = (new User())->fetch((int)Session::get('user_id'));
        if (!$user || (isset($user->status) && !$user->status)) {
            Session::delete('user_id');
            return $this->reject($request, '用户不存在或已被禁用', 403);
        }
        return $next($request);
    }

    private function reject($request, string $message, int $status)
    {
        $accept = strtolower((string)$request->header('accept', ''));
        if (strtolower((string)$request->header('x-requested-with', '')) === 'xmlhttprequest'
            || str_contains($accept, 'application/json')) {
            if (str_starts_with(trim((string)$request->pathinfo(), '/'), 'frontend-api/')) {
                return json(['success' => false, 'data' => null, 'message' => $message, 'errors' => [], 'meta' => []], $status);
            }
            return json(['status' => false, 'msg' => $message], $status);
        }
        return response(alert('error', $message, '2000', '/index'));
    }
}
