"""Website-seed lead worker for GitHub Actions.

This worker does not call Google Places. It operates only on up to five
owner-supplied official website URLs, checks robots.txt, reads a small number
of public same-site pages, and persists only publicly displayed contact details.

Preview mode is read-only. Persist mode inserts qualified leads and their state
audit events only; it never creates outreach records, performs migrations, or sends email.
"""
from __future__ import annotations

import argparse
import ipaddress
import re
import socket
from contextlib import redirect_stdout
from datetime import datetime, timezone
from functools import lru_cache
from io import StringIO
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from app.config import settings
from app.database import connect
from app.lead_engine import is_valid_public_email, normalize_email, normalize_website
from app.market_strategy import get_market
from app.models import LeadState
from app.pipeline import transition
from app.scoring import score_business

MAX_SITES = 5
MAX_BODY_BYTES = 1_500_000
REQUEST_TIMEOUT = (5, 10)
USER_AGENT = "RevenueEngineWebsiteResearch/1.0 (+https://sanchit-agarwal.me/)"
REQUIRED_TABLES = {"leads", "suppressions", "state_events"}
CONTACT_PATHS = ("/contact", "/contact-us", "/admissions", "/enquiry")
EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
)
BLOCKED_GENERIC = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster"}


def parse_website_urls(value: str, max_sites: int = MAX_SITES) -> list[str]:
    """Parse comma/semicolon/whitespace-separated HTTPS URLs, capped at five."""
    if not value or not value.strip():
        raise ValueError("Provide at least one official website URL.")
    raw_parts = [part.strip() for part in re.split(r"[\s,;]+", value) if part.strip()]
    if len(raw_parts) > max_sites:
        raise ValueError(f"Provide no more than {max_sites} websites per run.")

    normalized: list[str] = []
    seen_hosts: set[str] = set()
    for raw in raw_parts:
        parsed = urlparse(raw)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValueError("Every website seed must be a full HTTPS URL.")
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("Website seeds must not contain credentials or nonstandard ports.")
        host = parsed.hostname.lower().rstrip(".")
        if host in {"localhost"} or host.endswith((".local", ".internal", ".localhost")):
            raise ValueError("Local or internal hostnames are not permitted.")
        host_key = host.removeprefix("www.")
        if host_key in seen_hosts:
            continue
        seen_hosts.add(host_key)
        normalized.append(normalize_website(raw))
    if not normalized:
        raise ValueError("No valid website URLs were supplied.")
    return normalized


@lru_cache(maxsize=64)
def _host_resolves_publicly(host: str) -> bool:
    """Reject IP literals and DNS names that resolve to non-public addresses."""
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    ips = set()
    for answer in answers:
        try:
            ips.add(ipaddress.ip_address(answer[4][0]))
        except ValueError:
            return False
    return bool(ips) and all(ip.is_global for ip in ips)


def _same_site(host: str, seed_host: str) -> bool:
    return host == seed_host or host.removeprefix("www.") == seed_host.removeprefix("www.")


