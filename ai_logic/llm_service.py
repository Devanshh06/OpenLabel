"""Gemini-backed tech-justice analysis with strict Pydantic JSON output."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal
from urllib.parse import quote_plus

import requests
import google.generativeai as genai
from google.api_core import exceptions as google_api_exceptions
from google.generativeai.types import GenerationConfig
from pydantic import BaseModel, ConfigDict, Field

# Many projects get free-tier quota `limit: 0` for gemini-2.0-flash; override with GEMINI_MODEL if needed.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


class FlagItem(BaseModel):
    """Single red-flag finding with evidence tied to label text or OSINT."""

    model_config = ConfigDict(populate_by_name=True)

    code: str = Field(
        description="Stable machine-readable id, e.g. INGREDIENT_SPLITTING, UPF_MARKER, ECONOMIC_ADULTERATION, FAKE_ORGANIC."
    )
    title: str
    severity: Literal["low", "medium", "high"]
    evidence: str = Field(description="Short verbatim quote or OSINT reference supporting the flag.")
    rationale: str = Field(description="Why this matters for an Indian consumer under FSSAI / fair-trade norms.")


class ProductAnalysisResult(BaseModel):
    """Structured output returned to the OpenLabel app."""

    model_config = ConfigDict(populate_by_name=True)

    trust_score: float = Field(
        alias="trustScore",
        ge=0.0,
        le=100.0,
        description="0–100; lower if splitting, UPF, economic adulteration, or fake organic risk is high.",
    )
    overall_verdict: str = Field(
        alias="overallVerdict",
        description="2–4 sentences: balanced expert view for a lay consumer.",
    )
    flags: list[FlagItem]
    legal_draft_available: bool = Field(
        alias="legalDraftAvailable",
        description="True only if a misleading label / unfair practice case warrants a formal complaint draft.",
    )
    legal_draft_text: str | None = Field(
        alias="legalDraftText",
        default=None,
        description="Full Jago Grahak Jago / consumer-forum style complaint when legalDraftAvailable is true.",
    )


# Inline JSON Schema for Gemini structured output. Passing a Pydantic model class or
# model_json_schema() injects $defs / minimum / default etc., which protos.Schema rejects.
GEMINI_PRODUCT_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "trustScore": {"type": "number"},
        "overallVerdict": {"type": "string"},
        "flags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "title": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "evidence": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["code", "title", "severity", "evidence", "rationale"],
            },
        },
        "legalDraftAvailable": {"type": "boolean"},
        "legalDraftText": {"type": "string", "nullable": True},
    },
    "required": [
        "trustScore",
        "overallVerdict",
        "flags",
        "legalDraftAvailable",
        "legalDraftText",
    ],
}


# Gemini JSON schema for ingredient extraction (OFF enrichment precursor).
# Important: keep this schema minimal; Gemini's proto Schema rejects unsupported
# JSON-schema keywords produced by pydantic.model_json_schema().
GEMINI_INGREDIENT_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ingredients": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "originalFragment": {"type": "string"},
                    "normalizedName": {"type": "string"},
                },
                "required": ["originalFragment", "normalizedName"],
            },
        }
    },
    "required": ["ingredients"],
}


class ExtractedIngredient(BaseModel):
    """An ingredient fragment extracted from OCR, normalized for OFF lookups."""

    model_config = ConfigDict(populate_by_name=True)

    original_fragment: str = Field(alias="originalFragment")
    normalized_name: str = Field(alias="normalizedName")


class IngredientExtractionResult(BaseModel):
    """Gemini output contract for normalized ingredient extraction."""

    model_config = ConfigDict(populate_by_name=True)

    ingredients: list[ExtractedIngredient]


OFF_SUGGEST_URL = (
    "https://world.openfoodfacts.org/cgi/suggest.pl?tagtype=ingredients&term={term}&json=1"
)
OFF_TAXONOMY_URL = (
    "https://world.openfoodfacts.org/api/v2/taxonomy?tagtype=ingredients&tags=en:{slug}"
    "&fields=name,description,parents,children,wikidata&include_parents=1&lc=en"
)

_OFF_CACHE: dict[str, dict[str, Any]] = {
    "suggest": {},
    "taxonomy": {},
    "enriched": {},
}


def _off_slugify(term: str) -> str:
    term = (term or "").strip().lower()
    term = re.sub(r"[^a-z0-9]+", "-", term)
    term = re.sub(r"-{2,}", "-", term).strip("-")
    return term


def _off_http_get(url: str) -> Any:
    # OFF is public read-only; keep the payload small and avoid hammering.
    r = requests.get(
        url,
        headers={"User-Agent": "OpenLabel/1.0 (+hackathon)"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def _off_suggest(term: str) -> list[str]:
    key = (term or "").strip().lower()
    if not key:
        return []
    if key in _OFF_CACHE["suggest"]:
        return _OFF_CACHE["suggest"][key]
    url = OFF_SUGGEST_URL.format(term=quote_plus(key))
    suggestions = _off_http_get(url)
    if not isinstance(suggestions, list):
        suggestions = []
    _OFF_CACHE["suggest"][key] = suggestions
    return suggestions


def _off_taxonomy(slug: str) -> dict[str, Any]:
    slug = (slug or "").strip().lower()
    if not slug:
        return {}
    if slug in _OFF_CACHE["taxonomy"]:
        return _OFF_CACHE["taxonomy"][slug]
    url = OFF_TAXONOMY_URL.format(slug=slug)
    payload = _off_http_get(url)
    if not isinstance(payload, dict):
        payload = {}
    _OFF_CACHE["taxonomy"][slug] = payload
    return payload


def _extract_ingredients_with_gemini(
    raw_text: str,
    *,
    model_id: str,
) -> IngredientExtractionResult:
    """Extract normalized ingredient list from OCR using Gemini."""
    system_instruction = (
        "You are an expert food label reader. From the provided OCR label text, extract the ingredient list "
        "including sub-components (e.g., if you see multiple sweeteners, list them separately). "
        "Return a JSON object with `ingredients`, where each item contains:\n"
        "- `originalFragment`: the exact short fragment/ingredient phrase you used from the OCR.\n"
        "- `normalizedName`: a cleaned ingredient name suitable for Open Food Facts ingredient taxonomy lookup. "
        "Use common ingredient synonyms when OCR spelling varies.\n\n"
        "Rules:\n"
        "- Only output the JSON object.\n"
        "- Use a conservative approach: if uncertain, omit that ingredient rather than guessing.\n"
        "- Prefer ingredients that affect splitting, UPF markers, and allergens (e.g., sweeteners, emulsifiers, stabilizers, "
        "milk derivatives, wheat/gluten, nuts)."
    )

    model = genai.GenerativeModel(
        model_id,
        system_instruction=system_instruction,
    )

    generation_config = GenerationConfig(
        temperature=0.2,
        response_mime_type="application/json",
        response_schema=GEMINI_INGREDIENT_EXTRACTION_SCHEMA,
    )

    resp = model.generate_content(
        f"Extract normalized ingredients now from OCR text:\n\n{raw_text}",
        generation_config=generation_config,
    )
    text = _response_text(resp)
    return IngredientExtractionResult.model_validate_json(text)


def _enrich_ingredients_with_off(
    extraction: IngredientExtractionResult,
) -> list[dict[str, Any]]:
    """Call OFF suggest + taxonomy for each normalized ingredient."""
    max_env = os.environ.get("OFF_MAX_INGREDIENTS", "").strip().lower()
    max_n: int | None
    if not max_env or max_env == "all":
        max_n = None
    else:
        try:
            max_n = int(max_env)
        except ValueError:
            max_n = None

    enriched: list[dict[str, Any]] = []
    for item in extraction.ingredients:
        q = (item.normalized_name or "").strip()
        if not q:
            continue
        if max_n is not None and len(enriched) >= max_n:
            break

        cache_key = q.lower()
        if cache_key in _OFF_CACHE["enriched"]:
            enriched.append(_OFF_CACHE["enriched"][cache_key])
            continue

        suggestions = []
        taxonomy_payload: dict[str, Any] = {}
        selected: str | None = None

        try:
            suggestions = _off_suggest(q)
            # Try taxonomy for top suggestions until we find a matching payload key.
            # OFF taxonomy keys are of the form: en:{slug}
            candidate_slugs: list[str] = []
            for s in suggestions[:5]:
                candidate_slugs.append(_off_slugify(s))
            if not candidate_slugs:
                candidate_slugs.append(_off_slugify(q))

            for slug in candidate_slugs[:3]:
                if not slug:
                    continue
                payload = _off_taxonomy(slug)
                if payload and f"en:{slug}" in payload:
                    taxonomy_payload = payload
                    selected = f"en:{slug}"
                    break
                # If taxonomy call succeeded but key missing, keep last payload anyway
                if payload:
                    taxonomy_payload = payload
                    selected = f"en:{slug}"
        except Exception as e:
            enriched_item = {
                "query": q,
                "originalFragment": item.original_fragment,
                "off_suggest": suggestions,
                "off_selected": selected,
                "off_taxonomy": taxonomy_payload,
                "error": str(e),
            }
            _OFF_CACHE["enriched"][cache_key] = enriched_item
            enriched.append(enriched_item)
            continue

        enriched_item = {
            "query": q,
            "originalFragment": item.original_fragment,
            "off_suggest": suggestions,
            "off_selected": selected,
            "off_taxonomy": taxonomy_payload,
        }
        _OFF_CACHE["enriched"][cache_key] = enriched_item
        enriched.append(enriched_item)

    return enriched


MASTER_SYSTEM_PROMPT_TEMPLATE = """You are a senior food scientist and a consumer-rights lawyer working in India. Your job is to protect consumers from adulteration, health-washing, hidden allergens, and misleading packaging.

