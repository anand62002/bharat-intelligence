"""
agents/call_calibration.py — turn a discovery signal score into an honest,
empirically calibrated confidence.

Why this exists
---------------
Discovery used to publish the fundamental agent's 0–100 *quality score* as
"confidence" (`_best_upside` in discovery_screener.py). It is not a probability,
and the track record shows it: across 231 tracked discovery calls (2026-11-10),
the higher the stated confidence the WORSE the call did —

    stated 90%+   → median alpha −4.1%, 10/25 beat Nifty
    stated <70%   → median alpha +0.7%, 23/43 beat Nifty

So the score is kept (as `signal_score`) for what it is good at — deciding which
candidates pass the screen — and the number shown to the user becomes:

    confidence = share of past discovery calls in the same signal band that
                 beat the Nifty 50 at 90 days

That is what "75% confidence" should mean. It is recomputed every day from
`recommendation_outcomes` as more calls resolve, so it moves with the record.

Method
------
  * Outcome label: alpha_t90 > 0 (beat Nifty over 90 days) — the same definition
    as the Performance tab's agent attribution.
  * One call per symbol per 30 days: re-discoveries of the same stock (INFY was
    logged 12 times) would otherwise count one bet many times over.
  * Bands on signal_score; each band's rate is shrunk toward the overall rate
    (Beta prior, PRIOR_STRENGTH pseudo-calls) so a thin band can't swing to 0%
    or 100% on a handful of calls.
  * Fewer than MIN_RESOLVED resolved calls → no track record; returns the
    neutral 50% and says so (`has_history=False`).

Never raises — calibration failure must not block discovery or the re-check.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

log = logging.getLogger(__name__)

# How long a discovery call runs before it must be re-confirmed. 90 days is the
# only horizon with resolved outcomes behind it; the old 4–12 month horizons had
# no measurement at all.
CALL_HORIZON_DAYS = 90

SIGNAL_BANDS: list[tuple[float, float]] = [(0, 70), (70, 80), (80, 90), (90, 101)]
PRIOR_STRENGTH = 10      # pseudo-calls pulling each band toward the overall rate
MIN_RESOLVED   = 30      # below this there is no usable track record
DEDUP_DAYS     = 30      # one call per symbol per window
NEUTRAL        = 50.0


@dataclass
class Calibration:
    bands:       list[dict] = field(default_factory=list)  # {lo, hi, rate, n, hits}
    overall:     float = NEUTRAL                           # percent
    n:           int = 0
    has_history: bool = False
    as_of:       str = field(default_factory=lambda: date.today().isoformat())

    def confidence(self, signal_score: Optional[float]) -> float:
        """Calibrated P(beat Nifty at 90d) in percent for a signal score."""
        if not self.has_history or signal_score is None:
            return round(self.overall if self.has_history else NEUTRAL, 1)
        s = float(signal_score)
        for b in self.bands:
            if b["lo"] <= s < b["hi"]:
                return b["rate"]
        return round(self.overall, 1)

    def band_n(self, signal_score: Optional[float]) -> int:
        if signal_score is None:
            return self.n
        for b in self.bands:
            if b["lo"] <= float(signal_score) < b["hi"]:
                return b["n"]
        return self.n

    def summary(self) -> dict:
        return {
            "as_of": self.as_of, "n": self.n, "overall": self.overall,
            "has_history": self.has_history, "bands": self.bands,
        }


def signal_score_of(row: dict) -> Optional[float]:
    """
    The raw screening score of a recommendation row.

    New rows carry metadata.signal_score. Rows written before 2026-11-10 stored
    the raw score in `upside_confidence` — the re-check job copies it into
    metadata.signal_score BEFORE overwriting confidence, so this fallback is
    only ever reached for rows that have not been re-checked yet.
    """
    meta = row.get("metadata") or {}
    v = meta.get("signal_score")
    if v is None:
        v = row.get("upside_confidence")
    if v is None:
        v = row.get("confidence")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def build_calibration(pairs: list[tuple[dict, dict]]) -> Calibration:
    """
    Pure function: (recommendation_row, outcome_row) pairs → Calibration.
    Kept separate from the DB read so it can be tested directly.
    """
    resolved = []
    for rec, out in pairs:
        alpha = out.get("alpha_t90")
        score = signal_score_of(rec)
        rec_date = str(out.get("rec_date") or rec.get("created_at") or "")[:10]
        if alpha is None or score is None or not rec_date:
            continue
        resolved.append((rec.get("symbol") or out.get("symbol"), rec_date, score, float(alpha) > 0))

    # One call per symbol per DEDUP_DAYS window — keep the first in each window.
    resolved.sort(key=lambda t: (t[0], t[1]))
    kept: list[tuple[float, bool]] = []
    last_kept: dict[str, date] = {}
    for sym, d, score, hit in resolved:
        try:
            dd = date.fromisoformat(d)
        except ValueError:
            continue
        prev = last_kept.get(sym)
        if prev is not None and (dd - prev).days < DEDUP_DAYS:
            continue
        last_kept[sym] = dd
        kept.append((score, hit))

    n = len(kept)
    if n < MIN_RESOLVED:
        return Calibration(n=n, has_history=False)

    hits_total = sum(1 for _, h in kept if h)
    p0 = hits_total / n
    bands = []
    for lo, hi in SIGNAL_BANDS:
        in_band = [h for s, h in kept if lo <= s < hi]
        bn, bh = len(in_band), sum(in_band)
        rate = (bh + PRIOR_STRENGTH * p0) / (bn + PRIOR_STRENGTH)
        bands.append({"lo": lo, "hi": hi, "n": bn, "hits": bh, "rate": round(rate * 100, 1)})
    return Calibration(bands=bands, overall=round(p0 * 100, 1), n=n, has_history=True)


def load_calibration(client: Any) -> Calibration:
    """Read resolved discovery outcomes from Supabase and calibrate. Never raises."""
    try:
        recs: dict[str, dict] = {}
        off = 0
        while True:
            page = (client.table("recommendations")
                    .select("id,symbol,created_at,confidence,upside_confidence,metadata")
                    .eq("is_discovery", True)
                    .range(off, off + 999).execute().data or [])
            recs.update({r["id"]: r for r in page})
            if len(page) < 1000:
                break
            off += 1000

        outs: list[dict] = []
        off = 0
        while True:
            page = (client.table("recommendation_outcomes")
                    .select("rec_id,symbol,rec_date,alpha_t90")
                    .not_.is_("alpha_t90", "null")
                    .range(off, off + 999).execute().data or [])
            outs += page
            if len(page) < 1000:
                break
            off += 1000

        pairs = [(recs[o["rec_id"]], o) for o in outs if o.get("rec_id") in recs]
        cal = build_calibration(pairs)
        log.info("call_calibration: n=%d overall=%.1f%% has_history=%s bands=%s",
                 cal.n, cal.overall, cal.has_history,
                 [(b["lo"], b["rate"], b["n"]) for b in cal.bands])
        return cal
    except Exception as exc:
        log.warning("call_calibration: failed (%s) — using neutral %.0f%%", exc, NEUTRAL)
        return Calibration()
