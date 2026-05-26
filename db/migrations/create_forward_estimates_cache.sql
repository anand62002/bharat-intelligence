-- Migration: create_forward_estimates_cache
-- Run once in Supabase SQL Editor.
-- Caches yfinance forward EPS/PE estimates per symbol for 24 hours
-- so the orchestrator doesn't hammer yfinance on every daily run.
-- Without this table every symbol produces two 404 errors per run.

CREATE TABLE IF NOT EXISTS forward_estimates_cache (
    symbol          TEXT PRIMARY KEY,
    eps_current_yr  NUMERIC,
    eps_next_yr     NUMERIC,
    rev_current_yr  NUMERIC,
    rev_next_yr     NUMERIC,
    eps_growth_pct  NUMERIC,
    forward_pe      NUMERIC,
    peg_ratio       NUMERIC,
    current_price   NUMERIC,
    analyst_count   INT,
    cached_at       TIMESTAMPTZ DEFAULT now()
);

-- Allow service_role (used by Railway worker) full access
GRANT ALL ON forward_estimates_cache TO service_role;

-- Enable RLS but allow service_role to bypass
ALTER TABLE forward_estimates_cache ENABLE ROW LEVEL SECURITY;
