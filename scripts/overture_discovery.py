"""Automated Overture Places discovery and official-site contact qualification.

Never use email fields from Overture itself. A recipient address is eligible only
when visibly published on the business's official HTTPS website. Respect robots.txt,
stay on the same host, and keep run output to aggregate counts.
"""
from __future__ import annotations

import random
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from app.database import INTEGRITY_ERRORS
from app.lead_engine import normalize_email, normalize_website, is_valid_public_email
from app.market_strategy import get_market
from app.scoring import score_business
from scripts.discover_leads import (
    EMAIL_RE, clean_email_candidate, is_good_email, lead_priority, save_lead,
)
from scripts.overture_source import (
    OVERTURE_ATTRIBUTION, City, download_city_places, parse_cities_zip,
    GEONAMES_CITIES_URL,
)

REQUEST_TIMEOUT = 15
MAX_PAGE_BYTES = 1_500_000
MAX_PAGES_PER_SITE = 5
MAX_NEW_LEADS = 5
BOT_TOKEN = "RevenueEngineProspectingBot"
HEADERS = {"User-Agent": "RevenueEngineProspectingBot/1.0 (+https://sanchit-agarwal.me/257-2/)"}
COUNTRY_CODES = {
    "india": "IN", "nepal": "NP", "uganda": "UG", "rwanda": "RW",
    "zambia": "ZM", "tanzania": "TZ", "ethiopia": "ET", "senegal": "SN",
    "cote d'ivoire": "CI", "cameroon": "CM", "uzbekistan": "UZ",
    "cambodia": "KH", "laos": "LA", "mongolia": "MN",
    "papua new guinea": "PG", "fiji": "FJ", "madagascar": "MG",
    "malawi": "MW", "tajikistan": "TJ", "turkmenistan": "TM",
    "sierra leone": "SL", "liberia": "LR",
}
SEGMENTS = (
    ("coaching", 40, "coaching institute", ("coaching", "academy", "institute", "tuition", "tutorial", "education")),
    ("language_training", 20, "language and spoken English training", ("language", "ielts", "pte", "toefl", "english", "coaching", "training")),
    ("coding_training", 20, "coding and IT training institute", ("coding", "computer", "programming", "software", "technology", "it training", "training")),
    ("exam_training", 20, "competitive exam coaching", ("exam preparation", "competitive", "entrance", "coaching", "tutoring", "education")),
)
CONTACT_WORDS = ("contact", "about", "admission", "enquiry", "inquiry", "reach", "counselling", "counseling", "team")
FALLBACK_PATHS = ("/contact", "/contact-us", "/contactus", "/about", "/about-us", "/admission", "/admissions", "/enquiry", "/enquiry-form", "/reach-us")
LOCAL_PRIORITY = {"admissions": 0, "admission": 1, "enquiries": 2, "enquiry": 3, "contact": 4, "info": 5, "hello": 6, "office": 7, "sales": 8}


class DiscoveryError(RuntimeError):
    """Safe, operator-facing discovery error without provider payloads."""


def choose_random_city(country: str = "India", *, rng=None) -> tuple[City, str]:
    market = get_market(country)
    code = COUNTRY_CODES.get(market.country.lower())
    if not code:
        raise DiscoveryError("No GeoNames country mapping is configured for this market.")
    try:
        response = requests.get(GEONAMES_CITIES_URL, timeout=30, headers=HEADERS)
        response.raise_for_status()
    except requests.RequestException:
        raise DiscoveryError("The city list could not be loaded; discovery stopped.") from None
    cities = parse_cities_zip(response.content, country_code=code, min_population=40000)
    if not cities:
        raise DiscoveryError("No eligible cities were found; discovery stopped.")
    return (rng or random.SystemRandom()).choice(cities), market.country


def choose_random_segment(*, rng=None) -> tuple[str, str, tuple[str, ...]]:
    chooser = rng or random.SystemRandom()
    item = chooser.choices(SEGMENTS, weights=[v[1] for v in SEGMENTS], k=1)[0]
    return item[0], item[2], item[3]


def _first_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("primary", "common", "name"):
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _text_list(value) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        result = []
        for item in value:
            text = _first_text(item)
            if not text and isinstance(item, str):
                text = item.strip()
            if text:
                result.append(text)
        return result
    return []


