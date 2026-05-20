-- P5-D: Live performance columns for recommendation_outcomes
-- Run once in Supabase SQL Editor
-- Adds: t+30 milestone columns + daily live snapshot columns
-- ──────────────────────────────────────────────────────────────────────────

-- t+30 intermediate horizon (resolved once, like t90/t180/t365)
ALTER TABLE recommendation_outcomes
  ADD COLUMN IF NOT EXISTS price_t30   NUMERIC,
  ADD COLUMN IF NOT EXISTS nifty_t30   NUMERIC,
  ADD COLUMN IF NOT EXISTS alpha_t30   NUMERIC,
  ADD COLUMN IF NOT EXISTS outcome_t30 TEXT DEFAULT 'PENDING';

-- Daily live snapshot (updated every evening at 16:30 IST while PENDING)
ALTER TABLE recommendation_outcomes
  ADD COLUMN IF NOT EXISTS price_live      NUMERIC,
  ADD COLUMN IF NOT EXISTS nifty_live      NUMERIC,
  ADD COLUMN IF NOT EXISTS alpha_live      NUMERIC,
  ADD COLUMN IF NOT EXISTS return_live     NUMERIC,
  ADD COLUMN IF NOT EXISTS days_live       INTEGER,
  ADD COLUMN IF NOT EXISTS live_updated_at TIMESTAMPTZ;

-- Index for efficient live-data queries
CREATE INDEX IF NOT EXISTS idx_rec_outcomes_live
  ON recommendation_outcomes (outcome_t90, live_updated_at DESC);

-- Backfill outcome_t30 = 'PENDING' for existing rows that don't have it set
UPDATE recommendation_outcomes
  SET outcome_t30 = 'PENDING'
  WHERE outcome_t30 IS NULL;

-- Grant access
GRANT ALL ON recommendation_outcomes TO service_role;
