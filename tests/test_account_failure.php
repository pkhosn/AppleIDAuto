<?php
declare(strict_types=1);
if (PHP_SAPI !== 'cli') exit(1);
require dirname(__DIR__) . '/vendor/autoload.php';
$app = new think\App(dirname(__DIR__));
$app->initialize();
use app\controller\FrontendApiController;
use think\facade\Db;
use think\facade\Session;
function check($ok, string $label): void { if (!$ok) throw new RuntimeException($label); }
function controller(array $params = []): FrontendApiController {
    global $app;
    $app->instance('request', (new think\Request())->withServer(['REQUEST_METHOD'=>'GET'])->setMethod('GET')->withGet($params));
    return new FrontendApiController($app);
}
$session = Session::get('user_id');
$failed = false;
Db::startTrans();
try {
    // Read existing account IDs only; transaction changes are never committed.
    $account = Db::name('account')->order('id')->find();
    if (!$account) throw new RuntimeException('Need one existing account for read-only permission checks');
    $id = (int)$account['id'];
    $owner = (int)$account['owner'];
    $other = Db::name('user')->insertGetId(['username'=>'failure-test-'.bin2hex(random_bytes(6)), 'password'=>str_repeat('x',60), 'status'=>1,'is_admin'=>0]);
    $backend = new class {
        public int $calls = 0;
        public bool $available = true;
        public function setCurl($url, $token, $operation, $id, $extra = []): array {
            $this->calls++;
            if (!$this->available) return ['status'=>false];
            if ($operation === 'failureImage') return ['status'=>true,'data'=>['image'=>base64_encode("\x89PNG\r\n\x1a\nfixture")]];
            return ['status'=>true,'data'=>['message'=>'fixture failure', 'failed_at'=>'2026-11-13T10:00:00+00:00','images'=>[]]];
        }
    };
    $app->instance('backendService', $backend);
    Session::set('user_id', $other);
    check(controller()->accountFailure($id)->getCode() === 403, 'Foreign failure details blocked');
    check(controller(['name'=>'11111111-1111-4111-8111-111111111111.png'])->accountFailureImage($id)->getCode() === 403, 'Foreign screenshot blocked');
    check(controller(['scope'=>'admin'])->accountFailure($id)->getCode() === 403, 'Nonadmin cannot forge admin scope');
    check($backend->calls === 0, 'Ownership checked before backend call');
    Session::set('user_id', $owner);
    $response = controller()->accountFailure($id);
    check($response->getData()['data']['message'] === 'fixture failure', 'Owner can see reason');
    check($response->getHeader('Cache-Control') === 'private, no-store', 'Details never cached');
    $image = controller(['name'=>'11111111-1111-4111-8111-111111111111.png'])->accountFailureImage($id);
    check($image->getCode() === 200 && $image->getContent() === "\x89PNG\r\n\x1a\nfixture", 'Owner receives PNG bytes');
    check($image->getHeader('Cache-Control') === 'private, no-store', 'Images never cached');
    check($image->getHeader('Cross-Origin-Resource-Policy') === 'same-origin', 'Images remain same origin');
    $before = $backend->calls;
    check(controller(['name'=>'../secret.png'])->accountFailureImage($id)->getCode() === 404, 'Traversal blocked');
    check($backend->calls === $before, 'Invalid image never reaches backend');
    $backend->available = false;
    check(isset(controller()->accountFailure($id)->getData()['data']['warning']), 'Backend unavailable keeps saved reason');
    check(controller(['name'=>'11111111-1111-4111-8111-111111111111.png'])->accountFailureImage($id)->getCode() === 404, 'Missing image handled');
    Db::name('user')->where('id',$other)->update(['is_admin'=>1]);
    Session::set('user_id', $other);
    $backend->available = true;
    check(controller(['scope'=>'admin'])->accountFailure($id)->getCode() === 200, 'Admin scope can inspect accounts');
    echo "Account failure permission and proxy checks passed\n";
} catch (Throwable $e) { $failed=true; fwrite(STDERR, $e->getMessage()."\n".$e->getTraceAsString()."\n"); }
finally { Db::rollback(); Session::set('user_id', $session); }
exit($failed ? 1 : 0);
