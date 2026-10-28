<?php
declare(strict_types=1);

namespace app\service;

use app\model\Account;
use RuntimeException;
use think\Service;

/**
 * Stores recovery-mailbox credentials encrypted at rest.  The clear-text
 * credential is released only to the internal automation API task payload.
 */
class MailboxCredentialService extends Service
{
    private const CIPHER = 'aes-256-gcm';
    private const PREFIX = 'v1:';

    public function register(): void
    {
        $this->app->bind('mailboxCredentialService', self::class);
    }

    public function mergeInput(array $input, ?Account $existing = null): array
    {
        $existingCipher = $existing ? (string)($existing->mailbox_password_cipher ?? '') : '';
        // Treat omitted fields as "unchanged". Account edits are allowed to be
        // partial (for example changing only a schedule or remark), and must
        // never erase a previously configured rescue mailbox.
        if ($existing) {
            foreach (['recovery_email', 'mailbox_host', 'mailbox_port', 'mailbox_username',
                      'mailbox_tls', 'enable_email_recovery', 'recovery_methods'] as $field) {
                if (!array_key_exists($field, $input)) {
                    $input[$field] = $existing->$field;
                }
            }
        }
        $methods = $input['recovery_methods'] ?? null;
        if ($methods !== null) {
            $methods = is_array($methods) ? $methods : explode(',', (string)$methods);
            $input['enable_email_recovery'] = (int)(in_array('email_password', $methods, true)
                || in_array('email_security', $methods, true));
        }
        return $this->mergeMailboxInput($input, $existingCipher);
    }

    private function mergeMailboxInput(array $input, string $existingCipher): array
    {
        $enabled = !empty($input['enable_email_recovery']);
        $email = strtolower(trim((string)($input['recovery_email'] ?? '')));
        $host = strtolower(trim((string)($input['mailbox_host'] ?? '')));
        $port = (int)($input['mailbox_port'] ?? 993);
        $username = trim((string)($input['mailbox_username'] ?? ''));
        $submittedPassword = (string)($input['mailbox_password'] ?? '');

        $data = [
            'enable_email_recovery' => $enabled ? 1 : 0,
            'recovery_email' => $email,
            'mailbox_host' => $host,
            'mailbox_port' => $port ?: 993,
            'mailbox_username' => $username,
            'mailbox_tls' => empty($input['mailbox_tls']) ? 0 : 1,
            'mailbox_password_cipher' => $existingCipher,
        ];
        if (!$enabled) {
            return ['status' => true, 'data' => $data];
        }
        if (!filter_var($email, FILTER_VALIDATE_EMAIL)) {
            return ['status' => false, 'msg' => '救援邮箱格式无效'];
        }
        if (!$this->validHost($host) || $port < 1 || $port > 65535) {
            return ['status' => false, 'msg' => 'IMAP 主机或端口格式无效'];
        }
        if ($username === '' || strlen($username) > 255) {
            return ['status' => false, 'msg' => 'IMAP 用户名不能为空'];
        }
        if (!$data['mailbox_tls']) {
            return ['status' => false, 'msg' => '救援邮箱必须使用 TLS/IMAPS'];
        }
        if ($submittedPassword !== '') {
            try {
                $data['mailbox_password_cipher'] = $this->encrypt($submittedPassword);
            } catch (RuntimeException $exception) {
                return ['status' => false, 'msg' => $exception->getMessage()];
            }
        }
        if ($data['mailbox_password_cipher'] === '') {
            return ['status' => false, 'msg' => '启用邮件恢复时必须填写 IMAP 密码'];
        }
        return ['status' => true, 'data' => $data];
    }

    public function automationConfig(Account $account): array
    {
        if (empty($account->enable_email_recovery)) {
            return ['configured' => false, 'error_code' => 'email_recovery_disabled'];
        }
        try {
            $password = $this->decrypt((string)($account->mailbox_password_cipher ?? ''));
        } catch (RuntimeException $exception) {
            return ['configured' => false, 'error_code' => 'mailbox_config_invalid'];
        }
        if (!filter_var((string)($account->recovery_email ?? ''), FILTER_VALIDATE_EMAIL)
            || !$this->validHost((string)($account->mailbox_host ?? ''))
            || !$password) {
            return ['configured' => false, 'error_code' => 'mailbox_config_invalid'];
        }
        return [
            'configured' => true,
            'email' => (string)($account->recovery_email ?? ''),
            'host' => (string)($account->mailbox_host ?? ''),
            'port' => (int)($account->mailbox_port ?? 993),
            'username' => (string)($account->mailbox_username ?? ''),
            'password' => $password,
            'tls' => !empty($account->mailbox_tls),
        ];
    }

