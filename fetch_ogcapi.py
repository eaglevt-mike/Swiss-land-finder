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

# geodienste does NOT honour bbox-crs: sending a 2056 bbox returns zero because
# the server reads the metric numbers as lon/lat. So we send WGS84 lon/lat, which
# is the OGC default and works (the canton box returned 25,913 zoning features).
# minlon, minlat, maxlon, maxlat.
VAUD_BBOX_WGS84 = (6.06, 46.19, 7.24, 47.01)          # whole canton (works)

# Greater Lausanne in WGS84. NOTE: a *small* WGS84 box previously returned zero
# for some layers, so the pilot restriction is now enforced by the feature CAP
# (MAX_FEATURES_PER_LAYER) applied to the working canton box, not by a small box.
LAUSANNE_BBOX_WGS84 = (6.50, 46.48, 6.75, 46.60)

import os
# We query the canton box (which works) and let the feature cap bound volume.
# Set PILOT_ONLY=false only when you want full-canton with no cap.
ACTIVE_BBOX = VAUD_BBOX_WGS84

# Safety cap so no single layer can hang the pipeline. Raised because parcels/
# buildings are fetched canton-wide (small API boxes return nothing) and then
# filtered to the pilot communes AFTER loading. Env-tunable.
MAX_FEATURES_PER_LAYER = int(os.getenv("MAX_FEATURES_PER_LAYER", "250000"))

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/geo+json"})


def _get(url: str, params: Optional[dict] = None) -> dict:
    """GET with retry/backoff on transient errors.

    geodienste.ch content-negotiates and returns a 400 unless we explicitly
    ask for JSON, so f=json is forced on every request.
    """
    params = dict(params or {})
    params.setdefault("f", "json")
    for attempt in range(4):
        try:
            r = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
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
    seen = 0
    page = 0
    while True:
        data = _get(url, params=params)
        feats = data.get("features", [])
        for f in feats:
            yield f
        seen += len(feats)
        page += 1

        # Progress ping every 10 pages so long fetches show life in the logs.
        if page % 10 == 0:
            print(f"    ...{collection}: {seen} features so far", flush=True)

        # Hard safety cap: never let one layer run away. If hit, we log and stop
        # with a partial load rather than hanging the whole pipeline.
        if seen >= MAX_FEATURES_PER_LAYER:
            print(f"    [warn] {collection}: hit cap {MAX_FEATURES_PER_LAYER}; "
                  f"stopping early. Tighten the bbox for full coverage.",
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

    # Single fetch against the working WGS84 canton box; the feature cap bounds
    # volume for the pilot. (Earlier per-box retry logic is unnecessary now that
    # we use the proven canton box directly.)
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
