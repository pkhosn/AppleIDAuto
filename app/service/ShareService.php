<?php
declare (strict_types=1);

namespace app\service;

use app\model\SharePage;
use InvalidArgumentException;
use RuntimeException;
use think\facade\Db;
use think\Paginator;
use think\Service;

class ShareService extends Service
{
    /** Use the effective password-check cadence instead of the legacy shared interval. */
    public function isAccountHealthy(array $account, ?int $now = null, ?array $globals = null): bool
    {
        if (empty($account['enable']) || (string)($account['task_status'] ?? '') !== 'success') return false;
        $lastCheck = strtotime((string)($account['last_check'] ?? ''));
        if ($lastCheck === false) return false;
        $now = $now ?? time();
        $schedule = AutomationScheduleConfig::resolve($account, $globals);
        $passwordCheck = $schedule['features']['password_check'];
        // If password checking is explicitly disabled, retain the last successful
        // task result instead of declaring it stale against a schedule that no longer runs.
        if (!empty($schedule['active']) && empty($passwordCheck['enabled'])) return true;
        $minutes = !empty($schedule['active'])
            ? (int)$passwordCheck['effective_minutes']
            : max(1, (int)($account['check_interval'] ?? 30));
        return ($now - $lastCheck) < ($minutes + 2) * 60;
    }

    public function register()
    {
        $this->app->bind('shareService', shareService::class);
    }

    public function fetchByOwner($user_id, array $filters = [], int $perPage = 25): Paginator
    {
        return $this->applyStatusFilters(Db::name('share')->where('owner', $user_id), $filters)
            ->order('id', 'desc')->paginate($perPage, false, ['query' => $filters]);
    }

    public function fetchAll(array $filters = [], int $perPage = 25): Paginator
    {
        return $this->applyStatusFilters(Db::name('share'), $filters)
            ->order('id', 'desc')->paginate($perPage, false, ['query' => $filters]);
    }

    public function countAll($id = 0): int
    {
        if ($id == 0) {
            return Db::name('share')->count();
        } else {
            return Db::name('share')->where('owner', $id)->count();
        }
    }

    public function defaultHtmlTemplate(): string
    {
        return '<section class="share-welcome"><h2>账号使用说明</h2><p>请仅在授权范围内使用本页账号信息，不要转发链接或凭据。</p><ul><li>使用完毕后请及时退出相关服务。</li><li>如账号状态异常，请联系分享人处理。</li></ul></section>';
    }

    /** 分享页顶部“使用说明/使用提醒”区域的默认内容（可被模板/分享级 notice_html 覆盖）。 */
    public function defaultNoticeHtml(): string
    {
        return '<section class="share-notice"><strong>使用说明</strong><p>本页仅用于已授权的账号信息分享。请勿转发链接或凭据；使用完成后请及时退出相关服务。</p></section>'
            . '<section class="share-guidance" aria-label="使用提醒">'
            . '<div class="share-guidance-row"><span class="share-tag share-tag-blue">提示</span><p>复制后可直接粘贴账号或密码；页面不会自动保存到您的设备。</p></div>'
            . '<div class="share-guidance-row"><span class="share-tag share-tag-gray">状态</span><p>账号状态根据最近一次检测结果显示。如发现异常，请联系分享人处理。</p></div>'
            . '<div class="share-guidance-row"><span class="share-tag share-tag-gray">刷新</span><p>如需获取最新检测状态，可刷新本页面。</p></div>'
            . '<button class="share-refresh" type="button" onclick="window.location.reload()">刷新获取最新信息</button>'
            . '</section>';
    }

    /** Convert the batch dialog expiry choice into persisted share fields. */
    public function batchExpiry(array $input, bool $allowKeep = false): array
    {
        $mode = (string)($input['expiry_mode'] ?? 'never');
        if ($allowKeep && $mode === 'keep') {
            return [];
        }
        if ($mode === 'never') {
            return ['expire' => null, 'duration_minutes' => 0, 'activated_at' => null];
        }
        if ($mode === 'first_access') {
            $minutes = (int)($input['duration_minutes'] ?? 0);
            if ($minutes < 1 || $minutes > 525600) {
                throw new InvalidArgumentException('首次访问后的有效时长应为 1 至 525600 分钟');
            }
            return ['expire' => null, 'duration_minutes' => $minutes, 'activated_at' => null];
        }
        if ($mode === 'fixed') {
            $raw = trim((string)($input['expire'] ?? ''));
            $timestamp = strtotime($raw);
            if ($raw === '' || $timestamp === false || $timestamp <= time()) {
                throw new InvalidArgumentException('请填写晚于当前时间的固定过期时间');
            }
            return ['expire' => date('Y-m-d H:i:s', $timestamp), 'duration_minutes' => 0, 'activated_at' => null];
        }
        throw new InvalidArgumentException('过期方式无效');
    }

