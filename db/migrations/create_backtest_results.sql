-- Migration: create_backtest_results
-- Run in Supabase SQL Editor before deploying the P1-A backtest framework.
-- Referenced by: agents/backtester.py, api/main.py (/api/backtest/summary)

CREATE TABLE IF NOT EXISTS backtest_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date        DATE NOT NULL DEFAULT CURRENT_DATE,
    universe        TEXT NOT NULL DEFAULT 'NIFTY500_QUALITY',
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    split_type      TEXT CHECK (split_type IN ('TRAIN', 'TEST', 'FULL')),
    total_signals   INTEGER,
    hit_rate_90d    NUMERIC(5,2),      -- % of signals where alpha_90d > 0
    avg_alpha_90d   NUMERIC(7,4),      -- mean (trade_return - nifty_return) at 90 days
    avg_alpha_180d  NUMERIC(7,4),      -- same at 180 days (fewer signals available)
    sharpe_ratio    NUMERIC(6,3),      -- mean(alpha) / std(alpha) cross all trades
    max_drawdown    NUMERIC(7,4),      -- worst single trade return (as decimal, e.g. -0.15)
    win_loss_ratio  NUMERIC(6,3),      -- avg_win / abs(avg_loss)
    signal_details  JSONB,             -- per-trade detail array (capped at 500 rows)
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Grant access
GRANT ALL ON backtest_results TO service_role;

-- RLS
ALTER TABLE backtest_results ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all" ON backtest_results
    FOR ALL USING (true) WITH CHECK (true);

-- Useful indexes
CREATE INDEX IF NOT EXISTS idx_bt_run_date   ON backtest_results (run_date DESC);
CREATE INDEX IF NOT EXISTS idx_bt_split_type ON backtest_results (split_type, run_date DESC);
