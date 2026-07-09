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

from config import PAGE_LIMIT, REQUEST_TIMEOUT, USER_AGENT

# Approximate bounding box for Canton Vaud in LV95 (EPSG:2056), metres.
# minx, miny, maxx, maxy. Comfortable envelope around the canton.
VAUD_BBOX_2056 = (494000, 118000, 585000, 197000)

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
    bbox: tuple[float, float, float, float] = VAUD_BBOX_2056,
    bbox_crs: str = "http://www.opengis.net/def/crs/EPSG/0/2056",
) -> Iterator[dict]:
    """
    Yield GeoJSON features from one collection, paging until exhausted.

    We request geometry in LV95 (2056) so nothing is reprojected on ingest.
    Not every deployment honours bbox-crs; the loader tolerates either CRS and
    the enrichment step is CRS-correct regardless because we store 2056.
    """
    url = f"{ogcapi_base}/collections/{collection}/items"
    params = {
        "limit": PAGE_LIMIT,
        "bbox": ",".join(str(v) for v in bbox),
        "bbox-crs": bbox_crs,
        "crs": "http://www.opengis.net/def/crs/EPSG/0/2056",
    }
    seen = 0
    while True:
        data = _get(url, params=params)
        feats = data.get("features", [])
        for f in feats:
            yield f
        seen += len(feats)

        # Follow the standard OGC API "next" link if present.
        next_url = None
        for link in data.get("links", []):
            if link.get("rel") == "next":
                next_url = link.get("href")
                break
        if not next_url or not feats:
            break
        url, params = next_url, None   # next link already carries the params


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

    yield from fetch_features(base, collection)


if __name__ == "__main__":
    # Smoke test against the live service (zoning): count features in the bbox.
    from config import SOURCES
    n = 0
    for _feat in fetch_layer(SOURCES["zoning"]):
        n += 1
        if n >= 5:
            break
    print(f"zoning: pulled {n} sample features OK")
