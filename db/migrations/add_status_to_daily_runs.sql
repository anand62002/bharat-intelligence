-- Migration: add status column to daily_runs table
-- Run in Supabase SQL Editor
-- Purpose: store pipeline run outcome (OK | WARNING | DATA_DEGRADATION)
-- DATA_DEGRADATION = all symbols suppressed because external data sources were unreachable

ALTER TABLE daily_runs
  ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'OK';

-- Backfill existing rows: rows with 0 errors → OK, rows with errors → WARNING
UPDATE daily_runs
SET status = CASE
  WHEN errors = 0 THEN 'OK'
  ELSE 'WARNING'
END
WHERE status IS NULL OR status = 'OK';

-- Comment explaining values
COMMENT ON COLUMN daily_runs.status IS
  'Pipeline run outcome: OK (recs produced), WARNING (some errors but partial results), '
  'DATA_DEGRADATION (all symbols suppressed — screener.in + Trendlyne unreachable)';
