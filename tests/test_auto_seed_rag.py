"""
tests/test_auto_seed_rag.py — Unit tests for db/auto_seed_rag.py

All network / Supabase / OpenAI calls are mocked.
Tests cover:
  - _is_relevant()                 keyword pre-filter
  - _parse_pub_date()              RFC-2822 and partial date parsing
  - _build_embedding_text()        text serialisation
  - _classify_keyword_fallback()   pure rule-based classifier
  - _deduplicate_articles()        duplicate removal against existing events
  - run()                          full pipeline with mocked DB + network
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

# ── imports under test ────────────────────────────────────────────────────────
from db.auto_seed_rag import (
    _is_relevant,
    _parse_pub_date,
    _build_embedding_text,
    _classify_keyword_fallback,
    _deduplicate_articles,
    run,
    _VALID_EVENT_TYPES,
    _VALID_IMPACTS,
)


# ═════════════════════════════════════════════════════════════════════════════
# _is_relevant
# ═════════════════════════════════════════════════════════════════════════════

class TestIsRelevant:
    def test_rbi_keyword_match(self):
        assert _is_relevant("RBI cuts repo rate by 25 bps", "") is True

    def test_fii_keyword_match(self):
        assert _is_relevant("FII sells ₹5000 Cr in cash market", "Foreign investors pulling out") is True

    def test_budget_keyword_match(self):
        assert _is_relevant("Budget 2026: Finance Minister announces LTCG changes", "") is True

    def test_inflation_keyword_match(self):
        assert _is_relevant("India CPI inflation rises to 6.2%", "") is True

    def test_geopolitical_keyword_match(self):
        assert _is_relevant("India tariff dispute with US escalates", "Trade war concerns") is True

    def test_irrelevant_company_news(self):
        assert _is_relevant("ITC launches new cigarette brand in Gujarat", "ITC Ltd announced") is False

    def test_irrelevant_sports_news(self):
        assert _is_relevant("India wins cricket world cup", "India defeated Australia") is False

    def test_case_insensitive(self):
        assert _is_relevant("REPO RATE CUT BY RBI", "") is True

    def test_snippet_matched(self):
        # Title is irrelevant, but snippet has the keyword
        assert _is_relevant("Breaking news today", "RBI monetary policy statement released") is True

    def test_empty_inputs(self):
        assert _is_relevant("", "") is False

    def test_circuit_breaker_keyword(self):
        assert _is_relevant("NSE triggers circuit breaker on Nifty", "") is True

    def test_oil_price_keyword(self):
        assert _is_relevant("Crude oil prices surge to $90", "OPEC cut production") is True


# ═════════════════════════════════════════════════════════════════════════════
# _parse_pub_date
# ═════════════════════════════════════════════════════════════════════════════

class TestParsePubDate:
    def test_rfc2822_with_timezone_offset(self):
        d = _parse_pub_date("Mon, 12 May 2026 10:30:00 +0530")
        assert d == date(2026, 5, 12)

    def test_rfc2822_with_gmt(self):
        d = _parse_pub_date("Mon, 12 May 2026 05:00:00 GMT")
        assert d == date(2026, 5, 12)

    def test_rfc2822_without_weekday(self):
        d = _parse_pub_date("12 May 2026 10:30:00 +0530")
        assert d == date(2026, 5, 12)

    def test_fallback_regex_extraction(self):
        d = _parse_pub_date("Published: 5 Jan 2026 something extra")
        assert d == date(2026, 1, 5)

    def test_invalid_string_returns_none(self):
        assert _parse_pub_date("not a date at all") is None

    def test_empty_string_returns_none(self):
        assert _parse_pub_date("") is None

    def test_none_input_returns_none(self):
        assert _parse_pub_date(None) is None  # type: ignore[arg-type]

    def test_different_months(self):
        d = _parse_pub_date("Fri, 01 Jan 2026 00:00:00 +0000")
        assert d == date(2026, 1, 1)

        d2 = _parse_pub_date("Thu, 31 Dec 2025 23:59:59 +0000")
        assert d2 == date(2025, 12, 31)


# ═════════════════════════════════════════════════════════════════════════════
# _build_embedding_text
# ═════════════════════════════════════════════════════════════════════════════

class TestBuildEmbeddingText:
    def test_all_fields_present(self):
        row = {
            "event_type":      "RBI_RATE_CHANGE",
            "description":     "RBI cuts repo rate by 25 bps to stimulate economy",
            "market_impact":   "MILD_POSITIVE",
            "outcome":         "Markets rallied 2% on rate cut announcement",
            "affected_sectors": ["Banking", "Real Estate"],
        }
        text = _build_embedding_text(row)
        assert "RBI_RATE_CHANGE" in text
        assert "repo rate" in text
        assert "MILD_POSITIVE" in text
        assert "rallied" in text
        assert "Banking" in text

    def test_missing_fields_handled_gracefully(self):
        row = {"description": "RBI rate cut"}
        text = _build_embedding_text(row)
        assert "RBI rate cut" in text

    def test_empty_row_returns_empty_string(self):
        text = _build_embedding_text({})
        assert text == ""

    def test_sectors_as_list_joined(self):
        row = {
            "description": "Event",
            "affected_sectors": ["Banking", "IT", "FMCG"],
        }
        text = _build_embedding_text(row)
        assert "Banking" in text
        assert "IT" in text

    def test_sectors_as_string_not_crash(self):
        row = {
            "description": "Event",
            "affected_sectors": "Banking, IT",
        }
        text = _build_embedding_text(row)
        assert "Banking" in text

    def test_pipe_separator(self):
        row = {
            "event_type": "BUDGET",
            "description": "Budget announced",
            "outcome": "Markets up 3%",
        }
        text = _build_embedding_text(row)
        assert " | " in text


# ═════════════════════════════════════════════════════════════════════════════
# _classify_keyword_fallback
# ═════════════════════════════════════════════════════════════════════════════

class TestClassifyKeywordFallback:
    def _clf(self, title: str, snippet: str = "") -> dict:
        return _classify_keyword_fallback(title, snippet)

    # ── event_type ────────────────────────────────────────────────────────────
    def test_rbi_rate_cut(self):
        r = self._clf("RBI cuts repo rate by 25 basis points")
        assert r["event_type"] == "RBI_RATE_CHANGE"

    def test_rbi_rate_hike(self):
        r = self._clf("RBI raises repo rate by 50 bps to tighten policy")
        assert r["event_type"] == "RBI_RATE_CHANGE"

    def test_rbi_policy_statement(self):
        r = self._clf("RBI keeps policy rates unchanged, maintains stance")
        assert r["event_type"] == "RBI_POLICY_STATEMENT"

    def test_rbi_regulatory(self):
        r = self._clf("RBI issues new circular on NBFC guidelines")
        assert r["event_type"] == "RBI_REGULATORY"

    def test_budget(self):
        r = self._clf("Union Budget 2026: Finance Minister presents economy plan")
        assert r["event_type"] == "BUDGET"

    def test_fii_selloff(self):
        r = self._clf("FII sells ₹8000 crore from Indian equities amid outflow")
        assert r["event_type"] == "FII_SELLOFF"

    def test_fii_buying(self):
        r = self._clf("FII inflow surges — foreign investors buy ₹5000 Cr")
        assert r["event_type"] == "FII_BUYING"

    def test_inflation_data(self):
        r = self._clf("India CPI inflation rises to 6.5% in April")
        assert r["event_type"] == "INFLATION_DATA"

    def test_gdp_data(self):
        r = self._clf("India GDP growth slows to 5.8% in Q3")
        assert r["event_type"] == "GDP_DATA"

    def test_oil_price_shock(self):
        r = self._clf("Crude oil prices surge to $90 on OPEC cut")
        assert r["event_type"] == "OIL_PRICE_SHOCK"

    def test_geopolitical(self):
        r = self._clf("US imposes new tariffs, trade war escalates")
        assert r["event_type"] == "GEOPOLITICAL"

    def test_currency_crisis(self):
        r = self._clf("Rupee falls to all-time low, currency depreciation concerns")
        assert r["event_type"] == "CURRENCY_CRISIS"

    def test_currency_rally(self):
        # Headline must not contain FII keywords so currency branch fires first
        r = self._clf("Indian rupee hits 3-month high at 82 per dollar on strong capital flows")
        assert r["event_type"] == "CURRENCY_RALLY"

    def test_us_fed_decision(self):
        r = self._clf("US Federal Reserve FOMC keeps rates unchanged at 5.25%")
        assert r["event_type"] == "US_FED_DECISION"

    def test_nbfc_crisis(self):
        r = self._clf("Yes Bank crisis deepens as NBFC defaults mount")
        assert r["event_type"] == "NBFC_CRISIS"

    def test_sebi_regulation(self):
        r = self._clf("SEBI tightens F&O margin requirements with new circular")
        assert r["event_type"] == "SEBI_REGULATION"

    # ── market_impact ─────────────────────────────────────────────────────────
    def test_positive_impact_on_rate_cut(self):
        r = self._clf("RBI rate cut boosts markets, Nifty rallies strongly")
        assert r["market_impact"] in ("MILD_POSITIVE", "STRONG_POSITIVE")

    def test_negative_impact_on_crash(self):
        r = self._clf("Market crash: Nifty falls 5%, panic selloff and fear")
        assert r["market_impact"] in ("MODERATE_NEGATIVE", "SEVERE_NEGATIVE")

    def test_circuit_breaker_severe(self):
        r = self._clf("NSE triggers circuit breaker as market crashes in panic")
        assert r["market_impact"] in ("MODERATE_NEGATIVE", "SEVERE_NEGATIVE")

    def test_neutral_default(self):
        r = self._clf("RBI keeps repo rate unchanged")
        # Should be neutral without strong positive/negative signals
        assert r["market_impact"] in ("NEUTRAL", "MILD_POSITIVE", "MODERATE_NEGATIVE")

    # ── schema validity ───────────────────────────────────────────────────────
    def test_output_schema_complete(self):
        r = self._clf("RBI cuts rates")
        assert "event_type"       in r
        assert "market_impact"    in r
        assert "affected_sectors" in r
        assert "outcome"          in r
        assert "relevance_score"  in r
        assert "is_significant"   in r

    def test_event_type_always_valid(self):
        titles = [
            "RBI rate cut", "Budget 2026", "FII outflow", "Nifty crash",
            "India GDP slowdown", "Crude oil surge", "Trade war tariff",
            "Random news today",
        ]
        for title in titles:
            r = self._clf(title)
            assert r["event_type"] in _VALID_EVENT_TYPES, \
                f"Invalid event_type '{r['event_type']}' for title: {title}"

    def test_market_impact_always_valid(self):
        titles = [
            "Market rally boost positive", "Market crash selloff fear panic",
            "Neutral stable update",
        ]
        for title in titles:
            r = self._clf(title)
            assert r["market_impact"] in _VALID_IMPACTS, \
                f"Invalid impact '{r['market_impact']}' for title: {title}"

    def test_affected_sectors_is_list(self):
        r = self._clf("RBI rate cut")
        assert isinstance(r["affected_sectors"], list)
        assert len(r["affected_sectors"]) >= 1

    def test_relevance_score_is_float(self):
        r = self._clf("RBI rate cut")
        assert isinstance(r["relevance_score"], float)
        assert 0.0 <= r["relevance_score"] <= 1.0

    def test_is_significant_is_bool(self):
        r = self._clf("RBI rate cut")
        assert isinstance(r["is_significant"], bool)


# ═════════════════════════════════════════════════════════════════════════════
# _deduplicate_articles
# ═════════════════════════════════════════════════════════════════════════════

class TestDeduplicateArticles:

    def test_empty_articles_returns_empty(self):
        result = _deduplicate_articles([], [], window_days=7)
        assert result == []

    def test_no_existing_events_keeps_all(self):
        articles = [
            {"event_type": "BUDGET",          "event_date": date(2026, 2, 1),  "title": "Budget"},
            {"event_type": "RBI_RATE_CHANGE",  "event_date": date(2026, 4, 10), "title": "RBI cut"},
        ]
        result = _deduplicate_articles(articles, [], window_days=7)
        assert len(result) == 2

    def test_exact_duplicate_dropped(self):
        articles = [
            {"event_type": "RBI_RATE_CHANGE", "event_date": date(2026, 5, 9), "title": "RBI cuts"},
        ]
        existing = [{"event_type": "RBI_RATE_CHANGE", "event_date": "2026-05-09"}]
        result = _deduplicate_articles(articles, existing, window_days=7)
        assert len(result) == 0

    def test_within_window_dropped(self):
        articles = [
            {"event_type": "RBI_RATE_CHANGE", "event_date": date(2026, 5, 10), "title": "RBI"},
        ]
        existing = [{"event_type": "RBI_RATE_CHANGE", "event_date": "2026-05-07"}]  # 3 days before
        result = _deduplicate_articles(articles, existing, window_days=7)
        assert len(result) == 0

    def test_outside_window_kept(self):
        articles = [
            {"event_type": "RBI_RATE_CHANGE", "event_date": date(2026, 5, 20), "title": "RBI"},
        ]
        existing = [{"event_type": "RBI_RATE_CHANGE", "event_date": "2026-04-01"}]  # 49 days before
        result = _deduplicate_articles(articles, existing, window_days=7)
        assert len(result) == 1

    def test_different_event_type_same_date_kept(self):
        articles = [
            {"event_type": "INFLATION_DATA", "event_date": date(2026, 5, 9), "title": "CPI data"},
        ]
        existing = [{"event_type": "RBI_RATE_CHANGE", "event_date": "2026-05-09"}]
        result = _deduplicate_articles(articles, existing, window_days=7)
        assert len(result) == 1

    def test_same_type_different_months_kept(self):
        articles = [
            {"event_type": "RBI_RATE_CHANGE", "event_date": date(2026, 7, 10), "title": "RBI July"},
        ]
        existing = [{"event_type": "RBI_RATE_CHANGE", "event_date": "2026-05-09"}]
        result = _deduplicate_articles(articles, existing, window_days=7)
        assert len(result) == 1

    def test_article_without_date_kept(self):
        """Articles without a date field are included (can't verify, give benefit of doubt)."""
        articles = [{"event_type": "BUDGET", "title": "Budget news"}]  # no event_date
        existing = [{"event_type": "BUDGET", "event_date": "2026-05-01"}]
        result = _deduplicate_articles(articles, existing, window_days=7)
        assert len(result) == 1

    def test_existing_events_with_bad_date_ignored(self):
        """Existing events with unparseable dates don't crash dedup."""
        articles = [
            {"event_type": "BUDGET", "event_date": date(2026, 2, 1), "title": "Budget"}
        ]
        existing = [{"event_type": "BUDGET", "event_date": "not-a-date"}]
        result = _deduplicate_articles(articles, existing, window_days=7)
        assert len(result) == 1  # bad date in existing ignored → article kept

    def test_window_boundary_exact(self):
        """Article exactly 7 days from existing is still dropped (|Δ| ≤ window)."""
        articles = [
            {"event_type": "FII_SELLOFF", "event_date": date(2026, 5, 16), "title": "FII"},
        ]
        existing = [{"event_type": "FII_SELLOFF", "event_date": "2026-05-09"}]  # 7 days
        result = _deduplicate_articles(articles, existing, window_days=7)
        assert len(result) == 0

    def test_window_boundary_plus_one_kept(self):
        """Article 8 days from existing is kept (|Δ| > window)."""
        articles = [
            {"event_type": "FII_SELLOFF", "event_date": date(2026, 5, 17), "title": "FII"},
        ]
        existing = [{"event_type": "FII_SELLOFF", "event_date": "2026-05-09"}]  # 8 days
        result = _deduplicate_articles(articles, existing, window_days=7)
        assert len(result) == 1

    def test_multiple_articles_partial_deduplicate(self):
        articles = [
            {"event_type": "RBI_RATE_CHANGE", "event_date": date(2026, 5, 9), "title": "RBI dup"},
            {"event_type": "BUDGET",           "event_date": date(2026, 2, 1), "title": "Budget new"},
            {"event_type": "INFLATION_DATA",   "event_date": date(2026, 5, 9), "title": "CPI new"},
        ]
        existing = [{"event_type": "RBI_RATE_CHANGE", "event_date": "2026-05-09"}]
        result = _deduplicate_articles(articles, existing, window_days=7)
        assert len(result) == 2
        types = {a["event_type"] for a in result}
        assert "BUDGET" in types
        assert "INFLATION_DATA" in types
        assert "RBI_RATE_CHANGE" not in types


# ═════════════════════════════════════════════════════════════════════════════
# run() — integration tests (all I/O mocked)
# ═════════════════════════════════════════════════════════════════════════════

class TestRun:
    """Integration tests for the run() pipeline with fully mocked I/O."""

    def _mock_articles(self) -> list[dict]:
        return [
            {
                "title":    "RBI cuts repo rate by 25 bps to stimulate growth",
                "snippet":  "The Reserve Bank of India reduced the repo rate amid slowing economy.",
                "pub_date": "Mon, 12 May 2026 10:00:00 +0530",
                "link":     "https://example.com/rbi-cut",
            },
            {
                "title":    "FII sells ₹6000 crore in Indian equities on outflow concerns",
                "snippet":  "Foreign institutional investors have been net sellers this week.",
                "pub_date": "Mon, 12 May 2026 09:00:00 +0530",
                "link":     "https://example.com/fii-sell",
            },
            {
                "title":    "Cricket: India beats Australia in final",
                "snippet":  "India won the match by 5 wickets.",
                "pub_date": "Mon, 12 May 2026 08:00:00 +0530",
                "link":     "https://example.com/cricket",
            },
        ]

    @patch("db.auto_seed_rag._fetch_all_articles")
    def test_dry_run_returns_correct_counts(self, mock_fetch):
        """Dry run should NOT call Supabase insert but should return add count."""
        mock_fetch.return_value = self._mock_articles()

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value \
            .gte.return_value.execute.return_value.data = []

        with patch("db.auto_seed_rag._get_supabase_client", return_value=mock_client), \
             patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
            result = run(days=35, max_new=30, dry_run=True)

        assert result["dry_run"] is True
        # cricket article filtered, 2 relevant remain → 2 would be added
        assert result["added"] == 2
        assert result["skipped_irrelevant"] >= 1   # cricket article filtered
        assert result["errors"] == 0

    @patch("db.auto_seed_rag._fetch_all_articles")
    def test_live_run_inserts_events(self, mock_fetch):
        """Live run with no existing events should insert all relevant articles."""
        mock_fetch.return_value = self._mock_articles()

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value \
            .gte.return_value.execute.return_value.data = []
        mock_client.table.return_value.insert.return_value.execute.return_value.data = [{}]

        with patch("db.auto_seed_rag._get_supabase_client", return_value=mock_client), \
             patch.dict("os.environ", {}, clear=False):
            result = run(days=35, max_new=30, dry_run=False)

        assert result["dry_run"] is False
        assert result["added"] == 2
        assert result["errors"] == 0
        assert mock_client.table.return_value.insert.call_count == 2

    @patch("db.auto_seed_rag._fetch_all_articles")
    def test_deduplication_skips_existing_event(self, mock_fetch):
        """Article with same event_type within 7 days of existing row is skipped."""
        mock_fetch.return_value = [self._mock_articles()[0]]   # RBI cut only

        mock_client = MagicMock()
        # Existing DB entry: RBI_RATE_CHANGE 3 days ago
        mock_client.table.return_value.select.return_value \
            .gte.return_value.execute.return_value.data = [
                {"event_type": "RBI_RATE_CHANGE", "event_date": "2026-05-09"}
            ]

        # Do NOT patch the date class — doing so breaks isinstance(ed, date) inside dedup.
        # The article pub_date "Mon, 12 May 2026 10:00:00 +0530" parses to 2026-05-12,
        # which is within 7 days of the existing "2026-05-09" → should be dropped.
        with patch("db.auto_seed_rag._get_supabase_client", return_value=mock_client):
            result = run(days=35, max_new=30, dry_run=False)

        assert result["skipped_duplicate"] >= 1
        assert result["added"] == 0

    @patch("db.auto_seed_rag._fetch_all_articles")
    def test_no_articles_returns_zero_counts(self, mock_fetch):
        mock_fetch.return_value = []
        result = run(days=35, max_new=30, dry_run=True)
        assert result["added"] == 0
        assert result["articles_checked"] == 0

    @patch("db.auto_seed_rag._fetch_all_articles")
    def test_max_new_cap_respected(self, mock_fetch):
        """Only max_new events are inserted even if more are available."""
        # 5 unique relevant articles
        articles = [
            {
                "title":    f"RBI rate decision event {i} important policy",
                "snippet":  f"Reserve Bank of India monetary policy event {i}.",
                "pub_date": f"Mon, 0{i+1} May 2026 10:00:00 +0530",
                "link":     f"https://example.com/rbi-{i}",
            }
            for i in range(1, 6)
        ]
        mock_fetch.return_value = articles

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value \
            .gte.return_value.execute.return_value.data = []
        mock_client.table.return_value.insert.return_value.execute.return_value.data = [{}]

        with patch("db.auto_seed_rag._get_supabase_client", return_value=mock_client):
            result = run(days=35, max_new=2, dry_run=False)

        assert result["added"] <= 2

    @patch("db.auto_seed_rag._fetch_all_articles")
    def test_supabase_not_configured_returns_error(self, mock_fetch):
        mock_fetch.return_value = self._mock_articles()
        with patch("db.auto_seed_rag._get_supabase_client",
                   side_effect=RuntimeError("SUPABASE_URL not set")):
            result = run(days=35, max_new=30, dry_run=False)
        assert result["errors"] >= 1
        assert result["added"] == 0

    @patch("db.auto_seed_rag._fetch_all_articles")
    def test_result_schema_keys(self, mock_fetch):
        mock_fetch.return_value = []
        result = run(days=35, max_new=30, dry_run=True)
        assert "added"              in result
        assert "skipped_duplicate"  in result
        assert "skipped_irrelevant" in result
        assert "errors"             in result
        assert "dry_run"            in result
        assert "articles_checked"   in result

    @patch("db.auto_seed_rag._fetch_all_articles")
    @patch("db.auto_seed_rag._classify_llm")
    def test_llm_classification_used_when_openai_key_set(self, mock_clf, mock_fetch):
        """When OPENAI_API_KEY is set, _classify_llm should be called."""
        mock_fetch.return_value = [self._mock_articles()[0]]
        mock_clf.return_value = {
            "event_type":      "RBI_RATE_CHANGE",
            "market_impact":   "MILD_POSITIVE",
            "affected_sectors": ["Banking"],
            "outcome":         "Markets rallied on the rate cut news.",
            "relevance_score": 0.85,
            "is_significant":  True,
        }

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value \
            .gte.return_value.execute.return_value.data = []
        mock_client.table.return_value.insert.return_value.execute.return_value.data = [{}]

        with patch("db.auto_seed_rag._get_supabase_client", return_value=mock_client), \
             patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            result = run(days=35, max_new=30, dry_run=False)

        mock_clf.assert_called()
        assert result["added"] == 1

    @patch("db.auto_seed_rag._fetch_all_articles")
    @patch("db.auto_seed_rag._embed_openai")
    def test_embedding_generated_when_openai_key_set(self, mock_embed, mock_fetch):
        """Embeddings should be requested from OpenAI when key is available."""
        mock_fetch.return_value = [self._mock_articles()[0]]
        mock_embed.return_value = [0.1] * 1536

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value \
            .gte.return_value.execute.return_value.data = []
        mock_client.table.return_value.insert.return_value.execute.return_value.data = [{}]

        with patch("db.auto_seed_rag._get_supabase_client", return_value=mock_client), \
             patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            run(days=35, max_new=30, dry_run=False)

        mock_embed.assert_called()

    @patch("db.auto_seed_rag._fetch_all_articles")
    def test_no_openai_key_still_inserts_without_embedding(self, mock_fetch):
        """Without OpenAI key, events are inserted with embedding=None."""
        mock_fetch.return_value = [self._mock_articles()[0]]

        inserted_rows: list[dict] = []

        def capture_insert(row):
            inserted_rows.append(row)
            m = MagicMock()
            m.execute.return_value.data = [{}]
            return m

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value \
            .gte.return_value.execute.return_value.data = []
        mock_client.table.return_value.insert.side_effect = capture_insert

        with patch("db.auto_seed_rag._get_supabase_client", return_value=mock_client), \
             patch.dict("os.environ", {}, clear=False):
            # Ensure OPENAI_API_KEY is not set
            import os
            os.environ.pop("OPENAI_API_KEY", None)
            result = run(days=35, max_new=30, dry_run=False)

        assert result["added"] == 1
        assert inserted_rows[0]["embedding"] is None
