<?php
declare(strict_types=1);

namespace app\service;

use app\model\SharePage;
use think\facade\Db;
use think\facade\Session;
use think\Service;

class ShareAccessService extends Service
{
    private const FAILURE_LIMIT = 8;
    private const FAILURE_WINDOW = 900;

    public function register(): void
    {
        $this->app->bind('shareAccessService', self::class);
    }

    public function resolve(string $link, string $password, string $clientIp, bool $activate = true, bool $browserSession = false): array
    {
        $link = strtolower(trim($link));
        $share = (new SharePage())->fetchByLink($link);
        if (!$share) return $this->fail('分享页不存在或已失效', 404);

        $rateKey = $clientIp . "\0" . $link;
        $limiter = $this->app->securityRateLimitService;
        if ($limiter->blocked('share', $rateKey, self::FAILURE_LIMIT, self::FAILURE_WINDOW)) {
            return $this->fail('尝试次数过多，请稍后再试', 429);
        }
        $sessionAuthorized = $browserSession && (int)Session::get($this->sessionKey($link), 0) >= time();
        if (!$sessionAuthorized && !$this->passwordMatches((string)$share->password, $password)) {
            $limiter->failure('share', $rateKey, self::FAILURE_WINDOW);
            return $this->fail($password === '' ? '缺少密码' : '密码错误', $password === '' ? 400 : 403);
        }
        $limiter->clear('share', $rateKey);

        if ($share->expire !== '' && strtotime((string)$share->expire) < time()) return $this->fail('分享页已过期', 410);
        $accountIds = array_values(array_unique(array_filter(array_map('intval', (array)$share->account_list))));
        $rows = $accountIds ? Db::name('account')->field('id,owner')->whereIn('id', $accountIds)->select()->toArray() : [];
        foreach ($rows as $row) {
            if ((int)$row['owner'] !== (int)$share->owner) return $this->fail('分享页账号授权无效', 403);
        }
        $share->account_list = array_values(array_map('intval', array_column($rows, 'id')));
        if ($activate) {
            $share = $share->activateDuration((int)$share->id) ?? $share;
            if ($share->expire !== '' && strtotime((string)$share->expire) < time()) return $this->fail('分享页已过期', 410);
            $share = $share->markFirstAccess((int)$share->id) ?? $share;
        }
        if ($browserSession) Session::set($this->sessionKey($link), time() + 900);
        if ((string)$share->password !== '' && !str_starts_with((string)$share->password, '$2y$')) {
            Db::name('share')->where('id', (int)$share->id)->update([
                'password' => password_hash($password, PASSWORD_BCRYPT),
            ]);
        }
        return ['status' => true, 'http_status' => 200, 'share' => $share, 'msg' => '获取成功'];
    }

    public function hashPassword(string $password): ?string
    {
        return $password === '' ? null : password_hash($password, PASSWORD_BCRYPT);
    }

    public function actionToken(string $link): string
    {
        $key = $this->sessionKey(strtolower(trim($link))) . ':action';
        $token = (string)Session::get($key, '');
        if ($token === '') { $token = bin2hex(random_bytes(24)); Session::set($key, $token); }
        return $token;
    }

    public function validActionToken(string $link, string $token): bool
    {
        $expected = (string)Session::get($this->sessionKey(strtolower(trim($link))) . ':action', '');
        return $expected !== '' && $token !== '' && hash_equals($expected, $token);
    }

    public function browserAuthorized(string $link): bool
    {
        return (int)Session::get($this->sessionKey(strtolower(trim($link))), 0) >= time();
    }

    private function passwordMatches(string $stored, string $provided): bool
    {
        if ($stored === '') return true;
        if ($provided === '') return false;
        if (str_starts_with($stored, '$2y$')) return password_verify($provided, $stored);
        return hash_equals($stored, $provided);
    }

    private function fail(string $message, int $status): array
    {
        return ['status' => false, 'http_status' => $status, 'share' => null, 'msg' => $message];
    }

    private function sessionKey(string $link): string
    {
        return 'share_access.' . hash('sha256', $link);
    }
}
