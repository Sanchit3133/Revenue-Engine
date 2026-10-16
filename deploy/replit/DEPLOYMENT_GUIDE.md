# Replit deployment guide — Revenue Engine

The Revenue Engine is a Flask web service, not a static-only site. It uses PostgreSQL on Replit; SQLite is retained for local development and isolated tests. This guide describes the manual release procedure. Publishing, billing changes, live email, outreach, and payment processing are not performed by this project setup.

## Environment and database

- Replit maintains separate development and production PostgreSQL databases. The workspace preview connects to the development database.
- When the app is published, Replit creates/manages its production PostgreSQL database and syncs that environment's `DATABASE_URL` into the deployment secrets. The published app does not share the preview's database. Do not copy or print either connection string.
- `REPL_DEPLOYMENT=1` marks the published runtime as production. Production requires `DATABASE_URL` and refuses SQLite; there is no silent fallback to `OUTREACH_DB`.
- For an external PostgreSQL provider, configure distinct development and production URLs through the corresponding Secrets environments. Never reuse a development URL in production by assumption.
- The SQLite-to-PostgreSQL copier only writes into an empty development database. It never targets production.

## Replit run commands

- Preview workflow: `python -m scripts.run_web` on port `5000` (configured as a webview workflow in `.replit`).
- Published app: `gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 --threads 4 --timeout 45 wsgi:app` (the repository `Procfile`).
- Do not run the Flask development server in production. Neither command applies schema changes at startup.

## Secrets

Use Replit Secrets for deployment values; never commit `.env`, put credentials in source, or paste values into logs/chat.

- Replit-managed `DATABASE_URL` is supplied for each environment automatically. Do not set or copy it manually for the managed database.
- `FLASK_SECRET_KEY` — required in production; use a long random value.
- `OWNER_DASHBOARD_PASSWORD` — unique long password for the private owner dashboard.
- `OWNER_NOTIFICATION_EMAIL` and `PUBLIC_APP_URL` — only needed for configured owner notifications and published dashboard links.
- `HF_TOKEN` — only if enabling the Hugging Face assistant.
- `BREVO_API_KEY`, `BREVO_SENDER`, and optional webhook token — only after configuring and testing Brevo.
- `GOOGLE_API_KEY` — only when using lead discovery.
- `RAZORPAY_WEBHOOK_SECRET` — only after configuring the signed payment webhook.

Brevo credentials are not configured in the current environment. Keep live email/outreach and payment processing disabled until separate controlled acceptance tests and explicit approval.

## Safe schema release procedure

`app/database.py` is the schema source of truth. Schema changes are additive and repeatable. The configured `scripts/post-merge.sh` applies them to development after a task merge by running `python -m scripts.init_db`. This script is guarded against production. The app, health route, and Gunicorn workers never run DDL.

1. Add the schema change and tests; do not add production DDL to the startup or build command.
2. Apply the schema to development with `python -m scripts.init_db` and verify the feature with the test suite and preview.
3. Before a production release, use Replit Database → production → Settings to verify scheduled backups and a recent restore point. Replit-managed production supports scheduled backups and point-in-time restore.
4. The user starts Publish. Replit compares development and production schemas and applies the schema diff as part of Publish. Review warnings and rename confirmations carefully: an unconfirmed rename can be interpreted as drop-and-add and may lose data. Schema changes may cause brief downtime. Do not select "overwrite data" unless the deliberate goal is to replace production data.
5. After publishing, verify the published app's `/healthz` responds with HTTP 200, `status: ok`, and `database: postgresql`. This confirms the deployed process connected to its configured PostgreSQL database; a preview check is not production verification.

Production schema changes must go through Replit Publish. Do not run `scripts.init_db` against production, add a custom production migration job, or put DDL in the deployment command.

### Backups and rollback

- Before publishing a schema change, confirm a recent production restore point and scheduled backups in the Database tool. Replit documents daily scheduled backups and point-in-time restore for production databases.
- Rolling back the app build does not roll back database schema or data. If data recovery is required, use Database → production → Settings to restore to a point before the change, then redeploy code compatible with the restored schema. Writes after the selected restore point will be lost.
- Prefer additive and backward-compatible changes. Keep the prior app build until the new build and health checks pass.

## Existing SQLite records

Keep a protected copy of the SQLite file, then review the migration plan:

```sh
cp -p data/revenue_engine.db "$HOME/revenue_engine.sqlite.backup"
python -m scripts.migrate_sqlite_to_postgres --sqlite-path data/revenue_engine.db
```

The default is read-only dry-run. After reviewing counts and verifying the development PostgreSQL database is empty, records can be copied there with:

```sh
python -m scripts.migrate_sqlite_to_postgres --sqlite-path data/revenue_engine.db --apply
```

The copy preserves IDs, copies supported tables in foreign-key order inside one transaction, and refuses a nonempty target or unsupported populated source data. It does not delete the SQLite file or copy directly to production. Replit provisions a new production database from development during first publish; subsequent publishes do not copy development records into an existing production database. Never use "overwrite data" as a way to merge records. Migrating into an already-existing production database requires a separate approved and backed-up data migration. No SQLite records were copied during this task.

## Routes and operational safeguards

- `/` — customer homepage
- `/consultation` — consent-based consultation
- `/client/request` — delivery/revision request intake
- `/owner/login` and `/owner` — private operator dashboard
- `/healthz` — checks the configured database and core schema; does not expose connection details
- `/webhooks/brevo` and `/webhooks/razorpay` — authenticated/signed event endpoints

The request route does not email drafts to customers. Owner approval requires written-scope confirmation and a captured-payment record; uncertain send outcomes require reconciliation before retry. Lead discovery does not send email. Preserve these gates in any release.

## Acceptance gates before real use

1. Run `python -m compileall -q app scripts tests` and `python -m unittest discover -s tests -v`; run PostgreSQL integration tests only when a real development `DATABASE_URL` is available.
2. Verify preview `/healthz`, consultation consent persistence, owner-only draft access, payment approval gates, webhook authentication, suppression/opt-out behavior, and delivery-uncertain handling.
3. Publish only through the Replit UI after reviewing the deployment type/cost, schema diff, backup status, Production Secrets, and privacy settings.
4. After publish, verify `/healthz` on the published URL and confirm it reports PostgreSQL. Exercise controlled payment/email acceptance tests only with explicit approval and test-mode accounts.
5. Production is not considered verified until the published URL, production database connection, backups, HTTPS routes, and rollback path have all been checked.
