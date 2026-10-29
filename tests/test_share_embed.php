<?php
declare(strict_types=1);
if (PHP_SAPI !== 'cli') exit(1);
require dirname(__DIR__) . '/vendor/autoload.php';
use app\service\ShareEmbedPolicy;
$checks = 0;
function embedCheck(bool $ok, string $message): void {
    global $checks;
    if (!$ok) throw new RuntimeException($message);
    $checks++;
}
embedCheck(ShareEmbedPolicy::ancestors('') === '*', 'empty allows all');
embedCheck(ShareEmbedPolicy::normalize("https://SHOP.example.com:443/\nhttps://shop.example.com") === 'https://shop.example.com', 'canonical origins');
embedCheck(ShareEmbedPolicy::ancestors("https://one.example\nhttp://localhost:8080") === 'https://one.example http://localhost:8080', 'multiple exact origins');
foreach (['https://*.example.com', 'https://example.com/path', 'https://user:pass@example.com', "https://example.com; frame-ancestors *", 'javascript:alert(1)', 'https://example.com?q=1', 'https://example.com#x'] as $bad) {
    try { ShareEmbedPolicy::normalize($bad); throw new RuntimeException('accepted bad origin'); }
    catch (InvalidArgumentException $e) { $checks++; }
}
echo "{$checks} embed policy checks passed\n";

// Optional HTTP checks against the documented disposable fixture server only.
$base = rtrim((string)getenv('EMBED_TEST_URL'), '/');
if ($base !== '') {
    if (!preg_match('~^http://(?:127\.0\.0\.1:18941|appleidauto-embed-test-web)$~', $base)) {
        throw new RuntimeException('Only the isolated fixture server is accepted');
    }
    $fetch = static function (string $link, ?string $password = null) use ($base): array {
        $options = ['ignore_errors' => true];
        if ($password !== null) $options += ['method'=>'POST', 'header'=>'Content-Type: application/x-www-form-urlencoded', 'content'=>http_build_query(['password'=>$password])];
        $body = file_get_contents($base.'/share/'.$link.'/embed', false, stream_context_create(['http'=>$options]));
        return [$body, implode("\n", $http_response_header)];
    };
    [$body, $headers] = $fetch('openframe');
    embedCheck(str_contains($body, 'fixture@example.invalid') && str_contains($headers, 'frame-ancestors *'), 'open frame');
    embedCheck(!str_contains(strtolower($headers), 'x-frame-options:'), 'open frame has no conflicting XFO');
    [$body, $headers] = $fetch('restricted');
    embedCheck(str_contains($headers, 'frame-ancestors http://embed-parent-allowed'), 'exact origin header');
    [$body] = $fetch('protected');
    embedCheck(str_contains($body, 'name="password"') && !str_contains($body, 'FixturePass1!'), 'password gate');
    [$body, $headers] = $fetch('protected', 'wrong');
    embedCheck(str_contains($headers, '403') && !str_contains($body, 'FixturePass1!'), 'wrong password rejected');
    [$body] = $fetch('protected', 'gate-secret');
    embedCheck(str_contains($body, 'FixturePass1!'), 'correct password reveals fixture');
    [$body] = $fetch('protected');
    embedCheck(!str_contains($body, 'FixturePass1!'), 'reload requires password again');
    foreach (['disabled'=>404, 'expired'=>410, 'missing'=>404] as $link=>$status) {
        [$body, $headers] = $fetch($link);
        embedCheck(str_contains($headers, (string)$status) && !str_contains($body, 'FixturePass1!'), $link.' is denied');
    }
    echo "Policy and HTTP total: {$checks} checks passed\n";
}
