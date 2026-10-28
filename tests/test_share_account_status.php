<?php
declare(strict_types=1);
if (PHP_SAPI !== 'cli') exit(1);
require dirname(__DIR__) . '/vendor/autoload.php';
$app = new think\App(dirname(__DIR__)); $app->initialize();

$service = $app->shareService;
$now = strtotime('2026-09-06 11:00:00');
$globals = ['active' => true, 'features' => ['two_factor'=>30,'password_check'=>30,'profile_restore'=>360,'password_update'=>1440]];
$schedule = json_encode(['version'=>2,'mode'=>'custom','features'=>[
    'two_factor'=>['source'=>'custom','minutes'=>60], 'password_check'=>['source'=>'custom','minutes'=>60],
    'profile_restore'=>['source'=>'custom','minutes'=>60], 'password_update'=>['source'=>'custom','minutes'=>120],
]], JSON_THROW_ON_ERROR);
$base = ['enable'=>1,'task_status'=>'success','last_check'=>'2026-09-06 10:05:00','check_interval'=>30,
    'enable_check_password_correct'=>1,'automation_schedule'=>$schedule];
$checks = 0;
$check = function(bool $result,string $name) use (&$checks):void { if(!$result) throw new RuntimeException($name); $checks++; };
$check($service->isAccountHealthy($base,$now,$globals), 'effective 60-minute period is used');
$check(!$service->isAccountHealthy(array_replace($base,['last_check'=>'2026-09-06 09:50:00']),$now,$globals), 'expired effective period is rejected');
$check(!$service->isAccountHealthy(array_replace($base,['task_status'=>'failed']),$now,$globals), 'failed task is rejected');
$check($service->isAccountHealthy(array_replace($base,['enable_check_password_correct'=>0,'last_check'=>'2026-09-01 00:00:00']),$now,$globals), 'disabled password check does not become stale');
$legacyGlobals=$globals; $legacyGlobals['active']=false;
$check(!$service->isAccountHealthy($base,$now,$legacyGlobals), 'legacy mode still uses check_interval');
echo "{$checks} share status checks passed\n";
