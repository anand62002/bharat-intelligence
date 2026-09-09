-- ============================================================================
-- P7-I: persistent fundamentals cache with per-field decay
-- ============================================================================
-- Fundamentals change quarterly; before this we refetched them daily. P7-H
-- removed the duplication *within* a run (9 -> 2 fetches per symbol); this
-- removes the repetition *across* days.
--
-- It also hardens the system against source outages. When screener.in and
-- Trendlyne both return 403 (as they did throughout 2026-09-09), the fetch
-- chain degrades to yfinance, which carries far fewer fields. With this table
-- the agents instead receive the last known-good screener payload, flagged with
-- its age, which is strictly better input than a thin yfinance snapshot.
--
-- kind:
--   'snapshot'  get_screener_data()    — ratios, PE, margins, promoter holding
--   'history'   get_screener_history() — 10-year annual series
--
-- quality ranks the payload's origin so a degraded fetch can never overwrite a
-- good cached entry:
--   3 = screener.in   2 = trendlyne   1 = yfinance fallback   0 = unusable
--
-- Run once in Supabase -> SQL Editor.
-- ============================================================================

CREATE TABLE IF NOT EXISTS fundamentals_cache (
    symbol       TEXT        NOT NULL,
    kind         TEXT        NOT NULL CHECK (kind IN ('snapshot', 'history')),
    payload      JSONB       NOT NULL,
    source       TEXT,                                   -- screener_in | trendlyne | yfinance_fallback
    quality      SMALLINT    NOT NULL DEFAULT 0,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hit_count    INTEGER     NOT NULL DEFAULT 0,
    CONSTRAINT pk_fundamentals_cache PRIMARY KEY (symbol, kind)
);

-- Sweeping expired rows and reporting cache age both scan by recency.
CREATE INDEX IF NOT EXISTS idx_fundamentals_cache_fetched
    ON fundamentals_cache (fetched_at DESC);

CREATE INDEX IF NOT EXISTS idx_fundamentals_cache_kind
    ON fundamentals_cache (kind, fetched_at DESC);

-- service_role bypasses RLS but still needs table privileges.
GRANT ALL ON fundamentals_cache TO service_role;

COMMENT ON TABLE  fundamentals_cache IS
    'P7-I: persistent screener.in/Trendlyne fundamentals cache with per-kind TTL. '
    'Serves last known-good data when upstream sources are blocked.';
COMMENT ON COLUMN fundamentals_cache.quality IS
    '3=screener.in 2=trendlyne 1=yfinance_fallback 0=unusable. A lower-quality '
    'fetch never overwrites a higher-quality cached row.';
