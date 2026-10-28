<?php
declare (strict_types=1);

namespace app\service;

use InvalidArgumentException;
use think\facade\Db;
use think\Paginator;
use think\Service;

class ProxyService extends Service
{
    public function register()
    {
        $this->app->bind('proxyService', ProxyService::class);
    }

    public function getProtocolList(): array
    {
        return ['http', 'socks5'];
    }

    public function fetchByOwner($userId, int $perPage = 25): Paginator
    {
        return $this->decryptPaginator($this->endpointQuery()->where('proxy.owner', $userId)->paginate($perPage));
    }

    public function fetchAll(int $perPage = 25): Paginator
    {
        return $this->decryptPaginator($this->endpointQuery()->paginate($perPage));
    }

    public function fetchSourcesByOwner(int $owner): array
    {
        return array_map(fn($row) => $this->decryptSourceRow($row),
            Db::name('proxy_source')->where('owner', $owner)->order('id', 'desc')->select()->toArray());
    }

    public function fetchAllSources(): array
    {
        return array_map(fn($row) => $this->decryptSourceRow($row),
            Db::name('proxy_source')->order('id', 'desc')->select()->toArray());
    }

    public function fetchSource(int $id): ?array
    {
        $row = Db::name('proxy_source')->where('id', $id)->find();
        return $row ? $this->decryptSourceRow($row) : null;
    }

    private function decryptSourceRow(array $row): array
    {
        if (array_key_exists('source_url', $row)) {
            $row['source_url'] = $this->app->credentialCipherService->decrypt((string)$row['source_url'], 'proxy_source.source_url');
        }
        return $row;
    }

    private function endpointQuery()
    {
        return Db::name('proxy')->alias('proxy')->field(
            'proxy.*,(SELECT COUNT(*) FROM account WHERE account.proxy_id=proxy.id) AS bound_count'
        )->order('proxy.id', 'desc');
    }

    private function decryptPaginator(Paginator $paginator): Paginator
    {
        return $paginator->each(fn($row) => $this->decryptEndpointRow((array)$row));
    }

    public function decryptEndpointRow(array $row): array
    {
        if (array_key_exists('content', $row)) {
            $row['content'] = $this->app->credentialCipherService->decrypt((string)$row['content'], 'proxy.content');
        }
        return $row;
    }

    private function encryptedEndpoint(string $plaintext): array
    {
        return [
            'content' => $this->app->credentialCipherService->encrypt($plaintext, 'proxy.content'),
            'content_hash' => $this->app->credentialCipherService->fingerprint($plaintext, 'proxy.content'),
        ];
    }

    /**
     * Whether this account owner deliberately enabled at least one proxy.
     * Health is intentionally ignored: enabled but unhealthy nodes must fail
     * closed, while disabling every applicable node selects direct mode.
     */
    public function hasEnabledForOwner(int $owner): bool
    {
        return Db::name('proxy')->where('status', 1)
            ->whereIn('owner', [$owner, 0])->count() > 0;
    }

