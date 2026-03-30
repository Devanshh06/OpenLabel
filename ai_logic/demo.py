"""
OpenLabel demo: synthetic OCR label text + live OSINT + Gemini analysis.

Usage:
  python demo.py              # live Gemini (needs GEMINI_API_KEY or GOOGLE_API_KEY)
  python demo.py --mock       # no Gemini: validate Pydantic + print canned JSON result (OSINT still live)

Optional env:
  GEMINI_MODEL   default is gemini-2.5-flash (override if your free tier has no quota on a model)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from llm_service import ProductAnalysisResult, analyze_product
from osint_service import get_local_context

# Simulates combined front/back Vision output for a deliberately "dirty" demo product.
DEMO_RAW_TEXT = """
=== FRONT LABEL ===
FarmFresh
100% NATURAL ORGANIC TASTE
Premium Full Cream Milk Drink
Rich in Calcium | No Added Preservatives*
*Except those naturally occurring in ingredients

=== BACK LABEL (INGREDIENTS / NUTRITION) ===
INGREDIENTS: Water, Milk Solids (12%), Sugar, Maltodextrin, Glucose Syrup,
Invert Sugar, Emulsifier (INS 471), Stabilizer (INS 407), Nature Identical
Flavouring Substances (Vanilla), Salt, Colours (INS 102).

Contains milk. May contain traces of nuts.

NUTRITION INFORMATION (per 100 ml): Energy 78 kcal, Protein 2.1 g, Carbohydrate 14 g
(of which Sugars 8 g), Fat 1.8 g.

Best before: See cap. Store refrigerated below 8°C. Manufactured by Demo Foods Pvt Ltd, Nashik.
"""

MOCK_GEMINI_JSON = """
{
  "trustScore": 28.0,
  "overallVerdict": "The label mixes 'natural/organic taste' style claims with multiple added sugars and industrial additives typical of ultra-processed beverages; dairy solids are modest versus sugars and bulking carbs. OSINT heat stress in Nashik adds spoilage sensitivity for real dairy SKUs.",
  "flags": [
    {
      "code": "INGREDIENT_SPLITTING",
      "title": "Multiple added sugars listed separately",
      "severity": "high",
      "evidence": "Sugar, Maltodextrin, Glucose Syrup, Invert Sugar",
      "rationale": "Splitting sweeteners can obscure total added sugar burden versus a single 'sugar' line item."
    },
    {
      "code": "UPF_MARKER",
      "title": "Ultra-processed formulation cluster",
      "severity": "medium",
      "evidence": "Emulsifier (INS 471), Stabilizer (INS 407), Nature Identical Flavouring Substances",
      "rationale": "Industrial additive stack plus reconstitution-style milk solids fits UPF patterns."
    },
    {
      "code": "FAKE_ORGANIC",
      "title": "Organic/natural claim without certification cues",
      "severity": "high",
      "evidence": "100% NATURAL ORGANIC TASTE",
      "rationale": "Strong organic/natural impression on front without India Organic/NPOP traceability on the visible panel."
    },
    {
      "code": "WEATHER_SPOILAGE",
      "title": "Heat-wave spoilage risk (OSINT)",
      "severity": "medium",
      "evidence": "local_weather temp_c 42, dairy beverage category",
      "rationale": "Elevated ambient heat increases thermal abuse risk for milk-based drinks in last-mile distribution."
    }
  ],
  "legalDraftAvailable": true,
  "legalDraftText": "BEFORE THE DISTRICT CONSUMER DISPUTES REDRESSAL COMMISSION / FORUM, NASHIK. Complaint under the Consumer Protection Act, 2019. Complainant: A concerned consumer, Nashik. Opposite Party: Demo Foods Pvt Ltd, Nashik. Facts: Purchased 'FarmFresh Premium Full Cream Milk Drink' relying on front panel text '100% NATURAL ORGANIC TASTE' and 'Premium Full Cream Milk Drink'. The back label lists multiple added sugars (Sugar, Maltodextrin, Glucose Syrup, Invert Sugar) and industrial additives, and milk solids are only 12%, undermining the premium full-cream impression. Prayer: direction for corrective labelling, refund/compensation as may be awarded, and costs. (Demo draft for Jago Grahak Jago style awareness.)"
}
"""


def run_mock() -> ProductAnalysisResult:
    """Validate structured output path without calling Gemini."""
    return ProductAnalysisResult.model_validate_json(MOCK_GEMINI_JSON)


def run_live() -> ProductAnalysisResult:
    osint = get_local_context("FarmFresh Premium Full Cream Milk Drink", "Nashik")
    return analyze_product(DEMO_RAW_TEXT.strip(), osint)


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenLabel demo on synthetic label + OSINT.")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Skip Gemini; parse canned JSON into ProductAnalysisResult.",
    )
    args = parser.parse_args()

    if args.mock:
        result = run_mock()
        source = "mock (no API)"
    else:
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            print(
                "Missing GEMINI_API_KEY or GOOGLE_API_KEY. Use --mock or set an API key.",
                file=sys.stderr,
            )
            return 1
        result = run_live()
        source = "live Gemini"

    print("=== OpenLabel demo ===")
    print(f"Mode: {source}")
    print()
    print("--- Demo OCR excerpt (first 400 chars) ---")
    print(DEMO_RAW_TEXT.strip()[:400] + ("..." if len(DEMO_RAW_TEXT.strip()) > 400 else ""))
    print()
    print("--- OSINT (summary) ---")
    ctx = get_local_context("FarmFresh Premium Full Cream Milk Drink", "Nashik")
    ctx_dump = json.dumps(ctx, indent=2, ensure_ascii=False)
    print(ctx_dump[:1200])
    if len(ctx_dump) > 1200:
        print("...")
    print()
    print("--- ProductAnalysisResult (JSON) ---")
    print(result.model_dump_json(by_alias=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
