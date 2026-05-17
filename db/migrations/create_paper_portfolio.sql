-- db/migrations/create_paper_portfolio.sql
-- P5-B: Paper Portfolio Simulation
-- Run once in Supabase SQL Editor.

-- ── paper_portfolio_positions ─────────────────────────────────────────────────
-- One row per paper trade.  Opens when a new BUY rec is generated; closes when
-- stoploss/target/horizon/sell-signal exit conditions are met.

CREATE TABLE IF NOT EXISTS paper_portfolio_positions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rec_id           UUID REFERENCES recommendations(id) ON DELETE SET NULL,
    symbol           TEXT NOT NULL,
    yf_symbol        TEXT,
    action           TEXT NOT NULL DEFAULT 'BUY',
    entry_date       DATE NOT NULL,
    entry_price      NUMERIC NOT NULL,
    quantity         NUMERIC NOT NULL,
    allocation_inr   NUMERIC NOT NULL,
    position_label   TEXT,          -- "Full position (5%)" | "Half position (2.5%)" | etc.
    stoploss_price   NUMERIC,
    target_price     NUMERIC,
    nifty_entry      NUMERIC,       -- Nifty 50 level at entry for alpha calc
    -- Live / updated fields
    current_price    NUMERIC,
    current_value    NUMERIC,
    unrealized_pnl   NUMERIC,
    unrealized_pnl_pct NUMERIC,
    -- Closing fields (filled on exit)
    status           TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN | CLOSED | SKIPPED
    exit_date        DATE,
    exit_price       NUMERIC,
    nifty_exit       NUMERIC,
    realized_pnl     NUMERIC,
    realized_pnl_pct NUMERIC,
    alpha_pct        NUMERIC,       -- realized_pnl_pct - nifty_return_pct
    exit_reason      TEXT,          -- STOPLOSS | TARGET | HORIZON | SELL_SIGNAL
    created_at       TIMESTAMPTZ DEFAULT now(),
    updated_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pp_positions_status    ON paper_portfolio_positions (status);
CREATE INDEX IF NOT EXISTS idx_pp_positions_symbol    ON paper_portfolio_positions (symbol);
CREATE INDEX IF NOT EXISTS idx_pp_positions_entry     ON paper_portfolio_positions (entry_date);
CREATE INDEX IF NOT EXISTS idx_pp_positions_rec_id    ON paper_portfolio_positions (rec_id);

GRANT ALL ON paper_portfolio_positions TO service_role;


-- ── paper_portfolio_snapshots ─────────────────────────────────────────────────
-- One row per calendar day — portfolio-level aggregates for the P&L chart.

CREATE TABLE IF NOT EXISTS paper_portfolio_snapshots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_date       DATE UNIQUE NOT NULL,
    total_invested      NUMERIC DEFAULT 0,
    total_current_value NUMERIC DEFAULT 0,
    unrealized_pnl      NUMERIC DEFAULT 0,
    realized_pnl        NUMERIC DEFAULT 0,
    total_pnl           NUMERIC DEFAULT 0,
    total_pnl_pct       NUMERIC DEFAULT 0,
    open_positions      INT DEFAULT 0,
    closed_positions    INT DEFAULT 0,
    nifty_value         NUMERIC,    -- Nifty 50 index value for benchmark
    nifty_return_pct    NUMERIC,    -- Nifty % change since portfolio inception
    alpha_pct           NUMERIC,    -- total_pnl_pct - nifty_return_pct
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pp_snapshots_date ON paper_portfolio_snapshots (snapshot_date);

GRANT ALL ON paper_portfolio_snapshots TO service_role;
