"""
OpenLabel — Web Scraper Service
Extracts product data (name, ingredients, nutrition, price, FSSAI) from e-commerce URLs.
Supports BigBasket, Blinkit, Amazon.in, Flipkart, JioMart with a generic fallback.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

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


# ═══════════════════════════════════════════════════════════
#  HTTP Client
# ═══════════════════════════════════════════════════════════

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}


async def _fetch_page(url: str) -> Optional[BeautifulSoup]:
    """Fetch and parse an HTML page."""
    try:
        async with httpx.AsyncClient(
            headers=HEADERS,
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return BeautifulSoup(response.text, "lxml")
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return None


def _extract_fssai(text: str) -> Optional[str]:
    """Extract 14-digit FSSAI number from text."""
    match = re.search(r'\b(\d{14})\b', text)
    return match.group(1) if match else None


def _extract_price(text: str) -> Optional[float]:
    """Extract price from text."""
    match = re.search(r'₹\s*([\d,]+(?:\.\d{2})?)', text)
    if match:
        return float(match.group(1).replace(",", ""))
    return None


# ═══════════════════════════════════════════════════════════
#  Site-Specific Parsers
# ═══════════════════════════════════════════════════════════

def _parse_bigbasket(soup: BeautifulSoup, url: str) -> ScrapedProduct:
    """Parse BigBasket product page."""
    product = ScrapedProduct(source_url=url)
    
    # Product name
    title_tag = soup.find("h1") or soup.find("title")
    if title_tag:
        product.product_name = title_tag.get_text(strip=True)

    # Brand
    brand_tag = soup.find("a", class_=re.compile(r"brand", re.I))
    if brand_tag:
        product.brand = brand_tag.get_text(strip=True)

    # Price
    price_tag = soup.find("td", class_=re.compile(r"price|sp", re.I))
    if price_tag:
        product.price = _extract_price(price_tag.get_text())

    # Gather all text for ingredients and FSSAI
    full_text = soup.get_text(separator="\n", strip=True)
    product.raw_text = full_text

    # Ingredients — look for section
    for heading in soup.find_all(["h2", "h3", "strong", "b"]):
        if "ingredient" in heading.get_text(strip=True).lower():
            next_elem = heading.find_next(["p", "div", "span", "td"])
            if next_elem:
                product.ingredients = next_elem.get_text(strip=True)
                break

    # FSSAI
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

    # Look for ingredient and nutrition sections
    for section in soup.find_all("div"):
        text = section.get_text(strip=True).lower()
        if "ingredient" in text and len(text) < 2000:
            product.ingredients = section.get_text(strip=True)
        if "nutrition" in text and len(text) < 2000:
            product.nutrition_info = section.get_text(strip=True)

    product.fssai_number = _extract_fssai(full_text)
    product.price = _extract_price(full_text)

    return product


def _parse_amazon(soup: BeautifulSoup, url: str) -> ScrapedProduct:
    """Parse Amazon.in product page."""
    product = ScrapedProduct(source_url=url)
    
    # Product title
    title_tag = soup.find("span", id="productTitle")
    if title_tag:
        product.product_name = title_tag.get_text(strip=True)

    # Brand
    brand_tag = soup.find("a", id="bylineInfo")
    if brand_tag:
        product.brand = brand_tag.get_text(strip=True).replace("Visit the ", "").replace(" Store", "")

    # Price
    price_tag = soup.find("span", class_="a-price-whole")
    if price_tag:
        product.price = _extract_price("₹" + price_tag.get_text(strip=True))

    # Feature bullets (often contain ingredient info)
    bullets = soup.find("div", id="feature-bullets")
    if bullets:
        product.description = bullets.get_text(separator="\n", strip=True)

    # Product description
    desc = soup.find("div", id="productDescription")
    if desc:
        desc_text = desc.get_text(strip=True)
        if product.description:
            product.description += "\n" + desc_text
        else:
            product.description = desc_text

    # Important information section (ingredients, allergens)
    important = soup.find("div", id="important-information")
    if important:
        important_text = important.get_text(separator="\n", strip=True)
        if "ingredient" in important_text.lower():
            product.ingredients = important_text

    full_text = soup.get_text(separator="\n", strip=True)
    product.raw_text = full_text
    product.fssai_number = _extract_fssai(full_text)

    return product


def _parse_flipkart(soup: BeautifulSoup, url: str) -> ScrapedProduct:
    """Parse Flipkart product page."""
    product = ScrapedProduct(source_url=url)
    
    title_tag = soup.find("span", class_=re.compile(r"title", re.I)) or soup.find("h1")
    if title_tag:
        product.product_name = title_tag.get_text(strip=True)

    full_text = soup.get_text(separator="\n", strip=True)
    product.raw_text = full_text
    product.fssai_number = _extract_fssai(full_text)
    product.price = _extract_price(full_text)

    return product


def _parse_generic(soup: BeautifulSoup, url: str) -> ScrapedProduct:
    """Generic fallback parser for any product page."""
    product = ScrapedProduct(source_url=url)
    
    title_tag = soup.find("h1") or soup.find("title")
    if title_tag:
        product.product_name = title_tag.get_text(strip=True)

    full_text = soup.get_text(separator="\n", strip=True)
    product.raw_text = full_text
    product.fssai_number = _extract_fssai(full_text)
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
    Automatically selects the best parser based on the domain.
    
    Args:
        url: Product page URL
    
    Returns:
        ScrapedProduct with extracted data
    """
    logger.info(f"Scraping product from: {url}")
    
    soup = await _fetch_page(url)
    if soup is None:
        product = ScrapedProduct(source_url=url)
        product.errors.append("Failed to fetch the product page. The site may be blocking requests.")
        return product

    parser = _get_site_parser(url)
    product = parser(soup, url)

    # Log extraction results
    extracted_fields = sum([
        bool(product.product_name),
        bool(product.ingredients),
        bool(product.nutrition_info),
        bool(product.price),
        bool(product.fssai_number),
    ])
    logger.info(f"Extraction complete — {extracted_fields}/5 fields populated for: {product.product_name}")

    return product
