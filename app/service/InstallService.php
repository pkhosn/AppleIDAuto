<?php
declare(strict_types=1);

namespace app\service;

use PDO;
use RuntimeException;

class InstallService
{
    private const REQUIRED_TABLES = [
        'account',
        'automation_task_log',
        'order',
        'payment_config',
        'plan',
        'proxy',
        'proxy_source',
        'redeem_code',
        'share',
        'share_group',
        'share_template',
        'credential_audit',
        'site_config',
        'unlock_record',
        'user',
    ];

    private string $root;

    public function __construct()
    {
        $this->root = root_path();
    }

    public function environment(): array
    {
        $runtime = $this->root . 'runtime';
        if (!is_dir($runtime)) {
            @mkdir($runtime, 0775, true);
        }

        $checks = [
            $this->check('PHP 版本', PHP_VERSION, version_compare(PHP_VERSION, '7.4.0', '>=')),
        ];
        foreach (['pdo', 'pdo_mysql', 'curl', 'json', 'openssl', 'mbstring', 'dom'] as $extension) {
            $checks[] = $this->check('PHP 扩展：' . $extension, extension_loaded($extension) ? '已加载' : '未加载', extension_loaded($extension));
        }
        $checks[] = $this->check('runtime 目录', is_writable($runtime) ? '可写' : '只读', is_writable($runtime));
        $checks[] = $this->check('.env 配置文件', $this->targetWritable($this->root . '.env') ? '可写' : '只读', $this->targetWritable($this->root . '.env'));
        $checks[] = $this->check('自动化服务配置', $this->targetWritable($this->root . 'automation-server/.env') ? '可写' : '只读', $this->targetWritable($this->root . 'automation-server/.env'));
        $checks[] = $this->check('数据库初始化文件', is_readable($this->root . 'db/db.sql') ? '可读' : '缺失', is_readable($this->root . 'db/db.sql'));
        $checks[] = $this->check('网络连接函数', function_exists('fsockopen') ? '可用' : '未启用', function_exists('fsockopen'));
        return $checks;
    }

    public function ready(): bool
    {
        foreach ($this->environment() as $check) {
            if (!$check['ok']) {
                return false;
            }
        }
        return true;
    }

    public function defaults(string $siteUrl): array
    {
        return [
            'site_url' => rtrim($siteUrl, '/'),
            'db_host' => (string)env('database.hostname', '127.0.0.1'),
            'db_port' => (string)env('database.hostport', '3306'),
            'db_name' => (string)env('database.database', ''),
            'db_user' => (string)env('database.username', 'root'),
            'redis_host' => '127.0.0.1',
            'redis_container_host' => 'redis',
            'redis_port' => '6379',
            'redis_db' => '0',
        ];
    }

    public function test(array $data): array
    {
        $secrets = [$data['db_password'] ?? '', $data['redis_password'] ?? ''];
        $result = ['database' => ['ok' => false, 'message' => '未测试'], 'redis' => ['ok' => false, 'message' => '未测试']];

        try {
            $pdo = $this->database($data);
            $version = (string)$pdo->query('SELECT VERSION()')->fetchColumn();
            $result['database'] = ['ok' => true, 'message' => '连接成功（MySQL ' . $version . '）'];
        } catch (\Throwable $e) {
            $result['database']['message'] = $this->safeMessage($e, $secrets);
        }

        try {
            $this->pingRedis($data);
            $result['redis'] = ['ok' => true, 'message' => '连接成功（PING/PONG）'];
        } catch (\Throwable $e) {
            $result['redis']['message'] = $this->safeMessage($e, $secrets);
        }

        $result['ok'] = $result['database']['ok'] && $result['redis']['ok'];
        return $result;
    }

