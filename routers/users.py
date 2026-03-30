"""
OpenLabel — Users Router
Endpoints for managing user allergy preferences and profile settings.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from auth import get_current_user
from database import get_supabase_admin
from models.schemas import UserProfileResponse, UserProfileUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["👤 User Profile"])


# ═══════════════════════════════════════════════════════════
#  GET /api/v1/profile — Get User Profile
# ═══════════════════════════════════════════════════════════

@router.get(
    "",
    response_model=UserProfileResponse,
    summary="Get user profile",
    description="Returns the authenticated user's allergy and preference settings.",
)
async def get_profile(user: dict = Depends(get_current_user)):
    """Fetch user profile from user_profiles table."""
    logger.info(f"Fetching profile for user {user.id}")

    try:
        supabase = get_supabase_admin()
        
        result = (
            supabase.table("user_profiles")
            .select("*")
            .eq("id", str(user.id))
            .maybe_single()
            .execute()
        )

        if result.data:
            return UserProfileResponse(
                user_id=str(user.id),
                allergies=result.data.get("allergies", []) or [],
                preference_level=result.data.get("preference_level", "Casual"),
            )
        else:
            # Profile doesn't exist yet — return defaults
            return UserProfileResponse(
                user_id=str(user.id),
                allergies=[],
                preference_level="Casual",
            )

    except Exception as e:
        logger.error(f"Failed to fetch profile: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user profile.",
        )


# ═══════════════════════════════════════════════════════════
#  PUT /api/v1/profile — Update User Profile
# ═══════════════════════════════════════════════════════════

@router.put(
    "",
    response_model=UserProfileResponse,
    summary="Update user profile",
    description="Update allergy list and preference level (Strict/Casual).",
)
async def update_profile(
    update: UserProfileUpdate,
    user: dict = Depends(get_current_user),
):
    """Upsert user profile — creates if not exists, updates if exists."""
    logger.info(f"Updating profile for user {user.id}")

    try:
        supabase = get_supabase_admin()

        # Build the update payload (only non-None fields)
        payload = {"id": str(user.id)}
        if update.allergies is not None:
            payload["allergies"] = update.allergies
        if update.preference_level is not None:
            if update.preference_level not in ("Strict", "Casual"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="preference_level must be 'Strict' or 'Casual'.",
                )
            payload["preference_level"] = update.preference_level

        # Upsert — insert or update
        result = (
            supabase.table("user_profiles")
            .upsert(payload, on_conflict="id")
            .execute()
        )

        if result.data:
            row = result.data[0]
            return UserProfileResponse(
                user_id=str(user.id),
                allergies=row.get("allergies", []) or [],
                preference_level=row.get("preference_level", "Casual"),
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update profile.",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update profile: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user profile.",
        )
