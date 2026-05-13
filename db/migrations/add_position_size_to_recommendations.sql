-- Migration: add position sizing columns to recommendations
-- Run in Supabase SQL Editor (Project → SQL Editor → New Query)
--
-- Adds two columns to support P3-A Position Sizing output:
--   suggested_position_pct  — numeric % of portfolio (0 / 1.25 / 2.5 / 5.0)
--   position_label          — human-readable tier (e.g. "Half position (2.5%)")
--
-- Both are nullable so existing rows are unaffected.

ALTER TABLE recommendations
  ADD COLUMN IF NOT EXISTS suggested_position_pct NUMERIC(5, 2),
  ADD COLUMN IF NOT EXISTS position_label         TEXT;

-- Optional index for filtering/sorting by position size
CREATE INDEX IF NOT EXISTS idx_recs_position_pct
  ON recommendations (suggested_position_pct)
  WHERE suggested_position_pct IS NOT NULL;

COMMENT ON COLUMN recommendations.suggested_position_pct IS
  'Suggested % of total portfolio to allocate: 0 (avoid) / 1.25 (quarter) / 2.5 (half) / 5.0 (full). Computed by agents/position_sizer.py.';

COMMENT ON COLUMN recommendations.position_label IS
  'Human-readable position tier: "Full position (5%)" | "Half position (2.5%)" | "Quarter position (1.25%)" | "Avoid (0%)".';