    public function install(array $data): void
    {
        if (is_file($this->root . 'runtime/install.lock')) {
            throw new RuntimeException('项目已完成安装');
        }
        if (!$this->ready()) {
            throw new RuntimeException('环境检查未通过，请修复红色项目后刷新页面');
        }
        $this->validate($data);

        $pdo = $this->database($data);
        $this->pingRedis($data);

        $guardPath = $this->root . 'runtime/installing.lock';
        $guard = fopen($guardPath, 'c');
        if (!$guard || !flock($guard, LOCK_EX | LOCK_NB)) {
            throw new RuntimeException('安装程序正在运行，请稍后刷新');
        }

        try {
            $this->initializeDatabase($pdo);
            $keys = $this->installationKeys($pdo);
            $this->createAdmin($pdo, trim((string)$data['admin_username']), (string)$data['admin_password']);

            $panelToken = bin2hex(random_bytes(24));
            $automationToken = bin2hex(random_bytes(24));
            $driverToken = bin2hex(random_bytes(24));
            $redisUrl = $this->redisUrl($data);

            $this->updateEnv($this->root . '.env', [
                '' => [
                    'APP_DEBUG' => 'false',
                    'ENABLE_REGISTER' => 'false',
                    'API_KEY' => $panelToken,
                ],
                'BACKEND' => [
                    'ENABLE_API' => 'true',
                    'LISTEN_IP' => '127.0.0.1',
                    'LISTEN_PORT' => '3939',
                    'API_URL' => 'http://127.0.0.1:3939',
                    'TOKEN' => $automationToken,
                ],
                'DATABASE' => [
                    'TYPE' => 'mysql',
                    'HOSTNAME' => trim((string)$data['db_host']),
                    'DATABASE' => trim((string)$data['db_name']),
                    'USERNAME' => trim((string)$data['db_user']),
                    'PASSWORD' => (string)($data['db_password'] ?? ''),
                    'HOSTPORT' => (string)(int)$data['db_port'],
                    'CHARSET' => 'utf8mb4',
                ],
                'APP' => ['DEFAULT_TIMEZONE' => 'Asia/Shanghai'],
                'CREDENTIALS' => ['CIPHER_KEY' => $keys['credentials']],
                'MAILBOX' => ['CIPHER_KEY' => $keys['mailbox']],
            ]);

            $this->updateEnv($this->root . 'automation-server/.env', [
                '' => [
                    'AUTOMATION_TOKEN' => $automationToken,
                    'PANEL_URL' => rtrim(trim((string)$data['site_url']), '/'),
                    'PANEL_API_KEY' => $panelToken,
                    'REDIS_URL' => $redisUrl,
                    'ACCOUNT_DRIVER_TOKEN' => $driverToken,
                ],
            ]);

            if (@file_put_contents($this->root . 'runtime/install.lock', date(DATE_ATOM) . "\n", LOCK_EX) === false) {
                throw new RuntimeException('安装锁写入失败，请检查 runtime 目录权限');
            }
        } finally {
            flock($guard, LOCK_UN);
            fclose($guard);
            @unlink($guardPath);
        }
    }

    private function initializeDatabase(PDO $pdo): void
    {
        $tables = $pdo->query('SHOW TABLES')->fetchAll(PDO::FETCH_COLUMN);
        if ($tables && !array_diff(array_diff(self::REQUIRED_TABLES, ['share_group']), $tables)) {
            ShareSchemaMigration::apply($pdo);
            $tables = $pdo->query('SHOW TABLES')->fetchAll(PDO::FETCH_COLUMN);
        }
        $missing = array_values(array_diff(self::REQUIRED_TABLES, $tables));
        if (!$missing) {
            $this->validateInitializedDatabase($pdo);
            return;
        }
        if ($tables) {
            throw new RuntimeException('数据库结构不完整，缺失表：' . implode(', ', $missing) . '。请使用空数据库或人工迁移后重试');
        }

        $sql = file_get_contents($this->root . 'db/db.sql');
        if ($sql === false) {
            throw new RuntimeException('数据库初始化文件读取失败');
        }
        foreach (preg_split('/;\s*(?:\r?\n|$)/', $sql) as $statement) {
            $statement = trim($statement);
            if ($statement !== '' && !preg_match('/^\/\*.*\*\/$/s', $statement)) {
                $pdo->exec($statement);
            }
        }
        $this->validateInitializedDatabase($pdo);
    }

