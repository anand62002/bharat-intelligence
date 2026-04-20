-- ============================================================
-- Migration: sector_pe_snapshots
-- Purpose : Daily historical record of Nifty sector P/E ratios
--           and their valuation regime classifications.
--
-- Enables:
--   • Regime trend detection (is a sector getting cheaper/dearer?)
--   • Rolling long-run PE calibration (update SECTOR_LONGRUN_PE from data)
--   • Learning loop — correlate COMPRESSED-regime BUY signals with returns
--
-- Run against your Supabase project via:
--   psql $DATABASE_URL -f db/migrations/sector_pe_snapshots.sql
-- or paste into the Supabase SQL Editor.
-- ============================================================

-- ──────────────────────────────────────────────────────────────
-- Table: sector_pe_snapshots
-- One row per (date, sector_key) — UNIQUE constraint enables
-- safe idempotent daily upserts.
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sector_pe_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- When and what
    snapshot_date   DATE NOT NULL,
    sector_key      TEXT NOT NULL,          -- lower-case sector key, e.g. "it", "banking"

    -- Live market data
    live_pe         NUMERIC(8,2),           -- live sector index PE (NULL if unavailable)
    long_run_pe     NUMERIC(8,2),           -- 5-yr calibrated benchmark from SECTOR_LONGRUN_PE
    deviation_pct   NUMERIC(6,1),           -- (live_pe/long_run_pe - 1) * 100; NULL if no live PE

    -- Regime classification
    regime          TEXT NOT NULL           -- valuation regime label
                        CHECK (regime IN (
                            'COMPRESSED', 'MILDLY_COMPRESSED', 'FAIR',
                            'MILDLY_STRETCHED', 'STRETCHED', 'EXTREME'
                        )),
    multiplier      NUMERIC(5,3) NOT NULL,  -- scoring multiplier applied to benchmarks

    -- Provenance
    data_source     TEXT NOT NULL           -- where live_pe came from
                        DEFAULT 'fallback_fair'
                        CHECK (data_source IN (
                            'nse_api', 'yfinance_constituents', 'fallback_fair'
                        )),

    -- Audit
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Ensures exactly one snapshot per sector per day
    -- ON CONFLICT (snapshot_date, sector_key) DO UPDATE ... makes daily re-runs safe
    CONSTRAINT uq_sector_pe_snapshot UNIQUE (snapshot_date, sector_key)
);

-- ── Indices ───────────────────────────────────────────────────
-- Primary access pattern: fetch a sector's history ordered by date
CREATE INDEX IF NOT EXISTS idx_sps_sector_date
    ON sector_pe_snapshots (sector_key, snapshot_date DESC);

-- For date-range scans across all sectors (e.g. "all sectors on date X")
CREATE INDEX IF NOT EXISTS idx_sps_date
    ON sector_pe_snapshots (snapshot_date DESC);

-- For regime-based queries (e.g. "which sectors are COMPRESSED right now?")
CREATE INDEX IF NOT EXISTS idx_sps_regime_date
    ON sector_pe_snapshots (regime, snapshot_date DESC);

-- ── Comments ──────────────────────────────────────────────────
COMMENT ON TABLE sector_pe_snapshots IS
    'Daily Nifty sector P/E snapshots for regime classification and trend analysis.';

COMMENT ON COLUMN sector_pe_snapshots.sector_key IS
    'Lower-case sector identifier matching agents/fundamental.py SECTOR_PE_MAP keys.';

COMMENT ON COLUMN sector_pe_snapshots.live_pe IS
    'Live P/E at time of snapshot from NSE allIndices API or yfinance constituents. '
    'NULL when only the fallback FAIR regime was available.';

COMMENT ON COLUMN sector_pe_snapshots.long_run_pe IS
    'Five-year calibrated median PE benchmark from agents/sector_valuation.py SECTOR_LONGRUN_PE. '
    'Stored here so historical snapshots remain accurate even if the benchmark is later updated.';

COMMENT ON COLUMN sector_pe_snapshots.deviation_pct IS
    '(live_pe / long_run_pe - 1) * 100. '
    'Negative = sector cheaper than long-run. Positive = sector more expensive.';

COMMENT ON COLUMN sector_pe_snapshots.regime IS
    'Valuation regime: COMPRESSED < MILDLY_COMPRESSED < FAIR < MILDLY_STRETCHED < STRETCHED < EXTREME.';

COMMENT ON COLUMN sector_pe_snapshots.multiplier IS
    'Scoring multiplier applied to sector_pe benchmarks in fundamental.analyse(). '
    'Range: 0.80 (EXTREME) to 1.20 (COMPRESSED).';

COMMENT ON COLUMN sector_pe_snapshots.data_source IS
    'nse_api: live from NSE allIndices. '
    'yfinance_constituents: median of 3 representative stocks. '
    'fallback_fair: no live data available; FAIR regime assumed.';


-- ── Useful helper view ────────────────────────────────────────
-- Most-recent snapshot per sector (useful for current regime dashboard)
CREATE OR REPLACE VIEW sector_pe_latest AS
SELECT DISTINCT ON (sector_key)
    sector_key,
    snapshot_date,
    live_pe,
    long_run_pe,
    deviation_pct,
    regime,
    multiplier,
    data_source,
    created_at
FROM sector_pe_snapshots
ORDER BY sector_key, snapshot_date DESC;

COMMENT ON VIEW sector_pe_latest IS
    'Most recent sector_pe_snapshots row per sector_key. '
    'Useful for current-regime dashboards and report headers.';


-- ── Rolling long-run PE function ─────────────────────────────
-- Computes a rolling 365-day median PE for a sector from stored snapshots.
-- Used by sector_pe_tracker.py to auto-update long-run benchmarks.
CREATE OR REPLACE FUNCTION rolling_longrun_pe(
    p_sector_key    TEXT,
    p_window_days   INTEGER DEFAULT 365
)
RETURNS NUMERIC
LANGUAGE sql STABLE AS $$
    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY live_pe)
    FROM sector_pe_snapshots
    WHERE sector_key   = p_sector_key
      AND live_pe      IS NOT NULL
      AND snapshot_date >= CURRENT_DATE - p_window_days::INTEGER
    HAVING COUNT(*) >= 90   -- require at least 90 data points for statistical validity
$$;

COMMENT ON FUNCTION rolling_longrun_pe IS
    'Returns the 365-day rolling median live_pe for a sector. '
    'Returns NULL when fewer than 90 data points are available.';
