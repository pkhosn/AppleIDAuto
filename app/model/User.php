<?php
declare (strict_types=1);

namespace app\model;

use think\Container;
use think\facade\Db;
use think\Model;

/**
 * @mixin Model
 */
class User extends Model
{
    protected $table = 'user';
    protected $pk = 'id';

    public function getTgBotTokenAttr($value): string { return $this->decryptCredential((string)$value, 'user.tg_bot_token'); }
    public function setTgBotTokenAttr($value): string { return $this->encryptCredential((string)$value, 'user.tg_bot_token'); }
    public function getTgChatIdAttr($value): string { return $this->decryptCredential((string)$value, 'user.tg_chat_id'); }
    public function setTgChatIdAttr($value): string { return $this->encryptCredential((string)$value, 'user.tg_chat_id'); }
    public function getWxPusherIdAttr($value): string { return $this->decryptCredential((string)$value, 'user.wx_pusher_id'); }
    public function setWxPusherIdAttr($value): string { return $this->encryptCredential((string)$value, 'user.wx_pusher_id'); }
    public function getWebhookAttr($value): string { return $this->decryptCredential((string)$value, 'user.webhook'); }
    public function setWebhookAttr($value): string { return $this->encryptCredential((string)$value, 'user.webhook'); }

    private function decryptCredential(string $value, string $context): string
    { return Container::getInstance()->make('credentialCipherService')->decrypt($value, $context); }

    private function encryptCredential(string $value, string $context): string
    { return Container::getInstance()->make('credentialCipherService')->encrypt($value, $context); }

    public function addUser($username, $password): array
    {
        $user = $this->where('username', $username)->find();
        if ($user) {
            return ['status' => false, 'msg' => '用户名已存在'];
        }
        $user = new User();
        $password = password_hash($password, PASSWORD_DEFAULT);
        $user->create(['username' => $username, 'password' => $password]);
        return ['status' => true, 'msg' => '注册成功'];
    }

    public function updateUser($data): array
    {
        $id = $data['id'];
        $username = $data['username'] ?? $this->username;
        $password = $data['password'] ?? null;
        // 如果已经设置信息，则不再查询数据库
        if (!$this) {
            $user = new User();
            $user = $user->fetch($id);
        } else {
            $user = $this;
        }
        if (!$user) {
            return ['status' => false, 'msg' => '用户不存在'];
        } else {
            $update = [];
            if ($password !== null && $password !== '') {
                $password = password_hash($password, PASSWORD_DEFAULT);
                $update['password'] = $password;
            }
            if ($username != $user->username) {
                // 检查用户名是否重复
                if ($this->where('username', $username)->find()) {
                    return ['status' => false, 'msg' => '用户名已存在'];
                }
                $update['username'] = $username;
            }
            if (isset($data['is_admin']) && $user->is_admin != $data['is_admin']) {
                $update['is_admin'] = $data['is_admin'];
            }
            foreach (['vip_expire_at', 'max_accounts', 'min_manual_interval', 'max_share_pages', 'balance', 'status'] as $field) {
                if (array_key_exists($field, $data)) {
                    $update[$field] = $data[$field];
                }
            }
            foreach (['tg_bot_token', 'tg_chat_id', 'tg_notify_mode', 'wx_pusher_id', 'webhook'] as $field) {
                if (array_key_exists($field, $data)) {
                    $update[$field] = (string)$data[$field];
                }
            }
            if (count($update) > 0) {
                $user->update($update, ['id' => $id]);
            }
            return ['status' => true, 'msg' => '更新成功'];
        }
    }

    public function fetch($id)
    {
        $user = $this->where('id', (int)$id)->find();
        if (!$user) return null;
        foreach (['tg_bot_token', 'tg_chat_id', 'wx_pusher_id', 'webhook', 'vip_expire_at'] as $field) {
            if ($user->$field === null) $user->$field = '';
        }
        if (!in_array((string)$user->tg_notify_mode, ['success', 'failure', 'all'], true)) {
            $user->tg_notify_mode = 'all';
        }
        return $user;
    }

    public function deleteUser($id): array
    {
        $user = $this->fetch($id);
        if (!$user) {
            return ['status' => false, 'msg' => '用户不存在'];
        } else {
            // 删除代理
            Db::name('proxy')->where('owner', $id)->delete();
            // 删除分享页
            Db::name('share')->where('owner', $id)->delete();
            // 删除账号
            Db::name('account')->where('owner', $id)->delete();
            $result = $user->delete();
            return ['status' => $result, 'msg' => $result ? '删除成功' : '删除失败'];
        }
    }
}
