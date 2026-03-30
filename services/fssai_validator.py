"""
OpenLabel — FSSAI License Number Validator
Validates the format and decodes the structure of 14-digit FSSAI license numbers.
"""

import re
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
#  FSSAI State Codes — Digits 2 & 3
# ═══════════════════════════════════════════════════════════

FSSAI_STATE_CODES = {
    "01": "Jammu & Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "25": "Daman & Diu",
    "26": "Dadra & Nagar Haveli",
    "27": "Maharashtra",
    "28": "Andhra Pradesh",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman & Nicobar",
    "36": "Telangana",
    "37": "Andhra Pradesh (New)",
    "38": "Ladakh",
}

FSSAI_LICENSE_TYPES = {
    "1": "Central License",
    "2": "State License",
    "3": "Registration",
}


# ═══════════════════════════════════════════════════════════
#  Validation Result
# ═══════════════════════════════════════════════════════════

@dataclass
class FSSAIValidationResult:
    """Result of FSSAI license number validation."""
    is_valid: bool
    license_number: str
    license_type: Optional[str] = None
    state: Optional[str] = None
    year_granted: Optional[str] = None
    errors: list = None
    decoded_info: Optional[dict] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "license_number": self.license_number,
            "license_type": self.license_type,
            "state": self.state,
            "year_granted": self.year_granted,
            "errors": self.errors,
            "decoded_info": self.decoded_info,
        }


# ═══════════════════════════════════════════════════════════
#  Validator
# ═══════════════════════════════════════════════════════════

def validate_fssai_number(license_number: str) -> FSSAIValidationResult:
    """
    Validate and decode a 14-digit FSSAI license number.
    
    Format: LLSSYYQQQNNNNNN
    - L  (digit 1):     License type (1=Central, 2=State, 3=Registration)
    - SS (digits 2-3):  State code
    - YY (digits 4-5):  Year of license grant
    - QQQ (digits 6-8): Quantity/category of enrolling master
    - NNNNNN (digits 9-14): Unique registration number
    
    Args:
        license_number: The FSSAI number to validate (string)
    
    Returns:
        FSSAIValidationResult with validation status and decoded info
    """
    # Clean input
    cleaned = re.sub(r'\s|-', '', str(license_number).strip())
    
    result = FSSAIValidationResult(
        is_valid=False,
        license_number=cleaned,
    )

    # ── Check 1: Must be exactly 14 digits ─────────────────
    if not re.match(r'^\d{14}$', cleaned):
        result.errors.append(
            f"FSSAI number must be exactly 14 digits. Got {len(cleaned)} characters."
        )
        return result

    # ── Check 2: Decode license type (digit 1) ─────────────
    type_digit = cleaned[0]
    license_type = FSSAI_LICENSE_TYPES.get(type_digit)
    if license_type:
        result.license_type = license_type
    else:
        result.errors.append(
            f"Invalid license type digit '{type_digit}'. Expected 1 (Central), 2 (State), or 3 (Registration)."
        )

    # ── Check 3: Decode state code (digits 2-3) ────────────
    state_code = cleaned[1:3]
    state = FSSAI_STATE_CODES.get(state_code)
    if state:
        result.state = state
    else:
        result.errors.append(
            f"Unknown state code '{state_code}'. May be invalid or a newly assigned code."
        )

    # ── Check 4: Decode year (digits 4-5) ──────────────────
    year_digits = cleaned[3:5]
    try:
        year = int(year_digits)
        # FSSAI started in 2006, reasonable range: 06-30
        if 6 <= year <= 30:
            result.year_granted = f"20{year_digits}"
        elif year <= 5:
            result.year_granted = f"20{year_digits}"
            result.errors.append(
                f"Year '20{year_digits}' seems unusually recent. Verify authenticity."
            )
        else:
            result.year_granted = f"20{year_digits}" if year < 50 else f"19{year_digits}"
            result.errors.append(
                f"Year '20{year_digits}' is outside typical FSSAI range."
            )
    except ValueError:
        result.errors.append(f"Invalid year digits '{year_digits}'.")

    # ── Check 5: Remaining digits ──────────────────────────
    quantity_code = cleaned[5:8]
    unique_number = cleaned[8:14]

    # ── Build decoded info ────────────────────────────────
    result.decoded_info = {
        "license_type_code": type_digit,
        "license_type": license_type or "Unknown",
        "state_code": state_code,
        "state": state or "Unknown",
        "year_granted": result.year_granted or "Unknown",
        "category_code": quantity_code,
        "unique_number": unique_number,
    }

    # ── Final validity ────────────────────────────────────
    if not result.errors:
        result.is_valid = True
    
    logger.info(
        f"FSSAI Validation: {cleaned} — "
        f"Valid={result.is_valid}, Type={result.license_type}, State={result.state}"
    )

    return result
