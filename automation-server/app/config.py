import os
from dataclasses import dataclass


def _technical_retry_delays() -> tuple[int, ...]:
    values = []
    for item in os.getenv("TECHNICAL_RETRY_DELAYS", "30,120,300").split(","):
        try:
            values.append(max(int(item.strip()), 1))
        except ValueError:
            continue
    return tuple(values) or (30, 120, 300)


@dataclass(frozen=True)
class Settings:
    token: str = os.getenv("AUTOMATION_TOKEN", "change-me")
    panel_url: str = os.getenv("PANEL_URL", "http://host.docker.internal")
    panel_api_key: str = os.getenv("PANEL_API_KEY", "123456")
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    database_path: str = os.getenv("DATABASE_PATH", "data/tasks.sqlite3")
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))
    technical_retry_delays: tuple[int, ...] = _technical_retry_delays()
    request_timeout: float = float(os.getenv("REQUEST_TIMEOUT", "10"))
    poll_interval: float = float(os.getenv("POLL_INTERVAL", "2"))
    account_driver_url: str = os.getenv("ACCOUNT_DRIVER_URL", "fixture://local")
    account_driver_token: str = os.getenv("ACCOUNT_DRIVER_TOKEN", "")
    account_driver_poll_interval: float = max(float(os.getenv("ACCOUNT_DRIVER_POLL_INTERVAL", "2")), 0.2)
    account_driver_job_timeout: float = max(float(os.getenv("ACCOUNT_DRIVER_JOB_TIMEOUT", "300")), 10)
    account_driver_queue_timeout: float = max(float(os.getenv("ACCOUNT_DRIVER_QUEUE_TIMEOUT", "1800")), 30)
    worker_concurrency: int = max(int(os.getenv("WORKER_CONCURRENCY", "3")), 1)
    account_lock_ttl: int = max(int(os.getenv("ACCOUNT_LOCK_TTL", "600")), 30)
    proxy_health_concurrency: int = max(int(os.getenv("PROXY_HEALTH_CONCURRENCY", "10")), 1)
    proxy_retry_seconds: int = max(int(os.getenv("PROXY_RETRY_SECONDS", "300")), 60)
    proxy_check_url: str = os.getenv("PROXY_CHECK_URL", "https://api.ipify.org?format=json")
    proxy_target_url: str = os.getenv("PROXY_TARGET_URL", "https://account.apple.com/")
    proxy_loop_interval: int = max(int(os.getenv("PROXY_LOOP_INTERVAL", "60")), 10)
    proxy_source_max_bytes: int = max(int(os.getenv("PROXY_SOURCE_MAX_BYTES", "1048576")), 1024)
    proxy_source_max_endpoints: int = max(int(os.getenv("PROXY_SOURCE_MAX_ENDPOINTS", "500")), 1)
    allow_private_proxy_source: bool = os.getenv("ALLOW_PRIVATE_PROXY_SOURCE", "false").lower() in {"1", "true", "yes"}
    telegram_notify_timeout: float = max(float(os.getenv("TELEGRAM_NOTIFY_TIMEOUT", "8")), 1)
    telegram_poll_timeout: int = max(min(int(os.getenv("TELEGRAM_POLL_TIMEOUT", "25")), 50), 1)
    captcha_human_timeout: int = max(int(os.getenv("CAPTCHA_HUMAN_TIMEOUT", "180")), 10)
    captcha_human_attempts: int = max(min(int(os.getenv("CAPTCHA_HUMAN_ATTEMPTS", "3")), 5), 1)
    workflow_v2_enabled: bool = os.getenv("WORKFLOW_V2_ENABLED", "false").lower() in {"1", "true", "yes"}
    workflow_v2_percent: int = max(min(int(os.getenv("WORKFLOW_V2_PERCENT", "10")), 100), 0)
    schedule_jitter_seconds: int = max(min(int(os.getenv("SCHEDULE_JITTER_SECONDS", "180")), 900), 0)
    authorization_timeout: float = max(float(os.getenv("WORKFLOW_AUTHORIZATION_TIMEOUT", "60")), 10)
    reconciliation_cooldown_seconds: int = max(int(os.getenv("RECONCILIATION_COOLDOWN_SECONDS", "900")), 60)


settings = Settings()
