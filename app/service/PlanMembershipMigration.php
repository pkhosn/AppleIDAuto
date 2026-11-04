<?php
declare(strict_types=1);
namespace app\service;
use PDO;

/** Keep uncertain legacy quotas intact; never infer ownership from quota values alone. */
final class PlanMembershipMigration
{
    public static function apply(PDO $pdo): void
    {
        $fields = array_column($pdo->query('SHOW COLUMNS FROM user')->fetchAll(PDO::FETCH_ASSOC), 'Field');
        if (!in_array('current_plan_id', $fields, true)) $pdo->exec('ALTER TABLE user ADD COLUMN current_plan_id INT UNSIGNED NULL');
        if (!in_array('quota_custom', $fields, true)) $pdo->exec('ALTER TABLE user ADD COLUMN quota_custom TINYINT(1) NOT NULL DEFAULT 1');
        foreach ($pdo->query('SELECT * FROM user WHERE current_plan_id IS NULL')->fetchAll(PDO::FETCH_ASSOC) as $user) {
            $events = $pdo->prepare("SELECT plan_id,paid_at event_time FROM `order` WHERE user_id=? AND status='paid' AND kind='plan' AND paid_at IS NOT NULL UNION ALL SELECT plan_id,used_at event_time FROM redeem_code WHERE used_by=? AND status='used' AND used_at IS NOT NULL ORDER BY event_time DESC");
            $events->execute([$user['id'], $user['id']]); $rows = $events->fetchAll(PDO::FETCH_ASSOC);
            if (!$rows) continue;
            $latest = array_filter($rows, fn($row) => $row['event_time'] === $rows[0]['event_time']);
            if (count(array_unique(array_column($latest, 'plan_id'))) !== 1) continue;
            $q = $pdo->prepare('SELECT * FROM plan WHERE id=?'); $q->execute([$rows[0]['plan_id']]); $plan = $q->fetch(PDO::FETCH_ASSOC);
            if (!$plan) continue;
            $custom = (int)$user['max_accounts'] !== (int)$plan['max_accounts'] || (int)$user['max_share_pages'] !== (empty($plan['enable_share']) ? 0 : (int)$plan['max_share_pages']) || (int)$user['min_manual_interval'] !== (int)$plan['min_manual_interval'];
            $q = $pdo->prepare('UPDATE user SET current_plan_id=?,quota_custom=? WHERE id=? AND current_plan_id IS NULL');
            $q->execute([$plan['id'], (int)$custom, $user['id']]);
        }
    }
}
