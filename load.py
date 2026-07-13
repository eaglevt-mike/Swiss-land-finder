"""
Loader: write fetched GeoJSON features into the raw.* staging tables, then run
enrichment, scoring, and change-detection.

Geometry handling: features arrive as GeoJSON in LV95 (2056). We hand the raw
GeoJSON geometry to PostGIS via ST_GeomFromGeoJSON and force MultiPolygon +
SRID 2056 in SQL, so we never depend on client-side geometry libraries being
perfectly configured.
"""
from __future__ import annotations
import json
from typing import Iterable
import psycopg2
from psycopg2.extras import execute_batch

from config import (DATABASE_URL, BUILDING_ZONE_TOKENS, NON_BUILDING_TOKENS,
                    BUILDING_ZONE_CODES, GENEVA_BUILD_PREFIXES,
                    GENEVA_NONBUILD_TOKENS, CANTON as TARGET_CANTON)


def connect():
    return psycopg2.connect(DATABASE_URL)


def _geom_sql(placeholder: str = "%s") -> str:
    """Coerce a GeoJSON geometry into a valid MultiPolygon in SRID 2056,
    regardless of whether the server returned LV95 metres or WGS84 degrees.

    LV95 easting is a large metric value (~2.5 million); WGS84 longitude for
    Switzerland is ~6-10. We detect which one we got and transform if needed, so
    every downstream area/intersection stays correct even if geodienste ignores
    the `crs=2056` request parameter.

    The input placeholder is referenced once and expanded in SQL via a LATERAL-
    style scalar subselect, so each row still binds the GeoJSON exactly once.
    """
    return (
        "ST_Multi(ST_CollectionExtract(ST_MakeValid("
        "(SELECT CASE WHEN abs(ST_X(ST_PointOnSurface(g.geom))) <= 200 "
        "THEN ST_Transform(ST_SetSRID(g.geom,4326),2056) "
        "ELSE ST_SetSRID(g.geom,2056) END "
        f"FROM (SELECT ST_GeomFromGeoJSON({placeholder}) AS geom) g)"
        "),3))"
    )


def truncate_raw(cur, table: str):
    cur.execute(f"TRUNCATE {table};")


def load_zoning(cur, features: Iterable[dict]):
    """Load zoning polygons using the real affectation_primaire fields.

    Confirmed live fields: affectation_principale_code (federal build-zone code),
    affectation_principale_designation, canton, type_canton_designation, t_id.
    The canton box catches several cantons, so we keep only TARGET_CANTON.
    """
    sql = f"""
        INSERT INTO raw.zoning(zone_id, commune_bfs, typ_kt, hauptnutzung,
                               is_building_zone, geom)
        VALUES (%s,%s,%s,%s,%s,{_geom_sql()})
    """
    rows = []
    skipped = 0
    for f in features:
        p = f.get("properties", {})
        if TARGET_CANTON and str(p.get("canton", "")).upper() != TARGET_CANTON:
            skipped += 1
            continue
        code = p.get("affectation_principale_code")
        zone_label = (p.get("type_canton_designation")
                      or p.get("affectation_principale_designation") or "")
        rows.append((
            str(f.get("id") or p.get("t_id") or ""),
            None,   # no direct BFS in zoning; resolved via spatial join later
            zone_label,                                   # typ_kt: readable zone type
            p.get("affectation_principale_designation"),  # hauptnutzung: primary use
            _is_building_zone_code(code),
            json.dumps(f.get("geometry")),
        ))
    execute_batch(cur, sql, rows, page_size=500)
    if skipped:
        print(f"    (zoning: kept {len(rows)}, skipped {skipped} outside {TARGET_CANTON})",
              flush=True)
    return len(rows)


def _is_building_zone_code(code) -> bool:
    """Federal affectation-code build-zone test (geodienste path)."""
    try:
        return int(code) in BUILDING_ZONE_CODES
    except (TypeError, ValueError):
        return False


