# GitHub-primary deployment with the Heroku Student Developer Pack

Revenue Engine's GitHub repository is the source of truth. Replit remains the development/preview workspace and temporary fallback. The public marketing site remains in `Sanchit3133/sanchit-agarwal-website`; the Flask application runs on a separate persistent host.

## Why Heroku is the first choice

The current GitHub Student Developer Pack lists Heroku platform credits of **US$13 per month for 24 months** (US$312 total, issued monthly and not as an upfront balance). The published Heroku prices currently list a Basic web dyno at US$7/month and Essential-0 PostgreSQL at US$5/month: approximately US$12/month before tax or extras. This leaves only about US$1 per month of headroom under the student credit, so do not enable extra paid products or third-party add-ons without checking billing first.

- Choose a **Basic dyno**, not Eco, for the customer-facing app. Eco dynos can sleep after inactivity.
- Choose **Heroku Postgres Essential-0** for the initial pilot. It is a small 1 GB database with a 20-connection limit and a published expected uptime of 99.5% per month; it is not a high-availability tier.
- The offer requires an eligible student (18+ under Heroku's offer terms) and a valid credit or debit card on file. Any bill beyond the monthly US$13 credit is charged to the card. If you cannot accept that billing risk, do not activate paid resources yet.
- The credits expire after 24 months and do not roll forward. Budget for paid hosting before the credit period ends.

Offer and terms: https://www.heroku.com/github-students/ and https://help.heroku.com/Z3RHNRHD/how-does-the-heroku-for-github-students-program-work

## GitHub automatic deployment

The repository includes `requirements.txt`, a `Procfile`, and `.python-version` for Heroku's Python buildpack. A portable Dockerfile is also included for other hosts; the GitHub Deploys path on Heroku should be configured as the Python buildpack deployment.

1. In GitHub Student Developer Pack, open the Heroku offer and apply it to the correct personal Heroku account. Check the Billing page to confirm that the student credit is actually active before creating paid resources.
2. Create a personal Heroku app (not a Team app; the student credit cannot be applied to a Heroku Team account).
3. Add **Heroku Postgres Essential-0** under the app's Resources tab. Heroku attaches its connection string as `DATABASE_URL`; do not paste it into source control or public chat.
4. Before the first deploy, set these Config Vars in the Heroku dashboard:
   - `ENVIRONMENT=production`
   - `DATABASE_BACKEND=postgresql`
   - `FLASK_SECRET_KEY` — a strong unique random value
   - `OWNER_DASHBOARD_PASSWORD` — a strong unique password
   - `AI_PROVIDER=groq`
   - `GROQ_API_KEY`, `GROQ_API_URL=https://api.groq.com/openai/v1/chat/completions`, and `GROQ_MODEL=openai/gpt-oss-20b`
   - `LANDING_PAGE_URL=https://sanchit-agarwal.me/#services`
   Add Brevo, Razorpay, Google Places and other keys only when the corresponding functionality is deliberately enabled. Keep outreach sending disabled during deployment tests.
5. In Heroku → Deploy, connect GitHub repository `Sanchit3133/Revenue-Engine`, select branch `main`, enable automatic deploys, and enable **Wait for CI to pass before deploy** if Heroku recognizes the GitHub Actions status checks.
6. Perform the initial manual deploy after the configuration and database attachment are in place. Confirm the release phase succeeds, then confirm `/healthz` reports a healthy PostgreSQL backend.
7. Keep automatic deploys attached to `main`, not a feature branch. Changes should flow through pull requests and the existing GitHub Actions tests before the production release.

GitHub integration instructions: https://devcenter.heroku.com/articles/github-integration

## Database migrations and preservation

The `release:` process in the Procfile explicitly runs `python -m scripts.init_db --allow-production` before the web release. That command is allowed only when `ENVIRONMENT=production` and the selected backend is PostgreSQL. The same database layer still refuses production schema mutation by default. The schema setup is additive/repeatable and runs in the release phase, not in a web worker or during each request.

**Do not direct real customer traffic to the new app until data migration is complete.** Replit's preview and published app may have different PostgreSQL databases. Before cutover:

1. Identify the authoritative Replit published database and confirm a restorable backup exists.
2. Record baseline row counts for leads, outreach, suppressions, payments, client requests, consultation submissions/messages, webhook events, and audit records.
3. Prepare a reviewed PostgreSQL data transfer into the Heroku database that preserves IDs, relationships, payment references, consent records, suppressions, and audit history. Do not assume restoring a full dump over the newly initialized target schema is safe; resolve schema differences before transfer.
4. Compare row counts and sample records, then verify login, consultation, client requests, admin routes, and payment/webhook handling in the new app.
5. Keep the original Replit deployment and database unchanged during the validation window. Do not delete data or overwrite production to make migration easier.

Heroku Postgres supports backups. Configure a daily logical backup schedule, verify the schedule and at least one successful backup, and keep an additional protected copy for critical data. The Essential tier has limited retention and no database high availability; see https://devcenter.heroku.com/articles/heroku-postgres-backups.

## Website connection and domain

The marketing site stays on GitHub Pages at `https://sanchit-agarwal.me`. After the Heroku app is healthy and data has been migrated, configure a dedicated application hostname such as `app.sanchit-agarwal.me` using the exact custom-domain/DNS records Heroku gives you. Do not replace the root domain's existing GitHub Pages DNS records.

Then update the main website's consultation and client-request links to the verified app hostname, run end-to-end tests, and only then direct customers to the new endpoint. Keep the Replit URL as an operator-only fallback during the rollback window.

## Owner-authenticated customer delivery

The owner dashboard continues to handle approvals; no customer should be told a deliverable is ready until its URL and content have been checked. The future GitHub delivery integration should use GitHub App/OAuth authorization with the minimum required permissions, rather than asking for a GitHub password or hard-coding a personal access token. Each repository creation, deploy, and approval should have an audit record. Static customer sites can use GitHub Pages; projects needing Python/Node server code or a database need a runtime host.

## Cost and security guardrails

- The $13 credit is monthly, not an upfront $312 balance. Any overage is charged to the card on file.
- Keep the app to one Basic dyno and one Essential-0 database initially. Disable review apps or extra dynos unless you confirm the incremental cost is within the credit.
- Do not add a Key-Value Store, paid third-party add-ons, or extra worker dynos for this first release.
- Never commit `.env`, database exports, API credentials, client data, or payment secrets.
- Configure owner authentication, HTTPS, backups, suppression and opt-out handling before enabling real outreach or production payments.
- A valid `/healthz` response is one check, not proof that payment, email, or database migration paths have passed their acceptance tests.
- Retain Replit as fallback until production data and the restore procedure are proven on the new host.