    /** Create identical, independent share links for the selected account group. */
    public function createBatch(int $owner, array $accountIds, int $quantity, array $expiry, string $remark = '', string $baseUrl = '', array $linkDefaults = [], bool $checkEntitlement = true): array
    {
        if ($quantity < 1 || $quantity > 100) {
            throw new InvalidArgumentException('单次生成数量应为 1 至 100');
        }
        $accountIds = array_values(array_unique(array_filter(array_map('intval', $accountIds))));
        if (!$accountIds) {
            throw new InvalidArgumentException('请至少选择一个账号');
        }
        $remark = trim($remark);
        if (mb_strlen($remark) > 255) {
            throw new InvalidArgumentException('备注不能超过 255 个字符');
        }
        $baseUrl = trim($baseUrl);
        if ($baseUrl !== '' && !preg_match('#^https?://[^\s/]+#i', $baseUrl)) {
            throw new InvalidArgumentException('自定义分享网址需以 http:// 或 https:// 开头');
        }
        if (mb_strlen($baseUrl) > 255) {
            throw new InvalidArgumentException('自定义分享网址不能超过 255 个字符');
        }

        return $this->app->entitlementService->createWithinQuota($owner, 'share', $quantity, $checkEntitlement, function () use ($owner, $accountIds, $quantity, $expiry, $remark, $baseUrl, $linkDefaults): array {
            $links = [];
            for ($index = 1; $index <= $quantity; $index++) {
                $link = $this->newShareLink();
                $label = $remark === '' ? '批量生成' : $remark;
                if ($quantity > 1) {
                    $label .= ' #' . $index;
                }
                $result = (new SharePage())->addSharePage(array_merge([
                    'share_link' => $link,
                    'account_list' => implode(',', $accountIds),
                    'owner' => $owner,
                    'password' => null,
                    'html' => $this->defaultHtmlTemplate(),
                    'remark' => $label,
                    'base_url' => $baseUrl === '' ? null : $baseUrl,
                ], $expiry, $linkDefaults), false);
                if (!$result['status']) {
                    throw new RuntimeException($result['msg']);
                }
                $links[] = $link;
            }
            return $links;
        });
    }

    public function updateBatch(array $ids, array $changes): array
    {
        $ids = array_values(array_unique(array_filter(array_map('intval', $ids))));
        if (!$ids) {
            throw new InvalidArgumentException('请至少选择一个分享链接');
        }
        $data = $this->batchExpiry($changes, true);
        if (($changes['remark_mode'] ?? 'keep') === 'replace') {
            $remark = trim((string)($changes['remark'] ?? ''));
            if (mb_strlen($remark) > 255) {
                throw new InvalidArgumentException('备注不能超过 255 个字符');
            }
            $data['remark'] = $remark;
        } elseif (($changes['remark_mode'] ?? 'keep') !== 'keep') {
            throw new InvalidArgumentException('备注修改方式无效');
        }
        if (!$data) {
            throw new InvalidArgumentException('请选择至少一项需要修改的内容');
        }
        Db::name('share')->whereIn('id', $ids)->update($data);
        return ['status' => true, 'msg' => '已批量修改 ' . count($ids) . ' 个分享链接'];
    }

    public function deleteBatch(array $ids): array
    {
        $ids = array_values(array_unique(array_filter(array_map('intval', $ids))));
        if (!$ids) {
            throw new InvalidArgumentException('请至少选择一个分享链接');
        }
        $deleted = Db::name('share')->whereIn('id', $ids)->delete();
        return ['status' => $deleted === count($ids), 'msg' => '已删除 ' . $deleted . ' 个分享链接'];
    }

    private function newShareLink(): string
    {
        for ($attempt = 0; $attempt < 8; $attempt++) {
            $link = secure_token(16);
            if (!Db::name('share')->where('share_link', $link)->find()) {
                return $link;
            }
        }
        throw new RuntimeException('生成唯一分享链接失败，请重试');
    }

    private function applyStatusFilters($query, array $filters)
    {
        $keyword = mb_substr(trim((string)($filters['keyword'] ?? '')), 0, 100);
        if ($keyword !== '') {
            $groupIds = Db::name('share_group')->whereLike('name', '%' . $keyword . '%')->column('id');
            $query->where(function ($query) use ($keyword, $groupIds) {
                $like = '%' . $keyword . '%';
                $query->whereLike('share_link', $like)
                    ->whereLike('remark', $like, 'OR');
                if ($groupIds) {
                    $query->whereOr('group_id', 'in', $groupIds);
                }
                if (ctype_digit($keyword)) {
                    $query->whereOr('id', (int)$keyword);
                }
            });
        }
        $usage = (string)($filters['usage'] ?? '');
        if ($usage === 'used') {
            $query->where('access_tracking', 1)->whereNotNull('first_access_at');
        } elseif ($usage === 'unused') {
            $query->where('access_tracking', 1)->whereNull('first_access_at');
        } elseif ($usage === 'unknown') {
            $query->where('access_tracking', 0);
        }
        $validity = (string)($filters['validity'] ?? '');
        if ($validity === 'expired') {
            $query->whereNotNull('expire')->where('expire', '<', date('Y-m-d H:i:s'));
        } elseif ($validity === 'active') {
            $query->whereRaw('(expire IS NULL OR expire >= ?)', [date('Y-m-d H:i:s')]);
        }
        return $query;
    }
}
