"""
OpenLabel — Reports Router
Endpoints for retrieving scan history and individual report details.
Requires authentication — shows only the authenticated user's scans.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import get_current_user
from database import get_supabase_admin
from models.schemas import (
    ReportListResponse,
    ReportSummary,
    ReportDetailResponse,
    FlagItem,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["📊 Reports & History"])


# ═══════════════════════════════════════════════════════════
#  GET /api/v1/reports — List User's Scan History
# ═══════════════════════════════════════════════════════════

@router.get(
    "",
    response_model=ReportListResponse,
    summary="Get scan history",
    description="Returns a paginated list of the authenticated user's past scans, sorted by most recent.",
)
async def list_reports(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Results per page"),
    trust_score: Optional[str] = Query(None, description="Filter by trust score: RED, YELLOW, GREEN"),
    user: dict = Depends(get_current_user),
):
    """Fetch paginated scan history for the authenticated user."""
    logger.info(f"Listing reports for user {user.id} | page={page}, per_page={per_page}")

    try:
        supabase = get_supabase_admin()
        
        # Build query
        query = (
            supabase.table("scans")
            .select(
                "id, product_name, trust_score, trust_level, full_report, input_source, created_at",
                count="exact",
            )
            .eq("user_id", str(user.id))
            .order("created_at", desc=True)
        )

        # Optional filter
        if trust_score:
            upper = trust_score.upper()
            if upper in {"RED", "YELLOW", "GREEN"}:
                query = query.eq("trust_level", upper)
            else:
                # Best-effort: allow filtering by numeric trust_score if caller passes a float.
                try:
                    query = query.eq("trust_score", float(trust_score))
                except ValueError:
                    pass

        # Pagination
        offset = (page - 1) * per_page
        query = query.range(offset, offset + per_page - 1)

        result = query.execute()

        reports = []
        for row in result.data:
            full_report = row.get("full_report", {}) or {}
            reports.append(
                ReportSummary(
                    scan_id=row["id"],
                    product_name=row.get("product_name"),
                    trust_score=float(row.get("trust_score", 50.0)),
                    trust_level=row.get("trust_level", "YELLOW"),
                    overall_verdict=full_report.get("overallVerdict", "No verdict available."),
                    input_source=row.get("input_source", "unknown"),
                    created_at=str(row.get("created_at", "")),
                )
            )

        return ReportListResponse(
            total=result.count or len(reports),
            page=page,
            per_page=per_page,
            reports=reports,
        )

    except Exception as e:
        logger.error(f"Failed to fetch reports: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve scan history.",
        )


# ═══════════════════════════════════════════════════════════
#  GET /api/v1/reports/{scan_id} — Full Report Detail
# ═══════════════════════════════════════════════════════════

@router.get(
    "/{scan_id}",
    response_model=ReportDetailResponse,
    summary="Get full report details",
    description="Returns the complete analysis report for a specific scan.",
)
async def get_report(
    scan_id: str,
    user: dict = Depends(get_current_user),
):
    """Fetch a single scan report by ID, ensuring it belongs to the authenticated user."""
    logger.info(f"Fetching report {scan_id} for user {user.id}")

    try:
        supabase = get_supabase_admin()
        
        result = (
            supabase.table("scans")
            .select("*")
            .eq("id", scan_id)
            .eq("user_id", str(user.id))
            .single()
            .execute()
        )

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Scan report not found or you don't have access to it.",
            )

        row = result.data
        full_report = row.get("full_report", {}) or {}

        return ReportDetailResponse(
            scan_id=row["id"],
            product_name=row.get("product_name"),
            trust_score=float(row.get("trust_score", 50.0)),
            trust_level=row.get("trust_level", "YELLOW"),
            overall_verdict=full_report.get("overallVerdict", "No verdict available."),
            flags=[
                FlagItem(**flag) for flag in full_report.get("flags", [])
            ],
            upf_score=full_report.get("upfScore"),
            fssai_number=row.get("fssai_number"),
            legal_draft_available=full_report.get("legalDraftAvailable", False),
            legal_draft_text=full_report.get("legalDraftText"),
            created_at=str(row.get("created_at", "")),
            raw_text_extracted=row.get("raw_text_extracted"),
            input_source=row.get("input_source"),
            full_report=full_report,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch report {scan_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve scan report.",
        )
