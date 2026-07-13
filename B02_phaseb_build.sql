-- ============================================================================
-- Phase B build — THE UNDERUSE SIGNAL.
--
-- For each target zone we now compute what a promoter actually wants to know:
--   "How much of what's legally permitted here is already built?"
--
-- built_floor_area  = SUM(building footprint x above-ground floors)   [actual]
-- permitted_floor   = zone developable area x plausible floors        [allowed]
-- utilisation_pct   = built / permitted
--
-- A LOW utilisation on a target (D4A/D4B) zone = a genuine densification lead.
-- We also flag Geneva's own "surélévation" buildings (canton says: can be raised).
-- ============================================================================

-- 1) Buildings per zone: count, footprint, and FLOOR AREA (the real measure).
WITH b AS (
    SELECT zo.zone_uid,
           COUNT(*)                                       AS n_buildings,
           SUM(ST_Area(ST_Intersection(bg.geom, zo.geom))) AS footprint_m2,
           -- floor area = intersected footprint x floors (default 2 if unknown)
           SUM(ST_Area(ST_Intersection(bg.geom, zo.geom))
               * COALESCE(NULLIF(bg.niveaux_horsol, 0), 2))  AS floor_area_m2,
           AVG(NULLIF(bg.niveaux_horsol, 0))               AS avg_floors
    FROM core.zone_opportunity zo
    JOIN raw.buildings_ge bg ON ST_Intersects(bg.geom, zo.geom)
    GROUP BY zo.zone_uid
)
UPDATE core.zone_opportunity zo
SET n_buildings         = COALESCE(b.n_buildings, 0),
    built_footprint_m2  = COALESCE(b.footprint_m2, 0),
    built_floor_area_m2 = COALESCE(b.floor_area_m2, 0),
    avg_floors          = b.avg_floors
FROM b WHERE b.zone_uid = zo.zone_uid;

-- Zones with no buildings at all: explicitly zero (they're EMPTY = prime leads).
UPDATE core.zone_opportunity
SET n_buildings = 0, built_footprint_m2 = 0, built_floor_area_m2 = 0
WHERE n_buildings IS NULL;

-- 2) Surélévation: how many "can be raised" buildings sit in this zone?
UPDATE core.zone_opportunity zo
SET n_raisable = COALESCE((
    SELECT COUNT(*) FROM raw.surelevation s
    WHERE ST_Intersects(s.geom, zo.geom)), 0);

-- 3) Permitted floor area.
--    Geneva's height limit implies a plausible storey count (~3m per storey).
--    We assume a realistic ground coverage of 35% of the zone for an apartment
--    development — conservative, and consistent across zones so the RATIO is
--    comparable even if the absolute is approximate.
UPDATE core.zone_opportunity
SET permitted_floor_m2 = CASE
    WHEN developable_m2 > 0 AND height_limit_m IS NOT NULL
        THEN developable_m2 * 0.35 * GREATEST(FLOOR(height_limit_m / 3.0), 1)
    WHEN developable_m2 > 0
        THEN developable_m2 * 0.35 * 3          -- unknown height: assume 3 floors
    ELSE NULL
END;

-- 4) The ratios.
UPDATE core.zone_opportunity
SET utilisation_pct = CASE
        WHEN COALESCE(permitted_floor_m2, 0) > 0
        THEN LEAST(100.0, 100.0 * built_floor_area_m2 / permitted_floor_m2)
        ELSE NULL END,
    coverage_pct = CASE
        WHEN COALESCE(developable_m2, 0) > 0
        THEN LEAST(100.0, 100.0 * built_footprint_m2 / developable_m2)
        ELSE NULL END;

