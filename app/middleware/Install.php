<?php
declare(strict_types=1);

namespace app\middleware;

use Closure;
use PDO;

class Install
{
    public function handle($request, Closure $next)
    {
        $path = trim($request->pathinfo(), '/');
        $isInstaller = $path === 'install' || strpos($path, 'install/') === 0;
        $lock = root_path() . 'runtime/install.lock';

        if (!is_file($lock) && $this->hasExistingAdmin()) {
            $directory = dirname($lock);
            if (!is_dir($directory)) {
                @mkdir($directory, 0775, true);
            }
            @file_put_contents($lock, date(DATE_ATOM) . "\n", LOCK_EX);
        }

        if (is_file($lock)) {
            return $isInstaller ? redirect('/') : $next($request);
        }

        return $isInstaller ? $next($request) : redirect('/install');
    }

    private function hasExistingAdmin(): bool
    {
        try {
            $database = (string)env('database.database', '');
            if ($database === '') {
                return false;
            }
            $pdo = new PDO(
                'mysql:host=' . env('database.hostname', '127.0.0.1')
                . ';port=' . env('database.hostport', '3306')
                . ';dbname=' . $database . ';charset=utf8mb4',
                (string)env('database.username', 'root'),
                (string)env('database.password', ''),
                [PDO::ATTR_TIMEOUT => 2, PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
            );
            return (int)$pdo->query('SELECT COUNT(*) FROM `user` WHERE is_admin = 1')->fetchColumn() > 0;
        } catch (\Throwable $e) {
            return false;
        }
    }
}
