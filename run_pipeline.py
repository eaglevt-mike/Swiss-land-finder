"""
Orchestrator — one entry point for the whole ingest→score→detect cycle.

Run modes (env RUN_MODE):
  full     fetch every harmonized layer, load, enrich, score, detect (default)
  refresh  skip fetch, just re-enrich + re-score + detect from existing raw.*
  oereb    fetch OEREB extracts for the current top-N shortlist only

On Railway, schedule `python run_pipeline.py` daily (full) and optionally an
hourly `RUN_MODE=oereb` to keep the shortlist's legal status fresh.
"""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path

from config import SOURCES
import load as L
import fetch_ogcapi as F
import fetch_oereb as O

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
SCHEMA = SQL_DIR / "001_schema.sql"
ENRICH = SQL_DIR / "002_enrich.sql"
SCORE = SQL_DIR / "003_score.sql"


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure_schema(cur):
    L.run_sql_file(cur, str(SCHEMA))
    log("schema ensured")


def fetch_and_load_all(conn):
    cur = conn.cursor()

    log("fetching zoning...")
    L.truncate_raw(cur, "raw.zoning")
    n = L.load_zoning(cur, F.fetch_layer(SOURCES["zoning"]))
    log(f"  zoning: {n} polygons")

    log("fetching planning zones...")
    L.truncate_raw(cur, "raw.planning_zones")
    n = L.load_generic(cur, "raw.planning_zones",
                       F.fetch_layer(SOURCES["planning_zones"]),
                       columns=["pz_id", "commune_bfs", "status"])
    log(f"  planning zones: {n}")

    log("fetching forest...")
    L.truncate_raw(cur, "raw.forest")
    n = L.load_generic(cur, "raw.forest",
                       F.fetch_layer(SOURCES["forest"]),
                       columns=["fg_id"])
    log(f"  forest: {n}")

    conn.commit()
    cur.close()


def enrich_and_score(conn):
    cur = conn.cursor()
    L.run_sql_file(cur, str(ENRICH)); log("enriched")
    L.run_sql_file(cur, str(SCORE));  log("scored")
    changed = L.detect_changes(cur)
    conn.commit(); cur.close()
    log(f"change detection: {changed} parcels changed/new")
    return changed


def refresh_oereb_shortlist(conn, top_n: int = 100):
    """Fetch OEREB extracts for the current top-N scored parcels, then re-enrich
    the encumbrance flag and re-score so newly-blocked parcels drop out."""
    cur = conn.cursor()
    cur.execute("""
        SELECT egrid FROM core.parcel
        WHERE egrid IS NOT NULL AND opportunity_score > 0
        ORDER BY opportunity_score DESC NULLS LAST
        LIMIT %s
    """, (top_n,))
    egrids = [r[0] for r in cur.fetchall()]
    log(f"OEREB: checking {len(egrids)} shortlisted parcels")
    if egrids:
        cur.execute("DELETE FROM raw.oereb_restrictions WHERE egrid = ANY(%s)", (egrids,))
        n = L.load_oereb(cur, O.fetch_for_egrids(egrids))
        conn.commit()
        log(f"OEREB: wrote {n} restriction rows")
    cur.close()


def main():
    mode = os.getenv("RUN_MODE", "full")
    log(f"pipeline start (mode={mode})")
    conn = L.connect()
    try:
        cur = conn.cursor(); ensure_schema(cur); conn.commit(); cur.close()

        if mode == "full":
            fetch_and_load_all(conn)
            enrich_and_score(conn)
        elif mode == "refresh":
            enrich_and_score(conn)
        elif mode == "oereb":
            refresh_oereb_shortlist(conn)
            enrich_and_score(conn)
        else:
            log(f"unknown RUN_MODE '{mode}'"); sys.exit(2)

        log("pipeline done")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
