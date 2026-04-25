-- Migration: add yf_symbol and danger_sources columns to portfolio_holdings
-- Run in Supabase dashboard → SQL Editor
-- Safe to run multiple times (IF NOT EXISTS / IF NOT EXISTS guards)

ALTER TABLE portfolio_holdings
  ADD COLUMN IF NOT EXISTS yf_symbol      TEXT,          -- resolved yfinance ticker (e.g. RELIANCE.NS)
  ADD COLUMN IF NOT EXISTS danger_sources TEXT[] DEFAULT '{}';  -- array of signal sources for danger alerts

-- Back-fill yf_symbol with symbol for existing rows (agents will correct with .NS suffix on next run)
UPDATE portfolio_holdings
SET yf_symbol = symbol
WHERE yf_symbol IS NULL;

-- Index for fast price-refresh queries
CREATE INDEX IF NOT EXISTS idx_holding_yf_symbol ON portfolio_holdings (yf_symbol);
