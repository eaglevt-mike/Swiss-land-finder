"""
OGC API Features fetcher for geodienste.ch harmonized layers.

geodienste.ch serves each MGDM model as an OGC API Features endpoint. We:
  1. discover the collections at /collections
  2. page through /collections/<id>/items using the `limit` + `next` link
  3. filter to the target canton with a bbox (Vaud) so we don't drag the whole
     of Switzerland across the wire
  4. yield GeoJSON features for the loader to normalise into raw.* tables

This is intentionally dependency-light (requests only) so it runs in a small
Railway worker. GeoPandas is used only in the loader, where geometry parsing
happens.
"""
from __future__ import annotations
import time
from typing import Iterator, Optional
import requests

from config import PAGE_LIMIT, REQUEST_TIMEOUT, USER_AGENT, PILOT_ONLY

# WGS84 lon/lat box. With parcels/buildings off by default (Phase A uses zoning
# only), there's no volume pressure, so we use the FULL canton box that returned
# the complete ~25,900 Vaud zoning polygons. The earlier Geneva-excluding box was
# only needed to reduce parcel volume and was cutting off western Vaud zoning.
VAUD_BBOX_WGS84 = (6.06, 46.19, 7.24, 47.01)

# Greater Lausanne in WGS84. NOTE: a *small* WGS84 box previously returned zero
# for some layers, so the pilot restriction is now enforced by the feature CAP
# (MAX_FEATURES_PER_LAYER) applied to the working canton box, not by a small box.
LAUSANNE_BBOX_WGS84 = (6.50, 46.48, 6.75, 46.60)

import os
# We query the canton box (which works) and let the feature cap bound volume.
# Set PILOT_ONLY=false only when you want full-canton with no cap.
ACTIVE_BBOX = VAUD_BBOX_WGS84

# Safety cap so no single layer can hang the pipeline. Parcels are fetched
# canton-wide then filtered to VD client-side, so keep this bounded. Env-tunable.
MAX_FEATURES_PER_LAYER = int(os.getenv("MAX_FEATURES_PER_LAYER", "80000"))

# Hard ceiling on pages regardless of features, so a slow/looping endpoint can
# never hang the deploy. At PAGE_LIMIT=200 this allows up to ~80k features.
MAX_PAGES = int(os.getenv("MAX_PAGES", "450"))

# Absolute wall-clock budget per layer (seconds). Once exceeded, we stop with a
# partial load. This is the ultimate anti-hang guard, independent of pages/features.
import time as _time
LAYER_TIME_BUDGET = int(os.getenv("LAYER_TIME_BUDGET", "300"))

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/geo+json"})


def _get(url: str, params: Optional[dict] = None) -> dict:
    """GET with retry/backoff and a hard connect+read timeout.

    geodienste content-negotiates and returns 400 unless f=json is set.
    The timeout is a (connect, read) tuple: if the server accepts the connection
    but then stalls mid-response, the read times out fast rather than hanging the
    whole deploy (the failure mode we hit on the heavy AV parcel layer).
    """
    params = dict(params or {})
    params.setdefault("f", "json")
    # (connect timeout, read timeout) in seconds.
    timeout = (15, REQUEST_TIMEOUT)
    for attempt in range(4):
        try:
            r = _session.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed to GET {url}")


def list_collections(ogcapi_base: str) -> list[str]:
    """Return the collection IDs exposed by an OGC API Features service."""
    data = _get(f"{ogcapi_base}/collections")
    return [c["id"] for c in data.get("collections", [])]


