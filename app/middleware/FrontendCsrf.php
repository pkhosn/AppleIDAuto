<?php
declare(strict_types=1);

namespace app\middleware;

use Closure;
use think\facade\Session;

class FrontendCsrf
{
    public function handle($request, Closure $next)
    {
        if (in_array(strtoupper($request->method()), ['GET', 'HEAD', 'OPTIONS'], true)) return $next($request);
        $expected = (string)Session::get('frontend_csrf', '');
        $actual = (string)$request->header('x-csrf-token', '');
        if ($actual === '') $actual = (string)$request->param('_token', '');
        if ($expected === '' || $actual === '' || !hash_equals($expected, $actual)) {
            return json(['success' => false, 'data' => null, 'message' => '页面令牌已失效，请刷新后重试', 'errors' => [], 'meta' => []], 419);
        }
        return $next($request);
    }
}