def load_zoning_sitg(cur, features: Iterable[dict]):
    """
    Load Geneva zoning from SITG. Unlike geodienste, this gives us commune names,
    BFS numbers, and a density INDICE — everything Phase A wants.

    Real fields (confirmed live): COMMUNE, NO_COMM_FEDERAL, ZONE, NOM_ZONE,
    DESCRIPTION, INDICE, RESTRICTION.
    """
    sql = f"""
        INSERT INTO raw.zoning(zone_id, commune_bfs, typ_kt, hauptnutzung,
                               is_building_zone, geom)
        VALUES (%s,%s,%s,%s,%s,{_geom_sql()})
    """
    rows = []
    for f in features:
        p = f.get("properties", {})
        zone_code = p.get("ZONE") or ""
        zone_name = p.get("NOM_ZONE") or ""
        rows.append((
            str(p.get("OBJECTID") or f.get("id") or ""),
            _to_int(p.get("NO_COMM_FEDERAL")),       # real BFS number!
            zone_name,                                # readable zone type
            p.get("COMMUNE"),                         # commune name
            _is_building_zone_ge(zone_code, zone_name),
            json.dumps(f.get("geometry")),
        ))
    execute_batch(cur, sql, rows, page_size=500)
    return len(rows)


def _is_building_zone_ge(code, name) -> bool:
    """
    Geneva building-zone test. Geneva uses its own zone system (not the federal
    codes). Buildable = ordinary zones 1-5 and all development ('D...') zones,
    which permit housing/activity construction. Excluded: agricultural, forest,
    protected, water, rail — identified by name tokens.
    """
    nm = str(name or "").lower()
    if any(tok in nm for tok in GENEVA_NONBUILD_TOKENS):
        return False
    c = str(code or "").upper().strip()
    if not c:
        return False
    # Development zones (D3, D4A, DAM, DIA...) and ordinary zones (1..5)
    return c.startswith(GENEVA_BUILD_PREFIXES)


def _is_building_zone(use) -> bool:
    """Language-agnostic build-zone test by substring match, with a non-build
    safety net so 'zone agricole' never slips through on a stray token."""
    if not use:
        return False
    s = str(use).lower()
    if any(tok in s for tok in NON_BUILDING_TOKENS):
        return False
    return any(tok in s for tok in BUILDING_ZONE_TOKENS)


def load_parcels(cur, features: Iterable[dict]):
    """
    Load cadastral parcels (AV 'Liegenschaften' / RESF collection).

    The AV display layer doesn't always expose EGRID directly, so we derive a
    stable source_id from the feature id and use the parcel number where present.
    A synthetic egrid is built from the feature id so downstream joins and the
    change-detection snapshot have a stable key. Field names vary by canton, so
    every lookup is defensive.
    """
    sql = f"""
        INSERT INTO raw.parcels(source_id, egrid, commune_bfs, commune_name,
                                parcel_no, area_m2, geom)
        VALUES (%s,%s,%s,%s,%s,%s,{_geom_sql()})
    """
    rows = []
    skipped = 0
    for f in features:
        p = f.get("properties", {})
        # Real AV RESF field names (confirmed from live data):
        # BFSNr, EGRIS_EGRID, Flaeche, Kanton, Nummer, NBIdent, Vollstaendigkeit
        # The canton bbox also catches Geneva/Fribourg parcels, so keep only VD.
        if TARGET_CANTON and str(p.get("Kanton", "")).upper() != TARGET_CANTON:
            skipped += 1
            continue
        fid = str(f.get("id") or p.get("NBIdent") or "")
        egrid = p.get("EGRIS_EGRID") or (f"SYN-{fid}" if fid else None)
        parcel_no = p.get("Nummer")
        bfs = _to_int(p.get("BFSNr"))
        area = p.get("Flaeche")
        rows.append((
            fid,
            egrid,
            bfs,
            p.get("Kanton"),          # store canton code in commune_name slot for now
            str(parcel_no) if parcel_no is not None else None,
            _to_float(area),          # official recorded area; geom area computed in enrich
            json.dumps(f.get("geometry")),
        ))
    execute_batch(cur, sql, rows, page_size=500)
    if skipped:
        print(f"    (parcels: kept {len(rows)}, skipped {skipped} outside {TARGET_CANTON})",
              flush=True)
    return len(rows)


