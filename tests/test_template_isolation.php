<?php
declare(strict_types=1);
if (PHP_SAPI !== 'cli') exit(1);
require dirname(__DIR__) . '/vendor/autoload.php';
$app = new think\App(dirname(__DIR__));
$app->initialize();
use app\controller\FrontendApiController;
use think\facade\Db;
use think\facade\Session;

$checks = 0;
function checkTemplate($ok, string $message): void { global $checks; if (!$ok) throw new RuntimeException($message); $checks++; }
function templateController(array $params = [], string $method = 'POST'): FrontendApiController
{
    global $app;
    $request = (new think\Request())->withServer(['REQUEST_METHOD' => $method])->setMethod($method);
    if ($method === 'GET') $request = $request->withGet($params); else $request = $request->withPost($params);
    $app->instance('request', $request);
    return new FrontendApiController($app);
}

$failed = false;
Db::startTrans();
try {
    $users = Db::name('user')->order('id', 'asc')->limit(2)->select()->toArray();
    checkTemplate(count($users) >= 2, 'two user fixtures exist');
    $ownerOne = (int)$users[0]['id'];
    $ownerTwo = (int)$users[1]['id'];
    Db::name('user')->where('id', $ownerOne)->update(['is_admin' => 1]);
    $now = date('Y-m-d H:i:s');
    $foreignId = Db::name('share_template')->insertGetId([
        'owner' => $ownerTwo, 'name' => 'private foreign template', 'html' => '<p>foreign</p>',
        'notice_html' => '', 'is_default' => 1, 'create_time' => $now, 'update_time' => $now,
    ]);
    Session::set('user_id', $ownerOne);

    $list = json_decode(templateController([], 'GET')->templates()->getContent(), true);
    checkTemplate(($list['success'] ?? false) === true, 'template list succeeds');
    checkTemplate(array_reduce($list['data'] ?? [], fn($ok, $row) => $ok && (int)$row['owner'] === $ownerOne, true), 'admin list contains only own templates');
    checkTemplate(!array_filter($list['data'] ?? [], fn($row) => (int)$row['id'] === $foreignId || (int)$row['owner'] === 0), 'foreign and system templates are hidden');

    $created = templateController(['owner' => 0, 'name' => 'private new template', 'html' => '<p>mine</p>'])->saveTemplate();
    checkTemplate($created->getCode() === 200, 'template creation succeeds');
    $createdRow = Db::name('share_template')->where('name', 'private new template')->order('id', 'desc')->find();
    checkTemplate((int)$createdRow['owner'] === $ownerOne, 'submitted owner is ignored');

    $update = templateController(['name' => 'stolen', 'html' => '<p>stolen</p>'])->saveTemplate($foreignId);
    checkTemplate($update->getCode() === 403, 'admin cannot update another user template');
    checkTemplate(templateController()->setDefaultTemplate($foreignId)->getCode() === 403, 'admin cannot set another user template as default');
    checkTemplate(templateController([], 'DELETE')->deleteTemplate($foreignId)->getCode() === 403, 'admin cannot delete another user template');

    Session::set('user_id', $ownerTwo);
    $secondList = json_decode(templateController([], 'GET')->templates()->getContent(), true);
    checkTemplate(array_reduce($secondList['data'] ?? [], fn($ok, $row) => $ok && (int)$row['owner'] === $ownerTwo, true), 'second user sees only their own templates');
    checkTemplate(!array_filter($secondList['data'] ?? [], fn($row) => (int)$row['owner'] === $ownerOne || (int)$row['owner'] === 0), 'second user cannot see first user or system templates');
    Session::set('user_id', $ownerOne);

    $account = Db::name('account')->where('owner', $ownerOne)->order('id', 'asc')->find();
    checkTemplate((bool)$account, 'owner account fixture exists');
    $group = templateController([
        'name' => 'cross-owner group', 'template_id' => $foreignId,
        'account_list' => [(int)$account['id']], 'duration_minutes' => 0,
    ])->saveShareGroup();
    checkTemplate($group->getCode() === 422, 'share group rejects another user template');
    $share = templateController([
        'template_id' => $foreignId, 'account_list' => [(int)$account['id']],
        'share_link' => 'iso' . bin2hex(random_bytes(4)),
    ])->saveShare();
    checkTemplate($share->getCode() === 422, 'share page rejects another user template');

    $oldDefault = Db::name('share_template')->where('owner', $ownerOne)->where('is_default', 1)
        ->where('id', '<>', (int)$createdRow['id'])->order('id', 'asc')->find();
    if ($oldDefault) {
        $deleted = templateController([], 'DELETE')->deleteTemplate((int)$oldDefault['id']);
        checkTemplate($deleted->getCode() === 200, 'own default template can be deleted');
        checkTemplate((int)Db::name('share_template')->where('id', (int)$createdRow['id'])->value('is_default') === 1, 'next private template is promoted to default');
    }
} catch (Throwable $e) {
    $failed = true;
    fwrite(STDERR, 'FAIL: ' . $e->getMessage() . "\n");
} finally {
    Db::rollback();
    Session::delete('user_id');
}
if (!$failed) echo "{$checks} template-isolation checks passed (transaction rolled back).\n";
exit($failed ? 1 : 0);
