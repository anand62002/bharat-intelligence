// ─── LONG-HORIZON MULTIBAGGER TRACK (P8-E) ───────────────────────────────────
// Self-contained so it survives dashboard restyles: colours (C) and the
// authenticated fetch helper are passed in from App.jsx.
//
// Backend: GET /api/long_horizon (agents/long_horizon_book.get_summary).
// Separate from the 90-day paper portfolio: quality compounders held 1–3 years,
// wide stops, and larger sizing that stays LOCKED until this track's own
// 1-year outcomes earn it.
import { useEffect, useState } from "react";

const MONO = "JetBrains Mono, ui-monospace, monospace";
const fmtINR = (v) => `₹${Math.round(v || 0).toLocaleString("en-IN")}`;
const pct = (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(1)}%`);

export default function LongHorizonPanel({ C, apiFetch, isLive }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    if (!isLive) return;
    apiFetch("/api/long_horizon").then(setData).catch((e) => setErr(String(e)));
  }, [apiFetch, isLive]);

  const box = { border: `1px solid ${C.border}`, borderRadius: 8, marginTop: 14, overflow: "hidden" };
  const head = { display: "flex", alignItems: "center", gap: 8, padding: "10px 13px", cursor: "pointer", background: C.surface };

  const rec = data?.track_record;
  const sum = data?.summary;
  const rules = data?.rules;
  const posByPick = Object.fromEntries((data?.positions || []).map((p) => [p.pick_id, p]));
  const picks = (data?.picks || []).filter((p) => p.status === "ACTIVE" || posByPick[p.id]);
  const statusColor = { ACTIVE: C.green, THESIS_BROKEN: C.red, EXITED: C.muted };

  return (
    <div style={box}>
      <div style={head} onClick={() => setOpen((o) => !o)}>
        <span style={{ fontSize: 12, fontWeight: 700, color: C.text }}>🌱 Long-Horizon Track</span>
        <span style={{ fontSize: 10.5, color: C.muted }}>quality compounders · 1–3 yr holds · separate paper book</span>
        {sum && (
          <span style={{ marginLeft: "auto", fontSize: 11, fontFamily: MONO, color: (sum.total_pnl_pct || 0) >= 0 ? C.green : C.red }}>
            {pct(sum.total_pnl_pct)} total · best {sum.best_multiple || 0}×
          </span>
        )}
        <span style={{ fontSize: 11, color: C.muted, marginLeft: sum ? 8 : "auto" }}>{open ? "▾" : "▸"}</span>
      </div>

      {open && (
        <div style={{ padding: "10px 13px" }}>
          {!isLive && <div style={{ fontSize: 11, color: C.muted }}>Connect the backend to see the long-horizon track.</div>}
          {err && <div style={{ fontSize: 11, color: C.red }}>Could not load: {err}</div>}
          {data?.migration_needed && (
            <div style={{ fontSize: 11, color: C.orange || C.accent }}>
              Database tables not created yet — run <code>{data.migration}</code> in the Supabase SQL editor.
            </div>
          )}

          {rec && rules && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
              <Tile C={C} label="Active picks" value={sum.active_picks} />
              <Tile C={C} label="Open positions" value={`${sum.open_positions} · ${fmtINR(sum.invested_open)}`} />
              <Tile C={C} label="1-yr record" value={rec.hit_rate_1y == null ? "none yet" : `${rec.hit_rate_1y}% beat Nifty`} sub={rec.unlock_progress} />
              <Tile
                C={C}
                label="Conviction sizing"
                value={rec.conviction_unlocked ? `UNLOCKED · ${fmtINR(rules.conviction_allocation)}` : `LOCKED · ${fmtINR(rules.base_allocation)}/pick`}
                sub={rec.unlock_rule}
                color={rec.conviction_unlocked ? C.green : C.muted}
              />
            </div>
          )}

          {rules && (
            <div style={{ fontSize: 10.5, color: C.muted, marginBottom: 8 }}>
              Picks score ≥{rules.pick_threshold}/100 · stop −{rules.hard_stop_pct}% from entry · once a pick doubles,
              the stop trails {rules.trail_pct}% below its peak · exits early only if the quality thesis breaks
            </div>
          )}

          {data && !data.migration_needed && picks.length === 0 && (
            <div style={{ fontSize: 11, color: C.muted, padding: "8px 0" }}>
              No picks yet — the screen runs daily at 12:30 IST on the discovery slice and is deliberately strict.
            </div>
          )}

          {picks.length > 0 && (
            <div style={{ border: `1px solid ${C.border}`, borderRadius: 6, overflow: "auto", maxHeight: "50vh" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <thead>
                  <tr style={{ background: C.panel, position: "sticky", top: 0 }}>
                    {["Symbol", "Score", "Picked", "Entry", "Now", "Return", "Stop", "Size", "Status"].map((h) => (
                      <th key={h} style={{ textAlign: "left", padding: "5px 8px", fontSize: 10, color: C.muted, fontWeight: 700, textTransform: "uppercase" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {picks.map((p) => {
                    const pos = posByPick[p.id];
                    const ret = pos ? (pos.status === "CLOSED" ? pos.realized_pnl_pct : pos.unrealized_pnl_pct) : null;
                    const comps = Object.entries(p.components || {}).map(([k, c]) => `${k} ${c.points}/${c.max}`).join(" · ");
                    return (
                      <tr key={p.id} style={{ borderTop: `1px solid ${C.border}33` }} title={`${p.thesis || ""}\n${comps}`}>
                        <td style={{ padding: "5px 8px", fontFamily: MONO, fontWeight: 700, color: C.text }}>{p.symbol}</td>
                        <td style={{ padding: "5px 8px", fontFamily: MONO }}>{Math.round(p.score)}</td>
                        <td style={{ padding: "5px 8px", color: C.muted }}>{String(p.pick_date).slice(0, 10)}</td>
                        <td style={{ padding: "5px 8px", fontFamily: MONO }}>{pos ? fmtINR(pos.entry_price) : "—"}</td>
                        <td style={{ padding: "5px 8px", fontFamily: MONO }}>{pos ? fmtINR(pos.status === "CLOSED" ? pos.exit_price : pos.current_price) : "—"}</td>
                        <td style={{ padding: "5px 8px", fontFamily: MONO, color: (ret || 0) >= 0 ? C.green : C.red }}>{pct(ret)}</td>
                        <td style={{ padding: "5px 8px", fontFamily: MONO, color: C.muted }}>{pos && pos.status === "OPEN" ? fmtINR(pos.stop_price) : "—"}</td>
                        <td style={{ padding: "5px 8px", color: C.muted }}>{pos ? `${pos.tier} ${fmtINR(pos.allocation_inr)}` : "opens 16:50"}</td>
                        <td style={{ padding: "5px 8px", fontWeight: 700, color: statusColor[p.status] || C.muted }} title={p.status_reason || pos?.exit_reason || ""}>
                          {pos?.status === "CLOSED" ? `CLOSED · ${pos.exit_reason}` : p.status}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Tile({ C, label, value, sub, color }) {
  return (
    <div style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 6, padding: "6px 10px", minWidth: 150 }}>
      <div style={{ fontSize: 9.5, color: C.muted, textTransform: "uppercase", fontWeight: 700 }}>{label}</div>
      <div style={{ fontSize: 12, fontWeight: 700, color: color || C.text, fontFamily: MONO }}>{value}</div>
      {sub && <div style={{ fontSize: 9.5, color: C.muted, marginTop: 1 }}>{sub}</div>}
    </div>
  );
}
