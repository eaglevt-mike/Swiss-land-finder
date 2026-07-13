-- ============================================================================
-- Phase B schema — the UNDERUSE signal.
--
-- Adds building footprints and Geneva's own "surélévation" (can-be-raised)
-- layer, then computes, per target zone:
--     built floor area  vs  permitted floor area
-- Underbuilt zones are the real leads.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;

-- Building footprints inside target zones (NOT all 83k in the canton).
CREATE TABLE IF NOT EXISTS raw.buildings_ge (
    objectid        bigint,
    egid            text,
    commune         text,
    destination     text,          -- e.g. "Habitation plusieurs logements"
    nomen_classe    text,          -- e.g. "Habitation"
    niveaux_horsol  integer,       -- ABOVE-GROUND FLOORS — the key field
    hauteur         double precision,
    annee_constr    integer,
    footprint_m2    double precision,
    geom            geometry(MultiPolygon, 2056)
);
CREATE INDEX IF NOT EXISTS ix_bge_geom ON raw.buildings_ge USING gist (geom);

-- Geneva's OWN densification layer: buildings legally raisable (LCI art 23/27).
CREATE TABLE IF NOT EXISTS raw.surelevation (
    objectid    bigint,
    egid        text,
    commune     text,
    destination text,
    remarque    text,
    geom        geometry(MultiPolygon, 2056)
);
CREATE INDEX IF NOT EXISTS ix_surel_geom ON raw.surelevation USING gist (geom);

-- Underuse metrics attached to each zone opportunity.
ALTER TABLE IF EXISTS core.zone_opportunity
    ADD COLUMN IF NOT EXISTS n_buildings          integer DEFAULT 0,
    ADD COLUMN IF NOT EXISTS built_footprint_m2   double precision DEFAULT 0,
    ADD COLUMN IF NOT EXISTS built_floor_area_m2  double precision DEFAULT 0,
    ADD COLUMN IF NOT EXISTS avg_floors           double precision,
    ADD COLUMN IF NOT EXISTS permitted_floor_m2   double precision,
    ADD COLUMN IF NOT EXISTS utilisation_pct      double precision,  -- built / permitted
    ADD COLUMN IF NOT EXISTS coverage_pct         double precision,  -- footprint / area
    ADD COLUMN IF NOT EXISTS n_raisable           integer DEFAULT 0, -- surélévation hits
    ADD COLUMN IF NOT EXISTS underuse_score       double precision;
