-- Migration: add danger_confidence to recommendations table
-- Root cause: column was in schema.sql but never applied to live Supabase DB.
-- Symptom: PGRST204 "Could not find the 'danger_confidence' column" on every
--           suppressed-rec log attempt → 24 errors/day in governance dashboard.
--
-- Run in Supabase SQL Editor (Dashboard → SQL Editor → New Query).

ALTER TABLE recommendations
    ADD COLUMN IF NOT EXISTS danger_confidence NUMERIC(5, 2) DEFAULT 0;

-- Refresh Supabase schema cache (PostgREST picks up new columns on next request,
-- but this speeds it up for immediate use)
NOTIFY pgrst, 'reload schema';

-- Verify
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'recommendations'
  AND column_name IN ('danger_confidence', 'upside_confidence')
ORDER BY column_name;
