"""tests/test_rationale.py — agent vote rationale builder."""

from agents.rationale import attach_rationales, build_rationale


def _words(text: str) -> int:
    return len(text.split())


class TestFundamental:
    def test_cites_real_metrics(self):
        r = {
            "signal": "BUY", "score": 72, "data_quality": "FULL",
            "data_sources": ["screener_in"],
            "detail": {
                "growth_quality": {"revenue_growth_yoy": 18.4, "roce": 21.3},
                "profitability":  {"pe": 24.1, "sector_pe_effective": 31.0,
                                   "ebitda_margin": 19.5},
                "balance_sheet":  {"debt_equity": 0.34},
                "governance":     {"promoter_holding": 62.1, "promoter_pledging": 0},
            },
        }
        text = build_rationale("fundamental", r)
        assert "18.4%" in text and "21.3%" in text
        assert "24.1x" in text and "31.0x" in text
        assert "discount to peers" in text
        assert "no pledging" in text
        assert _words(text) <= 100

    def test_premium_when_pe_above_sector(self):
        r = {"signal": "HOLD", "score": 55,
             "detail": {"profitability": {"pe": 60.0, "sector_pe_effective": 25.0}}}
        assert "premium to peers" in build_rationale("fundamental", r)

    def test_flags_fallback_data(self):
        r = {"signal": "BUY", "score": 70, "data_quality": "FALLBACK",
             "detail": {"growth_quality": {"roce": 15.0}}}
        assert "fallback data" in build_rationale("fundamental", r).lower()

    def test_flags_estimated_data(self):
        r = {"signal": "BUY", "score": 70, "data_quality": "ESTIMATED", "detail": {}}
        assert "estimated" in build_rationale("fundamental", r).lower()

    def test_surfaces_danger_triggers(self):
        r = {"signal": "AVOID", "score": 30,
             "detail": {"danger": {"triggers": ["high pledging", "falling margins"]}}}
        assert "high pledging" in build_rationale("fundamental", r)


class TestTechnical:
    def test_describes_rsi_zone(self):
        r = {"signal": "BUY", "score": 68,
             "detail": {"indicators": {"rsi": 24.0},
                        "trend_alignment": {"ema_aligned": True, "adx": 31.0},
                        "momentum": {"macd_bullish": True}}}
        text = build_rationale("technical", r)
        assert "oversold" in text
        assert "stacked bullishly" in text
        assert "strong trend" in text
        assert "MACD is above" in text

    def test_overbought_and_weak_trend(self):
        r = {"signal": "HOLD", "score": 50,
             "detail": {"indicators": {"rsi": 78.0},
                        "trend_alignment": {"ema_aligned": False, "adx": 12.0},
                        "momentum": {"macd_bullish": False}}}
        text = build_rationale("technical", r)
        assert "overbought" in text
        assert "not in a bullish alignment" in text
        assert "weak or ranging" in text


class TestOtherAgents:
    def test_sentiment_counts_headlines(self):
        r = {"signal": "BULLISH", "score": 66,
             "detail": {"headlines_analysed": 12,
                        "sentiment_breakdown": {"bullish": 8, "bearish": 2},
                        "insider_signal": "ACCUMULATING"}}
        text = build_rationale("sentiment", r)
        assert "12 recent headlines" in text
        assert "8 bullish, 2 bearish" in text
        assert "accumulating" in text

    def test_institutional_direction(self):
        r = {"signal": "BUY", "score": 70,
             "detail": {"fii": {"net_5d_cr": 1250.0, "buy_streak": 4},
                        "dii": {"net_5d_cr": -300.0}}}
        text = build_rationale("institutional", r)
        assert "net bought" in text
        assert "4-session buying streak" in text
        assert "net selling" in text

    def test_macro_picks_most_extreme_indicators(self):
        r = {"signal": "NEUTRAL", "score": 55,
             "detail": {"india_vix": {"value": 22.0, "score": 20, "note": "elevated volatility"},
                        "dxy":       {"value": 103.0, "score": 49, "note": "flat"},
                        "inr_usd":   {"value": 86.0, "score": 85, "note": "rupee weak"}}}
        text = build_rationale("macro", r)
        assert "elevated volatility" in text     # score 20 -> distance 30
        assert "rupee weak" in text              # score 85 -> distance 35
        assert "flat" not in text                # score 49 -> distance 1, dropped

    def test_rag_uses_reasoning(self):
        r = {"signal": "BULLISH_ANALOGUE", "score": 64,
             "detail": {"reasoning": "3 of 4 analogues resolved positive"},
             "matched_events": [{"description": "2019 NBFC liquidity easing"}]}
        text = build_rationale("historical_rag", r)
        assert "analogues resolved positive" in text
        assert "2019 NBFC" in text


class TestGuards:
    def test_no_data_uses_gate_reason(self):
        r = {"signal": "NO_DATA", "score": 0,
             "reason": "completeness 20% below threshold"}
        text = build_rationale("fundamental", r)
        assert "No usable fundamentals signal" in text
        assert "completeness 20%" in text

    def test_never_raises_on_garbage(self):
        for bad in (None, [], "x", 42, {"detail": "not-a-dict"}, {"score": "abc"}):
            assert isinstance(build_rationale("fundamental", bad), str)

    def test_unknown_agent_still_returns_text(self):
        assert build_rationale("mystery", {"signal": "BUY", "score": 60})

    def test_word_budget_respected(self):
        r = {
            "signal": "BUY", "score": 80, "data_quality": "FULL",
            "data_sources": ["screener_in"],
            "detail": {
                "growth_quality": {"revenue_growth_yoy": 25.0, "roce": 30.0},
                "profitability":  {"pe": 20.0, "sector_pe_effective": 40.0,
                                   "ebitda_margin": 25.0},
                "balance_sheet":  {"debt_equity": 0.1},
                "governance":     {"promoter_holding": 70.0, "promoter_pledging": 5.0},
                "danger":         {"triggers": ["a", "b"]},
            },
        }
        assert _words(build_rationale("fundamental", r)) <= 100


class TestAttachRationales:
    def test_shapes_output_for_db(self):
        out = attach_rationales({
            "fundamental": {"signal": "BUY", "score": 70,
                            "detail": {"growth_quality": {"roce": 20.0}}},
            "technical":   {"signal": "HOLD", "score": 50, "detail": {}},
        })
        assert set(out) == {"fundamental", "technical"}
        for v in out.values():
            assert set(v) == {"signal", "score", "reason"}
            assert isinstance(v["reason"], str) and v["reason"]

    def test_skips_non_dict_entries(self):
        out = attach_rationales({"fundamental": None, "technical": {"signal": "BUY", "score": 1}})
        assert "fundamental" not in out and "technical" in out

    def test_empty_input(self):
        assert attach_rationales({}) == {}
        assert attach_rationales(None) == {}
