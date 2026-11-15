-- ============================================================================
-- WP-012 / ADR-002: the candidate ledger
-- ============================================================================
-- The system learned only from promoted calls (~15 independent labels a month)
-- while discovery screened 200 symbols a day and discarded what it computed.
-- This table records every screened candidate with what was known at decision
-- time; the poller writes 30- and 90-day alpha back onto each row later.
--
-- One row per (run_date, symbol, source). Rows are only updated to add the
-- deep-analysis fields the same day and, later, the labels.
--
--   source: discovery | portfolio | multibagger
--   stage:  SCREENED → PRESCREEN_PASS → DEEP → PROMOTED
--
-- Applied by the owner in Supabase → SQL Editor on 2026-11-16.
-- Rollback: DROP TABLE candidates;
-- ============================================================================

CREATE TABLE IF NOT EXISTS candidates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date        DATE        NOT NULL,
    symbol          TEXT        NOT NULL,
    yf_symbol       TEXT,
    source          TEXT        NOT NULL CHECK (source IN ('discovery','portfolio','multibagger')),
    stage           TEXT        NOT NULL CHECK (stage IN ('SCREENED','PRESCREEN_PASS','DEEP','PROMOTED')),
    price           NUMERIC,
    nifty           NUMERIC,
    features        JSONB,
    agent_signals   JSONB,
    signal_score    NUMERIC,
    gate_score      NUMERIC,
    data_quality    TEXT,
    tier            TEXT,
    rec_id          UUID,
    feature_version INT,
    price_t30 NUMERIC, nifty_t30 NUMERIC, alpha_t30 NUMERIC,
    price_t90 NUMERIC, nifty_t90 NUMERIC, alpha_t90 NUMERIC,
    labelled_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_candidates_day_symbol_source UNIQUE (run_date, symbol, source)
);

CREATE INDEX IF NOT EXISTS idx_candidates_run_date   ON candidates (run_date);
-- The labeller scans rows still waiting for a 30-day label.
CREATE INDEX IF NOT EXISTS idx_candidates_unlabelled ON candidates (run_date) WHERE alpha_t30 IS NULL;

-- Public market data only; service_role bypasses RLS but still needs privileges.
ALTER TABLE candidates DISABLE ROW LEVEL SECURITY;
GRANT ALL ON candidates TO service_role;
