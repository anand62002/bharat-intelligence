-- ============================================================
-- Migration: fix RLS / privilege issues on internal tables
-- Run once in Supabase SQL Editor.
-- ============================================================

-- ── agent_performance ─────────────────────────────────────────
-- Internal table used only by server-side agents; no user-facing RLS needed.
ALTER TABLE agent_performance DISABLE ROW LEVEL SECURITY;
GRANT ALL ON agent_performance TO service_role;
GRANT ALL ON agent_performance TO postgres;

-- ── institutional_flows ───────────────────────────────────────
-- Internal cache for FII/DII daily flows; no user-facing RLS needed.
ALTER TABLE institutional_flows DISABLE ROW LEVEL SECURITY;
GRANT ALL ON institutional_flows TO service_role;
GRANT ALL ON institutional_flows TO postgres;

-- ── enhancement_proposals ─────────────────────────────────────
ALTER TABLE enhancement_proposals DISABLE ROW LEVEL SECURITY;
GRANT ALL ON enhancement_proposals TO service_role;
GRANT ALL ON enhancement_proposals TO postgres;

-- ── Sequence grants (needed for uuid / serial inserts) ────────
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;
