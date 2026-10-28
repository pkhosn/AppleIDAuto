<?php
declare(strict_types=1);
// CLI-only helpers. Never render configuration, database errors or HTTP bodies.
if (PHP_SAPI !== 'cli') exit(1);

final class UpgradeError extends RuntimeException {}

function ensure(bool $condition, string $message): void
{
    if (!$condition) throw new UpgradeError($message);
}

function command(array $arguments): string
{
    $process = proc_open($arguments, [0 => ['file', '/dev/null', 'r'],
        1 => ['pipe', 'w'], 2 => ['file', '/dev/null', 'w']], $pipes);
    ensure(is_resource($process), '无法启动检查命令');
    $output = stream_get_contents($pipes[1]);
    fclose($pipes[1]);
    ensure(proc_close($process) === 0, 'Git 检查失败');
    return (string)$output;
}

function git(string $root, array $arguments): string
{
    return command(array_merge(['git', '-C', $root], $arguments));
}

function readJson(string $path): array
{
    $result = json_decode((string)file_get_contents($path), true, 512, JSON_THROW_ON_ERROR);
    ensure(is_array($result), '升级状态格式无效');
    return $result;
}

function atomicJson(string $path, array $value): void
{
    $temporary = $path . '.tmp-' . bin2hex(random_bytes(6));
    try {
        ensure(file_put_contents($temporary, json_encode($value, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE)) !== false,
            '无法保存升级状态');
        ensure(chmod($temporary, 0600) && rename($temporary, $path), '无法安全保存升级状态');
    } finally {
        if (is_file($temporary)) unlink($temporary);
    }
}

function artifactPath(string $path): bool
{
    return (str_starts_with($path, 'public/control-app/') || str_starts_with($path, 'public/react-preview/'))
        && !in_array('..', explode('/', $path), true);
}

function inventory(string $root): array
{
    $files = [];
    foreach (['public/control-app', 'public/react-preview'] as $directory) {
        ensure(is_dir($root . '/' . $directory) && !is_link($root . '/' . $directory), '前端产物目录无效');
        $iterator = new RecursiveIteratorIterator(new RecursiveDirectoryIterator(
            $root . '/' . $directory, FilesystemIterator::SKIP_DOTS));
        foreach ($iterator as $file) {
            ensure(!$file->isLink(), '前端产物中存在符号链接');
            if (!$file->isFile()) continue;
            $path = substr($file->getPathname(), strlen($root) + 1);
            ensure(artifactPath($path), '前端产物路径无效');
            $files[$path] = ['sha256' => hash_file('sha256', $file->getPathname()), 'mode' => $file->getPerms() & 0777];
        }
    }
    ksort($files);
    return $files;
}

function workspace(string $root, string $statePath, bool $normalize = false): void
{
    ensure(trim(git($root, ['diff', '--cached', '--name-only'])) === '', '暂存区有改动，请先提交或移走');
    $head = trim(git($root, ['rev-parse', 'HEAD']));
    $files = inventory($root);
    $state = is_file($statePath) ? readJson($statePath) : [];
    $managed = ($state['version'] ?? null) === 1 && ($state['commit'] ?? '') === $head
        && ($state['files'] ?? null) === $files;
    $entries = explode("\0", rtrim(git($root, ['status', '--porcelain=v1', '-z', '--untracked-files=all']), "\0"));
    foreach ($entries as $entry) {
        if ($entry === '') continue;
        $status = substr($entry, 0, 2);
        $path = substr($entry, 3);
        ensure(artifactPath($path) && $managed && in_array($status, [' M', ' D', '??'], true),
            '工作区存在源码或人工产物改动，请先提交或移走');
    }
    // Also reject ignored files in build directories: replacing a directory must
    // not silently discard a user's ignored notes or local assets.
    if (!$managed) {
        $tracked = array_filter(explode("\0", git($root, ['ls-files', '-z', '--', 'public/control-app', 'public/react-preview'])));
        sort($tracked);
        ensure(array_keys($files) === $tracked, '产物目录有未登记文件或缺失文件');
    }
    if (!$normalize) return;
    $tracked = array_fill_keys(array_filter(explode("\0", git($root, ['ls-files', '-z', '--',
        'public/control-app', 'public/react-preview']))), true);
    foreach ($files as $path => $_) {
        if (!isset($tracked[$path])) ensure(unlink($root . '/' . $path), '无法清理已登记产物');
    }
    git($root, ['restore', '--source=HEAD', '--worktree', '--', 'public/control-app', 'public/react-preview']);
}