def normalize_feature(feature: dict) -> dict | None:
    props = feature.get("properties", feature)
    if not isinstance(props, dict):
        return None
    place_id = str(props.get("id") or "").strip()
    name = _first_text(props.get("names")) or str(props.get("name") or "").strip()
    website_values = _text_list(props.get("websites"))
    website = normalize_website(website_values[0]) if website_values else ""
    parsed = urlparse(website)
    if not place_id or not name or parsed.scheme.lower() != "https" or not parsed.hostname:
        return None
    status = str(props.get("operating_status") or "").lower()
    if status in {"permanently_closed", "closed"}:
        return None
    try:
        confidence = float(props.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    if confidence < 0.55:
        return None

    category_parts = []
    basic = props.get("basic_category")
    if isinstance(basic, str):
        category_parts.append(basic)
    taxonomy = props.get("taxonomy")
    if isinstance(taxonomy, dict):
        category_parts.extend(_text_list(taxonomy.get("primary")))
        category_parts.extend(_text_list(taxonomy.get("hierarchy")))
        category_parts.extend(_text_list(taxonomy.get("alternates")))
    categories = props.get("categories")
    category_parts.extend(_text_list(categories))
    category_text = " ".join(category_parts).lower()
    name_lower = name.lower()
    education_terms = ("education", "school", "college", "university", "tutoring", "coaching", "training", "academy", "institute", "language")
    if not any(term in (category_text + " " + name_lower) for term in education_terms):
        return None

    address = ""
    addresses = props.get("addresses")
    if isinstance(addresses, list) and addresses and isinstance(addresses[0], dict):
        first = addresses[0]
        parts = []
        for key in ("freeform", "locality", "region", "country"):
            value = str(first.get(key) or "").strip()
            if value and value not in parts:
                parts.append(value)
        address = ", ".join(parts)[:500]
    phones = _text_list(props.get("phones"))
    source_names = []
    source_licenses = []
    raw_sources = props.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        # Fail closed when source provenance cannot be checked.
        return None
    for source in raw_sources:
        if not isinstance(source, dict):
            return None
        src = str(source.get("dataset") or source.get("provider") or "").strip()
        license_name = str(source.get("license") or "").strip()
        if not src or not license_name:
            return None
        if src not in source_names:
            source_names.append(src)
        source_licenses.append((src, license_name))
    if not source_licenses:
        return None
    # First version handles these documented permissive source licenses only.
    # Foursquare-derived records are excluded until its NOTICE/Apache license
    # bundle is carried through the release/distribution process.
    accepted_licenses = {"cdla-permissive-2.0", "cc0-1.0"}
    normalized_licenses = {license_name.casefold() for _, license_name in source_licenses}
    normalized_sources = {src.casefold() for src, _ in source_licenses}
    if "foursquare" in normalized_sources:
        return None
    if not normalized_licenses.issubset(accepted_licenses):
        return None
    return {
        "place_id": "overture:" + place_id,
        "business_name": name,
        "website": website.rstrip("/"),
        "address": address,
        "phone": phones[0][:80] if phones else "",
        "category_text": category_text[:1000],
        "source_names": source_names,
        "source_licenses": source_licenses,
        "confidence": confidence,
    }


def _official_https_site(url: str) -> str:
    site = normalize_website(url)
    parsed = urlparse(site)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        return ""
    return site.rstrip("/")


def _same_host(base: str, candidate: str) -> bool:
    a, b = urlparse(base), urlparse(candidate)
    return b.scheme.lower() == "https" and bool(b.hostname) and b.hostname.lower() == (a.hostname or "").lower() and b.port in (None, 443)


def _robots(base: str) -> RobotFileParser | None:
    robots_url = base.split("/", 3)[0] + "//" + urlparse(base).netloc + "/robots.txt"
    try:
        response = requests.get(robots_url, headers=HEADERS, timeout=10, allow_redirects=True)
    except requests.RequestException:
        return None
    if response.status_code == 404:
        rules = []
    elif response.status_code == 200:
        rules = response.text[:256000].splitlines()
    else:
        return None
    parser = RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(rules)
    return parser


def _fetch_html(base: str, url: str) -> str:
    try:
        with requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True, allow_redirects=True) as response:
            if response.status_code != 200 or "text/html" not in response.headers.get("content-type", "").lower():
                return ""
            if not _same_host(base, response.url):
                return ""
            length = response.headers.get("content-length")
            if length and int(length) > MAX_PAGE_BYTES:
                return ""
            chunks = bytearray()
            for chunk in response.iter_content(32768):
                chunks.extend(chunk)
                if len(chunks) > MAX_PAGE_BYTES:
                    return ""
            return bytes(chunks).decode(response.encoding or "utf-8", errors="replace")
    except (requests.RequestException, ValueError, OSError):
        return ""


def _emails_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found = set()
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "").strip()
        if href.lower().startswith("mailto:"):
            address = clean_email_candidate(href[7:].split("?", 1)[0])
            if is_good_email(address):
                found.add(address)
    visible = soup.get_text(" ", strip=True)
    for address in EMAIL_RE.findall(visible):
        cleaned = clean_email_candidate(address)
        if is_good_email(cleaned):
            found.add(cleaned)
    for tag in soup.find_all(True):
        for attr in ("data-email", "data-mail", "data-contact-email", "aria-label", "title"):
            raw = tag.get(attr)
            if raw:
                for address in EMAIL_RE.findall(str(raw)):
                    cleaned = clean_email_candidate(address)
                    if is_good_email(cleaned):
                        found.add(cleaned)
    return sorted(found, key=lambda item: (LOCAL_PRIORITY.get(item.split("@", 1)[0], 50), item))


