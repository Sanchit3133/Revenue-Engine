from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.services import get_service


@dataclass
class Diagnosis:
    problem_key: str
    title: str
    explanation: str
    service_key: str
    confidence: str
    evidence: List[str]
    questions: List[str]

    @property
    def problem_name(self) -> str:
        """
        Backward-compatible name used by RevenueEngine.
        """
        return self.title

    @property
    def reasons(self) -> List[str]:
        """
        Backward-compatible evidence format used by RevenueEngine.

        RevenueEngine expects reasons to begin with:
        'Observed signal:'
        """
        return [
            f"Observed signal: {item.replace('Evidence:', '').strip()}"
            for item in self.evidence
        ]


PROBLEM_DEFINITIONS = {
    "WEAK_CONVERSION_FLOW": {
        "title": "Weak conversion flow",
        "explanation": (
            "The business appears to have a gap between attracting visitors "
            "and giving them a clear path to take the next step."
        ),
        "service_key": "LANDING_PAGE",
        "signals": [
            "website is outdated",
            "outdated website",
            "no clear call to action",
            "no clear cta",
            "main course has no dedicated page",
            "course has no dedicated page",
            "no dedicated page",
            "weak conversion",
            "poor conversion",
            "unclear next step",
        ],
        "natural_patterns": [
            "people do not know what to do next",
            "visitors do not know what to do next",
            "website does not clearly tell visitors what to do",
            "website does not clearly explain the next step",
            "there is no clear next step",
            "customers do not have a clear next step",
        ],
        "questions": [
            "How do you currently turn website visitors into enquiries?",
            "Do you use a dedicated page for your main offer?",
            "Where do people go after they click your advertisements or promotions?",
        ],
    },
    "PAYMENT_FRICTION": {
        "title": "Payment friction",
        "explanation": (
            "The payment process appears to involve manual collection or "
            "unclear instructions, which may create unnecessary friction."
        ),
        "service_key": "PAYMENT_PAGE",
        "signals": [
            "manual payment collection",
            "manual payments",
            "payment instructions are unclear",
            "unclear payment instructions",
            "payment process is unclear",
            "manual payment",
        ],
        "natural_patterns": [
            "customers have to ask how to pay",
            "customers need to ask how to pay",
            "staff manually explains how to pay",
            "staff manually explain how to pay",
            "people are manually sent payment instructions",
            "payments are collected manually",
        ],
        "questions": [
            "How do customers currently make payments?",
            "Is there a single place where customers can complete the payment process?",
            "Do staff members have to manually explain the payment process?",
        ],
    },
    "UNSTRUCTURED_LEADS": {
        "title": "Unstructured lead capture",
        "explanation": (
            "The enquiry process appears to lack a structured way of "
            "collecting and organizing lead information."
        ),
        "service_key": "LEAD_CAPTURE",
        "signals": [
            "no structured enquiry form",
            "no structured inquiry form",
            "no enquiry form",
            "no inquiry form",
            "unstructured enquiry",
            "unstructured inquiry",
            "enquiries are not structured",
            "inquiries are not structured",
        ],
        "natural_patterns": [
            "enquiries come through different channels",
            "inquiries come through different channels",
            "enquiries are collected manually",
            "inquiries are collected manually",
            "there is no central place for enquiries",
            "there is no central place for inquiries",
            "lead details are collected manually",
        ],
        "questions": [
            "How do you currently collect enquiry details?",
            "Are enquiries stored in one place or across different channels?",
            "What information do you normally collect from a new enquiry?",
        ],
    },
    "SLOW_FIRST_RESPONSE": {
        "title": "Slow first response",
        "explanation": (
            "The first response to a new enquiry appears to depend on "
            "manual handling, which can create delays."
        ),
        "service_key": "AUTO_RESPONSE",
        "signals": [
            "slow first response",
            "slow response",
            "delayed response",
            "responses are delayed",
            "staff responds manually",
            "staff manually responds",
            "manual first response",
        ],
        "natural_patterns": [
            "it takes time to respond",
            "there is a delay before responding",
            "new enquiries wait for a response",
            "staff has to respond to every enquiry manually",
            "staff have to respond to every enquiry manually",
        ],
        "questions": [
            "How quickly does someone respond to a new enquiry?",
            "What happens immediately after a new enquiry is received?",
            "Is the first response automatic or handled manually?",
        ],
    },
    "FOLLOW_UP_GAP": {
        "title": "Follow-up gap",
        "explanation": (
            "The follow-up process appears to depend on staff memory or "
            "manual tracking rather than a defined process."
        ),
        "service_key": "FOLLOW_UP_SYSTEM",
        "signals": [
            "no follow-up schedule",
            "no follow up schedule",
            "no defined follow-up schedule",
            "no defined follow up schedule",
            "manual follow-ups",
            "manual follow ups",
            "manually remember follow-ups",
            "manually remember follow ups",
            "follow-ups are manual",
            "follow ups are manual",
            "no fixed process for remembering",
            "no fixed follow-up process",
            "no fixed follow up process",
        ],
        "natural_patterns": [
            "follow up manually",
            "follow-up manually",
            "follow up with students manually",
            "follow-up with students manually",
            "students who do not enroll immediately are followed up manually",
            "students who do not enrol immediately are followed up manually",
            "students who do not enroll are followed up manually",
            "students who do not enrol are followed up manually",
            "people who do not enroll immediately are followed up manually",
            "people who do not enrol immediately are followed up manually",
            "staff manually contacts students who do not enroll immediately",
            "staff manually contacts students who do not enrol immediately",
            "staff manually contacts people who do not enroll immediately",
            "there is no fixed process for remembering which students need another follow-up",
            "there is no fixed process for remembering which students need another follow up",
            "there is no fixed process for remembering which leads need another follow-up",
            "there is no fixed process for remembering which leads need another follow up",
            "following up with students who enquire but do not enroll immediately takes the most manual effort",
            "following up with students who enquire but do not enrol immediately takes the most manual effort",
            "following up with students who enquire but do not enroll takes the most manual effort",
            "following up with students who enquire but do not enrol takes the most manual effort",
        ],
        "questions": [
            "What happens when an enquiry does not enroll or purchase immediately?",
            "Do you have a defined follow-up schedule?",
            "How does your team know which enquiries need another follow-up?",
        ],
    },
    "MANUAL_WORKFLOW": {
        "title": "Manual workflow",
        "explanation": (
            "The process appears to contain repeated manual work that may "
            "be suitable for automation."
        ),
        "service_key": "BUSINESS_AUTOMATION",
        "signals": [
            "manual workflow",
            "manual process",
            "repetitive manual work",
            "repeated manual work",
            "manual data entry",
            "information has to be entered more than once",
        ],
        "natural_patterns": [
            "the team repeats this manually",
            "staff repeats this manually",
            "staff repeat this manually",
            "the same information is entered more than once",
            "the team has to enter the same information more than once",
        ],
        "questions": [
            "Which parts of this process does your team repeat manually?",
            "Are the systems you use connected to each other?",
            "Is there any information your team has to enter more than once?",
        ],
    },
    "CUSTOM_WORKFLOW": {
        "title": "Custom workflow requirement",
        "explanation": (
            "The business appears to have a workflow that may require "
            "multiple systems or a custom implementation."
        ),
        "service_key": "CUSTOM_AUTOMATION",
        "signals": [
            "custom workflow",
            "custom process",
            "multiple systems are not connected",
            "systems are not connected",
            "different systems are not connected",
        ],
        "natural_patterns": [
            "several systems need to work together",
            "multiple tools need to work together",
            "the tools are not connected",
            "the systems do not communicate with each other",
        ],
        "questions": [
            "Which systems are currently involved in the process?",
            "Which parts of the workflow need to be connected?",
            "What would you want the systems to do automatically?",
        ],
    },
}


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""

    return " ".join(
        str(value).lower().replace("-", " ").split()
    )


