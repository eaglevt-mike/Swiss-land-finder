-- ============================================================================
-- Phase A build (v2) — Geneva, tuned for MID-DENSITY promoters (D4A/D4B).
--
-- Why v2: v1 was a size-ranker. It scored every zone 70 and put villa districts
-- (Zone 5 — Cologny 200ha, Collonge-Bellerive 233ha) at the top. Big + clean
-- correlates with GREEN BELT, not development opportunity.
--   * ZONE TYPE is now the dominant signal. Target = D4A/D4B (apartment blocks).
--   * Villa zones (Zone 5) are actively DEMOTED, not rewarded for being big.
--   * Height limit (gabarit, from Geneva's own DESCRIPTION) = density proxy.
--   * Size is a modest bonus with a realistic sweet spot (0.3-3 ha), because a
--     regional promoter wants a workable site, not a 200ha estate district.
-- ============================================================================

TRUNCATE core.zone_opportunity;

INSERT INTO core.zone_opportunity
    (zone_id, commune_bfs, commune_name, zone_type, primary_use, zone_code,
     height_limit_m, density_indice, geom, area_m2)
SELECT
    z.zone_id, z.commune_bfs,
    z.hauptnutzung,        -- GE loader puts COMMUNE here
    z.typ_kt,              -- NOM_ZONE
    z.hauptnutzung,
    z.zone_code, z.height_limit_m, z.density_indice,
    z.geom, ST_Area(z.geom)
FROM raw.zoning z
WHERE z.is_building_zone = true AND z.geom IS NOT NULL;

UPDATE core.zone_opportunity zo
SET forest_overlap_m2 = COALESCE((
    SELECT SUM(ST_Area(ST_Intersection(f.geom, zo.geom)))
    FROM raw.forest f WHERE ST_Intersects(f.geom, zo.geom)), 0);

UPDATE core.zone_opportunity
SET developable_m2 = GREATEST(area_m2 - forest_overlap_m2, 0);

UPDATE core.zone_opportunity zo
SET in_planning_zone = EXISTS (
    SELECT 1 FROM raw.planning_zones pz WHERE ST_Intersects(pz.geom, zo.geom));

-- --- ZONE TIER: the primary signal ------------------------------------------
UPDATE core.zone_opportunity
SET zone_tier = CASE
    WHEN zone_code IN ('D4A','D4B')                THEN 'target'
    WHEN zone_code IN ('D2','D3','DAM','DIA','D5') THEN 'secondary'
    WHEN zone_code IN ('2','3','4A','4B')          THEN 'secondary'
    WHEN zone_code = '5'                           THEN 'avoid'   -- villa zone
    WHEN zone_code = '1'                           THEN 'avoid'   -- historic core
    ELSE 'secondary'
END;

-- --- Signals -----------------------------------------------------------------
UPDATE core.zone_opportunity
SET signal_large_zone = false, signal_plan_revision = false,
    signal_low_fragment = false;

-- Workable site for an apartment-block promoter: ~3,000-30,000 m².
UPDATE core.zone_opportunity
SET signal_large_zone = true
WHERE developable_m2 BETWEEN 3000 AND 30000;

UPDATE core.zone_opportunity
SET signal_plan_revision = true WHERE in_planning_zone = true;

UPDATE core.zone_opportunity
SET signal_low_fragment = true
WHERE ST_NumGeometries(geom) = 1 AND developable_m2 > 1000;

-- --- SCORE -------------------------------------------------------------------
UPDATE core.zone_opportunity zo
SET opportunity_score = GREATEST(0, LEAST(100,
      (CASE zo.zone_tier
          WHEN 'target'    THEN 50
          WHEN 'secondary' THEN 22
          ELSE 0 END)
    + (CASE
          WHEN zo.height_limit_m >= 21   THEN 25
          WHEN zo.height_limit_m >= 15   THEN 20
          WHEN zo.height_limit_m >= 12   THEN 12
          WHEN zo.height_limit_m IS NULL THEN 8
          ELSE 0 END)
    + (CASE WHEN zo.signal_large_zone THEN 15
            WHEN zo.developable_m2 BETWEEN 1000 AND 3000 THEN 7
            ELSE 0 END)
    + (CASE WHEN zo.signal_low_fragment  THEN 5 ELSE 0 END)
    + (CASE WHEN zo.signal_plan_revision THEN 5 ELSE 0 END)
    + (CASE WHEN zo.zone_tier = 'avoid'  THEN -20 ELSE 0 END)
    )),
    score_reasons = trim(BOTH ', ' FROM concat_ws(', ',
        CASE WHEN zo.zone_tier='target'
             THEN 'TARGET: mid-density apartment zone (' || zo.zone_code || ')' END,
        CASE WHEN zo.zone_tier='secondary'
             THEN 'buildable (' || zo.zone_code || ')' END,
        CASE WHEN zo.zone_tier='avoid'
             THEN 'villa/protected — low development potential' END,
        CASE WHEN zo.height_limit_m IS NOT NULL
             THEN 'height limit ' || zo.height_limit_m || 'm' END,
        CASE WHEN zo.signal_large_zone THEN 'workable site size' END,
        CASE WHEN zo.developable_m2 > 100000
             THEN 'very large (masterplan scale)' END,
        CASE WHEN zo.signal_plan_revision THEN 'plan under revision' END));

UPDATE core.zone_opportunity
SET opportunity_score = 0 WHERE COALESCE(developable_m2,0) = 0;

-- --- Commune aggregate: weighted to TARGET zones, not raw hectares -----------
TRUNCATE core.commune_opportunity;

INSERT INTO core.commune_opportunity
    (commune_bfs, commune_name, n_building_zones, total_developable_m2,
     n_in_plan_revision, largest_zone_m2, top_zone_score, commune_score)
SELECT
    COALESCE(commune_bfs,-1),
    MAX(commune_name),
    COUNT(*) FILTER (WHERE zone_tier='target'),
    SUM(developable_m2) FILTER (WHERE zone_tier='target'),
    COUNT(*) FILTER (WHERE in_planning_zone),
    MAX(developable_m2) FILTER (WHERE zone_tier='target'),
    MAX(opportunity_score),
    LEAST(100,
        0.55 * COALESCE(MAX(opportunity_score) FILTER (WHERE zone_tier='target'),0)
      + 0.30 * LEAST(100, COUNT(*) FILTER (WHERE zone_tier='target') * 8.0)
      + 0.15 * LEAST(100, COALESCE(SUM(developable_m2) FILTER (WHERE zone_tier='target'),0)/3000.0))
FROM core.zone_opportunity
GROUP BY COALESCE(commune_bfs,-1);

UPDATE core.commune_opportunity
SET headline = concat(COALESCE(n_building_zones,0),
                      ' target (D4A/D4B) zones, ',
                      round((COALESCE(total_developable_m2,0)/10000.0)::numeric,1), ' ha');

UPDATE core.commune_opportunity
SET commune_score = 0 WHERE COALESCE(n_building_zones,0) = 0;
