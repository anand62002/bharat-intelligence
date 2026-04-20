-- ============================================================
-- Migration: enhancement_proposals + recommendations.outcome
-- Run once in Supabase SQL Editor.
-- ============================================================

-- ── enhancement_proposals ─────────────────────────────────────────────────────
-- Stores auto-generated improvement proposals when agent accuracy falls below
-- threshold for two consecutive weekly audit cycles.
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS enhancement_proposals (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title            TEXT        NOT NULL,
    proposed_by      TEXT        NOT NULL DEFAULT 'performance_tracker',
    rationale        TEXT,
    impacted_agents  TEXT[],                          -- e.g. '{technical,fundamental}'
    cost_impact      TEXT        CHECK (cost_impact IN ('low', 'medium', 'high')),
    is_paid          BOOLEAN     NOT NULL DEFAULT FALSE,
    steps            JSONB,                           -- array of actionable step strings
    status           TEXT        NOT NULL DEFAULT 'PENDING'
                                 CHECK (status IN ('PENDING','IN_PROGRESS','IMPLEMENTED','REJECTED')),
    trigger_agent    TEXT,                            -- agent that triggered this proposal
    trigger_accuracy NUMERIC(5,2),                   -- accuracy value at trigger time
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ep_status     ON enhancement_proposals (status);
CREATE INDEX IF NOT EXISTS idx_ep_agent      ON enhancement_proposals (trigger_agent);
CREATE INDEX IF NOT EXISTS idx_ep_created_at ON enhancement_proposals (created_at DESC);

DROP TRIGGER IF EXISTS trg_ep_updated_at ON enhancement_proposals;
CREATE TRIGGER trg_ep_updated_at
    BEFORE UPDATE ON enhancement_proposals
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── recommendations.outcome ────────────────────────────────────────────────────
-- Tracks real-world outcome of each recommendation against its target/stoploss.
-- ──────────────────────────────────────────────────────────────────────────────
ALTER TABLE recommendations
    ADD COLUMN IF NOT EXISTS outcome          TEXT
        CHECK (outcome IN ('SUCCESS','PARTIAL_FAIL','IN_PROGRESS','EXPIRED')),
    ADD COLUMN IF NOT EXISTS outcome_price    NUMERIC(12,2),   -- price at outcome determination
    ADD COLUMN IF NOT EXISTS outcome_checked_at TIMESTAMPTZ;   -- when outcome was last evaluated

CREATE INDEX IF NOT EXISTS idx_rec_outcome ON recommendations (outcome)
    WHERE outcome IS NOT NULL;
