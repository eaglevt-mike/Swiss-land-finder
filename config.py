"""
Central configuration for the Swiss land pipeline (Vaud pilot).

Every endpoint below is a real, free, official Swiss geodata service. The
harmonized layers are served via geodienste.ch (OGC API Features / WFS); the
OEREB per-parcel service is federal. Swap CANTON to scale to another canton
once the Vaud pipeline is validated end-to-end.
"""
import os

# --- Target canton -----------------------------------------------------------
CANTON = "VD"                     # Vaud. Zoning updated monthly on geodienste.ch.
SRID = 2056                       # CH1903+ / LV95 — the CRS all sources publish in.

# Optional: restrict the first run to a few communes around Lausanne so you can
# validate the whole pipeline on a small tile before pulling the full canton.
# BFS numbers: Lausanne 5586, Prilly 5589, Renens 5591, Pully 5590, Écublens 5584.
PILOT_COMMUNES = [5586, 5589, 5591, 5590, 5584]
PILOT_ONLY = os.getenv("PILOT_ONLY", "true").lower() == "true"

# --- geodienste.ch OGC API Features base -------------------------------------
# Pattern confirmed from the services catalogue:
#   https://geodienste.ch/db/<model>_<ver>/<lang>/ogcapi
# Collections are filtered per canton via the STAC/feature "canton" attribute.
GEODIENSTE = "https://geodienste.ch/db"

# =============================================================================
# PRIMARY SOURCE: Geneva SITG (ArcGIS REST, returns GeoJSON).
#
# Why Geneva and not Vaud: geodienste cannot deliver VD zoning (proven — it
# pages nationally in canton order and never reaches VD; every server-side
# filter is ignored). Vaud's own geoservice is WMS-only (images, not features),
# and the ASIT-VD WFS does not resolve. Geneva's SITG, by contrast, serves the
# ENTIRE canton's zoning as 2,494 GeoJSON features in one query, with commune
# names, BFS numbers, zone types and a density INDICE.
# =============================================================================
SITG_ZONING = ("https://vector.sitg.ge.ch/arcgis/rest/services/"
               "SIT_ZONE_AMENAG/MapServer/0/query")
SITG_PAGE = 1000          # server maxRecordCount is 4000; stay well under
GENEVA_CANTON = "GE"

# Geneva zone codes (field ZONE / NOM_ZONE). Geneva's system is its own, not the
# federal code list. Building zones = ordinary + development zones where housing
# or activity construction is permitted. Confirmed from live data.
#   D3, D4A, D4AP, D4B, D4BP  development residential zones (buildable)
#   DAM  development mixed-activity      DIA  development industrial/artisanal
# Non-build examples: agricultural, forest, protected (appear as other codes).
GENEVA_BUILD_PREFIXES = ("D", "1", "2", "3", "4", "5")   # dev + ordinary zones
GENEVA_NONBUILD_TOKENS = ("agricole", "bois", "forêt", "foret", "protégée",
                          "protegee", "verdure", "ferroviaire", "eaux")
