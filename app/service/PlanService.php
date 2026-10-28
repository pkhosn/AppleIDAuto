<?php
declare (strict_types=1);

namespace app\service;

use think\facade\Db;
use think\Paginator;
use think\Service;

class PlanService extends Service
{
    public function register(): void
    {
        $this->app->bind('planService', PlanService::class);
    }

    public function fetchEnabled(): array
    {
        return Db::name('plan')->where('status', 1)->order('price', 'asc')->select()->toArray();
    }

    public function fetchAll(int $perPage = 25): Paginator
    {
        return Db::name('plan')->order('id', 'desc')->paginate($perPage);
    }

    public function fetch($id): ?array
    {
        $plan = Db::name('plan')->where('id', $id)->find();
        return $plan ?: null;
    }

    public function savePlan(array $data, int $id = 0): array
    {
        $data['name'] = mb_substr(trim((string)($data['name'] ?? '')), 0, 64);
        $data['price'] = number_format((float)($data['price'] ?? -1), 2, '.', '');
        $data['days'] = (int)($data['days'] ?? 0);
        $data['max_accounts'] = (int)($data['max_accounts'] ?? -1);
        $data['min_manual_interval'] = (int)($data['min_manual_interval'] ?? -1);
        $data['max_share_pages'] = (int)($data['max_share_pages'] ?? -1);
        if ($data['name'] === '' || (float)$data['price'] < 0 || $data['days'] < 1
            || $data['max_accounts'] < 0 || $data['min_manual_interval'] < 0
            || $data['max_share_pages'] < 0) {
            return ['status' => false, 'msg' => '套餐参数错误'];
        }
        if ($id > 0) {
            if (!Db::name('plan')->where('id', $id)->find()) {
                return ['status' => false, 'msg' => '套餐不存在'];
            }
            Db::name('plan')->where('id', $id)->update($data);
            return ['status' => true, 'msg' => '修改成功'];
        }
        Db::name('plan')->insert($data);
        return ['status' => true, 'msg' => '添加成功'];
    }

    public function deletePlan(int $id): array
    {
        $used = Db::name('order')->where('plan_id', $id)->count();
        if ($used > 0) {
            Db::name('plan')->where('id', $id)->update(['status' => 0]);
            return ['status' => true, 'msg' => '套餐已有订单，已改为停用'];
        }
        $result = Db::name('plan')->where('id', $id)->delete();
        return ['status' => (bool)$result, 'msg' => $result ? '删除成功' : '删除失败'];
    }
}
