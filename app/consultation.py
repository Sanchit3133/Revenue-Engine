from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class ConsultationStage(str, Enum):
    """
    Stages of a business conversation.

    The order matters. The engine should not jump directly from first contact
    to proposal or payment.
    """

    FIRST_CONTACT = "FIRST_CONTACT"
    REPLIED = "REPLIED"
    CONSULTATION = "CONSULTATION"
    PROBLEM_IDENTIFIED = "PROBLEM_IDENTIFIED"
    SOLUTION_EXPLAINED = "SOLUTION_EXPLAINED"
    IMPLEMENTATION_INTEREST = "IMPLEMENTATION_INTEREST"
    REQUIREMENTS = "REQUIREMENTS"
    BUDGET_DISCLOSED = "BUDGET_DISCLOSED"
    PROPOSAL = "PROPOSAL"
    PAID = "PAID"

    NOT_INTERESTED = "NOT_INTERESTED"
    UNSUBSCRIBED = "UNSUBSCRIBED"
    BOUNCED = "BOUNCED"
    COMPLAINT = "COMPLAINT"
    DISQUALIFIED = "DISQUALIFIED"
    SUPPRESSED = "SUPPRESSED"


@dataclass(frozen=True)
class ConsultationStep:
    """
    Instructions for the next appropriate step in a conversation.
    """

    stage: ConsultationStage
    objective: str
    question: Optional[str] = None
    explanation: Optional[str] = None
    next_stage: Optional[ConsultationStage] = None


# ---------------------------------------------------------------------------
# Sales rules
# ---------------------------------------------------------------------------

MAX_FOLLOW_UPS = 2

SALES_RULES = {
    "consultation_first": True,
    "ask_before_recommending": True,
    "no_unsolicited_discount": True,
    "no_work_without_agreement": True,
    "no_proposal_before_requirements": True,
    "no_proposal_before_budget": True,
    "maximum_follow_ups": MAX_FOLLOW_UPS,
}


# ---------------------------------------------------------------------------
# Consultation questions
# ---------------------------------------------------------------------------

CONSULTATION_QUESTIONS: Dict[str, List[str]] = {
    "general": [
        "How are you currently handling this process?",
        "What happens after a customer makes an enquiry?",
        "Which part of the process takes the most manual effort?",
    ],
    "website": [
        "How do you currently turn website visitors into enquiries?",
        "Do you use a dedicated page for your main offer?",
        "Where do people go after they click your advertisements or promotions?",
    ],
    "lead_capture": [
        "How do you currently collect enquiry details?",
        "Are enquiries stored in one place or across different channels?",
        "What information do you normally collect from a new enquiry?",
    ],
    "payment": [
        "How do customers currently make payments?",
        "Is there a single place where customers can complete the payment process?",
        "Do staff members have to manually explain the payment process?",
    ],
    "automation": [
        "Which parts of this process does your team repeat manually?",
        "Are the systems you use connected to each other?",
        "Is there any information your team has to enter more than once?",
    ],
    "follow_up": [
        "What happens when an enquiry does not enroll or purchase immediately?",
        "Do you have a defined follow-up schedule?",
        "How does your team know which enquiries need another follow-up?",
    ],
}


# ---------------------------------------------------------------------------
# Educational explanations
# ---------------------------------------------------------------------------

