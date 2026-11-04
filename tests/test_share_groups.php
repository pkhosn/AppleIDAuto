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
    checkGroup($create->getCode()===200,'group created');
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
    foreach ($generated as $row) {
        checkGroup(str_starts_with((string)$row['password_cipher'],'enc:v1:'),'batch inherited encrypted password');
        $copied=json_decode(groupController(['scope'=>'admin'])->copyShare($row['id'])->getContent(),true);
        checkGroup(str_ends_with($copied['data']['text']," 访问密码：changed"),'batch share copies inherited password');
    }
    $template=groupController(['name'=>'group test template','html'=>'<p>after</p>','notice_html'=>'<p>notice after</p>'])->saveTemplate($templateId);
    checkGroup($template->getCode()===200,'template updated');
    $share=Db::name('share')->where('id',$shareId)->find(); checkGroup($share['html']==='<p>after</p>'&&$share['notice_html']==='<p>notice after</p>','template propagated');
    $list=json_decode(groupController()->shareGroups()->getContent(),true); checkGroup(($list['success']??false)===true,'groups listed');
    $params=['scope'=>'admin','account_list'=>[(int)$account['id']],'password_enabled'=>true];
    checkGroup(groupController($params)->saveShare()->getCode()===422,'enabled password requires content');
    $link='test'.bin2hex(random_bytes(5));
    checkGroup(groupController($params+['share_link'=>$link,'password'=>'secret'])->saveShare()->getCode()===200,'protected share created');
    $standalone=Db::name('share')->where('share_link',$link)->find(); $sid=(int)$standalone['id'];
    checkGroup(groupController($params)->saveShare($sid)->getCode()===200,'blank enabled password preserves existing');
    checkGroup(password_verify('secret',(string)Db::name('share')->where('id',$sid)->value('password')),'existing password preserved');
    $detail=json_decode(groupController(['scope'=>'admin'])->share($sid)->getContent(),true)['data'];
    checkGroup($detail['password_configured']===true&&!array_key_exists('password',$detail)&&!array_key_exists('password_cipher',$detail),'detail hides both stored credentials');
    $cipher=(string)Db::name('share')->where('id',$sid)->value('password_cipher');
    checkGroup(str_starts_with($cipher,'enc:v1:')&&!str_contains($cipher,'secret'),'password encrypted at rest');
    Db::name('share')->where('id',$sid)->update(['base_url'=>'https://share.example.test/path/']);
    $copy=groupController()->copyShare($sid);
    $text=json_decode($copy->getContent(),true)['data']['text'];
    checkGroup($text==="分享链接：https://share.example.test/path/share/{$link} 访问密码：secret",'copy includes actual custom URL and preserved password');
    checkGroup(($copy->getHeader()['Cache-Control']??'')==='no-store','copy response not cached');
    Db::name('share')->where('id',$sid)->update(['owner'=>$owner+99999]);
    checkGroup(groupController()->copyShare($sid)->getCode()===403,'copy rejects another owner');
    checkGroup(groupController(['scope'=>'admin'])->copyShare($sid)->getCode()===200,'admin scoped copy allowed');
    Db::name('share')->where('id',$sid)->update(['owner'=>$owner]);
    checkGroup(groupController()->copyShare(2147483647)->getCode()===404,'missing share copy rejected');
    $params['password_enabled']=false;
    checkGroup(groupController($params)->saveShare($sid)->getCode()===200,'password switch disabled');
    checkGroup(!Db::name('share')->where('id',$sid)->value('password'),'disabled clears existing password');
    checkGroup(!Db::name('share')->where('id',$sid)->value('password_cipher'),'disabled clears encrypted password');
    $text=json_decode(groupController()->copyShare($sid)->getContent(),true)['data']['text'];
    checkGroup($text==="https://share.example.test/path/share/{$link}",'no password copies URL only');
    $detail=json_decode(groupController(['scope'=>'admin'])->share($sid)->getContent(),true)['data'];
    checkGroup($detail['password_configured']===false,'null password is not configured');
    checkGroup(groupController(['ids'=>[]])->shareBatchCopy()->getCode()===422,'batch copy requires selection');
    checkGroup(groupController(['ids'=>[$sid,2147483647]])->shareBatchCopy()->getCode()===403,'batch copy rejects missing entries');
    $batchCopy=groupController(['ids'=>[$sid,$generated[1]['id'],$sid]] )->shareBatchCopy();
    $data=json_decode($batchCopy->getContent(),true)['data'];
    checkGroup($data['count']===2&&str_starts_with($data['text'],"https://share.example.test/path/share/{$link}\n分享链接：")&&str_ends_with($data['text']," 访问密码：changed"),'batch copy deduplicates, preserves order and includes passwords');
    checkGroup(substr_count($data['text'], "\n")===1&&!str_contains($data['text'], "\r"),'batch copy uses one line per share');
    checkGroup(($batchCopy->getHeader()['Cache-Control']??'')==='no-store','batch copy response not cached');
    Db::name('share')->where('id',$sid)->update(['owner'=>$owner+99999]);
    checkGroup(groupController(['ids'=>[$sid,$generated[1]['id']]])->shareBatchCopy()->getCode()===403,'batch copy rejects entire unauthorized selection');
    checkGroup(groupController(['scope'=>'admin','ids'=>[$sid,$generated[1]['id']]])->shareBatchCopy()->getCode()===200,'admin scoped batch copy');
    Db::name('share')->where('id',$sid)->update(['owner'=>$owner]);
    $groupParams=['name'=>'更新后的组','account_list'=>[(int)$account['id']],'password_enabled'=>false];
    checkGroup(groupController($groupParams)->saveShareGroup($group['id'])->getCode()===200,'disable group password');
    checkGroup(Db::name('share')->where('group_id',$group['id'])->whereNotNull('password')->count()===0,'group disabling clears all member passwords');
    checkGroup(Db::name('share')->where('group_id',$group['id'])->whereNotNull('password_cipher')->count()===0,'group disabling clears all member encrypted passwords');
    $text=json_decode(groupController()->copyShare($shareId)->getContent(),true)['data']['text'];
    checkGroup(!str_contains($text,'访问密码'),'group member copies URL after disabling password');
    $groupParams['password_enabled']=true;
    checkGroup(groupController($groupParams)->saveShareGroup($group['id'])->getCode()===422,'group enabling requires password');
    $groupList=json_decode(groupController()->shareGroups()->getContent(),true)['data'];
    checkGroup(!array_key_exists('password_cipher',$groupList[0]),'group listing hides cipher');
    foreach (['0','all',(string)$group['id']] as $filter) {
        $response=json_decode(groupController(['group_id'=>$filter,'keyword'=>$filter==='0'?$link:'','per_page'=>100])->shares()->getContent(),true);
        checkGroup(($response['success']??false)===true,'filtered list succeeds');
        $rows=$response['data'];
        checkGroup(!array_key_exists('password_cipher',$rows[0]),'share listing hides cipher');
        if ($filter==='0') checkGroup(count($rows)===1&&(int)$rows[0]['id']===$sid,'ungrouped combines with keyword');
        elseif ($filter!=='all') checkGroup(count($rows)===3&&array_reduce($rows,fn($ok,$r)=>$ok&&(int)$r['group_id']===(int)$group['id'],true),'specific group filter');
        else checkGroup(in_array($sid,array_column($rows,'id'))&&in_array($shareId,array_column($rows,'id')),'all includes grouped and ungrouped');
    }
} catch(Throwable $e) { $failed=true; fwrite(STDERR,$e->getMessage()."\n"); }
Db::rollback(); Session::delete('user_id');
if($failed) exit(1); echo "{$checks} share group checks passed\n";
