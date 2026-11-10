-- P8-E: Long-horizon multibagger track (2026-11-11)
--
-- A separate research track from 90-day discovery: quality compounders with
-- room to grow, held 1–3 years in their own paper book. Larger allocation is
-- unlocked only once THIS track's own 1-year outcomes prove a hit rate —
-- see agents/long_horizon_book.py.
--
-- Run once in Supabase → SQL Editor. Safe to re-run.

CREATE TABLE IF NOT EXISTS long_horizon_picks (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol            TEXT        NOT NULL,
    pick_date         DATE        NOT NULL DEFAULT CURRENT_DATE,
    score             NUMERIC     NOT NULL,          -- 0–100 multibagger score
    components        JSONB       NOT NULL DEFAULT '{}'::jsonb,   -- per-component points + inputs
    thesis            TEXT,                          -- plain-English why
    entry_price       NUMERIC,
    market_cap_cr     NUMERIC,
    nifty_entry       NUMERIC,
    status            TEXT        NOT NULL DEFAULT 'ACTIVE',      -- ACTIVE | THESIS_BROKEN | EXITED
    status_reason     TEXT,
    last_reviewed_at  DATE,
    -- forward outcomes vs Nifty 50 (fractions, like recommendation_outcomes)
    price_t180  NUMERIC, nifty_t180  NUMERIC, alpha_t180  NUMERIC,
    price_t365  NUMERIC, nifty_t365  NUMERIC, alpha_t365  NUMERIC,
    price_t730  NUMERIC, nifty_t730  NUMERIC, alpha_t730  NUMERIC,
    metadata          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One ACTIVE pick per symbol.
CREATE UNIQUE INDEX IF NOT EXISTS long_horizon_picks_active_symbol
    ON long_horizon_picks (symbol) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS long_horizon_picks_pick_date ON long_horizon_picks (pick_date);

CREATE TABLE IF NOT EXISTS long_horizon_positions (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pick_id            UUID REFERENCES long_horizon_picks(id) ON DELETE CASCADE,
    symbol             TEXT        NOT NULL,
    yf_symbol          TEXT,
    entry_date         DATE        NOT NULL DEFAULT CURRENT_DATE,
    entry_price        NUMERIC     NOT NULL,
    quantity           NUMERIC     NOT NULL,
    allocation_inr     NUMERIC     NOT NULL,
    tier               TEXT        NOT NULL DEFAULT 'BASE',       -- BASE | CONVICTION
    nifty_entry        NUMERIC,
    current_price      NUMERIC,
    peak_price         NUMERIC,
    stop_price         NUMERIC,
    unrealized_pnl_pct NUMERIC,
    status             TEXT        NOT NULL DEFAULT 'OPEN',       -- OPEN | CLOSED
    exit_date          DATE,
    exit_price         NUMERIC,
    exit_reason        TEXT,                                      -- HARD_STOP | TRAILING_STOP | THESIS_BROKEN
    realized_pnl_pct   NUMERIC,
    alpha_pct          NUMERIC,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS long_horizon_positions_status ON long_horizon_positions (status);

-- Public market data only, written by the service role — same treatment as
-- fundamentals_cache.
ALTER TABLE long_horizon_picks     DISABLE ROW LEVEL SECURITY;
ALTER TABLE long_horizon_positions DISABLE ROW LEVEL SECURITY;
GRANT ALL ON long_horizon_picks     TO service_role;
GRANT ALL ON long_horizon_positions TO service_role;
