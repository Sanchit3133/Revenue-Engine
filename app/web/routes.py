from flask import Blueprint, render_template, request, session

from app.consultation import (
    ConsultationStage,
    consultation_questions,
    educational_explanation,
    next_step,
)
from app.revenue_engine import LeadProfile, RevenueEngine


web_bp = Blueprint(
    "web",
    __name__,
    template_folder="templates",
)


def _get_session_data():
    return session.setdefault(
        "consultation",
        {
            "stage": ConsultationStage.FIRST_CONTACT.value,
            "business_name": "",
            "business_type": "",
            "website": "",
            "customer_process": "",
            "biggest_problem": "",
            "current_tools": "",
            "answers": [],
            "question_index": 0,
            "service_key": None,
            "problem_identified": False,
            "solution_explained": False,
            "implementation_interest": False,
            "requirements_collected": False,
            "budget_disclosed": False,
            "written_agreement": False,
            "follow_up_count": 0,
        },
    )


def _save_session_data(data):
    session["consultation"] = data
    session.modified = True


def _build_profile(data):
    description = (
        f"Business type: {data.get('business_type', '')}\n"
        f"Customer/enquiry process: {data.get('customer_process', '')}\n"
        f"Prospect-reported problem: {data.get('biggest_problem', '')}\n"
        f"Current tools/process: {data.get('current_tools', '')}\n"
        f"Consultation answers: "
        f"{' | '.join(data.get('answers', []))}"
    )

    signals = [
        data.get("business_type", ""),
        data.get("customer_process", ""),
        data.get("biggest_problem", ""),
        data.get("current_tools", ""),
    ]

    signals.extend(data.get("answers", []))

    signals = [
        value.strip()
        for value in signals
        if value and value.strip()
    ]

    return LeadProfile(
        name=data.get("business_name", ""),
        website=data.get("website", ""),
        description=description,
        category=data.get("business_type", ""),
        signals=signals,
        signal=data.get("biggest_problem", ""),
        notes=(
            "Information submitted directly by the prospect "
            "through the Revenue Engine consultation."
        ),
    )


def _run_engine(data):
    profile = _build_profile(data)

    stage_value = data.get(
        "stage",
        ConsultationStage.FIRST_CONTACT.value,
    )

    try:
        stage = ConsultationStage(stage_value)
    except ValueError:
        stage = ConsultationStage.FIRST_CONTACT

    engine = RevenueEngine(
        profile,
        stage=stage,
    )

    diagnoses = engine.analyze_lead()

    return engine, diagnoses


@web_bp.get("/")
def home():
    return """
    <h1>Revenue Engine</h1>
    <p>
        <a href="/consultation">
            Start consultation
        </a>
    </p>
    """


