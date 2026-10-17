# Phase 1 automation deployment runbook

## Current status

The Overture discovery worker, recipient deduplication, persistent reminder scheduler, consultation handoff and inbound-reply parser are being validated on the development branch. **Do not enable scheduled live automation yet.** The code must be merged to the correct branches, the Replit app source deployed, the production schema migrated, and acceptance checks completed first.

Keep the GitHub Actions variable REVENUE_ENGINE_AUTOMATION_ENABLED unset or false until the checklist below passes.

## 1. Production duplicate-email preflight

Before creating the normalized unique email index, run this query in the Production database SQL console as a read-only query:

~~~sql
SELECT COUNT(*) AS duplicate_email_groups
FROM (
    SELECT LOWER(TRIM(email)) AS normalized_email
    FROM public.leads
    WHERE email IS NOT NULL AND TRIM(email) <> ''
    GROUP BY LOWER(TRIM(email))
    HAVING COUNT(*) > 1
) AS duplicate_groups;
~~~

It must return zero. If it does not, do not automatically delete duplicate leads or outreach history. Reconcile the records deliberately, retaining historical outreach and suppression records, and rerun the query.

## 2. Apply the schema change

1. Back up the Production database.
2. Publish the approved Replit app source containing the app/database.py changes. The production app deliberately does not run schema migrations automatically.
3. Open Replit Database → Production (or the linked Neon console) → SQL editor.
4. After the duplicate-email preflight returns zero, run deploy/phase1_production_schema.sql as the database owner.
5. Verify that follow_up_schedule, consultation_handoff_tokens and consultation_submissions.lead_id exist.

The migration adds the persistent reminder queue, expiring consultation tokens, the lead link on consultation records and normalized-email uniqueness. It does not delete existing leads, outreach, payments or client requests.

## 3. Create the restricted automation database role

Create a non-owner PostgreSQL login named revenue_engine_automation (or another name consistently substituted below) in the same Production database. Use a strong generated password. This role must not own the schema and must not have delete, DDL, payment, client-request or generated-file privileges. It may insert aggregate-only module run status for the owner dashboard, but must not read the existing module-run logs.

Run these grants in Production as the database owner:

~~~sql
GRANT USAGE ON SCHEMA public TO revenue_engine_automation;
GRANT SELECT, INSERT ON TABLE public.leads TO revenue_engine_automation;
GRANT UPDATE (state, updated_at) ON TABLE public.leads TO revenue_engine_automation;

GRANT SELECT, INSERT, UPDATE ON TABLE public.outreach TO revenue_engine_automation;
GRANT SELECT (email) ON TABLE public.suppressions TO revenue_engine_automation;
GRANT SELECT (status) ON TABLE public.market_validations TO revenue_engine_automation;

GRANT INSERT ON TABLE public.state_events TO revenue_engine_automation;
GRANT INSERT ON TABLE public.outreach_events TO revenue_engine_automation;
GRANT SELECT, INSERT, UPDATE ON TABLE public.follow_up_schedule TO revenue_engine_automation;
GRANT INSERT ON TABLE public.consultation_handoff_tokens TO revenue_engine_automation;
GRANT INSERT ON TABLE public.module_runs TO revenue_engine_automation;

DO $$
DECLARE
    table_name text;
    sequence_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'leads', 'outreach', 'state_events', 'outreach_events',
        'follow_up_schedule', 'consultation_handoff_tokens', 'module_runs'
    ] LOOP
        sequence_name := pg_get_serial_sequence('public.' || table_name, 'id');
        IF sequence_name IS NOT NULL THEN
            EXECUTE format(
                'GRANT USAGE, SELECT ON SEQUENCE %s TO revenue_engine_automation',
                sequence_name
            );
        END IF;
    END LOOP;
END
$$;
~~~

PostgreSQL often grants CONNECT to PUBLIC by default. If the role cannot connect, run SELECT current_database(); then grant CONNECT on the resulting database name as a quoted identifier.

Obtain a Production connection URI for the restricted role. In GitHub repository Settings → Secrets and variables → Actions, save it as REVENUE_ENGINE_AUTOMATION_DATABASE_URL. Never print the URI or paste it into chat. The existing REVENUE_ENGINE_PRODUCTION_DATABASE_URL is for read-only preview/check jobs; do not use a full owner login for scheduled writes.

