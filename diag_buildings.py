"""
diag_buildings.py — probe Geneva's building layers before we build Phase B.

Two candidates:
  A) CAD_BATIMENT_HORSOL      — above-ground building footprints (the underuse
                                signal: how much of a zone is actually built)
  B) SIT_SURELEVATION_BATIMENT — buildings the canton says COULD BE RAISED
                                (a ready-made densification layer!)

Questions to answer:
  1. How many features? (volume = feasibility)
  2. Do they return GeoJSON in 2056 like the zoning did?
  3. What attributes? (floors? height? commune?)
  4. Can we filter/aggregate server-side to avoid pulling 100k footprints?
"""
import json
import requests

UA = {"User-Agent": "swiss-land-diag/1.0"}
T = (15, 90)
BASE = "https://vector.sitg.ge.ch/arcgis/rest/services"


def probe(service, label):
    url = f"{BASE}/{service}/MapServer"
    print("\n" + "=" * 74)
    print(f"{label}  ({service})")
    print("=" * 74)

    # metadata
    try:
        r = requests.get(url, params={"f": "json"}, headers=UA, timeout=T)
        if r.status_code != 200:
            print(f"  [{r.status_code}] metadata failed: {r.text[:150]}")
            return
        m = r.json()
        layers = m.get("layers", [])
        print(f"  layers: {[(l.get('id'), l.get('name')) for l in layers]}")
        print(f"  maxRecordCount: {m.get('maxRecordCount')}")
    except Exception as e:
        print(f"  [ERR] metadata: {e}")
        return

    q = f"{url}/0/query"

    # count
    try:
        r = requests.get(q, headers=UA, timeout=T, params={
            "where": "1=1", "returnCountOnly": "true", "f": "json"})
        cnt = r.json().get("count")
        print(f"  TOTAL FEATURES: {cnt}")
    except Exception as e:
        print(f"  [ERR] count: {e}")
        cnt = None

    # sample with attributes
    try:
        r = requests.get(q, headers=UA, timeout=T, params={
            "where": "1=1", "outFields": "*", "returnGeometry": "true",
            "outSR": 2056, "resultRecordCount": 3, "f": "geojson"})
        data = r.json()
        feats = data.get("features", [])
        print(f"  sample returned: {len(feats)}")
        if feats:
            p = feats[0].get("properties", {})
            print(f"  property keys: {sorted(p.keys())}")
            print(f"  example: {json.dumps(p, ensure_ascii=False)[:400]}")
            c = feats[0].get("geometry", {}).get("coordinates")
            while isinstance(c, list) and c and isinstance(c[0], list):
                c = c[0]
            print(f"  first coord: {c}  (2.5M => LV95 OK)")
    except Exception as e:
        print(f"  [ERR] sample: {e}")

    # Can we aggregate server-side? (avoids pulling 100k footprints)
    try:
        r = requests.get(q, headers=UA, timeout=T, params={
            "where": "1=1",
            "outStatistics": json.dumps([{
                "statisticType": "count",
                "onStatisticField": "OBJECTID",
                "outStatisticFieldName": "n"}]),
            "f": "json"})
        d = r.json()
        ok = "features" in d and d["features"]
        print(f"  server-side aggregation supported: {bool(ok)}")
    except Exception as e:
        print(f"  [ERR] aggregation: {e}")


probe("CAD_BATIMENT_HORSOL", "A) BUILDING FOOTPRINTS — the underuse signal")
probe("SIT_SURELEVATION_BATIMENT", "B) BUILDINGS THAT COULD BE RAISED (bonus!)")

print("\n" + "=" * 74)
print("WHAT WE'RE LOOKING FOR")
print("=" * 74)
print("A) If footprints are ~100k, we CANNOT pull them all naively — but we can")
print("   fetch only those INTERSECTING our 198 target zones, which is a small")
print("   subset. ArcGIS supports a geometry filter, so this is feasible.")
print("B) If SIT_SURELEVATION exists with real features, Geneva has ALREADY")
print("   computed densification potential — that would be a massive shortcut.")
