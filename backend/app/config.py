"""Application configuration."""
from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "QA Agent"
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "change-me-in-production-please"
    FERNET_KEY: str = "3v4hFVn4QjQzJ2BcXnE1nKb6S0z5VjH8T9R2uM5aP3g="
    FRONTEND_URL: str = "http://localhost:8080"
    BACKEND_URL: str = "http://localhost:8000"

    # Database
    DATABASE_URL: str = "postgresql+psycopg2://qa:qa@localhost:5432/qa_agent"

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    RESET_TOKEN_EXPIRE_MINUTES: int = 15

    # Admin: comma-separated emails that get admin rights on registration
    ADMIN_EMAILS: str = ""

    @property
    def admin_emails(self) -> List[str]:
        return [e.strip().lower() for e in self.ADMIN_EMAILS.split(",") if e.strip()]

    # Limits
    RATE_LIMIT_USER: str = "10/minute"
    RATE_LIMIT_IP: str = "100/minute"

    # SMTP
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 1025
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "no-reply@qa-agent.local"
    SMTP_TLS: bool = False
    SMTP_SSL: bool = False
    SMTP_BACKUP_HOST: Optional[str] = None
    SMTP_BACKUP_PORT: int = 587
    SMTP_BACKUP_USER: str = ""
    SMTP_BACKUP_PASSWORD: str = ""

    # AI models
    AI_PRIMARY: str = "deepseek"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    GIGACHAT_API_KEY: str = ""
    GIGACHAT_BASE_URL: str = "https://gigachat.devices.sberbank.ru/api/v1"
    GIGACHAT_MODEL: str = "GigaChat"
    YANDEXGPT_API_KEY: str = ""
    YANDEXGPT_FOLDER_ID: str = ""
    YANDEXGPT_BASE_URL: str = "https://llm.api.cloud.yandex.net/foundationModels/v1"
    YANDEXGPT_MODEL: str = "yandexgpt-lite"
    AI_TIMEOUT_SECONDS: int = 30
    AI_RETRIES: int = 3
    VALIDATOR_RULES_VERSION: int = 1

    # Sandbox / containers
    SANDBOX_IMAGE: str = "qa-agent-sandbox:latest"
    SANDBOX_RUNNER: str = "docker"  # docker | local
    # When True, a Docker-sandbox failure falls back to running in-process
    # (no container limits). Disable in production: it lets an authed user run
    # unconstrained browser work directly on the worker.
    SANDBOX_ALLOW_LOCAL_FALLBACK: bool = False
    CELERY_ALWAYS_EAGER: bool = False  # run celery tasks in-process (no Redis needed)
    DEV_AUTO_VERIFY: bool = False  # dev: auto-verify email on register (no SMTP needed)
    SANDBOX_CPU_LIMIT: str = "1.0"
    SANDBOX_MEM_LIMIT: str = "1g"
    SANDBOX_TIMEOUT_SIMPLE_MINUTES: int = 10
    SANDBOX_TIMEOUT_FULL_MINUTES: int = 20
    SANDBOX_NETWORK: str = "qa_agent_net"
    SANDBOX_DNS_WHITELIST: str = ""  # comma separated domains
    ALLOW_PRIVATE_HOSTS: bool = False
    PRIVATE_HOST_WHITELIST: str = ""  # comma separated, only for self-hosted enterprise

    # Limits plan
    TRIAL_DAYS: int = 3
    TRIAL_TESTS_MAX: int = 20
    PRO_TESTS_MONTHLY: int = 100
    PRO_PARALLEL: int = 5
    ENTERPRISE_PARALLEL: int = 20
    ENTERPRISE_TRIAL_DAYS: int = 7
    ENTERPRISE_TRIAL_TESTS: int = 10
    # Global cap on simultaneously queued/running runs across all users, so a
    # busy set of authed users cannot exceed worker concurrency and starve the
    # Docker daemon (0 = unlimited).
    GLOBAL_MAX_RUNS_ACTIVE: int = 0
    REPORT_RETENTION_DAYS: int = 30
    MAX_SCENARIO_WORDS: int = 500
    FULL_PAGE_MAX_ELEMENTS: int = 50

    # Anti-redundancy sampling: identical items (e.g. product cards) beyond
    # SAMPLE_THRESHOLD are collapsed to SAMPLE_COUNT random representatives.
    SAMPLE_THRESHOLD: int = 4
    SAMPLE_COUNT: int = 2

    # Site crawl mode ("site"): BFS over same-host pages.
    CRAWL_MAX_PAGES: int = 100
    CRAWL_DEPTH: int = 5
    CRAWL_TIMEOUT_MINUTES: int = 120
    # Auto-chains: after a click reveals a new form/modal, fill & submit it (max N per run).
    CHAIN_MAX_FORMS: int = 3
    # Max negative validation cases per form in generated plans.
    NEG_MAX_PER_FORM: int = 8

    # Telemetry / analytics / ad domains whose noisy 4xx-5xx responses must not
    # fail otherwise-passing checks. Comma-separated host suffixes. These are
    # aborted in the sandbox so their errors never reach the browser console.
    TELEMETRY_BLOCKLIST: str = (
        "backtrace.io,google-analytics.com,googletagmanager.com,"
        "doubleclick.net,googleadservices.com,connect.facebook.net,"
        "mc.yandex.ru,yastatic.net,adservice.google.com,cdn.cookielaw.org"
    )

    # Payments (YooKassa)
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""
    YOOKASSA_TEST_MODE: bool = True

    # Messenger
    TELEGRAM_BOT_TOKEN: str = ""
    VK_API_TOKEN: str = ""

    # TestRail
    TESTRAIL_URL: str = ""
    TESTRAIL_USER: str = ""
    TESTRAIL_PASSWORD: str = ""
    TESTRAIL_PROJECT_ID: Optional[int] = None

    # Frontend static dir
    FRONTEND_DIR: str = "frontend"

    # Audit
    AUDIT_ENABLED: bool = True

    @property
    def allowed_domains(self) -> List[str]:
        return [d.strip() for d in self.SANDBOX_DNS_WHITELIST.split(",") if d.strip()]

    @property
    def private_whitelist(self) -> List[str]:
        return [d.strip() for d in self.PRIVATE_HOST_WHITELIST.split(",") if d.strip()]

    @property
    def telemetry_blocklist(self) -> List[str]:
        return [d.strip().lower() for d in self.TELEMETRY_BLOCKLIST.split(",") if d.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