def _validate_target(url: str, seed_host: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return False
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
        host = parsed.hostname.lower().rstrip(".")
        if not _same_site(host, seed_host):
            return False
        if not _host_resolves_publicly(host):
            return False
        return True
    except ValueError:
        return False


def _safe_get(url: str, seed_host: str) -> tuple[int, str, str] | None:
    """GET a same-site HTTPS page, validate every redirect, and cap response size."""
    current = url
    for _ in range(4):
        if not _validate_target(current, seed_host):
            return None
        try:
            response = requests.get(
                current,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.8"},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException:
            return None

        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.close()
            if not location:
                return None
            current = urljoin(current, location)
            continue

        status = response.status_code
        content_type = response.headers.get("content-type", "").lower()
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=16384):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_BODY_BYTES:
                    response.close()
                    return (413, current, "")
                chunks.append(chunk)
        finally:
            response.close()

        if status != 200 or "text/html" not in content_type:
            return (status, current, "")
        body = b"".join(chunks)
        encoding = response.encoding or "utf-8"
        try:
            html = body.decode(encoding, errors="replace")
        except LookupError:
            html = body.decode("utf-8", errors="replace")
        # Revalidate final URL even though redirects are manually bounded.
        if not _validate_target(current, seed_host):
            return None
        return (status, current, html)
    return None


def _robots_policy(base_url: str, seed_host: str) -> RobotFileParser | None:
    robots_url = urljoin(base_url, "/robots.txt")
    result = _safe_get(robots_url, seed_host)
    if result is None:
        # Fail closed if robots.txt cannot be retrieved reliably.
        return None
    status, final_url, body = result
    parser = RobotFileParser()
    parser.set_url(robots_url)
    if status == 404:
        parser.parse([])
        return parser
    if status != 200:
        return None
    parser.parse(body.splitlines())
    return parser


def _text(soup: BeautifulSoup, limit: int = 6000) -> str:
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    return soup.get_text(" ", strip=True)[:limit]


def extract_business_name(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for selector in ('meta[property="og:site_name"]', "h1", "title"):
        node = soup.select_one(selector)
        if node is None:
            continue
        if node.name == "meta":
            value = (node.get("content") or "").strip()
        else:
            value = node.get_text(" ", strip=True)
        value = re.sub(r"\s+", " ", value).strip(" -|—–:")
        if not value or value.casefold() in {"home", "homepage", "welcome", "official website"}:
            continue
        if selector == "title":
            value = re.split(r"\s+[|—–]\s+|\s+-\s+", value, maxsplit=1)[0].strip()
        if len(value) >= 2:
            return value[:200]
    return ""


def extract_public_email(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    for link in soup.find_all("a", href=True):
        href = str(link.get("href", "")).strip()
        if href.lower().startswith("mailto:"):
            candidates.append(href[7:].split("?", 1)[0])
    visible = _text(soup)
    candidates.extend(EMAIL_PATTERN.findall(visible))
    cleaned: list[str] = []
    for candidate in candidates:
        email = normalize_email(candidate)
        if not is_valid_public_email(email):
            continue
        local = email.split("@", 1)[0].lower()
        if local in BLOCKED_GENERIC or email in cleaned:
            continue
        cleaned.append(email)
    return cleaned[0] if cleaned else ""


def inspect_official_website(seed_url: str, country: str) -> dict[str, Any] | None:
    """Inspect at most one homepage plus four same-site contact paths."""
    parsed = urlparse(seed_url)
    seed_host = (parsed.hostname or "").lower().rstrip(".")
    base_url = f"https://{seed_host}/"
    robots = _robots_policy(base_url, seed_host)
    if robots is None:
        return None

    page_urls = [seed_url]
    page_urls.extend(urljoin(base_url, path.lstrip("/")) for path in CONTACT_PATHS)
    business_name = ""
    public_email = ""
    public_email_page = ""
    evidence_text = ""
    pages_checked = 0

    for page_url in page_urls:
        if not robots.can_fetch(USER_AGENT, page_url):
            continue
        result = _safe_get(page_url, seed_host)
        if result is None:
            continue
        status, final_url, html = result
        if status != 200 or not html:
            continue
        pages_checked += 1
        soup = BeautifulSoup(html, "html.parser")
        if not business_name:
            business_name = extract_business_name(html)
        evidence_text += " " + _text(soup, 3500)
        if not public_email:
            public_email = extract_public_email(html)
            if public_email:
                public_email_page = final_url
        if business_name and public_email:
            break

    if not business_name or not public_email:
        return None

    score_result = score_business(
        name=business_name,
        category=evidence_text,
        website=seed_url,
        signal="public_email_found",
        query="",
    )
    if score_result.score < 30:
        return None

    return {
        "business_name": business_name,
        "website": seed_url,
        "email": public_email,
        "country": country,
        "score": score_result.score,
        "qualification": score_result.qualification,
        "signal": "public_email_found",
        "notes": (
            "source=owner_supplied_official_website_seed; "
            f"contact_page={public_email_page}; pages_checked={pages_checked}; "
            f"score_reasons={','.join(score_result.reasons)}; "
            f"researched_at={datetime.now(timezone.utc).isoformat()}"
        ),
        "pages_checked": pages_checked,
    }


def _verify_required_tables(conn: Any) -> None:
    rows = conn.execute(
        """SELECT table_name FROM information_schema.tables
           WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"""
    ).fetchall()
    present = {row[0] for row in rows}
    missing = sorted(REQUIRED_TABLES - present)
    if missing:
        raise RuntimeError("Worker cannot see required tables: " + ", ".join(missing))


def _is_duplicate(conn: Any, lead: dict[str, Any]) -> bool:
    row = conn.execute(
        """SELECT 1 FROM leads
           WHERE (email IS NOT NULL AND lower(email)=lower(?))
              OR (website IS NOT NULL AND lower(rtrim(website, '/'))=lower(rtrim(?, '/')))
           LIMIT 1""",
        (lead["email"], lead["website"]),
    ).fetchone()
    if row is not None:
        return True
    suppression = conn.execute(
        "SELECT 1 FROM suppressions WHERE lower(email)=lower(?) LIMIT 1",
        (lead["email"],),
    ).fetchone()
    return suppression is not None


def inspect_candidates(websites: list[str], country: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    market = get_market(country)
    candidates: list[dict[str, Any]] = []
    counts = {"sites_submitted": len(websites), "sites_inspected": 0, "qualified": 0, "duplicates_or_suppressed": 0}
    # Silence libraries/helpers here too; only aggregate counts are printed by main().
    with redirect_stdout(StringIO()):
        with connect() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            _verify_required_tables(conn)
            for website in websites:
                counts["sites_inspected"] += 1
                candidate = inspect_official_website(website, market.country)
                if not candidate:
                    continue
                if _is_duplicate(conn, candidate):
                    counts["duplicates_or_suppressed"] += 1
                    continue
                candidates.append(candidate)
                counts["qualified"] += 1
    return candidates, counts


def persist_candidates(candidates: list[dict[str, Any]], country: str) -> dict[str, int]:
    results = {"saved": 0, "duplicates_or_suppressed": 0, "rejected": 0}
    with connect() as conn:
        conn.execute("SET TRANSACTION READ WRITE")
        _verify_required_tables(conn)
        for candidate in candidates[:MAX_SITES]:
            if _is_duplicate(conn, candidate):
                results["duplicates_or_suppressed"] += 1
                continue
            cursor = conn.execute(
                """INSERT INTO leads (
                    place_id, business_name, website, email, country,
                    qualification, score, signal, notes, state
                )
                VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id""",
                (
                    candidate["business_name"],
                    candidate["website"],
                    candidate["email"],
                    country,
                    candidate["qualification"],
                    candidate["score"],
                    candidate["signal"],
                    candidate["notes"],
                    LeadState.DISCOVERED.value,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                results["rejected"] += 1
                continue
            transition(
                conn,
                int(row[0]),
                LeadState.QUALIFIED,
                reason="Qualified from owner-supplied official website seed; public contact displayed.",
            )
            results["saved"] += 1
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crawl at most five owner-supplied official websites; no email outreach."
    )
    parser.add_argument("--websites", required=True, help="Up to five HTTPS URLs, comma/space separated")
    parser.add_argument("--country", default="India")
    parser.add_argument("--persist", action="store_true", help="Persist qualified leads (no outreach records or email)")
    parser.add_argument("--confirmation", default="", help="Must equal PERSIST_MAX_5_LEADS in persist mode")
    args = parser.parse_args()
    try:
        market = get_market(args.country)
        websites = parse_website_urls(args.websites)
        if args.persist and args.confirmation != "PERSIST_MAX_5_LEADS":
            raise ValueError("Persist mode requires explicit PERSIST_MAX_5_LEADS confirmation.")
        candidates, counts = inspect_candidates(websites, market.country)
        if args.persist and candidates:
            result = persist_candidates(candidates, market.country)
        else:
            result = {"saved": 0, "duplicates_or_suppressed": counts["duplicates_or_suppressed"], "rejected": 0}
    except Exception as exc:
        # Do not print raw exceptions; database/network errors may contain connection details.
        print(f"Website discovery failed: {type(exc).__name__}")
        raise SystemExit(1) from None

    print("REVENUE ENGINE — WEBSITE-SEED DISCOVERY")
    print(f"Mode: {'PERSIST LEADS' if args.persist else 'PREVIEW ONLY'}")
    print(f"Country: {market.country}")
    print(f"Submitted website seeds: {counts['sites_submitted']}")
    print(f"Websites inspected: {counts['sites_inspected']}")
    print(f"Qualified new candidates: {counts['qualified']}")
    print(f"Duplicates/suppressed: {counts['duplicates_or_suppressed']}")
    if args.persist:
        print(f"New qualified leads saved: {result['saved']}")
        print(f"Duplicates/suppressed at write time: {result['duplicates_or_suppressed']}")
        print(f"Rejected candidates: {result['rejected']}")
        print("No outreach records were created and no email was sent.")
    else:
        print("Preview only: no lead rows or schema were changed.")


if __name__ == "__main__":
    main()
