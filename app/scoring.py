"""
Revenue Engine — Lead Scoring

Scores businesses for the ₹490 landing-page offer.

The score is based on actual business context:
- business name
- Google/search category
- search query
- website
- conversion signals

This is a qualification score, not a guarantee that the business needs
a landing page.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreResult:
    score: int
    qualification: str
    reasons: tuple[str, ...]


# Strong indicators that the business is in our initial target market.
HIGH_INTENT_TERMS = {
    "ielts": 35,
    "pte": 35,
    "spoken english": 35,
    "english coaching": 30,
    "english institute": 30,

    "coding institute": 35,
    "coding classes": 30,
    "computer institute": 25,
    "computer classes": 25,
    "it training": 30,
    "software training": 30,
    "programming classes": 30,

    "upsc": 35,
    "mpsc": 35,
    "ssc": 30,
    "banking coaching": 30,
    "neet": 25,
    "jee": 25,
    "iit-jee": 30,
    "competitive exam": 35,
    "competitive exams": 35,
    "entrance exam": 25,

    "cat coaching": 30,
    "clat coaching": 30,
    "ipmat coaching": 30,
    "cuet coaching": 30,

    "professional training": 30,
    "skill training": 25,
    "vocational training": 25,

    "coaching institute": 35,
    "coaching classes": 35,
    "training institute": 30,
    "training center": 30,
    "training centre": 30,
    "academy": 20,
}


# Words that indicate an educational/training business even if the
# exact high-intent phrase isn't present.
EDUCATION_TERMS = {
    "coaching",
    "classes",
    "academy",
    "institute",
    "training",
    "education",
    "learning",
    "tuition",
    "tutorial",
    "exam preparation",
}


# Signals that indicate the business is actively trying to convert
# visitors into enquiries/admissions.
CONVERSION_TERMS = {
    "enquiry": 12,
    "enquiries": 12,
    "inquiry": 12,
    "inquiries": 12,
    "admission": 12,
    "admissions": 12,
    "counselling": 12,
    "counseling": 12,
    "book a demo": 10,
    "book demo": 10,
    "free demo": 10,
    "contact us": 8,
    "apply now": 8,
    "register now": 8,
    "join now": 8,
    "whatsapp": 8,
}


def _normalise_text(*values: str) -> str:
    return " ".join(
        str(value or "").strip().lower()
        for value in values
    )


def score_business(
    name: str,
    category: str = "",
    website: str = "",
    signal: str = "",
    query: str = "",
) -> ScoreResult:
    """
    Score a business against our target customer profile.

    `query` is intentionally included because the discovery query itself
    is useful evidence. For example:

        query = "coaching institute"

    is strong evidence that the discovered result is relevant.
    """

    text = _normalise_text(
        name,
        category,
        website,
        signal,
        query,
    )

    score = 0
    reasons: list[str] = []

    # --------------------------------------------------------
    # 1. Strong target-market terms
    # --------------------------------------------------------

    matched_high_intent: set[str] = set()

    for term, points in HIGH_INTENT_TERMS.items():

        if term in text and term not in matched_high_intent:

            score += points
            matched_high_intent.add(term)

            reasons.append(
                f"target:{term}"
            )

    # --------------------------------------------------------
    # 2. General education evidence
    # --------------------------------------------------------

    education_matches = [
        term
        for term in EDUCATION_TERMS
        if term in text
    ]

    if education_matches:

        # Cap this contribution so a keyword-heavy website
        # doesn't artificially dominate the score.
        education_points = min(
            len(education_matches) * 8,
            20,
        )

        score += education_points

        reasons.append(
            "education:"
            + ",".join(education_matches[:5])
        )

    # --------------------------------------------------------
    # 3. Conversion signals
    # --------------------------------------------------------

    conversion_matches = [
        term
        for term in CONVERSION_TERMS
        if term in text
    ]

    if conversion_matches:

        conversion_points = min(
            sum(
                CONVERSION_TERMS[term]
                for term in conversion_matches
            ),
            25,
        )

        score += conversion_points

        reasons.append(
            "conversion:"
            + ",".join(conversion_matches[:6])
        )

    # --------------------------------------------------------
    # 4. Website
    # --------------------------------------------------------

    if website.strip():

        score += 5
        reasons.append(
            "has_website"
        )

    # --------------------------------------------------------
    # 5. Public contact signal
    # --------------------------------------------------------

    if "public_email_found" in signal.lower():

        score += 5
        reasons.append(
            "public_email"
        )

    # --------------------------------------------------------
    # 6. Cap score
    # --------------------------------------------------------

    score = min(score, 100)

    # --------------------------------------------------------
    # Qualification
    # --------------------------------------------------------

    if score >= 60:
        qualification = "HIGH"

    elif score >= 35:
        qualification = "MEDIUM"

    else:
        qualification = "LOW"

    return ScoreResult(
        score=score,
        qualification=qualification,
        reasons=tuple(reasons),
    )