    public function leaseForAccount(int $accountId, int $owner): array
    {
        return Db::transaction(function () use ($accountId, $owner) {
            $account = Db::name('account')->where('id', $accountId)->lock(true)->find();
            if (!$account) {
                return [];
            }
            if (!empty($account['proxy_id'])) {
                $current = Db::name('proxy')->where('id', $account['proxy_id'])->lock(true)->find();
                if ($current && $this->selectable($current, $owner)
                    && Db::name('account')->where('proxy_id', $current['id'])->count() <= (int)$current['max_accounts']) {
                    Db::name('proxy')->where('id', $current['id'])->update(['last_use' => date('Y-m-d H:i:s')]);
                    return $this->publicEndpoint($current);
                }
                Db::name('account')->where('id', $accountId)->update(['proxy_id' => null]);
            }

            $rows = Db::name('proxy')->where('status', 1)->where('health_status', 'healthy')
                ->whereIn('owner', [$owner, 0])->lock(true)->select()->toArray();
            $candidates = [];
            foreach ($rows as $row) {
                if (!$this->selectable($row, $owner)) {
                    continue;
                }
                $row['bound_count'] = Db::name('account')->where('proxy_id', $row['id'])->count();
                if ((int)$row['bound_count'] < max((int)$row['max_accounts'], 1)) {
                    $candidates[] = $row;
                }
            }
            usort($candidates, function ($a, $b) use ($owner) {
                $privateOrder = ((int)$b['owner'] === $owner) <=> ((int)$a['owner'] === $owner);
                if ($privateOrder !== 0) {
                    return $privateOrder;
                }
                return [(int)$a['bound_count'], $a['last_use'], (int)$a['id']]
                    <=> [(int)$b['bound_count'], $b['last_use'], (int)$b['id']];
            });
            if (!$candidates) {
                return [];
            }
            $selected = $candidates[0];
            Db::name('account')->where('id', $accountId)->update(['proxy_id' => $selected['id']]);
            Db::name('proxy')->where('id', $selected['id'])->update(['last_use' => date('Y-m-d H:i:s')]);
            return $this->publicEndpoint($selected);
        });
    }

    private function selectable(array $row, int $owner): bool
    {
        return (int)$row['status'] === 1 && $row['health_status'] === 'healthy'
            && in_array((int)$row['owner'], [$owner, 0], true)
            && (empty($row['cooldown_until']) || strtotime($row['cooldown_until']) <= time());
    }

    private function publicEndpoint(array $row): array
    {
        $row = $this->decryptEndpointRow($row);
        return ['id' => (int)$row['id'], 'protocol' => $row['protocol'], 'content' => $row['content']];
    }

    public function normalizeEndpoint(string $content): string
    {
        $content = trim($content);
        $content = self::canonicalizeEndpoint($content);
        if ($content === null) {
            throw new InvalidArgumentException(
                '代理地址格式应为 hostname:port、hostname:port:username:password、'
                . 'username:password:hostname:port、username:password@hostname:port '
                . '或 hostname:port@username:password'
            );
        }
        $hostPort = str_contains($content, '@') ? substr($content, strrpos($content, '@') + 1) : $content;
        $host = str_starts_with($hostPort, '[')
            ? substr($hostPort, 1, (int)strpos($hostPort, ']') - 1)
            : substr($hostPort, 0, (int)strrpos($hostPort, ':'));
        $this->app->networkAddressPolicy->assertPublicHost($host);
        return $content;
    }

