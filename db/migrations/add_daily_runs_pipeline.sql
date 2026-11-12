-- 2026-11-12: tag daily_runs rows with the pipeline that wrote them.
--
-- Until now every row came from the 10:30 discovery screener (errors hard-coded
-- to 0) because the 06:00 orchestrator's insert always failed on a missing
-- `agents_run` column. data/run_log.py now writes `pipeline` + `details`; it
-- falls back to the base columns until this migration is applied.
--
-- Run once in Supabase → SQL Editor. Safe to re-run.

ALTER TABLE daily_runs ADD COLUMN IF NOT EXISTS pipeline TEXT;
ALTER TABLE daily_runs ADD COLUMN IF NOT EXISTS details  JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Every existing row was written by the discovery screener.
UPDATE daily_runs SET pipeline = 'discovery' WHERE pipeline IS NULL;

CREATE INDEX IF NOT EXISTS idx_daily_runs_pipeline_date ON daily_runs (pipeline, run_date DESC);
