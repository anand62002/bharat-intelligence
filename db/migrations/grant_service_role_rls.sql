-- =============================================================================
-- db/migrations/grant_service_role_rls.sql
--
-- PURPOSE
-- -------
-- All production tables have RLS ENABLED.  This migration ensures the backend
-- service role (used by Python agents and Next.js API routes via the
-- SUPABASE_SERVICE_KEY) can SELECT / INSERT / UPDATE / DELETE without being
-- blocked by row-level security policies.
--
-- HOW SUPABASE RLS + SERVICE ROLE WORKS
-- --------------------------------------
-- 1. service_role is a Postgres role with the BYPASSRLS attribute, so it
--    already skips RLS checks automatically.
-- 2. However, Postgres still requires the role to have table-level PRIVILEGES
--    (SELECT, INSERT, etc.) granted separately.
-- 3. This script grants those privileges AND adds explicit permissive RLS
--    policies for service_role as belt-and-suspenders (visible in the
--    Supabase dashboard Auth > Policies view).
--
-- HOW TO RUN
-- ----------
-- Supabase Dashboard → SQL Editor → New Query → paste & run.
-- Safe to run multiple times (CREATE POLICY uses IF NOT EXISTS, GRANT is
-- idempotent).
--
-- TABLES COVERED
-- --------------
--   agent_performance       institutional_flows     enhancement_proposals
--   recommendations         sector_pe_snapshots     research_proposals
-- =============================================================================


-- ─────────────────────────────────────────────────────────────────────────────
-- HELPER: shorthand to grant privileges + create a permissive service_role
--         policy on a table.
--
-- We use individual statements rather than a PL/pgSQL loop so the script works
-- in the Supabase SQL Editor (which does not allow DO $$ blocks in some plans).
-- ─────────────────────────────────────────────────────────────────────────────


-- =============================================================================
-- 1. agent_performance
-- =============================================================================

GRANT ALL ON agent_performance TO service_role;
GRANT ALL ON agent_performance TO postgres;

-- Keep RLS ON but allow service_role unrestricted access
ALTER TABLE agent_performance ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all_agent_performance" ON agent_performance;
CREATE POLICY "service_role_all_agent_performance"
    ON agent_performance
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);


-- =============================================================================
-- 2. institutional_flows
-- =============================================================================

GRANT ALL ON institutional_flows TO service_role;
GRANT ALL ON institutional_flows TO postgres;

ALTER TABLE institutional_flows ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all_institutional_flows" ON institutional_flows;
CREATE POLICY "service_role_all_institutional_flows"
    ON institutional_flows
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);


-- =============================================================================
-- 3. enhancement_proposals
-- =============================================================================

GRANT ALL ON enhancement_proposals TO service_role;
GRANT ALL ON enhancement_proposals TO postgres;

ALTER TABLE enhancement_proposals ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all_enhancement_proposals" ON enhancement_proposals;
CREATE POLICY "service_role_all_enhancement_proposals"
    ON enhancement_proposals
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);


-- =============================================================================
-- 4. recommendations
-- =============================================================================

GRANT ALL ON recommendations TO service_role;
GRANT ALL ON recommendations TO postgres;

ALTER TABLE recommendations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all_recommendations" ON recommendations;
CREATE POLICY "service_role_all_recommendations"
    ON recommendations
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);


-- =============================================================================
-- 5. sector_pe_snapshots
-- =============================================================================

GRANT ALL ON sector_pe_snapshots TO service_role;
GRANT ALL ON sector_pe_snapshots TO postgres;

ALTER TABLE sector_pe_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all_sector_pe_snapshots" ON sector_pe_snapshots;
CREATE POLICY "service_role_all_sector_pe_snapshots"
    ON sector_pe_snapshots
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Grant read access to the helper view as well
GRANT SELECT ON sector_pe_latest TO service_role;
GRANT SELECT ON sector_pe_latest TO postgres;


-- =============================================================================
-- 6. historical_events
-- =============================================================================

GRANT ALL ON historical_events TO service_role;
GRANT ALL ON historical_events TO postgres;

ALTER TABLE historical_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all_historical_events" ON historical_events;
CREATE POLICY "service_role_all_historical_events"
    ON historical_events
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);


-- =============================================================================
-- 7. research_proposals
-- =============================================================================

GRANT ALL ON research_proposals TO service_role;
GRANT ALL ON research_proposals TO postgres;

ALTER TABLE research_proposals ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_all_research_proposals" ON research_proposals;
CREATE POLICY "service_role_all_research_proposals"
    ON research_proposals
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);


-- =============================================================================
-- 8. Sequence grants  (required for uuid_generate_v4() / gen_random_uuid()
--    and any SERIAL / BIGSERIAL columns to work on INSERT)
-- =============================================================================

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO postgres;


-- =============================================================================
-- 9. Future tables  -- run this after every new CREATE TABLE so new tables
--    automatically inherit service_role access without a separate migration.
-- =============================================================================

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO service_role;


-- =============================================================================
-- VERIFY
-- =============================================================================
-- After running, confirm policies are in place:
--
--   SELECT tablename, policyname, roles, cmd, qual
--   FROM   pg_policies
--   WHERE  schemaname = 'public'
--     AND  'service_role' = ANY(roles)
--   ORDER  BY tablename;
--
-- And confirm table privileges:
--
--   SELECT grantee, table_name, privilege_type
--   FROM   information_schema.role_table_grants
--   WHERE  grantee = 'service_role'
--     AND  table_schema = 'public'
--   ORDER  BY table_name, privilege_type;
-- =============================================================================