    /** Convert supported provider formats to username:password@hostname:port. */
    public static function canonicalizeEndpoint(string $content): ?string
    {
        if ($content === '' || strlen($content) > 512 || preg_match('/\s/', $content)) {
            return null;
        }
        $hostPattern = '(?:\[[0-9a-fA-F:]+\]|[A-Za-z0-9._-]+)';
        $portIsValid = static fn(string $port): bool => ctype_digit($port)
            && (int)$port >= 1 && (int)$port <= 65535;
        $hostPort = static function (string $value) use ($hostPattern, $portIsValid): ?array {
            if (!preg_match('/^(' . $hostPattern . '):(\d{1,5})$/', $value, $match)
                || !$portIsValid($match[2])) {
                return null;
            }
            return [$match[1], $match[2]];
        };
        $auth = static function (string $value): ?array {
            if (!preg_match('/^([^:@]+):([^@]+)$/', $value, $match)) {
                return null;
            }
            return [$match[1], $match[2]];
        };

        if (!str_contains($content, '@')) {
            $direct = $hostPort($content);
            if ($direct !== null) {
                return $direct[0] . ':' . $direct[1];
            }
            $parts = explode(':', $content);
            if (count($parts) !== 4 || in_array('', $parts, true)) {
                return null;
            }
            [$first, $second, $third, $fourth] = $parts;
            $hostFirst = preg_match('/^' . $hostPattern . '$/', $first) && $portIsValid($second);
            $authFirst = preg_match('/^' . $hostPattern . '$/', $third) && $portIsValid($fourth);
            if ($hostFirst && $authFirst) {
                $firstLooksHost = str_contains($first, '.') || filter_var($first, FILTER_VALIDATE_IP);
                $thirdLooksHost = str_contains($third, '.') || filter_var($third, FILTER_VALIDATE_IP);
                if ($thirdLooksHost && !$firstLooksHost) {
                    $hostFirst = false;
                } else {
                    $authFirst = false;
                }
            }
            if ($hostFirst) {
                return $third . ':' . $fourth . '@' . $first . ':' . $second;
            }
            if ($authFirst) {
                return $first . ':' . $second . '@' . $third . ':' . $fourth;
            }
            return null;
        }

        if (substr_count($content, '@') !== 1) {
            return null;
        }
        [$left, $right] = explode('@', $content, 2);
        $rightHost = $hostPort($right);
        $leftAuth = $auth($left);
        $leftHost = $hostPort($left);
        $rightAuth = $auth($right);
        if ($leftAuth !== null && $rightHost !== null && $leftHost !== null && $rightAuth !== null) {
            $leftLooksHost = str_contains($leftHost[0], '.') || filter_var($leftHost[0], FILTER_VALIDATE_IP);
            $rightLooksHost = str_contains($rightHost[0], '.') || filter_var($rightHost[0], FILTER_VALIDATE_IP);
            if ($leftLooksHost && !$rightLooksHost) {
                return $rightAuth[0] . ':' . $rightAuth[1] . '@' . $leftHost[0] . ':' . $leftHost[1];
            }
        }
        if ($leftAuth !== null && $rightHost !== null) {
            return $leftAuth[0] . ':' . $leftAuth[1] . '@' . $rightHost[0] . ':' . $rightHost[1];
        }
        if ($leftHost !== null && $rightAuth !== null) {
            return $rightAuth[0] . ':' . $rightAuth[1] . '@' . $leftHost[0] . ':' . $leftHost[1];
        }
        return null;
    }

    public function saveEndpoint(int $id, array $data): bool
    {
        if (!in_array($data['protocol'] ?? '', $this->getProtocolList(), true)) {
            throw new InvalidArgumentException('代理协议无效');
        }
        $plaintext = $this->normalizeEndpoint((string)($data['content'] ?? ''));
        $data = array_merge($data, $this->encryptedEndpoint($plaintext));
        $data['max_accounts'] = max(1, min((int)($data['max_accounts'] ?? 3), 100));
        $data['name'] = mb_substr(trim((string)($data['name'] ?? '')), 0, 64);
        $data['health_status'] = 'unknown';
        $data['last_check_at'] = null;
        $duplicate = Db::name('proxy')->where('owner', (int)$data['owner'])
            ->where('protocol', $data['protocol'])->where('content_hash', $data['content_hash'])
            ->whereNull('source_id');
        if ($id) {
            $duplicate->where('id', '<>', $id);
        }
        if ($duplicate->find()) {
            throw new InvalidArgumentException('相同代理节点已存在');
        }
        if ($id) {
            $ok = Db::name('proxy')->where('id', $id)->update($data) !== false;
            if (empty($data['status'])) {
                $this->unbindProxy($id);
            }
            return $ok;
        }
        return Db::name('proxy')->insert($data) > 0;
    }

    public function deleteEndpoint(int $id): bool
    {
        return Db::transaction(function () use ($id) {
            $this->unbindProxy($id);
            return Db::name('proxy')->where('id', $id)->delete() > 0;
        });
    }

