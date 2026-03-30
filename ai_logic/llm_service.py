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
    healthier_alternatives: str | None = Field(
        alias="healthierAlternatives",
        default=None,
        description="A short, concise string listing healthier alternative product options.",
    )
    allergy_details: str | None = Field(
        alias="allergyDetails",
        default=None,
        description="A short, concise string explicitly stating if the product can cause any allergy or have allergy effects.",
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
        "healthierAlternatives": {"type": "string", "nullable": True},
        "allergyDetails": {"type": "string", "nullable": True},
    },
    "required": [
        "trustScore",
        "overallVerdict",
        "flags",
        "legalDraftAvailable",
        "legalDraftText",
        "healthierAlternatives",
        "allergyDetails",
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
        "Extract ingredients from OCR text. Return JSON with 'ingredients' array. "
        "Each item needs 'originalFragment' (verbatim) and 'normalizedName' (cleaned for taxonomy). "
        "Output ONLY JSON. Focus on UPF markers, sweeteners, and allergens. Omit if uncertain."
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
                "off_err": str(e),
            }
            _OFF_CACHE["enriched"][cache_key] = enriched_item
            enriched.append(enriched_item)
            continue

        # Prune OFF taxonomy to save LLM tokens
        min_tax = {}
        if taxonomy_payload and selected in taxonomy_payload:
            src = taxonomy_payload[selected]
            min_tax = {
                "name": src.get("name", {}).get("en", ""),
                "parents": src.get("parents", [])
            }

        enriched_item = {
            "query": q,
            "fragment": item.original_fragment,
            "tax": min_tax,
        }
        _OFF_CACHE["enriched"][cache_key] = enriched_item
        enriched.append(enriched_item)

    return enriched


MASTER_SYSTEM_PROMPT_TEMPLATE = """You are an Indian food safety/legal expert. Analyze OCR text + OSINT JSON to protect against deception.
MANDATORY HUNT LIST:
1. Split sugars/grains (e.g., maltodextrin + invert sugar).
2. UPF markers (emulsifiers, long additive lists).
3. Economic adulteration (retail vs wholesale price gaps from OSINT).
4. Fake organic/natural claims.
5. OSINT: heat spoilage risks, FSSAI news recalls.

OUTPUT JSON ONLY matching the schema (No markdown fences):
- trustScore: 0-100 float.
- overallVerdict: 2-3 sentences max.
- flags: Issues found with severity/evidence.
- legalDraftAvailable / legalDraftText: Jago Grahak Jago format complaint IF actionable deception exists; else false/null.
- healthierAlternatives: Ensure it is a VERY SHORT AND CONCISE string of healthier product options to consider instead of this.
- allergyDetails: Ensure it is a VERY SHORT AND CONCISE string stating if the product can cause any allergy or have an allergy effect.
If OFF ontology is in JSON, use it to map ingredient synonyms & UPF groups.

TEXT: {raw_text}
OSINT: {osint_data}"""


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

    # We skip OFF ingredient extraction and enrichment completely to save tokens and time.
    # The main Gemini prompt will analyze UPFs and split ingredients directly from the OCR text.

    osint_json = json.dumps(osint_data, ensure_ascii=False, indent=2)
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
