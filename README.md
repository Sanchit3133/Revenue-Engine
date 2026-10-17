# Revenue Engine — Integrated Release Candidate

This repository is a conservative workflow for turning publicly sourced business leads into a consultation-first conversation, then tracking delivery, replies, budget disclosure, proposals and captured payments.

**Current release status:** locally testable release candidate; not yet production-certified. Provider accounts, DNS, a public HTTPS deployment, webhook dashboard configuration and a controlled live mailbox check must still be verified in the target environment.

## Operating rules

- Never invent a business problem, email address, or observation.
- First contact is a question, not a proposal or price pitch.
- Do not disclose price before the prospect discloses budget and implementation requirements are understood.
- Do not send more than two follow-ups. Transport retries retain the same sales-touch number and must never erase the original attempt.
- Do not automatically respond to a reply. Replies are classified/recorded for the operator to respond appropriately.
- Opt-outs, complaints, invalid/hard-bounced recipients and suppression records block future contact.
- A provider accepting an email is `SENT`/`CONTACTED`, not `DELIVERED`. Delivery state comes from a provider event.
- Never start work without a written scope/agreement and required payment/authentication.
- Dry-run mode performs no provider request.

## Architecture included

### International market strategy

- Configured country-market hypotheses based on the owner's proposed target groups, explicitly marked as unverified rather than treated as market facts.
- Country-level validation checklist for cross-border payment settlement, preferred language, Google Maps/Facebook/WhatsApp discoverability, competition, buyer demand, compliance and unit economics.
- `python -m scripts.market_strategy` lists the hypotheses; `python -m scripts.market_strategy --country Uganda` shows one country's validation gates.
- Lead discovery accepts `--country` so the search location can be qualified (for example, `--city Kampala --country Uganda`). The owner dashboard lets you choose a configured country and stores that country on each lead; India is available as a domestic benchmark profile.
- Every country starts `RESEARCH_REQUIRED`. When `MARKET_VALIDATION_REQUIRED=true` (the default), the owner dashboard blocks live outreach until all seven market gates are checked and a dated evidence note is recorded for that country. This is an operator attestation, not an automatic audit of evidence accuracy.
- Website localization requirements are documented in `docs/WEBSITE_INTERNATIONAL_POSITIONING.md`. The website repository itself was not included in this archive, so its HTML has not been modified by this package.

### Lead and outreach

- Google Places lead discovery and public website email extraction (`scripts/discover_leads.py`).
- Lead qualification/scoring, normalization, duplicate checks and suppression gates.
- Separate preparation and delivery stages (`scripts/prepare_campaign.py`, `scripts/run_campaign.py`).
- Provider selection via `EMAIL_PROVIDER=brevo|sendgrid`.
- Brevo transactional-email client (`app/brevo_client.py`) and retained SendGrid client (`app/sendgrid_client.py`).
- Persistent reminders (four days after first contact, then five days after follow-up 1), hard cap of two follow-ups, rate delay, daily/run caps, atomic message claims, and a consecutive-failure stop guard.
- Append-only outreach event records and a controlled transport-retry path preserving the failed source row.

### Delivery events and replies

- Brevo transactional event intake at `POST /webhooks/brevo` with a shared-token check (header `X-Revenue-Webhook-Token` or `Authorization: Bearer <token>` / `Authorization: Token <token>`), idempotency, delivery mapping and durable suppression for hard bounce, invalid email, spam/complaint and unsubscribe.
- Manual reply intake (`scripts/process_reply.py`) which records and classifies a copied inbox reply; it never sends automatic responses.
- Bare `STOP` is treated as an opt-out.

**Webhook deployment note:** Brevo's different webhook products expose different authentication options. Configure a token/custom header if the selected webhook type supports it; otherwise use HTTPS and Brevo's recommended IP restrictions at a trusted reverse proxy. Do not expose an unauthenticated public webhook endpoint. The app route fails closed if `BREVO_WEBHOOK_TOKEN` is empty.

