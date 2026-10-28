<?php
declare(strict_types=1);

namespace app\middleware;

use Closure;
use think\Response;

class SecurityHeaders
{
    public function handle($request, Closure $next)
    {
        /** @var Response $response */
        $response = $next($request);
        $path = ltrim((string)$request->pathinfo(), '/');
        $headers = [
            'X-Content-Type-Options' => 'nosniff',
            'X-Frame-Options' => 'DENY',
            'Referrer-Policy' => str_starts_with($path, 'share') ? 'no-referrer' : 'strict-origin-when-cross-origin',
            'Permissions-Policy' => 'camera=(), microphone=(), geolocation=(), payment=()',
        ];
        if (preg_match('#^(?:share|shareapi|api|frontend-api)(?:/|$)#', $path)) {
            $headers['Cache-Control'] = 'no-store, private, max-age=0';
            $headers['Pragma'] = 'no-cache';
            $headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive';
        }
        if (str_starts_with($path, 'share/') && !str_ends_with($path, '/unlock')) {
            $headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'";
        }
        return $response->header($headers);
    }
}