@web_bp.route("/consultation", methods=["GET", "POST"])
def consultation():
    data = _get_session_data()

    error = None
    result = None
    question = None
    explanation = None

    if request.method == "POST":
        action = request.form.get("action", "").strip()

        # ---------------------------------------------------------
        # Start / restart consultation
        # ---------------------------------------------------------
        if action == "restart":
            session.pop("consultation", None)

            data = _get_session_data()

            return render_template(
                "consultation.html",
                data=data,
                question=None,
                explanation=None,
                result=None,
                error=None,
            )

        # ---------------------------------------------------------
        # Initial business information
        # ---------------------------------------------------------
        if action == "business_details":
            business_name = request.form.get(
                "business_name",
                "",
            ).strip()

            business_type = request.form.get(
                "business_type",
                "",
            ).strip()

            website = request.form.get(
                "website",
                "",
            ).strip()

            customer_process = request.form.get(
                "customer_process",
                "",
            ).strip()

            biggest_problem = request.form.get(
                "biggest_problem",
                "",
            ).strip()

            current_tools = request.form.get(
                "current_tools",
                "",
            ).strip()

            if (
                not business_name
                or not business_type
                or not biggest_problem
            ):
                error = (
                    "Please complete the required fields "
                    "before continuing."
                )

            else:
                data.update(
                    {
                        "business_name": business_name,
                        "business_type": business_type,
                        "website": website,
                        "customer_process": customer_process,
                        "biggest_problem": biggest_problem,
                        "current_tools": current_tools,
                        "stage": ConsultationStage.REPLIED.value,
                        "question_index": 0,
                    }
                )

                _save_session_data(data)

                return render_template(
                    "consultation.html",
                    data=data,
                    question=_get_next_question(data),
                    explanation=None,
                    result=None,
                    error=None,
                )

        # ---------------------------------------------------------
        # Consultation answer
        # ---------------------------------------------------------
        elif action == "answer":
            answer = request.form.get(
                "answer",
                "",
            ).strip()

            if not answer:
                error = "Please enter an answer before continuing."

            else:
                data.setdefault("answers", []).append(answer)

                data["stage"] = ConsultationStage.CONSULTATION.value

                data["question_index"] = (
                    int(data.get("question_index", 0)) + 1
                )

                _save_session_data(data)

                # Once enough direct evidence has been collected,
                # let the existing Revenue Engine analyze it.
                engine, diagnoses = _run_engine(data)

                if diagnoses:
                    data["problem_identified"] = True
                    data["stage"] = (
                        ConsultationStage.PROBLEM_IDENTIFIED.value
                    )

                    _save_session_data(data)

                    result = {
                        "analysis": "\n\n".join(
                            str(diagnosis)
                            for diagnosis in diagnoses
                        ),
                        "snapshot": engine.snapshot(),
                    }

                    explanation = _get_solution_explanation(
                        diagnoses,
                        data,
                    )

                else:
                    result = None

                question = _get_next_question(data)

        # ---------------------------------------------------------
        # Implementation interest
        # ---------------------------------------------------------
        elif action == "implementation_interest":
            interest = request.form.get(
                "implementation_interest",
                "",
            ).strip().lower()

            if interest == "yes":
                data["implementation_interest"] = True
                data["stage"] = (
                    ConsultationStage.IMPLEMENTATION_INTEREST.value
                )
            elif interest == "no":
                data["implementation_interest"] = False
                data["stage"] = (
                    ConsultationStage.NOT_INTERESTED.value
                )

            _save_session_data(data)

        # ---------------------------------------------------------
        # Requirements
        # ---------------------------------------------------------
        elif action == "requirements":
            requirements = request.form.get(
                "requirements",
                "",
            ).strip()

            if not requirements:
                error = (
                    "Please describe what you would want "
                    "the system or implementation to handle."
                )
            else:
                data["requirements_collected"] = True
                data["requirements"] = requirements
                data["stage"] = (
                    ConsultationStage.REQUIREMENTS.value
                )

                _save_session_data(data)

        # ---------------------------------------------------------
        # Budget
        # ---------------------------------------------------------
        elif action == "budget":
            budget = request.form.get(
                "budget",
                "",
            ).strip()

            if not budget:
                error = "Please enter your available budget."

            else:
                data["budget"] = budget
                data["budget_disclosed"] = True
                data["stage"] = (
                    ConsultationStage.BUDGET_DISCLOSED.value
                )

                _save_session_data(data)

        # ---------------------------------------------------------
        # Written agreement
        # ---------------------------------------------------------
        elif action == "agreement":
            agreement = request.form.get(
                "agreement",
                "",
            ).strip().lower()

            if agreement == "yes":
                data["written_agreement"] = True
                data["stage"] = (
                    ConsultationStage.PROPOSAL.value
                )

                _save_session_data(data)

    # -------------------------------------------------------------
    # GET / normal rendering
    # -------------------------------------------------------------

    if data.get("problem_identified"):
        try:
            engine, diagnoses = _run_engine(data)

            if diagnoses:
                result = {
                    "analysis": "\n\n".join(
                        str(diagnosis)
                        for diagnosis in diagnoses
                    ),
                    "snapshot": engine.snapshot(),
                }

                explanation = _get_solution_explanation(
                    diagnoses,
                    data,
                )

        except Exception as exc:
            error = (
                "The consultation could not complete this "
                f"assessment step: {exc}"
            )

    question = _get_next_question(data)

    return render_template(
        "consultation.html",
        data=data,
        question=question,
        explanation=explanation,
        result=result,
        error=error,
    )


def _get_next_question(data):
    """
    Select the next consultation question from the existing
    consultation question bank.
    """

    service_key = data.get("service_key")

    questions = consultation_questions(
        service_key=service_key,
    )

    if not questions:
        questions = consultation_questions()

    index = int(
        data.get("question_index", 0)
    )

    if index >= len(questions):
        return None

    return questions[index]


def _get_solution_explanation(diagnoses, data):
    """
    Use the existing educational explanation system where
    possible. Do not invent a service recommendation.
    """

    if not diagnoses:
        return None

    diagnosis = diagnoses[0]

    service_key = getattr(
        diagnosis,
        "service_key",
        None,
    )

    category = data.get("business_type")

    try:
        return educational_explanation(
            service_key=service_key,
            category=category,
        )
    except Exception:
        return None