### Automated acquisition lifecycle

- GitHub Actions contains a read-only Overture preview and a daily acquisition workflow. The daily workflow is **gated off** until a restricted production database role, Brevo sender/reply receiver, production schema migration, market validation and end-to-end checks are complete.
- Live automation uses the shared PostgreSQL database and never persists a runner-local SQLite database or pushes lead/contact history to Git.
- See docs/phase-one-automation-setup.md for the exact production migration, limited-role grants, Brevo inbound setup and acceptance checklist. Live automation must remain disabled until that checklist is complete.

### Payment tracking

- Razorpay webhook endpoint `POST /webhooks/razorpay` verifies `X-Razorpay-Signature` against the raw request body using the *webhook secret*.
- Captured events are deduplicated and stored. Amounts are stored in the smallest currency unit (for INR, paise).
- For direct mapping, the Razorpay order should include `notes.revenue_engine_lead_id=<lead_id>` (or `notes.lead_id`). Alternatively pre-register an order-to-lead map with `scripts/register_payment_order.py`.
- Signed captured events that cannot be linked to a lead are retained for explicit reconciliation. Do not manually mark a lead paid without confirming the captured transaction in Razorpay.
- Reconciliation command: `python -m scripts.reconcile_payment --event-id <local_webhook_events_row_id> --lead-id <lead_id> --confirm-payment-captured`.

### Website consultation

- The Flask consultation form now requests a business email and explicit consent to use submitted details to respond.
- Once consent is given, consultation progress is stored in the configured database instead of only in a browser session.
- The static GitHub Pages site cannot execute Flask routes itself. Deploy the Flask application separately on a public HTTPS host and point forms/webhooks to the deployed service where applicable.

### Database and app security

- PostgreSQL is the Replit preview/published backend; local development and isolated tests can use SQLite. Schema changes are additive and preserve existing lead/outreach history.
- `/healthz` verifies database connectivity and the core schema, and reports the active backend without exposing connection details.
- Production mode requires `FLASK_SECRET_KEY`, sets secure/HTTP-only session cookies, requires PostgreSQL, and uses the `wsgi.py` entry point. Do not run Flask's development server in production.

## Setup in Codespaces

