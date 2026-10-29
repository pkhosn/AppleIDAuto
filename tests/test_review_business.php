<?php
declare(strict_types=1);
// Uses ONLY an explicitly selected disposable test database and a temporary app root.
if (PHP_SAPI !== 'cli') exit(1);
$database = (string)getenv('TEST_DB_NAME');
if ($database === '') { echo "SKIP: set TEST_DB_* to a disposable MySQL database.\n"; exit(0); }
if (!preg_match('/^apple_review_test(?:_[a-z0-9]+)?$/', $database)) throw new RuntimeException('Unsafe test database name');
$root = dirname(__DIR__);
require $root . '/vendor/autoload.php';
$directory = sys_get_temp_dir() . '/apple-review-' . bin2hex(random_bytes(8));
mkdir($directory, 0700);
mkdir($directory . '/config', 0700);
mkdir($directory . '/runtime', 0700);
foreach (glob($root . '/config/*.php') as $file) copy($file, $directory . '/config/' . basename($file));
register_shutdown_function(static function () use ($directory): void {
    $items = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($directory, FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::CHILD_FIRST);
    foreach ($items as $item) $item->isDir() ? rmdir($item->getPathname()) : unlink($item->getPathname());
    rmdir($directory);
});
$key = base64_encode(str_repeat('K', 32)); // Deterministic test key, never a deployed credential.
$env = ['DATABASE' => ['TYPE'=>'mysql', 'HOSTNAME'=>getenv('TEST_DB_HOST') ?: '127.0.0.1',
    'HOSTPORT'=>getenv('TEST_DB_PORT') ?: '3306', 'DATABASE'=>$database,
    'USERNAME'=>getenv('TEST_DB_USER') ?: 'root', 'PASSWORD'=>getenv('TEST_DB_PASSWORD') ?: ''],
    'CREDENTIALS'=>['CIPHER_KEY'=>$key], 'MAILBOX'=>['CIPHER_KEY'=>$key], 'BACKEND'=>['ENABLE_API'=>'false','API_URL'=>'http://127.0.0.1:1','TOKEN'=>str_repeat('T',32)]];
