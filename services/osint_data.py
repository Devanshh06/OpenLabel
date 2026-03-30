"""
OpenLabel — OSINT Context Adapter

Returns one merged OSINT `dict` usable by `ai_logic.llm_service.analyze_product()`.

Composition:
- Member 3 live OSINT via `ai_logic.osint_service.get_local_context()`
- Member 2 static wholesale price lookup as a fallback / enrichment
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ai_logic.osint_service import get_local_context as _member3_get_local_context

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
#  Load Wholesale Price Database (Member 2 fallback)
# ═══════════════════════════════════════════════════════════

_DATA_PATH = Path(__file__).parent.parent / "data" / "wholesale_prices.json"
_price_data: dict[str, Any] = {}


def _load_prices() -> None:
    """Load wholesale prices from JSON file."""
    global _price_data
    try:
        with open(_DATA_PATH, "r", encoding="utf-8") as f:
            _price_data = json.load(f)
        logger.info(
            "Loaded %s commodity prices from OSINT database.",
            len(_price_data.get("commodities", [])),
        )
    except FileNotFoundError:
        logger.warning("Wholesale price file not found at %s", _DATA_PATH)
        _price_data = {"commodities": []}
    except json.JSONDecodeError as e:
        logger.error("Failed to parse wholesale prices JSON: %s", e)
        _price_data = {"commodities": []}


def get_wholesale_price(commodity_name: str) -> Optional[dict[str, Any]]:
    """
    Look up the wholesale reference price for a commodity.

    Args:
        commodity_name: Name of the commodity (e.g., "honey", "ghee")
    """
    if not _price_data:
        _load_prices()

    search = (commodity_name or "").lower().strip()
    if not search:
        return None

    for item in _price_data.get("commodities", []):
        names = [str(item.get("name", "")).lower()]
        names.extend([str(a).lower() for a in item.get("aliases", [])])
        if any(search in name or name in search for name in names):
            return item

    return None


def _get_static_wholesale_context(product_name: str) -> dict[str, Any]:
    """Best-effort static enrichment from `data/wholesale_prices.json`."""
    item = get_wholesale_price(product_name)
    return {
        "static_wholesale_found": item is not None,
        "static_wholesale_reference": item,
        "static_benchmark_wholesale_inr_per_kg": (
            item.get("wholesale_price_per_kg") if item else None
        ),
        "static_min_retail_expected_inr_per_kg": (
            item.get("min_retail_per_kg") if item else None
        ),
        "static_source": "data/wholesale_prices.json (Member 2 OSINT fallback)",
    }


def get_live_osint_context(product_name: str, *, location: str = "Nashik") -> dict[str, Any]:
    """Fetch live context using Member 3 OSINT. Never raise; always return a dict."""
    try:
        return _member3_get_local_context(product_name=product_name, location=location)
    except Exception as e:
        logger.error("Live OSINT failed: %s", e)
        fetched_at = datetime.now(timezone.utc).isoformat()
        return {
            "product_name": product_name,
            "location": location,
            "fetched_at": fetched_at,
            "sources": [],
            "geocode": None,
            "local_weather": {"error": str(e), "temp_c": None, "location": location, "source": "open-meteo.com"},
            "fssai_news_scraper": [],
            "osint_errors": {"live_osint": str(e)},
            "agmarknet_wholesale_price": {
                "source": "agmarknet.gov.in (public API)",
                "commodity_match": None,
                "commodity_lookup_error": str(e),
                "note": "Live OSINT failed; static wholesale fallback used instead.",
                "arbitrage_gap_percent": None,
                "benchmark_wholesale_inr_per_l": None,
                "data_gov_in": None,
            },
        }


def get_osint_context(
    product_name: Optional[str] = None,
    *,
    retail_price: Optional[float] = None,
    location: str = "Nashik",
) -> dict[str, Any]:
    """
    Build one merged OSINT dict for `ai_logic.llm_service.analyze_product()`.

    Includes:
    - live weather/news/commodity matching
    - static wholesale benchmark enrichment
    - the app-provided `retail_price` (best-effort, interpreted by the model)
    """
    pn = (product_name or "").strip()
    pn = pn if pn else "food"

    osint = get_live_osint_context(pn, location=location)
    static_ctx = _get_static_wholesale_context(pn)

    # Normalize expected structure for the Member 3 master prompt.
    ag = osint.get("agmarknet_wholesale_price") or {}
    ag["static_wholesale_reference"] = static_ctx.get("static_wholesale_reference")
    ag["static_benchmark_wholesale_inr_per_kg"] = static_ctx.get(
        "static_benchmark_wholesale_inr_per_kg"
    )
    ag["static_min_retail_expected_inr_per_kg"] = static_ctx.get(
        "static_min_retail_expected_inr_per_kg"
    )
    ag["static_source"] = static_ctx.get("static_source")
    osint["agmarknet_wholesale_price"] = ag

    osint["retail_price_inr"] = retail_price
    osint["retail_price_provided"] = retail_price is not None

    return osint


def get_all_commodities() -> list[dict[str, Any]]:
    """Return all commodity data for reference."""
    if not _price_data:
        _load_prices()
    return _price_data.get("commodities", [])