function configuration(string $root): array
{
    $panel = parse_ini_file($root . '/.env', true, INI_SCANNER_RAW);
    $automation = parse_ini_file($root . '/automation-server/.env', false, INI_SCANNER_RAW);
    ensure(is_array($panel) && is_array($automation), '两份 .env 必须存在且格式正确');
    $panel = array_change_key_case($panel, CASE_UPPER);
    foreach ($panel as &$section) {
        if (is_array($section)) $section = array_change_key_case($section, CASE_UPPER);
    }
    unset($section);
    foreach (['CREDENTIALS', 'MAILBOX'] as $kind) {
        $key = base64_decode((string)($panel[$kind]['CIPHER_KEY'] ?? ''), true);
        ensure($key !== false && strlen($key) === 32, '原加密密钥缺失或无效，请先恢复配置');
    }
    foreach (['AUTOMATION_TOKEN', 'PANEL_API_KEY', 'ACCOUNT_DRIVER_TOKEN'] as $name) {
        ensure(strlen((string)($automation[$name] ?? '')) >= 32
            && !str_starts_with($automation[$name], 'replace-with-'), '服务令牌未正确配置');
    }
    ensure(hash_equals((string)($panel['API_KEY'] ?? ''), $automation['PANEL_API_KEY'])
        && hash_equals((string)($panel['BACKEND']['TOKEN'] ?? ''), $automation['AUTOMATION_TOKEN']),
        '两端服务令牌不一致');
    ensure(rtrim((string)($automation['ACCOUNT_DRIVER_URL'] ?? ''), '/') === 'http://account-driver:3940',
        '自动升级仅支持本项目 Compose 内置驱动');
    $url = parse_url((string)($automation['PANEL_URL'] ?? ''));
    ensure(is_array($url) && ($url['scheme'] ?? '') === 'https' && !empty($url['host'])
        && in_array($url['path'] ?? '', ['', '/'], true) && !isset($url['user']) && !isset($url['pass'])
        && !isset($url['query']) && !isset($url['fragment']), 'PANEL_URL 必须为管理站 HTTPS 根地址');
    return [$panel, $automation];
}

