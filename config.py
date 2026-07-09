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

# Collection IDs below are the REAL geodienste ids (French domain names),
# confirmed from each service's /collections endpoint — not the model slug.
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

# --- Building-zone classification --------------------------------------------
# The harmonized MGDM Nutzungsplanung model groups every zone under one of 9
# primary uses. The build zones are residential, work, mixed, and centre. The
# geodienste service labels these in the language of the endpoint (we use the
# French /fra endpoint), and the harmonized code is also exposed. To be robust
# across languages and label spellings, we classify by SUBSTRING match on a set
# of tokens rather than exact strings. Anything matching is treated as a
# building zone; agriculture/protection/forest/traffic fall through to non-build.
BUILDING_ZONE_TOKENS = [
    # German
    "wohn", "arbeit", "misch", "zentrum", "kern", "bauzone",
    # French
    "habitation", "logement", "travail", "activit", "mixte", "centre",
    "batir", "à bâtir", "a batir", "constructible",
    # Italian (a few cantons)
    "abitativ", "lavoro", "mista", "centro", "edificabile",
]

# Tokens that force NON-build even if a build token also appears (safety net).
NON_BUILDING_TOKENS = [
    "agricol", "landwirtschaft", "agricoltura",
    "protect", "schutz", "protezione",
    "foret", "wald", "bosco", "forest",
]

# --- Database ----------------------------------------------------------------
# On Railway this comes from the PostGIS plugin as DATABASE_URL.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/swissland",
)

# --- HTTP behaviour ----------------------------------------------------------
REQUEST_TIMEOUT = 60
PAGE_LIMIT = 1000                 # OGC API Features page size
USER_AGENT = "swiss-land-pipeline/0.1 (deal-sourcing; contact: you@example.ch)"