def load_buildings(cur, features: Iterable[dict]):
    """
    Load building footprints from the AV land-cover layer (LCSF).

    LCSF contains ALL land-cover polygons (buildings, roads, water, vineyards...),
    so we keep only the building class. The class is in an 'art'/'genauigkeit'/
    'objektart' field; building values look like 'Gebaeude'/'batiment'/'edificio'.
    Filtering here keeps the coverage-ratio calculation meaningful and cuts load
    volume dramatically.
    """
    sql = f"""
        INSERT INTO raw.buildings(bld_id, footprint_m2, geom)
        VALUES (%s,%s,{_geom_sql()})
    """
    building_tokens = ("gebaeude", "gebäude", "batiment", "bâtiment",
                       "edificio", "building")
    rows = []
    kept = 0
    for f in features:
        p = f.get("properties", {})
        art = str(p.get("art") or p.get("objektart") or p.get("genre")
                  or p.get("type") or "").lower()
        if not any(tok in art for tok in building_tokens):
            continue
        kept += 1
        rows.append((
            str(f.get("id") or ""),
            None,  # footprint computed from geom in enrichment
            json.dumps(f.get("geometry")),
        ))
    execute_batch(cur, sql, rows, page_size=500)
    return kept


def load_generic(cur, table: str, features: Iterable[dict], columns: list[str]):
    """
    Generic loader for layers where we only keep an id + geometry
    (planning_zones, forest, buildings). `columns` excludes geom.
    """
    col_sql = ", ".join(columns + ["geom"])
    ph = ", ".join(["%s"] * len(columns) + [_geom_sql()])
    sql = f"INSERT INTO {table}({col_sql}) VALUES ({ph})"
    rows = []
    for f in features:
        p = f.get("properties", {})
        vals = [str(f.get("id") or "")]
        # remaining columns pulled from properties by name if present
        for c in columns[1:]:
            vals.append(p.get(c))
        vals.append(json.dumps(f.get("geometry")))
        rows.append(tuple(vals))
    execute_batch(cur, sql, rows, page_size=500)
    return len(rows)


def load_oereb(cur, restriction_rows: Iterable[dict]):
    sql = """
        INSERT INTO raw.oereb_restrictions
            (egrid, theme_code, theme_text, sub_theme, legal_state)
        VALUES (%(egrid)s,%(theme_code)s,%(theme_text)s,%(sub_theme)s,%(legal_state)s)
    """
    rows = list(restriction_rows)
    execute_batch(cur, sql, rows, page_size=200)
    return len(rows)


def run_sql_file(cur, path: str):
    with open(path, "r", encoding="utf-8") as fh:
        cur.execute(fh.read())


def detect_changes(cur):
    """
    Snapshot each parcel's material content as a hash, diff against the previous
    run, and append change_log rows. New parcels and score jumps become alerts.
    """
    cur.execute("""
        WITH cur AS (
            SELECT egrid,
                   md5(coalesce(zone_type,'') || '|' ||
                       coalesce(is_building_zone::text,'') || '|' ||
                       coalesce(round(area_m2)::text,'') || '|' ||
                       coalesce(oereb_blocking::text,'') || '|' ||
                       coalesce(round(opportunity_score)::text,'')) AS h
            FROM core.parcel
        ),
        prev AS (
            SELECT DISTINCT ON (egrid) egrid, content_hash
            FROM audit.parcel_snapshot
            ORDER BY egrid, run_ts DESC
        )
        INSERT INTO audit.change_log(egrid, change_type, detail)
        SELECT c.egrid,
               CASE WHEN p.egrid IS NULL THEN 'new' ELSE 'changed' END,
               jsonb_build_object('new_hash', c.h, 'old_hash', p.content_hash)
        FROM cur c
        LEFT JOIN prev p ON p.egrid = c.egrid
        WHERE p.content_hash IS DISTINCT FROM c.h;
    """)
    changed = cur.rowcount
    # write the new snapshot
    cur.execute("""
        INSERT INTO audit.parcel_snapshot(egrid, content_hash)
        SELECT egrid,
               md5(coalesce(zone_type,'') || '|' ||
                   coalesce(is_building_zone::text,'') || '|' ||
                   coalesce(round(area_m2)::text,'') || '|' ||
                   coalesce(oereb_blocking::text,'') || '|' ||
                   coalesce(round(opportunity_score)::text,''))
        FROM core.parcel;
    """)
    return changed


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
