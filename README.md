# Revenue Engine

A conservative revenue pipeline for legitimate B2B outreach.

## Current goal

One legitimate coaching/training business customer for the ₹490 landing-page offer.

## Pipeline

DISCOVERED → QUALIFIED → VERIFIED → CONTACTED → DELIVERED → REPLIED
→ INTERESTED → BUDGET_DISCLOSED → PROPOSAL → PAID

Suppression/failure states:

BOUNCED, COMPLAINT, UNSUBSCRIBED, NOT_INTERESTED, DISQUALIFIED, SUPPRESSED

## Safety principles

- Uses publicly available business information.
- Does not invent email addresses or business claims.
- Does not bypass spam filters.
- Starts with conservative sending limits.
- Never sends automatically from discovery alone.
- Keeps outreach history and suppression records.
- Follow-up logic will be capped at two follow-ups.
- Pricing/proposal logic will not be triggered before the appropriate buying signal.
- No work is started without written scope/agreement and required payment/authentication.

## Setup

Python 3.11+ recommended.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

Copy `.env.example` to `.env` for local development and fill in secrets through your environment/secret manager.

Initialize the database:

```bash
python -m scripts.init_db
```

Run the pipeline in dry-run mode:

```bash
python -m scripts.run_campaign --dry-run
```

No real email sending is implemented in Phase 1.
