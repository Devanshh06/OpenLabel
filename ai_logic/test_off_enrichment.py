"""
Smoke-test OFF enrichment without requiring Gemini or OCR.

Runs only the OFF calls:
  - ingredient suggest (cgi/suggest.pl)
  - ingredient taxonomy (api/v2/taxonomy)

This script should never crash the pipeline; each ingredient enrichment is
best-effort and returns an `error` field when OFF is unreachable.
"""

from __future__ import annotations

import os

from llm_service import ExtractedIngredient, IngredientExtractionResult, _enrich_ingredients_with_off


def main() -> int:
    extraction = IngredientExtractionResult(
        ingredients=[
            ExtractedIngredient(originalFragment="maltodextrin", normalizedName="maltodextrin"),
            ExtractedIngredient(originalFragment="ins 471", normalizedName="emulsifier"),
            ExtractedIngredient(originalFragment="milk solids", normalizedName="milk"),
        ]
    )

    # Test max cap
    os.environ["OFF_MAX_INGREDIENTS"] = "1"
    enriched_1 = _enrich_ingredients_with_off(extraction)
    assert isinstance(enriched_1, list)
    assert len(enriched_1) <= 1
    assert "query" in enriched_1[0]

    # Test "all" mode (should not crash even if OFF is unreachable)
    os.environ["OFF_MAX_INGREDIENTS"] = "all"
    enriched_all = _enrich_ingredients_with_off(extraction)
    assert isinstance(enriched_all, list)
    assert 0 < len(enriched_all) <= len(extraction.ingredients)
    for item in enriched_all:
        assert "query" in item
        assert "off_suggest" in item

    print("OFF enrichment smoke test: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