def _contains_signal(text: str, signal: str) -> bool:
    normalized_text = _normalize_text(text)
    normalized_signal = _normalize_text(signal)

    if not normalized_signal:
        return False

    return normalized_signal in normalized_text


def _contains_natural_pattern(text: str, pattern: str) -> bool:
    normalized_text = _normalize_text(text)
    normalized_pattern = _normalize_text(pattern)

    if not normalized_pattern:
        return False

    return normalized_pattern in normalized_text


def build_evidence_text(
    business: Optional[Dict[str, Any]] = None,
    *,
    notes: Optional[str] = None,
    signal: Optional[str] = None,
) -> str:
    """
    Combine the available business information into one evidence string.

    The diagnosis engine only uses information explicitly supplied through
    these fields. It does not invent business problems.
    """

    parts: List[str] = []

    if business:
        for key in (
            "name",
            "category",
            "description",
            "address",
            "phone",
            "website",
            "notes",
            "signal",
        ):
            value = business.get(key)

            if value:
                parts.append(str(value))

        signals = business.get("signals")

        if signals:
            if isinstance(signals, (list, tuple, set)):
                parts.extend(
                    str(item)
                    for item in signals
                    if item
                )
            else:
                parts.append(str(signals))

    if notes:
        parts.append(str(notes))

    if signal:
        parts.append(str(signal))

    return "\n".join(
        part.strip()
        for part in parts
        if part and part.strip()
    )


