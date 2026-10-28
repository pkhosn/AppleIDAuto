<?php
declare (strict_types=1);

namespace app\model;

use think\facade\Db;
use think\Model;

/**
 * @mixin Model
 */
class UnlockRecord extends Model
{
    /** 每个 Apple 账号保留的执行记录数。 */
    public const RETENTION_LIMIT = 10;

    protected $table = 'unlock_record';
    protected $pk = 'id';

    public function truncate()
    {
        $db = Db::connect();
        $db->execute('TRUNCATE TABLE ' . $this->table);
    }

    public function addRecord($data)
    {
        $data['time'] = date('Y-m-d H:i:s');
        $this->create($data);
        try {
            $this->pruneAccount((int)$data['account_id']);
        } catch (\Throwable $exception) {
            // Retention maintenance must never turn a successful report into a failed API call.
        }
    }

    /** 保留指定账号最新的 N 条记录，返回删除条数。 */
    public function pruneAccount(int $accountId, int $limit = self::RETENTION_LIMIT): int
    {
        $limit = max(1, $limit);
        if ($accountId <= 0) return 0;
        $keepIds = Db::name($this->table)->where('account_id', $accountId)
            ->order('id', 'desc')->limit($limit)->column('id');
        if (count($keepIds) < $limit) return 0;
        $cutoffId = min(array_map('intval', $keepIds));
        return (int)Db::name($this->table)->where('account_id', $accountId)
            ->where('id', '<', $cutoffId)->delete();
    }

    /** 清理所有账号的历史记录，返回删除总数。 */
    public function pruneAll(int $limit = self::RETENTION_LIMIT): int
    {
        $deleted = 0;
        foreach (Db::name($this->table)->distinct(true)->column('account_id') as $accountId) {
            $deleted += $this->pruneAccount((int)$accountId, $limit);
        }
        return $deleted;
    }
}
