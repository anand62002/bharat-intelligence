-- Migration: earnings_calendar
-- Purpose  : Stores confirmed and estimated earnings dates per NSE symbol.
--            Primary lookup source for agents/earnings_guard.py
--            (fallback: yfinance live probe)
-- Run once : Supabase dashboard → SQL Editor
-- Safe     : uses IF NOT EXISTS / OR REPLACE throughout

CREATE TABLE IF NOT EXISTS earnings_calendar (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol        TEXT NOT NULL,                              -- upper-case NSE ticker, no suffix
    earnings_date DATE NOT NULL,
    quarter       TEXT,                                       -- e.g. Q1FY26, Q4FY25
    source        TEXT NOT NULL DEFAULT 'yfinance',          -- yfinance / manual / nse_api / bse_api
    confirmed     BOOLEAN NOT NULL DEFAULT FALSE,            -- TRUE = officially confirmed by company
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One earnings date per symbol (company can't have two dates for the same event)
    CONSTRAINT uq_earnings_symbol_date UNIQUE (symbol, earnings_date)
);

-- Fast lookup: upcoming earnings for a specific symbol
CREATE INDEX IF NOT EXISTS idx_ec_symbol
    ON earnings_calendar (symbol);

-- Range scans: "all earnings in the next 14 days"
-- (CURRENT_DATE cannot be used in index predicates — not IMMUTABLE — so no partial index)
CREATE INDEX IF NOT EXISTS idx_ec_date
    ON earnings_calendar (earnings_date);

-- Auto-update updated_at on row modification
CREATE OR REPLACE FUNCTION update_earnings_calendar_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_ec_updated_at ON earnings_calendar;
CREATE TRIGGER trg_ec_updated_at
    BEFORE UPDATE ON earnings_calendar
    FOR EACH ROW EXECUTE FUNCTION update_earnings_calendar_updated_at();

-- Grant access to service_role (bypasses RLS but still needs table-level privileges)
GRANT ALL ON earnings_calendar TO service_role;

COMMENT ON TABLE earnings_calendar IS
    'Earnings announcement dates for NSE symbols. '
    'Populated by data/earnings_fetcher.py daily job. '
    'Primary lookup source for agents/earnings_guard.py pre-earnings risk guard.';

COMMENT ON COLUMN earnings_calendar.confirmed IS
    'TRUE = date officially confirmed by company via BSE/NSE filing. '
    'FALSE = estimated from analyst consensus or yfinance probe.';
