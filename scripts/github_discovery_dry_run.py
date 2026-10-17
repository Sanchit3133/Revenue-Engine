"""Bounded lead discovery preview for GitHub Actions.

This command only reads the production database and public business pages. It never
calls init_db(), persists leads, creates outreach records, or sends email.
Logs contain aggregate counts only; individual contact details are suppressed.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
from urllib.parse import urljoin, urlparse
from unittest.mock import patch

from app.config import settings
from app.database import connect
from app.lead_engine import normalize_email, normalize_website
from app.market_strategy import get_market
from scripts import discover_leads as discovery


def _bounded_find_public_email(website: str) -> tuple[str, str]:
    """Inspect only the homepage and two conventional contact pages."""
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


def preview(city: str, country: str, query: str, max_places: int = 5) -> dict:
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

    # Suppress the imported discovery helper's stdout to prevent personal
    # contact details from appearing in Actions logs for this public repository.
    with redirect_stdout(hidden_output):
        places = discovery.places_text_search(
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
            # This transaction must remain read-only. No init_db() call is made.
            conn.execute("SET TRANSACTION READ ONLY")
            known_ids = discovery.existing_place_ids(conn)
            known_emails = discovery.existing_emails(conn)
            # Override the standard crawler with the bounded, 3-page version.
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
    selected = candidates[:5]
    return {
        "country": market.country,
        "places_inspected": len(places),
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
        # Never print exception text; it may expose connection/provider details.
        print(f"Discovery preview failed: {type(exc).__name__}")
        raise SystemExit(1) from None

    print("REVENUE ENGINE — GITHUB DISCOVERY DRY RUN")
    print("Mode: read-only. No leads saved. No email sent.")
    print(f"Country: {result['country']}")
    print(f"Places inspected (maximum 5): {result['places_inspected']}")
    print(f"Eligible candidates: {result['eligible_candidates']}")
    print(
        f"Priority buckets: A={result['priority_a']}, "
        f"B={result['priority_b']}, C={result['priority_c']}"
    )
    print("No database rows or schema were changed.")


if __name__ == "__main__":
    main()
