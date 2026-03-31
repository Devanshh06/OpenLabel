"""
OpenLabel — API Request/Response Schemas
Pydantic models for all API endpoints.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime


# ═══════════════════════════════════════════════════════════
#  SCAN ENDPOINTS — Request Models
# ═══════════════════════════════════════════════════════════

class ScanImageRequest(BaseModel):
    """Request body for POST /api/v1/scan/image"""
    image_base64: str = Field(
        ...,
        description="Base64-encoded image of the food product label.",
    )
    product_name: Optional[str] = Field(
        None,
        description="Optional product name for better AI context.",
    )
    retail_price: Optional[float] = Field(
        None,
        description="Retail price in INR for economic fraud detection.",
    )


class ScanLinkRequest(BaseModel):
    """Request body for POST /api/v1/scan/link"""
    url: str = Field(
        ...,
        description="Product URL from an e-commerce site (BigBasket, Blinkit, Amazon.in, etc.)",
    )


# ═══════════════════════════════════════════════════════════
#  SCAN ENDPOINTS — Response Models
# ═══════════════════════════════════════════════════════════

class FlagItem(BaseModel):
    """A single violation flag detected by the AI engine."""
    code: str = Field(..., description="Stable machine-readable flag id, e.g. INGREDIENT_SPLITTING.")
    title: str = Field(..., description="Human-readable name of the violation.")
    severity: Literal["low", "medium", "high"] = Field(..., description="Severity of the finding.")
    evidence: str = Field(..., description="Short verbatim quote or OSINT reference.")
    rationale: str = Field(..., description="Why this matters for an Indian consumer under FSSAI norms.")


class ScanResponse(BaseModel):
    """Response body for scan endpoints."""
    scan_id: str = Field(..., description="Unique ID for this scan record.")
    product_name: Optional[str] = Field(None, description="Detected or provided product name.")
    is_non_edible: bool = Field(False, description="True if product is not a food/edible item.")
    trust_score: float = Field(..., ge=0.0, le=100.0, description="0–100 trust score.")
    trust_level: Literal["RED", "YELLOW", "GREEN"] = Field(..., description="Derived trust level (for backward compatibility).")
    overall_verdict: str = Field(..., description="Short summary of the forensic findings.")
    flags: List[FlagItem] = Field(default_factory=list, description="List of detected violations.")
    upf_score: Optional[int] = Field(None, description="Ultra-Processed Food risk score (1-10).")
    fssai_number: Optional[str] = Field(None, description="Detected FSSAI license number.")
    legal_draft_available: bool = Field(False, description="Whether a legal complaint draft is available.")
    legal_draft_text: Optional[str] = Field(None, description="Auto-generated Jago Grahak Jago complaint.")
    healthier_alternatives: List[str] = Field(default_factory=list, description="Short, concise list of healthier alternative product options.")
    allergy_risks: List[str] = Field(default_factory=list, description="Short, concise list of potential allergens or allergy effects.")
    created_at: Optional[str] = Field(None, description="Timestamp of the scan.")


class ScanDualImageRequest(BaseModel):
    """Request body for POST /api/v1/scan/dual-image"""
    front_image_base64: str = Field(
        ...,
        description="Base64-encoded front label image of the food product.",
    )
    back_image_base64: str = Field(
        ...,
        description="Base64-encoded back label image of the food product.",
    )
    product_name: Optional[str] = Field(
        None,
        description="Optional product name for better AI context.",
    )
    retail_price: Optional[float] = Field(
        None,
        description="Retail price in INR for economic fraud detection.",
    )


# ═══════════════════════════════════════════════════════════
#  REPORTS ENDPOINTS
# ═══════════════════════════════════════════════════════════

class ReportSummary(BaseModel):
    """Lightweight report for listing past scans."""
    scan_id: str
    product_name: Optional[str] = None
    trust_score: float
    trust_level: Literal["RED", "YELLOW", "GREEN"]
    overall_verdict: str
    input_source: str
    created_at: str


class ReportListResponse(BaseModel):
    """Response for GET /api/v1/reports"""
    total: int
    page: int
    per_page: int
    reports: List[ReportSummary]


class ReportDetailResponse(ScanResponse):
    """Full report detail — extends ScanResponse with raw data."""
    raw_text_extracted: Optional[str] = None
    input_source: Optional[str] = None
    full_report: Optional[dict] = None


# ═══════════════════════════════════════════════════════════
#  USER PROFILE
# ═══════════════════════════════════════════════════════════

class UserProfileResponse(BaseModel):
    """Response for GET /api/v1/profile"""
    user_id: str
    allergies: List[str] = Field(default_factory=list)
    preference_level: str = Field("Casual", description="'Strict' or 'Casual'")


class UserProfileUpdate(BaseModel):
    """Request body for PUT /api/v1/profile"""
    allergies: Optional[List[str]] = Field(None, description="e.g., ['Peanuts', 'Dairy']")
    preference_level: Optional[str] = Field(None, description="'Strict' or 'Casual'")


# ═══════════════════════════════════════════════════════════
#  HEALTH CHECK
# ═══════════════════════════════════════════════════════════

class HealthResponse(BaseModel):
    """Response for GET /"""
    status: str = "healthy"
    service: str = "OpenLabel Engine Room"
    version: str = "1.0.0"
    docs: str = "/docs"
