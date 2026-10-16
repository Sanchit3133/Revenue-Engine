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
- `python -m unittest discover -s tests -v`: 185 run, 185 passed, 0 failed, 0 skipped.
- Three PostgreSQL integration tests passed against the configured development PostgreSQL database, covering SQL adaptation, repeatable schema initialization/preservation of existing rows, and backend health; two additional tests verified safe backend selection.
- Preview `/healthz` returned HTTP 200 with `database: postgresql`; the home, consultation, client-request, and owner-login routes returned HTTP 200.
- The Replit preview workflow is running on port 5000 with `python -m scripts.run_web`.
- A synthetic SQLite migration dry run completed without writing to its source; the row count remained unchanged.
- No live email, outreach, payment processing, production deploy, or customer delivery was attempted.

## India + international market behaviour

- India is `Domestic baseline`, priority `benchmark`; its competition assumption is explicitly labeled as the owner's hypothesis, not verified statistics.
- The default discovery country is India, and the owner dashboard allows another configured country to be selected for a separate city/query run.
- The selected country is stored with each lead and displayed in the dashboard.
- Each country starts `RESEARCH_REQUIRED`. The operator must check the seven validation gates and record at least 30 characters of evidence notes before live outreach is allowed. This is an operator attestation; the software does not independently prove the evidence is accurate.
- Country profiles are not evidence that low competition, language access, payment access, AI adoption or demand has been established. The candidate does not automatically launch campaigns worldwide.

## Remaining production gates

1. This workspace is not published. Do not mark production verified until the user publishes and checks the published app's `/healthz` against Replit's separate production PostgreSQL database.
2. Before Publish, verify a recent production restore point and scheduled backups in the Database tool; review the schema diff and rename prompts. Do not overwrite production data unintentionally.
3. Configure `FLASK_SECRET_KEY` and `OWNER_DASHBOARD_PASSWORD` using Production Secrets. Add optional provider credentials only for explicitly enabled features; Brevo credentials are not configured in the current environment.
4. Verify `/healthz`, `/consultation`, `/client/request`, `/owner/login`, and dashboard safeguards on the published HTTPS URL.
5. Test Hugging Face response/fallback, owner notifications, private draft access, captured-payment and written-scope approval gates, and delivery uncertainty in controlled environments.
6. Test provider webhook authentication, duplicate events, suppression/opt-out, and payment reconciliation with test-mode accounts before live use.
7. Document app-code rollback separately from production database point-in-time restore; a code rollback does not undo data or schema changes.
