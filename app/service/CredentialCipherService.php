<?php
declare(strict_types=1);

namespace app\service;

use RuntimeException;
use think\Service;

/** Authenticated field-level encryption for credentials stored by the panel. */
class CredentialCipherService extends Service
{
    private const PREFIX = 'enc:v1:';
    private const CIPHER = 'aes-256-gcm';

    public function register(): void
    {
        $this->app->bind('credentialCipherService', self::class);
    }

    public function encrypt(string $plaintext, string $context): string
    {
        if ($plaintext === '' || $this->isEncrypted($plaintext)) return $plaintext;
        $iv = random_bytes(12);
        $tag = '';
        $ciphertext = openssl_encrypt(
            $plaintext, self::CIPHER, $this->key(), OPENSSL_RAW_DATA, $iv, $tag, $context, 16
        );
        if (!is_string($ciphertext) || strlen($tag) !== 16) {
            throw new RuntimeException('敏感凭据加密失败');
        }
        return self::PREFIX . base64_encode($iv . $tag . $ciphertext);
    }

    /** Legacy clear text is returned during the rolling migration window. */
    public function decrypt(string $value, string $context): string
    {
        if ($value === '' || !$this->isEncrypted($value)) return $value;
        $payload = base64_decode(substr($value, strlen(self::PREFIX)), true);
        if ($payload === false || strlen($payload) < 29) {
            throw new RuntimeException('敏感凭据密文格式无效');
        }
        $plaintext = openssl_decrypt(
            substr($payload, 28), self::CIPHER, $this->key(), OPENSSL_RAW_DATA,
            substr($payload, 0, 12), substr($payload, 12, 16), $context
        );
        if (!is_string($plaintext)) throw new RuntimeException('敏感凭据无法解密');
        return $plaintext;
    }

    public function isEncrypted(string $value): bool
    {
        return str_starts_with($value, self::PREFIX);
    }

    public function fingerprint(string $plaintext, string $context): string
    {
        return hash_hmac('sha256', $context . "\0" . $plaintext, $this->key());
    }

    public function keyConfigured(): bool
    {
        try { $this->key(); return true; } catch (RuntimeException) { return false; }
    }

    private function key(): string
    {
        $raw = base64_decode(trim((string)env('credentials.cipher_key', '')), true);
        if ($raw === false || strlen($raw) !== 32) {
            throw new RuntimeException('未配置有效的 CREDENTIALS.CIPHER_KEY（需为 32 字节 Base64 密钥）');
        }
        return $raw;
    }
}
