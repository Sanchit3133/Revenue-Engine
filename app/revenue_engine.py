from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.consultation import (
    CONSULTATION_QUESTIONS,
    ConsultationStage,
    ConsultationStep,
    MAX_FOLLOW_UPS,
    first_contact_message,
    next_step,
)

from app.diagnosis import Diagnosis, best_diagnosis
from app.services import get_service


# ============================================================================
# LEAD PROFILE
# ============================================================================

@dataclass
class LeadProfile:
    """
    Information known about a business lead.

    Normal business identity fields such as name, category, website and city
    are NOT treated as evidence of a business problem.

    Evidence must come from description, signal, signals or notes.
    """

    name: str = ""
    email: str = ""
    website: str = ""
    description: str = ""
    city: str = ""
    category: str = ""
    address: str = ""
    phone: str = ""
    place_id: str = ""

    signals: List[str] = field(default_factory=list)
    signal: str = ""
    notes: str = ""

    def __post_init__(self):
        if self.signals is None:
            self.signals = []

        if isinstance(self.signals, str):
            self.signals = [self.signals]

        self.signals = [
            str(item).strip()
            for item in self.signals
            if item is not None and str(item).strip()
        ]

        if self.signal is None:
            self.signal = ""

        self.signal = str(self.signal).strip()

        if self.signal and self.signal not in self.signals:
            self.signals.insert(0, self.signal)

    def evidence_text(self) -> str:
        """
        Return only actual problem evidence.

        Name, category, website, city, address and phone are deliberately
        excluded. Their presence alone must never create a diagnosis.
        """

        parts: List[str] = []

        if self.description and self.description.strip():
            parts.append(self.description.strip())

        if self.signal and self.signal.strip():
            parts.append(self.signal.strip())

        for item in self.signals:
            if item and item.strip():
                parts.append(item.strip())

        if self.notes and self.notes.strip():
            parts.append(self.notes.strip())

        return " ".join(parts).strip()


# ============================================================================
# ENGINE STATE
# ============================================================================

@dataclass
class RevenueEngineState:
    lead: LeadProfile

    stage: ConsultationStage = ConsultationStage.FIRST_CONTACT

    follow_up_count: int = 0

    requirements: Dict[str, Any] = field(default_factory=dict)

    budget: Optional[str] = None

    prospect_confirmed_problem: Optional[str] = None

    confirmed_problem: Optional[str] = None

    written_agreement: bool = False

    implementation_interest: bool = False

    diagnosis: Optional[Diagnosis] = None

    history: List[Dict[str, Any]] = field(default_factory=list)


# ============================================================================
# RECOMMENDATION
# ============================================================================

@dataclass
class EngineRecommendation:
    """
    Public recommendation returned by RevenueEngine.
    """

    stage: ConsultationStage

    message: str

    action: str

    objective: str

    question: Optional[str] = None

    explanation: Optional[str] = None

    next_stage: Optional[ConsultationStage] = None

    metadata: Dict[str, Any] = field(default_factory=dict)

    diagnosis: Optional[Diagnosis] = None


# ============================================================================
# REVENUE ENGINE
# ============================================================================

