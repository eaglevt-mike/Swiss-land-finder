-- ============================================================================
-- Scoring engine — turn enriched parcels into the three legal Swiss deal
-- signals and a composite opportunity score. Re-runnable after each enrichment.
--
-- The three signals map to the strategies validated as legal in Switzerland
-- (no rezoning required, so no value-gain levy trap):
--   assembly  – adjacent same-use building parcels under (likely) split owners
--   underuse  – building-zone parcel built far below plausible permitted density
--   servicing – building-zone parcel that is essentially unbuilt (raw buildable)
-- ============================================================================

-- Reset signals before recomputing.
UPDATE core.parcel
SET signal_assembly = false,
    signal_underuse = false,
    signal_servicing = false,
    opportunity_score = NULL;

-- Tunable thresholds kept inline for clarity (promote to a config table later).
--   underuse: building zone, developable, coverage below 25%
--   servicing: building zone, developable, essentially unbuilt (coverage < 3%)
--   assembly: parcel touches >=1 other building-zone parcel of the same
--             primary_use; small-to-mid parcels are the interesting ones.

-- 1) Underuse signal
UPDATE core.parcel cp
SET signal_underuse = true
WHERE cp.is_building_zone
  AND cp.developable_m2 > 0
  AND cp.coverage_ratio IS NOT NULL
  AND cp.coverage_ratio < 0.25
  AND COALESCE(cp.oereb_blocking, false) = false;

-- 2) Servicing signal (raw buildable land)
UPDATE core.parcel cp
SET signal_servicing = true
WHERE cp.is_building_zone
  AND cp.developable_m2 > 0
  AND COALESCE(cp.coverage_ratio, 0) < 0.03
  AND COALESCE(cp.oereb_blocking, false) = false;

-- 3) Assembly signal: parcel shares a boundary with another building-zone
--    parcel of the same primary use. ST_Touches on the geometries detects
--    shared edges; DWithin(0.5m) tolerates tiny survey gaps between neighbours.
WITH adjacency AS (
    SELECT DISTINCT a.egrid
    FROM core.parcel a
    JOIN core.parcel b
      ON a.egrid <> b.egrid
     AND a.primary_use = b.primary_use
     AND a.is_building_zone AND b.is_building_zone
     AND ST_DWithin(a.geom, b.geom, 0.5)
     AND NOT ST_Contains(a.geom, b.geom)
)
UPDATE core.parcel cp
SET signal_assembly = true
FROM adjacency adj
WHERE adj.egrid = cp.egrid
  AND cp.developable_m2 > 0
  AND COALESCE(cp.oereb_blocking, false) = false;

-- 4) Composite opportunity score (0-100), transparent and weighted.
--    Components:
--      base buildable size (bigger developable area = more upside), capped
--      + underuse bonus       (headroom to densify)
--      + servicing bonus      (raw uplift potential)
--      + assembly bonus       (aggregation premium)
--      + planning-zone bonus  (plan in flux = optionality)
--      - encumbrance penalty already excluded above (blocked parcels score low)
UPDATE core.parcel cp
SET opportunity_score = LEAST(100, GREATEST(0,
      (LEAST(cp.developable_m2, 5000) / 5000.0) * 30       -- size, up to 30
    + (CASE WHEN cp.signal_underuse  THEN 25 ELSE 0 END)
    + (CASE WHEN cp.signal_servicing THEN 20 ELSE 0 END)
    + (CASE WHEN cp.signal_assembly  THEN 20 ELSE 0 END)
    + (CASE WHEN cp.in_planning_zone THEN 5  ELSE 0 END)
));

-- Blocked / non-build parcels: force a low score so they never surface.
UPDATE core.parcel
SET opportunity_score = 0
WHERE NOT is_building_zone
   OR COALESCE(oereb_blocking, false) = true
   OR COALESCE(developable_m2, 0) = 0;
