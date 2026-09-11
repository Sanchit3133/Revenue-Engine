"""
Revenue Engine — Lead Discovery

Purpose:
    1. Discover local businesses through Google Places.
    2. Prioritize coaching/training businesses.
    3. Visit public business websites.
    4. Extract publicly displayed business email addresses.
    5. Score businesses for the ₹490 landing-page offer.
    6. Rank leads for the first-sale campaign.
    7. Prevent duplicate businesses/emails.
    8. Respect suppression records.
    9. Support a safe dry-run before persistence.

Usage:

    python -m scripts.discover_leads \
        --city "Nagpur" \
        --query "coaching institute" \
        --pages 2 \
        --max-new 10 \
        --dry-run

Environment:

    GOOGLE_API_KEY
    OUTREACH_DB (optional)
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import time
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.config import settings
from app.lead_engine import (
    is_valid_public_email,
    normalize_email,
    normalize_website,
)
from app.scoring import score_business


# ============================================================
# CONFIGURATION
# ============================================================

TEXT_SEARCH_URL = (
    "https://maps.googleapis.com/maps/api/place/textsearch/json"
)

DETAILS_URL = (
    "https://maps.googleapis.com/maps/api/place/details/json"
)

REQUEST_TIMEOUT = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; RevenueEngine/1.0; "
        "+https://sanchit-agarwal.me/257-2/)"
    )
}


# Common public pages where businesses publish contact details.
CONTACT_PATHS = (
    "",
    "/contact",
    "/contact-us",
    "/contactus",
    "/about",
    "/about-us",
    "/admission",
    "/admissions",
    "/enquiry",
    "/enquiry-form",
    "/reach-us",
)


EMAIL_RE = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
)


BAD_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "domain.com",
    "yourdomain.com",
    "localhost",
    "wixpress.com",
    "sentry.io",
    "schema.org",
    "w3.org",
}


# Addresses that should never be used for outreach.
BAD_LOCAL_PARTS = {
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "mailer-daemon",
    "postmaster",
}


# Legitimate addresses, but usually less likely to reach
# the owner/decision-maker directly.
GENERIC_LOCAL_PARTS = {
    "support",
    "help",
    "info",
    "contact",
    "admin",
    "office",
    "sales",
    "team",
    "hello",
}


# Large brands can be legitimate prospects, but for the
# FIRST SALE they generally get lower priority because
# procurement/decision cycles can be slower.
BIG_BRAND_TERMS = {
    "aakash",
    "narayana",
    "physicswallah",
    "pw ",
    "resonance",
    "career launcher",
}


# ============================================================
# GOOGLE PLACES
# ============================================================

def places_text_search(
    api_key: str,
    query: str,
    city: str,
    pages: int,
) -> list[dict]:
    """
    Search Google Places Text Search with robust pagination.

    Google next_page_token values may temporarily return
    INVALID_REQUEST immediately after page 1. We retry the
    SAME token with increasing delays rather than abandoning
    page 2 immediately.
    """

    results: list[dict] = []
    next_page_token: str | None = None

    for page_number in range(1, pages + 1):

        print()
        print(
            f"[PLACES] Search page {page_number}: "
            f"{query} in {city}"
        )

        data: dict | None = None

        # ----------------------------------------------------
        # First page
        # ----------------------------------------------------

        if not next_page_token:

            params = {
                "key": api_key,
                "query": f"{query} in {city}",
            }

            try:
                response = requests.get(
                    TEXT_SEARCH_URL,
                    params=params,
                    headers=HEADERS,
                    timeout=REQUEST_TIMEOUT,
                )

                response.raise_for_status()
                data = response.json()

            except requests.RequestException as exc:
                print(
                    f"[PLACES] Request failed: {exc}"
                )
                break

        # ----------------------------------------------------
        # Subsequent pages
        # ----------------------------------------------------

        else:

            # Google may need several seconds before the token
            # becomes usable. Retry progressively.
            retry_delays = (2, 4, 6, 8, 10)

            for attempt, delay in enumerate(
                retry_delays,
                start=1,
            ):

                if attempt > 1:
                    print(
                        f"[PLACES] Page {page_number} "
                        f"retry {attempt}/{len(retry_delays)} "
                        f"after {delay}s..."
                    )

                time.sleep(delay)

                params = {
                    "key": api_key,
                    "pagetoken": next_page_token,
                }

                try:
                    response = requests.get(
                        TEXT_SEARCH_URL,
                        params=params,
                        headers=HEADERS,
                        timeout=REQUEST_TIMEOUT,
                    )

                    response.raise_for_status()
                    candidate = response.json()

                except requests.RequestException as exc:
                    print(
                        f"[PLACES] Pagination request "
                        f"failed: {exc}"
                    )
                    continue

                status = candidate.get("status")

                if status == "OK":
                    data = candidate
                    break

                if status == "ZERO_RESULTS":
                    data = candidate
                    break

                if status == "INVALID_REQUEST":
                    print(
                        "[PLACES] Google says next_page_token "
                        "is not ready yet."
                    )
                    continue

                print(
                    f"[PLACES] Pagination status: {status}"
                )

                if candidate.get("error_message"):
                    print(
                        f"[PLACES] "
                        f"{candidate['error_message']}"
                    )

                break

        # ----------------------------------------------------
        # Validate response
        # ----------------------------------------------------

        if not data:
            print(
                f"[PLACES] Could not retrieve "
                f"page {page_number}."
            )
            break

        status = data.get("status")

        if status not in {"OK", "ZERO_RESULTS"}:

            print(
                f"[PLACES] Google returned status: "
                f"{status}"
            )

            if data.get("error_message"):
                print(
                    f"[PLACES] "
                    f"{data['error_message']}"
                )

            break

        page_results = data.get("results", [])

        print(
            f"[PLACES] Results returned: "
            f"{len(page_results)}"
        )

        results.extend(page_results)

        next_page_token = data.get(
            "next_page_token"
        )

        if not next_page_token:
            break

    return results


# ============================================================
# PLACE DETAILS
# ============================================================

def get_place_details(
    api_key: str,
    place_id: str,
) -> dict:
    """
    Retrieve website, phone, address and place types.
    """

    params = {
        "place_id": place_id,
        "fields": (
            "name,"
            "formatted_address,"
            "formatted_phone_number,"
            "website,"
            "types,"
            "url"
        ),
        "key": api_key,
    }

    try:

        response = requests.get(
            DETAILS_URL,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()
        data = response.json()

    except requests.RequestException as exc:

        print(
            f"[DETAILS] Request failed for "
            f"{place_id}: {exc}"
        )

        return {}

    if data.get("status") != "OK":

        print(
            f"[DETAILS] Status for {place_id}: "
            f"{data.get('status')}"
        )

        return {}

    return data.get("result", {})


# ============================================================
# EMAIL EXTRACTION
# ============================================================

def clean_email_candidate(value: str) -> str:
    """
    Normalize common HTML/URL encoding around an email.
    """

    value = unquote(value or "")
    value = value.replace(
        "&commat;",
        "@",
    )

    value = value.strip()

    # Remove common surrounding punctuation.
    value = value.strip(
        " \t\r\n<>\"'()[]{};,:"
    )

    return normalize_email(value)


def extract_emails_from_html(
    html: str,
) -> list[str]:
    """
    Extract publicly displayed email addresses.

    We inspect:
        - mailto links
        - visible page text
        - common HTML attributes

    We never invent addresses.
    """

    found: set[str] = set()

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    # --------------------------------------------------------
    # 1. mailto links
    # --------------------------------------------------------

    for link in soup.find_all(
        "a",
        href=True,
    ):

        href = link.get(
            "href",
            "",
        )

        if href.lower().startswith(
            "mailto:"
        ):

            raw = href[
                len("mailto:"):
            ]

            # Remove query parameters such as
            # ?subject=...
            raw = raw.split(
                "?",
                1,
            )[0]

            email = clean_email_candidate(
                raw
            )

            if is_good_email(email):
                found.add(email)

    # --------------------------------------------------------
    # 2. Visible text
    # --------------------------------------------------------

    text = soup.get_text(
        " ",
        strip=True,
    )

    for match in EMAIL_RE.findall(
        text
    ):

        email = clean_email_candidate(
            match
        )

        if is_good_email(email):
            found.add(email)

    # --------------------------------------------------------
    # 3. Common HTML attributes
    # --------------------------------------------------------

    attributes_to_check = (
        "data-email",
        "data-mail",
        "data-contact-email",
        "data-cfemail",
        "title",
        "aria-label",
    )

    for tag in soup.find_all(True):

        for attribute in attributes_to_check:

            value = tag.get(
                attribute
            )

            if not value:
                continue

            for match in EMAIL_RE.findall(
                str(value)
            ):

                email = clean_email_candidate(
                    match
                )

                if is_good_email(email):
                    found.add(email)

    return sorted(found)


def is_good_email(
    email: str,
) -> bool:
    """
    Conservative email quality gate.
    """

    email = clean_email_candidate(
        email
    )

    if not email:
        return False

    if not is_valid_public_email(
        email
    ):
        return False

    if "@" not in email:
        return False

    local, domain = email.rsplit(
        "@",
        1,
    )

    local = local.lower()
    domain = domain.lower()

    if domain in BAD_EMAIL_DOMAINS:
        return False

    if local in BAD_LOCAL_PARTS:
        return False

    return True


# ============================================================
# WEBSITE FETCHING
# ============================================================

def fetch_page(
    url: str,
) -> str:
    """
    Fetch a public website page.
    """

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        if response.status_code != 200:
            return ""

        content_type = (
            response.headers.get(
                "content-type",
                "",
            ).lower()
        )

        if "text/html" not in content_type:
            return ""

        return response.text

    except requests.RequestException:
        return ""


def discover_contact_links(
    base_url: str,
    html: str,
) -> list[str]:
    """
    Discover real contact-related links from the homepage.

    This supplements the fixed CONTACT_PATHS list.
    """

    found: list[str] = []

    try:

        parsed = urlparse(
            base_url
        )

        base_domain = parsed.netloc.lower()

    except Exception:
        return found

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    keywords = (
        "contact",
        "about",
        "admission",
        "enquiry",
        "inquiry",
        "reach",
        "counselling",
        "counseling",
    )

    for link in soup.find_all(
        "a",
        href=True,
    ):

        href = link.get(
            "href",
            "",
        ).strip()

        if not href:
            continue

        text = link.get_text(
            " ",
            strip=True,
        ).lower()

        combined = (
            f"{href.lower()} {text}"
        )

        if not any(
            keyword in combined
            for keyword in keywords
        ):
            continue

        absolute = urljoin(
            base_url,
            href,
        )

        try:

            parsed_link = urlparse(
                absolute
            )

        except Exception:
            continue

        if parsed_link.netloc.lower() != base_domain:
            continue

        if absolute not in found:
            found.append(
                absolute
            )

    return found[:10]


# ============================================================
# PUBLIC EMAIL DISCOVERY
# ============================================================

def find_public_email(
    website: str,
) -> tuple[str, str]:
    """
    Visit a small number of likely public contact pages.

    Returns:
        email, signal

    We do not guess addresses.
    """

    website = normalize_website(
        website
    )

    if not website:
        return "", ""

    parsed = urlparse(
        website
    )

    base = (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
    )

    visited: set[str] = set()
    candidate_urls: list[
        tuple[str, str]
    ] = []

    # --------------------------------------------------------
    # First: homepage + known paths
    # --------------------------------------------------------

    for path in CONTACT_PATHS:

        url = urljoin(
            base + "/",
            path.lstrip("/"),
        )

        candidate_urls.append(
            (
                url,
                path or "/",
            )
        )

    # --------------------------------------------------------
    # Inspect homepage first so we can discover real links.
    # --------------------------------------------------------

    homepage = fetch_page(
        website
    )

    if homepage:

        emails = extract_emails_from_html(
            homepage
        )

        if emails:

            return (
                emails[0],
                "public_email_found",
            )

        dynamic_links = (
            discover_contact_links(
                website,
                homepage,
            )
        )

        for link in dynamic_links:

            candidate_urls.append(
                (
                    link,
                    "discovered_link",
                )
            )

    # --------------------------------------------------------
    # Visit candidate pages
    # --------------------------------------------------------

    for url, signal_name in candidate_urls:

        if url in visited:
            continue

        visited.add(url)

        # Homepage was already fetched.
        if (
            normalize_website(url)
            == normalize_website(website)
            and homepage
        ):
            html = homepage

        else:

            html = fetch_page(
                url
            )

        if not html:
            continue

        emails = extract_emails_from_html(
            html
        )

        if emails:

            if signal_name.startswith(
                "/"
            ):

                signal = (
                    "public_email_found:"
                    f"{signal_name}"
                )

            else:

                signal = (
                    "public_email_found:"
                    "contact_link"
                )

            return (
                emails[0],
                signal,
            )

        # Small delay between website requests.
        time.sleep(0.25)

    return "", ""


# ============================================================
# LEAD PRIORITY
# ============================================================

def lead_priority(
    name: str,
    email: str,
    score: int,
) -> tuple[str, int, list[str]]:
    """
    Determine first-sale priority.

    A = strongest first-sale candidates
    B = reasonable candidates
    C = lower-priority candidates

    This does NOT reject a legitimate lead.
    It only determines outreach order.
    """

    priority_score = int(score)
    reasons: list[str] = []

    email = normalize_email(
        email
    )

    if "@" in email:

        local, domain = email.rsplit(
            "@",
            1,
        )

        local = local.lower()
        domain = domain.lower()

        # Generic mailbox.
        if local in GENERIC_LOCAL_PARTS:

            priority_score -= 15

            reasons.append(
                "generic_mailbox"
            )

        # Consumer mailbox.
        if domain in {
            "gmail.com",
            "yahoo.com",
            "hotmail.com",
            "outlook.com",
            "live.com",
        }:

            priority_score -= 5

            reasons.append(
                "consumer_email_domain"
            )

        # Business-domain email gets no penalty.

    name_lower = (
        name.lower()
    )

    for brand in BIG_BRAND_TERMS:

        if brand in name_lower:

            priority_score -= 15

            reasons.append(
                "large_brand"
            )

            break

    priority_score = max(
        0,
        min(
            100,
            priority_score,
        ),
    )

    if priority_score >= 80:
        priority = "A"

    elif priority_score >= 55:
        priority = "B"

    else:
        priority = "C"

    return (
        priority,
        priority_score,
        reasons,
    )


# ============================================================
# DATABASE HELPERS
# ============================================================

def existing_place_ids(
    conn: sqlite3.Connection,
) -> set[str]:

    rows = conn.execute(
        """
        SELECT place_id
        FROM leads
        WHERE place_id IS NOT NULL
        """
    ).fetchall()

    return {
        row["place_id"]
        for row in rows
        if row["place_id"]
    }


def existing_emails(
    conn: sqlite3.Connection,
) -> set[str]:

    rows = conn.execute(
        """
        SELECT email
        FROM leads
        WHERE email IS NOT NULL
        """
    ).fetchall()

    return {
        normalize_email(
            row["email"]
        )
        for row in rows
        if row["email"]
    }


def is_suppressed(
    conn: sqlite3.Connection,
    email: str,
) -> bool:

    row = conn.execute(
        """
        SELECT 1
        FROM suppressions
        WHERE email = ?
        LIMIT 1
        """,
        (
            normalize_email(
                email
            ),
        ),
    ).fetchone()

    return row is not None


def save_lead(
    conn: sqlite3.Connection,
    lead: dict,
) -> int | None:
    """
    Persist a qualified lead.
    """

    try:

        cursor = conn.execute(
            """
            INSERT INTO leads (
                place_id,
                business_name,
                address,
                phone,
                website,
                email,
                qualification,
                score,
                signal,
                notes,
                state
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lead["place_id"],
                lead["business_name"],
                lead["address"],
                lead["phone"],
                lead["website"],
                lead["email"],
                lead["qualification"],
                lead["score"],
                lead["signal"],
                lead["notes"],
                "QUALIFIED",
            ),
        )

        conn.commit()

        return cursor.lastrowid

    except sqlite3.IntegrityError:

        conn.rollback()

        return None


