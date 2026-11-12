-- 2026-11-12: add danger_drop_pct to recommendations.
--
-- db/schema.sql defines it, but the production table never got it (the same
-- drift add_danger_confidence_column.sql fixed for danger_confidence). Every
-- portfolio recommendation insert from scheduler/orchestrator.py::save_recs_node
-- failed with PGRST204 "Could not find the 'danger_drop_pct' column", so the
-- 06:00 pipeline published nothing — only SUPPRESSED rows (a separate insert
-- that omits the column) ever reached the table.
--
-- save_recs_node now drops unknown columns and retries, so publishing works
-- without this migration; applying it keeps the danger-drawdown figure.
--
-- Run once in Supabase → SQL Editor. Safe to re-run.

ALTER TABLE recommendations
    ADD COLUMN IF NOT EXISTS danger_drop_pct NUMERIC(7, 2);

NOTIFY pgrst, 'reload schema';
