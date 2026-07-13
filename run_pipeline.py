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
import fetch_sitg as FS
import fetch_oereb as O

# resolve SQL files next to this script, whatever the working directory is,
# so a flattened repo layout can't break the paths.
SQL_DIR = Path(__file__).resolve().parent
SCHEMA = SQL_DIR / "001_schema.sql"
MIGRATE = SQL_DIR / "000_migrate.sql"
ENRICH = SQL_DIR / "002_enrich.sql"
SCORE = SQL_DIR / "003_score.sql"
PHASEA_SCHEMA = SQL_DIR / "A01_phasea_schema.sql"
PHASEA_BUILD = SQL_DIR / "A02_phasea_build.sql"
PHASEB_SCHEMA = SQL_DIR / "B01_phaseb_schema.sql"
PHASEB_BUILD = SQL_DIR / "B02_phaseb_build.sql"


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure_schema(cur):
    L.run_sql_file(cur, str(SCHEMA))
    # Additive, idempotent column migrations. Runs after the schema file so the
    # tables exist, and upgrades an older database in place (no manual reset).
    try:
        L.run_sql_file(cur, str(MIGRATE))
        log("schema ensured (+ migrations)")
    except Exception as e:
        log(f"schema ensured (migration note: {e})")


def fetch_and_load_all(conn):
    cur = conn.cursor()

    # Parcels & buildings from the live AV endpoint stream the whole country and
    # can't reach Vaud in reasonable time (see project notes). Phase A needs only
    # zoning/planning/forest, so these are OFF by default. Set FETCH_PARCELS=true
    # to re-enable (e.g. once a targeted per-commune fetch is built in Phase B).
    if os.getenv("FETCH_PARCELS", "false").lower() == "true":
        _fetch_parcels_and_buildings(conn, cur)
    else:
        log("skipping parcels+buildings (FETCH_PARCELS not set) — Phase A uses zoning only")

    log("fetching Geneva zoning (SITG)...")
    L.truncate_raw(cur, "raw.zoning")
    try:
        total = FS.count_features()
        log(f"  SITG reports {total} zoning features for Geneva")
        n = L.load_zoning_sitg(cur, FS.fetch_zoning())
        conn.commit()
        log(f"  zoning: {n} polygons loaded")
    except Exception as e:
        conn.rollback()
        log(f"  zoning fetch FAILED ({e})")

    # Planning zones + forest still come from geodienste (national). They are
    # supplementary signals only; Phase A's core signal is the Geneva building
    # zones above. Non-fatal: a failure here must not block the ranking.
    if os.getenv("FETCH_SUPPLEMENTARY", "false").lower() == "true":
        try:
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
        except Exception as e:
            conn.rollback()
            log(f"  supplementary layers skipped ({e})")
    else:
        log("skipping supplementary layers (FETCH_SUPPLEMENTARY not set)")

    conn.commit()
    cur.close()


def _fetch_parcels_and_buildings(conn, cur):
    """Optional AV parcels + buildings fetch. Off by default (streams nationwide,
    can't reach VD in budget). Kept for the Phase B per-commune approach."""
    log("fetching cadastral parcels (AV)...")
    L.truncate_raw(cur, "raw.parcels")
    conn.commit()
    try:
        n = L.load_parcels(cur, F.fetch_layer(SOURCES["parcels"]))
        conn.commit()
    except Exception as e:
        conn.rollback()
        n = 0
        log(f"  parcels: fetch failed ({e})")
    log(f"  parcels: {n}")

    log("fetching buildings (AV land cover)...")
    L.truncate_raw(cur, "raw.buildings")
    conn.commit()
    try:
        n = L.load_buildings(cur, F.fetch_layer(SOURCES["buildings"]))
        conn.commit()
    except Exception as e:
        conn.rollback()
        n = 0
        log(f"  buildings: fetch failed ({e}); continuing without buildings")
    log(f"  buildings: {n}")


def run_phase_b(conn):
    """
    Phase B — the underuse signal.

    Fetch building footprints ONLY within the envelope of our target zones (not
    all 83,004 canton buildings), plus Geneva's own 'surelevation' layer, then
    compute built-vs-permitted floor area per zone.
    """
    cur = conn.cursor()
    try:
        L.run_sql_file(cur, str(PHASEB_SCHEMA))
        conn.commit()
    except Exception as e:
        conn.rollback()
        log(f"Phase B schema failed ({e})")
        cur.close()
        return

    # Envelope of the zones we actually care about.
    cur.execute("""
        SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e)
        FROM (SELECT ST_Extent(geom) AS e FROM core.zone_opportunity
              WHERE zone_tier IN ('target','secondary')) t
    """)
    row = cur.fetchone()
    if not row or row[0] is None:
        log("Phase B: no target zones yet, skipping")
        cur.close()
        return
    xmin, ymin, xmax, ymax = row
    log(f"Phase B: fetching buildings in envelope "
        f"({int(xmin)},{int(ymin)})-({int(xmax)},{int(ymax)})")

    try:
        cur.execute("TRUNCATE raw.buildings_ge;")
        conn.commit()
        n = L.load_buildings_ge(
            cur, FS.fetch_buildings_in_envelope(xmin, ymin, xmax, ymax))
        conn.commit()
        log(f"  buildings: {n} footprints loaded")
    except Exception as e:
        conn.rollback()
        log(f"  buildings fetch failed ({e})")

    try:
        cur.execute("TRUNCATE raw.surelevation;")
        conn.commit()
        n = L.load_surelevation(cur, FS.fetch_surelevation())
        conn.commit()
        log(f"  surelevation: {n} raisable buildings loaded")
    except Exception as e:
        conn.rollback()
        log(f"  surelevation fetch failed ({e})")

    try:
        L.run_sql_file(cur, str(PHASEB_BUILD))
        conn.commit()
        cur.execute("""SELECT count(*) FROM core.zone_opportunity
                       WHERE zone_tier='target' AND utilisation_pct < 40""")
        leads = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM core.zone_opportunity WHERE n_raisable > 0")
        rais = cur.fetchone()[0]
        log(f"Phase B: {leads} UNDERBUILT target zones (<40% utilised), "
            f"{rais} zones with raisable buildings")
    except Exception as e:
        conn.rollback()
        log(f"Phase B scoring failed ({e})")
    cur.close()


def enrich_and_score(conn):
    cur = conn.cursor()
    # Phase A: zoning-only opportunity ranking. Runs first and independently of
    # parcels, so it produces ranked areas even when parcels are unavailable.
    try:
        L.run_sql_file(cur, str(PHASEA_SCHEMA))
        L.run_sql_file(cur, str(PHASEA_BUILD))
        conn.commit()
        cur.execute("SELECT count(*) FROM core.zone_opportunity WHERE opportunity_score > 0")
        nz = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM core.commune_opportunity")
        nc = cur.fetchone()[0]
        log(f"Phase A: {nz} scored zones across {nc} communes")
    except Exception as e:
        conn.rollback()
        log(f"Phase A ranking failed ({e})")

    # Phase B: underuse signal (buildings vs permitted density).
    if os.getenv("SKIP_PHASE_B", "false").lower() != "true":
        run_phase_b(conn)

    # Parcel-level enrichment/scoring (only meaningful once parcels are loaded).
    try:
        L.run_sql_file(cur, str(ENRICH)); log("enriched")
        L.run_sql_file(cur, str(SCORE));  log("scored")
        changed = L.detect_changes(cur)
        conn.commit()
        log(f"change detection: {changed} parcels changed/new")
    except Exception as e:
        conn.rollback()
        changed = 0
        log(f"parcel scoring skipped ({e})")
    cur.close()
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
