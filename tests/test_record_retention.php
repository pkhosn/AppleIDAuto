<?php
declare(strict_types=1);
// Execution-record retention regression test. All writes are rolled back.
if (PHP_SAPI !== 'cli') exit(1);
require dirname(__DIR__) . '/vendor/autoload.php';
$app = new think\App(dirname(__DIR__));
$app->initialize();
use app\model\UnlockRecord;
use think\facade\Db;

$checks = 0;
function check($ok, string $name): void { global $checks; if (!$ok) throw new RuntimeException($name); $checks++; }
function insertRecord(int $accountId, string $message): int
{
    return (int)Db::name('unlock_record')->insertGetId([
        'account_id' => $accountId, 'type' => 'backend', 'status' => 1,
        'message' => $message, 'time' => date('Y-m-d H:i:s'), 'ip' => '127.0.0.1',
    ]);
}

$failed = false;
Db::startTrans();
try {
    check(UnlockRecord::RETENTION_LIMIT === 10, 'retention defaults to ten records per account');
    $firstAccount = 900000001;
    $secondAccount = 900000002;
    $thirdAccount = 900000003;
    $firstIds = [];
    for ($i = 1; $i <= 12; $i++) $firstIds[] = insertRecord($firstAccount, "first-{$i}");
    for ($i = 1; $i <= 7; $i++) insertRecord($secondAccount, "second-{$i}");

    $deleted = (new UnlockRecord())->pruneAccount($firstAccount);
    check($deleted === 2, 'pruneAccount removes rows beyond the ten-record limit');
    check(Db::name('unlock_record')->where('account_id', $firstAccount)->count() === 10, 'first account retains ten rows');
    check(Db::name('unlock_record')->where('account_id', $secondAccount)->count() === 7, 'other account is untouched');
    check(!Db::name('unlock_record')->where('id', $firstIds[0])->find(), 'oldest row is removed');
    check((bool)Db::name('unlock_record')->where('id', $firstIds[11])->find(), 'newest row is retained');

    for ($i = 1; $i <= 10; $i++) insertRecord($thirdAccount, "third-{$i}");
    (new UnlockRecord())->addRecord([
        'account_id' => $thirdAccount, 'type' => 'backend', 'status' => 1,
        'message' => 'third-live', 'ip' => '127.0.0.1',
    ]);
    check(Db::name('unlock_record')->where('account_id', $thirdAccount)->count() === 10, 'addRecord prunes its account immediately');
    check((bool)Db::name('unlock_record')->where('account_id', $thirdAccount)->where('message', 'third-live')->find(), 'addRecord keeps the new row');

    $allDeleted = (new UnlockRecord())->pruneAll(3);
    check($allDeleted >= 18, 'pruneAll reports rows removed across accounts');
    check(Db::name('unlock_record')->where('account_id', $firstAccount)->count() === 3, 'global prune limits first account independently');
    check(Db::name('unlock_record')->where('account_id', $secondAccount)->count() === 3, 'global prune limits second account independently');
    check(Db::name('unlock_record')->where('account_id', $thirdAccount)->count() === 3, 'global prune limits third account independently');

    $realAccount = Db::name('account')->order('id', 'asc')->find();
    check((bool)$realAccount, 'manual-cooldown account fixture exists');
    Db::name('user')->where('id', (int)$realAccount['owner'])->update([
        'status' => 1, 'vip_expire_at' => date('Y-m-d H:i:s', strtotime('+1 day')), 'min_manual_interval' => 30,
    ]);
    Db::name('account')->where('id', (int)$realAccount['id'])->update(['last_manual_unlock_at' => date('Y-m-d H:i:s')]);
    for ($i = 1; $i <= 12; $i++) insertRecord((int)$realAccount['id'], "cooldown-{$i}");
    (new UnlockRecord())->pruneAccount((int)$realAccount['id']);
    $cooldown = $app->entitlementService->canManualUnlock((int)$realAccount['owner'], (int)$realAccount['id']);
    check(!$cooldown['status'], 'manual cooldown survives execution-record pruning');
} catch (Throwable $e) {
    fwrite(STDERR, 'FAIL: ' . $e->getMessage() . "\n");
    $failed = true;
} finally {
    Db::rollback();
}
if (!$failed) echo "{$checks} execution-record retention checks passed (transaction rolled back).\n";
exit($failed ? 1 : 0);
