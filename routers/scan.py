"""
OpenLabel — Scan Router

Endpoints for analyzing food products via image upload, dual-image OCR,
or URL scraping.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from auth import get_optional_user
from database import get_supabase_admin
from models.schemas import FlagItem, ScanDualImageRequest, ScanImageRequest, ScanLinkRequest, ScanResponse
from services.ai_engine import analyze_dual_images, analyze_image, analyze_text, trust_level_from_score
from services.fssai_validator import validate_fssai_number
from services.osint_data import get_osint_context
from services.scraper import scrape_product

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scan", tags=["🔍 Scan & Analyze"])


_FSSAI_14_DIGIT_RE = re.compile(r"\b(\d{14})\b")


def _extract_candidate_fssai_14(raw_text: str) -> Optional[str]:
    if not raw_text:
        return None
    matches = _FSSAI_14_DIGIT_RE.findall(raw_text)
    if not matches:
        return None
    # Prefer the first that actually validates, but keep fallback to first match.
    for m in matches:
        res = validate_fssai_number(m)
        if res.is_valid:
            return m
    return matches[0]


def _convert_member3_flags(member3_flags) -> list[FlagItem]:
    flags: list[FlagItem] = []
    for f in member3_flags or []:
        flags.append(
            FlagItem(
                code=str(getattr(f, "code", "")),
                title=str(getattr(f, "title", "")),
                severity=getattr(f, "severity", "medium"),
                evidence=str(getattr(f, "evidence", "")),
                rationale=str(getattr(f, "rationale", "")),
            )
        )
    return flags


def _maybe_add_fssai_invalid_flag(flags: list[FlagItem], fssai_candidate: Optional[str]) -> Optional[str]:
    if not fssai_candidate:
        return None
    fssai_result = validate_fssai_number(fssai_candidate)
    if fssai_result.is_valid:
        return fssai_candidate

    flags.append(
        FlagItem(
            code="FSSAI_INVALID",
            title="FSSAI Number Invalid",
            severity="medium",
            evidence=f"FSSAI License No: {fssai_candidate}",
            rationale="The detected FSSAI number failed structural validation (14-digit format, type/state/year consistency).",
        )
    )
    return fssai_candidate


def _store_scan_in_supabase(
    *,
    scan_id: str,
    user: Optional[dict],
    product_name: Optional[str],
    input_source: str,
    raw_text_extracted: Optional[str],
    trust_score: float,
    trust_level: str,
    full_report: dict,
    fssai_number: Optional[str],
) -> None:
    try:
        supabase = get_supabase_admin()
        supabase.table("scans").insert(
            {
                "id": scan_id,
                "user_id": str(user.id) if user else None,
                "product_name": product_name,
                "input_source": input_source,
                "raw_text_extracted": raw_text_extracted,
                "trust_score": trust_score,
                "trust_level": trust_level,
                "full_report": full_report,
                "fssai_number": fssai_number,
            }
        ).execute()
        logger.info("Scan stored in DB: %s", scan_id)
    except Exception as e:
        logger.error("Failed to store scan in DB: %s", e)


@router.post(
    "/image",
    response_model=ScanResponse,
    summary="Analyze a food label image",
)
async def scan_image(
    request: ScanImageRequest,
    user: Optional[dict] = Depends(get_optional_user),
):
    logger.info("Image scan initiated | User: %s", user.id if user else "anonymous")

    osint_context = get_osint_context(
        request.product_name,
        retail_price=request.retail_price,
    )

    try:
        report, raw_text_extracted = await analyze_image(
            image_base64=request.image_base64,
            product_name=request.product_name,
            retail_price=request.retail_price,
            osint_context=osint_context,
        )
    except Exception as e:
        logger.exception("scan_image failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"scan_image failed: {type(e).__name__}: {e}",
        )

    product_name = request.product_name
    fssai_candidate = _extract_candidate_fssai_14(raw_text_extracted)
    flags = _convert_member3_flags(report.flags)
    fssai_number = _maybe_add_fssai_invalid_flag(flags, fssai_candidate)
    trust_level = trust_level_from_score(report.trust_score)

    scan_id = str(uuid.uuid4())
    full_report = {
        "trustScore": report.trust_score,
        "overallVerdict": report.overall_verdict,
        "productName": product_name,
        "flags": [f.model_dump() for f in flags],
        "upfScore": None,
        "fssaiNumber": fssai_number,
        "legalDraftAvailable": report.legal_draft_available,
        "legalDraftText": report.legal_draft_text,
    }
    _store_scan_in_supabase(
        scan_id=scan_id,
        user=user,
        product_name=product_name,
        input_source="image",
        raw_text_extracted=raw_text_extracted[:5000] if raw_text_extracted else None,
        trust_score=report.trust_score,
        trust_level=trust_level,
        full_report=full_report,
        fssai_number=fssai_number,
    )

    return ScanResponse(
        scan_id=scan_id,
        product_name=product_name,
        trust_score=report.trust_score,
        trust_level=trust_level,
        overall_verdict=report.overall_verdict,
        flags=flags,
        upf_score=None,
        fssai_number=fssai_number,
        legal_draft_available=report.legal_draft_available,
        legal_draft_text=report.legal_draft_text,
    )


@router.post(
    "/dual-image",
    response_model=ScanResponse,
    summary="Analyze front+back label images",
)
async def scan_dual_image(
    request: ScanDualImageRequest,
    user: Optional[dict] = Depends(get_optional_user),
):
    logger.info("Dual-image scan initiated | User: %s", user.id if user else "anonymous")

    osint_context = get_osint_context(
        request.product_name,
        retail_price=request.retail_price,
    )

    try:
        report, raw_text_extracted = await analyze_dual_images(
            front_image_base64=request.front_image_base64,
            back_image_base64=request.back_image_base64,
            product_name=request.product_name,
            retail_price=request.retail_price,
            osint_context=osint_context,
        )
    except Exception as e:
        logger.exception("scan_dual_image failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"scan_dual_image failed: {type(e).__name__}: {e}",
        )

    product_name = request.product_name
    fssai_candidate = _extract_candidate_fssai_14(raw_text_extracted)
    flags = _convert_member3_flags(report.flags)
    fssai_number = _maybe_add_fssai_invalid_flag(flags, fssai_candidate)
    trust_level = trust_level_from_score(report.trust_score)

    scan_id = str(uuid.uuid4())
    full_report = {
        "trustScore": report.trust_score,
        "overallVerdict": report.overall_verdict,
        "productName": product_name,
        "flags": [f.model_dump() for f in flags],
        "upfScore": None,
        "fssaiNumber": fssai_number,
        "legalDraftAvailable": report.legal_draft_available,
        "legalDraftText": report.legal_draft_text,
    }
    _store_scan_in_supabase(
        scan_id=scan_id,
        user=user,
        product_name=product_name,
        input_source="dual-image",
        raw_text_extracted=raw_text_extracted[:5000] if raw_text_extracted else None,
        trust_score=report.trust_score,
        trust_level=trust_level,
        full_report=full_report,
        fssai_number=fssai_number,
    )

    return ScanResponse(
        scan_id=scan_id,
        product_name=product_name,
        trust_score=report.trust_score,
        trust_level=trust_level,
        overall_verdict=report.overall_verdict,
        flags=flags,
        upf_score=None,
        fssai_number=fssai_number,
        legal_draft_available=report.legal_draft_available,
        legal_draft_text=report.legal_draft_text,
    )


@router.post(
    "/link",
    response_model=ScanResponse,
    summary="Analyze a product from its e-commerce URL",
)
async def scan_link(
    request: ScanLinkRequest,
    user: Optional[dict] = Depends(get_optional_user),
):
    logger.info(
        "Link scan initiated | URL: %s | User: %s",
        request.url,
        user.id if user else "anonymous",
    )

    scraped = await scrape_product(request.url)
    if scraped.errors and not scraped.raw_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not scrape product data: {scraped.errors[0]}",
        )

    extracted_text = scraped.to_analysis_text()
    osint_context = get_osint_context(
        scraped.product_name,
        retail_price=scraped.price,
    )

    try:
        report, _ = await analyze_text(
            extracted_text=extracted_text,
            product_name=scraped.product_name,
            retail_price=scraped.price,
            osint_context=osint_context,
        )
    except Exception as e:
        logger.exception("scan_link failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"scan_link failed: {type(e).__name__}: {e}",
        )

    product_name = scraped.product_name
    fssai_candidate = scraped.fssai_number or _extract_candidate_fssai_14(extracted_text)
    flags = _convert_member3_flags(report.flags)
    fssai_number = _maybe_add_fssai_invalid_flag(flags, fssai_candidate)
    trust_level = trust_level_from_score(report.trust_score)

    scan_id = str(uuid.uuid4())
    full_report = {
        "trustScore": report.trust_score,
        "overallVerdict": report.overall_verdict,
        "productName": product_name,
        "flags": [f.model_dump() for f in flags],
        "upfScore": None,
        "fssaiNumber": fssai_number,
        "legalDraftAvailable": report.legal_draft_available,
        "legalDraftText": report.legal_draft_text,
        "sourceUrl": request.url,
    }
    _store_scan_in_supabase(
        scan_id=scan_id,
        user=user,
        product_name=product_name,
        input_source="link",
        raw_text_extracted=extracted_text[:5000],
        trust_score=report.trust_score,
        trust_level=trust_level,
        full_report=full_report,
        fssai_number=fssai_number,
    )

    return ScanResponse(
        scan_id=scan_id,
        product_name=product_name,
        trust_score=report.trust_score,
        trust_level=trust_level,
        overall_verdict=report.overall_verdict,
        flags=flags,
        upf_score=None,
        fssai_number=fssai_number,
        legal_draft_available=report.legal_draft_available,
        legal_draft_text=report.legal_draft_text,
    )
