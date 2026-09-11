"""Central configuration for the Revenue Engine."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


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


@dataclass(frozen=True)
class Settings:
    environment: str
    db_path: Path
    landing_page_url: str

    google_api_key: str | None

    sendgrid_api_key: str | None
    sendgrid_sender: str | None
    sendgrid_reply_to: str | None

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

    return Settings(
        environment=os.getenv("ENVIRONMENT", "development").strip().lower(),
        db_path=Path(os.getenv("OUTREACH_DB", "data/revenue_engine.db")),
        landing_page_url=os.getenv(
            "LANDING_PAGE_URL",
            "https://sanchit-agarwal.me/257-2/",
        ).strip(),
        google_api_key=os.getenv("GOOGLE_API_KEY") or None,
        sendgrid_api_key=os.getenv("SENDGRID_API_KEY") or None,
        sendgrid_sender=os.getenv("SENDGRID_SENDER") or None,
        sendgrid_reply_to=os.getenv("SENDGRID_REPLY_TO") or None,
        campaign_limit=_int_env("CAMPAIGN_LIMIT", 10),
        daily_limit=_int_env("DAILY_LIMIT", 25),
        min_send_delay=min_delay,
        max_send_delay=max_delay,
        domain_cooldown=_int_env("DOMAIN_COOLDOWN", 60),
        max_consecutive_failures=_int_env("MAX_CONSECUTIVE_FAILURES", 3),
    )


settings = load_settings()
