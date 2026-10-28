<?php
declare(strict_types=1);

namespace app\service;

use InvalidArgumentException;
use think\Service;

/** Rejects endpoints that could reach loopback, private, reserved or metadata networks. */
class NetworkAddressPolicy extends Service
{
    public function register(): void { $this->app->bind('networkAddressPolicy', self::class); }

    public function assertPublicHost(string $host): array
    {
        $host = strtolower(rtrim(trim($host, '[]'), '.'));
        if ($host === '' || $host === 'localhost' || str_ends_with($host, '.localhost')
            || str_ends_with($host, '.local') || $host === 'metadata.google.internal') {
            throw new InvalidArgumentException('目标主机不允许访问本机或内部网络');
        }
        $addresses = filter_var($host, FILTER_VALIDATE_IP) ? [$host] : $this->resolve($host);
        if (!$addresses) throw new InvalidArgumentException('目标主机无法解析');
        foreach ($addresses as $address) {
            if (!filter_var($address, FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE)
                || in_array($address, ['169.254.169.254', '100.100.100.200'], true)) {
                throw new InvalidArgumentException('目标主机解析到了私网或保留地址');
            }
        }
        return array_values(array_unique($addresses));
    }

    private function resolve(string $host): array
    {
        $records = function_exists('dns_get_record') ? @dns_get_record($host, DNS_A | DNS_AAAA) : [];
        $addresses = [];
        foreach (is_array($records) ? $records : [] as $record) {
            if (!empty($record['ip'])) $addresses[] = (string)$record['ip'];
            if (!empty($record['ipv6'])) $addresses[] = (string)$record['ipv6'];
        }
        if (!$addresses) $addresses = @gethostbynamel($host) ?: [];
        return $addresses;
    }
}
