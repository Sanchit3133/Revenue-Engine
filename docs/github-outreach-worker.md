# GitHub website-seed outreach worker

## Purpose

The intended split is:

- **GitHub Actions:** bounded background discovery/qualification and campaign preparation.
- **Replit + PostgreSQL:** customer-facing consultation app, owner dashboard, and shared durable workflow state.
- **Brevo:** transactional/outreach delivery only after sender verification and explicit controlled tests.

The current website-seed worker accepts up to five HTTPS official business websites supplied by the operator. It checks `robots.txt`, stays on the same site, looks at the home page and four conventional contact paths at most, and stores only qualified leads with a publicly displayed email. It uses only aggregate counts in the public Actions log.

It does not use Google Places, run a schema migration, create outreach records, or send an email. Do not re-enable the retired Google Places export-style Actions workflow without reviewing the applicable Google Maps Platform terms and permitted use.

## Secrets

Add these through GitHub repository Settings → Secrets and variables → Actions:

- `REVENUE_ENGINE_PRODUCTION_DATABASE_URL`: production connection string already used for the read-only preview workflow; use only for preview.
- `REVENUE_ENGINE_WORKER_DATABASE_URL`: a connection string for a **separate restricted PostgreSQL role**, used only for persistence.
- Do not add a full owner/admin production database URL as the write-worker credential.
- Do not put Google Places or Brevo credentials in this worker. This worker does not use them.

## Create the restricted worker database role

Use Replit's Database tool, choose **Production**, and create a new PostgreSQL role/login in the connected Neon database. Name it `revenue_engine_worker` if available and give it a unique strong generated password. Do not paste its connection string into chat. If Replit's UI does not expose role creation, open the linked Neon Console and use the production branch's Roles interface.

Run the following grants in the **Production** database SQL runner as the database owner after creating the role. These grants are intentionally limited to the columns the website-seed worker needs; they do not permit deletes, schema changes, outreach writes, payments, consultations, or access to draft content.

```sql
GRANT USAGE ON SCHEMA public TO revenue_engine_worker;

GRANT SELECT (id, email, website, state)
  ON TABLE public.leads TO revenue_engine_worker;
GRANT INSERT (place_id, business_name, website, email, country, qualification, score, signal, notes, state)
  ON TABLE public.leads TO revenue_engine_worker;
GRANT UPDATE (state, updated_at)
  ON TABLE public.leads TO revenue_engine_worker;

GRANT SELECT (email)
  ON TABLE public.suppressions TO revenue_engine_worker;
GRANT INSERT (lead_id, old_state, new_state, reason)
  ON TABLE public.state_events TO revenue_engine_worker;

DO $$
DECLARE
  sequence_name text;
BEGIN
  sequence_name := pg_get_serial_sequence('public.leads', 'id');
  IF sequence_name IS NOT NULL THEN
    EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO revenue_engine_worker', sequence_name);
  END IF;

  sequence_name := pg_get_serial_sequence('public.state_events', 'id');
  IF sequence_name IS NOT NULL THEN
    EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO revenue_engine_worker', sequence_name);
  END IF;

  EXECUTE format('GRANT CONNECT ON DATABASE %I TO revenue_engine_worker', current_database());
END
$$;
```

If the role manager assigns a different login name, replace `revenue_engine_worker` consistently in the grants. Obtain a connection URI for that role and the same production database from the Neon/Replit connection tools. Add the full URI as the GitHub secret `REVENUE_ENGINE_WORKER_DATABASE_URL`. Never print it in logs.

## Run modes

### Preview only

GitHub Actions → **Revenue Engine official website seed preview**. Provide one to five owner-reviewed official HTTPS website URLs separated by commas, choose a configured country, and type `WEBSITE_SEED_PREVIEW`. This uses the production connection in a PostgreSQL read-only transaction. It reports aggregate counts only.

Website URL inputs are part of a manual workflow run on a public repository. Submit only public business website URLs, never passwords, emails lists, or private customer data. Use only sites where automated access and business-contact use are permitted, and honor each site's robots policy and terms.

### Persist qualified leads

Only after reviewing a successful preview and configuring the limited worker role, run **Revenue Engine persist approved website leads** with the same URLs/country and type `PERSIST_MAX_5_LEADS`.

Persistence is manual-only and capped at five leads per run. It performs an additional duplicate and suppression check. It creates qualified lead and state-history records, but no outreach rows. It outputs aggregate counts only. It does not send email.

## Next stages

Before creating prepared outreach rows, add a separate manual preparation step using the same restricted-write design. It must preserve suppression checks, at most two follow-ups, budget-first message rules, and existing campaign limits. Before any live email, configure and verify Brevo API key/sender, test one message to an inbox controlled by the owner, and verify provider events and opt-out handling. Do not enable a schedule until the manual path has been reviewed end-to-end.

Production schema changes must go through Replit Publish. No GitHub workflow in this design runs `init_db()` or a migration against production.
