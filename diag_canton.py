"""
diag_canton.py — answer ONE question: which cantons does the zoning stream
return, in what order, and is VD reachable within a sane time budget?

Run on Railway with start command:  python diag_canton.py

It does NOT touch the database. It pages the zoning layer and reports the canton
mix as it goes, so we can see whether VD ever appears — and it tests whether the
server will filter to VD directly, which would make the whole problem vanish.
"""
import time
import requests
from collections import Counter

BASE = "https://geodienste.ch/db/npl_nutzungsplanung_v1_2_0/fra/ogcapi"
URL = f"{BASE}/collections/affectation_primaire/items"
UA = {"User-Agent": "swiss-land-diag/1.0", "Accept": "application/geo+json"}
CANTON_BBOX = "6.06,46.19,7.24,47.01"
CRS2056 = "http://www.opengis.net/def/crs/EPSG/0/2056"

print("=" * 70)
print("TEST 1 — What cantons come back, and in what order?")
print("=" * 70)
counts = Counter()
seen = 0
url = URL
params = {"f": "json", "limit": 500, "bbox": CANTON_BBOX, "crs": CRS2056}
start = time.time()
first_vd_at = None

while True:
    try:
        r = requests.get(url, params=params, timeout=(15, 60))
        if r.status_code != 200:
            print(f"  HTTP {r.status_code} — stopping")
            break
        data = r.json()
    except Exception as e:
        print(f"  request failed: {e} — stopping")
        break

    feats = data.get("features", [])
    if not feats:
        break

    for f in feats:
        c = (f.get("properties") or {}).get("canton")
        counts[c] += 1
        seen += 1
        if c == "VD" and first_vd_at is None:
            first_vd_at = seen
            print(f"  >>> FIRST VD FEATURE at position {seen} "
                  f"({int(time.time()-start)}s) <<<")

    elapsed = int(time.time() - start)
    print(f"  {seen} features ({elapsed}s): {dict(counts)}")

    # stop conditions: found VD, or 3 min, or 20k features
    if first_vd_at or elapsed > 180 or seen >= 20000:
        break

    nxt = None
    for link in data.get("links", []):
        if link.get("rel") == "next":
            nxt = link.get("href")
            break
    if not nxt:
        break
    url, params = nxt, None

print(f"\n  RESULT: scanned {seen} features in {int(time.time()-start)}s")
print(f"  canton mix: {dict(counts)}")
if first_vd_at:
    print(f"  VD IS reachable — first VD feature at position {first_vd_at}")
else:
    print("  NO VD FEATURES FOUND in this scan — VD is not reachable by paging.")

print()
print("=" * 70)
print("TEST 2 — Will the server filter to VD directly? (this would fix everything)")
print("=" * 70)

# Try several server-side filter syntaxes. If ANY works, we stop paging blindly.
attempts = [
    ("CQL2 filter",       {"f": "json", "limit": 5, "filter": "canton='VD'",
                           "filter-lang": "cql2-text", "crs": CRS2056}),
    ("plain attribute",   {"f": "json", "limit": 5, "canton": "VD", "crs": CRS2056}),
    ("CQL2 + bbox",       {"f": "json", "limit": 5, "bbox": CANTON_BBOX,
                           "filter": "canton='VD'", "filter-lang": "cql2-text",
                           "crs": CRS2056}),
]

for label, params in attempts:
    try:
        r = requests.get(URL, params=params, timeout=(15, 60))
        if r.status_code != 200:
            print(f"  [{r.status_code}] {label}: HTTP error")
            continue
        data = r.json()
        feats = data.get("features", [])
        matched = data.get("numberMatched", "n/a")
        cantons = {(f.get("properties") or {}).get("canton") for f in feats}
        ok = cantons == {"VD"} and feats
        print(f"  [{r.status_code}] {label}: returned={len(feats)} matched={matched} "
              f"cantons={cantons or '{}'}  {'<<< WORKS' if ok else ''}")
    except Exception as e:
        print(f"  [ERR] {label}: {e}")

print("\nDone.")
print("If TEST 2 shows a working filter, we use it and the problem is solved.")
print("If not, and TEST 1 found no VD, we need a different source for VD zoning.")
