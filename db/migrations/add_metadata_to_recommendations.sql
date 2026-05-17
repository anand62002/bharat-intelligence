-- db/migrations/add_metadata_to_recommendations.sql
--
-- Adds a JSONB metadata column to recommendations.
-- Used by discovery_screener.py to store a rich context bag at write-time:
--
--   price           NUMERIC   snapshot price at discovery time (API overwrites on read)
--   sector          TEXT      sector string from screener data
--   name            TEXT      company display name
--   change          NUMERIC   price % change on discovery day
--   pe              NUMERIC   trailing P/E ratio
--   mkt_cap         TEXT      market cap string (e.g. "₹45,000 Cr")
--   discovery_score NUMERIC   composite score from discovery_screener
--   discovery_reason TEXT     human-readable reason for inclusion
--   screen_triggers TEXT[]    which pre-screen filters fired (e.g. ["RSI","FII"])
--   risks           TEXT[]    identified risk factors
--   catalysts       TEXT[]    identified upside catalysts
--   upside_basis    TEXT      e.g. "DCF undervaluation"
--   upside_horizon  TEXT      e.g. "12-18 months"
--   valid_till      DATE      expiry date for discovery recs (7-day window)
--   liquidity_tier  TEXT      "HIGH" | "MEDIUM" | "LOW"
--   impact_cost_pct NUMERIC   estimated market-impact cost for the position size
--   forward_pe      NUMERIC   forward P/E from analyst estimates
--   peg_ratio_fwd   NUMERIC   forward PEG ratio
--   peg_ratio       NUMERIC   trailing PEG ratio
--   eps_growth_pct  NUMERIC   EPS growth % YoY
--
-- Safe to run multiple times (IF NOT EXISTS guard on column add).
-- Run in: Supabase Dashboard → SQL Editor → New Query

ALTER TABLE recommendations
  ADD COLUMN IF NOT EXISTS metadata JSONB;

-- Index on discovery-specific sub-keys that the API queries frequently
CREATE INDEX IF NOT EXISTS idx_rec_metadata_gin
  ON recommendations USING GIN (metadata);

-- RLS: already covered by the service_role policy in grant_service_role_rls.sql.
-- No separate grant needed here.