def find_official_email(website: str) -> tuple[str, str]:
    base = _official_https_site(website)
    if not base:
        return "", "https_required"
    robots = _robots(base)
    if robots is None or not robots.can_fetch(BOT_TOKEN, base + "/"):
        return "", "robots_unavailable_or_disallowed"
    homepage_url = base + "/"
    home = _fetch_html(base, homepage_url)
    if not home:
        home = _fetch_html(base, base)
    if not home:
        return "", "homepage_unavailable"
    found = _emails_from_html(home)
    if found:
        return found[0], "public_email_found"
    soup = BeautifulSoup(home, "html.parser")
    candidates = []
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "").strip()
        text = link.get_text(" ", strip=True).lower()
        if not any(term in (href + " " + text).lower() for term in CONTACT_WORDS):
            continue
        absolute = urljoin(base + "/", href)
        if _same_host(base, absolute) and absolute not in candidates:
            candidates.append(absolute)
    for path in FALLBACK_PATHS:
        url = urljoin(base + "/", path.lstrip("/"))
        if url not in candidates:
            candidates.append(url)
    visited = {homepage_url, base}
    for url in candidates:
        if len(visited) >= MAX_PAGES_PER_SITE:
            break
        if url in visited or not _same_host(base, url) or not robots.can_fetch(BOT_TOKEN, url):
            continue
        visited.add(url)
        time.sleep(0.25)
        page = _fetch_html(base, url)
        if not page:
            continue
        found = _emails_from_html(page)
        if found:
            return found[0], "public_email_found"
    return "", "no_public_email_found"


def discover_and_persist(conn, *, country: str = "India", max_new: int = 5, rng=None, persist: bool = True) -> dict:
    """Discover and persist up to five qualified leads, returning aggregate counts only."""
    if not 1 <= max_new <= MAX_NEW_LEADS:
        raise ValueError(f"max_new must be between 1 and {MAX_NEW_LEADS}")
    city, canonical_country = choose_random_city(country, rng=rng)
    segment, query, keywords = choose_random_segment(rng=rng)
    features = download_city_places(city)
    known_places = {row["place_id"] for row in conn.execute("SELECT place_id FROM leads WHERE place_id IS NOT NULL").fetchall() if row["place_id"]}
    known_emails = {normalize_email(row["email"]) for row in conn.execute("SELECT email FROM leads WHERE email IS NOT NULL AND trim(email) <> ''").fetchall() if row["email"]}
    suppressed = {normalize_email(row["email"]) for row in conn.execute("SELECT email FROM suppressions").fetchall() if row["email"]}
    seen_ids, seen_emails, candidates = set(), set(), []
    stats = {"places_seen": len(features), "site_checks": 0, "published_emails_found": 0, "qualified": 0, "saved": 0}
    for raw in features:
        place = normalize_feature(raw)
        if not place or place["place_id"] in known_places or place["place_id"] in seen_ids:
            continue
        seen_ids.add(place["place_id"])
        combined = (place["business_name"] + " " + place["category_text"]).lower()
        if not any(term in combined for term in keywords):
            continue
        stats["site_checks"] += 1
        email, signal = find_official_email(place["website"])
        if not email:
            continue
        stats["published_emails_found"] += 1
        email = normalize_email(email)
        if not is_valid_public_email(email) or email in known_emails or email in seen_emails or email in suppressed:
            continue
        scored = score_business(place["business_name"], place["category_text"], place["website"], signal, query)
        if scored.score < 30:
            continue
        priority, priority_score, reasons = lead_priority(place["business_name"], email, scored.score)
        notes = [
            *scored.reasons, *reasons,
            f"first_sale_priority={priority}",
            f"priority_score={priority_score}",
            f"discovery_source=Overture Maps Places; segment={segment}",
            f"discovery_city={city.name}; city_source=GeoNames cities15000; city_license=CC BY 4.0",
            f"overture_source_records={','.join(place['source_names']) or 'unspecified'}",
            "overture_source_licenses=" + ",".join(sorted({license_name for _, license_name in place["source_licenses"]})),
            f"data_attribution={OVERTURE_ATTRIBUTION}",
            "email_evidence=publicly displayed on the business's official HTTPS website",
        ]
        candidates.append({
            **place, "email": email, "country": canonical_country,
            "qualification": scored.qualification, "score": scored.score,
            "signal": signal, "notes": "; ".join(notes),
            "priority": priority, "priority_score": priority_score,
        })
        seen_emails.add(email)
    order = {"A": 0, "B": 1, "C": 2}
    candidates.sort(key=lambda item: (
        order.get(item["priority"], 3), -item["priority_score"], -item["score"],
        item["business_name"].lower(),
    ))
    stats["qualified"] = min(len(candidates), max_new)
    saved_ids = []
    if not persist:
        return {"city": city.name, "segment": segment, "country": canonical_country, **stats, "lead_ids": []}
    for item in candidates[:max_new]:
        try:
            lead_id = save_lead(conn, item)
        except INTEGRITY_ERRORS:
            conn.rollback()
            lead_id = None
        if lead_id is not None:
            stats["saved"] += 1
            saved_ids.append(int(lead_id))
            known_places.add(item["place_id"])
            known_emails.add(normalize_email(item["email"]))
    return {"city": city.name, "segment": segment, "country": canonical_country, **stats, "lead_ids": saved_ids}
