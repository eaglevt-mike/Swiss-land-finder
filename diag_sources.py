"""
diag_sources.py — test BOTH candidate vector-zoning sources in one run.

  PART 1: ASIT-VD WFS (Vaud) — long shot; Vaud's own service is WMS-only.
  PART 2: Geneva SITG — has a documented ArcGIS REST API returning GeoJSON
          with attribute filtering, plus a WFS. This is the strong candidate.

No database writes. Run on Railway with:  python diag_sources.py
"""
import json
import requests

UA = {"User-Agent": "swiss-land-diag/1.0"}
T = (15, 60)


def show(label, r, want_json=True):
    print(f"  [{r.status_code}] {label}")
    if r.status_code != 200:
        print(f"      body: {r.text[:200]}")
        return None
    if not want_json:
        print(f"      len={len(r.text)} bytes; head: {r.text[:200]}")
        return None
    try:
        return r.json()
    except Exception:
        print(f"      NOT JSON. head: {r.text[:250]}")
        return None


print("=" * 72)
print("PART 1 — ASIT-VD / Vaud: is there ANY vector (WFS) zoning endpoint?")
print("=" * 72)

vd_attempts = [
    ("ASIT-VD WFS GetCapabilities",
     "https://ws.asitvd.ch/wfs",
     {"service": "WFS", "request": "GetCapabilities"}),
    ("Vaud geo WFS GetCapabilities",
     "https://www.geo.vd.ch/main/wsgi/mapserv_proxy",
     {"service": "WFS", "request": "GetCapabilities"}),
]
for label, url, params in vd_attempts:
    try:
        r = requests.get(url, params=params, headers=UA, timeout=T)
        body = r.text if r.status_code == 200 else ""
        print(f"  [{r.status_code}] {label}")
        if r.status_code == 200:
            # look for zoning-ish layer names in the capabilities XML
            low = body.lower()
            hits = [w for w in ("zone_affectation", "affectation", "zone")
                    if w in low]
            print(f"      capabilities len={len(body)}; keyword hits: {hits}")
            print(f"      head: {body[:200]}")
        else:
            print(f"      body: {r.text[:150]}")
    except Exception as e:
        print(f"  [ERR] {label}: {e}")

print()
print("=" * 72)
print("PART 2 — Geneva SITG: ArcGIS REST (GeoJSON!) + WFS")
print("=" * 72)

# The zoning layer: SIT_ZONE_AMENAG. ArcGIS MapServer layer 0 is the usual index.
GE_MAP = "https://vector.sitg.ge.ch/arcgis/rest/services/SIT_ZONE_AMENAG/MapServer"

print("\n-- 2a. Service metadata (does it exist, what layers?) --")
try:
    r = requests.get(GE_MAP, params={"f": "json"}, headers=UA, timeout=T)
    data = show("SIT_ZONE_AMENAG MapServer", r)
    if data:
        layers = data.get("layers", [])
        print(f"      layers: {[(l.get('id'), l.get('name')) for l in layers]}")
        print(f"      maxRecordCount: {data.get('maxRecordCount')}")
        print(f"      spatialRef: {(data.get('spatialReference') or {}).get('wkid')}")
except Exception as e:
    print(f"  [ERR] metadata: {e}")

print("\n-- 2b. Query layer 0 as GeoJSON (the money test) --")
try:
    r = requests.get(f"{GE_MAP}/0/query", headers=UA, timeout=T, params={
        "where": "1=1",
        "outFields": "*",
        "returnGeometry": "true",
        "resultRecordCount": 3,
        "f": "geojson",
    })
    data = show("layer 0 query f=geojson", r)
    if data:
        feats = data.get("features", [])
        print(f"      returned {len(feats)} features")
        if feats:
            props = feats[0].get("properties", {})
            print(f"      property keys: {sorted(props.keys())}")
            print(f"      example: {json.dumps(props, ensure_ascii=False)[:400]}")
            geom = feats[0].get("geometry", {})
            c = geom.get("coordinates")
            while isinstance(c, list) and c and isinstance(c[0], list):
                c = c[0]
            print(f"      first coord: {c}  (~2.5M => LV95/2056)")
except Exception as e:
    print(f"  [ERR] geojson query: {e}")

print("\n-- 2c. Count all features (how big is the whole canton?) --")
try:
    r = requests.get(f"{GE_MAP}/0/query", headers=UA, timeout=T, params={
        "where": "1=1", "returnCountOnly": "true", "f": "json",
    })
    data = show("layer 0 count", r)
    if data:
        print(f"      TOTAL FEATURES IN GENEVA ZONING: {data.get('count')}")
except Exception as e:
    print(f"  [ERR] count: {e}")

print("\n-- 2d. Attribute filter test (can we query by zone type?) --")
try:
    r = requests.get(f"{GE_MAP}/0/query", headers=UA, timeout=T, params={
        "where": "1=1", "outFields": "*", "returnGeometry": "false",
        "resultRecordCount": 50, "f": "json",
    })
    data = show("layer 0 attributes only", r)
    if data:
        feats = data.get("features", [])
        if feats:
            # show distinct values of every low-cardinality field
            keys = sorted(feats[0].get("attributes", {}).keys())
            print(f"      fields: {keys}")
            for k in keys:
                vals = {str((f.get("attributes") or {}).get(k)) for f in feats}
                if len(vals) <= 15:
                    print(f"        distinct {k}: {sorted(vals)}")
except Exception as e:
    print(f"  [ERR] attribute test: {e}")

print("\n" + "=" * 72)
print("VERDICT")
print("=" * 72)
print("If 2b returned features with an LV95 coord and real property keys, then")
print("Geneva zoning is FULLY AVAILABLE as GeoJSON — no paging through other")
print("cantons, no filters ignored. That becomes our primary source.")