SOURCES = {
    "zoning": {
        "ogcapi": f"{GEODIENSTE}/npl_nutzungsplanung_v1_2_0/fra/ogcapi",
        "collection": "affectation_primaire",   # primary land-use zones
        "raw_table": "raw.zoning",
    },
    "planning_zones": {
        "ogcapi": f"{GEODIENSTE}/planungszonen_v1_1_0/fra/ogcapi",
        "collection": "zones_reservees",        # Art. 27 RPG reserved zones
        "raw_table": "raw.planning_zones",
    },
    "forest": {
        "ogcapi": f"{GEODIENSTE}/npl_waldgrenzen_v1_2_0/fra/ogcapi",
        "collection": "limites_de_la_foret_statiques",  # static forest limits
        "collection_keywords": ["foret", "wald", "limite"],  # fallback match
        "raw_table": "raw.forest",
    },
    # Official cadastral survey (amtliche Vermessung). NOTE: AV data on
    # geodienste is access-controlled per canton — Vaud may require an
    # authenticated connection, in which case these return empty/403 and you
    # fall back to the viageo.ch export. The fetchers log this clearly.
    "parcels": {
        "ogcapi": f"{GEODIENSTE}/av_0/deu/ogcapi",
        "collection": "RESF",            # Liegenschaften = legal parcels
        "collection_keywords": ["liegenschaft", "resf"],
        # NOTE: no server-side cql_filter — geodienste does not reliably support
        # OGC Part 3 filtering and may hang or ignore it. The loader applies a
        # tested client-side Kanton='VD' filter instead.
        "raw_table": "raw.parcels",
    },
    "buildings": {
        "ogcapi": f"{GEODIENSTE}/av_0/deu/ogcapi",
        "collection": "LCSF",            # Bodenbedeckung (land cover incl. buildings)
        "collection_keywords": ["bodenbedeckung", "lcsf"],
        "raw_table": "raw.buildings",
    },
}

# --- Cantonal / cadastral sources --------------------------------------------
# Parcel geometry and buildings for Vaud come from the cantonal portal
# (viageo.ch / ASIT-VD). These often require a one-off registered download
# rather than an open API, so the pipeline supports a local file fallback:
# drop the exported GeoPackage/Shapefile in DATA_DIR and the loader picks it up.
DATA_DIR = os.getenv("DATA_DIR", "/data/vd")
PARCELS_FILE = os.getenv("PARCELS_FILE", f"{DATA_DIR}/parcels_vd.gpkg")
BUILDINGS_FILE = os.getenv("BUILDINGS_FILE", f"{DATA_DIR}/buildings_vd.gpkg")

# --- OEREB federal per-parcel service ----------------------------------------
# Public-law restrictions. Queried per EGRID for shortlisted candidates only.
# Reference implementation host; cantonal OEREB services follow the same API.
OEREB_BASE = "https://oereb.vd.ch"        # Vaud cantonal OEREB endpoint
OEREB_EXTRACT = OEREB_BASE + "/oereb/extract/json"

# --- Building-zone classification (federal harmonized code) -------------------
# The real zoning layer exposes `affectation_principale_code`, the official
# federal primary-affectation code (confirmed from live data). Building zones
# are the "zones à bâtir" codes; everything else (agricultural, protected,
# deferred, public-infrastructure) is non-build.
#   11 Zone d'habitation            12 Zones besoins publics (built)
#   13 Zones centrales              14 Zones d'activités économiques
#   15 Zones mixtes                 16 Zones petites entités urbanisées
#   18 (transport within build zone)19 autres zones à bâtir
#   41/43 further build-zone variants
# Non-build examples: 21 agricole, 49 autres hors zone à bâtir, etc.
BUILDING_ZONE_CODES = {11, 12, 13, 14, 15, 16, 18, 19, 41, 43}

# Kept for any legacy text-based fallback, but code-based is authoritative.
BUILDING_ZONE_TOKENS = [
    "habitation", "logement", "activit", "mixte", "centr", "urbanis",
    "à bâtir", "a batir", "constructible", "wohn", "arbeit", "misch", "zentrum",
]
NON_BUILDING_TOKENS = [
    "agricol", "landwirtschaft", "forêt", "foret", "wald", "protect", "schutz",
    "différé", "differe", "utilité publique", "verdure", "transport",
]

# --- Database ----------------------------------------------------------------
# On Railway this comes from the PostGIS plugin as DATABASE_URL.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/swissland",
)

# --- HTTP behaviour ----------------------------------------------------------
REQUEST_TIMEOUT = 45              # read timeout per request (seconds)
PAGE_LIMIT = 200                  # OGC API Features page size. Small on purpose:
                                  # heavy AV parcel geometries at 1000/page can
                                  # stall the server mid-response; 200 completes
                                  # reliably and fails fast if a page is slow.
USER_AGENT = "swiss-land-pipeline/0.1 (deal-sourcing; contact: you@example.ch)"
