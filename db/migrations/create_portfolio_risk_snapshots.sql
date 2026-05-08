-- Migration: create_portfolio_risk_snapshots
-- Run in Supabase SQL Editor before deploying portfolio risk feature.
-- Referenced by: agents/portfolio_risk.py

CREATE TABLE IF NOT EXISTS portfolio_risk_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_date   DATE NOT NULL,
    portfolio_id    TEXT NOT NULL DEFAULT 'default',
    metrics         JSONB NOT NULL,
    correlation     JSONB,
    sector_weights  JSONB,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(snapshot_date, portfolio_id)
);

GRANT ALL ON portfolio_risk_snapshots TO service_role;

-- RLS: allow service_role full access (bypasses RLS by default,
-- but explicit grant ensures table-level privileges are set).
ALTER TABLE portfolio_risk_snapshots ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all" ON portfolio_risk_snapshots
    FOR ALL USING (true) WITH CHECK (true);
