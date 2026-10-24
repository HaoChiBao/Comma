from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://acme:acme@localhost:5432/procurement"
    app_env: str = "development"
    demo_mode: bool = True
    session_secret: str = "dev-only-change-me"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    demo_clock: str = "2026-10-24T15:00:00+00:00"

    photon_mode: str = "mock"
    photon_api_key: str = ""
    photon_project_id: str = ""
    photon_webhook_secret: str = "dev-webhook-secret"
    photon_sender_id: str = "+15555551000"
    photon_base_url: str = ""

    email_mode: str = "sandbox"
    email_webhook_secret: str = "dev-email-secret"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = "procurement@sandbox.acme.test"

    llm_mode: str = "deterministic"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4.1-mini"

    manager_approval_limit: Decimal = Decimal("200000.00")
    quote_freshness_days: int = 14
    approval_ttl_hours: int = 24
    psu_headroom_w: int = 200

    eager_worker: bool = True
    worker_thread: bool = False
    seed_on_start: bool = True
    seed_demo_task: bool = True

    @property
    def live_imessage(self) -> bool:
        return False

    @property
    def live_email(self) -> bool:
        return False

    @property
    def demo_routes_enabled(self) -> bool:
        return self.demo_mode and self.app_env != "production"


settings = Settings()
