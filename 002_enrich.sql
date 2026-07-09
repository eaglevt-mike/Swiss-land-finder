-- ============================================================================
-- Enrichment — build core.parcel from the raw layers via spatial joins.
-- Idempotent: safe to re-run after every ingest. Uses UPSERT on egrid so a
-- parcel keeps its stable parcel_uid and first_seen across runs.
--
-- Order of operations matters: geometry/area first, then zoning, then
-- constraints, then build-state. Each block is independent and re-runnable.
-- ============================================================================

-- 0) Base upsert: bring parcels in with authoritative computed area.
INSERT INTO core.parcel (egrid, source_id, commune_bfs, commune_name, parcel_no, geom, area_m2)
SELECT
    p.egrid,
    p.source_id,
    p.commune_bfs,
    p.commune_name,
    p.parcel_no,
    p.geom,
    ST_Area(p.geom)                       -- LV95 is metric; ST_Area = m² directly
FROM raw.parcels p
ON CONFLICT (egrid) DO UPDATE SET
    geom         = EXCLUDED.geom,
    area_m2      = EXCLUDED.area_m2,
    commune_bfs  = EXCLUDED.commune_bfs,
    commune_name = EXCLUDED.commune_name,
    parcel_no    = EXCLUDED.parcel_no,
    last_updated = now();

-- 1) Dominant zone per parcel: the zone polygon covering the largest share of
--    the parcel wins. We precompute the winning zone per parcel in a CTE keyed
--    by egrid, then join it back — this is the UPDATE-safe form of the
--    "argmax overlap" pattern (a LATERAL cannot see the UPDATE target table).
WITH dominant AS (
    SELECT DISTINCT ON (cp.egrid)
           cp.egrid,
           zn.typ_kt,
           zn.hauptnutzung,
           zn.is_building_zone
    FROM core.parcel cp
    JOIN raw.zoning zn ON ST_Intersects(zn.geom, cp.geom)
    ORDER BY cp.egrid,
             ST_Area(ST_Intersection(zn.geom, cp.geom)) DESC
)
UPDATE core.parcel cp
SET zone_type        = d.typ_kt,
    primary_use      = d.hauptnutzung,
    is_building_zone = d.is_building_zone
FROM dominant d
WHERE d.egrid = cp.egrid;

-- 2) Planning-zone flag: parcel intersects an area under active plan revision.
UPDATE core.parcel cp
SET in_planning_zone = EXISTS (
    SELECT 1 FROM raw.planning_zones pz
    WHERE ST_Intersects(pz.geom, cp.geom)
);

-- 3) Forest overlap: subtract forest area (hard non-build constraint).
UPDATE core.parcel cp
SET forest_overlap_m2 = COALESCE((
    SELECT SUM(ST_Area(ST_Intersection(f.geom, cp.geom)))
    FROM raw.forest f
    WHERE ST_Intersects(f.geom, cp.geom)
), 0);

-- 4) Developable area = parcel area inside a building zone, minus forest.
--    Non-building-zone parcels get 0 developable (no legal build without rezoning).
UPDATE core.parcel cp
SET developable_m2 = CASE
    WHEN cp.is_building_zone THEN GREATEST(cp.area_m2 - cp.forest_overlap_m2, 0)
    ELSE 0
END;

-- 5) Building footprint on parcel + coverage ratio (built vs parcel area).
UPDATE core.parcel cp
SET building_footprint_m2 = COALESCE((
    SELECT SUM(ST_Area(ST_Intersection(b.geom, cp.geom)))
    FROM raw.buildings b
    WHERE ST_Intersects(b.geom, cp.geom)
), 0);

UPDATE core.parcel cp
SET coverage_ratio = CASE
    WHEN cp.area_m2 > 0 THEN cp.building_footprint_m2 / cp.area_m2
    ELSE NULL
END;

-- 6) OEREB blocking flag (only for parcels we've checked; see oereb fetcher).
--    Any restriction in a blocking theme set marks the parcel as encumbered.
UPDATE core.parcel cp
SET oereb_checked = true,
    oereb_blocking = EXISTS (
        SELECT 1 FROM raw.oereb_restrictions r
        WHERE r.egrid = cp.egrid
          AND r.theme_code IN (
              'ForestPerimeters',
              'NoiseSensitivityLevels',
              'ContaminatedSites',
              'GroundwaterProtectionZones'
          )
          AND r.legal_state = 'inForce'
    )
WHERE cp.egrid IN (SELECT DISTINCT egrid FROM raw.oereb_restrictions);
