# Deployment source of truth

Use this combined release candidate for deployment, not the earlier static landing-page-only experiment and not the unexpanded October 12 international ZIP by itself.

## What was merged conceptually

- The business owner's international-market candidate: country hypotheses, market validation gates, country-aware lead discovery option and website-positioning guidance.
- The integrated Replit candidate: Flask customer consultation, Hugging Face assistant, client delivery/revision request workflow, private owner dashboard, lead/outreach modules, notifications, Brevo/Razorpay webhook processing and tests.
- Deployment alignment updates: India domestic-baseline profile, country field/migration for lead records, country selector in the dashboard, persistent validation status, and enforcement that blocks live outreach until the chosen market is marked validated.

## Market operating model

India remains enabled as a domestic benchmark. International markets remain eligible for research and controlled tests from the configured list, one country/city at a time. Discovery itself does not send email. The send operation requires the country's validation checklist and dated evidence notes when `MARKET_VALIDATION_REQUIRED=true`.

## Deployment layout

- GitHub: version control and reviewed release branch.
- Replit: Flask web runtime, customer consultation, Hugging Face calls, owner dashboard, request queue and webhook routes.
- Hugging Face: inference API called server-side; its token belongs in Replit deployment secrets, never in browser code or Git.
- Database: current release uses SQLite. The deployment must provide persistent storage for `OUTREACH_DB`; otherwise port and test the data layer against a managed PostgreSQL database before real customer records or live outreach are enabled.
- Static website: GitHub Pages; connect it to the published Flask HTTPS URL only after backend routes, consent, session cookies and storage have passed smoke tests.

## Do not do these things

- Do not copy `.env`, provider keys, production database files, or private customer data into a public repository/archive.
- Do not replace `main` or the website repository with the backend source.
- Do not launch a live campaign from lead discovery alone.
- Do not send a generated delivery file before owner login, written-scope confirmation and payment verification.
- Do not publish a SQLite-backed app to an ephemeral filesystem and assume customer records will survive restarts.
