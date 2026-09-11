from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.services import get_service


@dataclass(frozen=True)
class Diagnosis:
    """
    Represents a possible business problem identified from available evidence.

    The engine distinguishes between:
    - observed evidence,
    - a possible business problem,
    - and questions that should verify the situation.

    It must never present an assumption as a confirmed fact.
    """

    problem_key: str
    problem_name: str
    confidence: str
    service_key: Optional[str]
    reasons: List[str] = field(default_factory=list)
    questions: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Problem definitions
# ---------------------------------------------------------------------------

PROBLEM_DEFINITIONS: Dict[str, Dict] = {
    "WEAK_CONVERSION_FLOW": {
        "name": "Potentially weak conversion flow",
        "service_key": "LANDING_PAGE",
        "signals": [
            "outdated website",
            "website is outdated",
            "website looks outdated",
            "no clear call to action",
            "no clear cta",
            "unclear offer",
            "generic website",
            "course promotion without dedicated page",
            "main course has no dedicated page",
            "important service lacks a focused page",
        ],
        "questions": [
            "How do you currently turn website visitors into enquiries?",
            "Do you use a dedicated page for your main course or offer?",
            "Where do people go when they click your advertisements or promotions?",
        ],
    },

    "PAYMENT_FRICTION": {
        "name": "Potential payment friction",
        "service_key": "PAYMENT_PAGE",
        "signals": [
            "no online payment",
            "no online payment option",
            "manual payment",
            "manual payment collection",
            "customers pay manually",
            "unclear payment instructions",
            "payment instructions scattered across channels",
        ],
        "questions": [
            "How do customers currently make payments?",
            "Is there a single place where customers can complete or start the payment process?",
            "Do staff members have to manually explain the payment process?",
        ],
    },

    "UNSTRUCTURED_LEADS": {
        "name": "Potentially unstructured enquiry collection",
        "service_key": "LEAD_CAPTURE",
        "signals": [
            "no structured enquiry form",
            "no structured inquiry form",
            "enquiries handled through multiple channels",
            "inquiries handled through multiple channels",
            "manual enquiry collection",
            "manual inquiry collection",
            "no obvious lead capture workflow",
        ],
        "questions": [
            "How do you currently collect enquiry details?",
            "Are enquiries stored in one place or across different channels?",
            "What information do you normally collect from a new enquiry?",
        ],
    },

    "SLOW_FIRST_RESPONSE": {
        "name": "Potential first-response delay",
        "service_key": "AUTO_RESPONSE",
        "signals": [
            "no immediate acknowledgement",
            "no immediate acknowledgment",
            "enquiries depend entirely on manual replies",
            "inquiries depend entirely on manual replies",
            "customers expected to wait for a response",
            "high-volume enquiry periods",
            "high volume enquiry periods",
        ],
        "questions": [
            "What happens immediately after someone sends an enquiry?",
            "Does the person receive an acknowledgement automatically?",
            "How quickly does someone from your team normally respond?",
        ],
    },

    "FOLLOW_UP_GAP": {
        "name": "Potential follow-up gap",
        "service_key": "FOLLOW_UP_SYSTEM",
        "signals": [
            "no follow-up schedule",
            "no follow up schedule",
            "staff manually remembering follow-ups",
            "staff manually remember follow-ups",
            "staff manually remembers follow-ups",
            "enquiries often go cold",
            "inquiries often go cold",
            "follow-up depends on individual staff members",
            "follow up depends on individual staff members",
        ],
        "questions": [
            "What happens when an enquiry does not enroll or purchase immediately?",
            "Do you have a defined follow-up schedule?",
            "How does your team know which enquiries need another follow-up?",
        ],
    },

    "MANUAL_WORKFLOW": {
        "name": "Potentially repetitive manual workflow",
        "service_key": "BUSINESS_AUTOMATION",
        "signals": [
            "repetitive manual processes",
            "repetitive manual process",
            "multiple disconnected tools",
            "staff repeatedly entering the same information",
            "staff repeatedly enter the same information",
            "workflow depends on spreadsheets or manual reminders",
            "process depends on spreadsheets or manual reminders",
        ],
        "questions": [
            "Which parts of the process does your team repeat manually?",
            "Are the systems you use connected to each other?",
            "Is there any information your team has to enter more than once?",
        ],
    },

    "CUSTOM_WORKFLOW": {
        "name": "Potential organization-specific workflow requirement",
        "service_key": "CUSTOM_AUTOMATION",
        "signals": [
            "complex multi-step process",
            "complex multi step process",
            "multiple tools involved",
            "manual transfer of information",
            "workflow cannot be solved by a standard package",
            "organization-specific automation requirement",
        ],
        "questions": [
            "Can you walk me through the process from beginning to end?",
            "Which step takes the most manual effort?",
            "Which systems or tools are involved?",
        ],
    },
}


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

def _normalize_text(value: object) -> str:
    """
    Safely convert input to normalized lowercase text.
    """

    if value is None:
        return ""

    return " ".join(str(value).lower().split())


