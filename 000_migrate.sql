-- ============================================================================
-- 000_migrate.sql — additive, idempotent column migrations.
--
-- Runs on every pipeline start (before the schema file). ADD COLUMN IF NOT
-- EXISTS is safe to re-run and never destroys data, so this upgrades an
-- existing database in place without needing a manual reset.
-- ============================================================================

-- Geneva zoning signals (added when we pivoted from geodienste to SITG).
ALTER TABLE IF EXISTS raw.zoning
    ADD COLUMN IF NOT EXISTS zone_code      text,
    ADD COLUMN IF NOT EXISTS height_limit_m integer,
    ADD COLUMN IF NOT EXISTS density_indice text;

-- Phase A zone-opportunity: tier + density signals.
ALTER TABLE IF EXISTS core.zone_opportunity
    ADD COLUMN IF NOT EXISTS commune_name   text,
    ADD COLUMN IF NOT EXISTS zone_code      text,
    ADD COLUMN IF NOT EXISTS height_limit_m integer,
    ADD COLUMN IF NOT EXISTS density_indice text,
    ADD COLUMN IF NOT EXISTS zone_tier      text;

-- Commune-opportunity: name column.
ALTER TABLE IF EXISTS core.commune_opportunity
    ADD COLUMN IF NOT EXISTS commune_name   text;
