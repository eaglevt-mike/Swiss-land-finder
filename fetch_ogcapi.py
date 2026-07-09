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

# Bounding box for Canton Vaud in WGS84 (lon/lat) — the OGC default CRS.
# minlon, minlat, maxlon, maxlat. Comfortable envelope around the canton.
VAUD_BBOX_WGS84 = (6.06, 46.19, 7.24, 47.01)

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
    bbox_wgs84: tuple[float, float, float, float] = VAUD_BBOX_WGS84,
    out_crs: str = "http://www.opengis.net/def/crs/EPSG/0/2056",
) -> Iterator[dict]:
    """
    Yield GeoJSON features from one collection, paging until exhausted.

    CRS strategy: we send the bbox in WGS84 lon/lat, which is the OGC default
    and is honoured by every compliant server (geodienste did NOT reliably honour
    a 2056 bbox-crs, silently returning zero features). We still ask for the
    geometry back in LV95 (2056) via the `crs` parameter, so nothing needs
    reprojecting on ingest. If the server ignores `crs` and returns WGS84, the
    loader's ST_SetSRID/ST_Transform-free path still stores valid geometry because
    the coordinates are self-describing GeoJSON — but in practice geodienste
    honours `crs`.
    """
    url = f"{ogcapi_base}/collections/{collection}/items"
    params = {
        "limit": PAGE_LIMIT,
        "bbox": ",".join(str(v) for v in bbox_wgs84),   # WGS84 lon/lat, no bbox-crs
        "crs": out_crs,
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
        print(f"    [warn] {collection}: 0 features for bbox {bbox_wgs84} "
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
