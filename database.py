"""
OpenLabel — Database / Supabase Client
Initializes and provides Supabase client instances for dependency injection.
"""

from supabase import create_client, Client
from config import get_settings


# ── Singleton Clients ────────────────────────────────────────────

_supabase_admin: Client | None = None
_supabase_public: Client | None = None


def get_supabase_admin() -> Client:
    """
    Returns a Supabase client using the SERVICE ROLE key.
    Bypasses Row-Level Security — use for backend-internal operations only.
    """
    global _supabase_admin
    if _supabase_admin is None:
        settings = get_settings()
        _supabase_admin = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,
        )
    return _supabase_admin


def get_supabase_public() -> Client:
    """
    Returns a Supabase client using the ANON key.
    Respects Row-Level Security policies.
    """
    global _supabase_public
    if _supabase_public is None:
        settings = get_settings()
        _supabase_public = create_client(
            settings.supabase_url,
            settings.supabase_anon_key,
        )
    return _supabase_public