def _contains_signal(text: str, signal: str) -> bool:
    """
    Check whether a known signal appears in the evidence.

    Signal matching is intentionally conservative. The engine only reports
    a signal when the supplied evidence actually contains that signal.
    """

    normalized_text = _normalize_text(text)
    normalized_signal = _normalize_text(signal)

    if not normalized_text or not normalized_signal:
        return False

    return normalized_signal in normalized_text


# ---------------------------------------------------------------------------
# Evidence construction
# ---------------------------------------------------------------------------

def build_evidence_text(
    business: Optional[Dict] = None,
    website_text: str = "",
    notes: str = "",
    signal: str = "",
) -> str:
    """
    Combine available business information into one evidence string.

    Only information supplied to this function is considered.

    Unknown information remains unknown.
    """

    business = business or {}

    parts = [
        business.get("name", ""),
        business.get("category", ""),
        business.get("type", ""),
        business.get("description", ""),
        business.get("website", ""),
        business.get("address", ""),
        business.get("signal", ""),
        business.get("notes", ""),
        website_text,
        notes,
        signal,
    ]

    return " ".join(
        str(part)
        for part in parts
        if part is not None and str(part).strip()
    )


# ---------------------------------------------------------------------------
# Diagnosis engine
# ---------------------------------------------------------------------------

def diagnose_business(
    business: Optional[Dict] = None,
    website_text: str = "",
    notes: str = "",
    signal: str = "",
) -> List[Diagnosis]:
    """
    Diagnose possible business problems from available evidence.

    Returns a ranked list of possible diagnoses.

    Important:
        A diagnosis is a hypothesis based on evidence.
        It is not presented as a confirmed business problem.
    """

    evidence = build_evidence_text(
        business=business,
        website_text=website_text,
        notes=notes,
        signal=signal,
    )

    if not evidence.strip():
        return []

    matches: List[Tuple[int, str, List[str]]] = []

    for problem_key, definition in PROBLEM_DEFINITIONS.items():
        matched_signals: List[str] = []

        for known_signal in definition["signals"]:
            if _contains_signal(evidence, known_signal):
                matched_signals.append(known_signal)

        if not matched_signals:
            continue

        # Each matched signal is counted as one independent piece of
        # evidence. Equivalent wording variants are handled in the
        # definitions themselves, so we do not collapse legitimate
        # observations into one generic group.
        score = len(set(matched_signals))

        matches.append(
            (
                score,
                problem_key,
                matched_signals,
            )
        )

    # Highest evidence first.
    matches.sort(
        key=lambda item: (
            item[0],
            item[1],
        ),
        reverse=True,
    )

    diagnoses: List[Diagnosis] = []

    for score, problem_key, matched_signals in matches:
        definition = PROBLEM_DEFINITIONS[problem_key]

        if score >= 3:
            confidence = "high"
        elif score >= 2:
            confidence = "medium"
        else:
            confidence = "low"

        service_key = definition.get("service_key")
        service = get_service(service_key) if service_key else None

        reasons = [
            f"Observed signal: {signal_text}"
            for signal_text in matched_signals
        ]

        if service:
            reasons.append(
                f"Potentially relevant service: {service.name}"
            )

        diagnoses.append(
            Diagnosis(
                problem_key=problem_key,
                problem_name=definition["name"],
                confidence=confidence,
                service_key=service_key,
                reasons=reasons,
                questions=list(definition["questions"]),
            )
        )

    return diagnoses


# ---------------------------------------------------------------------------
# Best diagnosis
# ---------------------------------------------------------------------------

def best_diagnosis(
    business: Optional[Dict] = None,
    website_text: str = "",
    notes: str = "",
    signal: str = "",
) -> Optional[Diagnosis]:
    """
    Return the strongest evidence-backed diagnosis.

    Returns None when there is insufficient evidence.

    The consultation engine should ask questions rather than force a
    recommendation when this function returns None.
    """

    diagnoses = diagnose_business(
        business=business,
        website_text=website_text,
        notes=notes,
        signal=signal,
    )

    if not diagnoses:
        return None

    return diagnoses[0]


# ---------------------------------------------------------------------------
# Consultation-safe recommendation
# ---------------------------------------------------------------------------

def recommendation_for_diagnosis(
    diagnosis: Optional[Diagnosis],
) -> Optional[Dict[str, object]]:
    """
    Convert a diagnosis into a consultation-safe recommendation.

    The result intentionally does not include:
    - a price,
    - a discount,
    - a proposal,
    - or a payment request.

    It only gives the consultation engine enough information to continue
    investigating the possible problem.
    """

    if diagnosis is None:
        return None

    service = (
        get_service(diagnosis.service_key)
        if diagnosis.service_key
        else None
    )

    return {
        "problem_key": diagnosis.problem_key,
        "problem_name": diagnosis.problem_name,
        "confidence": diagnosis.confidence,
        "service_key": diagnosis.service_key,
        "service_name": service.name if service else None,
        "reasons": list(diagnosis.reasons),
        "questions": list(diagnosis.questions),
        "next_step": (
            "Discuss the observed situation and ask whether the business "
            "would like to explore possible improvements."
        ),
    }