-- 5) UNDERUSE SCORE (0-100): the lower the utilisation, the bigger the lead.
--    Plus a bonus for Geneva's own "raisable" buildings sitting in the zone.
UPDATE core.zone_opportunity
SET underuse_score = GREATEST(0, LEAST(100,
      -- headroom: 100% utilised => 0 pts; empty => 70 pts
      (CASE WHEN utilisation_pct IS NULL THEN 35            -- unknown: neutral
            ELSE 0.70 * (100.0 - utilisation_pct) END)
      -- canton says buildings here can be raised: strong, official signal
    + (CASE WHEN n_raisable > 0 THEN LEAST(30, n_raisable * 10) ELSE 0 END)
    ));

-- 6) FINAL SCORE — blend the Phase A zone-quality score with Phase B underuse.
--    Phase A said "is this the right KIND of zone?"
--    Phase B says  "is there actually room to build here?"
--    Both matter; an underbuilt villa zone is still a bad lead.
UPDATE core.zone_opportunity
SET opportunity_score = GREATEST(0, LEAST(100,
        0.55 * opportunity_score        -- zone quality (type, height, size)
      + 0.45 * COALESCE(underuse_score, 35)
    ))
WHERE zone_tier IN ('target', 'secondary');

-- Rebuild the reasons to include the underuse story.
UPDATE core.zone_opportunity zo
SET score_reasons = trim(BOTH ', ' FROM concat_ws(', ',
    CASE WHEN zo.zone_tier='target'
         THEN 'TARGET ' || zo.zone_code END,
    CASE WHEN zo.zone_tier='secondary'
         THEN 'buildable ' || zo.zone_code END,
    CASE WHEN zo.zone_tier='avoid'
         THEN 'villa/protected' END,
    CASE WHEN zo.n_buildings = 0 AND zo.developable_m2 > 0
         THEN 'EMPTY — no buildings on site' END,
    CASE WHEN zo.utilisation_pct IS NOT NULL AND zo.n_buildings > 0
         THEN 'only ' || round(zo.utilisation_pct::numeric, 0)
              || '% of permitted density built' END,
    CASE WHEN zo.n_raisable > 0
         THEN zo.n_raisable || ' building(s) the canton says CAN BE RAISED' END,
    CASE WHEN zo.height_limit_m IS NOT NULL
         THEN 'height limit ' || zo.height_limit_m || 'm' END,
    CASE WHEN zo.signal_large_zone THEN 'workable site size' END
));

-- 7) Refresh commune aggregate against the new scores.
TRUNCATE core.commune_opportunity;
INSERT INTO core.commune_opportunity
    (commune_bfs, commune_name, n_building_zones, total_developable_m2,
     n_in_plan_revision, largest_zone_m2, top_zone_score, commune_score)
SELECT
    COALESCE(commune_bfs,-1), MAX(commune_name),
    COUNT(*) FILTER (WHERE zone_tier='target'),
    SUM(developable_m2) FILTER (WHERE zone_tier='target'),
    COALESCE(SUM(n_raisable), 0),
    MAX(developable_m2) FILTER (WHERE zone_tier='target'),
    MAX(opportunity_score),
    LEAST(100,
        0.60 * COALESCE(MAX(opportunity_score) FILTER (WHERE zone_tier='target'),0)
      + 0.25 * LEAST(100, COUNT(*) FILTER (WHERE zone_tier='target') * 8.0)
      + 0.15 * LEAST(100, COALESCE(SUM(developable_m2) FILTER (WHERE zone_tier='target'),0)/3000.0))
FROM core.zone_opportunity
GROUP BY COALESCE(commune_bfs,-1);

UPDATE core.commune_opportunity
SET headline = concat(COALESCE(n_building_zones,0), ' target zones, ',
    round((COALESCE(total_developable_m2,0)/10000.0)::numeric,1), ' ha',
    CASE WHEN COALESCE(n_in_plan_revision,0) > 0
         THEN ', ' || n_in_plan_revision || ' raisable buildings' ELSE '' END);

UPDATE core.commune_opportunity
SET commune_score = 0 WHERE COALESCE(n_building_zones,0) = 0;
