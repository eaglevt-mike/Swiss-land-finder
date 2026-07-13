"""
fetch_sitg.py — Geneva zoning via the SITG ArcGIS REST API.

This is refreshingly simple compared to geodienste: the ENTIRE canton is 2,494
features, the API returns GeoJSON directly, supports real attribute filtering,
and honours paging via resultOffset. No canton ordering, no ignored filters, no
5-minute timeouts.

Confirmed live fields (from diag_sources.py):
    OBJECTID, COMMUNE, NO_COMM_FEDERAL (BFS), ZONE (code), NOM_ZONE (label),
    DESCRIPTION (planning rules incl. height limits), INDICE (density index),
    SHAPE.AREA, ZONE_PREEXISTANTE, RESTRICTION, DS_OPB
"""
from __future__ import annotations
import time
from typing import Iterator
import requests

from config import SITG_ZONING, SITG_PAGE, REQUEST_TIMEOUT, USER_AGENT

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


def _get(params: dict) -> dict:
    """GET with retry and a hard (connect, read) timeout."""
    timeout = (15, REQUEST_TIMEOUT)
    for attempt in range(3):
        try:
            r = _session.get(SITG_ZONING, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("SITG request failed")


def count_features() -> int:
    """How many zoning features does Geneva have? (Sanity check / logging.)"""
    data = _get({"where": "1=1", "returnCountOnly": "true", "f": "json"})
    return int(data.get("count", 0))


def fetch_zoning() -> Iterator[dict]:
    """
    Yield every Geneva zoning feature as GeoJSON, paging with resultOffset.

    We request outSR=2056 so geometry comes back in Swiss LV95 directly (the
    service stores 2056 but defaults GeoJSON output to WGS84). The loader is
    CRS-defensive anyway, so either would work.
    """
    offset = 0
    total = 0
    while True:
        data = _get({
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": 2056,               # Swiss LV95 straight out
            "resultOffset": offset,
            "resultRecordCount": SITG_PAGE,
            "f": "geojson",
        })
        feats = data.get("features", [])
        if not feats:
            break
        for f in feats:
            yield f
        total += len(feats)
        offset += len(feats)
        print(f"    ...SITG zoning: {total} features", flush=True)

        # ArcGIS signals more pages via exceededTransferLimit; also stop if the
        # page came back short (last page) as a belt-and-braces guard.
        if len(feats) < SITG_PAGE and not data.get("exceededTransferLimit"):
            break
        if total > 50000:                # sanity ceiling; GE is ~2.5k
            print("    [warn] SITG: unexpected volume, stopping", flush=True)
            break


if __name__ == "__main__":
    print("Geneva zoning feature count:", count_features())
    n = 0
    for f in fetch_zoning():
        n += 1
        if n == 1:
            print("first feature props:", sorted((f.get("properties") or {}).keys()))
        if n >= 5:
            break
    print("sampled OK")
