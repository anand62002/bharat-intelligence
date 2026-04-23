-- ============================================================================
-- db/migrations/create_research_proposals.sql
--
-- Creates the research_proposals table used by governance/research_agent.py.
--
-- Run this in the Supabase SQL Editor (Dashboard > SQL Editor > New Query).
-- ============================================================================

-- ── Table ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS research_proposals (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Paper metadata
    title            TEXT        NOT NULL,
    source           TEXT        NOT NULL,   -- 'arxiv_rss_cs.AI' | 'arxiv_api' | 'semanticscholar' | 'ssrn'
    url              TEXT,                   -- canonical paper URL (arXiv abstract or SSRN page)

    -- Scoring
    relevance        INTEGER     CHECK (relevance BETWEEN 0 AND 100),

    -- Content
    summary          TEXT,                   -- abstract or first 800 chars
    proposed_change  TEXT,                   -- Sonnet-generated concrete improvement
    impacted_agents  TEXT[],                 -- e.g. {'technical', 'fundamental'}
    cost_impact      TEXT        DEFAULT 'medium'
                                 CHECK (cost_impact IN ('low', 'medium', 'high')),

    -- Debate: stored as array of {agent, stance, argument, confidence, key_concern, timestamp}
    debate_log       JSONB       DEFAULT '[]'::jsonb,

    -- Workflow
    status           TEXT        DEFAULT 'pending'
                                 CHECK (status IN ('pending', 'approved', 'rejected', 'implemented')),
    pr_url           TEXT,                   -- GitHub PR URL after approval

    -- Extra metadata (authors, venue, expected_improvement, file_to_change, etc.)
    metadata         JSONB       DEFAULT '{}'::jsonb,

    created_at       TIMESTAMPTZ DEFAULT now()
);

-- ── Indexes ────────────────────────────────────────────────────────────────

-- Dashboard primary sort: newest high-relevance proposals first
CREATE INDEX IF NOT EXISTS idx_research_proposals_relevance
    ON research_proposals (relevance DESC);

CREATE INDEX IF NOT EXISTS idx_research_proposals_status
    ON research_proposals (status);

CREATE INDEX IF NOT EXISTS idx_research_proposals_created_at
    ON research_proposals (created_at DESC);

-- Prevent re-processing the same paper URL across runs
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_proposals_url
    ON research_proposals (url)
    WHERE url IS NOT NULL;

-- ── RLS & permissions ──────────────────────────────────────────────────────

-- Disable RLS so service_role can INSERT/UPDATE/SELECT without a policy.
-- (Same pattern as agent_performance and institutional_flows tables.)
ALTER TABLE research_proposals DISABLE ROW LEVEL SECURITY;

GRANT ALL ON research_proposals TO service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- ── Verify ─────────────────────────────────────────────────────────────────
-- After running, confirm the table exists:
-- SELECT table_name FROM information_schema.tables
-- WHERE table_schema = 'public' AND table_name = 'research_proposals';