# ============================================================
# LEAD PROCESSING
# ============================================================

def process_place(
    api_key: str,
    query: str,
    place: dict,
    conn: sqlite3.Connection,
    known_place_ids: set[str],
    known_emails: set[str],
) -> dict | None:

    place_id = place.get(
        "place_id"
    )

    if not place_id:
        return None

    if place_id in known_place_ids:

        print(
            f"[SKIP] Already discovered: "
            f"{place.get('name', 'Unknown')}"
        )

        return None

    name = (
        place.get("name")
        or ""
    ).strip()

    if not name:
        return None

    details = get_place_details(
        api_key,
        place_id,
    )

    if not details:
        return None

    website = normalize_website(
        details.get("website")
    )

    address = (
        details.get(
            "formatted_address"
        )
        or place.get(
            "formatted_address"
        )
        or ""
    )

    phone = (
        details.get(
            "formatted_phone_number"
        )
        or ""
    )

    types = details.get(
        "types",
        [],
    )

    type_text = " ".join(
        types
    )

    # Search result text contributes to scoring.
    place_text = " ".join(
        [
            name,
            type_text,
        ]
    )

    # --------------------------------------------------------
    # Email discovery
    # --------------------------------------------------------

    email = ""
    email_signal = ""

    if website:

        print(
            f"[WEB] Checking {name}: "
            f"{website}"
        )

        email, email_signal = (
            find_public_email(
                website
            )
        )

    # --------------------------------------------------------
    # Duplicate / suppression checks
    # --------------------------------------------------------

    if email:

        email = normalize_email(
            email
        )

        if email in known_emails:

            print(
                f"[SKIP] Email already known: "
                f"{email}"
            )

            return None

        if is_suppressed(
            conn,
            email,
        ):

            print(
                f"[SKIP] Email suppressed: "
                f"{email}"
            )

            return None

    # --------------------------------------------------------
    # Scoring
    # --------------------------------------------------------

    signal_text = " ".join(
        filter(
            None,
            [
                email_signal,
                website,
            ],
        )
    )

    result = score_business(
        name=name,
        category=place_text,
        website=website,
        signal=signal_text,
        query=query,
    )

    # --------------------------------------------------------
    # Email is mandatory for outreach
    # --------------------------------------------------------

    if not email:

        print(
            f"[NO EMAIL] {name} "
            f"| score={result.score}"
        )

        return None

    # --------------------------------------------------------
    # Minimum relevance threshold
    # --------------------------------------------------------

    if result.score < 30:

        print(
            f"[DISQUALIFIED] {name} "
            f"| score={result.score}"
        )

        return None

    # --------------------------------------------------------
    # First-sale priority
    # --------------------------------------------------------

    priority, priority_score, priority_reasons = (
        lead_priority(
            name=name,
            email=email,
            score=result.score,
        )
    )

    notes = list(
        result.reasons
    )

    notes.extend(
        priority_reasons
    )

    notes.append(
        f"first_sale_priority={priority}"
    )

    notes.append(
        f"priority_score={priority_score}"
    )

    lead = {
        "place_id": place_id,
        "business_name": name,
        "address": address,
        "phone": phone,
        "website": website,
        "email": email,
        "qualification": result.qualification,
        "score": result.score,
        "signal": email_signal,
        "notes": "; ".join(notes),
        "priority": priority,
        "priority_score": priority_score,
    }

    print()
    print(
        "[QUALIFIED] "
        f"{name}"
    )

    print(
        f"  Email: {email}"
    )

    print(
        f"  Score: {result.score}/100"
    )

    print(
        f"  Qualification: "
        f"{result.qualification}"
    )

    print(
        f"  Priority: "
        f"{priority}"
    )

    print(
        f"  Priority Score: "
        f"{priority_score}/100"
    )

    print(
        f"  Website: "
        f"{website or 'none'}"
    )

    return lead


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Revenue Engine coaching-business "
            "lead discovery"
        )
    )

    parser.add_argument(
        "--city",
        required=True,
        help="City to search",
    )

    parser.add_argument(
        "--query",
        required=True,
        help="Google Places search query",
    )

    parser.add_argument(
        "--pages",
        type=int,
        default=2,
        help="Number of Google Places pages",
    )

    parser.add_argument(
        "--max-new",
        type=int,
        default=10,
        help=(
            "Maximum new qualified leads "
            "to select"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Discover and display leads "
            "without saving them"
        ),
    )

    args = parser.parse_args()

    if args.pages < 1:

        raise SystemExit(
            "--pages must be at least 1"
        )

    if args.max_new < 1:

        raise SystemExit(
            "--max-new must be at least 1"
        )

    api_key = settings.google_api_key

    if not api_key:

        raise SystemExit(
            "ERROR: GOOGLE_API_KEY is not set."
        )

    # --------------------------------------------------------
    # Database
    # --------------------------------------------------------

    from app.database import init_db

    init_db()

    conn = sqlite3.connect(
        settings.db_path
    )

    conn.row_factory = sqlite3.Row

    try:

        known_place_ids = (
            existing_place_ids(
                conn
            )
        )

        known_emails = (
            existing_emails(
                conn
            )
        )

        print()
        print("=" * 60)
        print(
            "REVENUE ENGINE — LEAD DISCOVERY"
        )
        print("=" * 60)

        print(
            f"City: {args.city}"
        )

        print(
            f"Query: {args.query}"
        )

        print(
            f"Pages: {args.pages}"
        )

        print(
            f"Maximum new leads: "
            f"{args.max_new}"
        )

        if args.dry_run:

            print(
                "Mode: DRY RUN"
            )

        else:

            print(
                "Mode: PERSIST"
            )

        # ----------------------------------------------------
        # Discover places
        # ----------------------------------------------------

        places = places_text_search(
            api_key=api_key,
            query=args.query,
            city=args.city,
            pages=args.pages,
        )

        print()
        print(
            f"[DISCOVERY] Total Places results: "
            f"{len(places)}"
        )

        # ----------------------------------------------------
        # Process all discovered places.
        #
        # We do NOT stop immediately after finding the first
        # 10 because a later result may have Priority A while
        # an earlier result has Priority C.
        # ----------------------------------------------------

        candidates: list[dict] = []

        seen_in_run: set[str] = set()
        seen_emails_in_run: set[str] = set()

        for place in places:

            place_id = place.get(
                "place_id"
            )

            if not place_id:
                continue

            if place_id in seen_in_run:
                continue

            seen_in_run.add(
                place_id
            )

            lead = process_place(
                api_key=api_key,
                query=args.query,
                place=place,
                conn=conn,
                known_place_ids=known_place_ids,
                known_emails=known_emails,
            )

            if not lead:
                continue

            email = normalize_email(
                lead["email"]
            )

            if email in seen_emails_in_run:

                print(
                    f"[SKIP] Duplicate email "
                    f"in current run: {email}"
                )

                continue

            seen_emails_in_run.add(
                email
            )

            candidates.append(
                lead
            )

        # ----------------------------------------------------
        # Rank candidates.
        #
        # A first, then B, then C.
        # Within each group, highest score first.
        # ----------------------------------------------------

        priority_order = {
            "A": 0,
            "B": 1,
            "C": 2,
        }

        candidates.sort(
            key=lambda lead: (
                priority_order.get(
                    lead.get(
                        "priority",
                        "C",
                    ),
                    3,
                ),
                -int(
                    lead.get(
                        "priority_score",
                        0,
                    )
                ),
                -int(
                    lead.get(
                        "score",
                        0,
                    )
                ),
                lead.get(
                    "business_name",
                    "",
                ).lower(),
            )
        )

        qualified = candidates[
            :args.max_new
        ]

        # ----------------------------------------------------
        # Show final selected list
        # ----------------------------------------------------

        print()
        print("=" * 60)
        print(
            "FIRST-SALE PRIORITY QUEUE"
        )
        print("=" * 60)

        for index, lead in enumerate(
            qualified,
            start=1,
        ):

            print(
                f"{index}. "
                f"[{lead['priority']}] "
                f"{lead['business_name']} "
                f"| "
                f"score={lead['score']} "
                f"| "
                f"priority={lead['priority_score']} "
                f"| "
                f"{lead['email']}"
            )

        # ----------------------------------------------------
        # Persist only the selected leads.
        # ----------------------------------------------------

        if not args.dry_run:

            for lead in qualified:

                lead_id = save_lead(
                    conn,
                    lead,
                )

                if lead_id:

                    known_place_ids.add(
                        lead["place_id"]
                    )

                    known_emails.add(
                        lead["email"]
                    )

                    print(
                        f"[SAVED] "
                        f"{lead['business_name']} "
                        f"→ Lead ID: "
                        f"{lead_id}"
                    )

                else:

                    print(
                        "[SKIP] Database duplicate: "
                        f"{lead['business_name']}"
                    )

        # ----------------------------------------------------
        # Summary
        # ----------------------------------------------------

        print()
        print("=" * 60)
        print(
            "DISCOVERY COMPLETE"
        )
        print("=" * 60)

        print(
            f"Qualified new leads: "
            f"{len(qualified)}"
        )

        a_count = sum(
            1
            for lead in qualified
            if lead.get(
                "priority"
            ) == "A"
        )

        b_count = sum(
            1
            for lead in qualified
            if lead.get(
                "priority"
            ) == "B"
        )

        c_count = sum(
            1
            for lead in qualified
            if lead.get(
                "priority"
            ) == "C"
        )

        print(
            f"Priority A: {a_count}"
        )

        print(
            f"Priority B: {b_count}"
        )

        print(
            f"Priority C: {c_count}"
        )

        if args.dry_run:

            print()
            print(
                "DRY RUN — NOTHING WAS SAVED "
                "AND NOTHING WAS SENT."
            )

        else:

            print()
            print(
                "Leads were saved to the "
                "Revenue Engine database."
            )

    finally:

        conn.close()


if __name__ == "__main__":
    main()