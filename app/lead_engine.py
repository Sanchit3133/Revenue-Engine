"""Lead normalization and safety checks.

Actual Google Places discovery will be added in the next phase.
This module establishes clean boundaries now.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse


EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"
)

BLOCKED_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "domain.com",
    "yourdomain.com",
    "localhost",
}

BLOCKED_PREFIXES = {
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "mailer-daemon",
    "postmaster",
}


def normalize_email(value: str | None) -> str:
    if not value:
        return ""
    value = unquote(value).strip().lower()
    if value.startswith("mailto:"):
        value = value[7:]
    value = value.split("?", 1)[0].split("#", 1)[0]
    return value.strip(" <>\"'")


def is_valid_public_email(value: str | None) -> bool:
    email = normalize_email(value)
    if not email or len(email) > 254:
        return False
    if not EMAIL_RE.fullmatch(email):
        return False

    local, domain = email.rsplit("@", 1)

    if domain in BLOCKED_DOMAINS:
        return False
    if local in BLOCKED_PREFIXES:
        return False
    if ".." in email:
        return False

    return True


def normalize_website(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    if not value:
        return ""

    parsed = urlparse(value if "://" in value else f"https://{value}")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
