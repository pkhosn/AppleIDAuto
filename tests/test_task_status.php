<?php
declare(strict_types=1);
if (PHP_SAPI !== 'cli') exit(1);
require dirname(__DIR__) . '/vendor/autoload.php';
function check(bool $condition, string $label): void { if (!$condition) throw new RuntimeException($label); }
// Runtime task presentation must not replace stored historical results or enable a frozen account.
$old = ['id'=>1, 'enable'=>0, 'message'=>'旧失败', 'task_status'=>'disabled', 'last_check'=>'2026-01-01'];
foreach (['pending','running','success','failed'] as $status) {
    $shown = app\service\AutomationTaskService::withRuntimeState($old, ['account_id'=>1, 'status'=>$status, 'message'=>'本次'.$status]);
    check($shown['task_status']===$status && $shown['message']==='本次'.$status, 'Stale task result displayed');
    check($shown['enable']===0 && $shown['last_check']===$old['last_check'], 'Presentation changed account state');
}
check(app\service\AutomationTaskService::withRuntimeState($old, null)===$old, 'Unavailable backend lost stored result');
check(app\service\AutomationTaskService::withRuntimeState($old, ['account_id'=>2,'status'=>'running'])===$old, 'Wrong account state applied');

echo "Task status presentation checks passed\n";
