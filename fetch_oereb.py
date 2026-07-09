"""
OEREB (Cadastre of Public-law Restrictions) fetcher.

Unlike the harmonized bulk layers, OEREB is a per-parcel legal extract. It is
the authoritative answer to "is this parcel actually developable, or is it
encumbered?" — the single most decision-relevant enrichment in the platform.

Because it is per-parcel and rate-sensitive, we do NOT run it across the whole
canton. The scoring engine produces a shortlist of candidate parcels; this
fetcher then pulls a structured extract for each and writes the restriction
rows into raw.oereb_restrictions for the enrichment step to consume.
"""
from __future__ import annotations
import time
from typing import Iterator
import requests

from config import OEREB_EXTRACT, REQUEST_TIMEOUT, USER_AGENT

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})


def fetch_extract(egrid: str) -> dict | None:
    """
    Fetch the OEREB JSON extract for one parcel by EGRID.
    Returns the parsed extract dict, or None if unavailable.
    """
    params = {"EGRID": egrid, "GEOMETRY": "false"}
    for attempt in range(3):
        try:
            r = _session.get(OEREB_EXTRACT, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            if r.status_code in (429, 503, 504):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
        except requests.RequestException:
            if attempt == 2:
                return None
            time.sleep(2 ** attempt)
    return None


def parse_restrictions(egrid: str, extract: dict) -> Iterator[dict]:
    """
    Flatten an OEREB extract into one dict per restriction theme.

    The extract structure nests restrictions under
      GetExtractByIdResponse.extract.RealEstate[].RestrictionOnLandownership[]
    Field names vary slightly by cantonal implementation, so we access
    defensively and skip anything malformed rather than crashing the run.
    """
    try:
        real_estates = (
            extract.get("GetExtractByIdResponse", {})
            .get("extract", {})
            .get("RealEstate", [])
        )
    except AttributeError:
        return

    for re_obj in real_estates:
        for r in re_obj.get("RestrictionOnLandownership", []) or []:
            theme = r.get("Theme", {})
            yield {
                "egrid": egrid,
                "theme_code": theme.get("Code") or theme.get("Sub_Code"),
                "theme_text": _localized(theme.get("Text")),
                "sub_theme": r.get("SubTheme"),
                "legal_state": r.get("Lawstatus", {}).get("Code"),
            }


def _localized(text_field) -> str | None:
    """OEREB multilingual text comes as a list of {Language, Text}."""
    if not text_field:
        return None
    if isinstance(text_field, str):
        return text_field
    if isinstance(text_field, list):
        # prefer French for Vaud, else first available
        by_lang = {t.get("Language"): t.get("Text") for t in text_field if isinstance(t, dict)}
        return by_lang.get("fr") or next(iter(by_lang.values()), None)
    return None


def fetch_for_egrids(egrids: list[str]) -> Iterator[dict]:
    """Yield restriction rows for a shortlist of EGRIDs, politely rate-limited."""
    for egrid in egrids:
        extract = fetch_extract(egrid)
        if extract:
            yield from parse_restrictions(egrid, extract)
        time.sleep(0.3)   # be gentle with the cantonal service
