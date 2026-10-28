<?php
declare (strict_types=1);

namespace app\middleware;

use Closure;

class AdminPath
{
    public function handle($request, Closure $next)
    {
        $expected = $request->app()->siteConfigService->adminPath();
        $actual = strtolower((string)$request->route('adminPath'));
        if ($actual === $expected) {
            return $next($request);
        }
        if ($actual === 'admin' && session('?user_id')) {
            $suffix = preg_replace('#^admin/?#i', '', trim($request->pathinfo(), '/'));
            return redirect('/' . $expected . ($suffix === '' ? '' : '/' . $suffix));
        }
        abort(404, '页面不存在');
    }
}
