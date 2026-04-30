-- Migration: create discovery_runs table
-- Purpose   : Stores the full list of symbols screened by the discovery screener
--             each day so the dashboard can show "what was evaluated today"
-- Run once  : Supabase dashboard → SQL Editor
-- Safe      : uses IF NOT EXISTS / OR REPLACE throughout

CREATE TABLE IF NOT EXISTS discovery_runs (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date         DATE        NOT NULL,               -- date of the screener run (UTC)
    slice_symbols    JSONB       NOT NULL DEFAULT '[]',  -- all symbols in today's rotation slice
    passed_symbols   JSONB       NOT NULL DEFAULT '[]',  -- symbols that passed pre-screen filters
    discovery_symbols JSONB      NOT NULL DEFAULT '[]',  -- symbols promoted to full recommendations
    coverage_stats   JSONB       NOT NULL DEFAULT '{}',  -- universe size, cycle day, pct complete
    total_screened   INTEGER     NOT NULL DEFAULT 0,     -- len(slice_symbols)
    total_passed     INTEGER     NOT NULL DEFAULT 0,     -- len(passed_symbols)
    total_discoveries INTEGER    NOT NULL DEFAULT 0,     -- len(discovery_symbols)
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per date (latest run wins on conflict)
CREATE UNIQUE INDEX IF NOT EXISTS discovery_runs_run_date_idx
    ON discovery_runs (run_date DESC);

-- Speed up "last N days" lookups from the API
CREATE INDEX IF NOT EXISTS discovery_runs_created_at_idx
    ON discovery_runs (created_at DESC);

-- Allow service_role to read and write
GRANT ALL ON discovery_runs TO service_role;
