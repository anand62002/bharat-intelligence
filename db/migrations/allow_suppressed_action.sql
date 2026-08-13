-- Allow the SUPPRESSED sentinel action on recommendations.
--
-- scheduler/orchestrator.py::_log_suppressed_synthesis() writes a row with
-- action='SUPPRESSED' so a validation-gated recommendation is still available
-- for human review. The original CHECK constraint (db/schema.sql:15) only
-- permits BUY/SELL/HOLD/AVOID, so every such insert failed with:
--
--   23514: new row for relation "recommendations" violates check constraint
--          "recommendations_action_check"
--
-- (observed in Railway logs 2026-08-13). The suppressed rows were silently lost.
--
-- Run once in Supabase → SQL Editor.

ALTER TABLE recommendations
    DROP CONSTRAINT IF EXISTS recommendations_action_check;

ALTER TABLE recommendations
    ADD CONSTRAINT recommendations_action_check
    CHECK (action IN ('BUY', 'SELL', 'HOLD', 'AVOID', 'SUPPRESSED'));
