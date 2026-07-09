# Swiss Land Deal-Sourcing Pipeline — Vaud pilot

The ingestion + core layer of the asset-light land data platform. It pulls
official Swiss geodata, joins it into one enriched parcel-centric table in
PostGIS, and scores every parcel for three *legal* Swiss value-creation signals
(no rezoning, so no value-gain-levy trap): plot assembly, densification
underuse, and servicing gaps.

This is a broker's intelligence engine, not a landholding vehicle — you never
take title, so Lex Koller does not apply to you.

## What runs

```
fetch (geodienste.ch OGC API) ─┐
                               ├─► raw.*  ──enrich──► core.parcel ──score──► ranked shortlist
cantonal cadastre (viageo.ch) ─┘                          │
OEREB per-parcel (shortlist) ─────────────────────────────┘ (legal encumbrance)
```

Every parcel ends up in `core.parcel` with: dominant zone, build-zone flag,
developable area (parcel minus forest and non-build), building coverage ratio,
OEREB blocking flag, the three deal signals, and a 0–100 opportunity score.

## Data sources (all official, all free)

| Layer | Source | Access |
|---|---|---|
| Zoning / land-use plan (MGDM 73.1) | geodienste.ch `npl_nutzungsplanung` | OGC API Features / WFS |
| Planning zones (MGDM 76.1) | geodienste.ch `planungszonen` | OGC API Features / WFS |
| Forest boundaries (MGDM 157.1) | geodienste.ch `npl_waldgrenzen` | OGC API Features / WFS |
| Cadastral parcels | Cantonal (VD: viageo.ch / ASIT-VD) | registered download → local file |
| Buildings | swissTLM3D / cantonal | registered download → local file |
| Public-law restrictions | OEREB / PLR cadastre (VD: oereb.vd.ch) | per-parcel JSON extract |

Vaud zoning refreshes monthly on geodienste.ch. Parcel + building layers for VD
usually need a one-off registered export from viageo.ch; drop the GeoPackage in
`$DATA_DIR` and the loader picks it up (the OGC path needs no file).

## Files

```
sql/
  001_schema.sql        non-destructive schema (run every pipeline start)
  001_schema_reset.sql  destructive rebuild (first-time / hard reset only)
  002_enrich.sql        spatial joins → core.parcel
  003_score.sql         three signals + composite score
  900_test_fixture.sql  synthetic Lausanne parcels for validation
pipeline/
  config.py             endpoints, Vaud settings, thresholds, DB URL
  fetch_ogcapi.py       paginated OGC API Features client (bbox-filtered to VD)
  fetch_oereb.py        per-parcel OEREB extract fetcher (shortlist only)
  load.py               GeoJSON → raw.* loader + change detection
  run_pipeline.py       orchestrator (RUN_MODE: full | refresh | oereb)
```

## Run locally

```bash
pip install -r requirements.txt
createdb swissland && psql swissland -c "CREATE EXTENSION postgis; CREATE EXTENSION pgcrypto;"

# first-time hard reset of the schema
psql swissland -f sql/001_schema_reset.sql

# validate the whole chain on synthetic data (no network needed)
psql swissland -f sql/900_test_fixture.sql
psql swissland -f sql/002_enrich.sql
psql swissland -f sql/003_score.sql
psql swissland -c "SELECT parcel_no, opportunity_score, signal_assembly, signal_underuse, signal_servicing FROM core.parcel ORDER BY opportunity_score DESC;"

# real run against the live Swiss services
export DATABASE_URL=postgresql://.../swissland RUN_MODE=full
python pipeline/run_pipeline.py
```

## Deploy on Railway

1. New project → add the PostGIS plugin (injects `DATABASE_URL`).
2. Deploy this repo. `railway.json` sets the start command and Nixpacks GDAL/GEOS/PROJ.
3. First deploy: exec `psql $DATABASE_URL -f sql/001_schema_reset.sql` once.
4. Add a Cron schedule `0 4 * * *` with `RUN_MODE=full` for the nightly rebuild.
5. Optional second cron `0 * * * *` with `RUN_MODE=oereb` to keep the shortlist's
   legal status current.

## Scoring — how a parcel earns its score (0–100)

- up to **30** for developable size (capped at 5 000 m²)
- **+25** underuse (building zone, developable, coverage < 25%)
- **+20** servicing (building zone, essentially unbuilt, coverage < 3%)
- **+20** assembly (touches a same-use building-zone neighbour)
- **+5** planning zone (plan under revision = optionality)
- **forced to 0** if non-build, OEREB-blocked, or zero developable area

Thresholds live inline in `sql/003_score.sql` — promote to a config table when
you start tuning per commune.

## Validation status

The enrichment + scoring logic is tested end-to-end against a synthetic Vaud
fixture (`sql/900_test_fixture.sql`) covering every path: residential vs
agricultural zoning, forest clipping, high vs low building coverage, planning
zones, and OEREB encumbrance. The change-detection layer is verified idempotent
(no false alerts on unchanged re-runs). The one thing that needs your input is
the cantonal parcel + building export from viageo.ch — the OGC layers are wired
and working, but VD gates parcel geometry behind a free registered download.

## Known limitations / next steps

- Parcel + building layers need the viageo.ch registered export (VD policy).
- `is_building_zone` classification uses the harmonized primary-use codes; a
  handful of special-use zones are optimistically included — tighten per canton.
- The assembly signal detects adjacency, not common ownership; owner data is not
  in open layers, so "likely split ownership" is inferred from parcel count.
  Confirm ownership at the dossier stage via the land register.
- Uplift/price estimates are not modelled here — that's the dashboard layer
  (the deal calculator from the analysis platform plugs in on top of this).
```
