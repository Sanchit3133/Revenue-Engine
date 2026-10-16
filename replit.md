# Revenue Engine on Replit

## Run the app

Replit provides separate development and production PostgreSQL databases. The preview uses the development `DATABASE_URL`; Replit automatically syncs the production database URL to the published deployment's secret configuration. A published deployment is detected through `REPL_DEPLOYMENT=1`, requires that URL, and never silently falls back to a local SQLite file. SQLite remains available for local development and isolated tests.

For a new or upgraded development database, apply the additive schema before starting the preview. The Replit preview workflow uses the project's Flask entry point:

```sh
python -m scripts.init_db
python -m scripts.run_web
```

The app itself does not change schemas at startup or from requests. Apply development schema updates explicitly; `scripts/post-merge.sh` applies them after task merges. Replit Publish compares development and production schemas and applies production schema changes. Do not run the development schema command against production or choose Publish's overwrite-data option unless replacing production data is intended.

The published WSGI process uses the repository's `Procfile` (`gunicorn`); production schema changes still go through Publish and are not performed by that process.

## Existing SQLite data

Use the read-only dry run in `docs/database-postgresql.md` before copying records. The migration requires an empty development PostgreSQL database, preserves IDs and foreign-key order, and aborts rather than overwriting existing PostgreSQL data. It never writes to production. Keep a backup of the SQLite file until the PostgreSQL copy is checked; a new production database is provisioned from development at first publish, but existing production rows are not replaced by later development changes.

## Health and tests

`/healthz` checks the configured backend and core schema and reports the backend name without exposing connection details. Run the full suite with `python -m unittest discover -s tests`; PostgreSQL integration checks run when `DATABASE_URL` is configured.
