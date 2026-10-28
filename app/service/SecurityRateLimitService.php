<?php
declare(strict_types=1);

namespace app\service;

use think\facade\Cache;
use think\Service;

class SecurityRateLimitService extends Service
{
    public function register(): void
    {
        $this->app->bind('securityRateLimitService', self::class);
    }

    public function blocked(string $scope, string $identifier, int $limit, int $window): bool
    {
        $state = Cache::get($this->key($scope, $identifier), []);
        return is_array($state) && (int)($state['expires_at'] ?? 0) > time()
            && (int)($state['count'] ?? 0) >= $limit;
    }

    public function failure(string $scope, string $identifier, int $window): void
    {
        $key = $this->key($scope, $identifier);
        $state = Cache::get($key, []);
        if (!is_array($state) || (int)($state['expires_at'] ?? 0) <= time()) {
            $state = ['count' => 0, 'expires_at' => time() + $window];
        }
        $state['count'] = (int)$state['count'] + 1;
        Cache::set($key, $state, max(1, (int)$state['expires_at'] - time()));
    }

    public function clear(string $scope, string $identifier): void
    {
        Cache::delete($this->key($scope, $identifier));
    }

    private function key(string $scope, string $identifier): string
    {
        return 'security:rate:' . preg_replace('/[^a-z0-9_-]/i', '_', $scope)
            . ':' . hash('sha256', $identifier);
    }
}
