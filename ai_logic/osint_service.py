"""Live OSINT: weather (Open-Meteo), news (Google News RSS), Agmarknet commodity metadata."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import requests

AGMARKNET_API = "https://api.agmarknet.gov.in/v1"
OPEN_METEO_GEO = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
DATA_GOV_IN_BASE = "https://api.data.gov.in/resource"

DEFAULT_TIMEOUT = 25
NEWS_MAX_ITEMS = 8
COMMODITY_MAX_PAGES = 8
HIGH_TEMP_C = 35.0
KEYWORD_COMMODITY_HINTS = (
    "milk",
    "ghee",
    "butter",
    "sugar",
    "wheat",
    "rice",
    "oil",
    "gram",
    "pulse",
    "potato",
    "onion",
)


def _http_get(url: str, *, params: dict[str, Any] | None = None) -> requests.Response:
    return requests.get(
        url,
        params=params,
        headers={"User-Agent": "OpenLabel/1.0 (hackathon; +https://example.invalid)"},
        timeout=DEFAULT_TIMEOUT,
    )


def _geocode_location(location: str) -> dict[str, Any] | None:
    r = _http_get(OPEN_METEO_GEO, params={"name": location, "count": 1, "language": "en"})
    r.raise_for_status()
    data = r.json()
    results = data.get("results") or []
    if not results:
        return None
    g = results[0]
    return {
        "name": g.get("name"),
        "latitude": g.get("latitude"),
        "longitude": g.get("longitude"),
        "country": g.get("country"),
        "admin1": g.get("admin1"),  # state/region
        "admin2": g.get("admin2"),  # district
        "timezone": g.get("timezone"),
    }


def _fetch_weather(lat: float, lon: float, location_label: str) -> dict[str, Any]:
    r = _http_get(
        OPEN_METEO_API,
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "timezone": "auto",
        },
    )
    r.raise_for_status()
    data = r.json()
    cur = data.get("current") or {}
    temp = cur.get("temperature_2m")
    risk_note = None
    if temp is not None and float(temp) >= HIGH_TEMP_C:
        risk_note = (
            f"Current ambient temperature around {location_label} is {temp}°C (≥{HIGH_TEMP_C:.0f}°C). "
            "Heat stress increases spoilage and thermal denaturation risk for dairy and "
            "high-protein beverages during storage and transport."
        )
    return {
        "temp_c": temp,
        "location": location_label,
        "relative_humidity_percent": cur.get("relative_humidity_2m"),
        "weather_code": cur.get("weather_code"),
        "wind_speed_kmh": cur.get("wind_speed_10m"),
        "conditions": "hot" if temp is not None and float(temp) >= HIGH_TEMP_C else "moderate",
        "risk_note": risk_note,
        "source": "open-meteo.com",
    }


def _severity_from_text(title: str, summary: str) -> str:
    blob = f"{title} {summary}".lower()
    high_kw = (
        "recall",
        "ban",
        "contamination",
        "failed",
        "unsafe",
        "cancer",
        "death",
        "poison",
        "aflatoxin",
        "pathogen",
    )
    med_kw = ("advisory", "notice", "warning", "inspection", "sample", "misleading", "label", "organic")
    if any(k in blob for k in high_kw):
        return "high"
    if any(k in blob for k in med_kw):
        return "medium"
    return "low"


def _parse_google_news_rss(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []
    out: list[dict[str, Any]] = []
    for item in channel.findall("item")[:NEWS_MAX_ITEMS]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        desc = (item.findtext("description") or "").strip()
        # strip HTML tags lightly
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()[:500]
        sev = _severity_from_text(title, desc)
        out.append(
            {
                "severity": sev,
                "region": "India",
                "summary": title if not desc else f"{title} — {desc}"[:800],
                "published_at": pub,
                "link": link,
                "source": "Google News RSS",
            }
        )
    return out


def _fetch_fssai_news_rss(location: str, state_hint: str | None) -> dict[str, Any]:
    q_parts = ["FSSAI", "India", "food", "safety"]
    if state_hint:
        q_parts.append(state_hint)
    else:
        q_parts.append(location)
    query = " ".join(q_parts)
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    )
    try:
        r = _http_get(url)
        r.raise_for_status()
        items = _parse_google_news_rss(r.text)
        return {"ok": True, "items": items, "query": query, "feed_url": url}
    except Exception as e:
        return {"ok": False, "error": str(e), "items": [], "query": query, "feed_url": url}


def _tokenize_product(name: str) -> set[str]:
    words = re.findall(r"[a-zA-Z]{3,}", name.lower())
    return {w for w in words if w not in {"the", "and", "for", "with", "from", "drink", "food"}}


def _fetch_commodity_match(product_name: str) -> dict[str, Any]:
    """Public Agmarknet API: list commodities and fuzzy-match by product name tokens."""
    tokens = _tokenize_product(product_name)
    if not tokens:
        tokens = {"food"}
    pn = product_name.lower()
    hint_keywords = [k for k in KEYWORD_COMMODITY_HINTS if k in pn]
    best: tuple[int, dict[str, Any]] | None = None

    def score_row(row: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        name = (row.get("cmdt_name") or "").lower()
        score = sum(1 for t in tokens if t in name)
        for kw in hint_keywords:
            if kw in name:
                score += 5
        detail = {
            "commodity_id": row.get("id"),
            "cmdt_name": row.get("cmdt_name"),
            "cmdt_group": row.get("cmdt_group"),
            "comm_code": row.get("comm_code"),
            "matched_tokens": sorted(t for t in tokens if t in name),
            "matched_hints": [kw for kw in hint_keywords if kw in name],
        }
        return score, detail

    try:
        for page in range(1, COMMODITY_MAX_PAGES + 1):
            r = _http_get(
                f"{AGMARKNET_API}/commodities",
                params={"limit": 100, "page": page},
            )
            r.raise_for_status()
            payload = r.json()
            rows = payload.get("data") or []
            if not rows:
                break
            for row in rows:
                sc, det = score_row(row)
                if sc > 0 and (best is None or sc > best[0]):
                    best = (sc, det)
            if payload.get("pagination", {}).get("next_page") is None:
                break

        # Agmarknet often lists dairy as Ghee/Butter, not the substring "milk".
        if best is None and "milk" in pn:
            for page in range(1, COMMODITY_MAX_PAGES + 1):
                r = _http_get(f"{AGMARKNET_API}/commodities", params={"limit": 100, "page": page})
                r.raise_for_status()
                payload = r.json()
                rows = payload.get("data") or []
                for row in rows:
                    cn = (row.get("cmdt_name") or "").strip().lower()
                    if cn in ("ghee", "butter"):
                        best = (
                            4,
                            {
                                "commodity_id": row.get("id"),
                                "cmdt_name": row.get("cmdt_name"),
                                "cmdt_group": row.get("cmdt_group"),
                                "comm_code": row.get("comm_code"),
                                "matched_tokens": [],
                                "matched_hints": ["milk_product_proxy"],
                                "note": "Product mentions milk; Agmarknet list uses Ghee/Butter as nearest dairy benchmarks.",
                            },
                        )
                        break
                if best:
                    break
                if payload.get("pagination", {}).get("next_page") is None:
                    break

        if best:
            return {"ok": True, "best_match": best[1], "match_score": best[0]}
        return {"ok": True, "best_match": None, "match_score": 0, "note": "No commodity name overlap in scanned pages."}
    except Exception as e:
        return {"ok": False, "error": str(e), "best_match": None}


def _fetch_data_gov_in_prices() -> dict[str, Any] | None:
    key = os.environ.get("DATA_GOV_IN_API_KEY")
    resource = os.environ.get(
        "DATA_GOV_IN_RESOURCE_ID",
        "",  # user sets when they register at data.gov.in
    )
    if not key or not resource:
        return None
    try:
        r = _http_get(
            f"{DATA_GOV_IN_BASE}/{resource}",
            params={"api-key": key, "format": "json", "limit": 5},
        )
        r.raise_for_status()
        return {"ok": True, "records": r.json()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_local_context(product_name: str, location: str = "Nashik") -> dict[str, Any]:
    """
    Fetch live context: geocoded weather, Google News (FSSAI-related), Agmarknet commodity match,
    optional data.gov.in mandi records (requires DATA_GOV_IN_API_KEY + DATA_GOV_IN_RESOURCE_ID).
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    sources: list[str] = ["open-meteo.com"]

    geo = None
    weather: dict[str, Any] = {
        "error": None,
        "temp_c": None,
        "location": location,
        "risk_note": None,
        "source": "open-meteo.com",
    }
    try:
        geo = _geocode_location(location)
        if geo and geo.get("latitude") is not None:
            label = geo.get("name") or location
            weather = _fetch_weather(float(geo["latitude"]), float(geo["longitude"]), label)
        else:
            weather["error"] = f"Geocoding returned no results for {location!r}"
    except Exception as e:
        weather["error"] = str(e)

    state_hint = (geo or {}).get("admin1")
    news_block = _fetch_fssai_news_rss(location, state_hint)
    if news_block.get("ok"):
        sources.append("news.google.com (RSS)")

    commodity = _fetch_commodity_match(product_name)
    if commodity.get("ok"):
        sources.append("api.agmarknet.gov.in/v1/commodities")

    dg = _fetch_data_gov_in_prices()
    if dg:
        sources.append("api.data.gov.in")

    best = commodity.get("best_match") if commodity.get("ok") else None
    agmarknet_wholesale_price: dict[str, Any] = {
        "source": "agmarknet.gov.in (public API) + optional data.gov.in",
        "commodity_match": best,
        "commodity_lookup_error": commodity.get("error"),
        "note": (
            "Public Agmarknet API exposes commodity metadata (IDs, groups). "
            "Modal/wholesale prices for a mandi usually require authenticated market endpoints "
            "or a data.gov.in dataset (set DATA_GOV_IN_API_KEY and DATA_GOV_IN_RESOURCE_ID)."
        ),
        "arbitrage_gap_percent": None,
        "benchmark_wholesale_inr_per_l": None,
        "data_gov_in": dg,
    }

    return {
        "product_name": product_name,
        "location": location,
        "fetched_at": fetched_at,
        "sources": sources,
        "geocode": geo,
        "agmarknet_wholesale_price": agmarknet_wholesale_price,
        "local_weather": weather,
        "fssai_news_scraper": news_block.get("items") or [],
        "osint_errors": {
            "weather": weather.get("error"),
            "news": None if news_block.get("ok") else news_block.get("error"),
            "commodities": None if commodity.get("ok") else commodity.get("error"),
        },
    }
