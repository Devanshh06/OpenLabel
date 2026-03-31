"""
OpenLabel — Web Scraper Service
Extracts product data (name, ingredients, nutrition, price, FSSAI) from e-commerce URLs.
Supports BigBasket, Blinkit, Amazon.in, Flipkart, JioMart with a generic fallback.

Multi-layer scraping strategy:
  Layer 1: httpx async client (fast, async-native)
  Layer 2: requests + mobile user-agent (different TLS fingerprint, simpler HTML)
  Layer 3: Gemini AI URL analysis (when scraping is completely blocked)
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
import requests as sync_requests
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
#  Scraped Data Model
# ═══════════════════════════════════════════════════════════

@dataclass
class ScrapedProduct:
    """Structured data extracted from a product page."""
    product_name: Optional[str] = None
    brand: Optional[str] = None
    ingredients: Optional[str] = None
    nutrition_info: Optional[str] = None
    price: Optional[float] = None
    weight: Optional[str] = None
    fssai_number: Optional[str] = None
    description: Optional[str] = None
    source_url: str = ""
    raw_text: str = ""
    errors: list = field(default_factory=list)

    def to_analysis_text(self) -> str:
        """Format scraped data into text for AI analysis."""
        parts = []
        if self.product_name:
            parts.append(f"Product Name: {self.product_name}")
        if self.brand:
            parts.append(f"Brand: {self.brand}")
        if self.price:
            parts.append(f"Price: ₹{self.price}")
        if self.weight:
            parts.append(f"Weight/Volume: {self.weight}")
        if self.ingredients:
            parts.append(f"Ingredients: {self.ingredients}")
        if self.nutrition_info:
            parts.append(f"Nutrition Information: {self.nutrition_info}")
        if self.fssai_number:
            parts.append(f"FSSAI License No: {self.fssai_number}")
        if self.description:
            parts.append(f"Description: {self.description}")
        if self.raw_text and not any([self.ingredients, self.nutrition_info]):
            parts.append(f"Raw Page Text: {self.raw_text[:3000]}")
        return "\n".join(parts) if parts else self.raw_text[:3000]

    @property
    def has_useful_data(self) -> bool:
        """Check if we extracted anything useful beyond raw text."""
        return bool(self.product_name or self.ingredients or self.nutrition_info)


# ═══════════════════════════════════════════════════════════
#  HTTP Clients — multiple fingerprints
# ═══════════════════════════════════════════════════════════

# Desktop browser headers for httpx
_DESKTOP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7,hi;q=0.6",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1",
}

# Mobile browser headers (Amazon/Flipkart serve simpler pages to mobile)
_MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.google.com/",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
}


async def _fetch_with_httpx(url: str) -> Optional[BeautifulSoup]:
    """Layer 1: httpx async client."""
    try:
        async with httpx.AsyncClient(
            headers=_DESKTOP_HEADERS,
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return BeautifulSoup(response.text, "lxml")
    except Exception as e:
        logger.warning(f"httpx fetch failed for {url}: {e}")
        return None


async def _fetch_with_requests(url: str) -> Optional[BeautifulSoup]:
    """Layer 2: sync requests with mobile UA (different TLS fingerprint)."""
    try:
        session = sync_requests.Session()
        session.headers.update(_MOBILE_HEADERS)
        response = await asyncio.to_thread(
            session.get, url, timeout=15, allow_redirects=True
        )
        response.raise_for_status()
        return BeautifulSoup(response.text, "lxml")
    except Exception as e:
        logger.warning(f"requests fetch failed for {url}: {e}")
        return None


async def _fetch_page(url: str) -> Optional[BeautifulSoup]:
    """Try multiple fetch strategies until one works."""
    # Try httpx first (async native)
    soup = await _fetch_with_httpx(url)
    if soup and _is_valid_page(soup):
        logger.info("Page fetched successfully with httpx")
        return soup

    # Fallback to requests (different TLS fingerprint)
    logger.info("Trying requests fallback for: %s", url)
    soup = await _fetch_with_requests(url)
    if soup and _is_valid_page(soup):
        logger.info("Page fetched successfully with requests (mobile)")
        return soup

    return soup  # Return whatever we got, even if it's a bot page


def _is_valid_page(soup: BeautifulSoup) -> bool:
    """Check if the page is a real product page (not a CAPTCHA/bot page)."""
    title = (soup.find("title") or soup.new_tag("title")).get_text(strip=True).lower()
    text_len = len(soup.get_text(strip=True))

    # Bot detection indicators
    if any(kw in title for kw in ["robot", "captcha", "security check", "sorry", "blocked", "challenge"]):
        return False
    if text_len < 200:
        return False
    return True


# ═══════════════════════════════════════════════════════════
#  Gemini AI Fallback — extract product info from URL
# ═══════════════════════════════════════════════════════════

async def _gemini_url_analysis(url: str) -> ScrapedProduct:
    """
    Layer 3: When scraping is completely blocked, use Gemini AI to extract
    product information based on the URL structure and its training knowledge.
    """
    try:
        import google.generativeai as genai
        from google.generativeai.types import GenerationConfig
        from config import get_settings

        settings = get_settings()
        if not settings.gemini_api_key:
            return ScrapedProduct(source_url=url, errors=["No Gemini API key for fallback"])

        genai.configure(api_key=settings.gemini_api_key)

        model_id = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        model = genai.GenerativeModel(
            model_id,
            system_instruction=(
                "You are a product data extraction assistant. Given an e-commerce product URL, "
                "extract as much product information as you can based on the URL structure (ASIN, "
                "product slug, etc.) and your training knowledge. Focus on food products sold in India."
            ),
        )

        schema = {
            "type": "object",
            "properties": {
                "product_name": {"type": "string"},
                "brand": {"type": "string"},
                "ingredients": {"type": "string"},
                "nutrition_info": {"type": "string"},
                "price_inr": {"type": "number"},
                "weight": {"type": "string"},
                "description": {"type": "string"},
                "is_food_product": {"type": "boolean"},
            },
            "required": ["product_name", "brand", "description", "is_food_product"],
        }

        generation_config = GenerationConfig(
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=schema,
        )

        prompt = (
            f"Extract product information from this e-commerce URL: {url}\n\n"
            "Based on the URL structure and your knowledge, provide:\n"
            "- product_name: Full product name\n"
            "- brand: Brand name\n"
            "- ingredients: Ingredient list if known (for food products)\n"
            "- nutrition_info: Nutritional information if known\n"
            "- price_inr: Approximate price in INR if known\n"
            "- weight: Product weight/volume\n"
            "- description: Brief product description\n"
            "- is_food_product: Whether this is a food/edible product\n\n"
            "If you don't know a field, use empty string or 0. Be accurate, don't fabricate."
        )

        response = await asyncio.to_thread(
            model.generate_content, prompt, generation_config=generation_config
        )

        if not response.candidates:
            return ScrapedProduct(source_url=url, errors=["Gemini returned no candidates"])

        text = response.candidates[0].content.parts[0].text
        data = json.loads(text)

        product = ScrapedProduct(source_url=url)
        product.product_name = data.get("product_name") or None
        product.brand = data.get("brand") or None
        product.ingredients = data.get("ingredients") or None
        product.nutrition_info = data.get("nutrition_info") or None
        product.description = data.get("description") or None
        product.weight = data.get("weight") or None
        price = data.get("price_inr")
        if price and price > 0:
            product.price = float(price)

        # Build raw_text from what we got
        product.raw_text = product.to_analysis_text()

        logger.info("Gemini URL analysis extracted product: %s", product.product_name)
        return product

    except Exception as e:
        logger.error(f"Gemini URL analysis failed: {e}")
        return ScrapedProduct(source_url=url, errors=[f"AI analysis failed: {e}"])


# ═══════════════════════════════════════════════════════════
#  Common extraction helpers
# ═══════════════════════════════════════════════════════════

def _extract_fssai(text: str) -> Optional[str]:
    """Extract 14-digit FSSAI number from text."""
    match = re.search(r'\b(\d{14})\b', text)
    return match.group(1) if match else None


def _extract_price(text: str) -> Optional[float]:
    """Extract price from text (₹ symbol or 'Rs' prefix)."""
    match = re.search(r'₹\s*([\d,]+(?:\.\d{1,2})?)', text)
    if match:
        return float(match.group(1).replace(",", ""))
    match = re.search(r'Rs\.?\s*([\d,]+(?:\.\d{1,2})?)', text)
    if match:
        return float(match.group(1).replace(",", ""))
    return None


def _extract_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Extract all JSON-LD blocks from the page."""
    results: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                results.extend(data)
            elif isinstance(data, dict):
                results.append(data)
        except (json.JSONDecodeError, TypeError):
            continue
    return results


