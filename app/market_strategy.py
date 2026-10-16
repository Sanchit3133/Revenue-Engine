"""Country-market hypotheses and validation gates for international expansion.

This is a planning aid, not a source of verified current market statistics.
Country groupings and initial hypotheses are supplied by the business owner;
operators must validate payments, language, discovery channels, competition,
and willingness to pay before outreach.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketProfile:
    country: str
    group: str
    priority: str
    language_hypothesis: str
    initial_hypothesis: str
    main_risks: tuple[str, ...]
    validation_checks: tuple[str, ...]


COMMON_CHECKS = (
    "Confirm a practical way for the business to pay an India-based provider; test settlement, fees, FX, and refund handling.",
    "Confirm the decision-maker's preferred business language and whether outreach/landing-page localization is needed.",
    "Verify that target businesses are discoverable on Google Maps, Facebook, or WhatsApp; record actual public evidence.",
    "Review at least 10 local competitors/agencies and compare their offers, proof, prices where public, and response quality.",
    "Interview or consult at least 3 target businesses before treating demand or low competition as established.",
    "Confirm local privacy, direct-marketing, consumer, tax, and payment rules before live outreach or accepting payment.",
)


def _profile(country: str, group: str, priority: str, language: str, hypothesis: str, risks: tuple[str, ...]) -> MarketProfile:
    return MarketProfile(country, group, priority, language, hypothesis, risks, COMMON_CHECKS)


PROFILES: dict[str, MarketProfile] = {
    "india": _profile(
        "India",
        "Domestic baseline",
        "benchmark",
        "English, Hindi, and regional languages; validate the preferred language by city and business segment",
        "Business-owner hypothesis: competition is intense in many digital-service niches. Keep India as an active domestic benchmark and compare actual reply quality, budget disclosure, payment completion, and delivery margin against international pilots; do not assume every Indian niche is saturated.",
        ("Segment-level competition", "Price acceptance", "Regional language fit", "Trust and proof"),
    ),
    "ethiopia": _profile("Ethiopia", "East Africa", "exploratory", "Amharic and other local languages; English varies by segment", "Potential opportunity; validate language fit especially carefully", ("Language localization", "International payment access", "Trust and local proof")),
    "tanzania": _profile("Tanzania", "East Africa", "test", "Swahili and English depending on audience", "User hypothesis: English is usable in some business segments and mobile money is widespread; verify actual cross-border payment route", ("Language fit", "Cross-border settlement", "Trust")),
    "uganda": _profile("Uganda", "East Africa", "test", "English and local languages depending on audience", "User hypothesis: English is common in target segments and mobile money is widespread; validate provider/payment compatibility", ("Cross-border settlement", "Trust", "Local competition")),
    "rwanda": _profile("Rwanda", "East Africa", "test", "Kinyarwanda, English, and French depending on audience", "User hypothesis: potential English-capable small-business market; validate segment by segment", ("Language fit", "Payment route", "Local proof")),
    "zambia": _profile("Zambia", "East Africa", "test", "English and local languages depending on audience", "User hypothesis: English-accessible business segments; verify buyer demand and payment route", ("Cross-border settlement", "Local competition", "Trust")),
    "senegal": _profile("Senegal", "Francophone West Africa", "test-after-localization", "French is a primary business-language hypothesis to validate", "User hypothesis: fewer suitable local providers; test with French-first outreach and landing-page copy", ("French localization", "Payment settlement", "Local trust")),
    "cote d'ivoire": _profile("Côte d'Ivoire", "Francophone West Africa", "test-after-localization", "French-first hypothesis", "User hypothesis: possible French-speaking niche; competition claim must be checked locally", ("French localization", "Payment settlement", "Local trust")),
    "cameroon": _profile("Cameroon", "Francophone West Africa", "test-after-localization", "French and English; varies by region and segment", "User hypothesis: possible bilingual market; choose language by location and buyer", ("Bilingual localization", "Payment settlement", "Regional differences")),
    "uzbekistan": _profile("Uzbekistan", "Central and Southeast Asia", "exploratory", "Uzbek and Russian; English depends on segment", "User hypothesis: growing small businesses with opportunity to investigate; validate digital channels and demand", ("Language localization", "Cross-border payments", "Trust")),
    "cambodia": _profile("Cambodia", "Central and Southeast Asia", "test-after-localization", "Khmer and English depending on segment", "User hypothesis: investigate growing small-business demand; verify language and payment feasibility", ("Khmer localization", "Cross-border payments", "Trust")),
    "laos": _profile("Laos", "Central and Southeast Asia", "exploratory", "Lao and some English depending on segment", "User hypothesis: investigate smaller, less-saturated niches; validate reachability first", ("Lao localization", "Cross-border payments", "Lead volume")),
    "mongolia": _profile("Mongolia", "Central and Southeast Asia", "exploratory", "Mongolian; English depends on segment", "User hypothesis: investigate small-business growth and channel reachability", ("Mongolian localization", "Cross-border payments", "Lead volume")),
    "nepal": _profile("Nepal", "Central and Southeast Asia", "test", "Nepali and English depending on audience", "User hypothesis: accessible nearby test market; validate buyer budgets and payment route", ("Payment access", "Local competition", "Language fit")),
    "papua new guinea": _profile("Papua New Guinea", "Islands and small markets", "exploratory", "English, Tok Pisin, and other local languages", "User hypothesis: small-market opportunity; validate reachable buyer density before investing", ("Lead volume", "Payment access", "Trust")),
    "fiji": _profile("Fiji", "Islands and small markets", "exploratory", "English, iTaukei, Fiji Hindi depending on audience", "User hypothesis: small-market opportunity; validate niche and buyer density", ("Lead volume", "Payment access", "Competition")),
    "madagascar": _profile("Madagascar", "Islands and small markets", "test-after-localization", "Malagasy and French depending on audience", "User hypothesis: potential niche; validate language and cross-border payment route", ("French/Malagasy localization", "Payment access", "Trust")),
    "malawi": _profile("Malawi", "Islands and small markets", "exploratory", "English and Chichewa depending on audience", "User hypothesis: investigate accessible English-speaking business segments", ("Payment access", "Lead volume", "Trust")),
    "tajikistan": _profile("Tajikistan", "Higher risk, low-competition hypothesis", "later-validation", "Tajik and Russian; English depends on segment", "Higher-risk hypothesis from user; do not prioritize without payment/trust validation", ("Payment access", "Language localization", "Trust")),
    "turkmenistan": _profile("Turkmenistan", "Higher risk, low-competition hypothesis", "later-validation", "Turkmen and Russian; English depends on segment", "Higher-risk hypothesis from user; do not prioritize without feasibility checks", ("Payment access", "Market access", "Trust")),
    "sierra leone": _profile("Sierra Leone", "Higher risk, low-competition hypothesis", "exploratory", "English and local languages depending on audience", "User hypothesis: validate payment, trust, and reachable lead volume", ("Payment access", "Trust", "Lead volume")),
    "liberia": _profile("Liberia", "Higher risk, low-competition hypothesis", "exploratory", "English and local languages depending on audience", "User hypothesis: validate payment, trust, and reachable lead volume", ("Payment access", "Trust", "Lead volume")),
    "burundi": _profile("Burundi", "Higher risk, low-competition hypothesis", "later-validation", "Kirundi, French, and other languages depending on audience", "Higher-risk hypothesis from user; validate localization and payment route", ("Language localization", "Payment access", "Trust")),
    "somalia": _profile("Somalia", "Higher risk, low-competition hypothesis", "later-validation", "Somali and Arabic; English depends on segment", "Higher-risk hypothesis from user; require strong payment and trust validation", ("Payment access", "Trust", "Market access")),
    "nigeria": _profile("Nigeria", "Crowded-market comparison", "selective", "English and local languages depending on audience", "User hypothesis: large, crowded freelancer/agency market; compete only with a clear niche", ("Competition", "Differentiation", "Payment and trust")),
    "bangladesh": _profile("Bangladesh", "Crowded-market comparison", "selective", "Bangla and English depending on audience", "User hypothesis: crowded provider market; validate niche before entry", ("Competition", "Language localization", "Differentiation")),
    "pakistan": _profile("Pakistan", "Crowded-market comparison", "selective", "Urdu and English depending on audience", "User hypothesis: crowded provider market; validate niche before entry", ("Competition", "Differentiation", "Payment access")),
}


def get_market(country: str) -> MarketProfile:
    key = " ".join(country.casefold().replace("’", "'").split())
    aliases = {"ivory coast": "cote d'ivoire", "côte d'ivoire": "cote d'ivoire", "cote divoire": "cote d'ivoire", "papua new guinea": "papua new guinea"}
    key = aliases.get(key, key)
    try:
        return PROFILES[key]
    except KeyError as exc:
        raise ValueError(f"Country not in configured market hypotheses: {country!r}") from exc


def list_markets() -> list[MarketProfile]:
    return sorted(PROFILES.values(), key=lambda item: (item.group, item.country))

VALIDATION_CHECK_KEYS = (
    "payment_checked",
    "language_checked",
    "reachability_checked",
    "competition_checked",
    "demand_checked",
    "compliance_checked",
    "unit_economics_checked",
)


def market_validation_complete(checks: dict[str, bool], evidence_notes: str) -> bool:
    """Return true only when every market gate is checked and notes contain evidence.

    This validates the operator's attestation structure; it cannot independently
    verify that evidence is accurate. Human review remains required.
    """
    return all(checks.get(key, False) is True for key in VALIDATION_CHECK_KEYS) and len((evidence_notes or "").strip()) >= 30
