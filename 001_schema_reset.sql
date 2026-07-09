-- ============================================================================
-- Swiss Land Deal-Sourcing Platform — PostGIS schema (Vaud pilot)
-- Run order: this file first, then load data via the pipeline, then 002_enrich.sql
--
-- Design notes
--   * All geometry is stored in EPSG:2056 (CH1903+ / LV95) — the official Swiss
--     national grid. Every Swiss source publishes in this CRS, so we never
--     reproject on ingest; we reproject to 4326 only at the API/map edge.
--   * Three schemas keep concerns separate:
--       raw   – landing zone, one table per source, overwritten each run
--       core  – the canonical parcel table (the asset we own)
--       audit – snapshots + change log for the change-detection layer
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- for gen_random_uuid()

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS audit;

-- ----------------------------------------------------------------------------
-- RAW LANDING TABLES (truncated + reloaded on every ingest run)
-- ----------------------------------------------------------------------------

-- Cadastral parcels (official survey / amtliche Vermessung, via geodienste.ch
-- or the cantonal viageo.ch export). One row per legal parcel.
DROP TABLE IF EXISTS raw.parcels CASCADE;
CREATE TABLE raw.parcels (
    source_id     text NOT NULL,          -- EGRIS-EGRID where available
    egrid         text,                    -- federal parcel identifier (CH...)
    commune_bfs   integer,                 -- BFS commune number
    commune_name  text,
    parcel_no     text,                    -- local parcel number
    area_m2       double precision,        -- official recorded area
    geom          geometry(MultiPolygon, 2056) NOT NULL
);

-- Zoning / land-use plan (MGDM Nutzungsplanung 73.1 v1.2, harmonized).
-- The "typ_kt" / primary-use code is what tells us build vs non-build zone.
DROP TABLE IF EXISTS raw.zoning CASCADE;
CREATE TABLE raw.zoning (
    zone_id       text,
    commune_bfs   integer,
    typ_kt        text,                    -- cantonal zone type label
    hauptnutzung  text,                    -- one of 9 harmonized primary uses
    is_building_zone boolean,              -- derived on load (see fetcher)
    geom          geometry(MultiPolygon, 2056) NOT NULL
);

-- Planning zones (MGDM Planungszonen 76.1) — areas flagged for zoning revision
-- under Art. 27 RPG. A live signal that a plan is in flux = future opportunity.
DROP TABLE IF EXISTS raw.planning_zones CASCADE;
CREATE TABLE raw.planning_zones (
    pz_id         text,
    commune_bfs   integer,
    status        text,
    valid_from    date,
    geom          geometry(MultiPolygon, 2056) NOT NULL
);

-- Building footprints (from swissTLM3D / cantonal buildings layer). Used to
-- estimate how much of a parcel's permitted density is actually built.
DROP TABLE IF EXISTS raw.buildings CASCADE;
CREATE TABLE raw.buildings (
    bld_id        text,
    footprint_m2  double precision,
    geom          geometry(MultiPolygon, 2056) NOT NULL
);

-- Forest boundaries (npl_waldgrenzen 157.1) — a hard non-build constraint that
-- clips developable area even inside a building zone.
DROP TABLE IF EXISTS raw.forest CASCADE;
CREATE TABLE raw.forest (
    fg_id         text,
    geom          geometry(MultiPolygon, 2056) NOT NULL
);

-- Public-law restrictions (ÖREB / PLR cadastre) attached to a parcel. Stored
-- as one row per (parcel, restriction theme). Populated per-parcel from the
-- OEREB web service for shortlisted candidates (it is not a bulk layer).
DROP TABLE IF EXISTS raw.oereb_restrictions CASCADE;
CREATE TABLE raw.oereb_restrictions (
    egrid         text,
    theme_code    text,                    -- e.g. LandUsePlans, ForestPerimeters
    theme_text    text,
    sub_theme     text,
    legal_state   text,
    fetched_at    timestamptz DEFAULT now()
);

-- Spatial indexes on raw geometry (created after load in the pipeline, but
-- declared here so the schema is self-documenting).
CREATE INDEX IF NOT EXISTS ix_raw_parcels_geom  ON raw.parcels  USING gist (geom);
CREATE INDEX IF NOT EXISTS ix_raw_zoning_geom   ON raw.zoning   USING gist (geom);
CREATE INDEX IF NOT EXISTS ix_raw_pz_geom       ON raw.planning_zones USING gist (geom);
CREATE INDEX IF NOT EXISTS ix_raw_bld_geom      ON raw.buildings USING gist (geom);
CREATE INDEX IF NOT EXISTS ix_raw_forest_geom   ON raw.forest   USING gist (geom);

-- ----------------------------------------------------------------------------
-- CORE CANONICAL PARCEL TABLE (the enriched asset)
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS core.parcel CASCADE;
CREATE TABLE core.parcel (
    parcel_uid        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    egrid             text UNIQUE,
    source_id         text NOT NULL,
    commune_bfs       integer,
    commune_name      text,
    parcel_no         text,

    -- geometry + measures
    geom              geometry(MultiPolygon, 2056) NOT NULL,
    area_m2           double precision,          -- computed from geom, authoritative

    -- zoning enrichment (dominant zone by area overlap)
    zone_type         text,
    primary_use       text,
    is_building_zone  boolean,
    in_planning_zone  boolean DEFAULT false,     -- plan under revision (opportunity/risk)

    -- constraint enrichment
    forest_overlap_m2 double precision DEFAULT 0,
    developable_m2    double precision,          -- area minus hard constraints
    oereb_checked     boolean DEFAULT false,
    oereb_blocking    boolean,                   -- true if a restriction kills development

    -- build-state enrichment
    building_footprint_m2 double precision DEFAULT 0,
    coverage_ratio        double precision,      -- footprint / area

    -- scoring outputs (written by the scoring engine in a later stage)
    signal_assembly   boolean DEFAULT false,
    signal_underuse   boolean DEFAULT false,
    signal_servicing  boolean DEFAULT false,
    opportunity_score double precision,

    first_seen        timestamptz DEFAULT now(),
    last_updated      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_core_parcel_geom    ON core.parcel USING gist (geom);
CREATE INDEX IF NOT EXISTS ix_core_parcel_commune ON core.parcel (commune_bfs);
CREATE INDEX IF NOT EXISTS ix_core_parcel_score   ON core.parcel (opportunity_score DESC NULLS LAST);

-- ----------------------------------------------------------------------------
-- AUDIT / CHANGE DETECTION
-- ----------------------------------------------------------------------------
-- A lightweight hash snapshot per parcel per run lets us diff runs cheaply:
-- if the content hash changes, something material (zone, area, restriction)
-- moved, and the parcel is re-scored and can trigger an alert.
DROP TABLE IF EXISTS audit.parcel_snapshot CASCADE;
CREATE TABLE audit.parcel_snapshot (
    egrid        text,
    run_ts       timestamptz DEFAULT now(),
    content_hash text,
    PRIMARY KEY (egrid, run_ts)
);

DROP TABLE IF EXISTS audit.change_log CASCADE;
CREATE TABLE audit.change_log (
    id           bigserial PRIMARY KEY,
    egrid        text,
    change_type  text,          -- new | zone_changed | restriction_changed | scored_up
    detail       jsonb,
    detected_at  timestamptz DEFAULT now()
);