def _product_from_json_ld(ld_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract product fields from JSON-LD Product schema."""
    for item in ld_items:
        item_type = item.get("@type", "")
        types = item_type if isinstance(item_type, list) else [item_type]
        if "Product" not in types:
            continue

        result: dict[str, Any] = {}
        result["product_name"] = item.get("name")
        result["brand"] = (
            item.get("brand", {}).get("name")
            if isinstance(item.get("brand"), dict)
            else item.get("brand")
        )
        result["description"] = item.get("description")
        result["weight"] = item.get("weight") or item.get("size")

        offers = item.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if isinstance(offers, dict):
            price_val = offers.get("price") or offers.get("lowPrice")
            if price_val:
                try:
                    result["price"] = float(str(price_val).replace(",", ""))
                except ValueError:
                    pass

        if item.get("ingredients"):
            result["ingredients"] = item["ingredients"]

        return result
    return {}


def _extract_meta_content(soup: BeautifulSoup, attrs: dict) -> Optional[str]:
    """Safely extract content from a <meta> tag."""
    tag = soup.find("meta", attrs=attrs)
    if tag and isinstance(tag, Tag):
        return tag.get("content", "")  # type: ignore
    return None


def _clean_text(text: str | None) -> str:
    """Collapse whitespace and strip."""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()


def _extract_text_near_heading(soup: BeautifulSoup, keyword: str, max_len: int = 3000) -> Optional[str]:
    """Find a heading containing `keyword` and return the text of its next sibling block."""
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "strong", "b", "th", "dt"]):
        if keyword.lower() in heading.get_text(strip=True).lower():
            for sibling in heading.find_next_siblings(["p", "div", "span", "td", "dd", "ul", "ol"]):
                text = sibling.get_text(separator="\n", strip=True)
                if text and len(text) > 5:
                    return text[:max_len]
            parent = heading.find_parent(["div", "tr", "section"])
            if parent:
                next_block = parent.find_next_sibling(["div", "tr", "section", "p"])
                if next_block:
                    text = next_block.get_text(separator="\n", strip=True)
                    if text and len(text) > 5:
                        return text[:max_len]
    return None


def _extract_table_data(soup: BeautifulSoup) -> dict[str, str]:
    """Extract key-value pairs from HTML tables (product detail tables)."""
    kv: dict[str, str] = {}
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                key = _clean_text(cells[0].get_text())
                val = _clean_text(cells[1].get_text())
                if key and val:
                    kv[key.lower()] = val
    return kv


# ═══════════════════════════════════════════════════════════
#  Site-Specific Parsers
# ═══════════════════════════════════════════════════════════

def _parse_bigbasket(soup: BeautifulSoup, url: str) -> ScrapedProduct:
    """Parse BigBasket product page."""
    product = ScrapedProduct(source_url=url)

    title_tag = soup.find("h1") or soup.find("title")
    if title_tag:
        product.product_name = title_tag.get_text(strip=True)

    brand_tag = soup.find("a", class_=re.compile(r"brand", re.I))
    if brand_tag:
        product.brand = brand_tag.get_text(strip=True)

    price_tag = soup.find("td", class_=re.compile(r"price|sp", re.I))
    if price_tag:
        product.price = _extract_price(price_tag.get_text())

    full_text = soup.get_text(separator="\n", strip=True)
    product.raw_text = full_text

    # JSON-LD
    ld_data = _product_from_json_ld(_extract_json_ld(soup))
    if ld_data.get("product_name") and not product.product_name:
        product.product_name = ld_data["product_name"]
    if ld_data.get("brand") and not product.brand:
        product.brand = ld_data["brand"]
    if ld_data.get("price") and not product.price:
        product.price = ld_data["price"]
    if ld_data.get("ingredients"):
        product.ingredients = ld_data["ingredients"]

    if not product.ingredients:
        product.ingredients = _extract_text_near_heading(soup, "ingredient")
    if not product.nutrition_info:
        product.nutrition_info = _extract_text_near_heading(soup, "nutrition")

    product.fssai_number = _extract_fssai(full_text)
    return product


def _parse_blinkit(soup: BeautifulSoup, url: str) -> ScrapedProduct:
    """Parse Blinkit product page."""
    product = ScrapedProduct(source_url=url)

    title_tag = soup.find("h1") or soup.find("title")
    if title_tag:
        product.product_name = title_tag.get_text(strip=True)

    full_text = soup.get_text(separator="\n", strip=True)
    product.raw_text = full_text

    ld_data = _product_from_json_ld(_extract_json_ld(soup))
    if ld_data.get("product_name") and not product.product_name:
        product.product_name = ld_data["product_name"]
    if ld_data.get("price"):
        product.price = ld_data["price"]
    if ld_data.get("ingredients"):
        product.ingredients = ld_data["ingredients"]

    if not product.ingredients:
        product.ingredients = _extract_text_near_heading(soup, "ingredient")
    if not product.nutrition_info:
        product.nutrition_info = _extract_text_near_heading(soup, "nutrition")

    if not product.ingredients or not product.nutrition_info:
        for section in soup.find_all("div"):
            text = section.get_text(strip=True).lower()
            if not product.ingredients and "ingredient" in text and len(text) < 2000:
                product.ingredients = section.get_text(strip=True)
            if not product.nutrition_info and "nutrition" in text and len(text) < 2000:
                product.nutrition_info = section.get_text(strip=True)

    product.fssai_number = _extract_fssai(full_text)
    if not product.price:
        product.price = _extract_price(full_text)
    return product


def _parse_amazon(soup: BeautifulSoup, url: str) -> ScrapedProduct:
    """
    Parse Amazon.in / Amazon.com product page.
    Strategy (priority order):
      1. JSON-LD structured data
      2. <meta> tags (og:title, og:description, keywords)
      3. Specific DOM IDs (#productTitle, #bylineInfo, etc.)
      4. HTML tables (Product Information section)
      5. Full-page text scan for ingredients/FSSAI
    """
    product = ScrapedProduct(source_url=url)
    full_text = soup.get_text(separator="\n", strip=True)
    product.raw_text = full_text

    # 1. JSON-LD
    ld_data = _product_from_json_ld(_extract_json_ld(soup))
    if ld_data.get("product_name"):
        product.product_name = ld_data["product_name"]
    if ld_data.get("brand"):
        product.brand = ld_data["brand"]
    if ld_data.get("price"):
        product.price = ld_data["price"]
    if ld_data.get("description"):
        product.description = ld_data["description"]
    if ld_data.get("ingredients"):
        product.ingredients = ld_data["ingredients"]

    # 2. Meta tags
    if not product.product_name:
        product.product_name = (
            _extract_meta_content(soup, {"property": "og:title"})
            or _extract_meta_content(soup, {"name": "title"})
        )
    if not product.description:
        product.description = (
            _extract_meta_content(soup, {"property": "og:description"})
            or _extract_meta_content(soup, {"name": "description"})
        )
    meta_keywords = _extract_meta_content(soup, {"name": "keywords"})

    # 3. DOM selectors
    if not product.product_name:
        title_tag = soup.find("span", id="productTitle")
        if title_tag:
            product.product_name = title_tag.get_text(strip=True)

    if not product.brand:
        brand_tag = soup.find("a", id="bylineInfo")
        if brand_tag:
            product.brand = (
                brand_tag.get_text(strip=True)
                .replace("Visit the ", "").replace(" Store", "").replace("Brand: ", "")
            )

    if not product.price:
        price_tag = soup.find("span", class_="a-price-whole")
        if price_tag:
            product.price = _extract_price("₹" + price_tag.get_text(strip=True))

    # Feature bullets & descriptions
    for div_id in ["feature-bullets", "productDescription", "aplus", "aplus_feature_div"]:
        div = soup.find("div", id=div_id)
        if div:
            text = div.get_text(separator="\n", strip=True)[:2000]
            if product.description:
                product.description += "\n" + text
            else:
                product.description = text

    # 4. HTML tables
    table_data = _extract_table_data(soup)
    for key_pattern, attr in [
        ("ingredient", "ingredients"), ("allergen", "ingredients"),
        ("brand", "brand"), ("weight", "weight"),
        ("net quantity", "weight"), ("item weight", "weight"),
    ]:
        for tk, tv in table_data.items():
            if key_pattern in tk:
                current = getattr(product, attr) or ""
                if tv not in current:
                    setattr(product, attr, (current + "\n" + tv).strip() if current else tv)
                break

    # 5. Important Information section
    important = soup.find("div", id="important-information")
    if important and not product.ingredients:
        important_text = important.get_text(separator="\n", strip=True)
        if "ingredient" in important_text.lower():
            product.ingredients = important_text

    # Heading-based fallback
    if not product.ingredients:
        product.ingredients = _extract_text_near_heading(soup, "ingredient")
    if not product.nutrition_info:
        product.nutrition_info = _extract_text_near_heading(soup, "nutrition")

    product.fssai_number = _extract_fssai(full_text)
    if not product.price:
        product.price = _extract_price(full_text)

    # Title fallback from <title> tag
    if not product.product_name:
        title_tag = soup.find("title")
        if title_tag:
            raw_title = title_tag.get_text(strip=True)
            product.product_name = re.sub(
                r'\s*[:\-|]\s*(Amazon\.(in|com)|Online Shopping).*$', '', raw_title, flags=re.I
            ).strip() or raw_title

    # Meta keywords as description enrichment
    if meta_keywords and not product.ingredients:
        if product.description:
            product.description += f"\nKeywords: {meta_keywords}"
        else:
            product.description = f"Keywords: {meta_keywords}"

    return product


def _parse_flipkart(soup: BeautifulSoup, url: str) -> ScrapedProduct:
    """Parse Flipkart product page."""
    product = ScrapedProduct(source_url=url)

    ld_data = _product_from_json_ld(_extract_json_ld(soup))
    if ld_data.get("product_name"):
        product.product_name = ld_data["product_name"]
    if ld_data.get("brand"):
        product.brand = ld_data["brand"]
    if ld_data.get("price"):
        product.price = ld_data["price"]
    if ld_data.get("description"):
        product.description = ld_data["description"]

    if not product.product_name:
        title_tag = soup.find("span", class_=re.compile(r"title", re.I)) or soup.find("h1")
        if title_tag:
            product.product_name = title_tag.get_text(strip=True)

    full_text = soup.get_text(separator="\n", strip=True)
    product.raw_text = full_text

    table_data = _extract_table_data(soup)
    for key_pattern, attr in [
        ("ingredient", "ingredients"), ("brand", "brand"),
        ("weight", "weight"), ("net quantity", "weight"),
    ]:
        for tk, tv in table_data.items():
            if key_pattern in tk and not getattr(product, attr):
                setattr(product, attr, tv)
                break

    if not product.ingredients:
        product.ingredients = _extract_text_near_heading(soup, "ingredient")
    if not product.nutrition_info:
        product.nutrition_info = _extract_text_near_heading(soup, "nutrition")

    product.fssai_number = _extract_fssai(full_text)
    if not product.price:
        product.price = _extract_price(full_text)
    return product


def _parse_generic(soup: BeautifulSoup, url: str) -> ScrapedProduct:
    """Generic fallback parser for any product page."""
    product = ScrapedProduct(source_url=url)

    ld_data = _product_from_json_ld(_extract_json_ld(soup))
    if ld_data.get("product_name"):
        product.product_name = ld_data["product_name"]
    if ld_data.get("brand"):
        product.brand = ld_data["brand"]
    if ld_data.get("price"):
        product.price = ld_data["price"]
    if ld_data.get("description"):
        product.description = ld_data["description"]
    if ld_data.get("ingredients"):
        product.ingredients = ld_data["ingredients"]

    if not product.product_name:
        product.product_name = (
            _extract_meta_content(soup, {"property": "og:title"})
            or _extract_meta_content(soup, {"name": "title"})
        )
    if not product.product_name:
        title_tag = soup.find("h1") or soup.find("title")
        if title_tag:
            product.product_name = title_tag.get_text(strip=True)

    full_text = soup.get_text(separator="\n", strip=True)
    product.raw_text = full_text

    table_data = _extract_table_data(soup)
    for key_pattern, attr in [("ingredient", "ingredients"), ("brand", "brand"), ("weight", "weight")]:
        for tk, tv in table_data.items():
            if key_pattern in tk and not getattr(product, attr):
                setattr(product, attr, tv)
                break

    if not product.ingredients:
        product.ingredients = _extract_text_near_heading(soup, "ingredient")
    if not product.nutrition_info:
        product.nutrition_info = _extract_text_near_heading(soup, "nutrition")

    product.fssai_number = _extract_fssai(full_text)
    if not product.price:
        product.price = _extract_price(full_text)
    return product


# ═══════════════════════════════════════════════════════════
#  Main Scraper Entry Point
# ═══════════════════════════════════════════════════════════

SITE_PARSERS = {
    "bigbasket.com": _parse_bigbasket,
    "blinkit.com": _parse_blinkit,
    "amazon.in": _parse_amazon,
    "amazon.com": _parse_amazon,
    "flipkart.com": _parse_flipkart,
}


def _get_site_parser(url: str):
    """Determine the site-specific parser based on URL domain."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")
    for site_domain, parser in SITE_PARSERS.items():
        if site_domain in domain:
            return parser
    return _parse_generic


async def scrape_product(url: str) -> ScrapedProduct:
    """
    Scrape product data from a given URL.
    
    Multi-layer strategy:
      1. Try httpx (async, fast)
      2. Try requests with mobile UA (different TLS fingerprint)
      3. If both fail or return no data → use Gemini AI to infer product info from URL

    Args:
        url: Product page URL

    Returns:
        ScrapedProduct with extracted data
    """
    logger.info(f"Scraping product from: {url}")

    soup = await _fetch_page(url)

    # If we got a valid page, parse it
    if soup and _is_valid_page(soup):
        parser = _get_site_parser(url)
        product = parser(soup, url)

        # Check if we extracted useful data
        if product.has_useful_data:
            _log_extraction(product)
            return product

        # Page loaded but no useful fields — maybe JS-rendered; try Gemini
        logger.warning(
            "Scraping returned a page but no useful fields for: %s. Falling back to Gemini.",
            url,
        )

    elif soup and not _is_valid_page(soup):
        logger.warning("Bot detection page received for: %s. Falling back to Gemini.", url)
    else:
        logger.warning("Could not fetch page at all for: %s. Falling back to Gemini.", url)

    # Layer 3: Gemini AI fallback
    product = await _gemini_url_analysis(url)
    if product.has_useful_data:
        logger.info("Gemini URL analysis succeeded for: %s", url)
        _log_extraction(product)
        return product

    # Nothing worked — return what we have with an error
    if not product.errors:
        product.errors.append(
            "Could not extract product data. The site may be blocking automated requests. "
            "Try using Image Scan instead."
        )
    _log_extraction(product)
    return product


def _log_extraction(product: ScrapedProduct) -> None:
    """Log extraction results."""
    extracted_fields = sum([
        bool(product.product_name),
        bool(product.ingredients),
        bool(product.nutrition_info),
        bool(product.price),
        bool(product.fssai_number),
    ])
    logger.info(
        f"Extraction complete — {extracted_fields}/5 fields populated for: "
        f"{product.product_name or '(unknown)'}"
    )
