<?php
declare (strict_types=1);

namespace app\model;

use think\Model;

/**
 * @mixin Model
 */
class SharePage extends Model
{
    protected $table = 'share';
    protected $pk = 'id';

    public function addSharePage($data, bool $checkEntitlement = true): array
    {
        $data = $this->normalizeInput($data);
        if ($this->fetchByLink($data['share_link'])) {
            return ['status' => false, 'msg' => '分享链接已存在'];
        }
        \think\Container::getInstance()->make('entitlementService')->createWithinQuota(
            (int)$data['owner'], 'share', 1, $checkEntitlement,
            static fn() => (new SharePage())->create($data)
        );
        return ['status' => true, 'msg' => '添加成功'];
    }

    public function fetchByLink($link): ?SharePage
    {
        $share = $this->where('share_link', $link)->find();
        if (!$share) return null;
        $share->account_list = array_map('intval', explode(",", $share->account_list));
        foreach (['password', 'html', 'notice_html', 'remark', 'expire', 'activated_at', 'first_access_at'] as $field) {
            if ($share->$field === null) $share->$field = '';
        }
        $share->duration_minutes = (int)($share->duration_minutes ?? 0);
        $share->access_tracking = (int)($share->access_tracking ?? 1);
        return $share;
    }

    public function updateSharePage($id, $data): array
    {
        $data = $this->normalizeInput($data);
        $share = $this->fetch((int)$id);
        if (!$share) {
            return ['status' => false, 'msg' => '分享链接不存在'];
        }
        // 检查链接重复
        if ($share->share_link != $data['share_link']) {
            if ($this->fetchByLink($data['share_link'])) {
                return ['status' => false, 'msg' => '分享链接已存在'];
            }
        }
        if ((int)$share->duration_minutes !== (int)$data['duration_minutes']) {
            $data['activated_at'] = null;
            $data['expire'] = null;
        } else {
            unset($data['activated_at']);
        }
        $share->update($data, ['id' => $id]);
        return ['status' => true, 'msg' => '修改成功'];
    }

    public function fetch($id): ?SharePage
    {
        $share = $this->where('id', $id)->find();
        if (!$share) return null;
        $share->account_list = array_map('intval', explode(",", $share->account_list));
        foreach (['password', 'html', 'notice_html', 'remark', 'expire', 'activated_at', 'first_access_at'] as $field) {
            if ($share->$field === null) $share->$field = '';
        }
        $share->duration_minutes = (int)($share->duration_minutes ?? 0);
        $share->access_tracking = (int)($share->access_tracking ?? 1);
        return $share;
    }

    /** Start the duration once, at the first successful share-page visit. */
    public function activateDuration(int $id): ?SharePage
    {
        $share = $this->fetch($id);
        if (!$share || $share->duration_minutes <= 0 || $share->activated_at !== '') {
            return $share;
        }
        $updated = $this->where('id', $id)->whereNull('activated_at')->update([
            'activated_at' => date('Y-m-d H:i:s'),
            'expire' => date('Y-m-d H:i:s', time() + $share->duration_minutes * 60),
        ]);
        return $updated ? $this->fetch($id) : $this->fetch($id);
    }

    /** Record only a successful, first public visit for newly tracked links. */
    public function markFirstAccess(int $id): ?SharePage
    {
        $this->where('id', $id)->where('access_tracking', 1)->whereNull('first_access_at')->update([
            'first_access_at' => date('Y-m-d H:i:s'),
        ]);
        return $this->fetch($id);
    }

    public function deleteSharePage($id): array
    {
        $share = $this->fetch((int)$id);
        if (!$share) {
            return ['status' => false, 'msg' => '分享链接不存在'];
        }
        $result = $share->delete();
        return ['status' => $result, 'msg' => $result ? '删除成功' : '删除失败'];
    }

    private function normalizeInput(array $data): array
    {
        $data['share_link'] = strtolower(trim((string)($data['share_link'] ?? '')));
        if (!preg_match('/^[a-z0-9_-]{4,32}$/', $data['share_link'])) {
            throw new \InvalidArgumentException('分享代码只能使用 4-32 位小写字母、数字、下划线或短横线');
        }
        $rawAccountList = (string)($data['account_list'] ?? '');
        $accountIds = array_map('intval', explode(',', $rawAccountList));
        $accountIds = array_values(array_unique(array_filter($accountIds)));
        $data['account_list'] = implode(',', $accountIds);
        if ($data['account_list'] === '') {
            throw new \InvalidArgumentException('请至少选择一个账号');
        }
        $data['embed_enabled'] = (int)(bool)($data['embed_enabled'] ?? false);
        $data['embed_origins'] = \app\service\ShareEmbedPolicy::normalize((string)($data['embed_origins'] ?? ''));
        $data['remark'] = trim((string)($data['remark'] ?? ''));
        $data['html'] = trim((string)($data['html'] ?? ''));
        $data['duration_minutes'] = max(0, min(525600, (int)($data['duration_minutes'] ?? 0)));
        if ($data['duration_minutes'] > 0) {
            $data['expire'] = null;
        } elseif (empty($data['expire'])) {
            $data['expire'] = null;
        }
        return $data;
    }
}
