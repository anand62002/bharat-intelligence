-- Migration: create warren_bot_cache table
-- Purpose   : 24-hour on-demand cache for GET /api/warren_bot/{symbol}
-- Run once  : Supabase dashboard → SQL Editor
-- Safe      : uses IF NOT EXISTS / OR REPLACE throughout

CREATE TABLE IF NOT EXISTS warren_bot_cache (
    symbol     TEXT        PRIMARY KEY,           -- upper-case NSE ticker, e.g. RELIANCE
    result     JSONB       NOT NULL,              -- full warren_bot output dict
    cached_at  TIMESTAMPTZ NOT NULL DEFAULT now() -- controls 24h expiry in the API
);

-- Index to speed up the gte("cached_at", ...) filter used by the cache lookup
CREATE INDEX IF NOT EXISTS warren_bot_cache_cached_at_idx
    ON warren_bot_cache (cached_at DESC);

-- Allow the service_role to read and write (bypasses RLS automatically,
-- but explicit GRANT is needed for table-level privileges)
GRANT ALL ON warren_bot_cache TO service_role;
