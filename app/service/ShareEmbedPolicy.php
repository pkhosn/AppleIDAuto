<?php
declare(strict_types=1);

namespace app\service;

use InvalidArgumentException;

final class ShareEmbedPolicy
{
    public static function normalize(string $input): string
    {
        $origins = [];
        foreach (preg_split('/\R/', trim($input)) as $line) {
            $line = trim($line);
            if ($line === '') continue;
            $parts = parse_url($line);
            if (!$parts || !in_array(strtolower($parts['scheme'] ?? ''), ['https', 'http'], true)
                || empty($parts['host']) || isset($parts['user']) || isset($parts['pass'])
                || isset($parts['query']) || isset($parts['fragment'])
                || !in_array($parts['path'] ?? '', ['', '/'], true)
                || !preg_match('/^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/i', $parts['host'])
                || (isset($parts['port']) && ($parts['port'] < 1 || $parts['port'] > 65535))) {
                throw new InvalidArgumentException('嵌入来源须为完整 http(s) 网站地址，不含路径、通配符或登录信息');
            }
            $scheme = strtolower($parts['scheme']);
            $port = $parts['port'] ?? null;
            $origin = $scheme . '://' . strtolower($parts['host']);
            if ($port !== null && !(($scheme === 'https' && $port === 443) || ($scheme === 'http' && $port === 80))) {
                $origin .= ':' . $port;
            }
            $origins[$origin] = true;
        }
        if (count($origins) > 30) throw new InvalidArgumentException('最多配置 30 个嵌入来源');
        return implode("\n", array_keys($origins));
    }

    public static function ancestors(string $origins): string
    {
        $normalized = self::normalize($origins);
        return $normalized === '' ? '*' : str_replace("\n", ' ', $normalized);
    }
}
