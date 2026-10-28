<?php
// Read-only release check for an explicitly selected site and matching local config.
// A fresh diagnostic session is removed in finally.
if (PHP_SAPI !== 'cli') exit(1);
$baseUrl = rtrim(trim((string)getenv('RELEASE_CHECK_BASE_URL')), '/');
$target = parse_url($baseUrl);
if (!$target || ($target['scheme'] ?? '') !== 'https' || empty($target['host'])
    || !empty($target['path']) || isset($target['user']) || isset($target['pass'])
    || isset($target['query']) || isset($target['fragment'])) {
    fwrite(STDERR, "Set RELEASE_CHECK_BASE_URL to the HTTPS origin of the site matching the local application configuration.\n");
    exit(1);
}
require dirname(__DIR__) . '/vendor/autoload.php';
$app = new think\App(dirname(__DIR__));
$app->initialize();
use think\facade\Db;
use think\facade\Session;
$failed = false;
Session::setId(bin2hex(random_bytes(20)));
Session::init();
try {
    $admin = Db::name('user')->where('is_admin', 1)->where('status', 1)->order('id')->find();
    if (!$admin) throw new RuntimeException('No active administrator for read-only release check');
    Session::set('user_id', $admin['id']);
    Session::set('frontend_csrf', bin2hex(random_bytes(32)));
    Session::save();
    $paths = ['automation-defaults', 'admin/settings'];
    foreach (Db::name('account')->field('id')->select() as $row) $paths[] = 'accounts/' . $row['id'] . '/schedule?scope=admin';
    foreach ($paths as $path) {
        $ch = curl_init($baseUrl . '/frontend-api/v1/' . $path);
        curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 15,
            CURLOPT_HTTPHEADER => ['Accept: application/json', 'Cookie: PHPSESSID=' . Session::getId()]]);
        $body = json_decode((string)curl_exec($ch), true);
        $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
        if ($code !== 200 || empty($body['success'])) throw new RuntimeException('Release check failed for ' . $path . ': HTTP ' . $code);
        if (str_contains($path, '/schedule') && count($body['data']['features'] ?? []) !== 5) throw new RuntimeException('Schedule route did not return five feature states');
        if ($path === 'automation-defaults' && empty($body['data']['active'])) throw new RuntimeException('Independent scheduler is not active');
        echo "PASS $path\n";
    }
} catch (Throwable $e) {
    fwrite(STDERR, $e->getMessage() . "\n");
    $failed = true;
} finally {
    Session::destroy();
}
exit($failed ? 1 : 0);
