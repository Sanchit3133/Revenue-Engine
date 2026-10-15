# Replit deployment guide — Revenue Engine

The application is a Flask web service, not a static-only site. GitHub is the source-control/CI home; Replit runs the customer consultation, Hugging Face assistant, webhook receivers, client-request queue, and private owner dashboard.

## Before publishing

1. Import the reviewed Revenue Engine source into a Replit App (Import from GitHub after the release branch has been published, or upload the reviewed source archive into the existing Replit project after backing up its current files).
2. Use Python 3.11 or newer and install the dependencies with `pip install -r requirements.txt`.
3. Configure the web server run command as:

   `gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 45 wsgi:app`

4. Configure the deployment secrets listed below. Never commit `.env` or paste secrets into issue/chat logs.
5. Run `python -m compileall -q app scripts tests` and `python -m unittest discover -s tests -v` in the same Replit environment that will run the app.
6. Run `python -m scripts.preflight` and verify `/healthz`, `/consultation`, `/client/request`, and `/owner/login`.
7. **Persistence is a hard launch gate.** This candidate uses SQLite through `OUTREACH_DB`; simply setting Replit's `DATABASE_URL` does not make this code use PostgreSQL. Confirm the exact deployment's filesystem is durable across restart and republish. If it is not, do not accept real customer data or enable live outreach yet: complete a tested PostgreSQL adapter/migration first. Back up the database before any redeployment.

## Required deployment secrets

- `ENVIRONMENT=production`
- `FLASK_SECRET_KEY` — generate a new random secret (e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`)
- `OUTREACH_DB` — path on storage that persists across restarts/deployments
- `OWNER_DASHBOARD_PASSWORD` — unique, long password not used elsewhere
- `OWNER_NOTIFICATION_EMAIL` — the inbox that should receive new client/revision/dissatisfaction requests
- `PUBLIC_APP_URL` — the published HTTPS Replit app URL; used to place a dashboard link in owner notifications
- `HF_TOKEN` — Hugging Face token with Inference Providers permission
- `TARGET_MARKET_COUNTRY=India` — default country for discovery when a country is not passed explicitly; the dashboard always records the selected country per lead
- `MARKET_VALIDATION_REQUIRED=true` — keep enabled for launch; blocks live send actions until the country has a saved validation attestation
- `HF_MODEL` — configured supported model; current default can be changed if unavailable
- `EMAIL_PROVIDER=brevo`, `BREVO_API_KEY`, `BREVO_SENDER`, and optional `BREVO_REPLY_TO`
- `GOOGLE_API_KEY` for lead discovery (optional until that module is used)
- `RAZORPAY_WEBHOOK_SECRET` from the Razorpay webhook configuration (optional until payment webhook is configured)
- `BREVO_WEBHOOK_TOKEN` only if your actual Brevo event/webhook configuration supports sending that authentication header/token

The owner dashboard will be unavailable until `OWNER_DASHBOARD_PASSWORD` is configured. Owner request email notifications will remain visible in the dashboard but will not be emailed until `OWNER_NOTIFICATION_EMAIL`, `BREVO_API_KEY`, and `BREVO_SENDER` are configured. The dashboard link in emails uses `PUBLIC_APP_URL`; configure it after the app has a stable published URL.

## Replit deployment UI

- Open the Replit App that will run the Revenue Engine.
- Confirm the editor's files are the reviewed full engine, not just the previous Razorpay/JSON intake receiver. Back up that receiver before replacing anything.
- Open **Deploy / Publish**, choose a web-server deployment type, and set the run command above. Check the shown cost before confirming publish; do not choose a paid resource without reviewing the charge.
- Add secrets to deployment secrets (not source files), then publish.
- Use the generated `*.replit.app` HTTPS URL for checks before connecting a custom domain.

## Live URLs after deployment

- `/` — Revenue Engine customer homepage
- `/consultation` — consented guided consultation with an AI assistant (HF-backed when token/model are available; controlled fallback otherwise)
- `/client/request` — delivery, revision, dissatisfaction and delivery-issue intake
- `/owner/login` — private owner dashboard sign-in
- `/owner` — module counts, lead/outreach/payment/webhook/consultation tables, client draft approval queue, country-aware lead discovery, market validation, operational buttons, and audit history
- `/healthz` — health check
- `/webhooks/brevo` — delivery/bounce/complaint/unsubscribe events
- `/webhooks/razorpay` — signed Razorpay event intake

## Important delivery controls

- The customer request route may generate a private draft, but never emails that file to the customer.
- Owner approval is blocked unless the owner confirms the written scope/agreement and enters a Razorpay payment ID that matches a captured-payment record or a signed captured-payment webhook retained in the database. A typed reference alone is not enough.
- A confirmed approval claims the request atomically before sending. An ambiguous provider timeout becomes `DELIVERY_UNCERTAIN`; check provider activity and reconcile it before retrying.
- `Send one prepared email` sends at most one reviewed PREPARED outreach message per click and only after explicit confirmation. It is not automatically started by lead discovery.
- The Razorpay capture webhook still needs a tested path linking a real payment to the right customer/request. The approval gate only verifies that the payment ID is present in this app’s captured records or signed webhook log; the owner must also verify payer, amount, and agreement in the Razorpay dashboard before approving. Payment-to-request/customer reconciliation remains a live acceptance test.
- Configure Razorpay's webhook endpoint to the deployed `/webhooks/razorpay` path and use the webhook signing secret, not API key secret. Configure Brevo event authentication to match the provider's actual supported mechanism. Do not expose unauthenticated event endpoints.

## End-to-end acceptance checklist

1. `/healthz` returns JSON with `status: ok`.
2. A test consultation is persisted only after consent.
3. A user chat prompt returns an HF answer or a clearly identified fallback response.
4. A client request appears in `/owner`, and notification reaches the configured owner inbox.
5. A generated HTML file is downloadable only after owner login.
6. Approval without a written-scope confirmation or payment reference is blocked.
7. A controlled test email receives the approved attachment once; check provider activity and a controlled recipient mailbox.
8. Razorpay test-mode signed capture, duplicate event, and customer/payment reconciliation all pass.
9. Bounce, complaint, unsubscribe, duplicate webhook and delivery-uncertain cases are exercised.
10. Storage persistence, database backup/restore, production secrets, HTTPS and rollback are verified before real outreach volume increases.
