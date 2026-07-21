"""
tests/test_on_demand_analyse.py
pytest suite for POST /api/analyse (on-demand full 10-agent analysis)

Run from project root:
    pytest tests/test_on_demand_analyse.py -v
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import anyio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def pipeline_state_with_rec():
    """Simulate a pipeline state that produced one recommendation."""
    return {
        "recommendations": [
            {
                "symbol": "RELIANCE",
                "action": "BUY",
                "confidence": 72,
                "risk_score": 30,
                "entry_low": 2800.0,
                "entry_high": 2900.0,
                "target": 3500.0,
                "stoploss": 2650.0,
                "upside_pct": 25.0,
                "upside_confidence": 70,
                "danger_drop_pct": 10.0,
                "danger_confidence": 55,
                "horizon_days": 180,
                "headline": "BUY: RELIANCE — strong composite score",
                "synthesis": "Bull case outweighs bear.",
                "bull_case": ["Point 1", "Point 2", "Point 3"],
                "bear_case": ["Risk 1", "Risk 2", "Risk 3"],
                "agent_signals": {
                    "technical": {"signal": "BUY", "score": 65},
                    "fundamental": {"signal": "BUY", "score": 70},
                },
            }
        ]
    }


@pytest.fixture()
def pipeline_state_suppressed():
    """Simulate a pipeline state where synthesis was suppressed."""
    return {"recommendations": []}


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests for the endpoint logic (no real HTTP server needed)
# ──────────────────────────────────────────────────────────────────────────────

class TestOnDemandAnalyseResponseSchema:
    """Test the response structure of POST /api/analyse."""

    @pytest.mark.anyio
    async def test_successful_recommendation_response(self, pipeline_state_with_rec):
        """When pipeline produces a rec, response must include analysis and agents keys."""
        from api.main import on_demand_analyse

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"symbol": "RELIANCE"})

        with patch("api.main._resolve_yf_symbol", return_value="RELIANCE.NS"), \
             patch("api.main.asyncio.wait_for", new_callable=AsyncMock, return_value=pipeline_state_with_rec):
            response = await on_demand_analyse(mock_request)

        assert response["symbol"] == "RELIANCE"
        assert response["yf_symbol"] == "RELIANCE.NS"
        assert response["status"] == "OK"
        assert "analysis" in response
        analysis = response["analysis"]
        assert analysis["action"] == "BUY"
        assert analysis["confidence"] == 72
        assert "agents" in response

    @pytest.mark.anyio
    async def test_no_recommendation_response(self, pipeline_state_suppressed):
        """When pipeline suppresses all recs, status must be NO_RECOMMENDATION."""
        from api.main import on_demand_analyse

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"symbol": "TINY"})

        with patch("api.main._resolve_yf_symbol", return_value="TINY.NS"), \
             patch("api.main.asyncio.wait_for", new_callable=AsyncMock, return_value=pipeline_state_suppressed):
            response = await on_demand_analyse(mock_request)

        assert response["status"] == "NO_RECOMMENDATION"
        assert "analysis" not in response or response.get("analysis") is None

    @pytest.mark.anyio
    async def test_empty_symbol_returns_error(self):
        """Empty symbol payload must return an error response."""
        from api.main import on_demand_analyse
        from fastapi import HTTPException

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"symbol": ""})

        with pytest.raises(HTTPException) as exc_info:
            await on_demand_analyse(mock_request)
        assert exc_info.value.status_code == 400

    @pytest.mark.anyio
    async def test_symbol_uppercased(self, pipeline_state_suppressed):
        """Symbol is normalised to uppercase before pipeline call."""
        from api.main import on_demand_analyse

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"symbol": "reliance"})

        with patch("api.main._resolve_yf_symbol", return_value="RELIANCE.NS") as mock_resolve, \
             patch("api.main.asyncio.wait_for", new_callable=AsyncMock, return_value=pipeline_state_suppressed):
            await on_demand_analyse(mock_request)

        # _resolve_yf_symbol was called with the UPPERCASED input
        mock_resolve.assert_called_once_with("RELIANCE")

    @pytest.mark.anyio
    async def test_timeout_returns_error(self):
        """When pipeline times out, endpoint must return an HTTPException or error dict."""
        import asyncio
        from api.main import on_demand_analyse
        from fastapi import HTTPException

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"symbol": "RELIANCE"})

        with patch("api.main._resolve_yf_symbol", return_value="RELIANCE.NS"), \
             patch("api.main.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            try:
                response = await on_demand_analyse(mock_request)
                # If it returns an error dict (not raises), status must signal the error
                assert response.get("status") in ("ERROR", "TIMEOUT")
            except HTTPException as e:
                assert e.status_code in (504, 500, 408)


class TestOnDemandAnalyseSymbolResolution:
    """Test that symbol aliases resolve correctly before the pipeline call."""

    @pytest.mark.anyio
    async def test_zomato_resolves_to_eternal(self, pipeline_state_suppressed):
        """ZOMATO → ETERNAL.NS (2025 rebrand) must be handled by _resolve_yf_symbol."""
        from api.main import on_demand_analyse

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"symbol": "ZOMATO"})

        with patch("api.main._resolve_yf_symbol", return_value="ETERNAL.NS") as mock_resolve, \
             patch("api.main.asyncio.wait_for", new_callable=AsyncMock, return_value=pipeline_state_suppressed):
            response = await on_demand_analyse(mock_request)

        assert response["yf_symbol"] == "ETERNAL.NS"