You will receive:
1) RAW LABEL TEXT from OCR (front and back sections).
2) OSINT CONTEXT (JSON): simulated Agmarknet wholesale pricing, local weather, and FSSAI-style news alerts.

## Mandatory hunt list (actively search; do not skip)

### Ingredient splitting (hiding sugar / carbs / refined grains)
- Multiple added sugar sources listed separately (e.g. sucrose, invert sugar, glucose syrup, maltodextrin, HFCS, fruit juice concentrate used as sweetener).
- Flour blends that obscure refined grain share (maida mixed with multigrain claims).
- Synonyms and "split" ingredients that reduce transparency of total sugar or salt.

### Ultra-processed food (UPF) markers
- Industrial formulations: emulsifiers, stabilisers, thickeners clusters; "nature identical flavouring substances"; reconstituted or reformed products.
- Very long ingredient lists dominated by additives vs. whole foods.

### Economic adulteration
- Compare implied premium vs. OSINT `agmarknet_wholesale_price` / arbitrage signals when category matches (dairy, oils, staples).
- Dilution cues: skimmed milk in "full cream" context, water extenders, cheap oil substitution patterns suggested by label order and category.

### Fake / weak organic or "natural" claims
- "Organic" without credible India Organic / NPOP / certifier cues on label when claim is strong.
- "Natural" used broadly without definitional backing; green imagery without substance.