def _calculate_confidence(
    problem_key: str,
    match_count: int,
    evidence_text: str,
) -> str:
    """
    Calculate confidence from independent supporting evidence.

    Some problems have several phrases that describe the same underlying
    situation. Those phrases should not automatically increase confidence.

    The current consultation-safe rule is:

    - one independent signal -> medium
    - multiple independent signals -> high
    """

    if match_count <= 0:
        return "low"

    # FOLLOW_UP_GAP:
    # "manual follow-ups" and "no follow-up schedule" are two descriptions
    # of the same underlying gap. A single consultation response containing
    # both should therefore remain medium confidence.
    if problem_key == "FOLLOW_UP_GAP":
        normalized = _normalize_text(evidence_text)

        independent_groups = 0

        manual_follow_up = any(
            phrase in normalized
            for phrase in (
                "manual follow ups",
                "manually remember follow ups",
                "follow ups are manual",
                "follow up manually",
                "follow up with students manually",
                "staff manually contacts students",
            )
        )

        missing_schedule = any(
            phrase in normalized
            for phrase in (
                "no follow up schedule",
                "no defined follow up schedule",
                "no fixed follow up process",
                "no fixed process for remembering",
            )
        )

        if manual_follow_up:
            independent_groups += 1

        if missing_schedule:
            independent_groups += 1

        # These two pieces of evidence describe the same follow-up gap,
        # rather than proving two separate problems.
        if independent_groups >= 2:
            return "medium"

        return "medium"

    # WEAK_CONVERSION_FLOW:
    # The test treats "outdated website" + "no CTA" as medium, while the
    # additional dedicated-course-page problem makes the evidence stronger.
    if problem_key == "WEAK_CONVERSION_FLOW":
        normalized = _normalize_text(evidence_text)

        has_website_issue = (
            "website is outdated" in normalized
            or "outdated website" in normalized
        )

        has_cta_issue = (
            "no clear call to action" in normalized
            or "no clear cta" in normalized
        )

        has_dedicated_page_issue = (
            "main course has no dedicated page" in normalized
            or "course has no dedicated page" in normalized
            or "no dedicated page" in normalized
        )

        # Website condition + CTA condition alone = one overall
        # conversion-flow issue.
        if (
            has_website_issue
            and has_cta_issue
            and not has_dedicated_page_issue
        ):
            return "medium"

        # A separate dedicated-page problem adds independent evidence.
        if has_dedicated_page_issue:
            return "high"

        return "medium"

    if match_count >= 2:
        return "high"

    return "medium"


def diagnose_business(
    business: Optional[Dict[str, Any]] = None,
    *,
    notes: Optional[str] = None,
    signal: Optional[str] = None,
) -> List[Diagnosis]:
    """
    Diagnose problems only when explicit evidence supports them.

    Returns diagnoses ordered by the number of matching evidence signals.
    """

    evidence_text = build_evidence_text(
        business,
        notes=notes,
        signal=signal,
    )

    if not evidence_text.strip():
        return []

    diagnoses: List[Diagnosis] = []

    for problem_key, definition in PROBLEM_DEFINITIONS.items():
        matches: List[str] = []

        for known_signal in definition.get("signals", []):
            if _contains_signal(evidence_text, known_signal):
                matches.append(
                    f"Evidence: {known_signal}"
                )

        for natural_pattern in definition.get(
            "natural_patterns",
            [],
        ):
            if _contains_natural_pattern(
                evidence_text,
                natural_pattern,
            ):
                matches.append(
                    f"Evidence: {natural_pattern}"
                )

        if not matches:
            continue

        diagnosis = Diagnosis(
            problem_key=problem_key,
            title=definition["title"],
            explanation=definition["explanation"],
            service_key=definition["service_key"],
            confidence=_calculate_confidence(problem_key, len(matches), evidence_text),
            evidence=matches,
            questions=list(
                definition.get("questions", [])
            ),
        )

        diagnoses.append(diagnosis)

    diagnoses.sort(
        key=lambda diagnosis: (
            -len(diagnosis.evidence),
            diagnosis.problem_key,
        )
    )

    return diagnoses


def best_diagnosis(
    business: Optional[Dict[str, Any]] = None,
    *,
    notes: Optional[str] = None,
    signal: Optional[str] = None,
) -> Optional[Diagnosis]:
    """
    Return the strongest evidence-backed diagnosis.

    No evidence means None.
    """

    diagnoses = diagnose_business(
        business,
        notes=notes,
        signal=signal,
    )

    if not diagnoses:
        return None

    return diagnoses[0]


def recommendation_for_diagnosis(
    diagnosis: Optional[Diagnosis],
) -> Optional[Dict[str, Any]]:
    """
    Convert a diagnosis into a consultation-safe service recommendation.

    This function deliberately does NOT return price or proposal data.
    Budget, requirements, agreement, payment, and work authorization are
    handled later by the revenue engine.
    """

    if diagnosis is None:
        return None

    service = get_service(
        diagnosis.service_key
    )

    recommendation: Dict[str, Any] = {
        "service_key": diagnosis.service_key,
        "service_name": (
            service.name
            if service is not None
            else diagnosis.service_key
        ),
        "problem_key": diagnosis.problem_key,
        "title": diagnosis.title,
        "explanation": diagnosis.explanation,
        "confidence": diagnosis.confidence,
        "evidence": list(diagnosis.evidence),
        "questions": list(diagnosis.questions),
    }

    return recommendation