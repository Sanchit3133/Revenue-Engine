# Revenue Engine — combined Replit release-candidate audit

## Source of truth

This package combines the October 12 international-market source supplied by the business owner with the integrated October 14 Replit candidate containing the Hugging Face assistant, consultation persistence, client-request generation, owner dashboard, notifications, email delivery, webhook/payment processing and tests. It then adds India as an explicit market profile and country-aware lead/validation handling.

This is a **deployment candidate, not a production-certified live system**.

## Current modules present

- Flask customer home and consented multi-step consultation.
- Hugging Face Inference Providers integration with controlled fallback when the token/model is unavailable.
- Private owner login and dashboard for consultation history, lead discovery, outreach preparation/sending, replies, payments, webhooks, generated client drafts, review/approval, request status and audit history.
- Client delivery/revision/dissatisfaction/issue intake. Draft files are private until owner approval; the app requires written-scope confirmation and verified captured-payment evidence before sending the deliverable.
- Brevo and SendGrid mail client integrations; Brevo event intake, suppression, idempotency, explicit retry tooling and controlled outreach sending.
- Razorpay signed-webhook capture handling and reconciliation workflow.
- Country hypotheses for the configured international markets plus India as a domestic benchmark.
- Country-aware lead discovery and lead storage; country market validation is an explicit operator attestation gate before live outreach when `MARKET_VALIDATION_REQUIRED=true`.

## Validation performed for this package

- `python -m compileall -q app scripts tests`: passed.
- `python -m unittest discover -s tests -v`: 178 discovered; 162 passed; 16 skipped because Flask is not installed in the isolated review environment. The skipped tests are route/persistence tests and must run in the actual Codespaces/Replit environment after `pip install -r requirements.txt`.
- All Jinja templates parsed successfully.
- `python -m scripts.market_strategy --country India`: passed.
- `python -m scripts.discover_leads --help`: passed and shows country selection.
- `scripts.preflight` with temporary database and placeholder provider configuration: passed SQLite integrity check. This only verifies local configuration/database wiring; placeholder credentials are not real account verification.
- `scripts.run_campaign --dry-run --limit 1 --provider brevo` against an empty temporary database: passed; no provider API request and no email were sent.
- No live email, AI inference, webhook, payment, production deploy or customer delivery was attempted.

## India + international market behaviour

- India is `Domestic baseline`, priority `benchmark`; its competition assumption is explicitly labeled as the owner's hypothesis, not verified statistics.
- The default discovery country is India, and the owner dashboard allows another configured country to be selected for a separate city/query run.
- The selected country is stored with each lead and displayed in the dashboard.
- Each country starts `RESEARCH_REQUIRED`. The operator must check the seven validation gates and record at least 30 characters of evidence notes before live outreach is allowed. This is an operator attestation; the software does not independently prove the evidence is accurate.
- Country profiles are not evidence that low competition, language access, payment access, AI adoption or demand has been established. The candidate does not automatically launch campaigns worldwide.

## Remaining production gates

1. Install declared requirements in the real Codespaces/Replit environment and run all 178 tests, including the 16 Flask-dependent tests.
2. Confirm the Replit deployment's database persists across restart and republish. **This candidate uses SQLite via `OUTREACH_DB`; setting Replit `DATABASE_URL` alone does not switch it to PostgreSQL.** If the chosen deployment has ephemeral local storage, a tested PostgreSQL adapter/migration is required before accepting durable customer records or enabling live outreach.
3. Set secrets only in the deployment secret manager: production `FLASK_SECRET_KEY`, unique `OWNER_DASHBOARD_PASSWORD`, `OWNER_NOTIFICATION_EMAIL`, `PUBLIC_APP_URL`, valid `HF_TOKEN`, Google Places key when discovery is enabled, Brevo key/active sender, webhook authentication, and Razorpay webhook signing secret as needed.
4. Verify `/healthz`, `/consultation`, `/client/request`, `/owner/login`, and all dashboard operations on the published HTTPS URL.
5. Test Hugging Face response and fallback, owner notification, private draft download, approval rejection without captured payment/written scope, and a controlled approved delivery.
6. Test Brevo delivered/bounce/complaint/unsubscribe/duplicate events and Razorpay test-mode signed capture/idempotency/payment reconciliation.
7. Verify backups, restore, cost/billing limits and rollback before public campaign operation.