$app = new think\App($directory . '/');
$app->setAppPath($root . '/app/');
$app->env->set($env);
$app->initialize();
set_exception_handler(static function (Throwable $e): void {
    fwrite(STDERR, get_class($e).': '.$e->getMessage()."\n".$e->getTraceAsString()."\n");
    exit(1);
});
use think\facade\Db;
$pdo = new PDO('mysql:host='.$env['DATABASE']['HOSTNAME'].';port='.$env['DATABASE']['HOSTPORT'].';dbname='.$database.';charset=utf8mb4',
    $env['DATABASE']['USERNAME'], $env['DATABASE']['PASSWORD'], [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
function check(bool $condition, string $label): void { if (!$condition) throw new RuntimeException($label); }

// Child processes coordinate before entering the real application transaction.
if (($argv[1] ?? '') === 'worker') {
    $input = json_decode($argv[3], true, 512, JSON_THROW_ON_ERROR);
    while (!is_file($input['barrier'])) usleep(1000);
    switch ($argv[2]) {
        case 'paid': $result = $app->orderService->paid($input['order'], 'fixture-'.$input['order'], '10.00', 'alipay'); break;
        case 'redeem': $result = $app->redeemCodeService->redeem($input['code'], 1); break;
        case 'account': $result = (new app\model\Account())->addAccount(['username'=>$input['name'], 'password'=>'FixturePass1@', 'owner'=>1, 'enable'=>0, 'enable_check_password_correct'=>0, 'enable_auto_update_password'=>0]); break;
        case 'share':
            try { $result = (new app\model\SharePage())->addSharePage(['share_link'=>$input['name'], 'account_list'=>'1', 'owner'=>1]); }
            catch (InvalidArgumentException $e) { $result=['status'=>false]; }
            break;
        case 'quota-slow':
            $result=$app->entitlementService->createWithinQuota((int)$input['owner'],'share',1,true,function () use ($input) {
                usleep(200000);
                Db::name('share')->insert(['share_link'=>$input['name'],'account_list'=>'1','owner'=>$input['owner']]);
                return ['status'=>true];
            });
            break;
        case 'batch':
            try { $result=['status'=>true, 'links'=>$app->shareService->createBatch(1, [1], 2, ['expire'=>null,'duration_minutes'=>0])]; }
            catch (InvalidArgumentException $e) { $result=['status'=>false]; }
            break;
        default: throw new RuntimeException('Unknown worker');
    }
    echo json_encode($result, JSON_THROW_ON_ERROR);
    exit;
}

function concurrent(string $operation, array $inputs, string $directory): array {
    $barrier = $directory . '/barrier-' . bin2hex(random_bytes(4));
    $processes=[];
    foreach ($inputs as $input) {
        $pipes=[];
        $process=proc_open([PHP_BINARY, __FILE__, 'worker', $operation, json_encode($input+['barrier'=>$barrier])],
            [0=>['pipe','r'],1=>['pipe','w'],2=>['pipe','w']], $pipes);
        fclose($pipes[0]); $processes[]=[$process,$pipes];
    }
    touch($barrier); $results=[];
    foreach ($processes as [$process,$pipes]) {
        $body=stream_get_contents($pipes[1]); $error=stream_get_contents($pipes[2]);
        fclose($pipes[1]); fclose($pipes[2]);
        check(proc_close($process)===0, 'Worker failed: '.$error);
        try { $results[]=json_decode($body,true,512,JSON_THROW_ON_ERROR); }
        catch (JsonException $e) { throw new RuntimeException('Invalid worker response: '.$body.' '.$error, 0, $e); }
    }
    unlink($barrier); return $results;
}

// Empty database initialization and a repeated upgrade from the old share schema.
foreach ($pdo->query('SHOW TABLES')->fetchAll(PDO::FETCH_COLUMN) as $table) $pdo->exec('DROP TABLE `'.$table.'`');
$sql = file_get_contents($root.'/db/db.sql');
foreach (preg_split('/;\s*(?:\r?\n|$)/', $sql) as $statement) {
    $statement=trim($statement);
    if ($statement!=='' && !preg_match('/^\/\*.*\*\/$/s',$statement)) $pdo->exec($statement);
}
$pdo->exec('DROP TABLE share_group');
$pdo->exec('ALTER TABLE share DROP COLUMN group_id, DROP COLUMN template_id, DROP COLUMN embed_enabled, DROP COLUMN embed_origins');
app\service\ShareSchemaMigration::apply($pdo);
app\service\ShareSchemaMigration::apply($pdo);
$embedColumns = array_column($pdo->query('SHOW COLUMNS FROM share')->fetchAll(PDO::FETCH_ASSOC), 'Field');
check(in_array('embed_enabled', $embedColumns, true) && in_array('embed_origins', $embedColumns, true), 'Embed schema missing after repeated migration');
$installer = new app\service\InstallService();
(new ReflectionMethod($installer,'validateInitializedDatabase'))->invoke($installer,$pdo);
$app->env->set('credentials.cipher_key',''); $app->env->set('mailbox.cipher_key','');
$keys=(new ReflectionMethod($installer,'installationKeys'))->invoke($installer,$pdo);
check(strlen(base64_decode($keys['credentials']))===32 && $keys['credentials']!==$keys['mailbox'], 'Independent install keys');
$app->env->set('credentials.cipher_key',$key); $app->env->set('mailbox.cipher_key',$key);
$kept=(new ReflectionMethod($installer,'installationKeys'))->invoke($installer,$pdo);
check($kept['credentials']===$key, 'Existing key changed');
// Missing keys may not replace a key protecting existing encrypted fields.
$pdo->exec("INSERT INTO user (id,username,password,tg_bot_token) VALUES(99,'cipher-fixture','fixture','enc:v1:fixture')");
$app->env->set('credentials.cipher_key','');
try { (new ReflectionMethod($installer,'installationKeys'))->invoke($installer,$pdo); throw new LogicException('Missing-key protection failed'); }
catch (RuntimeException $e) { check(str_contains($e->getMessage(),'已有密文'), 'Unexpected key failure'); }
$app->env->set('credentials.cipher_key',$key);
$pdo->exec('DELETE FROM user');
// Existing sections receive new keys in the correct section with restrictive permissions.
file_put_contents($directory.'/.env', "API_KEY=fixture\n[CREDENTIALS]\n[MAILBOX]\n");
(new ReflectionMethod($installer,'updateEnv'))->invoke($installer,$directory.'/.env', [''=>['NEW_KEY'=>'global'], 'CREDENTIALS'=>['CIPHER_KEY'=>$key]]);
$written=parse_ini_file($directory.'/.env',true,INI_SCANNER_RAW);
check($written['NEW_KEY']==='global' && $written['CREDENTIALS']['CIPHER_KEY']===$key, 'Config key written to wrong section');
check((fileperms($directory.'/.env')&0777)===0600, 'Configuration permissions');
unlink($directory.'/.env');

$initial = date('Y-m-d H:i:s', time()+86400);
foreach ([1,2] as $id) Db::name('user')->insert(['id'=>$id,'username'=>'fixture-'.$id,'password'=>'fixture','vip_expire_at'=>$initial,'max_accounts'=>1,'max_share_pages'=>1,'status'=>1]);
Db::name('plan')->insert(['id'=>1,'name'=>'fixture','price'=>'10.00','days'=>30,'max_accounts'=>1,'min_manual_interval'=>0,'enable_share'=>1,'max_share_pages'=>1]);
foreach (['fixture-a','fixture-b'] as $order) Db::name('order')->insert(['order_no'=>$order,'user_id'=>1,'plan_id'=>1,'plan_name'=>'fixture','amount'=>'10.00','pay_type'=>'alipay','status'=>'pending']);
$results=concurrent('paid', [['order'=>'fixture-a'],['order'=>'fixture-b']],$directory);
check(count(array_filter($results,fn($r)=>$r['status']))===2, 'Concurrent payment failed');
$expiry=(string)Db::name('user')->where('id',1)->value('vip_expire_at');
check(strtotime($expiry)-strtotime($initial)===60*86400, 'Concurrent renewal lost purchased days');
$app->orderService->paid('fixture-a','fixture-fixture-a','10.00','alipay');
check(Db::name('user')->where('id',1)->value('vip_expire_at')===$expiry, 'Duplicate callback extended subscription');
foreach (['fixture-code-a','fixture-code-b'] as $code) Db::name('redeem_code')->insert(['code'=>strtoupper($code),'plan_id'=>1,'created_by'=>1]);
concurrent('redeem',[['code'=>'fixture-code-a'],['code'=>'fixture-code-b']],$directory);
check(strtotime((string)Db::name('user')->where('id',1)->value('vip_expire_at'))-strtotime($expiry)===60*86400, 'Concurrent redemption lost purchased days');
$before=Db::name('user')->where('id',1)->value('vip_expire_at');
check(!$app->redeemCodeService->redeem('fixture-code-a',1)['status'], 'Code redeemed twice');
check(Db::name('user')->where('id',1)->value('vip_expire_at')===$before,'Duplicate redemption changed expiry');

$results=concurrent('account',[['name'=>'fixture-a@example.test'],['name'=>'fixture-b@example.test']],$directory);
check(Db::name('account')->where('owner',1)->count()===1 && count(array_filter($results,fn($r)=>$r['status']))===1,'Account quota exceeded');
$results=concurrent('share',[['name'=>'fixture-share-a'],['name'=>'fixture-share-b']],$directory);
check(Db::name('share')->where('owner',1)->count()===1,'Share quota exceeded');
Db::name('share')->where('owner',1)->delete(); Db::name('user')->where('id',1)->update(['max_share_pages'=>3]);
concurrent('batch',[[],[]],$directory);
check(Db::name('share')->where('owner',1)->count()===2,'Batch quota exceeded or partial batch persisted');
Db::name('share')->where('owner',1)->delete(); Db::name('account')->where('owner',1)->delete();
$separate=concurrent('quota-slow', [['owner'=>1,'name'=>'separate-owner-a'],['owner'=>2,'name'=>'separate-owner-b']],$directory);
check(count(array_filter($separate,fn($r)=>$r['status']))===2,'Different tenants blocked each other on empty quota ranges');
Db::name('share')->delete(true);
foreach ([[1,1],[11,2]] as [$id,$owner]) Db::name('account')->insert(['id'=>$id,'owner'=>$owner,'username'=>'account-'.$id,'password'=>'fixture','enable'=>0,'dob'=>'2000-01-01','question1'=>'','answer1'=>'','question2'=>'','answer2'=>'','question3'=>'','answer3'=>'','frontend_remark'=>'','message'=>'','enable_check_password_correct'=>0,'enable_delete_devices'=>0,'enable_auto_update_password'=>0]);
Db::name('share')->insert(['id'=>1,'share_link'=>'owned-share','account_list'=>'1','owner'=>1]);
Db::name('share')->insert(['id'=>2,'share_link'=>'other-share','account_list'=>'11','owner'=>2]);
Db::name('share_group')->insert(['owner'=>1,'name'=>'group-fixture','account_list'=>'1']);
check((new app\model\Account())->deleteAccount(1)['status'],'Account deletion failed');
check(!Db::name('share')->where('id',1)->find() && Db::name('share')->where('id',2)->find()!==null,'Cross-tenant share deletion');
check(Db::name('share_group')->where('owner',1)->value('account_list')==='','Group retained deleted account');

// Backend startup must observe a committed account through another connection.
$startup = new class($app, $pdo) extends app\service\BackendService {
    public bool $visible = false;
    private PDO $testPdo;
    public function __construct($app, PDO $pdo) { parent::__construct($app); $this->testPdo=$pdo; }
    public function addTask($id): array {
        $query=$this->testPdo->prepare('SELECT COUNT(*) FROM account WHERE id=?'); $query->execute([$id]);
        $this->visible=(int)$query->fetchColumn()===1;
        return ['status'=>true,'msg'=>'fixture'];
    }
};
$app->instance(app\service\BackendService::class, $startup);
$result=(new app\model\Account())->addAccount(['username'=>'committed@example.test','password'=>'FixturePass1@','owner'=>1,
    'enable'=>1,'enable_check_password_correct'=>0,'enable_auto_update_password'=>0]);
check($result['status'] && $startup->visible,'Backend started before account transaction committed');

// Repay a pending recharge with plan_id=0, without a gateway request.
Db::name('payment_config')->where('id',1)->update(['status'=>1,'gateway'=>'https://payment.example.invalid','pid'=>'fixture','merchant_key'=>'fixture']);
$order=$app->orderService->createRechargeOrder(1,25,'alipay');
$app->session->set('user_id',1);
$response=(new app\controller\PayController($app))->repayOrder($order['order_no']);
check($response instanceof think\response\View,'Recharge repayment rejected');
$vars=(new ReflectionProperty($response,'vars'))->getValue($response);
check($vars['params']['money']==='25.00' && $vars['params']['name']==='余额充值','Recharge repayment parameters');
$app->orderService->paid($order['order_no'],'fixture-recharge','25.00','alipay');
$app->orderService->paid($order['order_no'],'fixture-recharge','25.00','alipay');
check((float)Db::name('user')->where('id',1)->value('balance')===25.0,'Recharge callback credited twice');
check($app->orderService->balancePurchase(1,1)['status'],'Balance purchase failed');
check((float)Db::name('user')->where('id',1)->value('balance')===15.0,'Balance purchase amount');
echo "Business integration checks passed (isolated MySQL).\n";