function panelIdle(string $root): void
{
    [$panel] = configuration($root);
    $database = $panel['DATABASE'] ?? [];
    ensure(empty($database['PREFIX']), '自动迁移不支持自定义表前缀，请按运维指南处理');
    $pdo = new PDO('mysql:host=' . ($database['HOSTNAME'] ?? '127.0.0.1') . ';port=' . ($database['HOSTPORT'] ?? '3306')
        . ';dbname=' . ($database['DATABASE'] ?? '') . ';charset=utf8mb4', (string)($database['USERNAME'] ?? ''),
        (string)($database['PASSWORD'] ?? ''), [PDO::ATTR_TIMEOUT => 5, PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    $columns = $pdo->query('SHOW COLUMNS FROM account')->fetchAll(PDO::FETCH_COLUMN);
    ensure(in_array('automation_schedule', $columns, true), '旧库缺少独立周期结构，请先按运维指南迁移');
    ensure(!(bool)$pdo->query('SELECT 1 FROM account WHERE enable<>0 LIMIT 1')->fetchColumn(),
        '请先停用全部账号自动任务，等待执行与邮件恢复结束');
    foreach (['share', 'share_template'] as $table) $pdo->query('SELECT 1 FROM `' . $table . '` LIMIT 1');
}

function fingerprints(string $root): array
{
    $result = [];
    foreach (['.env', 'automation-server/.env'] as $path) {
        $stat = stat($root . '/' . $path);
        ensure($stat !== false, '配置文件不可读取');
        $result[$path] = ['sha256' => hash_file('sha256', $root . '/' . $path), 'mode' => $stat['mode'] & 0777,
            'uid' => $stat['uid'], 'gid' => $stat['gid']];
    }
    return $result;
}

function recordArtifacts(string $root, string $statePath): void
{
    atomicJson($statePath, ['version' => 1, 'commit' => trim(git($root, ['rev-parse', 'HEAD'])), 'files' => inventory($root)]);
}

function deploy(string $root, string $stage, string $statePath): void
{
    foreach (['vendor', 'public/control-app', 'public/react-preview'] as $path) {
        $source = $stage . '/source/' . $path;
        ensure(is_dir($source) && !is_link($source), '准备的部署目录无效');
        $destination = $root . '/' . $path;
        ensure(!is_link($destination), '部署目录不能是符号链接');
        $previous = $stage . '/previous-' . str_replace('/', '-', $path);
        $existed = is_dir($destination);
        ensure(!$existed || rename($destination, $previous), '无法保存替换前的部署目录');
        if (!rename($source, $destination)) {
            if ($existed) rename($previous, $destination);
            throw new UpgradeError('无法安装准备好的部署目录');
        }
        // Record each completed directory swap so a later failure is retryable.
        recordArtifacts($root, $statePath);
    }
}

function bootstrap(string $root): void
{
    [, $automation] = configuration($root);
    $handle = curl_init(rtrim($automation['PANEL_URL'], '/') . '/frontend-api/v1/bootstrap');
    curl_setopt_array($handle, [CURLOPT_RETURNTRANSFER => true, CURLOPT_CONNECTTIMEOUT => 5, CURLOPT_TIMEOUT => 15,
        CURLOPT_HTTPHEADER => ['Accept: application/json', 'Cache-Control: no-cache'], CURLOPT_FOLLOWLOCATION => false]);
    $body = curl_exec($handle);
    $status = curl_getinfo($handle, CURLINFO_HTTP_CODE);
    curl_close($handle);
    $data = is_string($body) ? json_decode($body, true) : null;
    ensure($status === 200 && is_array($data) && ($data['success'] ?? false) === true
        && is_array($data['data'] ?? null) && array_key_exists('authenticated', $data['data'])
        && isset($data['data']['admin_base']), 'PHP bootstrap 健康检查失败，请检查 HTTPS、PHP-FPM 和路由');
}

try {
    $root = realpath($argv[1] ?? '');
    ensure($root !== false && is_file($root . '/think'), '项目目录无效');
    $action = $argv[2] ?? '';
    switch ($action) {
        case 'platform':
            ensure(PHP_VERSION_ID >= 80000, '需要 PHP 8.0 或以上，建议 PHP 8.2');
            foreach (['pdo_mysql', 'curl', 'dom', 'mbstring', 'openssl'] as $extension)
                ensure(extension_loaded($extension), 'PHP 缺少必要扩展：' . $extension);
            ensure(function_exists('proc_open'), 'PHP CLI 需允许 proc_open');
            configuration($root);
            break;
        case 'workspace': workspace($root, $argv[3]); break;
        case 'normalize': workspace($root, $argv[3], true); break;
        case 'panel-idle': panelIdle($root); break;
        case 'fingerprint': echo json_encode(fingerprints($root), JSON_THROW_ON_ERROR); break;
        case 'verify-config':
            ensure(fingerprints($root) === readJson($argv[3]), '配置内容、权限或所有者发生变化，请停止并核对备份');
            break;
        case 'snapshot':
            $directory = $argv[3];
            foreach (['.env' => 'panel.env', 'automation-server/.env' => 'automation.env'] as $source => $name) {
                ensure(copy($root . '/' . $source, $directory . '/' . $name)
                    && chmod($directory . '/' . $name, 0600), '无法安全备份配置');
            }
            atomicJson($directory . '/config-metadata.json', fingerprints($root));
            break;
        case 'deploy': deploy($root, $argv[3], $argv[4]); break;
        case 'record-artifacts': recordArtifacts($root, $argv[3]); break;
        case 'bootstrap': bootstrap($root); break;
        case 'json-name':
            $config = json_decode((string)stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
            ensure(is_string($config['name'] ?? null) && preg_match('/^[a-z0-9][a-z0-9_-]*$/D', $config['name']) === 1,
                'Compose 项目名称无效');
            echo $config['name'];
            break;
        case 'private-directory':
            $directory = realpath($argv[3]);
            ensure($directory !== false && $directory !== $root && !str_starts_with($directory . '/', $root . '/')
                && !is_link($argv[3]) && (fileperms($directory) & 0077) === 0, '备份目录必须在项目外且仅当前用户可访问');
            break;
        case 'same-filesystem':
            ensure(stat($root)['dev'] === stat($argv[3])['dev'], '暂存目录与项目不在同一文件系统，请调整部署目录');
            break;
        default: throw new UpgradeError('未知升级检查操作');
    }
} catch (Throwable $error) {
    // PDO, JSON and filesystem exception details can contain secrets/paths.
    fwrite(STDERR, ($error instanceof UpgradeError ? $error->getMessage() : '升级检查失败，请核对配置、数据库及目录权限') . "\n");
    exit(1);
}
