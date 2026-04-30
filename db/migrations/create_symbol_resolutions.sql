-- Migration: create symbol_resolutions table
-- Purpose   : Persists auto-discovered and manually-confirmed yfinance ticker mappings
--             so the API never needs to probe Yahoo Finance twice for the same symbol.
--             Eliminates the need to hand-edit symbol_map.py every time a new stock
--             is added to the portfolio with a name that differs from its Yahoo ticker.
-- Run once  : Supabase dashboard → SQL Editor
-- Safe      : uses IF NOT EXISTS / OR REPLACE throughout

CREATE TABLE IF NOT EXISTS symbol_resolutions (
    input_symbol  TEXT        PRIMARY KEY,          -- uppercased raw input (no .NS/.BO)
    yf_symbol     TEXT        NOT NULL,             -- validated yfinance ticker (e.g. INDHOTEL.NS)
    source        TEXT        NOT NULL DEFAULT 'auto',
                                                    -- 'probe'  = .NS/.BO live-probe succeeded
                                                    -- 'search' = yf.Search company-name lookup
                                                    -- 'manual' = set via POST /api/symbol/override
    resolved_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Allow service_role to read and write
GRANT ALL ON symbol_resolutions TO service_role;

-- Index for bulk loads at startup
CREATE INDEX IF NOT EXISTS symbol_resolutions_source_idx
    ON symbol_resolutions (source);

-- Optional: seed with the known manual overrides that are already in _NSE_OVERRIDES
-- (the API will also populate this automatically via probe/search, but seeding speeds
--  up the first boot after deployment)
INSERT INTO symbol_resolutions (input_symbol, yf_symbol, source) VALUES
    ('IHCL',              'INDHOTEL.NS',  'manual'),
    ('TAJHOTELS',         'INDHOTEL.NS',  'manual'),
    ('BHARATSEAT',        'BHARATSE.NS',  'manual'),
    ('BHARATSEATS',       'BHARATSE.NS',  'manual'),
    ('HITACHIENERGYINDIA','POWERINDIA.NS','manual'),
    ('HITACHIENERGY',     'POWERINDIA.NS','manual'),
    ('MUTHOOT',           'MUTHOOTFIN.NS','manual'),
    ('BAJAJFINANCE',      'BAJFINANCE.NS','manual'),
    ('INTERGLOBE',        'INDIGO.NS',    'manual'),
    ('LTIMINDTREE',       'LTIM.NS',      'manual'),
    ('WELSPUNIND',        'WELSPUNLIV.NS','manual'),
    ('ZOMATO',            'ETERNAL.NS',   'manual')
ON CONFLICT (input_symbol) DO UPDATE
    SET yf_symbol   = EXCLUDED.yf_symbol,
        source      = EXCLUDED.source,
        resolved_at = now();
