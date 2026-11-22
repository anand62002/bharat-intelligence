"""
tests/test_options_fetcher.py — option metrics without ICICI Breeze (WP-024, ADR-008).

Breeze needed a manual daily session token and Trendlyne F&O already supplies
the same chain metrics, so the Breeze branch, data/breeze_auth.py and the
08:35 token-refresh job were removed. Source order is now
Trendlyne F&O → NSE → India VIX fallback.
"""
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data import options_fetcher as of  # noqa: E402

FALLBACK = {"pcr": None, "max_pain": None, "atm_iv": None, "iv_skew": None, "india_vix": 14.2,
            "hv20": 18.0, "iv_hv_ratio": 0.79, "underlying_price": 24000.0, "source": "fallback"}


def test_trendlyne_first():
    tl = {"error": None, "pcr": 1.1, "max_pain": 24000, "atm_iv": 13.0, "iv_skew": 1.2,
          "ann_vol": 0.15, "spot": 24050}
    with patch("data.trendlyne_fno_fetcher.get_option_metrics", return_value=tl), \
         patch.object(of, "_fallback_metrics", return_value=dict(FALLBACK)):
        out = of.get_option_metrics("NIFTY")
    assert out["source"] == "trendlyne_fno" and out["pcr"] == 1.1


def test_nse_when_trendlyne_unavailable():
    records = {"underlyingValue": 2850.0, "data": []}
    with patch("data.trendlyne_fno_fetcher.get_option_metrics", return_value={"error": "no session"}), \
         patch.object(of, "_fetch_nse_option_chain", return_value=records), \
         patch.object(of, "_compute_pcr", return_value=0.9), \
         patch.object(of, "_compute_max_pain", return_value=2800.0), \
         patch.object(of, "_compute_atm_iv_and_skew", return_value=(22.0, 1.5)):
        out = of.get_option_metrics("RELIANCE")
    assert out["source"] == "nse" and out["pcr"] == 0.9 and out["underlying_price"] == 2850.0


def test_vix_fallback_when_nothing_else_works():
    with patch("data.trendlyne_fno_fetcher.get_option_metrics", side_effect=RuntimeError("down")), \
         patch.object(of, "_fetch_nse_option_chain", return_value=None), \
         patch.object(of, "_fallback_metrics", return_value=dict(FALLBACK)):
        out = of.get_option_metrics("TITAN.NS")
    assert out["source"] == "fallback" and out["symbol"] == "TITAN"


def test_breeze_is_gone():
    assert not hasattr(of, "_fetch_breeze_option_chain")
    assert not hasattr(of, "_breeze_cache")
    assert not (ROOT / "data" / "breeze_auth.py").exists()
    assert "breeze" not in (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()


def test_worker_no_longer_schedules_a_breeze_job():
    import worker
    assert not hasattr(worker, "job_breeze_token_refresh")
    ids = {j.id for j in worker.build_scheduler().get_jobs()}
    assert "breeze_token_refresh" not in ids and "orchestrator" in ids


# ── Trendlyne F&O credentials: environment only, failures named ──────────────

def test_trendlyne_missing_cookies_fail_clearly(monkeypatch):
    import pytest
    from data import trendlyne_fno_fetcher as tl
    monkeypatch.delenv("TRENDLYNE_SESSION", raising=False)
    monkeypatch.delenv("TRENDLYNE_CSRF", raising=False)
    with pytest.raises(RuntimeError, match="Trendlyne cookies not set"):
        tl._make_fno_session()


def test_trendlyne_html_reply_is_named_not_a_zip_error():
    import pytest
    from data import trendlyne_fno_fetcher as tl
    with pytest.raises(RuntimeError, match="session cookie is missing or expired"):
        tl._require_excel(b"<!DOCTYPE html><html>login</html>", "text/html; charset=utf-8")
    tl._require_excel(b"PK\x03\x04rest-of-xlsx")          # a real .xlsx passes


def test_cookies_are_read_from_the_environment(monkeypatch):
    from data import trendlyne_fno_fetcher as tl
    monkeypatch.setenv("TRENDLYNE_SESSION", "sess-x")
    monkeypatch.setenv("TRENDLYNE_CSRF", "csrf-y")
    s = tl._make_fno_session()
    assert s.cookies.get(".trendlyne") == "sess-x" and s.cookies.get("csrftoken") == "csrf-y"


def test_no_hard_coded_trendlyne_cookie_in_tracked_code():
    import subprocess
    leaked = ("bce34ncrlhwelezjn884vaxvyh0g543w", "8pNPblqCbfTikxjVbCLRZwDbQq6cdZZw")
    tracked = subprocess.run(["git", "ls-files", "*.py", "*.js", "*.jsx", "*.json", "*.toml"],
                             cwd=ROOT, capture_output=True, text=True).stdout.split()
    hits = [f for f in tracked if f != "tests/test_options_fetcher.py"
            and any(v in (ROOT / f).read_text(encoding="utf-8", errors="ignore") for v in leaked)]
    assert not hits, hits
