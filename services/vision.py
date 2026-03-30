"""
OpenLabel — Vision Adapter (Member 3)

Thin wrapper around `ai_logic/vision_service.py` so the API layer can pass
base64 images and receive a single OCR text blob.
"""

from __future__ import annotations

import base64
import logging
from typing import Optional

from ai_logic.vision_service import extract_text_from_images

logger = logging.getLogger(__name__)


def _decode_image_base64(image_base64: str) -> bytes:
    """
    Decode base64 image data from the app.

    Supports either raw base64 or `data:image/...;base64,...` strings.
    """
    if not image_base64:
        raise ValueError("image_base64 is required")
    # Drop any data URL prefix if present.
    if "base64," in image_base64:
        image_base64 = image_base64.split("base64,", 1)[1]
    return base64.b64decode(image_base64)


def extract_text_from_base64_images(
    front_image_base64: str,
    back_image_base64: str,
) -> str:
    """Run OCR for front+back label images and return combined raw text."""
    front_bytes = _decode_image_base64(front_image_base64)
    back_bytes = _decode_image_base64(back_image_base64)
    return extract_text_from_images(front_img_bytes=front_bytes, back_img_bytes=back_bytes)