    public function saveSource(int $id, array $data): bool
    {
        if (!in_array($data['protocol'] ?? '', $this->getProtocolList(), true)) {
            throw new InvalidArgumentException('代理协议无效');
        }
        $url = trim((string)($data['source_url'] ?? ''));
        $parts = parse_url($url);
        if (strlen($url) > 1024 || !$parts
            || !in_array(strtolower($parts['scheme'] ?? ''), ['http', 'https'], true)
            || empty($parts['host'])) {
            throw new InvalidArgumentException('提取链接必须是 HTTP 或 HTTPS 地址');
        }
        $data['source_url'] = $this->app->credentialCipherService->encrypt($url, 'proxy_source.source_url');
        $this->app->networkAddressPolicy->assertPublicHost((string)$parts['host']);
        $data['name'] = mb_substr(trim((string)($data['name'] ?? '')), 0, 64);
        $data['refresh_interval'] = max(60, min((int)($data['refresh_interval'] ?? 600), 86400));
        if ($id) {
            return Db::name('proxy_source')->where('id', $id)->update($data) !== false;
        }
        return Db::name('proxy_source')->insert($data) > 0;
    }

    public function deleteSource(int $id): bool
    {
        return Db::transaction(function () use ($id) {
            $ids = Db::name('proxy')->where('source_id', $id)->column('id');
            if ($ids) {
                Db::name('account')->whereIn('proxy_id', $ids)->update(['proxy_id' => null]);
                Db::name('proxy')->whereIn('id', $ids)->delete();
            }
            return Db::name('proxy_source')->where('id', $id)->delete() > 0;
        });
    }

    public function dueSources(): array
    {
        $rows = array_map(fn($row) => $this->decryptSourceRow($row),
            Db::name('proxy_source')->where('status', 1)->select()->toArray());
        return array_values(array_filter($rows, static function ($row) {
            return empty($row['last_fetch_at'])
                || strtotime($row['last_fetch_at']) + max((int)$row['refresh_interval'], 60) <= time();
        }));
    }

    public function markSourceDue(int $id): bool
    {
        return Db::name('proxy_source')->where('id', $id)->update(['last_fetch_at' => null]) !== false;
    }

    public function syncSource(int $id, array $endpoints, string $errorCode = ''): bool
    {
        $source = $this->fetchSource($id);
        if (!$source) {
            return false;
        }
        $now = date('Y-m-d H:i:s');
        if ($errorCode !== '') {
            Db::name('proxy_source')->where('id', $id)->update([
                'last_fetch_at' => $now, 'last_error' => mb_substr($errorCode, 0, 255),
            ]);
            return true;
        }
        $normalized = [];
        foreach (array_slice($endpoints, 0, 500) as $endpoint) {
            try {
                $normalized[$this->normalizeEndpoint((string)$endpoint)] = true;
            } catch (InvalidArgumentException $ignored) {
            }
        }
        return Db::transaction(function () use ($source, $normalized, $now) {
            foreach (array_keys($normalized) as $content) {
                $secured = $this->encryptedEndpoint($content);
                $existing = Db::name('proxy')->where('source_id', $source['id'])
                    ->where('protocol', $source['protocol'])->where('content_hash', $secured['content_hash'])->find();
                $data = [
                    'name' => $source['name'], 'protocol' => $source['protocol'],
                    'owner' => $source['owner'], 'source_id' => $source['id'], 'status' => 1,
                    'source_seen_at' => $now,
                ];
                $data = array_merge($data, $secured);
                if ($existing) {
                    Db::name('proxy')->where('id', $existing['id'])->update($data);
                } else {
                    $data['health_status'] = 'unknown';
                    $data['max_accounts'] = 3;
                    Db::name('proxy')->insert($data);
                }
            }
            $missingIds = Db::name('proxy')->where('source_id', $source['id'])
                ->where(function ($query) use ($now) {
                    $query->whereNull('source_seen_at')->whereOr('source_seen_at', '<', $now);
                })->column('id');
            if ($missingIds) {
                Db::name('proxy')->whereIn('id', $missingIds)->update(['status' => 0]);
                Db::name('account')->whereIn('proxy_id', $missingIds)->update(['proxy_id' => null]);
            }
            Db::name('proxy_source')->where('id', $source['id'])->update([
                'last_fetch_at' => $now, 'last_error' => '',
            ]);
            return true;
        });
    }

