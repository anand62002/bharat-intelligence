-- ============================================================
-- Supabase SQL Migration — Stock Analysis Platform
-- ============================================================

-- Enable pgvector for semantic similarity on historical events
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- recommendations
-- Stores AI-generated trade recommendations
-- ============================================================
CREATE TABLE IF NOT EXISTS recommendations (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol           TEXT NOT NULL,
    action           TEXT NOT NULL CHECK (action IN ('BUY', 'SELL', 'HOLD', 'AVOID')),
    confidence       NUMERIC(5,2),           -- 0–100
    risk_score       NUMERIC(5,2),           -- 0–100
    entry_low        NUMERIC(12,2),
    entry_high       NUMERIC(12,2),
    target           NUMERIC(12,2),
    stoploss         NUMERIC(12,2),
    horizon_days     INTEGER,
    valid_till       DATE,
    headline         TEXT,
    summary          TEXT,
    agent_signals    JSONB,                  -- per-agent signal breakdown
    gov_check        JSONB,                  -- governance / red-flag checks
    upside_pct       NUMERIC(7,2),
    upside_confidence NUMERIC(5,2),          -- 0–100
    is_discovery     BOOLEAN NOT NULL DEFAULT FALSE,   -- TRUE = found by discovery_screener
    position_label   TEXT,                  -- "Full position (5%)" | "Half position (2.5%)" | etc.
    metadata         JSONB,                 -- discovery context bag: price, sector, name, risks, catalysts, valid_till, etc.
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rec_symbol       ON recommendations (symbol);
CREATE INDEX IF NOT EXISTS idx_rec_created_at   ON recommendations (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rec_action       ON recommendations (action);
CREATE INDEX IF NOT EXISTS idx_rec_is_discovery ON recommendations (is_discovery) WHERE is_discovery = TRUE;

-- ============================================================
-- portfolio_holdings
-- Tracks user's live portfolio positions
-- ============================================================
CREATE TABLE IF NOT EXISTS portfolio_holdings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              TEXT NOT NULL,
    name                TEXT,
    sector              TEXT,
    qty                 NUMERIC(15,4) NOT NULL DEFAULT 0,
    avg_buy             NUMERIC(12,2) NOT NULL,
    current_price       NUMERIC(12,2),
    buy_date            DATE,
    target_price        NUMERIC(12,2),
    stoploss_price      NUMERIC(12,2),
    linked_rec_id       UUID REFERENCES recommendations (id) ON DELETE SET NULL,
    notes               TEXT,
    status              TEXT NOT NULL DEFAULT 'OPEN'
                            CHECK (status IN ('OPEN', 'CLOSED', 'PARTIAL')),
    -- Danger / drawdown monitoring
    danger_drop_pct     NUMERIC(7,2),        -- % drop that triggers danger alert
    danger_confidence   NUMERIC(5,2),        -- model confidence in danger signal
    danger_trigger      TEXT,                -- human-readable trigger condition
    danger_window       INTEGER,             -- look-back / forward window in days
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_holding_symbol ON portfolio_holdings (symbol);
CREATE INDEX IF NOT EXISTS idx_holding_status ON portfolio_holdings (status);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_holding_updated_at ON portfolio_holdings;
CREATE TRIGGER trg_holding_updated_at
    BEFORE UPDATE ON portfolio_holdings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- portfolio_alerts
-- Event-driven alerts linked to holdings
-- ============================================================
CREATE TABLE IF NOT EXISTS portfolio_alerts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    holding_id  UUID REFERENCES portfolio_holdings (id) ON DELETE CASCADE,
    symbol      TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'DANGER', 'CRITICAL')),
    alert_type  TEXT NOT NULL,              -- e.g. STOPLOSS_HIT, TARGET_HIT, DANGER_ZONE
    title       TEXT NOT NULL,
    detail      TEXT,
    resolved    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_alert_holding_id  ON portfolio_alerts (holding_id);
CREATE INDEX IF NOT EXISTS idx_alert_symbol      ON portfolio_alerts (symbol);
CREATE INDEX IF NOT EXISTS idx_alert_severity    ON portfolio_alerts (severity);
CREATE INDEX IF NOT EXISTS idx_alert_resolved    ON portfolio_alerts (resolved);

-- ============================================================
-- agent_performance
-- Tracks accuracy and health of each analysis agent
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_performance (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name          TEXT NOT NULL,
    accuracy_90d        NUMERIC(5,2),        -- % correct over last 90 days
    hallucination_rate  NUMERIC(5,2),        -- % flagged hallucinations
    trend               TEXT CHECK (trend IN ('IMPROVING', 'STABLE', 'DEGRADING')),
    audit_date          DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE INDEX IF NOT EXISTS idx_agent_name       ON agent_performance (agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_audit_date ON agent_performance (audit_date DESC);

-- ============================================================
-- historical_events
-- Macro / market events used for RAG-based context injection
-- ============================================================
CREATE TABLE IF NOT EXISTS historical_events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type       TEXT NOT NULL,          -- CRISIS, BUDGET, RBI_POLICY, REGULATION, GLOBAL
    description      TEXT NOT NULL,
    event_date       DATE NOT NULL,
    affected_sectors TEXT[],                 -- e.g. {'BANKING','REALTY'}
    market_impact    TEXT,                   -- qualitative: SEVERE_NEGATIVE, MILD_POSITIVE …
    outcome          TEXT,
    embedding        vector(1536)            -- OpenAI / any 1536-dim embedding
);

CREATE INDEX IF NOT EXISTS idx_he_event_type  ON historical_events (event_type);
CREATE INDEX IF NOT EXISTS idx_he_event_date  ON historical_events (event_date DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_he_unique_event ON historical_events (event_date, event_type, LEFT(description, 80));

-- IVFFlat index for approximate nearest-neighbour search on embeddings
-- (run AFTER table is populated for best index quality)
-- CREATE INDEX IF NOT EXISTS idx_he_embedding
--     ON historical_events USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);

-- ============================================================
-- institutional_flows
-- Daily FII/DII net flow cache used by agents/institutional.py
-- for rolling 5/10-session window calculations.
-- ============================================================
CREATE TABLE IF NOT EXISTS institutional_flows (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_date DATE NOT NULL UNIQUE,          -- one row per trading session
    fii_net      NUMERIC(14,2),                 -- FII net flow in ₹ Crores
    dii_net      NUMERIC(14,2),                 -- DII net flow in ₹ Crores
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inst_flows_date ON institutional_flows (session_date DESC);

DROP TRIGGER IF EXISTS trg_inst_flows_updated_at ON institutional_flows;
CREATE TRIGGER trg_inst_flows_updated_at
    BEFORE UPDATE ON institutional_flows
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- daily_runs
-- Audit log for scheduled pipeline executions
-- ============================================================
CREATE TABLE IF NOT EXISTS daily_runs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date          DATE NOT NULL DEFAULT CURRENT_DATE,
    symbols_processed INTEGER NOT NULL DEFAULT 0,
    errors            INTEGER NOT NULL DEFAULT 0,
    duration_seconds  NUMERIC(10,2),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_run_date ON daily_runs (run_date DESC);
