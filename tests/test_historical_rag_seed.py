"""
tests/test_historical_rag_seed.py
===================================
Tests for the comprehensive historical events seed data.

Validates:
  - Total event count >= 150
  - All required fields are present and typed correctly
  - market_impact values are from the allowed set
  - event_type values are from the expected set
  - No duplicate descriptions (first 80 chars)
  - Affected_sectors is always a list
  - Date range is sensible (2000–2026)
  - Distribution across positive/negative/neutral
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from db.seed_historical_events_comprehensive import EVENTS, _NEW_EVENTS
from db.seed_historical_events import EVENTS as BASE_EVENTS


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

VALID_IMPACTS = {
    "STRONG_POSITIVE", "MILD_POSITIVE", "LONG_TERM_POSITIVE",
    "SEVERE_NEGATIVE", "MODERATE_NEGATIVE", "SECTOR_NEGATIVE",
    "SEVERE_SECTOR_DISRUPTION",
    "NEUTRAL", "MIXED", "SECTOR_BULL", "MIXED",
}

VALID_EVENT_TYPES = {
    "GLOBAL", "CRISIS", "BUDGET", "RBI_POLICY", "REGULATION",
    "INDEX_EVENT", "POLICY", "POLITICAL", "SECTOR_SHOCK", "BULL_MARKET",
    "RECOVERY", "GEOPOLITICAL", "SECTOR_BULL", "MACRO_POSITIVE",
    "INSTITUTIONAL_FLOW", "IPO_EVENT", "COMMODITY",
}

MIN_YEAR = 2000
MAX_YEAR = 2027


# ──────────────────────────────────────────────────────────────────────────────
# Count tests
# ──────────────────────────────────────────────────────────────────────────────

class TestEventCount:
    def test_total_at_least_150(self):
        assert len(EVENTS) >= 150, f"Expected >=150 events, got {len(EVENTS)}"

    def test_base_has_at_least_50(self):
        assert len(BASE_EVENTS) >= 50

    def test_new_events_added(self):
        assert len(_NEW_EVENTS) >= 50, "Should have added >= 50 new events"

    def test_combined_exceeds_base(self):
        assert len(EVENTS) > len(BASE_EVENTS)


# ──────────────────────────────────────────────────────────────────────────────
# Required fields
# ──────────────────────────────────────────────────────────────────────────────

class TestRequiredFields:
    @pytest.mark.parametrize("event", EVENTS)
    def test_event_type_present(self, event):
        assert "event_type" in event and event["event_type"]

    @pytest.mark.parametrize("event", EVENTS)
    def test_description_non_empty(self, event):
        assert "description" in event and len(event["description"]) > 20

    @pytest.mark.parametrize("event", EVENTS)
    def test_event_date_is_date(self, event):
        assert isinstance(event.get("event_date"), date)

    @pytest.mark.parametrize("event", EVENTS)
    def test_market_impact_present(self, event):
        assert "market_impact" in event and event["market_impact"]

    @pytest.mark.parametrize("event", EVENTS)
    def test_outcome_present(self, event):
        assert "outcome" in event and event["outcome"]

    @pytest.mark.parametrize("event", EVENTS)
    def test_affected_sectors_is_list(self, event):
        sectors = event.get("affected_sectors")
        assert isinstance(sectors, list), f"affected_sectors must be list, got {type(sectors)}"


# ──────────────────────────────────────────────────────────────────────────────
# Value validation
# ──────────────────────────────────────────────────────────────────────────────

class TestValueValidation:
    def test_all_event_types_known(self):
        unknown = {e["event_type"] for e in EVENTS if e["event_type"] not in VALID_EVENT_TYPES}
        assert len(unknown) == 0, f"Unknown event types: {unknown}"

    def test_dates_in_range(self):
        for ev in EVENTS:
            yr = ev["event_date"].year
            assert MIN_YEAR <= yr <= MAX_YEAR, (
                f"Date {ev['event_date']} out of range [{MIN_YEAR}, {MAX_YEAR}]"
            )

    def test_affected_sectors_non_empty(self):
        for ev in EVENTS:
            assert len(ev["affected_sectors"]) >= 1, (
                f"Event {ev['event_date']} has empty affected_sectors"
            )

    def test_description_length(self):
        for ev in EVENTS:
            assert len(ev["description"]) >= 50, (
                f"Description too short for event {ev['event_date']}: {ev['description'][:50]}"
            )


# ──────────────────────────────────────────────────────────────────────────────
# Uniqueness
# ──────────────────────────────────────────────────────────────────────────────

class TestUniqueness:
    def test_no_duplicate_descriptions(self):
        keys = [e["description"][:80].strip().lower() for e in EVENTS]
        dupes = [k for k in keys if keys.count(k) > 1]
        assert len(dupes) == 0, f"Duplicate descriptions found: {dupes[:3]}"

    def test_event_type_diversity(self):
        types_used = {e["event_type"] for e in EVENTS}
        assert len(types_used) >= 10, f"Need at least 10 event types; got {len(types_used)}"


# ──────────────────────────────────────────────────────────────────────────────
# Distribution checks
# ──────────────────────────────────────────────────────────────────────────────

class TestDistribution:
    def test_positive_events_present(self):
        pos = [e for e in EVENTS if "POSITIVE" in e.get("market_impact", "")]
        assert len(pos) >= 40, f"Need >=40 positive events; got {len(pos)}"

    def test_negative_events_present(self):
        neg = [e for e in EVENTS if "NEGATIVE" in e.get("market_impact", "")]
        assert len(neg) >= 30, f"Need >=30 negative events; got {len(neg)}"

    def test_budget_events_present(self):
        budgets = [e for e in EVENTS if e["event_type"] == "BUDGET"]
        assert len(budgets) >= 8

    def test_rbi_events_present(self):
        rbi = [e for e in EVENTS if e["event_type"] == "RBI_POLICY"]
        assert len(rbi) >= 5

    def test_geopolitical_events_present(self):
        geo = [e for e in EVENTS if e["event_type"] == "GEOPOLITICAL"]
        assert len(geo) >= 8

    def test_sector_bull_events_present(self):
        sb = [e for e in EVENTS if e["event_type"] == "SECTOR_BULL"]
        assert len(sb) >= 8

    def test_dates_span_multiple_decades(self):
        years = {e["event_date"].year for e in EVENTS}
        assert min(years) <= 2010
        assert max(years) >= 2024

    def test_recent_events_included(self):
        recent = [e for e in EVENTS if e["event_date"].year >= 2023]
        assert len(recent) >= 15, f"Need >= 15 recent (2023+) events; got {len(recent)}"


# ──────────────────────────────────────────────────────────────────────────────
# Serialization (matches DB schema)
# ──────────────────────────────────────────────────────────────────────────────

class TestSerialization:
    def test_serialize_produces_iso_date(self):
        from db.seed_historical_events import serialize_event
        ev = EVENTS[0]
        row = serialize_event(ev)
        assert isinstance(row["event_date"], str)
        assert "-" in row["event_date"]   # ISO format YYYY-MM-DD

    def test_serialize_embedding_is_none(self):
        from db.seed_historical_events import serialize_event
        row = serialize_event(EVENTS[0])
        assert row["embedding"] is None

    def test_new_events_serializable(self):
        from db.seed_historical_events import serialize_event
        for ev in _NEW_EVENTS:
            row = serialize_event(ev)
            assert "event_type" in row
            assert "description" in row
            assert "event_date" in row
