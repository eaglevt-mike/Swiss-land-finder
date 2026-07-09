-- ============================================================================
-- Phase A build — populate & score zone_opportunity from raw layers, then
-- aggregate to commune_opportunity. Needs only raw.zoning, raw.planning_zones,
-- raw.forest. Idempotent; safe to re-run after each ingest.
-- ============================================================================

TRUNCATE core.zone_opportunity;

-- 1) Load building-zone polygons (only building zones are opportunities).
INSERT INTO core.zone_opportunity
    (zone_id, commune_bfs, zone_type, primary_use, geom, area_m2)
SELECT
    z.zone_id,
    z.commune_bfs,
    z.typ_kt,
    z.hauptnutzung,
    z.geom,
    ST_Area(z.geom)                    -- LV95 metric; m² directly
FROM raw.zoning z
WHERE z.is_building_zone = true
  AND z.geom IS NOT NULL;

-- 2) Forest overlap (hard non-build constraint clips developable area).
UPDATE core.zone_opportunity zo
SET forest_overlap_m2 = COALESCE((
    SELECT SUM(ST_Area(ST_Intersection(f.geom, zo.geom)))
    FROM raw.forest f
    WHERE ST_Intersects(f.geom, zo.geom)
), 0);

UPDATE core.zone_opportunity
SET developable_m2 = GREATEST(area_m2 - forest_overlap_m2, 0);

-- 3) Planning-zone flag: zone intersects an area under active plan revision.
UPDATE core.zone_opportunity zo
SET in_planning_zone = EXISTS (
    SELECT 1 FROM raw.planning_zones pz
    WHERE ST_Intersects(pz.geom, zo.geom)
);

-- ---------------------------------------------------------------------------
-- 4) Proxy signals — computable from zoning alone.
-- ---------------------------------------------------------------------------
-- Reset
UPDATE core.zone_opportunity
SET signal_large_zone = false,
    signal_plan_revision = false,
    signal_low_fragment = false;

-- Large contiguous buildable area: top quartile by developable area, and at
-- least 2000 m² (a plot a promoter can actually do something with).
WITH q AS (
    SELECT percentile_cont(0.75) WITHIN GROUP (ORDER BY developable_m2) AS p75
    FROM core.zone_opportunity WHERE developable_m2 > 0
)
UPDATE core.zone_opportunity zo
SET signal_large_zone = true
FROM q
WHERE zo.developable_m2 >= GREATEST(q.p75, 2000);

-- Plan in revision.
UPDATE core.zone_opportunity
SET signal_plan_revision = true
WHERE in_planning_zone = true;

-- Low fragmentation: the polygon is a single ring (not multi-part), which tends
-- to mean a clean, coherent site rather than scattered slivers.
UPDATE core.zone_opportunity
SET signal_low_fragment = true
WHERE ST_NumGeometries(geom) = 1
  AND developable_m2 > 1000;

-- ---------------------------------------------------------------------------
-- 5) Score (0-100) + human-readable reasons.
-- ---------------------------------------------------------------------------
UPDATE core.zone_opportunity zo
SET opportunity_score = LEAST(100, GREATEST(0,
        (LEAST(zo.developable_m2, 20000) / 20000.0) * 45     -- size, up to 45
      + (CASE WHEN zo.signal_plan_revision THEN 30 ELSE 0 END)
      + (CASE WHEN zo.signal_large_zone    THEN 15 ELSE 0 END)
      + (CASE WHEN zo.signal_low_fragment  THEN 10 ELSE 0 END)
    )),
    score_reasons = trim(BOTH ', ' FROM concat_ws(', ',
        CASE WHEN zo.developable_m2 >= 10000 THEN 'large developable area' END,
        CASE WHEN zo.signal_plan_revision THEN 'plan under revision (optionality)' END,
        CASE WHEN zo.signal_large_zone AND zo.developable_m2 < 10000 THEN 'above-median parcel size' END,
        CASE WHEN zo.signal_low_fragment THEN 'clean single-polygon site' END,
        CASE WHEN zo.forest_overlap_m2 > 0 THEN 'partially forest-constrained' END
    ));

-- Zero out anything with no developable land.
UPDATE core.zone_opportunity
SET opportunity_score = 0
WHERE COALESCE(developable_m2, 0) = 0;

-- ---------------------------------------------------------------------------
-- 6) Aggregate to commune for the promoter shortlist.
-- ---------------------------------------------------------------------------
TRUNCATE core.commune_opportunity;

INSERT INTO core.commune_opportunity
    (commune_bfs, n_building_zones, total_developable_m2, n_in_plan_revision,
     largest_zone_m2, top_zone_score, commune_score)
SELECT
    commune_bfs,
    COUNT(*),
    SUM(developable_m2),
    COUNT(*) FILTER (WHERE in_planning_zone),
    MAX(developable_m2),
    MAX(opportunity_score),
    -- commune score blends: best single zone, breadth of opportunity, and
    -- plan-revision activity. Normalised to 0-100.
    LEAST(100,
        0.5 * MAX(opportunity_score)
      + 0.3 * LEAST(100, SUM(developable_m2) / 5000.0)      -- breadth
      + 0.2 * LEAST(100, COUNT(*) FILTER (WHERE in_planning_zone) * 20.0)
    )
FROM core.zone_opportunity
WHERE commune_bfs IS NOT NULL
GROUP BY commune_bfs;

-- Headline pitch per commune.
UPDATE core.commune_opportunity
SET headline = concat(
    n_building_zones, ' building zones, ',
    round((total_developable_m2/10000.0)::numeric, 1), ' ha developable',
    CASE WHEN n_in_plan_revision > 0
         THEN concat(', ', n_in_plan_revision, ' under plan revision')
         ELSE '' END
);