    public function dueHealthChecks(int $limit = 50): array
    {
        $cutoff = date('Y-m-d H:i:s', time() - max((int)env('PROXY_HEALTH_INTERVAL', 300), 60));
        $rows = Db::name('proxy')->field('id,protocol,content')->where('status', 1)
            ->whereRaw("(health_status='unknown' OR (health_status='cooldown' AND cooldown_until<=NOW()) "
                . "OR (health_status='healthy' AND (last_check_at IS NULL OR last_check_at<=?)))", [$cutoff])
            ->limit(max(1, min($limit, 100)))->select()->toArray();
        return array_map(fn($row) => $this->decryptEndpointRow($row), $rows);
    }

    public function markHealthDue(int $id): bool
    {
        return Db::name('proxy')->where('id', $id)->update(['health_status' => 'unknown', 'last_check_at' => null]) !== false;
    }

    /** Safe node health data for polling from the management UI. */
    public function healthSummary(int $id): ?array
    {
        $row = Db::name('proxy')->field('id,health_status,last_check_at,last_error,exit_ip,latency_ms')->where('id', $id)->find();
        if (!$row) return null;
        return [
            'id' => (int)$row['id'], 'health_status' => (string)($row['health_status'] ?? 'unknown'),
            'last_check_at' => (string)($row['last_check_at'] ?? ''), 'last_error' => (string)($row['last_error'] ?? ''),
            'exit_ip' => (string)($row['exit_ip'] ?? ''), 'latency_ms' => $row['latency_ms'] === null ? null : (int)$row['latency_ms'],
        ];
    }

    public function recordHealth(int $id, bool $success, string $exitIp = '', ?int $latencyMs = null, string $errorCode = ''): bool
    {
        return Db::transaction(function () use ($id, $success, $exitIp, $latencyMs, $errorCode) {
            $proxy = Db::name('proxy')->where('id', $id)->lock(true)->find();
            if (!$proxy) {
                return false;
            }
            $now = date('Y-m-d H:i:s');
            if ($success) {
                $data = [
                    'health_status' => 'healthy', 'consecutive_failures' => 0,
                    'success_count' => Db::raw('success_count+1'), 'cooldown_until' => null,
                    'last_check_at' => $now, 'last_error' => '',
                ];
                if ($exitIp !== '') {
                    $data['exit_ip'] = mb_substr($exitIp, 0, 64);
                }
                if ($latencyMs !== null) {
                    $data['latency_ms'] = $latencyMs;
                }
                return Db::name('proxy')->where('id', $id)->update($data) !== false;
            }
            $failures = (int)$proxy['consecutive_failures'] + 1;
            $threshold = max((int)env('PROXY_FAILURE_THRESHOLD', 3), 1);
            $cooldown = $failures >= $threshold;
            $updated = Db::name('proxy')->where('id', $id)->update([
                'health_status' => $cooldown ? 'cooldown' : ($proxy['health_status'] === 'healthy' ? 'healthy' : 'unknown'),
                'consecutive_failures' => $failures, 'failure_count' => Db::raw('failure_count+1'),
                'cooldown_until' => $cooldown
                    ? date('Y-m-d H:i:s', time() + max((int)env('PROXY_COOLDOWN_SECONDS', 1800), 60)) : null,
                'last_check_at' => $now, 'last_error' => mb_substr($errorCode ?: 'proxy_error', 0, 255),
            ]) !== false;
            if ($cooldown) {
                $this->unbindProxy($id);
            }
            return $updated;
        });
    }

    private function unbindProxy(int $id): void
    {
        Db::name('account')->where('proxy_id', $id)->update(['proxy_id' => null]);
    }
}
