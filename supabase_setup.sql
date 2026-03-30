-- ═══════════════════════════════════════════════════════════════
-- 🛡️ OpenLabel — Supabase Database Setup
-- ═══════════════════════════════════════════════════════════════
-- Run this ENTIRE script in your Supabase SQL Editor:
--   Dashboard → SQL Editor → New Query → Paste → Run
-- ═══════════════════════════════════════════════════════════════


-- ─────────────────────────────────────────────────────────────
-- 1. Enable Required Extensions
-- ─────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";


-- ─────────────────────────────────────────────────────────────
-- 2. Core Tables
-- ─────────────────────────────────────────────────────────────

-- Scans Table — Stores every food label analysis
CREATE TABLE IF NOT EXISTS scans (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    product_name TEXT,
    input_source TEXT CHECK (input_source IN ('image', 'link', 'dual-image')),
    raw_text_extracted TEXT,
    trust_score NUMERIC,
    trust_level TEXT CHECK (trust_level IN ('RED', 'YELLOW', 'GREEN')),
    full_report JSONB,
    fssai_number VARCHAR(14)
);

-- If this DB was created with an older schema, `scans` may exist but miss new columns.
-- Make the schema forward-compatible with the backend code.
ALTER TABLE public.scans
ADD COLUMN IF NOT EXISTS trust_level TEXT CHECK (trust_level IN ('RED', 'YELLOW', 'GREEN'));

-- User Profiles Table — Allergy preferences & settings
CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    allergies TEXT[] DEFAULT '{}',
    preference_level TEXT DEFAULT 'Casual' CHECK (preference_level IN ('Strict', 'Casual'))
);


-- ─────────────────────────────────────────────────────────────
-- 3. Indexes for Performance
-- ─────────────────────────────────────────────────────────────

-- Fast lookup of scans by user (for history page)
CREATE INDEX IF NOT EXISTS idx_scans_user_id ON scans(user_id);

-- Fast filtering by trust score
CREATE INDEX IF NOT EXISTS idx_scans_trust_score ON scans(trust_score);

-- Fast sorting by date (most recent first)
CREATE INDEX IF NOT EXISTS idx_scans_created_at ON scans(created_at DESC);

-- FSSAI number lookup
CREATE INDEX IF NOT EXISTS idx_scans_fssai ON scans(fssai_number);

-- Composite index for the most common query: user's recent scans
CREATE INDEX IF NOT EXISTS idx_scans_user_date ON scans(user_id, created_at DESC);


-- ─────────────────────────────────────────────────────────────
-- 4. Row Level Security (RLS) Policies
-- ─────────────────────────────────────────────────────────────

-- Enable RLS on both tables
ALTER TABLE scans ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;

-- ── Scans Policies ──────────────────────────────────────────

-- Users can read their own scans
CREATE POLICY "Users can view own scans"
    ON scans FOR SELECT
    USING (auth.uid() = user_id);

-- Users can insert their own scans
CREATE POLICY "Users can insert own scans"
    ON scans FOR INSERT
    WITH CHECK (auth.uid() = user_id OR user_id IS NULL);

-- Allow anonymous scans (user_id = NULL)
CREATE POLICY "Anonymous scans are readable by no one"
    ON scans FOR SELECT
    USING (user_id IS NULL AND FALSE);
    -- Anonymous scans exist in DB but can't be listed via client
    -- Only the backend (service role) can access them

-- ── User Profiles Policies ──────────────────────────────────

-- Users can read their own profile
CREATE POLICY "Users can view own profile"
    ON user_profiles FOR SELECT
    USING (auth.uid() = id);

-- Users can insert their own profile
CREATE POLICY "Users can create own profile"
    ON user_profiles FOR INSERT
    WITH CHECK (auth.uid() = id);

-- Users can update their own profile
CREATE POLICY "Users can update own profile"
    ON user_profiles FOR UPDATE
    USING (auth.uid() = id)
    WITH CHECK (auth.uid() = id);


-- ─────────────────────────────────────────────────────────────
-- 5. Auto-Update Trigger for user_profiles.updated_at
-- ─────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON user_profiles
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();


-- ─────────────────────────────────────────────────────────────
-- 6. Helper Views (Optional — for Supabase Dashboard)
-- ─────────────────────────────────────────────────────────────

-- View: Recent scans with summary (for admin dashboard)
CREATE OR REPLACE VIEW recent_scans_summary AS
SELECT
    s.id,
    s.created_at,
    s.product_name,
    s.trust_score,
    s.input_source,
    s.fssai_number,
    s.full_report->>'overallVerdict' AS verdict,
    COALESCE(jsonb_array_length(s.full_report->'flags'), 0) AS flag_count
FROM scans s
ORDER BY s.created_at DESC
LIMIT 100;

-- View: Trust score distribution (for analytics)
CREATE OR REPLACE VIEW trust_score_stats AS
SELECT
    trust_score,
    COUNT(*) AS scan_count,
    COUNT(DISTINCT user_id) AS unique_users
FROM scans
WHERE trust_score IS NOT NULL
GROUP BY trust_score
ORDER BY scan_count DESC;

-- View: Most flagged products
CREATE OR REPLACE VIEW most_flagged_products AS
SELECT
    product_name,
    trust_score,
    COUNT(*) AS times_scanned,
    AVG(COALESCE(jsonb_array_length(full_report->'flags'), 0))::NUMERIC(4,1) AS avg_flags
FROM scans
WHERE product_name IS NOT NULL
GROUP BY product_name, trust_score
HAVING COUNT(*) > 1
ORDER BY times_scanned DESC
LIMIT 50;


-- ─────────────────────────────────────────────────────────────
-- 7. Seed Data: Auto-create profile on user signup
-- ─────────────────────────────────────────────────────────────

-- Trigger: Automatically create a user_profile when a new user signs up
CREATE OR REPLACE FUNCTION create_profile_on_signup()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.user_profiles (id)
    VALUES (NEW.id)
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Drop if exists to avoid conflicts
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION create_profile_on_signup();


-- ═══════════════════════════════════════════════════════════════
-- ✅ Setup Complete!
-- ═══════════════════════════════════════════════════════════════
-- Tables created: scans, user_profiles
-- Indexes created: 5 performance indexes
-- RLS Policies: 5 policies for secure access
-- Triggers: auto-update timestamp, auto-create profile
-- Views: 3 dashboard views for analytics
-- ═══════════════════════════════════════════════════════════════