class RevenueEngine:
    """
    Consultation-first revenue engine.

    Rules:

    1. First contact is consultation-first.
    2. Do not invent a business problem without evidence.
    3. Do not disclose price before requirements and budget.
    4. Do not create a proposal before requirements and budget.
    5. Do not start work without written agreement.
    6. Maximum two follow-ups.
    7. Do not proactively disclose discounts.
    """

    def __init__(
        self,
        lead: LeadProfile,
        stage: ConsultationStage = ConsultationStage.FIRST_CONTACT,
    ):
        self.state = RevenueEngineState(
            lead=lead,
            stage=stage,
        )

    # ========================================================================
    # EVIDENCE
    # ========================================================================

    def evidence_text(self) -> str:
        return self.state.lead.evidence_text()

    # ========================================================================
    # DIAGNOSIS
    # ========================================================================

    def analyze_lead(self) -> List[Diagnosis]:
        """
        Return all evidence-backed diagnoses.

        If there is no actual evidence, return [].

        The diagnosis engine itself works with a singular `signal` argument,
        so plural LeadProfile signals are combined and passed through that
        argument.
        """

        evidence = self.evidence_text()

        if not evidence:
            self.state.diagnosis = None
            return []

        combined_signals = []

        if self.state.lead.signal:
            combined_signals.append(
                self.state.lead.signal
            )

        for item in self.state.lead.signals:
            if item and item not in combined_signals:
                combined_signals.append(item)

        combined_signal_text = " ".join(
            combined_signals
        ).strip()

        business = {
            "description": self.state.lead.description,
            "signal": combined_signal_text,
        }

        diagnosis = best_diagnosis(
            business=business,
            notes=self.state.lead.notes,
            signal=combined_signal_text,
        )

        if diagnosis is None:
            self.state.diagnosis = None
            return []

        self.state.diagnosis = diagnosis

        return [diagnosis]

    def best_lead_diagnosis(self) -> Optional[Diagnosis]:
        """
        Return the strongest available diagnosis.
        """

        result = self.analyze_lead()

        if isinstance(result, list):
            if not result:
                return None

            return result[0]

        return result

    def current_diagnosis(self) -> Optional[Diagnosis]:
        """
        Return the currently stored diagnosis, or calculate it if necessary.
        """

        if self.state.diagnosis is not None:
            return self.state.diagnosis

        return self.best_lead_diagnosis()

    # ========================================================================
    # SERVICE
    # ========================================================================

    def current_service(self):
        diagnosis = self.current_diagnosis()

        if diagnosis is None:
            return None

        if not diagnosis.service_key:
            return None

        return get_service(
            diagnosis.service_key
        )

    def recommended_service(self):
        return self.current_service()

    # ========================================================================
    # CONSULTATION QUESTIONS
    # ========================================================================

    def consultation_questions(self) -> List[str]:
        """
        Return the available general consultation questions.
        """

        questions = CONSULTATION_QUESTIONS.get(
            "general",
            [],
        )

        return list(questions)

    # ========================================================================
    # FIRST CONTACT
    # ========================================================================

    def first_contact(self) -> EngineRecommendation:
        diagnosis = self.current_diagnosis()

        observation = None

        if diagnosis is not None and diagnosis.reasons:
            observation = diagnosis.reasons[0].replace(
                "Observed signal:",
                "",
            ).strip()

        message = first_contact_message(
            self.state.lead.name,
            observation=observation,
        )

        recommendation = EngineRecommendation(
            stage=ConsultationStage.FIRST_CONTACT,
            message=message,
            action="CONTACT",
            objective=(
                "Start a business conversation rather than make a sales pitch."
            ),
            question=(
                "How do you currently handle an enquiry after the initial "
                "conversation if the person does not enroll immediately?"
            ),
            explanation=None,
            next_stage=ConsultationStage.REPLIED,
            metadata={
                "proposal_included": False,
                "price_included": False,
                "discount_included": False,
                "conversation_closed": False,
            },
            diagnosis=diagnosis,
        )

        self.state.history.append(
            {
                "type": "first_contact",
                "stage": self.state.stage.value,
            }
        )

        return recommendation

    # ========================================================================
    # RECOMMENDATION WRAPPER
    # ========================================================================

    def _recommendation_from_step(
        self,
        step: ConsultationStep,
        action: Optional[str] = None,
    ) -> EngineRecommendation:

        terminal_stages = {
            ConsultationStage.NOT_INTERESTED,
            ConsultationStage.UNSUBSCRIBED,
            ConsultationStage.BOUNCED,
            ConsultationStage.COMPLAINT,
            ConsultationStage.DISQUALIFIED,
            ConsultationStage.SUPPRESSED,
        }

        if action is None:
            if step.stage in terminal_stages:
                action = "STOP"
            else:
                action = "CONTINUE"

        diagnosis = self.current_diagnosis()

        conversation_closed = (
            step.stage in terminal_stages
        )

        metadata = {
            "proposal_included": False,
            "price_included": False,
            "discount_included": False,
            "conversation_closed": conversation_closed,
        }

        return EngineRecommendation(
            stage=step.stage,
            message=(
                step.question
                or step.objective
                or "No further action is required."
            ),
            action=action,
            objective=step.objective,
            question=step.question,
            explanation=step.explanation,
            next_stage=step.next_stage,
            metadata=metadata,
            diagnosis=diagnosis,
        )

    # ========================================================================
    # REPLY PROCESSING
    # ========================================================================

    def process_reply(
        self,
        reply_text: str,
    ) -> EngineRecommendation:
        """
        Process a prospect reply and move the conversation forward.
        """

        reply_text = (
            reply_text or ""
        ).strip()

        current_stage = self.state.stage

        if current_stage == ConsultationStage.FIRST_CONTACT:
            current_stage = ConsultationStage.REPLIED

        step = next_step(
            current_stage,
            reply=reply_text,
            problem_identified=(
                self.state.prospect_confirmed_problem is not None
                or self.state.confirmed_problem is not None
            ),
            solution_explained=(
                self.state.stage
                == ConsultationStage.SOLUTION_EXPLAINED
            ),
            implementation_interest=(
                self.state.implementation_interest
            ),
            requirements_collected=(
                self.requirements_complete()
            ),
            budget_disclosed=(
                self.budget_disclosed()
            ),
            written_agreement=(
                self.has_written_agreement()
            ),
            follow_up_count=(
                self.state.follow_up_count
            ),
        )

        self.state.stage = step.stage

        self.state.history.append(
            {
                "type": "reply",
                "reply": reply_text,
                "stage": step.stage.value,
            }
        )

        recommendation = self._recommendation_from_step(
            step
        )

        terminal_stages = {
            ConsultationStage.NOT_INTERESTED,
            ConsultationStage.UNSUBSCRIBED,
            ConsultationStage.BOUNCED,
            ConsultationStage.COMPLAINT,
            ConsultationStage.DISQUALIFIED,
            ConsultationStage.SUPPRESSED,
        }

        recommendation.metadata[
            "conversation_closed"
        ] = (
            step.stage in terminal_stages
        )

        if recommendation.metadata[
            "conversation_closed"
        ]:
            recommendation.action = "STOP"

        return recommendation

    # ========================================================================
    # PROBLEM CONFIRMATION
    # ========================================================================

    def confirm_problem(
        self,
        problem: str,
    ) -> None:
        """
        Record that the prospect confirmed the problem.
        """

        value = (
            problem or ""
        ).strip()

        if value:
            self.state.prospect_confirmed_problem = value
            self.state.confirmed_problem = value
            self.state.stage = (
                ConsultationStage.PROBLEM_IDENTIFIED
            )
        else:
            self.state.prospect_confirmed_problem = None
            self.state.confirmed_problem = None

        self.state.history.append(
            {
                "type": "problem_confirmed",
                "problem": value,
            }
        )

    # ========================================================================
    # SOLUTION EXPLANATION
    # ========================================================================

    def mark_solution_explained(
        self,
        explained: bool = True,
    ) -> None:

        if explained:
            self.state.stage = (
                ConsultationStage.SOLUTION_EXPLAINED
            )

        self.state.history.append(
            {
                "type": "solution_explained",
                "value": bool(explained),
            }
        )

    # ========================================================================
    # IMPLEMENTATION INTEREST
    # ========================================================================

    def record_implementation_interest(
        self,
        interested: bool = True,
    ) -> None:

        self.state.implementation_interest = bool(
            interested
        )

        if interested:
            self.state.stage = (
                ConsultationStage.IMPLEMENTATION_INTEREST
            )

        self.state.history.append(
            {
                "type": "implementation_interest",
                "value": bool(interested),
            }
        )

    # ========================================================================
    # REQUIREMENTS
    # ========================================================================

    def record_requirements(
        self,
        requirements: Optional[Dict[str, Any]],
    ) -> None:

        self.state.requirements = dict(
            requirements or {}
        )

        if self.requirements_complete():
            self.state.stage = (
                ConsultationStage.REQUIREMENTS
            )

        self.state.history.append(
            {
                "type": "requirements",
                "requirements": dict(
                    self.state.requirements
                ),
            }
        )

    def set_requirements(
        self,
        requirements: Optional[Dict[str, Any]],
    ) -> None:
        """
        Compatibility alias.
        """

        self.record_requirements(
            requirements
        )

    def requirements_complete(self) -> bool:
        if not self.state.requirements:
            return False

        for value in self.state.requirements.values():
            if value is None:
                continue

            if isinstance(value, str):
                if value.strip():
                    return True
            else:
                if value:
                    return True

        return False

    # ========================================================================
    # BUDGET
    # ========================================================================

    def record_budget(
        self,
        budget: Optional[str],
    ) -> None:

        if budget is None:
            self.state.budget = None
        else:
            value = str(
                budget
            ).strip()

            self.state.budget = (
                value or None
            )

        if self.state.budget:
            self.state.stage = (
                ConsultationStage.BUDGET_DISCLOSED
            )

        self.state.history.append(
            {
                "type": "budget",
                "budget": self.state.budget,
            }
        )

    def set_budget(
        self,
        budget: Optional[str],
    ) -> None:
        """
        Compatibility alias.
        """

        self.record_budget(
            budget
        )

    def budget_disclosed(self) -> bool:
        return bool(
            self.state.budget
            and str(
                self.state.budget
            ).strip()
        )

    # ========================================================================
    # PRICE RULES
    # ========================================================================

    def can_discuss_price(self) -> bool:
        """
        Price discussion requires:
        - requirements
        - prospect-disclosed budget
        """

        return (
            self.requirements_complete()
            and self.budget_disclosed()
        )

    def can_quote_price(self) -> bool:
        return self.can_discuss_price()

    def price(
        self,
        amount: Any,
    ) -> Any:

        if not self.can_discuss_price():
            raise PermissionError(
                "Price cannot be discussed before "
                "requirements and budget are recorded."
            )

        return amount

    # ========================================================================
    # PROPOSAL RULES
    # ========================================================================

    def can_make_proposal(self) -> bool:
        return (
            self.requirements_complete()
            and self.budget_disclosed()
        )

    def can_create_proposal(self) -> bool:
        return self.can_make_proposal()

    def create_proposal(
        self,
        proposal: Any,
    ) -> Any:

        if not self.can_make_proposal():
            raise PermissionError(
                "Proposal cannot be created before "
                "requirements and budget are recorded."
            )

        self.state.stage = (
            ConsultationStage.PROPOSAL
        )

        self.state.history.append(
            {
                "type": "proposal",
            }
        )

        return proposal

    # ========================================================================
    # WRITTEN AGREEMENT
    # ========================================================================

    def record_written_agreement(
        self,
        agreed: bool = True,
    ) -> None:

        self.state.written_agreement = bool(
            agreed
        )

        self.state.history.append(
            {
                "type": "written_agreement",
                "value": bool(agreed),
            }
        )

    def set_written_agreement(
        self,
        agreed: bool = True,
    ) -> None:
        """
        Compatibility alias.
        """

        self.record_written_agreement(
            agreed
        )

    def has_written_agreement(self) -> bool:
        return bool(
            self.state.written_agreement
        )

    # ========================================================================
    # WORK AUTHORIZATION
    # ========================================================================

    def can_start_work(self) -> bool:
        return self.has_written_agreement()

    def start_work(self) -> None:

        if not self.can_start_work():
            raise PermissionError(
                "Work cannot start without "
                "a written agreement."
            )

        self.state.history.append(
            {
                "type": "work_started",
            }
        )

    # ========================================================================
    # FOLLOW-UPS
    # ========================================================================

    def can_follow_up(self) -> bool:
        return (
            self.state.follow_up_count
            < MAX_FOLLOW_UPS
        )

    def record_follow_up(self) -> bool:

        if not self.can_follow_up():
            return False

        self.state.follow_up_count += 1

        self.state.history.append(
            {
                "type": "follow_up",
                "count": (
                    self.state.follow_up_count
                ),
            }
        )

        return True

    def register_follow_up(self) -> bool:
        """
        Compatibility alias.
        """

        return self.record_follow_up()

    def follow_up(self) -> bool:
        return self.record_follow_up()

    # ========================================================================
    # SNAPSHOT
    # ========================================================================

    def snapshot(self) -> Dict[str, Any]:

        diagnosis = self.current_diagnosis()

        service = self.current_service()

        diagnosis_data = None

        if diagnosis is not None:
            diagnosis_data = {
                "problem_key": (
                    diagnosis.problem_key
                ),
                "problem_name": (
                    diagnosis.problem_name
                ),
                "confidence": (
                    diagnosis.confidence
                ),
                "service_key": (
                    diagnosis.service_key
                ),
                "reasons": list(
                    diagnosis.reasons
                ),
                "questions": list(
                    diagnosis.questions
                ),
            }

        service_data = None

        if service is not None:
            service_data = {
                "key": getattr(
                    service,
                    "key",
                    diagnosis.service_key
                    if diagnosis
                    else None,
                ),
                "name": getattr(
                    service,
                    "name",
                    None,
                ),
            }

        return {
            "lead": {
                "name": (
                    self.state.lead.name
                ),
                "email": (
                    self.state.lead.email
                ),
                "website": (
                    self.state.lead.website
                ),
                "city": (
                    self.state.lead.city
                ),
                "category": (
                    self.state.lead.category
                ),
            },

            "stage": (
                self.state.stage.value
            ),

            "follow_up_count": (
                self.state.follow_up_count
            ),

            "max_follow_ups": (
                MAX_FOLLOW_UPS
            ),

            "requirements": dict(
                self.state.requirements
            ),

            "requirements_complete": (
                self.requirements_complete()
            ),

            "budget": (
                self.state.budget
            ),

            "budget_disclosed": (
                self.budget_disclosed()
            ),

            "prospect_confirmed_problem": (
                self.state.prospect_confirmed_problem
            ),

            "confirmed_problem": (
                self.state.confirmed_problem
            ),

            "written_agreement": (
                self.state.written_agreement
            ),

            "can_start_work": (
                self.can_start_work()
            ),

            "implementation_interest": (
                self.state.implementation_interest
            ),

            "can_discuss_price": (
                self.can_discuss_price()
            ),

            "can_make_proposal": (
                self.can_make_proposal()
            ),

            "diagnosis": diagnosis_data,

            "service": service_data,

            "recommended_service": service_data,

            "history_count": (
                len(self.state.history)
            ),
        }


# ============================================================================
# FACTORY
# ============================================================================

def create_engine(
    lead: LeadProfile,
    stage: ConsultationStage = ConsultationStage.FIRST_CONTACT,
) -> RevenueEngine:

    return RevenueEngine(
        lead=lead,
        stage=stage,
    )