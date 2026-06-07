-- Migration: dynamic target tracking columns (P7-A)
-- Adds fields needed by agents/target_updater.py
-- Run once in Supabase SQL Editor.

ALTER TABLE portfolio_holdings
    ADD COLUMN IF NOT EXISTS original_target        NUMERIC,          -- target price at first save (never overwritten)
    ADD COLUMN IF NOT EXISTS target_updated_at      TIMESTAMPTZ,      -- last time target was revised
    ADD COLUMN IF NOT EXISTS target_update_count    INTEGER DEFAULT 0, -- how many times target has been raised
    ADD COLUMN IF NOT EXISTS protect_gains_flag     BOOLEAN DEFAULT FALSE, -- steam detected — stop raising, protect profits
    ADD COLUMN IF NOT EXISTS stoploss_ratchet_level TEXT    DEFAULT 'ORIGINAL',  -- ORIGINAL | BREAKEVEN | LOCK_20
    ADD COLUMN IF NOT EXISTS last_review_at         DATE;             -- last laggard review date (30-day cooldown)

-- Index for the after-close updater query (OPEN holdings only)
CREATE INDEX IF NOT EXISTS idx_portfolio_holdings_status_review
    ON portfolio_holdings (status, last_review_at);

NOTIFY pgrst, 'reload schema';
SELECT 'add_target_tracking_columns migration complete' AS status;