    /** Validate a form draft and return an in-memory automation config without persisting it. */
    public function transientAutomationConfig(array $input, ?Account $existing = null): array
    {
        $existingCipher = $existing ? (string)($existing->mailbox_password_cipher ?? '') : '';
        $input['enable_email_recovery'] = 1;
        $merged = $this->mergeMailboxInput($input, $existingCipher);
        if (empty($merged['status'])) return $merged;
        try {
            $password = $this->decrypt((string)$merged['data']['mailbox_password_cipher']);
        } catch (RuntimeException $exception) {
            return ['status' => false, 'msg' => $exception->getMessage()];
        }
        return ['status' => true, 'data' => [
            'configured' => true,
            'email' => (string)$merged['data']['recovery_email'],
            'host' => (string)$merged['data']['mailbox_host'],
            'port' => (int)$merged['data']['mailbox_port'],
            'username' => (string)$merged['data']['mailbox_username'],
            'password' => $password,
            'tls' => !empty($merged['data']['mailbox_tls']),
        ]];
    }

    public function decryptMailboxPassword(Account $account): string
    {
        $cipher = (string)($account->mailbox_password_cipher ?? '');
        if ($cipher === '') return '';
        try { return $this->decrypt($cipher); } catch (RuntimeException) { return ''; }
    }

    public function keyConfigured(): bool
    {
        try {
            $this->key();
            return true;
        } catch (RuntimeException) {
            return false;
        }
    }

    private function encrypt(string $plaintext): string
    {
        return $this->encryptWithKey($plaintext, $this->key());
    }

    public function rewrap(string $value, string $newBase64Key): string
    {
        if ($value === '') return '';
        $newKey = base64_decode(trim($newBase64Key), true);
        if ($newKey === false || strlen($newKey) !== 32) throw new RuntimeException('新的救援邮箱密钥无效');
        return $this->encryptWithKey($this->decrypt($value), $newKey);
    }

    private function encryptWithKey(string $plaintext, string $key): string
    {
        $iv = random_bytes(12);
        $tag = '';
        $ciphertext = openssl_encrypt($plaintext, self::CIPHER, $key, OPENSSL_RAW_DATA, $iv, $tag, '', 16);
        if (!is_string($ciphertext) || strlen($tag) !== 16) {
            throw new RuntimeException('救援邮箱凭据加密失败');
        }
        return self::PREFIX . base64_encode($iv . $tag . $ciphertext);
    }

    private function decrypt(string $value): string
    {
        if (!str_starts_with($value, self::PREFIX)) {
            throw new RuntimeException('救援邮箱凭据格式无效');
        }
        $payload = base64_decode(substr($value, strlen(self::PREFIX)), true);
        if ($payload === false || strlen($payload) < 29) {
            throw new RuntimeException('救援邮箱凭据格式无效');
        }
        $plaintext = openssl_decrypt(
            substr($payload, 28), self::CIPHER, $this->key(), OPENSSL_RAW_DATA,
            substr($payload, 0, 12), substr($payload, 12, 16), ''
        );
        if (!is_string($plaintext) || $plaintext === '') {
            throw new RuntimeException('救援邮箱凭据无法解密');
        }
        return $plaintext;
    }

    private function key(): string
    {
        $raw = base64_decode(trim((string)env('mailbox.cipher_key', '')), true);
        if ($raw === false || strlen($raw) !== 32) {
            throw new RuntimeException('未配置有效的 MAILBOX.CIPHER_KEY（需为 32 字节 Base64 密钥）');
        }
        return $raw;
    }

    private function validHost(string $host): bool
    {
        if (!(bool)preg_match('/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$/i', $host)
            && filter_var($host, FILTER_VALIDATE_IP) === false) return false;
        try { $this->app->networkAddressPolicy->assertPublicHost($host); return true; }
        catch (\InvalidArgumentException) { return false; }
    }
}