### OSINT integration
- Use `local_weather` (e.g. 42°C in Nashik) to flag spoilage / thermal denaturation risk for heat-sensitive products (dairy, high-protein drinks).
- Use `fssai_news_scraper` items to elevate severity if the product category or region aligns.

## Legal / consumer protection (India)
- Reference framework conceptually where useful: FSSAI labelling & claims, Consumer Protection Act 2019 (misleading advertisement / unfair trade practices) — do not invent case citations or FIR numbers.

## Output contract
- Respond with ONE JSON object ONLY, matching the schema. No markdown fences, no commentary outside JSON.
- `trustScore`: float 0–100.
- `overallVerdict`: clear, non-alarmist unless evidence is strong.
- `flags`: include every material issue found; use severity honestly.
- `legalDraftAvailable` / `legalDraftText`:
  - If you find a strong misleading-label or unfair-practice case, set `legalDraftAvailable` to true and set `legalDraftText` to a **complete** complaint suitable for **Jago Grahak Jago** / consumer commission style: parties (consumer vs manufacturer/seller), dated facts, **verbatim misleading label quotes**, how it misleads, particulars of loss/inconvenience if any, relief sought (replacement/refund/damages/corrective labelling as appropriate), and statutory basis in plain language.
  - If no actionable violation, set `legalDraftAvailable` to false and `legalDraftText` to null.

