<?php
declare (strict_types=1);

namespace app\service;

use Exception;
use think\Service;

class BackendService extends Service
{
    public function register(): void
    {
        $this->app->bind('backendService', BackendService::class);
    }

    public function addTask($id): array
    {
        $backendUrl = env('backend.api_url');
        $backendToken = env('backend.token');
        return $this->setCurl($backendUrl, $backendToken, "addTask", $id);
    }

    public function setCurl(string $backendUrl, string $backendToken, $operation, $id, array $extra = []): array
    {
        // Independent scheduling is served by the current automation API even
        // when the retired legacy backend switch is off. Returning success
        // here used to make Restart look successful without sending a request
        // or placing the account in the queue.
        $independent = AutomationScheduleConfig::globals()['active'];
        if (!env('backend.enable_api') && !$independent) {
            return ["status" => true, "msg" => "后端接口未启用"];
        }
        if (trim($backendUrl) === '' || trim($backendToken) === '') {
            return ["status" => false, "msg" => "自动化后端配置不完整"];
        }
        $ch = curl_init();
        curl_setopt_array($ch, [
            CURLOPT_URL => rtrim($backendUrl, '/') . "/$operation",
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_POST => true,
            CURLOPT_HTTPHEADER => ["token: $backendToken", 'Content-Type: application/json'],
            CURLOPT_POSTFIELDS => json_encode(array_merge(['id' => (int)$id], $extra)),
            CURLOPT_CONNECTTIMEOUT => (int)env('backend.connect_timeout', 3),
            CURLOPT_TIMEOUT => (int)env('backend.read_timeout', 10),
        ]);
        $result = curl_exec($ch);
        $error = curl_error($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
        if ($result === false || $error || $httpCode < 200 || $httpCode >= 300)
            return ["status" => false, "msg" => "后端接口异常"];
        else {
            try {
                $result = json_decode($result, true);
                if (!is_array($result) || !array_key_exists('status', $result))
                    return ["status" => false, "msg" => "后端响应格式错误"];
                if (!$result["status"])
                    return ["status" => false, "msg" => $result["msg"] ?? "后端拒绝任务"];
                else
                    return [
                        "status" => true,
                        "msg" => $result["msg"] ?? "任务已提交",
                        "data" => is_array($result["data"] ?? null) ? $result["data"] : [],
                    ];
            } catch (Exception $e) {
                return ["status" => false, "msg" => "后端接口异常"];
            }
        }
    }

    public function restartTask($id, array $options = []): array
    {
        $backendUrl = env('backend.api_url');
        $backendToken = env('backend.token');
        return $this->setCurl($backendUrl, $backendToken, "restartTask", $id, $options);
    }

    public function removeTask($id): array
    {
        $backendUrl = env('backend.api_url');
        $backendToken = env('backend.token');
        return $this->setCurl($backendUrl, $backendToken, "removeTask", $id);
    }

    public function startEmailPasswordRecovery(int $id, bool $resumeExistingLink = false): array
    {
        return $this->setCurl((string)env('backend.api_url'), (string)env('backend.token'),
            'startEmailPasswordRecovery', $id, [
                'confirmation' => 'ONE_TIME_APPLE_EMAIL_PASSWORD_RECOVERY',
                'resume_existing_link' => $resumeExistingLink ? 'true' : 'false',
            ]);
    }

    public function startSecurityRollback(int $id): array
    {
        return $this->setCurl((string)env('backend.api_url'), (string)env('backend.token'),
            'startSecurityRollback', $id, ['confirmation' => 'ONE_TIME_APPLE_SECURITY_ROLLBACK']);
    }

    public function testMailboxConnection(int $id): array
    {
        return $this->setCurl((string)env('backend.api_url'), (string)env('backend.token'), 'testMailbox', $id);
    }

    public function testMailboxConfig(array $config): array
    {
        return $this->setCurl((string)env('backend.api_url'), (string)env('backend.token'),
            'testMailboxConfig', 0, $config);
    }

    public function testProxyConnection(int $id): array
    {
        if (!env('backend.enable_api')) return ['status' => false, 'msg' => '自动化后端未启用'];
        $ch = curl_init(rtrim((string)env('backend.api_url'), '/') . '/testProxy');
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true, CURLOPT_POST => true,
            CURLOPT_HTTPHEADER => ['token: ' . (string)env('backend.token'), 'Content-Type: application/json'],
            CURLOPT_POSTFIELDS => json_encode(['id' => $id]), CURLOPT_CONNECTTIMEOUT => 3, CURLOPT_TIMEOUT => 25,
        ]);
        $body = curl_exec($ch); $error = curl_error($ch); $code = curl_getinfo($ch, CURLINFO_HTTP_CODE); curl_close($ch);
        if ($error || $code < 200 || $code >= 300 || !is_string($body)) return ['status' => false, 'msg' => '静态 IP 测试服务不可用'];
        $result = json_decode($body, true);
        if (!is_array($result) || !array_key_exists('status', $result)) return ['status' => false, 'msg' => '静态 IP 测试响应无效'];
        return ['status' => (bool)$result['status'], 'msg' => (string)($result['msg'] ?? '测试完成'),
            'data' => is_array($result['data'] ?? null) ? $result['data'] : []];
    }

    public function manualRecoveryStatus(string $runId): array
    {
        if (!preg_match('/^[a-f0-9]{32}$/', $runId)) {
            return ['status' => false, 'msg' => '恢复任务标识无效'];
        }
        if (!env('backend.enable_api')) {
            return ['status' => false, 'msg' => '自动化后端未启用'];
        }
        $ch = curl_init(rtrim((string)env('backend.api_url'), '/') . '/manualRecovery/' . rawurlencode($runId));
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => ['token: ' . (string)env('backend.token')],
            CURLOPT_CONNECTTIMEOUT => (int)env('backend.connect_timeout', 3),
            CURLOPT_TIMEOUT => (int)env('backend.read_timeout', 10),
        ]);
        $body = curl_exec($ch);
        $error = curl_error($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
        if ($error || $httpCode !== 200 || !is_string($body)) {
            return ['status' => false, 'msg' => '无法读取恢复任务状态'];
        }
        $result = json_decode($body, true);
        if (!is_array($result) || empty($result['status']) || !is_array($result['data'] ?? null)) {
            return ['status' => false, 'msg' => '恢复任务状态无效'];
        }
        return ['status' => true, 'msg' => '获取成功', 'data' => $result['data']];
    }

    public function manualRecoveryHistory(int $accountId): array
    {
        if ($accountId < 1 || !env('backend.enable_api')) {
            return ['status' => false, 'msg' => '自动化后端未启用', 'data' => []];
        }
        $ch = curl_init(rtrim((string)env('backend.api_url'), '/') . '/manualRecoveryHistory/' . $accountId);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => ['token: ' . (string)env('backend.token')],
            CURLOPT_CONNECTTIMEOUT => (int)env('backend.connect_timeout', 3),
            CURLOPT_TIMEOUT => (int)env('backend.read_timeout', 10),
        ]);
        $body = curl_exec($ch);
        $error = curl_error($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
        if ($error || $httpCode !== 200 || !is_string($body)) {
            return ['status' => false, 'msg' => '无法读取恢复历史', 'data' => []];
        }
        $result = json_decode($body, true);
        if (!is_array($result) || empty($result['status']) || !is_array($result['data'] ?? null)) {
            return ['status' => false, 'msg' => '恢复历史格式无效', 'data' => []];
        }
        return ['status' => true, 'msg' => '获取成功', 'data' => $result['data']];
    }

    public function healthCheck(): array
    {
        if (!env('backend.enable_api')) {
            return ['status' => false, 'msg' => '后端API未启用'];
        }
        $backendUrl = env('backend.api_url');
        $backendToken = env('backend.token');
        $ch = curl_init(rtrim($backendUrl, '/') . '/health');
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => ["token: $backendToken"],
            CURLOPT_CONNECTTIMEOUT => (int)env('backend.connect_timeout', 3),
            CURLOPT_TIMEOUT => (int)env('backend.read_timeout', 10),
        ]);
        $body = curl_exec($ch);
        $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $error = curl_error($ch);
        curl_close($ch);
        if ($error || $code !== 200 || !$body) {
            return ['status' => false, 'msg' => '后端健康检查失败'];
        }
        $result = json_decode($body, true);
        return is_array($result) && !empty($result['status'])
            ? ['status' => true, 'msg' => $result['msg'] ?? 'ok']
            : ['status' => false, 'msg' => $result['msg'] ?? '后端状态异常'];
    }
}
