"""
diagnose.py — one-shot probe to find which query variant geodienste actually
honours. Run this ONCE on Railway (set it as the start command, or run via the
Railway shell) and read the output. It makes no DB changes.

    python diagnose.py

It tries the zoning collection with several bbox / CRS combinations and prints
how many features each returns, so we stop guessing and use whatever works.
"""
import requests, json

BASE = "https://geodienste.ch/db/npl_nutzungsplanung_v1_2_0/fra/ogcapi"
COLL = "affectation_primaire"
URL = f"{BASE}/collections/{COLL}/items"
UA = {"User-Agent": "swiss-land-diagnose/1.0", "Accept": "application/geo+json"}

# Lausanne area expressed both ways
WGS84_SMALL = "6.55,46.50,6.72,46.58"
WGS84_CANTON = "6.06,46.19,7.24,47.01"
LV95_SMALL = "2528000,1148000,2548000,1160000"
LV95_CANTON = "494000,118000,585000,197000"
CRS2056 = "http://www.opengis.net/def/crs/EPSG/0/2056"

VARIANTS = [
    ("no bbox at all (limit 5)",            {"f": "json", "limit": 5}),
    ("WGS84 small, no bbox-crs",            {"f": "json", "limit": 5, "bbox": WGS84_SMALL}),
    ("WGS84 canton, no bbox-crs",           {"f": "json", "limit": 5, "bbox": WGS84_CANTON}),
    ("LV95 small + bbox-crs=2056",          {"f": "json", "limit": 5, "bbox": LV95_SMALL, "bbox-crs": CRS2056}),
    ("LV95 canton + bbox-crs=2056",         {"f": "json", "limit": 5, "bbox": LV95_CANTON, "bbox-crs": CRS2056}),
    ("WGS84 small + crs=2056 out",          {"f": "json", "limit": 5, "bbox": WGS84_SMALL, "crs": CRS2056}),
    ("WGS84 canton + crs=2056 out",         {"f": "json", "limit": 5, "bbox": WGS84_CANTON, "crs": CRS2056}),
]

print(f"Probing {URL}\n")
for label, params in VARIANTS:
    try:
        r = requests.get(URL, params=params, headers=UA, timeout=60)
        status = r.status_code
        n = "?"
        matched = "?"
        sample_coord = ""
        if status == 200:
            data = r.json()
            feats = data.get("features", [])
            n = len(feats)
            matched = data.get("numberMatched", "n/a")
            if feats:
                geom = feats[0].get("geometry", {})
                # grab first coordinate pair to reveal the CRS of returned geom
                c = geom.get("coordinates")
                while isinstance(c, list) and c and isinstance(c[0], list):
                    c = c[0]
                sample_coord = f" firstcoord={c}"
        print(f"[{status}] returned={n:<3} matched={matched:<8} {label}{sample_coord}")
    except Exception as e:
        print(f"[ERR] {label}: {e}")

print("\nDone. The variant(s) with returned>0 are the ones to use.")
print("firstcoord tells you the CRS of returned geometry: ~2.5M => LV95, ~6-8 => WGS84.")
