from flask import Blueprint, render_template, request, session
from datetime import datetime, timezone
from email.utils import parseaddr
import json
import uuid
import secrets

from app.database import connect, init_db
from app.web.session_state import default_consultation, get_current_consultation, save_consultation
from app.consultation_handoff import resolve_handoff
import logging

from app.consultation import (
    ConsultationStage,
    consultation_questions,
    educational_explanation,
)
from app.revenue_engine import LeadProfile, RevenueEngine


web_bp = Blueprint(
    "web",
    __name__,
    template_folder="templates",
)


def _get_session_data():
    return get_current_consultation()


def _save_session_data(data):
    save_consultation(data)


def _valid_contact_email(value):
    email = (value or "").strip()
    _, parsed = parseaddr(email)
    if not parsed or parsed.lower() != email.lower() or "@" not in email:
        return False
    local, _, domain = email.rpartition("@")
    return bool(local and domain and "." in domain and not domain.startswith(".") and not domain.endswith("."))




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


def _format_diagnosis(diagnosis):
    """
    Convert the internal Diagnosis object into prospect-safe
    presentation data.

    Internal Python representation must never be shown directly
    to a prospect.
    """

    problem_name = getattr(
        diagnosis,
        "problem_name",
        None,
    )

    if not problem_name:
        problem_name = getattr(
            diagnosis,
            "title",
            "Potential process gap",
        )

    explanation = getattr(
        diagnosis,
        "explanation",
        "",
    )

    confidence = getattr(
        diagnosis,
        "confidence",
        "medium",
    )

    service_key = getattr(
        diagnosis,
        "service_key",
        "",
    )

    return {
        "problem_name": problem_name,
        "explanation": explanation,
        "confidence": confidence,
        "service_key": service_key,
    }


def _format_assessment(diagnoses):
    """
    Return only human-readable assessment information.

    This deliberately excludes internal keys, dataclass
    representations, raw evidence structures, and Python objects.
    """

    if not diagnoses:
        return []

    return [
        _format_diagnosis(diagnosis)
        for diagnosis in diagnoses
    ]


@web_bp.get("/")
def home():
    return render_template("home.html")


