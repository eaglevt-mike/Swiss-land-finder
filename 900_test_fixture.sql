-- Synthetic Vaud-like fixture to validate enrichment + scoring logic.
-- Coordinates are in LV95 (2056), around Lausanne (~2538000, 1152000).
-- Parcels are simple squares; zones/buildings overlap them deliberately.

TRUNCATE raw.parcels, raw.zoning, raw.planning_zones, raw.buildings, raw.forest, raw.oereb_restrictions;
TRUNCATE core.parcel CASCADE;

-- Helper: a square polygon of side `s` at origin (x,y), as MultiPolygon.
-- We just inline ST_MakeEnvelope for clarity.

-- P1: residential building zone, 2000 m², barely built (underuse candidate)
INSERT INTO raw.parcels(source_id, egrid, commune_bfs, commune_name, parcel_no, area_m2, geom) VALUES
('P1','CH100000000001',5586,'Lausanne','101', NULL,
 ST_Multi(ST_MakeEnvelope(2538000,1152000,2538044.72,1152044.72,2056)));  -- ~2000 m²

-- P2: residential building zone, adjacent to P1 (assembly pair), also small build
INSERT INTO raw.parcels(source_id, egrid, commune_bfs, commune_name, parcel_no, area_m2, geom) VALUES
('P2','CH100000000002',5586,'Lausanne','102', NULL,
 ST_Multi(ST_MakeEnvelope(2538044.72,1152000,2538089.44,1152044.72,2056)));

-- P3: agricultural (non-build) — should get 0 developable, no signals
INSERT INTO raw.parcels(source_id, egrid, commune_bfs, commune_name, parcel_no, area_m2, geom) VALUES
('P3','CH100000000003',5586,'Lausanne','103', NULL,
 ST_Multi(ST_MakeEnvelope(2538200,1152200,2538244.72,1152244.72,2056)));

-- P4: building zone but heavily built (coverage high) — not underuse
INSERT INTO raw.parcels(source_id, egrid, commune_bfs, commune_name, parcel_no, area_m2, geom) VALUES
('P4','CH100000000004',5586,'Lausanne','104', NULL,
 ST_Multi(ST_MakeEnvelope(2538300,1152000,2538344.72,1152044.72,2056)));

-- P5: zoned residential but in a planning zone (plan under revision) + unbuilt
INSERT INTO raw.parcels(source_id, egrid, commune_bfs, commune_name, parcel_no, area_m2, geom) VALUES
('P5','CH100000000005',5586,'Lausanne','105', NULL,
 ST_Multi(ST_MakeEnvelope(2538400,1152000,2538444.72,1152044.72,2056)));

-- Zoning polygons -----------------------------------------------------------
-- A big residential zone covering P1, P2, P4, P5
INSERT INTO raw.zoning(zone_id, commune_bfs, typ_kt, hauptnutzung, is_building_zone, geom) VALUES
('Z-RES',5586,'Zone village','Wohnzonen', true,
 ST_Multi(ST_MakeEnvelope(2537990,1151990,2538460,1152060,2056)));
-- An agricultural zone covering P3
INSERT INTO raw.zoning(zone_id, commune_bfs, typ_kt, hauptnutzung, is_building_zone, geom) VALUES
('Z-AGR',5586,'Zone agricole','Landwirtschaftszonen', false,
 ST_Multi(ST_MakeEnvelope(2538190,1152190,2538260,1152260,2056)));

-- Planning zone covering only P5
INSERT INTO raw.planning_zones(pz_id, commune_bfs, status, valid_from, geom) VALUES
('PZ-1',5586,'inForce','2025-01-01',
 ST_Multi(ST_MakeEnvelope(2538395,1151995,2538450,1152055,2056)));

-- Buildings ------------------------------------------------------------------
-- Small building on P1 (~200 m² footprint => 10% coverage, underuse)
INSERT INTO raw.buildings(bld_id, footprint_m2, geom) VALUES
('B1', NULL, ST_Multi(ST_MakeEnvelope(2538005,1152005,2538019.14,1152019.14,2056)));
-- Small building on P2 (~200 m²)
INSERT INTO raw.buildings(bld_id, footprint_m2, geom) VALUES
('B2', NULL, ST_Multi(ST_MakeEnvelope(2538050,1152005,2538064.14,1152019.14,2056)));
-- Large building on P4 (~1500 m² => 75% coverage, NOT underuse)
INSERT INTO raw.buildings(bld_id, footprint_m2, geom) VALUES
('B4', NULL, ST_Multi(ST_MakeEnvelope(2538302,1152002,2538340.7,1152040.7,2056)));
-- P5 has no building (0% coverage)

-- OEREB restriction: put a blocking one on P2 to test the encumbrance flag
INSERT INTO raw.oereb_restrictions(egrid, theme_code, theme_text, legal_state) VALUES
('CH100000000002','GroundwaterProtectionZones','Zone de protection des eaux','inForce');