EDUCATIONAL_EXPLANATIONS: Dict[str, str] = {
    "website": (
        "A focused landing page can make the next action clearer for a visitor. "
        "Instead of asking someone to navigate through a large website, the "
        "page can focus on one offer, explain the important information, and "
        "provide a clear enquiry or contact action."
    ),
    "lead_capture": (
        "A structured lead-capture process can make enquiry information easier "
        "to organize. The goal is not simply to collect more information, but "
        "to make sure the useful information reaches the right person without "
        "unnecessary manual work."
    ),
    "payment": (
        "A simpler payment flow can reduce the number of steps a customer has "
        "to understand before completing a payment. The exact setup depends on "
        "the business's existing payment provider and process."
    ),
    "auto_response": (
        "An automated first response does not need to replace the sales team. "
        "It can acknowledge a legitimate enquiry immediately and provide the "
        "next appropriate step while the human team handles the actual "
        "conversation."
    ),
    "follow_up": (
        "A follow-up system can help make sure legitimate enquiries do not "
        "disappear simply because the team is busy. The objective is not to "
        "send more messages, but to make appropriate follow-up more consistent."
    ),
    "automation": (
        "Automation is most useful when it removes repetitive work from a "
        "process that already makes sense. The first step should therefore be "
        "understanding the workflow rather than automating everything."
    ),
    "general": (
        "The useful starting point is normally understanding the existing "
        "process. Once the actual bottleneck is clear, a small targeted "
        "improvement can often be more useful than adding unnecessary tools."
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(value: object) -> str:
    if value is None:
        return ""

    return " ".join(str(value).lower().split())


def _is_positive(value: object) -> bool:
    """
    Conservative interpretation of implementation interest.

    The engine should not assume interest from silence or vague replies.
    """

    text = _normalize(value)

    positive_terms = [
        "yes",
        "sure",
        "interested",
        "sounds good",
        "tell me more",
        "how much",
        "can you build",
        "can you do it",
        "let's do it",
        "would like",
        "want to implement",
        "implement it",
    ]

    return any(term in text for term in positive_terms)


def _is_negative(value: object) -> bool:
    text = _normalize(value)

    negative_terms = [
        "no",
        "not interested",
        "don't need",
        "do not need",
        "not required",
        "remove me",
        "stop",
        "unsubscribe",
        "never contact",
    ]

    return any(term in text for term in negative_terms)


def _category_for_service(service_key: Optional[str]) -> str:
    if not service_key:
        return "general"

    service_key = service_key.upper()

    if service_key == "LANDING_PAGE":
        return "website"

    if service_key == "PAYMENT_PAGE":
        return "payment"

    if service_key == "LEAD_CAPTURE":
        return "lead_capture"

    if service_key == "AUTO_RESPONSE":
        return "auto_response"

    if service_key == "FOLLOW_UP_SYSTEM":
        return "follow_up"

    if service_key in {
        "BUSINESS_AUTOMATION",
        "CUSTOM_AUTOMATION",
        "CUSTOM_OUTREACH_SYSTEM",
    }:
        return "automation"

    return "general"


# ---------------------------------------------------------------------------
# First contact
# ---------------------------------------------------------------------------

def first_contact_message(
    business_name: str,
    observation: Optional[str] = None,
) -> str:
    """
    Generate a consultation-first opening.

    This intentionally avoids:
    - price,
    - discount,
    - proposal,
    - aggressive sales language.
    """

    name = business_name.strip() if business_name else "your organization"

    if observation:
        return (
            f"Hi, I was looking at how {name} handles enquiries online and "
            f"noticed {observation}. I had a quick question: how do you "
            f"currently handle this after the initial enquiry?"
        )

    return (
        f"Hi, I was looking at how {name} handles enquiries online and had "
        f"a quick question. When someone makes an enquiry but doesn't "
        f"enroll or purchase immediately, how do you currently handle "
        f"the follow-up?"
    )


# ---------------------------------------------------------------------------
# Stage progression
# ---------------------------------------------------------------------------

def next_step(
    current_stage: ConsultationStage,
    *,
    reply: Optional[str] = None,
    problem_identified: bool = False,
    solution_explained: bool = False,
    implementation_interest: bool = False,
    requirements_collected: bool = False,
    budget_disclosed: bool = False,
    written_agreement: bool = False,
    follow_up_count: int = 0,
) -> ConsultationStep:
    """
    Decide what the conversation should do next.

    This is the central rule engine.

    It deliberately prevents shortcuts such as:
        FIRST_CONTACT -> PROPOSAL
        REPLIED -> PAYMENT
        PROBLEM_IDENTIFIED -> PRICE

    without the required intermediate conversation.
    """

    # ---------------------------------------------------------------
    # Terminal states
    # ---------------------------------------------------------------

    if current_stage in {
        ConsultationStage.PAID,
        ConsultationStage.NOT_INTERESTED,
        ConsultationStage.UNSUBSCRIBED,
        ConsultationStage.BOUNCED,
        ConsultationStage.COMPLAINT,
        ConsultationStage.DISQUALIFIED,
        ConsultationStage.SUPPRESSED,
    }:
        return ConsultationStep(
            stage=current_stage,
            objective="No further sales action.",
        )

    # ---------------------------------------------------------------
    # Safety / suppression
    # ---------------------------------------------------------------

    if reply and _is_negative(reply):
        if "unsubscribe" in _normalize(reply) or "remove me" in _normalize(reply):
            return ConsultationStep(
                stage=ConsultationStage.UNSUBSCRIBED,
                objective="Stop communication and suppress future outreach.",
            )

        if "not interested" in _normalize(reply):
            return ConsultationStep(
                stage=ConsultationStage.NOT_INTERESTED,
                objective="Respect the decision and stop the sales conversation.",
            )

    # ---------------------------------------------------------------
    # First contact
    # ---------------------------------------------------------------

    if current_stage == ConsultationStage.FIRST_CONTACT:
        return ConsultationStep(
            stage=ConsultationStage.FIRST_CONTACT,
            objective="Start a business conversation rather than make a sales pitch.",
            question=(
                "How do you currently handle an enquiry after the initial "
                "conversation if the person does not enroll immediately?"
            ),
            next_stage=ConsultationStage.REPLIED,
        )

    # ---------------------------------------------------------------
    # Reply received
    # ---------------------------------------------------------------

    if current_stage == ConsultationStage.REPLIED:
        return ConsultationStep(
            stage=ConsultationStage.CONSULTATION,
            objective="Understand the existing process before recommending anything.",
            question=(
                "That makes sense. Could you walk me through what normally "
                "happens from the first enquiry until the person either "
                "enrolls or stops responding?"
            ),
            next_stage=ConsultationStage.CONSULTATION,
        )

    # ---------------------------------------------------------------
    # Consultation
    # ---------------------------------------------------------------

    if current_stage == ConsultationStage.CONSULTATION:
        if problem_identified:
            return ConsultationStep(
                stage=ConsultationStage.PROBLEM_IDENTIFIED,
                objective="Confirm that the identified problem is actually relevant.",
                question=(
                    "So the main difficulty is [PROBLEM]. Is that the part "
                    "you would most like to improve?"
                ),
                next_stage=ConsultationStage.PROBLEM_IDENTIFIED,
            )

        return ConsultationStep(
            stage=ConsultationStage.CONSULTATION,
            objective="Ask another diagnostic question.",
            question=(
                "Which part of the current process creates the most manual "
                "work or causes the most difficulty for your team?"
            ),
            next_stage=ConsultationStage.CONSULTATION,
        )

    # ---------------------------------------------------------------
    # Problem identified
    # ---------------------------------------------------------------

    if current_stage == ConsultationStage.PROBLEM_IDENTIFIED:
        if solution_explained:
            return ConsultationStep(
                stage=ConsultationStage.SOLUTION_EXPLAINED,
                objective="Explain a practical solution without immediately selling it.",
                explanation=(
                    "There may be a relatively simple way to improve that "
                    "part of the process. I can explain what the workflow "
                    "could look like and what would need to be integrated."
                ),
                next_stage=ConsultationStage.SOLUTION_EXPLAINED,
            )

        return ConsultationStep(
            stage=ConsultationStage.PROBLEM_IDENTIFIED,
            objective="Confirm the problem before discussing implementation.",
            question=(
                "How much of an issue is this for your team at the moment?"
            ),
            next_stage=ConsultationStage.PROBLEM_IDENTIFIED,
        )

    # ---------------------------------------------------------------
    # Solution explained
    # ---------------------------------------------------------------

    if current_stage == ConsultationStage.SOLUTION_EXPLAINED:
        return ConsultationStep(
            stage=ConsultationStage.SOLUTION_EXPLAINED,
            objective="Get permission before transitioning from education to implementation.",
            question=(
                "If you'd like, I can explain what a simple implementation "
                "of this could look like for your organization. Would that "
                "be useful?"
            ),
            next_stage=ConsultationStage.IMPLEMENTATION_INTEREST,
        )

    # ---------------------------------------------------------------
    # Implementation interest
    # ---------------------------------------------------------------

    if current_stage == ConsultationStage.IMPLEMENTATION_INTEREST:
        if implementation_interest or _is_positive(reply):
            return ConsultationStep(
                stage=ConsultationStage.REQUIREMENTS,
                objective="Understand the required implementation before discussing price.",
                question=(
                    "Sure. Before discussing implementation, could you tell "
                    "me a little about your current setup and what you would "
                    "want the system to do?"
                ),
                next_stage=ConsultationStage.REQUIREMENTS,
            )

        return ConsultationStep(
            stage=ConsultationStage.IMPLEMENTATION_INTEREST,
            objective="Do not pressure the prospect.",
            question=(
                "No problem. If you ever want to explore it, I can explain "
                "the workflow without any obligation."
            ),
            next_stage=ConsultationStage.IMPLEMENTATION_INTEREST,
        )

    # ---------------------------------------------------------------
    # Requirements
    # ---------------------------------------------------------------

    if current_stage == ConsultationStage.REQUIREMENTS:
        if requirements_collected:
            return ConsultationStep(
                stage=ConsultationStage.BUDGET_DISCLOSED,
                objective="Understand the prospect's budget before preparing a proposal.",
                question=(
                    "Based on the scope you've described, what budget range "
                    "have you set aside for implementing this?"
                ),
                next_stage=ConsultationStage.BUDGET_DISCLOSED,
            )

        return ConsultationStep(
            stage=ConsultationStage.REQUIREMENTS,
            objective="Collect enough information to understand the actual scope.",
            question=(
                "What would you want the finished system to handle, and "
                "which existing tools would it need to work with?"
            ),
            next_stage=ConsultationStage.REQUIREMENTS,
        )

    # ---------------------------------------------------------------
    # Budget
    # ---------------------------------------------------------------

    if current_stage == ConsultationStage.BUDGET_DISCLOSED:
        if budget_disclosed:
            return ConsultationStep(
                stage=ConsultationStage.PROPOSAL,
                objective="Prepare a proposal based on confirmed requirements and budget.",
                next_stage=ConsultationStage.PROPOSAL,
            )

        return ConsultationStep(
            stage=ConsultationStage.BUDGET_DISCLOSED,
            objective="Obtain the prospect's budget before proposing a price.",
            question=(
                "What budget range have you allocated for this project?"
            ),
            next_stage=ConsultationStage.BUDGET_DISCLOSED,
        )

    # ---------------------------------------------------------------
    # Proposal
    # ---------------------------------------------------------------

    if current_stage == ConsultationStage.PROPOSAL:
        if not budget_disclosed:
            return ConsultationStep(
                stage=ConsultationStage.BUDGET_DISCLOSED,
                objective="Do not send a proposal before budget is disclosed.",
                question=(
                    "Before I prepare the proposal, what budget range are "
                    "you working with?"
                ),
                next_stage=ConsultationStage.BUDGET_DISCLOSED,
            )

        return ConsultationStep(
            stage=ConsultationStage.PROPOSAL,
            objective="Present the agreed scope and price clearly without over-explaining.",
            next_stage=ConsultationStage.PAID,
        )

    # ---------------------------------------------------------------
    # Fallback
    # ---------------------------------------------------------------

    return ConsultationStep(
        stage=current_stage,
        objective="Continue the conversation based on verified information.",
    )


# ---------------------------------------------------------------------------
# Educational content
# ---------------------------------------------------------------------------

def educational_explanation(
    service_key: Optional[str] = None,
    category: Optional[str] = None,
) -> str:
    """
    Return a neutral explanation of a possible solution.

    This is intentionally educational rather than promotional.
    """

    if category:
        selected_category = _normalize(category)

        if selected_category in EDUCATIONAL_EXPLANATIONS:
            return EDUCATIONAL_EXPLANATIONS[selected_category]

    selected_category = _category_for_service(service_key)

    return EDUCATIONAL_EXPLANATIONS.get(
        selected_category,
        EDUCATIONAL_EXPLANATIONS["general"],
    )


# ---------------------------------------------------------------------------
# Consultation questions
# ---------------------------------------------------------------------------

def consultation_questions(
    service_key: Optional[str] = None,
) -> List[str]:
    """
    Return questions relevant to a possible service.

    Questions are designed to discover the actual business process rather
    than manufacture a problem.
    """

    category = _category_for_service(service_key)

    questions = list(
        CONSULTATION_QUESTIONS.get(
            category,
            CONSULTATION_QUESTIONS["general"],
        )
    )

    if not questions:
        questions = list(CONSULTATION_QUESTIONS["general"])

    return questions


# ---------------------------------------------------------------------------
# Follow-up control
# ---------------------------------------------------------------------------

def can_follow_up(follow_up_count: int) -> bool:
    """
    Enforce the maximum of two follow-ups.
    """

    try:
        count = int(follow_up_count)
    except (TypeError, ValueError):
        return False

    return 0 <= count < MAX_FOLLOW_UPS


def follow_up_message(
    business_name: str,
    follow_up_number: int,
) -> Optional[str]:
    """
    Generate a restrained follow-up.

    Returns None when the two-follow-up limit has been reached.
    """

    if not can_follow_up(follow_up_number):
        return None

    name = business_name.strip() if business_name else "there"

    if follow_up_number == 0:
        return (
            f"Hi, just following up on my earlier question, {name}. "
            "If this is something you already have handled, no problem."
        )

    return (
        f"Hi, one final follow-up, {name}. If improving the process is "
        "something you'd like to discuss, I'm happy to have a short "
        "conversation. Otherwise, no worries."
    )


# ---------------------------------------------------------------------------
# Price/proposal protection
# ---------------------------------------------------------------------------

def price_discussion_allowed(
    *,
    budget_disclosed: bool,
    requirements_collected: bool,
) -> bool:
    """
    Determine whether a price discussion is appropriate.

    Both requirements and budget must be established.

    The function does NOT return a price.
    """

    return bool(
        budget_disclosed
        and requirements_collected
    )


def proposal_allowed(
    *,
    budget_disclosed: bool,
    requirements_collected: bool,
) -> bool:
    """
    A proposal requires both a known scope and disclosed budget.
    """

    return bool(
        budget_disclosed
        and requirements_collected
    )


def work_allowed(
    *,
    written_agreement: bool,
) -> bool:
    """
    Work must not begin based only on a conversation.

    A clear written agreement/authentication is required.
    """

    return bool(written_agreement)


# ---------------------------------------------------------------------------
# Conversation state helper
# ---------------------------------------------------------------------------

def summarize_rules() -> Dict[str, object]:
    """
    Return the active sales rules for use by other Revenue Engine modules.
    """

    return dict(SALES_RULES)