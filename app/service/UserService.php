<?php
declare (strict_types=1);

namespace app\service;

use app\model\User;
use think\facade\Db;
use think\Paginator;
use think\Service;

class UserService extends Service
{
    /**
     * 注册服务
     *
     * @return mixed
     */
    public function register(): void
    {
        $this->app->bind('userService', userService::class);
    }

    public function countAll($id = 0): int
    {
        if ($id == 0) {
            return Db::name('user')->count();
        } else {
            return Db::name('user')->where('owner', $id)->count();
        }
    }

    public function fetchAll(int $perPage = 25): Paginator
    {
        return Db::name('user')->paginate($perPage);
    }

    public function getNotifyMethods($id): array
    {
        $user = User::field('tg_bot_token,tg_chat_id,tg_notify_mode,wx_pusher_id,webhook')
            ->where('id', $id)
            ->find();
        $result = $user ? $user->toArray() : null;
        if (empty($result)) {
            return [];
        } else {
            $result['tg_notify_mode'] = in_array((string)($result['tg_notify_mode'] ?? ''), ['success', 'failure', 'all'], true)
                ? (string)$result['tg_notify_mode'] : 'all';
            return $result;
        }
    }

}
