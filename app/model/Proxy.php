<?php
declare (strict_types=1);

namespace app\model;

use think\Model;
use think\facade\Db;

/**
 * @mixin Model
 */
class Proxy extends Model
{
    protected $table = 'proxy';
    protected $pk = 'id';

    public function addProxy($data): bool
    {
        $proxy = new Proxy();
        $proxy->create($data);
        return true;
    }

    public function updateProxy($id, $data): bool
    {
        $proxy = $this->fetch((int)$id);
        if (!$proxy) {
            return false;
        }
        $proxy->update($data, ['id' => $id]);
        return true;
    }

    public function updateUse($id): bool
    {
        $proxy = $this->fetch((int)$id);
        if (!$proxy) {
            return false;
        }
        Db::table('proxy')
            ->where('id', $id)
            ->update(['last_use' => date('Y-m-d H:i:s')]);
        return true;
    }

    public function setDisable($id): bool
    {
        $proxy = $this->fetch((int)$id);
        if (!$proxy) {
            return false;
        }
        Db::table('proxy')
            ->where('id', $id)
            ->update(['status' => 0]);
        return true;
    }

    public function fetch($id): ?Proxy
    {
        $proxy = $this->where('id', (int)$id)->find();
        if (!$proxy) return null;
        foreach (['name', 'content', 'exit_ip', 'last_error', 'last_check_at', 'cooldown_until'] as $field) {
            if ($proxy->$field === null) $proxy->$field = '';
        }
        return $proxy;
    }

    public function deleteProxy($id): bool
    {
        $proxy = $this->fetch((int)$id);
        if (!$proxy) {
            return false;
        }
        $proxy->delete();
        return true;
    }
}
