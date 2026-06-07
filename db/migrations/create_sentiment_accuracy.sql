-- Migration: sentiment_accuracy table (P6-D-8: Signal Validation Loop)
-- Stores per-recommendation sentiment signal vs actual alpha_t90 outcome.
-- Enables: rolling-30d direction accuracy monitoring → DEGRADING flag in
--          agent_performance when accuracy < 52% (near-random baseline).
--
-- Run in Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS sentiment_accuracy (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              TEXT        NOT NULL,
    rec_id              UUID        REFERENCES recommendations(id) ON DELETE SET NULL,
    rec_date            DATE        NOT NULL,
    sentiment_score     NUMERIC,                -- sentiment.py score 0-100
    sentiment_signal    TEXT,                   -- VERY_POSITIVE|POSITIVE|NEUTRAL|NEGATIVE|VERY_NEGATIVE
    event_class         TEXT,                   -- dominant event class from D-2 classifier
    actual_alpha_t90    NUMERIC,                -- alpha_t90 from recommendation_outcomes
    correct_direction   BOOLEAN,                -- sentiment ≥60→BUY and alpha>0, or ≤40→SELL and alpha<0
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- Index for rolling-window accuracy queries
CREATE INDEX IF NOT EXISTS idx_sentiment_accuracy_rec_date  ON sentiment_accuracy (rec_date DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_accuracy_symbol     ON sentiment_accuracy (symbol);
CREATE INDEX IF NOT EXISTS idx_sentiment_accuracy_correct    ON sentiment_accuracy (correct_direction, rec_date);

-- RLS: service_role can read/write
ALTER TABLE sentiment_accuracy ENABLE ROW LEVEL SECURITY;
CREATE POLICY IF NOT EXISTS "service_role_all_sentiment_accuracy"
    ON sentiment_accuracy FOR ALL TO service_role USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';

SELECT 'sentiment_accuracy table created' AS status;
