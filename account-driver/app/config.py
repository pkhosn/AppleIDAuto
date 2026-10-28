import os
from dataclasses import dataclass


def _password_prefix() -> str:
    value = os.getenv("PASSWORD_PREFIX", "Aa1@")
    if len(value) > 12 or any(character.isspace() or ord(character) > 127 for character in value):
        raise ValueError("PASSWORD_PREFIX 必须是 0-12 个非空白 ASCII 字符")
    return value


def _ui_locale() -> str:
    value = os.getenv("APPLE_UI_LOCALE", "zh-CN").strip()
    if not value or len(value) > 20 or any(character.isspace() for character in value):
        raise ValueError("APPLE_UI_LOCALE 必须是有效的浏览器语言标识")
    return value


@dataclass(frozen=True)
class Settings:
    token: str = os.getenv("ACCOUNT_DRIVER_TOKEN", "change-me")
    selenium_url: str = os.getenv("SELENIUM_URL", "http://selenium:4444/wd/hub")
    concurrency: int = max(int(os.getenv("DRIVER_CONCURRENCY", "1")), 1)
    job_ttl: int = max(int(os.getenv("DRIVER_JOB_TTL", "3600")), 60)
    action_timeout: int = max(int(os.getenv("DRIVER_ACTION_TIMEOUT", "300")), 30)
    page_load_timeout: int = max(int(os.getenv("DRIVER_PAGE_LOAD_TIMEOUT", "45")), 10)
    command_timeout: int = max(int(os.getenv("DRIVER_COMMAND_TIMEOUT", "35")), 10)
    render_timeout: int = max(int(os.getenv("DRIVER_RENDER_TIMEOUT", "30")), 5)
    post_login_timeout: int = max(int(os.getenv("DRIVER_POST_LOGIN_TIMEOUT", "45")), 10)
    navigation_attempts: int = max(min(int(os.getenv("DRIVER_NAVIGATION_ATTEMPTS", "2")), 2), 1)
    captcha_retries: int = max(int(os.getenv("CAPTCHA_RETRIES", "5")), 1)
    captcha_human_timeout: int = max(int(os.getenv("CAPTCHA_HUMAN_TIMEOUT", "180")), 10)
    captcha_human_attempts: int = max(min(int(os.getenv("CAPTCHA_HUMAN_ATTEMPTS", "3")), 5), 1)
    artifact_dir: str = os.getenv("ARTIFACT_DIR", "data/artifacts")
    artifact_days: int = max(int(os.getenv("ARTIFACT_DAYS", "5")), 1)
    password_prefix: str = _password_prefix()
    ui_locale: str = _ui_locale()
    sign_in_url: str = os.getenv("ACCOUNT_SIGN_IN_URL", "https://account.apple.com/sign-in")
    account_url: str = os.getenv("ACCOUNT_URL", "https://account.apple.com/")
    recovery_url: str = os.getenv(
        "ACCOUNT_RECOVERY_URL", "https://iforgot.apple.com/password/verify/appleid"
    )
    proxy_bridge_host: str = os.getenv("PROXY_BRIDGE_HOST", "account-driver")
    proxy_bridge_port_start: int = int(os.getenv("PROXY_BRIDGE_PORT_START", "18080"))


settings = Settings()
