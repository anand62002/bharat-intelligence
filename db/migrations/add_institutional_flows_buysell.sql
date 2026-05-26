-- Migration: add_institutional_flows_buysell
-- Run once in Supabase SQL Editor.
-- Adds fii_buy and fii_sell columns to institutional_flows table.
-- These store gross buy/sell values from NSE live API per session date.
-- fii_net = fii_buy - fii_sell (gross turnover breakdown)
-- Used by agents/institutional.py _fetch_historical_flows() for trend analysis.
-- Without these columns the upsert in _save_institutional_flows() silently
-- ignores the buy/sell fields, meaning historical gross data is never persisted.

ALTER TABLE institutional_flows
    ADD COLUMN IF NOT EXISTS fii_buy  NUMERIC,
    ADD COLUMN IF NOT EXISTS fii_sell NUMERIC;

-- Allow service_role full access (already covered by existing RLS policy,
-- but explicit grant ensures no permission gap if table RLS was reconfigured).
GRANT ALL ON institutional_flows TO service_role;
