# Deploying on Railway — read this first

This project is a **flat directory** on purpose. GitHub's web drag-and-drop
cannot preserve folders, so everything lives at the repo root and
`run_pipeline.py` finds its `.sql` files next to itself. Do not re-nest the
files into `pipeline/` and `sql/` subfolders.

## Step 1 — Get the files onto GitHub without losing any

The earlier failure happened because drag-and-drop uploaded only some files.
Use ONE of these instead:

**Option A — upload the ZIP contents (simplest).** In your empty GitHub repo:
"Add file" → "Upload files", then drag in **all** the loose files from this
folder at once (select everything inside, not the containing folder). Confirm
you see all of these before committing:

```
001_schema.sql   001_schema_reset.sql   002_enrich.sql   003_score.sql
900_test_fixture.sql   config.py   fetch_ogcapi.py   fetch_oereb.py
load.py   run_pipeline.py   requirements.txt   railpack.json
Procfile   runtime.txt   .env.example   README.md   DEPLOY.md
```

Seventeen files. If any are missing, the build fails again.

**Option B — git command line (most reliable):**
```bash
git clone https://github.com/<you>/<repo>.git
cd <repo>
cp /path/to/these/files/* .        # copy everything in
git add -A && git commit -m "Swiss land pipeline" && git push
```

## Step 2 — Provision a PostGIS database (NOT plain Postgres)

Railway's default "Add Postgres" plugin does **not** include PostGIS, so
`CREATE EXTENSION postgis` will fail on first run. Use a PostGIS image:

- In your Railway project: "New" → "Database" → "Add PostgreSQL", then in the
  service settings change the image to `postgis/postgis:16-3.4` (or any
  `postgis/postgis` tag). Redeploy the database.
- Alternatively: "New" → "Docker Image" → `postgis/postgis:16-3.4` and set a
  `POSTGRES_PASSWORD` variable.

The database service exposes `DATABASE_URL`. Reference it from the app service
(Variables → "Add reference" → the Postgres service's `DATABASE_URL`).

## Step 3 — Set the app service to run as a scheduled job, not a server

The pipeline runs once and exits. A normal Railway service expects a
long-running process and will restart it in a loop. Two clean options:

**Cron (recommended).** In the app service → Settings → set a **Cron Schedule**,
e.g. `0 4 * * *` (04:00 daily). Railway then runs the start command on schedule
and treats exit code 0 as success, not a crash. Set the restart policy to
"Never".

**Variables to set on the app service:**
```
RUN_MODE=full        # full nightly rebuild; use refresh/oereb for others
PILOT_ONLY=true      # restrict to Lausanne pilot communes for the first runs
DATABASE_URL=${{Postgres.DATABASE_URL}}   # reference, set via the UI
```

## Step 4 — First run

The first scheduled run (or a manual "Deploy") will:
1. create the schema (tables auto-created via the non-destructive schema file),
2. fetch the geodienste.ch OGC layers (zoning, planning zones, forest) for Vaud,
3. enrich + score + detect changes.

Parcel geometry and buildings still need the viageo.ch registered export (see
README). Until you add that, the zoning/forest/planning layers load and the
`core.parcel` table stays empty of parcels — that's expected, not a failure.

## Why the build failed before

Railpack saw six flat files and no `requirements.txt`, so it couldn't detect a
Python app or its dependencies. This package fixes that: `requirements.txt`,
`railpack.json`, `Procfile`, and `runtime.txt` are all present at the root, and
`geopandas`/`shapely` (which need system GEOS/GDAL and are the most common
Railway build failure) have been removed — the OGC pipeline doesn't use them.