    private function validateInitializedDatabase(PDO $pdo): void
    {
        $tables = $pdo->query('SHOW TABLES')->fetchAll(PDO::FETCH_COLUMN);
        $missing = array_values(array_diff(self::REQUIRED_TABLES, $tables));
        if ($missing) {
            throw new RuntimeException('数据库初始化未完成，缺失表：' . implode(', ', $missing));
        }

        foreach (['share' => ['group_id', 'template_id'],
                  'share_group' => ['owner', 'name', 'account_list', 'template_id', 'password', 'duration_minutes', 'expire', 'base_url', 'access_tracking'],
                  'share_template' => ['owner', 'html', 'notice_html', 'is_default']] as $table => $required) {
            $columns = array_column($pdo->query("SHOW COLUMNS FROM `{$table}`")->fetchAll(PDO::FETCH_ASSOC), 'Field');
            if (array_diff($required, $columns)) {
                throw new RuntimeException('数据库初始化未完成：' . $table . ' 缺少关联字段');
            }
        }

        $paymentConfigCount = (int)$pdo->query('SELECT COUNT(*) FROM `payment_config`')->fetchColumn();
        if ($paymentConfigCount < 1) {
            throw new RuntimeException('数据库初始化未完成：payment_config 缺少默认配置');
        }
    }

    private function installationKeys(PDO $pdo): array
    {
        $keys = [];
        foreach (['credentials', 'mailbox'] as $kind) {
            $key = trim((string)env($kind . '.cipher_key', ''));
            $decoded = base64_decode($key, true);
            if ($decoded !== false && strlen($decoded) === 32) {
                $keys[$kind] = $key;
                continue;
            }
            $fields = $kind === 'credentials' ? [
                'account' => ['password', 'answer1', 'answer2', 'answer3'],
                'user' => ['tg_bot_token', 'tg_chat_id', 'wx_pusher_id', 'webhook'],
                'proxy' => ['content'], 'proxy_source' => ['source_url'], 'payment_config' => ['merchant_key'],
            ] : ['account' => ['mailbox_password_cipher'], 'user' => ['mailbox_password_cipher']];
            foreach ($fields as $table => $columns) {
                foreach ($columns as $column) {
                    $pattern = $kind === 'credentials' ? 'enc:v1:%' : 'v1:%';
                    $query = $pdo->prepare("SELECT COUNT(*) FROM `{$table}` WHERE `{$column}` LIKE ?");
                    $query->execute([$pattern]);
                    if ((int)$query->fetchColumn() > 0) {
                        throw new RuntimeException('已有密文但 ' . strtoupper($kind) . '.CIPHER_KEY 缺失或无效，请恢复原密钥');
                    }
                }
            }
            $keys[$kind] = base64_encode(random_bytes(32));
        }
        return $keys;
    }

    private function createAdmin(PDO $pdo, string $username, string $password): void
    {
        $hash = password_hash($password, PASSWORD_DEFAULT);
        $find = $pdo->prepare('SELECT id FROM `user` WHERE username = ? LIMIT 1');
        $find->execute([$username]);
        $id = $find->fetchColumn();
        if ($id) {
            $statement = $pdo->prepare('UPDATE `user` SET password = ?, is_admin = 1, status = 1 WHERE id = ?');
            $statement->execute([$hash, $id]);
            return;
        }
        $statement = $pdo->prepare('INSERT INTO `user` (username, password, is_admin, status) VALUES (?, ?, 1, 1)');
        $statement->execute([$username, $hash]);
    }