## 4. Configure Replit secrets

In the Replit app's Secrets panel, set:

- PUBLIC_APP_URL=https://revenue-engine--noreplysanchita.replit.app
- BREVO_API_KEY
- BREVO_SENDER
- BREVO_REPLY_TO
- BREVO_WEBHOOK_TOKEN
- OWNER_DASHBOARD_PASSWORD
- FLASK_SECRET_KEY

The public app URL must be the actual HTTPS published URL, not the placeholder in .env.example. Do not commit secrets.

## 5. Configure Brevo inbound replies and delivery events

The application accepts transactional delivery events at POST /webhooks/brevo and parsed inbound replies at POST /webhooks/brevo/inbound. Both routes require the bearer token configured as BREVO_WEBHOOK_TOKEN.

For inbound parsing:

1. Choose a receiving subdomain you own that differs from the sending domain; for example reply.yourdomain.com. Verify the domain with Brevo.
2. Add the inbound MX records for that subdomain: priority 10 → inbound1.sendinblue.com.; priority 20 → inbound2.sendinblue.com.
3. Set BREVO_REPLY_TO to an address at that receiving domain.
4. In Brevo, create an inbound webhook with type inbound, event inboundEmailProcessed, URL https://revenue-engine--noreplysanchita.replit.app/webhooks/brevo/inbound, the receiving domain, and Bearer token authentication using BREVO_WEBHOOK_TOKEN.
5. Configure the transactional event webhook to send delivery, hard-bounce, spam/complaint and unsubscribe events to /webhooks/brevo using the configured token.

Brevo requires the receiving subdomain to be configured and DNS changes can take time. Until replies and unsubscribe events are verified end-to-end, keep live sending disabled. The inbound handler classifies and records replies, suppresses opt-outs and cancels reminders; it never sends an automatic response to STOP.

## 6. GitHub Actions settings

After the new code and workflows are on the default branch, add these repository Actions secrets:

- REVENUE_ENGINE_AUTOMATION_DATABASE_URL — restricted write role from step 3.
- REVENUE_ENGINE_PRODUCTION_DATABASE_URL — existing Production database URI for read-only preview/check jobs.
- BREVO_API_KEY, BREVO_SENDER, BREVO_REPLY_TO, BREVO_WEBHOOK_TOKEN, PUBLIC_APP_URL.

Keep the repository Actions variable REVENUE_ENGINE_AUTOMATION_ENABLED false or unset until all acceptance checks pass. The daily schedule is 03:30 UTC (09:00 India). A run is capped at five messages; the daily cap is ten across the worker's activity. Scheduled jobs cannot overlap.

## 7. Acceptance checklist before activation

- Read-only Overture preview finds candidates and reports aggregate counts only.
- Production migration succeeds; normalized duplicate-email preflight returns zero.
- The restricted worker role can operate the lead/outreach/reminder tables but cannot access payments, client requests or generated files.
- The Replit app is published with the new source, and /healthz returns HTTP 200.
- PUBLIC_APP_URL is the actual HTTPS URL. A prepared test first-contact message has a working one-time consultation link without exposing the recipient's email in the URL.
- Brevo sender and reply-to are verified. One test email to an inbox controlled by the owner is accepted and a delivery event is received.
- An inbound email tests matching, duplicate webhook behavior, an interested reply and STOP; STOP must create suppression and cancel pending reminders.
- A reminder becomes due and is processed only once. Replies, bounces, complaints and opt-outs cancel the queue.
- Ambiguous provider outcomes are not automatically retried.
- India's market record is marked VALIDATED with the required dated evidence. This is an owner attestation, not a substitute for legal review.
- The full unit-test suite passes.
- Only then set REVENUE_ENGINE_AUTOMATION_ENABLED=true.

## 8. Product expansion (Phase 2)

After the acquisition/consultation loop is stable, add a separate Business Website catalogue entry and delivery generator, then implement Payment Page, Lead Capture, Auto-Response, Follow-Up, Business Automation, Custom Outreach and Custom Automation delivery lanes. Use one catalogue, one consultation record, one client-request dashboard, and one payment/agreement gate. Generated artifacts must remain private drafts until written scope, captured payment and explicit owner approval.
