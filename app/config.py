"""Central, validated configuration for the Revenue Engine."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SUPPORTED_EMAIL_PROVIDERS = frozenset({"brevo", "sendgrid"})
DEFAULT_LANDING_PAGE_URL = "https://sanchit-agarwal.me/#services"


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value


def _optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    environment: str
    db_path: Path
    database_url: str | None = field(repr=False)
    database_backend: str
    landing_page_url: str
    target_market_country: str
    target_market_language: str
    market_validation_required: bool
    google_api_key: str | None

    email_provider: str
    brevo_api_key: str | None
    brevo_sender: str | None
    brevo_reply_to: str | None
    brevo_webhook_token: str | None

    sendgrid_api_key: str | None
    sendgrid_sender: str | None
    sendgrid_reply_to: str | None

    razorpay_key_id: str | None
    razorpay_key_secret: str | None = field(repr=False)
    razorpay_webhook_secret: str | None
    flask_secret_key: str | None
    owner_dashboard_password: str | None
    owner_notification_email: str | None
    public_app_url: str | None
    hf_token: str | None
    hf_api_url: str
    hf_model: str

    campaign_limit: int
    daily_limit: int
    min_send_delay: int
    max_send_delay: int
    domain_cooldown: int
    max_consecutive_failures: int


def load_settings() -> Settings:
    min_delay = _int_env("MIN_SEND_DELAY", 8)
    max_delay = _int_env("MAX_SEND_DELAY", 20)
    if max_delay < min_delay:
        raise ValueError("MAX_SEND_DELAY must be >= MIN_SEND_DELAY")

    provider = os.getenv("EMAIL_PROVIDER", "brevo").strip().lower()
    if provider not in SUPPORTED_EMAIL_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_EMAIL_PROVIDERS))
        raise ValueError(f"EMAIL_PROVIDER must be one of: {supported}")

    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    if os.getenv("REPL_DEPLOYMENT") == "1":
        environment = "production"
    database_backend = os.getenv("DATABASE_BACKEND", "auto").strip().lower()
    if database_backend not in {"auto", "sqlite", "postgresql"}:
        raise ValueError("DATABASE_BACKEND must be auto, sqlite, or postgresql")
    if environment == "production" and database_backend == "sqlite":
        raise ValueError("SQLite is not allowed as the production database backend")
    database_url = _optional_env("DATABASE_URL")
    if environment == "production" and not database_url:
        raise ValueError("DATABASE_URL is required in production")

    return Settings(
        environment=environment,
        db_path=Path(os.getenv("OUTREACH_DB", "data/revenue_engine.db")),
        database_url=database_url,
        database_backend=database_backend,
        landing_page_url=os.getenv(
            "LANDING_PAGE_URL", DEFAULT_LANDING_PAGE_URL
        ).strip(),
        target_market_country=os.getenv("TARGET_MARKET_COUNTRY", "India").strip(),
        target_market_language=os.getenv("TARGET_MARKET_LANGUAGE", "en").strip().lower(),
        market_validation_required=os.getenv("MARKET_VALIDATION_REQUIRED", "true").strip().lower() in {"1", "true", "yes", "on"},
        google_api_key=_optional_env("GOOGLE_API_KEY"),
        email_provider=provider,
        brevo_api_key=_optional_env("BREVO_API_KEY"),
        brevo_sender=_optional_env("BREVO_SENDER"),
        brevo_reply_to=_optional_env("BREVO_REPLY_TO"),
        brevo_webhook_token=_optional_env("BREVO_WEBHOOK_TOKEN"),
        sendgrid_api_key=_optional_env("SENDGRID_API_KEY"),
        sendgrid_sender=_optional_env("SENDGRID_SENDER"),
        sendgrid_reply_to=_optional_env("SENDGRID_REPLY_TO"),
        razorpay_key_id=_optional_env("RAZORPAY_KEY_ID"),
        razorpay_key_secret=_optional_env("RAZORPAY_KEY_SECRET"),
        razorpay_webhook_secret=_optional_env("RAZORPAY_WEBHOOK_SECRET"),
        flask_secret_key=_optional_env("FLASK_SECRET_KEY"),
        owner_dashboard_password=_optional_env("OWNER_DASHBOARD_PASSWORD"),
        owner_notification_email=_optional_env("OWNER_NOTIFICATION_EMAIL"),
        public_app_url=_optional_env("PUBLIC_APP_URL"),
        hf_token=_optional_env("HF_TOKEN") or _optional_env("HF_API_KEY"),
        hf_api_url=os.getenv("HF_API_URL", "https://router.huggingface.co/v1/chat/completions").strip(),
        hf_model=os.getenv("HF_MODEL", "meta-llama/Llama-3.1-8B-Instruct:fastest").strip(),
        campaign_limit=_int_env("CAMPAIGN_LIMIT", 10),
        daily_limit=_int_env("DAILY_LIMIT", 25),
        min_send_delay=min_delay,
        max_send_delay=max_delay,
        domain_cooldown=_int_env("DOMAIN_COOLDOWN", 60),
        max_consecutive_failures=_int_env("MAX_CONSECUTIVE_FAILURES", 3),
    )


settings = load_settings()
