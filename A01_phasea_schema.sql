-- ============================================================================
-- Phase A — zoning-only opportunity ranking.
--
-- Produces ranked development-opportunity signals from the layers that already
-- flow freely (zoning, planning zones, forest) WITHOUT needing parcels or the
-- communal density index. This is a coarse lead-generation filter, not the
-- final underuse product: it ranks AREAS by zoning-derived proxies.
--
-- Two output grains:
--   core.zone_opportunity     — one row per building-zone polygon, scored
--   core.commune_opportunity  — aggregated to commune, for the promoter shortlist
--
-- Honest scope: this does NOT measure built-vs-permitted density (needs footprints
-- + communal IBUS, arriving in Phase B). It ranks where buildable land is
-- concentrated, where plans are in flux, and where zones are large/clean.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------------------
-- Zone-level opportunity: one row per building-zone polygon.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS core.zone_opportunity CASCADE;
CREATE TABLE core.zone_opportunity (
    zone_uid          bigserial PRIMARY KEY,
    zone_id           text,
    commune_bfs       integer,
    zone_type         text,
    primary_use       text,
    geom              geometry(MultiPolygon, 2056),

    area_m2           double precision,          -- zone polygon area
    developable_m2    double precision,          -- area minus forest overlap
    forest_overlap_m2 double precision DEFAULT 0,
    in_planning_zone  boolean DEFAULT false,     -- plan under active revision

    -- zoning-only proxy signals (no parcels, no density index needed)
    signal_large_zone     boolean DEFAULT false, -- big contiguous buildable area
    signal_plan_revision  boolean DEFAULT false, -- plan in flux = optionality
    signal_low_fragment   boolean DEFAULT false, -- clean single polygon (assembly-friendly)

    opportunity_score double precision,
    score_reasons     text,                       -- human-readable "why" for the promoter
    last_updated      timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_zoneopp_geom    ON core.zone_opportunity USING gist (geom);
CREATE INDEX IF NOT EXISTS ix_zoneopp_commune ON core.zone_opportunity (commune_bfs);
CREATE INDEX IF NOT EXISTS ix_zoneopp_score   ON core.zone_opportunity (opportunity_score DESC NULLS LAST);

-- ---------------------------------------------------------------------------
-- Commune-level opportunity: aggregate for the promoter-facing shortlist.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS core.commune_opportunity CASCADE;
CREATE TABLE core.commune_opportunity (
    commune_bfs           integer PRIMARY KEY,
    commune_name          text,
    n_building_zones      integer,
    total_developable_m2  double precision,
    n_in_plan_revision    integer,
    largest_zone_m2       double precision,
    top_zone_score        double precision,
    commune_score         double precision,       -- 0-100 aggregate
    headline              text,                    -- one-line pitch for the promoter
    last_updated          timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_communeopp_score ON core.commune_opportunity (commune_score DESC NULLS LAST);