    private function database(array $data): PDO
    {
        $host = trim((string)($data['db_host'] ?? ''));
        $name = trim((string)($data['db_name'] ?? ''));
        $user = trim((string)($data['db_user'] ?? ''));
        $port = (int)($data['db_port'] ?? 0);
        if ($host === '' || $name === '' || $user === '' || $port < 1 || $port > 65535 || preg_match('/[;\x00-\x1F]/', $host . $name)) {
            throw new RuntimeException('数据库配置格式错误');
        }
        return new PDO(
            "mysql:host={$host};port={$port};dbname={$name};charset=utf8mb4",
            $user,
            (string)($data['db_password'] ?? ''),
            [
                PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                PDO::ATTR_TIMEOUT => 5,
                PDO::ATTR_EMULATE_PREPARES => false,
            ]
        );
    }

    private function pingRedis(array $data): void
    {
        $host = trim((string)($data['redis_host'] ?? ''));
        $port = (int)($data['redis_port'] ?? 0);
        $database = (int)($data['redis_db'] ?? 0);
        if ($host === '' || $port < 1 || $port > 65535 || $database < 0 || $database > 15 || preg_match('/[\x00-\x20]/', $host)) {
            throw new RuntimeException('Redis 配置格式错误');
        }
        $socket = @fsockopen($host, $port, $errorNumber, $errorMessage, 5);
        if (!$socket) {
            throw new RuntimeException('Redis 连接失败：' . ($errorMessage ?: (string)$errorNumber));
        }
        stream_set_timeout($socket, 5);
        try {
            $password = (string)($data['redis_password'] ?? '');
            $username = trim((string)($data['redis_username'] ?? ''));
            if ($password !== '') {
                $this->redisCommand($socket, $username === '' ? ['AUTH', $password] : ['AUTH', $username, $password]);
            }
            if ($database > 0) {
                $this->redisCommand($socket, ['SELECT', (string)$database]);
            }
            if ($this->redisCommand($socket, ['PING']) !== 'PONG') {
                throw new RuntimeException('Redis PING 响应异常');
            }
        } finally {
            fclose($socket);
        }
    }

    private function redisCommand($socket, array $arguments): string
    {
        $command = '*' . count($arguments) . "\r\n";
        foreach ($arguments as $argument) {
            $argument = (string)$argument;
            $command .= '$' . strlen($argument) . "\r\n" . $argument . "\r\n";
        }
        if (fwrite($socket, $command) === false) {
            throw new RuntimeException('Redis 请求发送失败');
        }
        $line = fgets($socket);
        if ($line === false) {
            throw new RuntimeException('Redis 未返回响应');
        }
        $line = rtrim($line, "\r\n");
        if ($line !== '' && $line[0] === '-') {
            throw new RuntimeException('Redis 返回错误：' . substr($line, 1));
        }
        return substr($line, 1);
    }

    private function redisUrl(array $data): string
    {
        $host = trim((string)($data['redis_container_host'] ?? $data['redis_host']));
        if ($host === '' || preg_match('/[\x00-\x20\/@]/', $host)) {
            throw new RuntimeException('Redis 容器访问地址格式错误');
        }
        if (strpos($host, ':') !== false && $host[0] !== '[') {
            $host = '[' . $host . ']';
        }
        $password = (string)($data['redis_password'] ?? '');
        $username = trim((string)($data['redis_username'] ?? ''));
        $auth = '';
        if ($password !== '') {
            $auth = ($username === '' ? '' : rawurlencode($username)) . ':' . rawurlencode($password) . '@';
        }
        return 'redis://' . $auth . $host . ':' . (int)$data['redis_port'] . '/' . (int)$data['redis_db'];
    }

    private function validate(array $data): void
    {
        $username = trim((string)($data['admin_username'] ?? ''));
        $password = (string)($data['admin_password'] ?? '');
        if (!preg_match('/^[\p{L}\p{N}_-]{3,32}$/u', $username)) {
            throw new RuntimeException('管理员用户名需为 3–32 位字母、数字、中文、下划线或短横线');
        }
        if (strlen($password) < 10 || strlen($password) > 72) {
            throw new RuntimeException('管理员密码长度需为 10–72 位');
        }
        if (!hash_equals($password, (string)($data['admin_password_confirm'] ?? ''))) {
            throw new RuntimeException('两次输入的管理员密码不一致');
        }
        $siteUrl = trim((string)($data['site_url'] ?? ''));
        if (!filter_var($siteUrl, FILTER_VALIDATE_URL) || !in_array(parse_url($siteUrl, PHP_URL_SCHEME), ['http', 'https'], true)) {
            throw new RuntimeException('站点地址需为完整的 HTTP 或 HTTPS 地址');
        }
        foreach (['db_user', 'db_password', 'redis_username', 'redis_password'] as $field) {
            if (preg_match('/[\x00\r\n]/', (string)($data[$field] ?? ''))) {
                throw new RuntimeException('连接配置包含无效字符');
            }
        }
    }