def fetch_features(
    ogcapi_base: str,
    collection: str,
    bbox: tuple[float, float, float, float] = ACTIVE_BBOX,
    out_crs: str = "http://www.opengis.net/def/crs/EPSG/0/2056",
    cql_filter: str = None,
) -> Iterator[dict]:
    """
    Yield GeoJSON features from one collection, paging until exhausted.

    CRS strategy (confirmed empirically against geodienste via diagnose.py):
      * bbox MUST be WGS84 lon/lat with NO bbox-crs — geodienste ignores bbox-crs,
        so a 2056 bbox returns zero.
      * SMALL boxes return zero on this server; only a canton-sized box matches.
        So we fetch the whole canton and (optionally) clip to the pilot area in
        PostGIS afterwards.
      * crs=2056 IS honoured for output, so geometry comes back in Swiss LV95.
        The loader is also CRS-defensive, so WGS84 output would still store OK.
    """
    url = f"{ogcapi_base}/collections/{collection}/items"
    params = {
        "limit": PAGE_LIMIT,
        "bbox": ",".join(str(v) for v in bbox),   # WGS84 lon/lat (OGC default)
        "crs": out_crs,                            # ask for 2056 geometry back
    }
    if cql_filter:
        # OGC API Features Part 3: server-side attribute filter (e.g. Kanton='VD').
        # If geodienste doesn't support it, the request errors and the caller
        # falls back to client-side filtering.
        params["filter"] = cql_filter
        params["filter-lang"] = "cql2-text"
    seen = 0
    page = 0
    started = _time.time()
    while True:
        try:
            data = _get(url, params=params)
        except Exception as e:
            print(f"    [warn] {collection}: page {page+1} failed ({e}); "
                  f"stopping with {seen} features.", flush=True)
            break
        feats = data.get("features", [])
        for f in feats:
            yield f
        seen += len(feats)
        page += 1

        # Progress ping every 10 pages so long fetches show life in the logs.
        if page % 10 == 0:
            elapsed = int(_time.time() - started)
            print(f"    ...{collection}: {seen} features so far ({elapsed}s)", flush=True)

        # Wall-clock budget: ultimate anti-hang guard.
        if _time.time() - started > LAYER_TIME_BUDGET:
            print(f"    [warn] {collection}: hit time budget {LAYER_TIME_BUDGET}s; "
                  f"stopping with {seen} features.", flush=True)
            break

        # Hard safety cap: never let one layer run away. If hit, we log and stop
        # with a partial load rather than hanging the whole pipeline.
        if seen >= MAX_FEATURES_PER_LAYER:
            print(f"    [warn] {collection}: hit cap {MAX_FEATURES_PER_LAYER}; "
                  f"stopping early. Tighten the bbox for full coverage.",
                  flush=True)
            break

        # Hard page ceiling: absolute anti-hang guard, independent of features.
        if page >= MAX_PAGES:
            print(f"    [warn] {collection}: hit page ceiling {MAX_PAGES}; stopping.",
                  flush=True)
            break

        # Follow the standard OGC API "next" link if present.
        next_url = None
        for link in data.get("links", []):
            if link.get("rel") == "next":
                next_url = link.get("href")
                break
        if not next_url or not feats:
            break
        url, params = next_url, None   # next link already carries the params

    if seen == 0:
        # Surface WHY nothing came back so a zero-run is diagnosable from logs.
        print(f"    [warn] {collection}: 0 features for bbox {bbox} "
              f"(check bbox covers the canton and collection has data here)",
              flush=True)


def fetch_layer(source_cfg: dict) -> Iterator[dict]:
    """
    Stream features from a source's main collection.

    The collection id is taken from config ("collection") because geodienste's
    real ids are French domain names (e.g. "affectation_primaire"), not the
    model slug. If config doesn't pin one, we discover and match by keyword.
    """
    base = source_cfg["ogcapi"]
    collection = source_cfg.get("collection")

    available = None
    if not collection:
        available = list_collections(base)
        if not available:
            return
        keywords = source_cfg.get("collection_keywords", [])
        collection = next(
            (c for c in available if any(k in c.lower() for k in keywords)),
            available[0],
        )

    # Verify the pinned collection exists; if not, fall back to keyword match
    # against the live collection list so a stale id degrades gracefully.
    if available is None:
        available = list_collections(base)
    if collection not in available:
        keywords = source_cfg.get("collection_keywords", [])
        collection = next(
            (c for c in available if any(k in c.lower() for k in keywords)),
            available[0] if available else collection,
        )

    # Try a server-side attribute filter (e.g. Kanton='VD') if the source
    # defines one. geodienste may not support OGC Part 3 filtering; if the
    # filtered request errors, fall back to unfiltered (the loader still applies
    # a client-side canton filter as a safety net).
    cql = source_cfg.get("cql_filter")
    if cql:
        try:
            first = None
            gen = fetch_features(base, collection, bbox=ACTIVE_BBOX, cql_filter=cql)
            first = next(gen, "STOP")
            if first != "STOP":
                yield first
                yield from gen
                return
            # empty result with filter — fall through to unfiltered below
            print(f"    [info] {collection}: server filter returned 0, retrying unfiltered",
                  flush=True)
        except Exception as e:
            print(f"    [info] {collection}: server filter unsupported ({e}); unfiltered",
                  flush=True)

    yield from fetch_features(base, collection, bbox=ACTIVE_BBOX)


if __name__ == "__main__":
    # Smoke test against the live service (zoning): count features in the bbox.
    from config import SOURCES
    n = 0
    for _feat in fetch_layer(SOURCES["zoning"]):
        n += 1
        if n >= 5:
            break
    print(f"zoning: pulled {n} sample features OK")
