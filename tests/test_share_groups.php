<?php
declare(strict_types=1);
if (PHP_SAPI !== 'cli') exit(1);
require dirname(__DIR__) . '/vendor/autoload.php';
$app = new think\App(dirname(__DIR__)); $app->initialize();
use app\controller\FrontendApiController;
use think\facade\Db;
use think\facade\Session;

$checks=0;
function checkGroup($ok,string $message):void { global $checks; if(!$ok) throw new RuntimeException($message); $checks++; }
function groupController(array $params=[]):FrontendApiController { global $app; $app->instance('request',(new think\Request())->withServer(['REQUEST_METHOD'=>'POST'])->setMethod('POST')->withPost($params)); return new FrontendApiController($app); }

$failed=false; Db::startTrans();
try {
    $account=Db::name('account')->order('id')->find(); checkGroup((bool)$account,'fixture account exists');
    $owner=(int)$account['owner']; Session::set('user_id',$owner);
    $templateId=Db::name('share_template')->insertGetId(['owner'=>$owner,'name'=>'group test template','html'=>'<p>before</p>','notice_html'=>'<p>notice before</p>','is_default'=>0,'create_time'=>date('Y-m-d H:i:s'),'update_time'=>date('Y-m-d H:i:s')]);
    $create=groupController(['name'=>'测试分组','template_id'=>$templateId,'account_list'=>[(int)$account['id']],'password'=>'secret','duration_minutes'=>20,'access_tracking'=>true])->saveShareGroup();
    checkGroup($create->getCode()===200,'group created: '.$create->getContent());
    $group=Db::name('share_group')->where('name','测试分组')->find(); checkGroup((bool)$group,'group persisted');
    $shareId=Db::name('share')->insertGetId(['share_link'=>'test'.bin2hex(random_bytes(5)),'account_list'=>(string)$account['id'],'owner'=>$owner,'password'=>null,'html'=>'old','notice_html'=>'old','remark'=>'test','duration_minutes'=>0,'access_tracking'=>1,'group_id'=>$group['id'],'template_id'=>$templateId]);
    $update=groupController(['name'=>'更新后的组','template_id'=>$templateId,'account_list'=>[(int)$account['id']],'password'=>'changed','duration_minutes'=>45,'access_tracking'=>false])->saveShareGroup($group['id']);
    checkGroup($update->getCode()===200,'group updated');
    $share=Db::name('share')->where('id',$shareId)->find();
    checkGroup(password_verify('changed',(string)$share['password'])&&(int)$share['duration_minutes']===45&&(int)$share['access_tracking']===0,'group properties propagated');
    Db::name('user')->where('id',$owner)->update(['is_admin'=>1]);
    $batch=groupController(['scope'=>'admin','group_id'=>$group['id'],'quantity'=>2,'remark'=>'组内批量'])->shareBatchCreate();
    checkGroup($batch->getCode()===200,'batch create into group');
    $generated=Db::name('share')->where('group_id',$group['id'])->where('remark','like','组内批量%')->select()->toArray();
    checkGroup(count($generated)===2,'batch links joined group');
    checkGroup(array_reduce($generated,fn($ok,$row)=>$ok&&password_verify('changed',(string)$row['password'])&&(int)$row['template_id']===$templateId&&(int)$row['duration_minutes']===45,true),'batch links inherited group settings');
    $template=groupController(['name'=>'group test template','html'=>'<p>after</p>','notice_html'=>'<p>notice after</p>'])->saveTemplate($templateId);
    checkGroup($template->getCode()===200,'template updated');
    $share=Db::name('share')->where('id',$shareId)->find(); checkGroup($share['html']==='<p>after</p>'&&$share['notice_html']==='<p>notice after</p>','template propagated');
    $list=json_decode(groupController()->shareGroups()->getContent(),true); checkGroup(($list['success']??false)===true,'groups listed');
} catch(Throwable $e) { $failed=true; fwrite(STDERR,$e->getMessage()."\n"); }
Db::rollback(); Session::delete('user_id');
if($failed) exit(1); echo "{$checks} share group checks passed\n";
