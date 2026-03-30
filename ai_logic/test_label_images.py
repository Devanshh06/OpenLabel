"""
End-to-end test using real label images: front.png + back.png in this folder.

  python test_label_images.py                 Vision + live OSINT + Gemini (needs API keys)
  python test_label_images.py --skip-llm      Vision + OSINT only (no Gemini)
  python test_label_images.py --vision-only   OCR only (no OSINT / LLM)

Requires Google Cloud credentials for Vision (ADC or GOOGLE_APPLICATION_CREDENTIALS).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from llm_service import analyze_product
from osint_service import get_local_context
from vision_service import extract_text_from_images


def _read_image(path: Path) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing image: {path.resolve()}\n"
            f"Place front.png and back.png next to this script (or pass --front / --back)."
        )
    return path.read_bytes()


def main() -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Test OpenLabel with front.png and back.png")
    parser.add_argument("--front", type=Path, default=root / "front.png", help="Front label image path")
    parser.add_argument("--back", type=Path, default=root / "back.png", help="Back label image path")
    parser.add_argument(
        "--product-name",
        default="Scanned product",
        help="Product name passed to OSINT (e.g. brand from packaging)",
    )
    parser.add_argument("--location", default="Nashik", help="City/region for OSINT")
    parser.add_argument(
        "--vision-only",
        action="store_true",
        help="Run Google Vision OCR only; print raw_text and exit.",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Run Vision + OSINT; do not call Gemini.",
    )
    args = parser.parse_args()

    try:
        front_bytes = _read_image(args.front)
        back_bytes = _read_image(args.back)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1

    print("=== OpenLabel image test ===")
    print(f"Front: {args.front.resolve()}")
    print(f"Back:  {args.back.resolve()}")
    print()

    print("--- Vision (document_text_detection) ---")
    try:
        raw_text = extract_text_from_images(front_bytes, back_bytes)
    except Exception as e:
        print(f"Vision failed: {e}", file=sys.stderr)
        return 1

    print(raw_text[:2000] + ("..." if len(raw_text) > 2000 else ""))
    print(f"\n[OCR length: {len(raw_text)} characters]\n")

    if args.vision_only:
        return 0

    print("--- OSINT (live) ---")
    osint = get_local_context(args.product_name, args.location)
    print(json.dumps(osint, indent=2, ensure_ascii=False)[:4000])
    if len(json.dumps(osint)) > 4000:
        print("...")
    print()

    if args.skip_llm:
        print("Skipped LLM (--skip-llm).")
        return 0

    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        print(
            "Missing GEMINI_API_KEY or GOOGLE_API_KEY. Re-run with --skip-llm or set an API key.",
            file=sys.stderr,
        )
        return 1

    print("--- Gemini (analyze_product) ---")
    try:
        result = analyze_product(raw_text, osint)
    except Exception as e:
        print(f"LLM failed: {e}", file=sys.stderr)
        return 1

    print(result.model_dump_json(by_alias=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