    private function updateEnv(string $path, array $changes): void
    {
        $content = is_file($path) ? (string)file_get_contents($path) : '';
        $lines = preg_split('/\r\n|\r|\n/', $content);
        $section = '';
        $written = [];

        foreach ($lines as &$line) {
            if (preg_match('/^\s*\[([^]]+)]\s*$/', $line, $match)) {
                $section = strtoupper($match[1]);
                continue;
            }
            if (preg_match('/^\s*([A-Z][A-Z0-9_]*)\s*=/', $line, $match)) {
                $key = strtoupper($match[1]);
                if (isset($changes[$section]) && array_key_exists($key, $changes[$section])) {
                    $line = $key . ' = ' . $this->envValue((string)$changes[$section][$key]);
                    $written[$section][$key] = true;
                }
            }
        }
        unset($line);

        foreach ($changes as $targetSection => $values) {
            $missing = array_diff_key($values, $written[$targetSection] ?? []);
            if (!$missing) continue;
            $insert = [];
            foreach ($missing as $key => $value) $insert[] = $key . ' = ' . $this->envValue((string)$value);
            if ($targetSection === '') {
                array_splice($lines, 0, 0, $insert);
                continue;
            }
            $offset = null;
            foreach ($lines as $i => $line) {
                if (preg_match('/^\s*\[' . preg_quote($targetSection, '/') . ']\s*$/i', $line)) {
                    $offset = $i + 1;
                    break;
                }
            }
            if ($offset !== null) array_splice($lines, $offset, 0, $insert);
            else $lines = array_merge($lines, ['', '[' . $targetSection . ']'], $insert);
        }

        $newContent = rtrim(implode(PHP_EOL, $lines)) . PHP_EOL;
        if (is_file($path)) {
            if (!copy($path, $path . '.backup') || !chmod($path . '.backup', 0600)) {
                throw new RuntimeException('无法安全备份配置文件');
            }
        }
        $temporary = $path . '.install-' . bin2hex(random_bytes(6));
        $oldMask = umask(0077);
        try {
            if (file_put_contents($temporary, $newContent, LOCK_EX) === false
                || !chmod($temporary, 0600)
                || (is_file($path) && (!chown($temporary, fileowner($path)) || !chgrp($temporary, filegroup($path))))
                || !rename($temporary, $path)) {
                throw new RuntimeException('配置文件安全写入失败');
            }
        } finally {
            umask($oldMask);
            if (is_file($temporary)) unlink($temporary);
        }
    }

    private function envValue(string $value): string
    {
        if ($value !== '' && preg_match('/^[A-Za-z0-9_\.\/:@+\-]+$/', $value)) {
            return $value;
        }
        return '"' . str_replace(['\\', '"'], ['\\\\', '\\"'], $value) . '"';
    }

    private function targetWritable(string $path): bool
    {
        return is_file($path) ? is_writable($path) : is_writable(dirname($path));
    }

    private function safeMessage(\Throwable $error, array $secrets): string
    {
        $message = $error->getMessage();
        foreach ($secrets as $secret) {
            if ($secret !== '') {
                $message = str_replace((string)$secret, '******', $message);
            }
        }
        return mb_substr($message, 0, 240);
    }

    private function check(string $label, string $value, bool $ok): array
    {
        return ['label' => $label, 'value' => $value, 'ok' => $ok];
    }
}
