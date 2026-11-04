<?php
declare(strict_types=1);
if (PHP_SAPI !== 'cli') exit(1);
require dirname(__DIR__) . '/vendor/autoload.php';
$app = new think\App(dirname(__DIR__)); $app->initialize();
use think\facade\Db;
use think\facade\Session;
use app\controller\FrontendApiController;
$checks=0; $failed=false;
function assertMembership($ok,string $message):void {global $checks;if(!$ok)throw new RuntimeException($message);$checks++;}
function memberController(array $params=[]):FrontendApiController {global $app;$app->instance('request',(new think\Request())->withServer(['REQUEST_METHOD'=>'POST'])->setMethod('POST')->withPost($params));return new FrontendApiController($app);}
Db::startTrans();
try {
    $user=Db::name('user')->order('id')->find();$uid=(int)$user['id'];Session::set('user_id',$uid);
    $data=['name'=>'membership fixture','price'=>'1.00','days'=>30,'max_accounts'=>12,'max_share_pages'=>60,'min_manual_interval'=>2,'enable_share'=>1,'status'=>1];
    $pid=Db::name('plan')->insertGetId($data);$plan=$app->planService->fetch($pid);
    $expiry=date('Y-m-d H:i:s',time()+3*86400);
    Db::name('user')->where('id',$uid)->update(['vip_expire_at'=>$expiry]);
    assertMembership($app->orderService->grantPlan($uid,$plan)['status'],'grant succeeds');
    $row=Db::name('user')->where('id',$uid)->find();$granted=$row['vip_expire_at'];
    assertMembership((int)$row['current_plan_id']===$pid&&(int)$row['quota_custom']===0,'grant associates plan');
    assertMembership(strtotime($granted)===strtotime($expiry)+30*86400,'renewal extends unexpired time');
    $data['max_accounts']=15;$data['max_share_pages']=80;
    assertMembership($app->planService->savePlan($data,$pid)['status'],'plan changes succeed');
    $row=Db::name('user')->where('id',$uid)->find();
    assertMembership((int)$row['max_accounts']===15&&(int)$row['max_share_pages']===80,'followers sync limits');
    assertMembership($row['vip_expire_at']===$granted,'sync preserves expiry');
    Db::name('user')->where('id',$uid)->update(['quota_custom'=>1,'max_accounts'=>7]);
    $data['max_accounts']=16;$app->planService->savePlan($data,$pid);
    assertMembership((int)Db::name('user')->where('id',$uid)->value('max_accounts')===7,'custom quota preserved');
    $saveParams=['username'=>$user['username'],'is_admin'=>true,'status'=>true,'quota_custom'=>false,'balance'=>$user['balance'],'vip_expire_at'=>$granted];
    assertMembership(memberController($saveParams)->saveAdminUser($uid)->getCode()===200,'admin switches back to plan');
    $row=Db::name('user')->where('id',$uid)->find();assertMembership((int)$row['max_accounts']===16&&(int)$row['quota_custom']===0,'switch refreshes current plan values');
    $saveParams['quota_custom']=true;$saveParams['max_accounts']=7;$saveParams['max_share_pages']=80;$saveParams['min_manual_interval']=2;
    assertMembership(memberController($saveParams)->saveAdminUser($uid)->getCode()===200,'admin custom quota saved');
    $m=$app->entitlementService->membership($uid);
    assertMembership($m['quota_custom']&&$m['plan_id']===$pid&&$m['status']==='active','membership reports identity and custom state');
    foreach (['accounts','shares'] as $kind)assertMembership($m[$kind]['remaining']===max(0,$m[$kind]['limit']-$m[$kind]['used']),'remaining quota calculated');
    Db::name('user')->where('id',$uid)->update(['vip_expire_at'=>$expiry]);
    assertMembership($app->entitlementService->membership($uid)['status']==='expiring','seven day reminder');
    Db::name('user')->where('id',$uid)->update(['vip_expire_at'=>date('Y-m-d H:i:s',time()-86400)]);
    $m=$app->entitlementService->membership($uid);assertMembership($m['status']==='expired'&&$m['remaining_days']===0,'expired state');
    Db::name('user')->where('id',$uid)->update(['vip_expire_at'=>null]);
    assertMembership($app->entitlementService->membership($uid)['status']==='none','no membership state');
    Db::name('user')->where('id',$uid)->update(['quota_custom'=>0]);
    $data['max_accounts']=0;$data['max_share_pages']=0;$before=Db::name('account')->where('owner',$uid)->count();$app->planService->savePlan($data,$pid);
    assertMembership(Db::name('account')->where('owner',$uid)->count()===$before,'downsize preserves accounts');
    assertMembership(!$app->entitlementService->canAddAccount($uid)['status'],'downsize prevents adding');
    assertMembership($app->planService->deletePlan($pid)['status']&&$app->planService->fetch($pid)!==null,'associated plan retained when removed');
    Db::name('plan')->where('id',$pid)->update(['status'=>1]);
    assertMembership($app->orderService->grantPlan($uid,$app->planService->fetch($pid))['status'],'re-purchase succeeds');
    assertMembership((int)Db::name('user')->where('id',$uid)->value('quota_custom')===0,'re-purchase restores plan following');
    // Backfill only proven latest grants; mismatches retain custom quotas.
    Db::name('user')->where('id',$uid)->update(['current_plan_id'=>null,'max_accounts'=>0,'max_share_pages'=>0,'min_manual_interval'=>2]);
    Db::name('order')->insert(['order_no'=>'fixture'.bin2hex(random_bytes(6)),'user_id'=>$uid,'plan_id'=>$pid,'plan_name'=>'fixture','amount'=>'1.00','kind'=>'plan','status'=>'paid','paid_at'=>'2099-01-01 00:00:00']);
    app\service\PlanMembershipMigration::apply(Db::connect()->getPdo());
    $row=Db::name('user')->where('id',$uid)->find();assertMembership((int)$row['current_plan_id']===$pid&&(int)$row['quota_custom']===0,'legacy matching quotas follow latest purchase');
    Db::name('user')->where('id',$uid)->update(['current_plan_id'=>null,'max_accounts'=>99]);
    app\service\PlanMembershipMigration::apply(Db::connect()->getPdo());
    assertMembership((int)Db::name('user')->where('id',$uid)->value('quota_custom')===1,'legacy differing quotas preserved');
    Db::name('user')->where('id',$uid)->update(['current_plan_id'=>null]);
    Db::name('redeem_code')->insert(['code'=>bin2hex(random_bytes(8)),'plan_id'=>$pid+999,'status'=>'used','created_by'=>$uid,'used_by'=>$uid,'used_at'=>'2099-01-01 00:00:00']);
    app\service\PlanMembershipMigration::apply(Db::connect()->getPdo());
    assertMembership(Db::name('user')->where('id',$uid)->value('current_plan_id')===null,'ambiguous latest grants remain unlinked');
    $dashboard=json_decode(memberController()->dashboard()->getContent(),true)['data'];assertMembership(isset($dashboard['membership']['accounts']),'user dashboard includes membership');
} catch(Throwable $e){$failed=true;fwrite(STDERR,$e->getMessage()."\n");}
Db::rollback();Session::delete('user_id');if($failed)exit(1);echo "{$checks} membership checks passed (rolled back).\n";
