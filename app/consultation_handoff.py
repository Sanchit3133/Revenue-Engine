"""Opaque, expiring consultation links for known prospects."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from urllib.parse import urlencode, urlparse


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def new_handoff_material(public_app_url: str | None, *, ttl_days: int = 21) -> dict | None:
    """Create link material; caller inserts the hash within the outreach transaction."""
    base = (public_app_url or "").strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    if parsed.query or parsed.fragment:
        return None
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = _utc_iso(datetime.now(timezone.utc) + timedelta(days=max(1, int(ttl_days))))
    url = base + "/consultation?" + urlencode({"h": token})
    return {
        "token": token,
        "token_hash": token_hash,
        "expires_at": expires_at,
        "url": url,
    }


def resolve_handoff(conn, raw_token: str | None) -> dict | None:
    """Resolve an unexpired, unused token without exposing its database hash to the URL."""
    token = (raw_token or "").strip()
    if len(token) < 32 or len(token) > 200:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = conn.execute(
        """SELECT h.token_hash, h.lead_id, h.expires_at, h.used_at,
                  l.business_name, l.email, l.website
           FROM consultation_handoff_tokens h
           JOIN leads l ON l.id = h.lead_id
           WHERE h.token_hash = ?
           LIMIT 1""",
        (token_hash,),
    ).fetchone()
    if row is None or row["used_at"] is not None:
        return None
    try:
        expiry = datetime.fromisoformat(str(row["expires_at"]))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    if expiry <= datetime.now(timezone.utc):
        return None
    return {
        "lead_id": int(row["lead_id"]),
        "business_name": str(row["business_name"] or ""),
        "email": str(row["email"] or "").strip().lower(),
        "website": str(row["website"] or ""),
        "token_hash": token_hash,
    }