@web_bp.route("/consultation", methods=["GET", "POST"])
def consultation():
    data = _get_session_data()
    if not session.get("assistant_csrf_token"):
        session["assistant_csrf_token"] = secrets.token_urlsafe(32)
    assistant_csrf_token = session["assistant_csrf_token"]
    if not session.get("consultation_csrf_token"):
        session["consultation_csrf_token"] = secrets.token_urlsafe(32)
    consultation_csrf_token = session["consultation_csrf_token"]

    error = None
    result = None
    question = None
    explanation = None

    # An opaque, expiring handoff token may prefill the public form. Personal
    # details are not put in the URL and are not saved until the prospect consents.
    handoff_raw = (
        (request.form.get("handoff_token") or "").strip()
        if request.method == "POST"
        else (request.args.get("h") or "").strip()
    )
    if not data.get("business_name") and handoff_raw:
        with connect() as conn:
            handoff = resolve_handoff(conn, handoff_raw)
        if handoff:
            data["handoff_prefill"] = {
                "business_name": handoff["business_name"],
                "email": handoff["email"],
                "website": handoff["website"],
                "token": handoff_raw,
            }
        elif request.method == "GET":
            error = "This consultation link has expired or has already been used. You can still start a fresh consultation."

    if request.method == "POST" and not secrets.compare_digest(
        str(session.get("consultation_csrf_token", "")),
        str(request.form.get("consultation_csrf_token", "")),
    ):
        error = "This form expired or failed its security check. Refresh the page and try again."
    elif request.method == "POST":
        action = request.form.get("action", "").strip()

        # ---------------------------------------------------------
        # Start / restart consultation
        # ---------------------------------------------------------
        if action == "restart":
            session.pop("consultation", None)
            session.pop("consultation_public_id", None)

            data = _get_session_data()

            return render_template(
                "consultation.html",
                data=data,
                assistant_csrf_token=assistant_csrf_token,
                consultation_csrf_token=consultation_csrf_token,
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
            ).strip()[:160]

            contact_email = request.form.get("contact_email", "").strip()[:254]
            contact_consent = request.form.get("contact_consent") == "yes"
            submitted_handoff_token = (request.form.get("handoff_token") or "").strip()
            handoff = None
            if submitted_handoff_token:
                with connect() as conn:
                    handoff = resolve_handoff(conn, submitted_handoff_token)

            business_type = request.form.get(
                "business_type",
                "",
            ).strip()[:120]

            website = request.form.get(
                "website",
                "",
            ).strip()[:300]

            customer_process = request.form.get(
                "customer_process",
                "",
            ).strip()[:1000]

            biggest_problem = request.form.get(
                "biggest_problem",
                "",
            ).strip()[:1500]

            current_tools = request.form.get(
                "current_tools",
                "",
            ).strip()[:1000]

            if submitted_handoff_token and not handoff:
                error = "This consultation link has expired or has already been used. Open a fresh consultation link and try again."
            elif handoff and contact_email.strip().lower() != handoff["email"]:
                error = "For security, use the business email address associated with this consultation link."
            elif not business_name or not business_type or not biggest_problem:
                error = "Please complete the required fields before continuing."
            elif not _valid_contact_email(contact_email):
                error = "Please enter a valid business email address."
            elif not contact_consent:
                error = "Please confirm that we may use your details to respond to this consultation."

            else:
                data.update(
                    {
                        "business_name": business_name,
                        "contact_email": contact_email,
                        "contact_consent": True,
                        "lead_id": int(handoff["lead_id"]) if handoff else data.get("lead_id"),
                        "handoff_token_hash": handoff["token_hash"] if handoff else data.get("handoff_token_hash"),
                        "business_type": business_type,
                        "website": website,
                        "customer_process": customer_process,
                        "biggest_problem": biggest_problem,
                        "current_tools": current_tools,
                        "stage": ConsultationStage.REPLIED.value,
                        "question_index": 0,
                    }
                )

                data.pop("handoff_prefill", None)
                try:
                    _save_session_data(data)
                except ValueError:
                    data = default_consultation()
                    error = "This consultation link has expired or has already been used. Open a fresh consultation link and try again."
                    return render_template(
                        "consultation.html",
                        data=data,
                        assistant_csrf_token=assistant_csrf_token,
                        consultation_csrf_token=consultation_csrf_token,
                        question=None,
                        explanation=None,
                        result=None,
                        error=error,
                    )

                return render_template(
                    "consultation.html",
                    data=data,
                    assistant_csrf_token=assistant_csrf_token,
                    consultation_csrf_token=consultation_csrf_token,
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
            ).strip()[:1500]

            if not answer:
                error = "Please enter an answer before continuing."

            else:
                answers = data.setdefault("answers", [])
                if len(answers) >= 30:
                    error = "This consultation has reached its question limit. Please submit the requirements below or restart the consultation."
                    question = _get_next_question(data)
                    return render_template(
                        "consultation.html", data=data,
                        assistant_csrf_token=assistant_csrf_token,
                        consultation_csrf_token=consultation_csrf_token,
                        question=question, explanation=None, result=None, error=error,
                    )
                answers.append(answer)

                data["stage"] = (
                    ConsultationStage.CONSULTATION.value
                )

                data["question_index"] = (
                    int(data.get("question_index", 0)) + 1
                )

                _save_session_data(data)

                engine, diagnoses = _run_engine(data)

                if diagnoses:
                    data["problem_identified"] = True
                    data["stage"] = (
                        ConsultationStage.PROBLEM_IDENTIFIED.value
                    )

                    data["service_key"] = getattr(
                        diagnoses[0],
                        "service_key",
                        None,
                    )

                    _save_session_data(data)

                    result = {
                        "assessments": _format_assessment(
                            diagnoses
                        ),
                        "snapshot": engine.snapshot(),
                    }

                    explanation = _get_solution_explanation(
                        diagnoses,
                        data,
                    )

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
            ).strip()[:3000]

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
            ).strip()[:300]

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
                    "assessments": _format_assessment(
                        diagnoses
                    ),
                    "snapshot": engine.snapshot(),
                }

                explanation = _get_solution_explanation(
                    diagnoses,
                    data,
                )

        except Exception:
            logging.exception("Revenue Engine consultation assessment failed")
            error = "The consultation could not complete this assessment step right now. Your saved information is unchanged; please try again."

    question = _get_next_question(data)

    return render_template(
        "consultation.html",
        data=data,
        assistant_csrf_token=assistant_csrf_token,
        consultation_csrf_token=consultation_csrf_token,
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