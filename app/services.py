from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    starting_price: Optional[int]
    category: str
    problems_solved: List[str]
    business_signals: List[str]
    deliverables: List[str]


SERVICE_CATALOG = {
    "LANDING_PAGE": Service(
        key="LANDING_PAGE",
        name="Professional Landing Page",
        starting_price=490,
        category="website",
        problems_solved=[
            "outdated website",
            "weak conversion page",
            "unclear offer",
            "poor enquiry flow",
            "lack of focused campaign page",
        ],
        business_signals=[
            "no clear call to action",
            "outdated website",
            "course promotion without dedicated page",
            "advertising traffic sent to a generic page",
            "important service lacks a focused page",
        ],
        deliverables=[
            "one complete landing page",
            "mobile-responsive design",
            "conversion-focused structure",
            "clear enquiry or WhatsApp call to action",
            "one revision round",
        ],
    ),

    "PAYMENT_PAGE": Service(
        key="PAYMENT_PAGE",
        name="Payment Page",
        starting_price=790,
        category="payments",
        problems_solved=[
            "no simple online payment flow",
            "payment friction",
            "manual payment collection",
            "unclear payment instructions",
        ],
        business_signals=[
            "payment handled manually",
            "customers asked to transfer money manually",
            "no obvious online payment option",
            "payment instructions scattered across channels",
        ],
        deliverables=[
            "dedicated payment page",
            "clear payment instructions",
            "payment call to action",
            "integration with supported payment flow",
        ],
    ),

    "LEAD_CAPTURE": Service(
        key="LEAD_CAPTURE",
        name="Lead Capture System",
        starting_price=990,
        category="lead_management",
        problems_solved=[
            "unstructured enquiries",
            "leads lost in messages",
            "manual enquiry collection",
            "lack of centralized lead information",
        ],
        business_signals=[
            "enquiries handled through multiple channels",
            "no structured enquiry form",
            "no obvious lead capture workflow",
            "staff manually collecting enquiry details",
        ],
        deliverables=[
            "structured lead capture form",
            "lead information collection",
            "organized enquiry workflow",
            "basic lead handoff process",
        ],
    ),

    "AUTO_RESPONSE": Service(
        key="AUTO_RESPONSE",
        name="Auto-Response System",
        starting_price=1490,
        category="automation",
        problems_solved=[
            "slow first response",
            "enquiries waiting for replies",
            "repetitive first-response work",
            "missed initial contact",
        ],
        business_signals=[
            "enquiries depend entirely on manual replies",
            "no immediate acknowledgement",
            "high-volume enquiry periods",
            "customers expected to wait for a response",
        ],
        deliverables=[
            "automated initial response workflow",
            "lead acknowledgement",
            "basic response routing",
            "human handoff for further conversation",
        ],
    ),

    "FOLLOW_UP_SYSTEM": Service(
        key="FOLLOW_UP_SYSTEM",
        name="Follow-Up System",
        starting_price=2490,
        category="automation",
        problems_solved=[
            "inconsistent follow-up",
            "leads not followed up",
            "manual follow-up reminders",
            "enquiries disappearing after the first conversation",
        ],
        business_signals=[
            "staff manually remembering follow-ups",
            "no follow-up schedule",
            "enquiries often go cold",
            "follow-up depends on individual staff members",
        ],
        deliverables=[
            "follow-up workflow",
            "reminder logic",
            "lead status tracking",
            "appropriate human handoff",
        ],
    ),

    "BUSINESS_AUTOMATION": Service(
        key="BUSINESS_AUTOMATION",
        name="Business Automation",
        starting_price=3490,
        category="automation",
        problems_solved=[
            "repetitive administrative work",
            "manual business workflows",
            "information moving between multiple systems",
            "avoidable operational effort",
        ],
        business_signals=[
            "repetitive manual processes",
            "multiple disconnected tools",
            "staff repeatedly entering the same information",
            "workflow depends on spreadsheets or manual reminders",
        ],
        deliverables=[
            "workflow analysis",
            "automation of agreed repetitive processes",
            "system connections where technically appropriate",
            "basic monitoring and handoff",
        ],
    ),

    "CUSTOM_OUTREACH_SYSTEM": Service(
        key="CUSTOM_OUTREACH_SYSTEM",
        name="Custom Outreach System",
        starting_price=4900,
        category="custom",
        problems_solved=[
            "manual lead discovery",
            "manual outreach operations",
            "large repetitive outreach workload",
            "lack of structured outreach tracking",
        ],
        business_signals=[
            "large-scale prospecting requirement",
            "repetitive lead research",
            "manual campaign management",
            "need for organization-specific outreach workflow",
        ],
        deliverables=[
            "requirements analysis",
            "custom lead workflow",
            "outreach process automation",
            "tracking and operational controls",
        ],
    ),

    "CUSTOM_AUTOMATION": Service(
        key="CUSTOM_AUTOMATION",
        name="Custom Automation",
        starting_price=4900,
        category="custom",
        problems_solved=[
            "complex manual workflow",
            "multiple-system process",
            "organization-specific automation requirement",
            "workflow bottleneck",
        ],
        business_signals=[
            "complex multi-step process",
            "multiple tools involved",
            "manual transfer of information",
            "workflow cannot be solved by a standard package",
        ],
        deliverables=[
            "requirements analysis",
            "workflow design",
            "custom implementation",
            "testing of agreed workflow",
        ],
    ),
}


def get_service(service_key: str) -> Optional[Service]:
    """
    Return a service by its internal key.

    Returns None if the key does not exist.
    """
    return SERVICE_CATALOG.get(service_key)


def list_services() -> List[Service]:
    """
    Return all services in the catalogue.
    """
    return list(SERVICE_CATALOG.values())


def services_for_category(category: str) -> List[Service]:
    """
    Return services belonging to a category.
    """
    return [
        service
        for service in SERVICE_CATALOG.values()
        if service.category == category
    ]


def service_keys() -> List[str]:
    """
    Return all service keys.
    """
    return list(SERVICE_CATALOG.keys())