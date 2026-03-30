"""
OpenLabel — AI Engine Service (Member 2 adapter)

This file keeps *Member 3* reasoning untouched by delegating all analysis to:
- `ai_logic.llm_service.analyze_product()` -> returns `ProductAnalysisResult`

We implement only glue:
- Single-image scans: Gemini Vision extracts raw label text; then Member 3 analyzes.
- Text scans: directly call Member 3 with OCR/scraped text + OSINT dict.
- Dual-image scans: routers call Member 3 OCR adapter separately, then analyze.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Any, Optional, Tuple

import google.generativeai as genai
from google.generativeai.types import GenerationConfig

from config import get_settings
from ai_logic.llm_service import (
    DEFAULT_GEMINI_MODEL,
    ProductAnalysisResult,
    analyze_product as _member3_analyze_product,
)

logger = logging.getLogger(__name__)


def trust_level_from_score(trust_score: float) -> str:
    """Map 0–100 to RED/YELLOW/GREEN."""
    if trust_score <= 40:
        return "RED"
    if trust_score <= 70:
        return "YELLOW"
    return "GREEN"


def _decode_image_base64(image_base64: str) -> bytes:
    """Decode base64 image bytes (supports data URLs)."""
    if not image_base64:
        raise ValueError("image_base64 is required")
    if "base64," in image_base64:
        image_base64 = image_base64.split("base64,", 1)[1]
    return base64.b64decode(image_base64)


def _infer_image_mime_type(image_base64: str, *, default: str = "image/jpeg") -> str:
    """
    Infer full Gemini MIME type from a data URL.

    Gemini expects values like `image/png` (not `png`).
    """
    if not image_base64:
        return default
    if image_base64.startswith("data:image/") and ";base64," in image_base64:
        ext = image_base64.split("data:image/", 1)[1].split(";base64,", 1)[0].strip().lower()
        if ext in {"jpg", "jpeg"}:
            return "image/jpeg"
        if ext in {"png", "webp", "gif", "bmp", "tiff", "tif"}:
            return f"image/{ext}"
        # Best-effort: if ext already looks like a full MIME type, return it.
        if "/" in ext:
            return ext
        return default
    return default


def _infer_image_mime_type_from_bytes(image_bytes: bytes, *, default: str) -> str:
    """
    Infer MIME type from binary signatures.

    Helps when the client sends raw base64 without a `data:image/...` prefix.
    """
    if not image_bytes:
        return default

    # PNG signature: 89 50 4E 47 0D 0A 1A 0A
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    # JPEG signature: FF D8 FF
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    # WebP signature: RIFF....WEBP
    if image_bytes.startswith(b"RIFF") and len(image_bytes) >= 12 and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    # GIF signatures
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "image/gif"

    return default


def _gemini_api_key() -> str:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY must be set in environment.")
    return settings.gemini_api_key


def _ensure_member3_env() -> None:
    """Member 3 `ai_logic` reads API keys from `os.environ`."""
    settings = get_settings()
    os.environ.setdefault("GEMINI_API_KEY", settings.gemini_api_key)
    # Optional override: ai_logic reads GEMINI_MODEL directly from env var.
    if settings.gemini_model and not os.environ.get("GEMINI_MODEL"):
        os.environ["GEMINI_MODEL"] = settings.gemini_model


def _response_text(response: Any) -> str:
    # Keep logic aligned with `ai_logic.llm_service._response_text`.
    if getattr(response, "prompt_feedback", None) and response.prompt_feedback.block_reason:
        raise RuntimeError(f"Gemini blocked the prompt: {response.prompt_feedback.block_reason}")
    if not getattr(response, "candidates", None):
        raise RuntimeError("Gemini returned no candidates.")
    cand = response.candidates[0]
    parts = cand.content.parts
    if not parts or not getattr(parts[0], "text", None):
        raise RuntimeError("Gemini returned empty content.")
    return parts[0].text


def _extract_raw_text_from_image_bytes(
    image_bytes: bytes,
    *,
    model_id: str,
    mime_type: str = "image/jpeg",
) -> str:
    """Use Gemini Vision to extract all readable label text."""
    genai.configure(api_key=_gemini_api_key())

    model = genai.GenerativeModel(
        model_id,
        system_instruction=(
            "You are an expert food label OCR assistant for India. "
            "Extract all visible text from the provided label image. "
            "Return ONLY the raw text (no analysis, no JSON, no markdown fences). "
            "Preserve line breaks as much as possible."
        ),
    )

    prompt = "Extract all visible label text from this image."
    contents = [
        prompt,
        {"inline_data": {"mime_type": mime_type, "data": image_bytes}},
    ]

    generation_config = GenerationConfig(temperature=0.2)
    resp = model.generate_content(contents, generation_config=generation_config)
    return _response_text(resp)


async def analyze_image(
    image_base64: str,
    *,
    product_name: Optional[str] = None,
    retail_price: Optional[float] = None,
    location: str = "Nashik",
) -> Tuple[ProductAnalysisResult, str]:
    """
    Analyze a single-image scan.

    Returns:
      (ProductAnalysisResult, raw_text_extracted)
    """
    from services.osint_data import get_osint_context
    model_id = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    _ensure_member3_env()

    image_bytes = _decode_image_base64(image_base64)
    mime_type = _infer_image_mime_type(image_base64, default="image/jpeg")
    mime_type = _infer_image_mime_type_from_bytes(image_bytes, default=mime_type)

    ocr_task = asyncio.to_thread(
        _extract_raw_text_from_image_bytes,
        image_bytes,
        model_id=model_id,
        mime_type=mime_type,
    )
    
    osint_task = get_osint_context(
        product_name=product_name,
        retail_price=retail_price,
        location=location
    )

    raw_text_extracted, osint_data = await asyncio.gather(ocr_task, osint_task)

    report: ProductAnalysisResult = await asyncio.to_thread(
        _member3_analyze_product,
        raw_text_extracted,
        osint_data,
        model_name=model_id,
    )
    return report, raw_text_extracted


async def analyze_text(
    extracted_text: str,
    *,
    product_name: Optional[str] = None,
    retail_price: Optional[float] = None,
    location: str = "Nashik",
) -> Tuple[ProductAnalysisResult, str]:
    """Analyze scraped/OCR text via Member 3."""
    from services.osint_data import get_osint_context
    _ensure_member3_env()
    
    osint_data = await get_osint_context(
        product_name=product_name,
        retail_price=retail_price,
        location=location
    )

    model_id = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    report: ProductAnalysisResult = await asyncio.to_thread(
        _member3_analyze_product,
        extracted_text,
        osint_data,
        model_name=model_id,
    )
    return report, extracted_text


async def analyze_dual_images(
    *,
    front_image_base64: str,
    back_image_base64: str,
    product_name: Optional[str] = None,
    retail_price: Optional[float] = None,
    location: str = "Nashik",
) -> Tuple[ProductAnalysisResult, str]:
    """
    Dual-image analysis using Gemini multi-modal OCR concurrently with OSINT.
    """
    from services.osint_data import get_osint_context
    _ensure_member3_env()

    front_bytes = _decode_image_base64(front_image_base64)
    back_bytes = _decode_image_base64(back_image_base64)
    front_mime = _infer_image_mime_type(front_image_base64, default="image/jpeg")
    back_mime = _infer_image_mime_type(back_image_base64, default="image/jpeg")
    front_mime = _infer_image_mime_type_from_bytes(front_bytes, default=front_mime)
    back_mime = _infer_image_mime_type_from_bytes(back_bytes, default=back_mime)

    model_id = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    
    def _extract_both(f_bytes, f_mime, b_bytes, b_mime):
        genai.configure(api_key=_gemini_api_key())
        model = genai.GenerativeModel(
            model_id,
            system_instruction=(
                "You are an expert food label OCR assistant for India. "
                "Extract all visible text from the provided front and back label images. "
                "Clearly section your output with '=== FRONT LABEL ===' and '=== BACK LABEL (INGREDIENTS / NUTRITION) ==='. "
                "Return ONLY the raw text (no analysis, no JSON, no markdown fences). "
                "Preserve line breaks as much as possible."
            ),
        )
        prompt = "Extract all visible label text from these images. The first image is the front, the second is the back."
        contents = [
            prompt,
            {"inline_data": {"mime_type": f_mime, "data": f_bytes}},
            {"inline_data": {"mime_type": b_mime, "data": b_bytes}},
        ]
        generation_config = GenerationConfig(temperature=0.2)
        resp = model.generate_content(contents, generation_config=generation_config)
        return _response_text(resp)

    ocr_task = asyncio.to_thread(
        _extract_both, front_bytes, front_mime, back_bytes, back_mime
    )
    
    osint_task = get_osint_context(
        product_name=product_name,
        retail_price=retail_price,
        location=location
    )

    raw_text_extracted, osint_data = await asyncio.gather(ocr_task, osint_task)

    report: ProductAnalysisResult = await asyncio.to_thread(
        _member3_analyze_product,
        raw_text_extracted,
        osint_data,
        model_name=model_id,
    )
    return report, raw_text_extracted