## Inputs

### RAW LABEL TEXT
{raw_text}

### OSINT CONTEXT (JSON)
{osint_data}

### OpenFoodFacts ingredient ontology (if provided in OSINT CONTEXT JSON)
- If the key `open_food_facts_ingredients` exists, use it to recognize ingredient synonyms and sub-component identities.
- For ingredient splitting: treat multiple extracted ingredients as potentially the same sweetener/additive class if OFF taxonomy/description indicates they belong together.
- For UPF markers: use OFF ingredient descriptions/parents/children as supporting evidence for emulsifiers, stabilizers, thickeners, and industrial processing signals.
- For hidden allergens: if OFF taxonomy names/descriptions indicate allergenic ingredients (milk derivatives, wheat/gluten, nuts, etc.), treat them as present even if OCR wording is incomplete.
"""


def _gemini_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("Set GEMINI_API_KEY or GOOGLE_API_KEY for Gemini API access.")
    return key


def analyze_product(
    raw_text: str,
    osint_data: dict[str, Any],
    *,
    model_name: str | None = None,
) -> ProductAnalysisResult:
    """
    Run Gemini with JSON schema constrained output and validate with Pydantic.
    """
    genai.configure(api_key=_gemini_api_key())
    model_id = model_name or os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)

    # Enrich osint_data with OFF ingredient ontology (best-effort; failures should not prevent analysis).
    osint_data_enriched = dict(osint_data)
    try:
        extracted = _extract_ingredients_with_gemini(raw_text, model_id=model_id)
        osint_data_enriched["open_food_facts_ingredients"] = _enrich_ingredients_with_off(
            extracted
        )
    except Exception:
        # If ingredient extraction or OFF enrichment fails, proceed with original osint_data.
        pass

    osint_json = json.dumps(osint_data_enriched, ensure_ascii=False, indent=2)
    system_instruction = MASTER_SYSTEM_PROMPT_TEMPLATE.format(
        raw_text=raw_text or "(empty)",
        osint_data=osint_json,
    )

    generation_config = GenerationConfig(
        temperature=0.2,
        response_mime_type="application/json",
        response_schema=GEMINI_PRODUCT_ANALYSIS_SCHEMA,
    )

    model = genai.GenerativeModel(
        model_id,
        system_instruction=system_instruction,
    )

    try:
        response = model.generate_content(
            "Analyze the label and OSINT context. Output the JSON object now.",
            generation_config=generation_config,
        )
    except google_api_exceptions.ResourceExhausted as e:
        raise RuntimeError(
            "Gemini quota exceeded (429). Your project may have no free-tier allowance for the "
            f"selected model ({model_id!r}). Try setting GEMINI_MODEL to another model "
            "(e.g. gemini-2.5-flash-lite, gemini-2.5-flash, gemini-1.5-flash) or enable billing "
            "in Google AI Studio. See https://ai.google.dev/gemini-api/docs/rate-limits"
        ) from e

    text = _response_text(response)
    return ProductAnalysisResult.model_validate_json(text)


def _response_text(response: Any) -> str:
    if getattr(response, "prompt_feedback", None) and response.prompt_feedback.block_reason:
        raise RuntimeError(f"Gemini blocked the prompt: {response.prompt_feedback.block_reason}")
    if not response.candidates:
        raise RuntimeError("Gemini returned no candidates.")
    cand = response.candidates[0]
    parts = cand.content.parts
    if not parts or not getattr(parts[0], "text", None):
        raise RuntimeError("Gemini returned empty content.")
    return parts[0].text