Python 3.11+ is required (`StrEnum` is used). In the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
```

Keep the existing `.env` if already present—do not overwrite API keys by recopying the example. For local development, keep provider keys only in `.env`; in Replit, use the appropriate environment's Secrets pane. `.env` must remain ignored by Git. Replit supplies a separate managed `DATABASE_URL` for preview and published environments; do not copy either URL into a file.

Required minimum settings for Brevo sending:

```dotenv
EMAIL_PROVIDER=brevo
BREVO_API_KEY=your_private_key
BREVO_SENDER=hello@sanchit-agarwal.me
BREVO_REPLY_TO=noreply.sanchitagarwal@gmail.com
LANDING_PAGE_URL=https://sanchit-agarwal.me/#services
```

Never print or paste API keys into chat/logs. Brevo API calls originate from the deployed runtime, whose IP may be different from Codespaces; authorize the actual deployment's outbound IP as required by Brevo.

## Preflight and tests (no email sent)

```bash
python -m scripts.preflight
python -m scripts.preflight --online
python -m compileall -q app scripts tests
python -m unittest discover -s tests -v
```

`--online` sends only read-only account/sender checks to the selected email provider. It does not use the Mail Send endpoint.

## Safe first-campaign procedure

1. Run the full test suite and preflight.
2. Prepare a small batch: `python -m scripts.prepare_campaign --limit 1`.
3. Inspect the database message before delivery.
4. Dry-run: `python -m scripts.run_campaign --dry-run --limit 1 --provider brevo`.
5. The earlier SendGrid request for outreach record `1` returned `HTTP 401: Maximum credits exceeded`. To store that already-observed diagnostic in the migrated database (without changing status or sending), run once:

   ```bash
   python -m scripts.annotate_failed_outreach --outreach-id 1 --provider sendgrid --reason "sendgrid_http_401: Maximum credits exceeded" --confirm-recorded-failure
   ```

   This annotates the existing `FAILED` row only. Never use a retry to bypass an uncertain provider timeout. For any old `FAILED` row, only create a separate linked retry record after independently confirming the provider did not accept the prior attempt:

   ```bash
   python -m scripts.retry_failed_outreach --outreach-id <id> --confirm-not-delivered
   ```

6. Before contacting a real prospect, send one explicitly approved test message to an inbox you control and verify actual receipt. Use `--send --limit 1 --provider brevo` only when the prepared row is that controlled test recipient.
7. Inspect stored status and webhooks. Acceptance changes the outreach row to `SENT` and a qualified lead to `CONTACTED`; it does not imply inbox delivery.
8. Do not immediately run a live campaign after a test. Verify bounce, complaint, opt-out and webhook handling first.

## Useful commands

```bash
python -m scripts.discover_leads --city "Nagpur" --country "India" --query "coaching institute" --pages 1 --max-new 5 --dry-run
python -m scripts.market_strategy --country India
python -m scripts.market_strategy --country Uganda
python -m scripts.prepare_campaign --limit 1
python -m scripts.run_campaign --dry-run --limit 1 --provider brevo
python -m scripts.process_reply --lead-id 2 --from-email owner@example.com --subject "Re: Quick question" --body "STOP"
python -m scripts.register_payment_order --lead-id 2 --order-id order_example --amount-minor 49000 --currency INR
python -m scripts.init_db
```

## Deploying the Flask app

For local development only:

```bash
python -m scripts.run_web
```

On Replit, the preview workflow listens on port 5000. The published process uses the production WSGI command in `Procfile`, which binds to Replit's `$PORT`. Replit creates a separate production PostgreSQL database and injects its `DATABASE_URL` into the deployment; do not assume it is the preview database or copy its connection string into source. Add `FLASK_SECRET_KEY`, `OWNER_DASHBOARD_PASSWORD`, and only the provider credentials needed for explicitly enabled features through Production Secrets.

### Replit schema and data release

1. `app/database.py` is the schema source. Run `python -m scripts.init_db` against the development database; the configured `scripts/post-merge.sh` also applies additive schema changes after a task merge. No DDL runs in a web worker or at app startup.
2. Run the complete tests and verify the preview's `/healthz`.
3. Before publishing, verify a recent production restore point and scheduled backups in the Database tool's production Settings.
4. The user starts Publish. Replit compares development and production schema and applies the schema diff. Review rename prompts and warnings; schema changes may briefly interrupt service. Do not select overwrite-data unless replacing production data is intended.
5. After publishing, check the published app's `/healthz` returns `status: ok` and `database: postgresql`. Code rollback does not revert database changes; use the production Database tool's point-in-time restore if data recovery is needed, accounting for writes after the restore point.

For a SQLite-to-PostgreSQL data move, first back up the SQLite file and review the dry run described in [`docs/database-postgresql.md`](docs/database-postgresql.md). The explicit `--apply` path copies only to an empty development PostgreSQL database, preserving IDs and the source SQLite file. A newly provisioned production database is created from development at first publish; later publishes do not copy development rows into existing production. The current project is not published, and no SQLite records were migrated by this change.

## Launch gates still requiring external verification

- A real test email accepted by Brevo and observed in a user-controlled inbox.
- Brevo transactional webhooks configured to the deployed HTTPS URL with the supported authentication/IP restrictions, and a real delivered event observed.
- Razorpay webhook URL/secret configured in the Razorpay dashboard, with a test-mode captured payment that maps to a known lead.
- Production hosting and persistent storage verified. The source ZIP does not contain your local `.env`, active database, hosted service, or account dashboard state.
- If SendGrid is selected as a fallback, only its sending client is present here; configure a SendGrid-specific signed event webhook separately if you need SendGrid delivery-event intake.
