"""Bounded lead discovery preview for GitHub Actions.

This module only reads the production database and public web pages. It never
calls init_db(), persists leads, creates outreach records, or sends email.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO

from app.config import settings
from app.database import connect
from app.market_strategy import get_market
from scripts.discover_leads import (
    existing_emails,
    existing_place_ids,
    places_text_search,
    process_place,
)
from app.lead_engine import normalize_email


def preview(city: str, country: str, query: str, max_places: int = 5) -> dict:
    if not settings.google_api_key:
        raise RuntimeError("GOOGLE_API_KEY is not configured")
    if not 1 <= max_places <= 5:
        raise ValueError("max_places must be between 1 and 5")
    market = get_market(country)
    location = f"{city.strip()}, {market.country}"
    hidden_output = StringIO()

    # The imported helper prints contact details; suppress them because GitHub
    # Actions logs may be public. Keep only aggregate counts in the output.
    with redirect_stdout(hidden_output):
        places = places_text_search(
            api_key=settings.google_api_key,
            query=query.strip(),
            city=location,
            pages=1,
        )
        places = places[:max_places]
        seen_ids: set[str] = set()
        seen_emails: set[str] = set()
        candidates = []

        with connect() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            known_ids = existing_place_ids(conn)
            known_emails = existing_emails(conn)
            for place in places:
                place_id = place.get("place_id")
                if not place_id or place_id in seen_ids:
                    continue
                seen_ids.add(place_id)
                lead = process_place(
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
    selected = candidates[:5]
    return {
        "country": market.country,
        "places_returned": len(places),
        "eligible_candidates": len(selected),
        "priority_a": sum(item.get("priority") == "A" for item in selected),
        "priority_b": sum(item.get("priority") == "B" for item in selected),
        "priority_c": sum(item.get("priority") == "C" for item in selected),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only, bounded lead discovery preview")
    parser.add_argument("--city", required=True)
    parser.add_argument("--country", default="India")
    parser.add_argument("--query", required=True)
    parser.add_argument("--max-places", type=int, default=5)
    args = parser.parse_args()
    try:
        result = preview(args.city, args.country, args.query, args.max_places)
    except Exception as exc:
        # Do not print raw exceptions; they may reveal a provider URL or settings.
        print(f"Discovery preview failed: {type(exc).__name__}")
        raise SystemExit(1) from None

    print("REVENUE ENGINE — GITHUB DISCOVERY DRY RUN")
    print("Mode: read-only. No leads saved. No email sent.")
    print(f"Country: {result['country']}")
    print(f"Places inspected (maximum 5): {result['places_returned']}")
    print(f"Eligible candidates: {result['eligible_candidates']}")
    print(
        f"Priority buckets: A={result['priority_a']}, "
        f"B={result['priority_b']}, C={result['priority_c']}"
    )


if __name__ == "__main__":
    main()
