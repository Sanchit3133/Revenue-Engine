# SQLite and PostgreSQL operations

## Backend selection and environment isolation

- `DATABASE_BACKEND=auto` (the default) uses PostgreSQL when `DATABASE_URL` is present.
- Replit provides separate development and production PostgreSQL databases. The preview uses the development database; the published app receives the production database's `DATABASE_URL`. Replit creates/manages that production connection and syncs it to the deployment secret configuration; do not copy the connection string into source or logs.
- `DATABASE_BACKEND=postgresql` requires `DATABASE_URL`. Production is detected by `REPL_DEPLOYMENT=1` and refuses to start without the URL or with `DATABASE_BACKEND=sqlite`.
- SQLite remains available for local development and isolated tests. `OUTREACH_DB` selects the SQLite file only.
- For an external PostgreSQL provider, set its URL separately in the development and production secret environments. Never reuse a development URL as a production URL by assumption.

## Safe schema release procedure

`app/database.py` is the schema source of truth. Schema changes are additive and repeatable. The web app, Gunicorn workers, and `/healthz` do not run DDL.

1. Make an additive schema change in source and add/update tests.
2. Apply it to development with `python -m scripts.init_db`. The configured Replit post-merge script (`scripts/post-merge.sh`) runs this same development-only command after a task merge. `init_db()` refuses production.
3. Run the test suite and exercise the feature in the preview. Check `/healthz` reports `database: postgresql`.
4. Before publishing, confirm a recent production restore point exists in Replit's Database tool → production database → Settings. Enable/verify scheduled backups and understand the point-in-time restore option.
5. The user starts Publish. Replit compares the development and production schemas and applies the schema diff during Publish. Review the diff and confirm any rename prompts; an unconfirmed rename may be interpreted as drop-and-add. Schema changes can cause brief downtime. Do not select the "overwrite data" option unless intentionally replacing production data.
6. After the user publishes, check the published app's `/healthz`. An HTTP 200 with `database: postgresql` confirms that the deployed app connected to its configured PostgreSQL database. Do not infer production health from the preview.

Never run `scripts.init_db` against production, add DDL to app startup/build commands, or create a custom production migration script. The published process in `Procfile` only starts Gunicorn.

### Backup and rollback

- Before a schema release, check Replit's production Database tool Settings for scheduled daily backups and a recent restore point. Replit-managed production PostgreSQL supports point-in-time restore and scheduled backups. Keep a separate protected backup for data that must survive a restore window.
- Rolling back application code does not undo a database schema change. If a release requires data recovery, use Replit Database → production → Settings to restore to a point before the change, then deploy code compatible with that schema. A restore also removes data written after the selected restore point; coordinate this before restoring.
- Prefer additive, backward-compatible schema changes and keep the previous app build available until the new build and health check pass.

## Preserving records from SQLite

The existing SQLite file is not deleted or changed by the PostgreSQL setup. Back it up to a protected location outside Git, then inspect the read-only migration plan:

```sh
cp -p data/revenue_engine.db "$HOME/revenue_engine.sqlite.backup"
python -m scripts.migrate_sqlite_to_postgres --sqlite-path data/revenue_engine.db
```

The migration command is dry-run by default. It reports record counts, opens SQLite read-only, and writes nothing without `--apply`. The apply step targets development PostgreSQL only, requires every Revenue Engine table there to be empty, preserves IDs, copies supported records in foreign-key order in one transaction, and updates identity sequences. It stops rather than overwriting, skipping, or deleting data.

Only after reviewing the dry run and confirming that the development PostgreSQL target is empty:

```sh
python -m scripts.migrate_sqlite_to_postgres --sqlite-path data/revenue_engine.db --apply
```

Keep the SQLite backup until the migrated records are checked. Replit provisions a new production database from the development database during the first publish; on later publishes, development records are not copied into an already-existing production database. Do not use Publish's overwrite-data option to merge datasets. This CLI deliberately cannot import directly into production; moving SQLite records into an already-provisioned production database requires a separate, explicitly approved data-migration procedure and a verified backup. No SQLite records were copied as part of the current PostgreSQL setup.

## Run and verify

The Replit preview workflow runs on port 5000:

```sh
python -m scripts.init_db
python -m scripts.run_web
```

The production WSGI command is defined in `Procfile` and binds to Replit's `$PORT`. `/healthz` checks the configured backend and core `leads` table without exposing connection details; it returns HTTP 503 if the database or schema is unavailable.

```sh
python -m compileall -q app scripts tests
python -m unittest discover -s tests -v
```

PostgreSQL integration tests run only when a real `DATABASE_URL` is available; otherwise they skip.
