"""Label OCR: Google Cloud Vision (primary) with Tesseract OCR fallback."""

from __future__ import annotations

import os
import re
from io import BytesIO
from typing import Any


def _normalize_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _combine_sections(front_clean: str, back_clean: str) -> str:
    parts = [
        "=== FRONT LABEL ===",
        front_clean,
        "",
        "=== BACK LABEL (INGREDIENTS / NUTRITION) ===",
        back_clean,
    ]
    return "\n".join(parts).strip()


def _document_text_google(client: Any, image_bytes: bytes) -> str:
    from google.cloud import vision

    image = vision.Image(content=image_bytes)
    response = client.document_text_detection(image=image)
    if response.error.message:
        raise RuntimeError(f"Vision API error: {response.error.message}")

    if response.full_text_annotation and response.full_text_annotation.text:
        return response.full_text_annotation.text

    if response.text_annotations:
        return response.text_annotations[0].description or ""

    return ""


def _extract_google_vision(front_img_bytes: bytes, back_img_bytes: bytes) -> str:
    from google.api_core import exceptions as gcp_exceptions
    from google.cloud import vision

    client = vision.ImageAnnotatorClient()
    try:
        front_text = _document_text_google(client, front_img_bytes)
        back_text = _document_text_google(client, back_img_bytes)
    except gcp_exceptions.GoogleAPICallError as e:
        raise RuntimeError(f"Vision API call failed: {e}") from e

    front_clean = _normalize_text(front_text)
    back_clean = _normalize_text(back_text)
    return _combine_sections(front_clean, back_clean)


def _tesseract_bytes_to_string(image_bytes: bytes) -> str:
    import pytesseract
    from PIL import Image

    cmd = os.environ.get("TESSERACT_CMD")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd

    img = Image.open(BytesIO(image_bytes))
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    return pytesseract.image_to_string(img, lang=os.environ.get("TESSERACT_LANG", "eng"))


def _extract_tesseract(front_img_bytes: bytes, back_img_bytes: bytes) -> str:
    try:
        import pytesseract  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "Tesseract fallback requires: pip install pytesseract Pillow "
            "and a system Tesseract install (see context.txt)."
        ) from e

    front_text = _tesseract_bytes_to_string(front_img_bytes)
    back_text = _tesseract_bytes_to_string(back_img_bytes)
    front_clean = _normalize_text(front_text)
    back_clean = _normalize_text(back_text)
    return _combine_sections(front_clean, back_clean)


def extract_text_from_images(front_img_bytes: bytes, back_img_bytes: bytes) -> str:
    """
    Run OCR on front and back label images and return a single context string.

    Primary: Google Cloud Vision ``document_text_detection``.
    Fallback: local Tesseract via ``pytesseract`` (requires Tesseract binary on PATH
    or ``TESSERACT_CMD`` on Windows).

    Set ``OPENLABEL_SKIP_VISION=1`` to use only Tesseract (no GCP calls).
    """
    skip_vision = os.environ.get("OPENLABEL_SKIP_VISION", "").lower() in (
        "1",
        "true",
        "yes",
    )
    vision_error: BaseException | None = None

    if not skip_vision:
        try:
            return _extract_google_vision(front_img_bytes, back_img_bytes)
        except ImportError as e:
            vision_error = e
        except Exception as e:
            vision_error = e

    try:
        return _extract_tesseract(front_img_bytes, back_img_bytes)
    except Exception as te:
        msg_parts = []
        if vision_error is not None:
            msg_parts.append(f"Google Cloud Vision failed: {vision_error}")
        elif skip_vision:
            msg_parts.append("OPENLABEL_SKIP_VISION is set; Google Vision was skipped.")
        else:
            msg_parts.append("Google Cloud Vision path did not run successfully.")
        msg_parts.append(f"Tesseract OCR fallback failed: {te}")
        raise RuntimeError(" | ".join(msg_parts)) from te
