# GitHub-primary production deployment

This repository is the source of truth for Revenue Engine. Replit may be used to edit and preview work, but approved changes should be transferred into a reviewed GitHub pull request. Production is deployed from `main` only after CI passes.

## Important architecture boundary

GitHub stores the source, versions, runs the tests, and can coordinate deployments. GitHub Pages is static hosting; it cannot run this Flask server. The Flask service and its PostgreSQL database must run on a persistent application host.

The website repository (`Sanchit3133/sanchit-agarwal-website`) remains the public marketing site. Once the production backend hostname is verified, the website can link its consultation/client portal buttons to that host. Do not embed secrets or private client data in the static website.

## Recommended low-maintenance deployment path

1. Use DigitalOcean App Platform with this repository connected at branch `main`, root directory, and the root `Dockerfile`. Enable automatic deploy on push. App Platform supports Git-repository deployment; this Dockerfile creates a non-root Python container.
2. Attach a **production managed PostgreSQL database**, not an ephemeral or development database. Configure the application with the database's private connection string as the secret `DATABASE_URL`; set `DATABASE_BACKEND=postgresql` and `ENVIRONMENT=production`.
3. Configure runtime secrets in the hosting dashboard, not in GitHub files. At minimum set `FLASK_SECRET_KEY`, `OWNER_DASHBOARD_PASSWORD`, `DATABASE_URL`, and the selected AI provider settings. Add Brevo, Razorpay, Google, and other provider keys only when the related integration is being enabled.
4. Add a health check for `/healthz`. The health check must pass against the new PostgreSQL database before routing the public website to the new backend.
5. Configure a dedicated app subdomain (proposed: `app.sanchit-agarwal.me`) in the host and DNS. Use the target host's exact DNS instructions; do not change the website's existing root-domain records.
6. Enable daily backups and test a restore procedure. A single-node managed database is not the same as a highly available cluster; keep a separate protected export for critical records.
7. Only after data migration, smoke tests, and webhook/payment checks pass should the website's consultation and client-request links change to the new hostname. Keep Replit available during a rollback window.

## Cost note (verify current prices before creating resources)

DigitalOcean's current public pricing lists App Platform shared/fixed containers from $5/month and a single-node managed PostgreSQL database from $15/month. That is about $20/month before taxes or extras at the smallest container size; a 1 GiB container is listed at $10/month, making that pairing about $25/month. A 512 MiB app may be too constrained as the app's load grows.

If an existing DigitalOcean Droplet/database from earlier setup is already available, check it before creating new resources to avoid duplicate charges. A small Droplet can cost less but requires you to operate PostgreSQL, OS patches, TLS, firewall, backup, restore, and deployment automation yourself.

## Production data migration is a separate gate

Do not assume Replit preview and published production use the same database. Before cutover:

- Confirm the authoritative Replit production database and take a restorable backup.
- Record the counts of leads, client requests, consultation submissions/messages, payments, webhook events, suppression rows, and audit records.
- Use a reviewed export/import runbook that preserves IDs, foreign keys, payment references, consent timestamps, and audit history.
- Verify destination counts and sample rows before opening public traffic.
- Never commit database dumps, API keys, customer emails, or environment files to Git.

The existing `scripts.init_db` intentionally refuses to run in `ENVIRONMENT=production`; do not bypass that guard or run an unreviewed schema migration against production. A first-time host provisioning and data migration procedure must be explicitly prepared for the selected host/database before cutover.

## Website and customer-delivery plan

- Public marketing site: `sanchit-agarwal.me`, deployed from `sanchit-agarwal-website` using GitHub Pages.
- Revenue Engine customer portal: Flask routes for consultation and client requests at the production app subdomain.
- Owner panel: remains behind the app's existing owner authentication. Before production, change the owner password, use a strong unique `FLASK_SECRET_KEY`, and verify cookie/TLS settings.
- Customer-specific deliverables: after the GitHub integration is implemented, the owner can authorize the app via a GitHub App/OAuth flow and approve each repository/release operation from the owner panel. Never collect a GitHub password or store a personal access token in source code. The app should request only the repository permissions it needs and record an audit event for each action.
- Static customer sites may be deployed to GitHub Pages; customer sites needing server-side code or a database require a runtime host. Store each project's repository, target URL, status, commit, and approval trail in Revenue Engine. Only send customer delivery messages after the owner has verified the deployment URL and approved delivery.

## Release checklist

1. Pull request to `main` passes GitHub Actions.
2. Deployment host is configured with secrets and persistent PostgreSQL.
3. Database backup and reviewed migration complete.
4. `/healthz` reports healthy PostgreSQL; consultation, client request, owner login, file generation/review, and payment/webhook test paths have been exercised safely.
5. Public website links are updated to the verified app hostname.
6. Replit remains the temporary fallback until the permanent deployment and restore process have both been verified.
