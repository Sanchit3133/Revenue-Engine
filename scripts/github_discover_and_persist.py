"""Bounded, manual GitHub lead discovery that persists candidates without outreach.

No schema initialization or migration occurs here. This job is separate from
email preparation/delivery, examines at most five Places results, and uses a
restricted PostgreSQL worker credential. Public Action logs contain counts only.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
from typing import Any
from urllib.parse import urljoin, urlparse
from unittest.mock import patch

from app.config import settings
from app.database import connect
from app.lead_engine import is_valid_public_email, normalize_email, normalize_website
from app.market_strategy import get_market
from app.models import LeadState
from app.pipeline import transition
from scripts import discover_leads as discovery

REQUIRED_TABLES = {"leads", "suppressions", "state_events"}


def _verify_required_tables(conn: Any) -> None:
    rows = conn.execute(
        """SELECT table_name FROM information_schema.tables
           WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"""
    ).fetchall()
    present = {row[0] for row in rows}
    missing = sorted(REQUIRED_TABLES - present)
    if missing:
        raise RuntimeError("Required worker tables are missing: " + ", ".join(missing))


def _bounded_find_public_email(website: str) -> tuple[str, str]:
    """Inspect only a homepage and two conventional contact pages per business."""
    normalized = normalize_website(website)
    if not normalized:
        return "", ""
    parsed = urlparse(normalized)
    root = f"{parsed.scheme}://{parsed.netloc}/"
    pages = [
        (normalized, "/"),
        (urljoin(root, "contact"), "/contact"),
        (urljoin(root, "contact-us"), "/contact-us"),
    ]
    for url, label in pages:
        html = discovery.fetch_page(url)
        if not html:
            continue
        emails = discovery.extract_emails_from_html(html)
        if emails:
            return emails[0], f"public_email_found:{label}"
    return "", ""


def persist_candidates(conn: Any, candidates: list[dict[str, Any]], country: str) -> dict[str, int]:
    """Persist up to five screened candidates; never create an outreach row."""
    counts = {"persisted": 0, "duplicate": 0, "suppressed": 0, "invalid": 0}
    for lead in candidates[:5]:
        email = normalize_email(lead.get("email", ""))
        place_id = str(lead.get("place_id") or "").strip()
        if not place_id or not email or not is_valid_public_email(email):
            counts["invalid"] += 1
            continue

        suppressed = conn.execute(
            "SELECT 1 FROM suppressions WHERE lower(email)=lower(?) LIMIT 1",
            (email,),
        ).fetchone()
        if suppressed is not None:
            counts["suppressed"] += 1
            continue

        duplicate = conn.execute(
            """SELECT 1 FROM leads
               WHERE place_id = ? OR lower(email) = lower(?)
               LIMIT 1""",
            (place_id, email),
        ).fetchone()
        if duplicate is not None:
            counts["duplicate"] += 1
            continue

        cursor = conn.execute(
            """INSERT INTO leads (
                   place_id, business_name, address, phone, website, email,
                   country, qualification, score, signal, notes, state
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(place_id) DO NOTHING
               RETURNING id""",
            (
                place_id,
                (lead.get("business_name") or "").strip()[:200],
                lead.get("address") or "",
                lead.get("phone") or "",
                lead.get("website") or "",
                email,
                country,
                lead.get("qualification") or "",
                int(lead.get("score") or 0),
                lead.get("signal") or "",
                lead.get("notes") or "",
                LeadState.DISCOVERED.value,
            ),
        )
        inserted = cursor.fetchone()
        if inserted is None:
            counts["duplicate"] += 1
            continue

        lead_id = int(inserted[0])
        # transition() writes a state_events audit entry and commits this lead.
        transition(
            conn,
            lead_id,
            LeadState.QUALIFIED,
            reason=(
                "GitHub bounded discovery worker "
                f"(score={int(lead.get('score') or 0)}, "
                f"qualification={lead.get('qualification') or 'unknown'})"
            ),
        )
        counts["persisted"] += 1
    return counts


def discover_candidates(
    city: str,
    country: str,
    query: str,
    max_places: int = 5,
) -> tuple[str, list[dict[str, Any]]]:
    """Fetch and qualify up to five candidates using read-only database queries."""
    if not settings.google_api_key:
        raise RuntimeError("GOOGLE_API_KEY is not configured")
    if not city.strip() or len(city) > 100:
        raise ValueError("City must be between 1 and 100 characters")
    if not query.strip() or len(query) > 120:
        raise ValueError("Query must be between 1 and 120 characters")
    if not 1 <= max_places <= 5:
        raise ValueError("max_places must be between 1 and 5")

    market = get_market(country)
    location = f"{city.strip()}, {market.country}"
    hidden_output = StringIO()

    with redirect_stdout(hidden_output):
        places = discovery.places_text_search(
            api_key=settings.google_api_key,
            query=query.strip(),
            city=location,
            pages=1,
        )[:max_places]
        seen_ids: set[str] = set()
        seen_emails: set[str] = set()
        candidates: list[dict[str, Any]] = []

        with connect() as conn:
            # Start the transaction read-only: this connection only checks
            # duplicate/suppression state while public websites are inspected.
            conn.execute("SET TRANSACTION READ ONLY")
            _verify_required_tables(conn)
            known_ids = discovery.existing_place_ids(conn)
            known_emails = discovery.existing_emails(conn)

            with patch.object(discovery, "find_public_email", _bounded_find_public_email):
                for place in places:
                    place_id = place.get("place_id")
                    if not place_id or place_id in seen_ids:
                        continue
                    seen_ids.add(place_id)
                    lead = discovery.process_place(
                        api_key=settings.google_api_key,
                        query=query.strip(),
                        place=place,
                        conn=conn,
                        known_place_ids=known_ids,
                        known_emails=known_emails,
                    )
                    if not lead:
                        continue
                    email = normalize_email(lead.get("email", ""))
                    if email and email not in seen_emails:
                        seen_emails.add(email)
                        candidates.append(lead)

    rank = {"A": 0, "B": 1, "C": 2}
    candidates.sort(
        key=lambda item: (
            rank.get(item.get("priority", "C"), 3),
            -int(item.get("priority_score", 0)),
            -int(item.get("score", 0)),
        )
    )
    return market.country, candidates[:5]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manually discover and persist up to five qualified leads; never sends email."
    )
    parser.add_argument("--city", required=True)
    parser.add_argument("--country", default="India")
    parser.add_argument("--query", required=True)
    parser.add_argument("--max-places", type=int, default=5)
    args = parser.parse_args()

    try:
        country, candidates = discover_candidates(
            args.city, args.country, args.query, args.max_places
        )
        if not candidates:
            print("REVENUE ENGINE — PERSIST DISCOVERY")
            print("Eligible candidates: 0. No database writes were needed.")
            return

        with connect() as conn:
            # A separate short write transaction; this must use a restricted
            # worker role, not the production owner credential.
            conn.execute("SET TRANSACTION READ WRITE")
            _verify_required_tables(conn)
            result = persist_candidates(conn, candidates, country)

    except Exception as exc:
        # Do not print driver/network exceptions: they can contain sensitive details.
        print(f"Discovery persistence failed: {type(exc).__name__}")
        raise SystemExit(1) from None

    print("REVENUE ENGINE — GITHUB PERSIST DISCOVERY")
    print("Mode: persisted leads only. No outreach records or emails were created.")
    print(f"Country: {country}")
    print(f"Qualified candidates inspected: {len(candidates)} (maximum 5)")
    print(f"New qualified leads saved: {result['persisted']}")
    print(f"Duplicates skipped: {result['duplicate']}")
    print(f"Suppressed emails skipped: {result['suppressed']}")
    print(f"Invalid candidates skipped: {result['invalid']}")


if __name__ == "__main__":
    main()
