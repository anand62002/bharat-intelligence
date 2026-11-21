import { useState, useEffect, useRef, useCallback } from "react";
import LongHorizonPanel from "./LongHorizonPanel";

// Editorial Research palette (Claude Design canvas, direction C). Light "paper"
// ground, serif display type, one saffron-rust accent. Every value is a 6-digit
// hex because call sites append 2-digit alpha (C.green+"22") for tints.
// Semantic colours are darkened so text meets 4.5:1 on the paper surfaces.
const C = {
  bg:"#f6f3ec", surface:"#fffdf8", panel:"#efe9dc", border:"#e2dccd", borderHi:"#c9c0aa",
  accent:"#b4441a", accentDim:"#8f3514", green:"#17703f", red:"#b4232c",
  blue:"#1f5c99", purple:"#6b3fa0", teal:"#2b7a78", orange:"#8a4b00",
  cyan:"#1f6f8b", lime:"#4d7c0f",
  muted:"#6b6457", text:"#1c1a16", textDim:"#5c564b",
  onFill:"#ffffff",
};

// Type families: serif for headings and theses, sans for UI, mono for numbers.
const SERIF = "'Source Serif 4',Georgia,serif";
const SANS  = "Inter,system-ui,sans-serif";
const MONO  = "'JetBrains Mono',ui-monospace,monospace";

const FONT_STYLE = `
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,400;8..60,500;8..60,600;8..60,700&family=JetBrains+Mono:wght@400;500;600&display=swap');
  body{background:#f6f3ec;color:#1c1a16;-webkit-font-smoothing:antialiased;}
  button,input,select,textarea{font-family:inherit;}
  button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid #b4441a;outline-offset:2px;}
  input::placeholder,textarea::placeholder{color:#6b6457;}
  *{box-sizing:border-box;margin:0;padding:0;}
  /* 3px scrollbars were effectively invisible and ungrabbable — lists looked
     like they did not scroll. Wide enough to see and drag, still unobtrusive. */
  ::-webkit-scrollbar{width:9px;height:9px;}
  ::-webkit-scrollbar-track{background:transparent;}
  ::-webkit-scrollbar-thumb{background:#c9c0aa;border-radius:5px;border:2px solid transparent;background-clip:padding-box;}
  ::-webkit-scrollbar-thumb:hover{background:#8c857a;background-clip:padding-box;}
  *{scrollbar-width:thin;scrollbar-color:#c9c0aa transparent;}
  @keyframes fadeUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
  @keyframes dotBounce{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-6px)}}
  @keyframes slideIn{from{opacity:0;transform:translateX(20px)}to{opacity:1;transform:translateX(0)}}
  @keyframes glow{0%,100%{box-shadow:0 0 0 1px #b4441a33}50%{box-shadow:0 0 0 2px #b4441a55}}
  @keyframes scanline{0%{transform:translateY(-100%)}100%{transform:translateY(400%)}}
  @keyframes criticalPulse{0%,100%{box-shadow:0 0 0 0 #10B98144,0 0 12px #10B98133}50%{box-shadow:0 0 0 3px #10B98122,0 0 24px #10B98166}}
  @keyframes dangerPulse{0%,100%{box-shadow:0 0 0 0 #EF444444,0 0 12px #EF444433}50%{box-shadow:0 0 0 3px #EF444422,0 0 24px #EF444466}}
  @keyframes criticalBadge{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.85;transform:scale(1.04)}}
  .aria-glow{box-shadow:-12px 0 32px rgba(28,26,22,.08);}
  .portfolio-alert{animation:glow 2s infinite;}
  .ed-card:hover{background:#fffdf8!important;}
  .research-card:hover{border-color:#c9c0aa!important;background:#fffdf8;transition:all .15s;}
  .critical-discovery{box-shadow:0 0 0 1px #17703f55;}
  .critical-danger{box-shadow:0 0 0 1px #b4232c66;}
  .tabs-scroll::-webkit-scrollbar{display:none;}
  .ticker-scroll::-webkit-scrollbar{display:none;}
`;

// ─── API CONFIGURATION ────────────────────────────────────────────────────────
// Set REACT_APP_API_URL (e.g. http://localhost:8000 or https://api.yourdomain.com)
// and REACT_APP_API_KEY in dashboard/.env to connect to the FastAPI backend.
// When API_URL is empty the dashboard keeps displaying the built-in mock data (local dev only).
const API_URL = (process.env.REACT_APP_API_URL || "").replace(/\/$/, "");
const API_KEY = process.env.REACT_APP_API_KEY  || "";
// IS_LIVE: true when a backend URL is configured.
// When IS_LIVE, all states start empty and NEVER fall back to mock constants —
// users see a proper empty-state placeholder instead of fabricated data.
const IS_LIVE = Boolean(API_URL);

/**
 * Authenticated fetch helper.
 * Returns parsed JSON or throws on non-2xx.
 * Silently skips when API_URL is not configured.
 */
const apiFetch = (path, opts = {}) =>
  fetch(`${API_URL}${path}`, {
    ...opts,
    headers: {
      ...(API_KEY ? { "x-api-key": API_KEY } : {}),
      "Content-Type": "application/json",
      ...(opts.headers || {}),
    },
  }).then(r => {
    if (!r.ok) throw new Error(`API ${r.status} ${path}`);
    return r.json();
  });

/** First non-empty text block of an ARIA (Anthropic Messages) response.
 *  content[0] is not guaranteed to be text — a model that thinks returns a
 *  `thinking` block first. */
const ariaText = (data) =>
  (data?.content || []).find(b => b?.type === "text" && b.text)?.text || "";

// ─── CRITICAL THRESHOLDS ─────────────────────────────────────────────────────
// Discovery: flag if upsidePct >= 100 AND upsideConfidence >= 70
// Portfolio danger: flag if dangerDropPct >= 70 AND dangerConfidence >= 65 within 2-3 months
// CRITICAL is decided server-side (calibrated hit rate ≥60% AND ≥40% upside) —
// `confidence` is now a historical hit rate, so a client-side 75% threshold
// would never fire and would contradict the tier the backend assigned.
const isCriticalDiscovery = (s) => s.opportunityTier === "CRITICAL";

/** Exit marker per holding (agents/target_updater.exit_signal). Advisory only. */
const EXIT_META = {
  EXIT:         {label:"EXIT",         icon:"⛔", color:"#EF4444", rank:0, hint:"Price is at/below the stop"},
  BOOK_PARTIAL: {label:"BOOK PARTIAL", icon:"💰", color:"#F97316", rank:1, hint:"Past target and fully valued — consider booking part"},
  REVIEW:       {label:"REVIEW",       icon:"🔎", color:"#EAB308", rank:2, hint:"Laggard — re-check the thesis"},
  TRAIL:        {label:"TRAIL",        icon:"📈", color:"#22C55E", rank:3, hint:"Past target, fair value still higher — ride the trailing stop"},
  HOLD:         {label:"HOLD",         icon:"·",  color:"#64748B", rank:4, hint:"Within plan"},
};

/** "Called 2026-10-18 · 23d · +4.1% since" — the call's lifecycle in one line. */
const callLine = (s) => {
  const parts = [];
  if (s.callDate) parts.push(`Called ${s.callDate}`);
  if (s.daysActive != null) parts.push(`${s.daysActive}d`);
  if (s.returnSinceCallPct != null) parts.push(`${s.returnSinceCallPct >= 0 ? "+" : ""}${s.returnSinceCallPct.toFixed(1)}% since`);
  return parts.join(" · ");
};
const hitRateTitle = (s) =>
  s.hasTrackRecord === false
    ? "No track record yet — shown as a neutral 50%."
    : `Share of past discovery calls in this signal band that beat the Nifty 50 at 90 days`
      + (s.calibrationN ? ` (n=${s.calibrationN}).` : ".")
      + (s.signalScore != null ? ` Raw signal score: ${Math.round(s.signalScore)}.` : "");
const isCriticalDanger    = (h) => h.dangerDropPct >= 70 && h.dangerConfidence >= 65;

// LIVE_PRICES removed — optimistic portfolio adds now use avgBuy as the temporary
// price until the backend returns the live yfinance-refreshed value.

// ─── DISCOVERY UNIVERSE — stocks NOT in portfolio, screened by agents ─────────
const DISCOVERY_UNIVERSE = [
  {
    id:"d1", symbol:"DIXON", name:"Dixon Technologies", sector:"Electronics/PLI",
    price:15840, change:3.2, pe:68, mktCap:"₹95,400 Cr",
    discoveryScore:87, discoveryReason:"PLI scheme electronics beneficiary + Apple supply chain entry",
    action:"BUY", confidence:81, riskScore:38, horizon:"4–7 months",
    entry:"₹15,200–₹15,800", target:"₹19,500", stoploss:"₹13,800",
    validTill:"2025-07-31",
    // Critical upside fields
    upsidePct:123, upsideConfidence:76,
    upsideHorizon:"9–12 months",
    upsideBasis:"PLI full-cycle revenue ₹35,000 Cr by FY27 at current growth. Apple India supply chain localisation hitting 25% by FY26. Historical: Amber Enterprises 4x in 14 months on identical trigger. Consensus analyst target: ₹35,200 (median of 8 analysts).",
    screenTriggers:["FII net buy ₹840 Cr in 30d","Revenue +94% YoY","Apple iPhone component order confirmed","PLI payout Q3 FY25 received"],
    agents:{
      technical:    {signal:"BUY",         score:79, detail:"Weekly breakout above ₹15,500 resistance. Volume 3.2x average. RSI 64 — room to run."},
      fundamental:  {signal:"BUY",         score:88, detail:"Revenue ₹17,690 Cr FY25 vs ₹9,100 Cr FY24. EBITDA margin expanding. PLI incentive ₹400 Cr recognised."},
      sentiment:    {signal:"BUY",         score:74, detail:"Google Trends for 'Dixon Technologies' +280% MoM. 6 analyst upgrades in 30 days. Management tone bullish."},
      institutional:{signal:"BUY",         score:83, detail:"3 major MFs added 2.1% stake. FII ownership at 18.4% (up from 14.2% in Dec). No promoter selling."},
      macro:        {signal:"POSITIVE",    score:72, detail:"China+1 supply chain shift accelerating. India PLI electronics ₹41,000 Cr scheme. USD/INR stable = export benefit."},
      historical:   {signal:"BUY",         score:76, detail:"Amber Enterprises ran +180% in 12 months after similar PLI trigger in 2021. Dixon setup closely mirrors it."},
    },
    risks:["High PE (68x) — priced for perfection","Apple order size smaller than expected","Component import cost rise if INR depreciates","Competition from Tata Electronics"],
    catalysts:["Q1 FY26 results (July 25)","New PLI tranche announcement","Apple India supply chain expansion news"],
    govCheck:{verified:true, hallucinations:0, sourceQuality:89, lastChecked:"09:10 IST", flags:[]},
    notInPortfolio:true,
  },
  {
    id:"d2", symbol:"POLYCAB", name:"Polycab India", sector:"Cables & Wires",
    price:6720, change:1.1, pe:42, mktCap:"₹1.01L Cr",
    discoveryScore:82, discoveryReason:"Infrastructure capex supercycle + EV charging grid buildout",
    action:"BUY", confidence:74, riskScore:31, horizon:"5–8 months",
    entry:"₹6,400–₹6,700", target:"₹8,200", stoploss:"₹5,900",
    validTill:"2025-08-15",
    // No critical upside flag (upside ~22% to target — solid but not 100%+)
    upsidePct:22, upsideConfidence:74,
    upsideHorizon:"5–8 months",
    upsideBasis:"Target ₹8,200 represents 22% upside from current levels driven by infra capex. Strong but below the 100% critical threshold.",
    screenTriggers:["Govt capex ₹11L Cr FY25 on track","EV charging infra tenders ₹8,400 Cr","FII net buy 8 consecutive sessions","FMEG segment revenue doubled YoY"],
    agents:{
      technical:    {signal:"BUY",         score:74, detail:"Cup-and-handle pattern on weekly chart. 50 DMA acting as support. RSI 57 — healthy."},
      fundamental:  {signal:"BUY",         score:84, detail:"Market share 25% in wires. EBITDA ₹1,890 Cr (+22% YoY). Debt-free. ROCE 31%. FMEG growing 42% YoY."},
      sentiment:    {signal:"POSITIVE",    score:68, detail:"Infra sector sentiment elevated post Budget capex reaffirmation. Polycab mentioned in 4 govt tender documents."},
      institutional:{signal:"BUY",         score:79, detail:"LIC increased holding to 6.4%. HDFC MF largest MF holder. Promoter buying on dips (₹12 Cr in March)."},
      macro:        {signal:"POSITIVE",    score:77, detail:"India power sector ₹4L Cr investments planned FY25–27. EV penetration at 6.4% driving charging infra demand."},
      historical:   {signal:"POSITIVE",    score:71, detail:"Havells ran +130% during 2017–2018 infra capex supercycle. Polycab at comparable stage in its own cycle now."},
    },
    risks:["Copper price spike (key input)","Govt capex delay","FMEG competitive pressure from Havells/Legrand","GST rate change on cables"],
    catalysts:["Q4 FY25 results (May 15)","Govt infra tender wins","EV charging tender awards"],
    govCheck:{verified:true, hallucinations:0, sourceQuality:92, lastChecked:"09:05 IST", flags:[]},
    notInPortfolio:true,
  },
  {
    id:"d3", symbol:"IRFC", name:"Indian Railway Finance Corp", sector:"PSU Finance",
    price:186, change:-0.5, pe:29, mktCap:"₹2.43L Cr",
    discoveryScore:73, discoveryReason:"Railway capex ₹2.65L Cr FY25 — IRFC is the funding arm",
    action:"BUY", confidence:69, riskScore:22, horizon:"6–9 months",
    entry:"₹178–₹190", target:"₹240", stoploss:"₹158",
    validTill:"2025-09-30",
    // Moderate upside — not critical
    upsidePct:29, upsideConfidence:69,
    upsideHorizon:"6–9 months",
    upsideBasis:"₹240 target = 29% upside. PFC/REC analog suggests potential for larger move over 18–24 months but near-term upside below critical threshold.",
    screenTriggers:["Railway budget ₹2.65L Cr — highest ever","Borrowing programme fully subscribed","Dividend yield 1.1% at CMP","Zero NPA model — pure pass-through"],
    agents:{
      technical:    {signal:"NEUTRAL",     score:52, detail:"Range-bound ₹175–₹200 for 3 months. Needs breakout above ₹200 to confirm next leg. Accumulation pattern."},
      fundamental:  {signal:"BUY",         score:82, detail:"NIM 1.4% (narrow but guaranteed). Zero credit risk — lends only to Railways. Book value growing 18% YoY."},
      sentiment:    {signal:"POSITIVE",    score:65, detail:"Railway infra narrative strong post Budget. 3 analyst initiations in March with target ₹230–₹260."},
      institutional:{signal:"BUY",         score:76, detail:"LIC holds 2.8%. DII net buyers. Retail investor SIP flows into PSU ETFs continue to support."},
      macro:        {signal:"POSITIVE",    score:74, detail:"Rate cut cycle = lower borrowing cost for IRFC = margin expansion. Govt committed to railway modernisation."},
      historical:   {signal:"POSITIVE",    score:68, detail:"PFC/REC rallied 200%+ during 2022–2024 power sector capex cycle. IRFC at similar early stage."},
    },
    risks:["Govt capex slowdown pre-election","Rate reversal","Illiquidity vs other PSU financials","Slow NIM expansion timeline"],
    catalysts:["RBI rate cut (margin expansion)","Q4 FY25 book value update","New railway capex announcement"],
    govCheck:{verified:true, hallucinations:1, sourceQuality:86, lastChecked:"08:55 IST", flags:["Railway budget figure sourced from press release — verify on indiabudget.gov.in"]},
    notInPortfolio:true,
  },
  {
    id:"d4", symbol:"ZOMATO", name:"Zomato", sector:"Consumer Internet",
    price:212, change:2.8, pe:280, mktCap:"₹1.87L Cr",
    discoveryScore:68, discoveryReason:"Quick commerce Blinkit hitting profitability + food delivery monopoly",
    action:"HOLD", confidence:58, riskScore:61, horizon:"3–5 months",
    entry:"₹195–₹215", target:"₹265", stoploss:"₹172",
    validTill:"2025-06-15",
    upsidePct:25, upsideConfidence:58,
    upsideHorizon:"3–5 months",
    upsideBasis:"₹265 target from current ₹212 = 25% upside. Low conviction — HOLD, not BUY. High PE risk limits confidence in larger moves.",
    screenTriggers:["Blinkit GMV +100% YoY","Food delivery order frequency at ATH","B2B Hyperpure revenue tripling","FII momentum buying"],
    agents:{
      technical:    {signal:"BUY",         score:71, detail:"Breaking above ₹210 resistance. 200 DMA at ₹172 is distant support. RSI 66 approaching overbought."},
      fundamental:  {signal:"NEUTRAL",     score:44, detail:"PAT positive for 3 quarters but PE of 280x prices in perfection. Blinkit burn rate still elevated. Cash runway 18 months."},
      sentiment:    {signal:"POSITIVE",    score:72, detail:"Consumer tech sentiment improving. Blinkit brand recognition surpassing Zepto in surveys. Social buzz elevated."},
      institutional:{signal:"BUY",         score:68, detail:"Tiger Global, Ant Group partial exits absorbed by FIIs. Domestic MF buying continued. Float remains high."},
      macro:        {signal:"NEUTRAL",     score:55, detail:"Urban consumption strong but premium. Rising food inflation could pressure order frequency. Dark store expansion needs permits."},
      historical:   {signal:"NEUTRAL",     score:48, detail:"High-PE consumer internet stocks globally compressed 60–80% in 2022. Zomato already corrected 70% — base is low."},
    },
    risks:["Extremely high valuation (280x PE)","Swiggy ONDC competition","Regulatory risk for dark stores","Profitability not yet structural"],
    catalysts:["Q4 FY25 results (May 12)","Blinkit EBITDA break-even","New city expansion"],
    govCheck:{verified:true, hallucinations:0, sourceQuality:83, lastChecked:"09:00 IST", flags:[]},
    notInPortfolio:true,
  },
];

// ─── PORTFOLIO ────────────────────────────────────────────────────────────────
const DEFAULT_PORTFOLIO = [
  {
    id:"p1",symbol:"RELIANCE",name:"Reliance Industries",sector:"Energy",
    qty:15,avgBuy:2680,currentPrice:2847.5,buyDate:"2024-11-12",linkedRecId:null,
    notes:"Core holding",targetPrice:3100,stoplossPrice:2450,status:"holding",
    // No critical danger
    dangerDropPct:0, dangerConfidence:0, dangerTrigger:null, dangerWindow:null,
  },
  {
    id:"p2",symbol:"TATAMOTORS",name:"Tata Motors",sector:"Auto",
    qty:50,avgBuy:808,currentPrice:812.0,buyDate:"2025-03-10",linkedRecId:1,
    notes:"Acting on BUY rec",targetPrice:980,stoplossPrice:740,status:"holding",
    // No critical danger
    dangerDropPct:0, dangerConfidence:0, dangerTrigger:null, dangerWindow:null,
  },
  {
    id:"p3",symbol:"GOLDBEES",name:"Gold BeES ETF",sector:"Commodity",
    qty:8,avgBuy:5790,currentPrice:5824.0,buyDate:"2025-02-28",linkedRecId:3,
    notes:"Gold allocation",targetPrice:6800,stoplossPrice:5400,status:"holding",
    dangerDropPct:0, dangerConfidence:0, dangerTrigger:null, dangerWindow:null,
  },
  {
    id:"p4",symbol:"PAYTM",name:"One97 Communications (Paytm)",sector:"Fintech",
    qty:120,avgBuy:620,currentPrice:415.0,buyDate:"2024-08-05",linkedRecId:null,
    notes:"Bought on dip after PPBL ban",targetPrice:750,stoplossPrice:320,status:"holding",
    // ⚠ CRITICAL DANGER — 70%+ drop predicted within 2-3 months
    dangerDropPct:74, dangerConfidence:71,
    dangerWindow:"6–10 weeks",
    dangerTrigger:"SEBI investigation into KYC violations confirmed. RBI reviewing remaining payment aggregator licence. FII holding collapsed from 18% → 4.2% in 60 days. 3 major MFs fully exited. Revenue -38% YoY as merchant partners migrate to Razorpay/PhonePe. If payment aggregator licence is revoked, book value support disappears — stock could retest ₹108 (FY23 lows). Governance cross-checked: SEBI SCORES portal shows 3 pending regulatory notices. High conviction danger signal.",
    dangerSources:["SEBI enforcement order dated Mar 18 (BSE filing)","RBI payment aggregator review circular","MF portfolio disclosures Feb 2025 — 4 exits","NSE bulk deal data — FII selling ₹1,840 Cr in 45 days"],
  },
];

// ─── EXISTING RECOMMENDATIONS (from portfolio stocks) ────────────────────────
const PORTFOLIO_RECOMMENDATIONS = [
  {
    id:1,symbol:"TATAMOTORS",action:"BUY",confidence:78,horizon:"3–6 months",
    entry:"₹790–₹820",entryLow:790,entryHigh:820,target:"₹980",targetNum:980,stoploss:"₹740",stoplossNum:740,
    riskScore:42,validTill:"2025-06-30",headline:"EV cycle upturn + strong JLR order book",
    summary:"Tata Motors is at an inflection point — JLR reported record order book of 150,000 units. Domestic EV market share at 61%. PLI scheme tailwinds.",
    agents:{
      technical:{signal:"BUY",score:72,detail:"Weekly Inverse H&S breakout above ₹800. RSI(14) at 58. 200 DMA ₹740 = strong floor."},
      fundamental:{signal:"BUY",score:82,detail:"Revenue +18% YoY. EBITDA margin +200bps. JLR D/E improved 1.4x→0.9x. EV volumes +42% QoQ."},
      sentiment:{signal:"NEUTRAL",score:55,detail:"News 62% positive. Social mentions +35% WoW. Concall NLP tone score 7.4/10."},
      institutional:{signal:"BUY",score:81,detail:"FII net buy ₹1,240 Cr last 30d. 3 MFs increased stake. Promoter holding stable 46.4%."},
      macro:{signal:"POSITIVE",score:70,detail:"Crude $78 manageable. INR 83.5 stable. RBI cut likely Q2. EV supply chain easing."},
      historical:{signal:"POSITIVE",score:76,detail:"Apr 2021 analog: stock +140% over 8 months post earnings beat."},
    },
    risks:["JLR production disruption","Commodity cost spike","UK recession risk","EV competition Hyundai/MG"],
    catalysts:["Q4 FY25 earnings Apr 25","JLR monthly sales","PLI disbursements"],
    govCheck:{verified:true,hallucinations:0,sourceQuality:91,lastChecked:"09:15 IST",flags:[]},
  },
  {
    id:2,symbol:"SUNPHARMA",action:"HOLD",confidence:64,horizon:"2–4 months",
    entry:"₹1,580–₹1,620",entryLow:1580,entryHigh:1620,target:"₹1,820",targetNum:1820,stoploss:"₹1,480",stoplossNum:1480,
    riskScore:31,validTill:"2025-05-15",headline:"US specialty pharma traction + India branded generics strength",
    summary:"Sun Pharma US specialty pipeline gaining traction. India business +12% vs industry 8%. EBITDA margins 27% — best in class.",
    agents:{
      technical:{signal:"NEUTRAL",score:50,detail:"Consolidating ₹1,580–₹1,680 for 6 weeks. MACD crossover expected within 2 weeks."},
      fundamental:{signal:"BUY",score:80,detail:"US specialty $450M run rate. Ilumya +30% YoY in US. India Rx market share #1. ROCE 22%."},
      sentiment:{signal:"POSITIVE",score:72,detail:"USFDA approval for Winlevi extension. Zero warning letters. 4 analyst upgrades last 30d."},
      institutional:{signal:"HOLD",score:58,detail:"FII slight net sell ₹320 Cr. DIIs accumulating. MF SIP steady."},
      macro:{signal:"POSITIVE",score:68,detail:"INR depreciation benefits pharma exporters. US healthcare stable. China+1 play."},
      historical:{signal:"NEUTRAL",score:55,detail:"Sun consolidates 3–4 months after 30%+ run. Pattern matches 2018 and 2021 cycles."},
    },
    risks:["USFDA inspection Halol plant","INR appreciation","US generics pricing pressure","Guidance miss"],
    catalysts:["Q4 FY25 results May 10","New USFDA approvals","India pharma policy"],
    govCheck:{verified:true,hallucinations:1,sourceQuality:84,lastChecked:"08:50 IST",flags:["Halol inspection date unverified"]},
  },
  {
    id:3,symbol:"GOLDBEES",action:"BUY",confidence:71,horizon:"4–8 months",
    entry:"₹5,750–₹5,850",entryLow:5750,entryHigh:5850,target:"₹6,800",targetNum:6800,stoploss:"₹5,400",stoplossNum:5400,
    riskScore:28,validTill:"2025-08-30",headline:"Gold in structural bull run — Fed cuts + geopolitical premium",
    summary:"Gold driven by de-dollarization, Fed rate cut cycle, geopolitical uncertainty. INR gold has additional currency depreciation tailwind.",
    agents:{
      technical:{signal:"BUY",score:78,detail:"International gold broke $2,450 ATH. MCX Gold above all EMAs. RSI 65."},
      fundamental:{signal:"BUY",score:75,detail:"Central bank buying 50yr high (1,037t). India imports 750–800t/yr. ETF flows positive."},
      sentiment:{signal:"BUY",score:70,detail:"Google Trends 'buy gold India' +45% MoM. WGC Q1 FY25 jewelry demand +11% YoY."},
      institutional:{signal:"BUY",score:82,detail:"RBI added 19t in March. SGBs oversubscribed 3x. FIIs increasing gold ETF allocations."},
      macro:{signal:"VERY POSITIVE",score:85,detail:"US real yields declining. DXY weakening. Fed 2–3 cuts 2025. Middle East premium."},
      historical:{signal:"BUY",score:80,detail:"2019–2020 Fed cut cycle: gold +40%. India gold ₹35k→₹56k in 18 months."},
    },
    risks:["Sharp USD strengthening","RBI gold import duty cut","INR appreciation","Risk-on rally"],
    catalysts:["Next FOMC meeting","US CPI data","Union Budget import duty"],
    govCheck:{verified:true,hallucinations:0,sourceQuality:95,lastChecked:"09:20 IST",flags:[]},
  },
];

const MARKET_PULSE = [
  {key:"NIFTY 50",value:"22,147",change:"+0.6%",up:true},
  {key:"SENSEX",value:"73,088",change:"+0.5%",up:true},
  {key:"NIFTY BANK",value:"47,320",change:"-0.3%",up:false},
  {key:"GOLD MCX",value:"₹72,840",change:"+0.3%",up:true},
  {key:"CRUDE MCX",value:"₹6,240",change:"-0.5%",up:false},
  {key:"INR/USD",value:"83.47",change:"-0.1%",up:false},
  {key:"FII NET",value:"+₹1,840 Cr",change:"buy",up:true},
  {key:"INDIA VIX",value:"14.2",change:"-0.8",up:false},
];

// NEWS_FEED removed — replaced by empty-state placeholder in the Market tab.
// A live news integration is planned as a future enhancement.

const GOV_ALERTS = [
  {id:"G1",severity:"warning",module:"Sentiment Agent",title:"Coordinated misinformation detected — TATAMOTORS",detail:"3 Reddit posts about TATAMOTORS UK factory closure appear coordinated. Cross-referenced with BSE filings — no such announcement. Signal down-weighted 40%.",action:"Source quarantined. Confidence maintained at 78%.",time:"10:44 IST",resolved:true},
  {id:"G2",severity:"info",module:"Historical Analogy Engine",title:"Low confidence historical match — SUNPHARMA 2018",detail:"Pattern match confidence 61% (threshold 70%). 2018 context differed — applied with reduced weighting.",action:"Historical score reduced from 72 to 55.",time:"09:30 IST",resolved:true},
  {id:"G3",severity:"critical",module:"Fundamental Agent",title:"Stale data — HDFCBANK Q3 FY25 balance sheet used",detail:"Q4 FY25 results published yesterday. Analysis must be rerun before recommendation issued.",action:"HDFCBANK recommendation withheld pending refresh.",time:"08:15 IST",resolved:false},
];

// ─── GOVERNANCE RESEARCH AGENT DATA ──────────────────────────────────────────
const AI_RESEARCH_FEED = [
  {
    id:"r1", type:"whitepaper", date:"2025-03-18",
    source:"arXiv / Stanford CRFM",
    title:"FinAgent-v2: Multi-Modal Financial Reasoning with Structured Tool Use",
    relevance:94,
    summary:"Introduces structured tool-calling chains for financial agents — separating data retrieval, analysis, and synthesis into discrete verifiable steps. Shows 31% reduction in hallucination rate vs end-to-end LLM approaches.",
    proposedChange:"Refactor all 7 analysis agents to use tool-calling chains (data→analyse→synthesise) instead of single LLM calls. Expected: hallucination rate drops from 2.1% to ~1.4%.",
    impactedAgents:["Sentiment Agent","Fundamental Agent","Historical RAG"],
    costImpact:"Free — architectural change only, no new subscriptions",
    debateStatus:"pending",
    votes:{for:3,against:1,abstain:2},
    tag:"Architecture",
  },
  {
    id:"r2", type:"industry_report", date:"2025-03-15",
    source:"Morgan Stanley Research",
    title:"India 2025: Sector Rotation Map — From Consumption to Capex",
    relevance:91,
    summary:"Morgan Stanley identifies 4 sectors for next 18-month outperformance: Power & Renewables, Railways/Infra, Defense, Specialty Chemicals. Their quant model uses different factor weights than current Bharat Intelligence setup.",
    proposedChange:"Add Morgan Stanley sector rotation weights as a new 'Institutional Consensus' signal layer in the Macro Agent. High-alpha sectors get +10 confidence boost when aligned with MS consensus.",
    impactedAgents:["Macro Agent","Orchestrator"],
    costImpact:"Free — prompt engineering change only",
    debateStatus:"approved",
    votes:{for:5,against:0,abstain:1},
    tag:"Signal Quality",
  },
  {
    id:"r3", type:"whitepaper", date:"2025-03-10",
    source:"Google DeepMind",
    title:"Debate as a Verification Mechanism for LLM Factual Claims",
    relevance:87,
    summary:"DeepMind shows that structured multi-agent debate (where one agent argues FOR a claim and another argues AGAINST before a judge LLM decides) reduces factual errors by 44% vs single-model responses. Applied to financial claims specifically.",
    proposedChange:"Upgrade Governance fact-checker from single-model verification to full debate loop: Verifier Agent argues claim is true + Devil's Advocate Agent argues it's false → Judge Haiku decides. Adds ~$2/month in tokens.",
    impactedAgents:["Governance — Fact Checker"],
    costImpact:"~$2/month additional Haiku tokens",
    debateStatus:"debating",
    votes:{for:2,against:2,abstain:2},
    tag:"Governance",
  },
  {
    id:"r4", type:"news", date:"2025-03-20",
    source:"Anthropic Blog",
    title:"Claude Tool Use Latency Improvements — 40% Faster Function Calling",
    relevance:82,
    summary:"Anthropic reduced tool use latency by 40% in claude-haiku-4-5. This directly impacts the Governance fact-checker and sentiment agent which make many sequential tool calls during morning analysis runs.",
    proposedChange:"Migrate Governance fact-checker and Sentiment Agent from claude-sonnet to claude-haiku-4-5 for the tool-calling layer. Same quality, 40% faster, lower cost.",
    impactedAgents:["Governance — Fact Checker","Sentiment Agent"],
    costImpact:"Saves ~$3/month",
    debateStatus:"approved",
    votes:{for:6,against:0,abstain:0},
    tag:"Performance",
  },
  {
    id:"r5", type:"whitepaper", date:"2025-03-05",
    source:"SSRN / IIM Ahmedabad",
    title:"FII Flow Predictability in Indian Markets Using Sentiment-Momentum Fusion",
    relevance:88,
    summary:"IIM-A study finds that combining FII net flow data with social sentiment creates a 3-day leading indicator for Nifty direction with 67% accuracy (vs 58% for either alone). Specifically validates the fusion approach used by our Institutional + Sentiment agents.",
    proposedChange:"Formalise the FII+Sentiment fusion signal as a dedicated cross-agent output in the orchestrator. Weight this composite signal 1.5x the individual agent signals when both agree.",
    impactedAgents:["Institutional Flow Agent","Sentiment Agent","Orchestrator"],
    costImpact:"Free — orchestrator logic change only",
    debateStatus:"pending",
    votes:{for:4,against:1,abstain:1},
    tag:"Signal Quality",
  },
];

// AGENT_DEBATE_LOG, ENHANCEMENT_PROPOSALS, AGENT_PERF removed.
// All three were static mock constants with no API backing.
// They are replaced by empty-state placeholders that show when the
// governance agent has been running and generating real data.

// ─── COMPUTE PORTFOLIO ALERTS ─────────────────────────────────────────────────
const computePortfolioAlerts = (portfolio) => {
  const alerts = [];
  portfolio.forEach(h => {
    const pnlPct = ((h.currentPrice - h.avgBuy) / h.avgBuy) * 100;
    const toTarget = ((h.targetPrice - h.currentPrice) / h.currentPrice) * 100;
    const toStop = ((h.currentPrice - h.stoplossPrice) / h.currentPrice) * 100;
    // Critical danger — highest priority
    if(isCriticalDanger(h)){
      alerts.unshift({
        id:`danger-${h.id}`, symbol:h.symbol, severity:"critical_danger",
        type:"critical_danger",
        title:`🚨 CRITICAL DANGER — ${h.symbol} predicted -${h.dangerDropPct}% in ${h.dangerWindow}`,
        detail:h.dangerTrigger,
        action:`EXIT RECOMMENDED — Governance confidence: ${h.dangerConfidence}%. Review immediately.`,
        portfolioId:h.id,
        holding:h,
      });
    }
    if(toStop < 8 && toStop > 0) alerts.push({id:`stop-${h.id}`,symbol:h.symbol,severity:"critical",type:"stoploss_proximity",title:`${h.symbol} approaching stop-loss`,detail:`Price ${inr(h.currentPrice)} is ${toStop.toFixed(1)}% above the stop-loss at ${inr(h.stoplossPrice)}.`,action:"Review or reduce position",portfolioId:h.id});
    if(toTarget < 12 && toTarget > 0) alerts.push({id:`tgt-${h.id}`,symbol:h.symbol,severity:"info",type:"target_proximity",title:`${h.symbol} approaching target`,detail:`${toTarget.toFixed(1)}% below the target of ${inr(h.targetPrice)}. Consider booking partial profits.`,action:"Review profit booking",portfolioId:h.id});
    if(pnlPct > 15 && h.linkedRecId) alerts.push({id:`mile-${h.id}`,symbol:h.symbol,severity:"info",type:"rec_milestone",title:`${h.symbol} +${pnlPct.toFixed(1)}% since recommendation`,detail:`P&L: ₹${((h.currentPrice-h.avgBuy)*h.qty).toLocaleString("en-IN",{maximumFractionDigits:0})}. Original target still ahead.`,action:"Review thesis validity",portfolioId:h.id});
  });
  const rank={critical_danger:0,critical:1,warning:2,info:3};
  return alerts.sort((a,b)=>(rank[a.severity]??9)-(rank[b.severity]??9));
};

// ─── ATOMS ───────────────────────────────────────────────────────────────────
const Tag=({children,color=C.accent,small=false})=>(
  <span style={{display:"inline-flex",alignItems:"center",background:color+"14",color,borderRadius:4,padding:small?"1px 6px":"2px 8px",fontSize:small?10.5:11.5,fontWeight:700,letterSpacing:".03em",whiteSpace:"nowrap",fontFamily:SANS}}>{children}</span>
);
const Bar=({pct,color=C.accent,h=5})=>(
  <div style={{background:C.border,borderRadius:4,height:h,overflow:"hidden"}}>
    <div style={{width:`${Math.min(pct,100)}%`,height:"100%",background:color,borderRadius:4,transition:"width 1s ease"}}/>
  </div>
);
const Dot=({color=C.green,pulse=false})=>(
  <span style={{display:"inline-block",width:7,height:7,borderRadius:"50%",background:color,animation:pulse?"pulse 2s infinite":undefined,flexShrink:0}}/>
);
/** Empty-state placeholder shown instead of mock data when API is live but has no rows yet. */
// `icon` is accepted for old call sites but no longer drawn: the Editorial
// style uses a single neutral line icon instead of emoji.
const EmptyState=({title,sub,mono=false})=>(
  <div style={{textAlign:"center",padding:"40px 20px",color:C.textDim,border:`1px dashed ${C.borderHi}`,borderRadius:8,display:"flex",flexDirection:"column",alignItems:"center",gap:8}}>
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 8v4M12 16h.01"/></svg>
    <div style={{fontFamily:SERIF,fontSize:18,fontWeight:600,color:C.text}}>{title}</div>
    {sub&&<div style={{fontSize:13,maxWidth:460,lineHeight:1.6,fontFamily:mono?MONO:undefined}}>{sub}</div>}
  </div>
);
const GovShield=({size=18})=>(
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
    <path d="M12 2L4 6v6c0 5.5 3.8 10.7 8 12 4.2-1.3 8-6.5 8-12V6L12 2z" fill={C.blue+"22"} stroke={C.blue} strokeWidth="1.5" strokeLinejoin="round"/>
    <path d="M9 12l2 2 4-4" stroke={C.blue} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);

// ─── VALUATION SCENARIOS PANEL ───────────────────────────────────────────────
const ValuationScenariosPanel=({val,onFetch,symbol})=>{
  if(!val){
    return(
      <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:7,padding:"8px 12px",marginTop:10,marginBottom:6,display:'flex',alignItems:'center',justifyContent:'space-between'}}>
        <span style={{fontSize:12,color:C.muted}}>📐 Valuation Scenarios — not loaded</span>
        {onFetch&&<button onClick={onFetch} style={{fontSize:12,background:C.accent,color:C.onFill,border:'none',borderRadius:4,padding:'3px 8px',cursor:'pointer'}}>Load</button>}
      </div>
    );
  }
  const rec=val.recommendation||"—";
  const recColor=rec==="STRONG_BUY"||rec==="BUY"?C.green:rec==="SELL"?C.red:rec==="HOLD"?C.accent:C.muted;
  const sc=val.scenarios||{};
  const fv=val.fair_value_range||{};
  const cp=val.current_price;
  const tornado=val.tornado||[];
  const maxImpact=tornado[0]?.impact||1;
  return(
    <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:7,padding:"10px 12px",marginTop:10,marginBottom:6}}>
      <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:10}}>
        <span style={{fontSize:14}}>📐</span>
        <span style={{fontWeight:700,fontSize:13,color:C.text}}>Valuation Scenarios</span>
        <span style={{fontSize:12.5,color:recColor,fontWeight:700}}>{rec.replace('_',' ')}</span>
        {val.data_quality==='ESTIMATED'&&<span style={{fontSize:11,color:C.muted,background:C.card,padding:'1px 5px',borderRadius:3}}>estimated</span>}
      </div>

      {/* Three scenario tiles */}
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:6,marginBottom:10}}>
        {['BEAR','BASE','BULL'].map(sn=>{
          const s=sc[sn];
          if(!s) return <div key={sn}/>;
          const iv=s.intrinsic_value;
          const mos=s.margin_of_safety_pct;
          const color=sn==='BULL'?C.green:sn==='BEAR'?C.red:C.accent;
          const mosColor=mos>20?C.green:mos>0?C.accent:C.red;
          return(
            <div key={sn} style={{background:C.card,border:`1px solid ${color}33`,borderRadius:6,padding:'8px 6px',textAlign:'center'}}>
              <div style={{fontSize:11,fontWeight:700,color,marginBottom:4}}>{sn}</div>
              <div style={{fontWeight:800,fontSize:14,color:C.text}}>
                {iv!=null?`₹${iv.toLocaleString('en-IN',{maximumFractionDigits:0})}`:'—'}
              </div>
              <div style={{fontSize:11,color:mosColor,marginTop:3}}>
                MOS {mos!=null?`${mos>0?'+':''}${mos.toFixed(0)}%`:'—'}
              </div>
              <div style={{fontSize:11,color:C.muted,marginTop:2}}>
                g={s.growth_rate?.toFixed(1)}% w={s.wacc?.toFixed(1)}%
              </div>
            </div>
          );
        })}
      </div>

      {/* Fair value range bar */}
      {fv.low&&fv.high&&cp&&(
        <div style={{marginBottom:10}}>
          <div style={{fontSize:11,color:C.muted,marginBottom:4}}>
            Fair Value Range: ₹{fv.low.toLocaleString('en-IN',{maximumFractionDigits:0})} – ₹{fv.high.toLocaleString('en-IN',{maximumFractionDigits:0})}
            &nbsp;·&nbsp;Current: ₹{cp.toLocaleString('en-IN',{maximumFractionDigits:0})}
          </div>
          <div style={{background:C.card,borderRadius:4,height:8,position:'relative',overflow:'hidden'}}>
            {/* Range bar */}
            <div style={{
              position:'absolute',height:'100%',
              left:`${Math.min(100,Math.max(0,(fv.low/fv.high)*60))}%`,
              width:`${Math.min(100,(1-(fv.low/fv.high))*60)}%`,
              background:`${C.accent}44`,borderRadius:4
            }}/>
            {/* Current price marker */}
            {(()=>{const pct=Math.min(100,Math.max(0,(cp/fv.high)*60));return(
              <div style={{position:'absolute',height:'100%',width:2,
                background:cp<fv.low?C.red:cp>fv.high?C.green:C.text,
                left:`${pct}%`,top:0}}/>
            )})()}
          </div>
        </div>
      )}

      {/* Sensitivity Tornado */}
      {tornado.length>0&&(
        <div>
          <div style={{fontSize:11,fontWeight:700,color:C.muted,marginBottom:4,textTransform:'uppercase'}}>Sensitivity Tornado</div>
          {tornado.map(row=>{
            const barW=Math.round((row.impact/maxImpact)*100);
            return(
              <div key={row.assumption} style={{display:'flex',alignItems:'center',gap:6,marginBottom:4}}>
                <div style={{fontSize:11,color:C.textDim,width:90,flexShrink:0}}>{row.assumption}</div>
                <div style={{flex:1,background:C.card,borderRadius:3,height:6,overflow:'hidden'}}>
                  <div style={{width:`${barW}%`,height:'100%',background:C.accent,borderRadius:3}}/>
                </div>
                <div style={{fontSize:11,color:C.muted,width:45,textAlign:'right'}}>
                  ±{row.impact_pct?.toFixed(0)}%
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

// ─── WARREN BOT PANEL ────────────────────────────────────────────────────────
const WarrenBotPanel=({wb})=>{
  if(!wb) return(
    <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:7,padding:"8px 12px",marginTop:10,marginBottom:6}}>
      <span style={{fontSize:12,color:C.muted}}>🏦 WarrenBot — data not yet available for this stock</span>
    </div>
  );

  const convColor=wb.conviction_rating?.includes("STRONG")?C.green
                 :wb.conviction_rating?.includes("MODERATE")?C.accent
                 :C.muted;

  const promoColor=wb.promoter_quality==="EXCELLENT"?C.green
                  :wb.promoter_quality==="GOOD"?C.accent
                  :C.red;

  const inrFmt=v=>v!=null?"₹"+new Intl.NumberFormat("en-IN",{maximumFractionDigits:0}).format(v):"—";

  const subScores=[
    {label:"Moat",     val:wb.moat_strength_score},
    {label:"ROCE",     val:wb.roce_score},
    {label:"Mgmt",     val:wb.management_score},
    {label:"Earnings", val:wb.earnings_score},
    {label:"Valuation",val:wb.valuation_score},
  ];

  return(
    <div style={{background:C.accent+"08",border:`1px solid ${C.accent}33`,borderRadius:7,padding:10,marginTop:10,marginBottom:6}}>
      {/* Header */}
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:10}}>
        <div>
          <div style={{fontSize:12.5,fontWeight:700,color:C.accent}}>🏦 WarrenBot</div>
          <div style={{fontSize:11,color:C.muted,marginTop:2}}>Long-term quality lens · Not a momentum signal</div>
        </div>
        <div style={{display:"flex",alignItems:"center",gap:8}}>
          <Tag color={convColor} small>{wb.conviction_rating}</Tag>
          <span style={{fontSize:20,fontWeight:800,color:convColor,fontFamily:MONO,lineHeight:1}}>
            {wb.score}<span style={{fontSize:12,color:C.muted,fontWeight:400}}>/100</span>
          </span>
        </div>
      </div>

      {/* Five sub-score boxes */}
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(88px,1fr))",gap:5,marginBottom:9}}>
        {subScores.map(({label,val})=>(
          <div key={label} style={{background:C.bg,border:`1px solid ${C.border}`,borderRadius:5,padding:"5px 6px"}}>
            <div style={{fontSize:10.5,color:C.muted,marginBottom:2}}>{label}</div>
            <div style={{fontSize:13,fontWeight:700,color:C.accent,fontFamily:MONO,marginBottom:3}}>
              {val??0}<span style={{fontSize:10.5,color:C.muted,fontWeight:400}}>/20</span>
            </div>
            <Bar pct={(val??0)/20*100} color={C.accent} h={3}/>
          </div>
        ))}
      </div>

      {/* Three metric boxes */}
      <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:5,marginBottom:9}}>
        {[
          {label:"Intrinsic Value",  val:inrFmt(wb.intrinsic_value_per_share), color:C.text},
          {label:"Margin of Safety", val:wb.margin_of_safety_pct!=null?`${wb.margin_of_safety_pct.toFixed(1)}%`:"—",
           color:wb.margin_of_safety_pct>0?C.green:wb.margin_of_safety_pct<0?C.red:C.muted},
          {label:"10yr EPS CAGR",   val:wb.ten_year_eps_cagr!=null?`${wb.ten_year_eps_cagr.toFixed(1)}%`:"—", color:C.text},
        ].map(({label,val,color})=>(
          <div key={label} style={{background:C.bg,border:`1px solid ${C.border}`,borderRadius:5,padding:"5px 7px"}}>
            <div style={{fontSize:10.5,color:C.muted,marginBottom:2}}>{label}</div>
            <div style={{fontSize:13,fontWeight:700,color,fontFamily:MONO}}>{val}</div>
          </div>
        ))}
      </div>

      {/* Tags row */}
      <div style={{display:"flex",flexWrap:"wrap",gap:5,marginBottom:9}}>
        {wb.moat_type&&<Tag color={C.accent} small>{wb.moat_type}</Tag>}
        {wb.india_consumption_play&&<Tag color={C.green} small>🇮🇳 India Consumption Play</Tag>}
        {wb.jhunjhunwala_cyclical_flag&&<Tag color={C.blue} small>📉 Cyclical Trough</Tag>}
        {wb.promoter_quality&&<Tag color={promoColor} small>Promoter: {wb.promoter_quality}</Tag>}
      </div>

      {/* Why like / Why pass */}
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:7,marginBottom:9}}>
        <div style={{background:C.green+"08",border:`1px solid ${C.green}22`,borderRadius:5,padding:"6px 8px"}}>
          <div style={{fontSize:11,fontWeight:700,color:C.green,marginBottom:4}}>✅ Why Buffett/RJ would like it</div>
          <div style={{fontSize:11,color:C.green+"cc",lineHeight:1.55}}>{wb.why_like||"—"}</div>
        </div>
        <div style={{background:C.red+"08",border:`1px solid ${C.red}22`,borderRadius:5,padding:"6px 8px"}}>
          <div style={{fontSize:11,fontWeight:700,color:C.red,marginBottom:4}}>⛔ Why they would pass</div>
          <div style={{fontSize:11,color:C.red+"cc",lineHeight:1.55}}>{wb.why_pass||"—"}</div>
        </div>
      </div>

      {/* Data gaps notice */}
      {wb.data_gaps?.length>0&&(
        <div style={{background:C.accent+"0f",border:`1px solid ${C.accent}33`,borderRadius:4,padding:"4px 8px",marginBottom:7,fontSize:11,color:C.accent}}>
          ⚠ Data gaps: {wb.data_gaps.join(", ")} — score may be understated
        </div>
      )}

      {/* Bottom disclaimer */}
      <div style={{fontSize:10.5,color:C.muted,fontStyle:"italic",lineHeight:1.55}}>
        WarrenBot scores long-term business quality only. A low score means momentum-driven, not fundamentals-driven. Your call based on your time horizon.
      </div>
    </div>
  );
};

// ─── CRITICAL OPPORTUNITY BANNER ─────────────────────────────────────────────
function CriticalOpportunityBanner({stocks, onSelect, onOpenARIA}){
  if(!stocks.length) return null;
  return(
    <div className="critical-discovery" style={{
      background:`linear-gradient(135deg,${C.green}12,${C.lime}08)`,
      border:`1.5px solid ${C.green}88`,
      borderRadius:10,padding:14,marginBottom:14,
    }}>
      <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:10}}>
        <div style={{width:28,height:28,borderRadius:6,background:C.green,display:"flex",alignItems:"center",justifyContent:"center",fontSize:14,flexShrink:0}}>🚀</div>
        <div style={{flex:1}}>
          <div style={{display:"flex",alignItems:"center",gap:7}}>
            <span style={{fontSize:14,fontWeight:800,color:C.green,letterSpacing:-.2}}>CRITICAL OPPORTUNITY</span>
            <span style={{background:C.green,color:C.onFill,borderRadius:4,padding:"1px 7px",fontSize:12,fontWeight:800,animation:"criticalBadge 2s ease-in-out infinite"}}>
              ≥60% HISTORICAL HIT RATE · ≥40% UPSIDE
            </span>
          </div>
          <div style={{fontSize:12,color:C.green,marginTop:1}}>
            {stocks.length} call{stocks.length>1?"s":""} whose signal band has beaten the Nifty ≥60% of the time at 90 days — re-checked daily
          </div>
        </div>
        <button onClick={()=>onOpenARIA("discovery_critical")} style={{background:C.green+"22",border:`1px solid ${C.green}55`,borderRadius:6,padding:"5px 11px",color:C.green,fontSize:12,fontWeight:700,cursor:"pointer",whiteSpace:"nowrap"}}>
          ✦ Deep Dive with ARIA
        </button>
      </div>
      <div style={{display:"flex",gap:9,flexWrap:"wrap"}}>
        {stocks.map(s=>(
          <div key={s.id} onClick={()=>onSelect(s.id)} style={{
            background:C.bg,border:`1px solid ${C.green}55`,borderRadius:8,
            padding:"10px 13px",flex:1,minWidth:220,cursor:"pointer",
            transition:"all .15s",
          }}>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:6}}>
              <div>
                <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:2}}>
                  <span style={{fontSize:14,fontWeight:800,color:C.text,fontFamily:MONO}}>{s.symbol}</span>
                  <span style={{background:C.green+"22",color:C.green,border:`1px solid ${C.green}44`,borderRadius:4,padding:"1px 6px",fontSize:12,fontWeight:800}}>+{s.upsidePct}%</span>
                </div>
                <div style={{fontSize:12,color:C.textDim}}>{s.name}</div>
              </div>
              <div style={{textAlign:"right"}}>
                <div title={hitRateTitle(s)} style={{fontSize:12.5,fontWeight:700,color:C.green,fontFamily:MONO,cursor:"help"}}>{s.upsideConfidence}%</div>
                <div style={{fontSize:10.5,color:C.muted}}>hit rate</div>
              </div>
            </div>
            <div style={{fontSize:11,color:C.green,lineHeight:1.5,marginBottom:7}}>{s.upsideBasis.slice(0,90)}...</div>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
              <span style={{fontSize:11,color:C.muted}}>⏱ {callLine(s)||s.upsideHorizon}</span>
              <span style={{fontSize:11,color:C.muted}}>Entry: {s.entry}</span>
            </div>
            <div style={{marginTop:6,background:C.green+"15",borderRadius:3,height:4,overflow:"hidden"}}>
              <div style={{width:`${s.upsideConfidence}%`,height:"100%",background:C.green,borderRadius:3}}/>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── CRITICAL DANGER BANNER ───────────────────────────────────────────────────
// Shared signal-category sets (used in cards + deep dive)
const BULL_SIGNALS=new Set(["BULLISH","BUY","POSITIVE","STRONG_BUY","BULLISH_ANALOGUE"]);
const BEAR_SIGNALS=new Set(["BEARISH","SELL","NEGATIVE","AVOID","BEARISH_ANALOGUE","STRONG_SELL"]);

// ─── DISCOVERY CARD ───────────────────────────────────────────────────────────
// ─── EDITORIAL UI PRIMITIVES ──────────────────────────────────────────────────
// Shared by the rebuilt tabs (Discovery first). Line icons instead of emoji,
// tags that carry meaning in text as well as colour, and controls sized for
// touch (40px+ targets).
const ICON_PATHS={
  search:"M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-3.5-3.5",
  close:"M6 6l12 12M18 6L6 18",
  back:"M15 18l-6-6 6-6",
  plus:"M12 5v14M5 12h14",
  spark:"M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z",
  info:"M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 8v4M12 16h.01",
  up:"M7 17L17 7M9 7h8v8",
  down:"M17 7L7 17M15 17H7V9",
  chevron:"M9 18l6-6-6-6",
  run:"M4 20V10M10 20V4M16 20v-7M2 20h20",
  target:"M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 12h.01",
  briefcase:"M3 7h18v13H3zM8 7V4h8v3M3 13h18",
  activity:"M3 12h4l3-8 4 16 3-8h4",
  more:"M5 12h.01M12 12h.01M19 12h.01",
};
const Icon=({name,size=16,style})=>(
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{flexShrink:0,...style}}>
    <path d={ICON_PATHS[name]}/>
  </svg>
);
const TAG_TONE={
  buy:{bg:C.green+"14",fg:C.green}, sell:{bg:C.red+"12",fg:C.red}, hold:{bg:C.orange+"16",fg:C.orange},
  warn:{bg:C.orange+"16",fg:C.orange}, info:{bg:C.blue+"12",fg:C.blue}, mute:{bg:C.panel,fg:C.textDim},
  crit:{bg:C.accent,fg:C.onFill},
};
const EdTag=({tone="mute",children,title})=>{
  const t=TAG_TONE[tone]||TAG_TONE.mute;
  return <span title={title} style={{display:"inline-flex",alignItems:"center",height:20,padding:"0 7px",borderRadius:4,
    background:t.bg,color:t.fg,fontSize:11,fontWeight:700,letterSpacing:".04em",textTransform:"uppercase",
    whiteSpace:"nowrap",fontFamily:SANS,cursor:title?"help":undefined}}>{children}</span>;
};
const actionTone=a=>BULL_SIGNALS.has(a)?"buy":BEAR_SIGNALS.has(a)?"sell":"hold";
const edBtn=(variant="default")=>({display:"inline-flex",alignItems:"center",justifyContent:"center",gap:8,minHeight:40,
  padding:"0 16px",borderRadius:6,fontSize:13.5,fontWeight:600,cursor:"pointer",whiteSpace:"nowrap",fontFamily:SANS,
  ...(variant==="accent"?{background:C.accent,color:C.onFill,border:`1px solid ${C.accent}`}
    :variant==="ghost"?{background:"transparent",color:C.textDim,border:"1px solid transparent"}
    :{background:C.surface,color:C.text,border:`1px solid ${C.borderHi}`})});
const edChip=on=>({display:"inline-flex",alignItems:"center",gap:7,minHeight:36,padding:"0 13px",borderRadius:999,
  border:`1px solid ${on?C.accent:C.border}`,background:on?C.accent+"12":C.surface,color:on?C.text:C.textDim,
  fontSize:13,fontWeight:on?600:500,cursor:"pointer",whiteSpace:"nowrap",fontFamily:SANS});
const edSelect={fontFamily:SANS,fontSize:13,color:C.text,background:C.surface,border:`1px solid ${C.border}`,
  borderRadius:6,minHeight:38,padding:"0 10px"};
const edH3={fontFamily:SERIF,fontSize:19,fontWeight:600,margin:"0 0 12px",color:C.text,display:"flex",alignItems:"baseline",gap:10,flexWrap:"wrap"};
const edBox={border:`1px solid ${C.border}`,borderRadius:6,padding:16,background:C.surface};

/** True below `px` — switches split views to a list → detail → back flow. */
function useNarrow(px=900){
  const q=`(max-width:${px}px)`;
  const read=()=>typeof window!=="undefined"&&typeof window.matchMedia==="function"&&window.matchMedia(q).matches;
  const [narrow,setNarrow]=useState(read);
  useEffect(()=>{
    if(typeof window==="undefined"||typeof window.matchMedia!=="function") return;
    const mq=window.matchMedia(q), h=e=>setNarrow(e.matches);
    if(mq.addEventListener) mq.addEventListener("change",h); else mq.addListener(h);
    setNarrow(mq.matches);
    return()=>{ if(mq.removeEventListener) mq.removeEventListener("change",h); else mq.removeListener(h); };
  },[q]);
  return narrow;
}

const inr=(n,d)=>{
  if(n==null||isNaN(n)) return "—";
  const dp=d??(Math.abs(n)<1000?2:0);
  return "₹"+Number(n).toLocaleString("en-IN",{minimumFractionDigits:dp,maximumFractionDigits:dp});
};
const signedPct=(n,d=1)=>n==null||isNaN(n)?"—":(n>0?"+":n<0?"−":"")+Math.abs(n).toFixed(d)+"%";
const dirColor=n=>n==null||isNaN(n)||n===0?C.textDim:n>0?C.green:C.red;
const cleanSym=s=>(s||"").replace(/\.(NS|BO)$/,"");
const fmtDate=d=>{
  if(!d) return "";
  const t=new Date(d.length===10?d+"T00:00:00":d);
  return isNaN(t)?d:t.toLocaleDateString("en-IN",{day:"numeric",month:"short"});
};
const sectorOf=s=>s.sector&&s.sector!=="UNKNOWN"?s.sector:null;
const nameOf=s=>s.name&&s.name!==s.symbol&&s.name!==cleanSym(s.symbol)?s.name:null;
const footLine=s=>[
  s.callDate?`Called ${fmtDate(s.callDate)}`:null,
  s.daysActive!=null?`${s.daysActive}d active`:null,
  s.validTill?`valid till ${fmtDate(s.validTill)}`:null,
].filter(Boolean).join(" · ");

const Metric=({k,v,color,title})=>(
  <span title={title} style={{display:"flex",flexDirection:"column",gap:1,minWidth:0,cursor:title?"help":undefined}}>
    <span style={{fontSize:11,color:C.textDim,textTransform:"uppercase",letterSpacing:".05em"}}>{k}</span>
    <span style={{fontFamily:MONO,fontSize:15,fontWeight:600,color:color||C.text}}>{v}</span>
  </span>
);

// ─── DISCOVERY: LIST CARD ─────────────────────────────────────────────────────
function DiscoveryCard({stock:s,selected,onSelect}){
  const weak=s.callStatus==="WEAKENED";
  const sub=[nameOf(s),sectorOf(s)].filter(Boolean).join(" · ");
  return(
    <button onClick={onSelect} aria-pressed={!!selected} className="ed-card" style={{
      display:"flex",flexDirection:"column",gap:8,width:"100%",textAlign:"left",cursor:"pointer",
      padding:"16px 12px 15px",border:"none",borderTop:`1px solid ${C.borderHi}`,borderRadius:0,
      background:selected?C.surface:"transparent",boxShadow:selected?`inset 0 2px 0 ${C.accent}`:"none",
      color:C.text,fontFamily:SANS}}>
      <span style={{display:"flex",alignItems:"center",gap:7,flexWrap:"wrap",width:"100%"}}>
        <span style={{fontFamily:SERIF,fontSize:19,fontWeight:600}}>{cleanSym(s.symbol)}</span>
        <EdTag tone={actionTone(s.action)}>{s.action}</EdTag>
        {isCriticalDiscovery(s)&&<EdTag tone="crit">Critical</EdTag>}
        {weak&&<EdTag tone="warn">Weakened</EdTag>}
        {s.dataQuality==="ESTIMATED"&&<EdTag tone="info">Estimated</EdTag>}
        <span style={{marginLeft:"auto",fontFamily:MONO,fontSize:13}}>{inr(s.price)}</span>
      </span>
      {sub&&<span style={{fontSize:12.5,color:C.textDim,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",display:"block",maxWidth:"100%"}}>{sub}</span>}
      <span style={{display:"grid",gridTemplateColumns:"repeat(3,minmax(0,1fr))",gap:8,width:"100%"}}>
        <Metric k="Upside" v={signedPct(s.upsidePct,0)} color={dirColor(s.upsidePct)}/>
        <Metric k="Hit rate" v={s.confidence!=null?`${Math.round(s.confidence)}%`:"—"} title={hitRateTitle(s)}/>
        <Metric k="Since call" v={signedPct(s.returnSinceCallPct)} color={dirColor(s.returnSinceCallPct)}/>
      </span>
      {footLine(s)&&<span style={{fontSize:12,color:C.textDim}}>{footLine(s)}</span>}
    </button>
  );
}

// ─── DISCOVERY: PRICE LEVELS BAR ──────────────────────────────────────────────
function LevelsBar({s}){
  const stop=s.stoplossNum, tgt=s.targetNum, lo=s.entryLow, hi=s.entryHigh, now=s.price;
  const vals=[stop,tgt,lo,hi,now].filter(v=>v!=null&&!isNaN(v)&&v>0);
  if(vals.length<2) return <p style={{margin:0,fontSize:13,color:C.textDim}}>Price levels aren't available for this call yet.</p>;
  const mn=Math.min(...vals), mx=Math.max(...vals), pad=(mx-mn)*0.08||1, L=mn-pad, H=mx+pad;
  const pos=v=>`${((v-L)/(H-L)*100).toFixed(2)}%`;
  const mark=(v,color,label)=>v!=null&&!isNaN(v)&&v>0&&(
    <span title={`${label} ${inr(v)}`} style={{position:"absolute",top:-7,left:pos(v),width:4,height:24,borderRadius:2,background:color,transform:"translateX(-50%)"}}/>
  );
  const legend=[
    ["Stop-loss",inr(stop),{background:C.red}],
    ["Entry zone",lo&&hi?`${inr(lo)}–${inr(hi).slice(1)}`:"—",{background:C.accent+"18",border:`1px solid ${C.accent}`}],
    ...(now>0?[["Now",inr(now),{background:C.text}]]:[]),
    ["Target",inr(tgt),{background:C.green}],
  ];
  return(
    <div>
      <div aria-hidden="true" style={{position:"relative",height:10,borderRadius:5,background:C.panel,margin:"14px 6px 18px"}}>
        {lo&&hi&&<span style={{position:"absolute",top:-1,bottom:-1,left:pos(lo),width:`calc(${pos(hi)} - ${pos(lo)})`,background:C.accent+"18",border:`1px solid ${C.accent}`,borderRadius:5}}/>}
        {mark(stop,C.red,"Stop-loss")}{mark(now,C.text,"Now")}{mark(tgt,C.green,"Target")}
      </div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(120px,1fr))",gap:10}}>
        {legend.map(([k,v,sw])=>(
          <div key={k} style={{display:"flex",flexDirection:"column",gap:3,fontSize:12,color:C.textDim}}>
            <span style={{display:"inline-flex",alignItems:"center",gap:6}}><span style={{width:10,height:10,borderRadius:2,display:"inline-block",...sw}}/>{k}</span>
            <span style={{fontFamily:MONO,fontSize:14,fontWeight:600,color:C.text}}>{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── SHARED DETAIL SECTIONS (Discovery + Recs) ────────────────────────────────
function AgentSignals({agents:raw,narrow}){
  const agents=Object.entries(raw||{}).filter(([,a])=>a&&a.signal);
  if(!agents.length) return null;
  const bull=agents.filter(([,a])=>BULL_SIGNALS.has(a.signal)).length;
  const bear=agents.filter(([,a])=>BEAR_SIGNALS.has(a.signal)).length;
  return(
    <section>
      <h3 style={edH3}>Agent signals <span style={{fontFamily:SANS,fontSize:12,color:C.textDim,fontWeight:400}}>{bull} bullish · {bear} bearish · {agents.length-bull-bear} neutral</span></h3>
      <div style={{border:`1px solid ${C.border}`,borderRadius:6,overflow:"hidden",background:C.surface}}>
        {agents.map(([k,a],i)=>{
          const tone=actionTone(a.signal), fill=tone==="buy"?C.green:tone==="sell"?C.red:C.orange;
          return(
            <div key={k} style={{display:"grid",gridTemplateColumns:narrow?"minmax(0,1fr) auto 32px":"130px 120px minmax(60px,110px) 32px",columnGap:12,rowGap:4,
              alignItems:"center",padding:"11px 14px",borderTop:i?`1px solid ${C.border}`:"none",fontSize:13}}>
              <span style={{fontWeight:600,textTransform:"capitalize"}}>{k.replace(/_/g," ")}</span>
              <span><EdTag tone={tone}>{String(a.signal).replace(/_/g," ")}</EdTag></span>
              {!narrow&&<span aria-hidden="true" style={{height:6,borderRadius:3,background:C.panel,overflow:"hidden",display:"block"}}>
                <span style={{display:"block",height:"100%",width:`${Math.max(0,Math.min(100,a.score||0))}%`,background:fill}}/></span>}
              <span style={{fontFamily:MONO,textAlign:"right"}}>{a.score??"—"}</span>
              {(a.detail||a.reason)&&<span style={{gridColumn:"1/-1",color:C.textDim,fontSize:12.5,lineHeight:1.5}}>{a.detail||a.reason}</span>}
            </div>
          );
        })}
      </div>
    </section>
  );
}
function CatalystsRisks({catalysts,risks,narrow}){
  const cats=catalysts||[], rks=risks||[];
  const list=(items,icon,color)=>(
    <ul style={{margin:0,padding:0,listStyle:"none",display:"flex",flexDirection:"column",gap:9}}>
      {items.map(t=><li key={t} style={{display:"flex",gap:9,fontSize:13,lineHeight:1.45}}><Icon name={icon} size={15} style={{color,marginTop:2}}/><span>{t}</span></li>)}
    </ul>
  );
  return(
    <div style={{display:"grid",gridTemplateColumns:narrow?"minmax(0,1fr)":"repeat(2,minmax(0,1fr))",gap:12}}>
      <div style={edBox}>
        <h3 style={{...edH3,fontSize:17}}>Catalysts</h3>
        {cats.length?list(cats,"up",C.green):<p style={{margin:0,fontSize:13,color:C.textDim}}>No specific catalyst was recorded.</p>}
      </div>
      <div style={edBox}>
        <h3 style={{...edH3,fontSize:17}}>Risks</h3>
        {rks.length?list(rks,"down",C.red):<p style={{margin:0,fontSize:13,color:C.textDim}}>No agent voted bearish and liquidity is fine. Keep an eye on the stop-loss.</p>}
      </div>
    </div>
  );
}
const DetailNote=({tone,children})=>(
  <div role="note" style={{display:"flex",gap:10,alignItems:"flex-start",padding:"12px 14px",borderRadius:6,fontSize:13.5,lineHeight:1.5,
    background:tone==="warn"?C.orange+"14":C.blue+"10",color:C.text}}>
    <Icon name="info" style={{color:tone==="warn"?C.orange:C.blue,marginTop:1}}/><span>{children}</span>
  </div>
);

// ─── DISCOVERY: DETAIL PANEL ──────────────────────────────────────────────────
function DiscoveryDetail({s,narrow,isHeld,onBack,onAdd,onAsk,valuationCache,setValuationCache}){
  const notes=[];
  if(s.callStatus==="WEAKENED") notes.push(["warn",s.statusReason||"The daily re-check weakened this call. It won't be extended unless the agents confirm it again."]);
  if(s.dataQuality==="ESTIMATED") notes.push(["info","Some fundamentals came from a fallback source, so treat the upside as an estimate."]);
  if(s.hasTrackRecord===false) notes.push(["info","No track record for this signal band yet, so the hit rate is shown as a neutral 50%."]);
  const thesis=s.upsideBasis||s.summary||s.discoveryReason;
  const fwd=[
    ["Forward PE",s.forwardPe!=null?`${s.forwardPe.toFixed(1)}×`:null],
    ["PEG",s.pegRatio!=null?s.pegRatio.toFixed(2):null],
    ["EPS growth",s.epsGrowthPct!=null?signedPct(s.epsGrowthPct):null],
    ["Impact cost",s.impactCostPct!=null?`${s.impactCostPct.toFixed(2)}%`:null],
    ["Liquidity",s.liquidityTier||null],
  ].filter(([,v])=>v!=null);
  const flags=s.govCheck?.flags||[];
  const nm=nameOf(s), sec=sectorOf(s);
  return(
    <article aria-label={`${cleanSym(s.symbol)} detail`} style={{padding:narrow?"16px 4px":"30px 34px",display:"flex",flexDirection:"column",gap:24}}>
      {narrow&&<button onClick={onBack} style={{...edBtn("ghost"),alignSelf:"flex-start",paddingLeft:6}}><Icon name="back"/>Back to list</button>}

      <header style={{display:"flex",justifyContent:"space-between",gap:16,flexWrap:"wrap"}}>
        <div style={{minWidth:0}}>
          <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
            <h2 style={{fontFamily:SERIF,fontSize:narrow?28:36,fontWeight:600,margin:0,lineHeight:1.1}}>{cleanSym(s.symbol)}</h2>
            <EdTag tone={actionTone(s.action)}>{s.action}</EdTag>
            {isCriticalDiscovery(s)&&<EdTag tone="crit">Critical</EdTag>}
            {s.callStatus==="WEAKENED"&&<EdTag tone="warn">Weakened</EdTag>}
          </div>
          {(nm||sec)&&<p style={{margin:"4px 0 0",color:C.textDim,fontSize:14}}>{[nm,sec].filter(Boolean).join(" · ")}</p>}
        </div>
        <div style={{display:"flex",flexDirection:"column",alignItems:narrow?"flex-start":"flex-end",gap:2}}>
          <span style={{fontFamily:MONO,fontSize:24,fontWeight:600}}>{inr(s.price)}</span>
          {s.returnSinceCallPct!=null&&<span style={{fontFamily:MONO,fontSize:13,color:dirColor(s.returnSinceCallPct)}}>{signedPct(s.returnSinceCallPct)} since the call{s.callPrice?` at ${inr(s.callPrice)}`:""}</span>}
        </div>
      </header>

      {notes.map(([tone,text],i)=>(
        <div key={i} role="note" style={{display:"flex",gap:10,alignItems:"flex-start",padding:"12px 14px",borderRadius:6,fontSize:13.5,lineHeight:1.5,
          background:tone==="warn"?C.orange+"14":C.blue+"10",color:C.text}}>
          <Icon name="info" style={{color:tone==="warn"?C.orange:C.blue,marginTop:1}}/><span>{text}</span>
        </div>
      ))}

      {thesis&&<p style={{margin:0,fontFamily:SERIF,fontSize:narrow?17:19,lineHeight:1.55,maxWidth:"70ch"}}>{thesis}</p>}

      <div style={{display:"grid",gridTemplateColumns:narrow?"repeat(2,minmax(0,1fr))":"repeat(4,minmax(0,1fr))",gap:10}}>
        {[
          ["Upside",signedPct(s.upsidePct,0),dirColor(s.upsidePct),null],
          ["Hit rate",s.confidence!=null?`${Math.round(s.confidence)}%`:"—",null,hitRateTitle(s)],
          ["Signal score",s.signalScore!=null?Math.round(s.signalScore):"—",null,"Raw agent score. It drives selection and position size; the hit rate is what that score has historically delivered."],
          ["Position size",s.positionLabel||(s.suggestedPositionPct?`${s.suggestedPositionPct}%`:"—"),null,null],
        ].map(([k,v,color,title])=>(
          <div key={k} title={title||undefined} style={{padding:"12px 14px",borderRadius:6,background:C.panel,cursor:title?"help":undefined,minWidth:0}}>
            <div style={{fontSize:11,color:C.textDim,textTransform:"uppercase",letterSpacing:".05em"}}>{k}</div>
            <div style={{fontFamily:k==="Position size"?SANS:MONO,fontSize:k==="Position size"?14:20,fontWeight:600,marginTop:2,color:color||C.text}}>{v}</div>
          </div>
        ))}
      </div>
      {s.hasTrackRecord!==false&&s.calibrationN!=null&&(
        <p style={{margin:"-14px 0 0",fontSize:12,color:C.textDim}}>Hit rate = share of {s.calibrationN} past calls in this signal band that beat the Nifty 50 over 90 days.</p>
      )}

      <section>
        <h3 style={edH3}>Price levels <span style={{fontFamily:SANS,fontSize:12,color:C.textDim,fontWeight:400}}>{s.upsideHorizon||s.horizon||""}</span></h3>
        <LevelsBar s={s}/>
      </section>

      <AgentSignals agents={s.agents} narrow={narrow}/>
      <CatalystsRisks catalysts={s.catalysts} risks={s.risks} narrow={narrow}/>

      {fwd.length>0&&(
        <section>
          <h3 style={edH3}>Valuation snapshot</h3>
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(130px,1fr))",gap:10}}>
            {fwd.map(([k,v])=>(
              <div key={k} style={{borderTop:`1px solid ${C.borderHi}`,paddingTop:8}}>
                <div style={{fontSize:11,color:C.textDim,textTransform:"uppercase",letterSpacing:".05em"}}>{k}</div>
                <div style={{fontFamily:MONO,fontSize:15,fontWeight:600,marginTop:2}}>{v}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      {s.warrenBot&&<WarrenBotPanel wb={s.warrenBot}/>}
      <ValuationScenariosPanel
        val={valuationCache[s.symbol]}
        symbol={s.symbol}
        onFetch={()=>{
          apiFetch(`/api/valuation/${encodeURIComponent(s.symbol)}`)
            .then(d=>{if(d&&!d.error)setValuationCache(prev=>({...prev,[s.symbol]:d}));})
            .catch(()=>{});
        }}
      />

      <p style={{margin:0,fontSize:12.5,color:C.textDim,display:"flex",gap:6,alignItems:"center"}}>
        <GovShield size={14}/>
        {flags.length?`Governance flagged: ${flags[0]}`:"Governance check passed with no flags."}
      </p>

      <div style={{display:"flex",gap:10,flexWrap:"wrap",borderTop:`1px solid ${C.border}`,paddingTop:18}}>
        <button onClick={onAdd} disabled={isHeld} title={isHeld?undefined:"Opens ARIA to record your purchase"}
          style={{...edBtn("accent"),opacity:isHeld?0.5:1,cursor:isHeld?"not-allowed":"pointer"}}>
          <Icon name="plus"/>{isHeld?"Already in your portfolio":"Add to portfolio"}
        </button>
        <button onClick={onAsk} style={edBtn()}><Icon name="spark"/>Ask ARIA about {cleanSym(s.symbol)}</button>
      </div>
    </article>
  );
}

// ─── PORTFOLIO RECS ───────────────────────────────────────────────────────────
// Brand-name holdings whose NSE/BSE ticker differs. Mirrors the aliases in
// data/symbol_map.py::YF_SYMBOL_MAP and api/main.py::_NSE_OVERRIDES — only
// needed because /api/portfolio returns yfSymbol=null for every holding.
const HOLDING_TICKER_ALIASES={
  IHCL:"INDHOTEL", BHARATSEAT:"BHARATSE", HITACHIENERGYINDIA:"POWERINDIA", ZOMATO:"ETERNAL",
  MUTHOOT:"MUTHOOTFIN", LNT:"LT", "L&T":"LT", SHAKTIPUMPS:"SHAKTIPUMP",
  GEVERNOVA:"522275", GETDINDIA:"522275",
};
/** Map of ticker (no .NS/.BO) → holding, so a rec can find the holding it is about. */
const holdingsByTicker=portfolio=>{
  const m=new Map();
  (portfolio||[]).filter(h=>h.status!=="exited").forEach(h=>{
    const sym=cleanSym(h.symbol).toUpperCase();
    m.set(sym,h);
    if(h.yfSymbol) m.set(cleanSym(h.yfSymbol).toUpperCase(),h);
    if(HOLDING_TICKER_ALIASES[sym]) m.set(HOLDING_TICKER_ALIASES[sym],h);
  });
  return m;
};
/** Synthesis summaries embed "⚠ …" warnings; split them out as notes. */
const splitSummary=txt=>{
  const parts=String(txt||"").split("⚠");
  const body=[], notes=[];
  const addBody=t=>t.split(/\n+/).map(x=>x.trim()).filter(Boolean).forEach(x=>body.push(x));
  addBody(parts[0]);
  parts.slice(1).forEach(seg=>{
    const lines=seg.split(/\n+/).map(x=>x.trim()).filter(Boolean);
    const note=[];
    while(lines.length&&lines[0].length<=220) note.push(lines.shift());
    if(!note.length&&lines.length) note.push(lines.shift());
    if(note.length){
      const text=note.join(" ");
      notes.push({tone:/QUALIFIED|unverified|could not/i.test(text)?"info":"warn",text});
    }
    lines.forEach(x=>body.push(x));
  });
  return {body,notes};
};
const cleanHeadline=h=>String(h||"").replace(/^[^\p{L}\p{N}]+/u,"").trim();
const recConfLabel=r=>r.isDiscovery?"Hit rate":"Confidence";

function RecCard({rec:r,held,selected,onSelect}){
  const hasTarget=r.targetNum!=null;
  return(
    <button onClick={onSelect} aria-pressed={!!selected} className="ed-card" style={{
      display:"flex",flexDirection:"column",gap:8,width:"100%",textAlign:"left",cursor:"pointer",
      padding:"16px 12px 15px",border:"none",borderTop:`1px solid ${C.borderHi}`,borderRadius:0,
      background:selected?C.surface:"transparent",boxShadow:selected?`inset 0 2px 0 ${C.accent}`:"none",
      color:C.text,fontFamily:SANS}}>
      <span style={{display:"flex",alignItems:"center",gap:7,flexWrap:"wrap",width:"100%"}}>
        <span style={{fontFamily:SERIF,fontSize:19,fontWeight:600}}>{cleanSym(r.symbol)}</span>
        <EdTag tone={actionTone(r.action)}>{r.action}</EdTag>
        {held&&<EdTag tone="info">Held</EdTag>}
        {r.isDiscovery&&<EdTag tone="mute">Discovery</EdTag>}
        {r.price>0&&<span style={{marginLeft:"auto",fontFamily:MONO,fontSize:13}}>{inr(r.price)}</span>}
      </span>
      {r.headline&&<span style={{fontSize:13,color:C.textDim,lineHeight:1.45,display:"-webkit-box",WebkitLineClamp:2,WebkitBoxOrient:"vertical",overflow:"hidden"}}>{cleanHeadline(r.headline)}</span>}
      <span style={{display:"grid",gridTemplateColumns:"repeat(3,minmax(0,1fr))",gap:8,width:"100%"}}>
        <Metric k={recConfLabel(r)} v={r.confidence!=null?`${Math.round(r.confidence)}%`:"—"} title={r.isDiscovery?hitRateTitle(r):"How sure the synthesis model is about this call"}/>
        <Metric k="Upside" v={hasTarget?signedPct(r.upsidePct,0):"—"} color={hasTarget?dirColor(r.upsidePct):undefined} title={hasTarget?undefined:"No target set for this call"}/>
        <Metric k="Risk" v={r.riskScore>0?`${Math.round(r.riskScore)}/100`:"—"} color={r.riskScore>=70?C.red:undefined}/>
      </span>
      <span style={{fontSize:12,color:C.textDim}}>{[r.callDate||r.createdAt?`Called ${fmtDate(r.callDate||r.createdAt)}`:null,r.horizon||null,r.validTill?`valid till ${fmtDate(r.validTill)}`:null].filter(Boolean).join(" · ")}</span>
    </button>
  );
}

function RecDetail({r,holding,narrow,onBack,onAsk,onAdd,onGoPortfolio,valuationCache,setValuationCache}){
  const {body,notes}=splitSummary(r.summary);
  const gov=r.govCheck||{};
  const flags=gov.flags||[];
  const hasTarget=r.targetNum!=null;
  const lvl={price:r.price>0?r.price:(holding?.currentPrice||0),stoplossNum:r.stoplossNum,targetNum:r.targetNum,entryLow:r.entryLow,entryHigh:r.entryHigh};
  const nm=nameOf(r), sec=sectorOf(r)||holding?.sector;
  return(
    <article aria-label={`${cleanSym(r.symbol)} recommendation`} style={{padding:narrow?"16px 4px":"30px 34px",display:"flex",flexDirection:"column",gap:24}}>
      {narrow&&<button onClick={onBack} style={{...edBtn("ghost"),alignSelf:"flex-start",paddingLeft:6}}><Icon name="back"/>Back to list</button>}

      <header style={{display:"flex",justifyContent:"space-between",gap:16,flexWrap:"wrap"}}>
        <div style={{minWidth:0,flex:"1 1 320px"}}>
          <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
            <h2 style={{fontFamily:SERIF,fontSize:narrow?28:36,fontWeight:600,margin:0,lineHeight:1.1}}>{cleanSym(r.symbol)}</h2>
            <EdTag tone={actionTone(r.action)}>{r.action}</EdTag>
            {holding&&<EdTag tone="info">Held</EdTag>}
            {r.isDiscovery&&<EdTag tone="mute">Discovery</EdTag>}
          </div>
          {(nm||sec)&&<p style={{margin:"4px 0 0",color:C.textDim,fontSize:14}}>{[nm||holding?.name,sec].filter(Boolean).join(" · ")}</p>}
          {r.headline&&<p style={{margin:"10px 0 0",fontFamily:SERIF,fontSize:narrow?18:21,lineHeight:1.4,fontWeight:500}}>{cleanHeadline(r.headline)}</p>}
        </div>
        {lvl.price>0&&(
          <div style={{display:"flex",flexDirection:"column",alignItems:narrow?"flex-start":"flex-end",gap:2}}>
            <span style={{fontFamily:MONO,fontSize:24,fontWeight:600}}>{inr(lvl.price)}</span>
            {holding&&holding.avgBuy>0&&<span style={{fontFamily:MONO,fontSize:13,color:dirColor(lvl.price-holding.avgBuy)}}>{signedPct((lvl.price-holding.avgBuy)/holding.avgBuy*100)} on your {holding.qty} shares</span>}
          </div>
        )}
      </header>

      {notes.map((n,i)=><DetailNote key={i} tone={n.tone}>{n.text}</DetailNote>)}
      {gov.withheld&&<DetailNote tone="warn">Governance withheld part of this call because its claims could not be verified.</DetailNote>}

      {body.length>0&&(
        <div style={{display:"flex",flexDirection:"column",gap:12,maxWidth:"72ch"}}>
          {body.map((t,i)=><p key={i} style={{margin:0,fontSize:15,lineHeight:1.65}}>{t}</p>)}
        </div>
      )}

      <div style={{display:"grid",gridTemplateColumns:narrow?"repeat(2,minmax(0,1fr))":"repeat(4,minmax(0,1fr))",gap:10}}>
        {[
          [recConfLabel(r),r.confidence!=null?`${Math.round(r.confidence)}%`:"—",null],
          ["Upside",hasTarget?signedPct(r.upsidePct,0):"—",hasTarget?dirColor(r.upsidePct):null],
          ["Risk score",r.riskScore>0?`${Math.round(r.riskScore)}/100`:"—",r.riskScore>=70?C.red:null],
          ["Position size",r.positionLabel||(r.suggestedPositionPct?`${r.suggestedPositionPct}%`:"—"),null],
        ].map(([k,v,color])=>(
          <div key={k} style={{padding:"12px 14px",borderRadius:6,background:C.panel,minWidth:0}}>
            <div style={{fontSize:11,color:C.textDim,textTransform:"uppercase",letterSpacing:".05em"}}>{k}</div>
            <div style={{fontFamily:k==="Position size"?SANS:MONO,fontSize:k==="Position size"?14:20,fontWeight:600,marginTop:2,color:color||C.text}}>{v}</div>
          </div>
        ))}
      </div>

      <section>
        <h3 style={edH3}>Price levels <span style={{fontFamily:SANS,fontSize:12,color:C.textDim,fontWeight:400}}>{[r.horizon,r.validTill?`valid till ${fmtDate(r.validTill)}`:null].filter(Boolean).join(" · ")}</span></h3>
        <LevelsBar s={lvl}/>
      </section>

      <AgentSignals agents={r.agents} narrow={narrow}/>
      {((r.catalysts||[]).length>0||(r.risks||[]).length>0)&&<CatalystsRisks catalysts={r.catalysts} risks={r.risks} narrow={narrow}/>}

      {r.warrenBot&&<WarrenBotPanel wb={r.warrenBot}/>}
      <ValuationScenariosPanel
        val={valuationCache[r.symbol]}
        symbol={r.symbol}
        onFetch={()=>{
          apiFetch(`/api/valuation/${encodeURIComponent(r.symbol)}`)
            .then(d=>{if(d&&!d.error)setValuationCache(prev=>({...prev,[r.symbol]:d}));})
            .catch(()=>{});
        }}
      />

      <p style={{margin:0,fontSize:12.5,color:C.textDim,display:"flex",gap:6,alignItems:"center"}}>
        <GovShield size={14}/>
        {flags.length?`Governance flagged: ${flags[0]}`:"Governance check passed with no flags."}
      </p>

      <div style={{display:"flex",gap:10,flexWrap:"wrap",borderTop:`1px solid ${C.border}`,paddingTop:18}}>
        {holding
          ?<button onClick={onGoPortfolio} style={edBtn("accent")}>View in portfolio</button>
          :BULL_SIGNALS.has(r.action)&&<button onClick={onAdd} title="Opens ARIA to record your purchase" style={edBtn("accent")}><Icon name="plus"/>Add to portfolio</button>}
        <button onClick={onAsk} style={edBtn()}><Icon name="spark"/>Ask ARIA about {cleanSym(r.symbol)}</button>
      </div>
    </article>
  );
}

const RECS_SORTS=[["newest","Newest first"],["conf","Highest confidence"],["risk","Highest risk"],["upside","Highest upside"],["sym","Symbol A–Z"]];
function RecsTab({recs:_recs,portfolio,apiLoaded,selRecId,setSelRecId,valuationCache,setValuationCache,onAsk,onAdd,onGoPortfolio}){
  const recs=Array.isArray(_recs)?_recs:[];
  const narrow=useNarrow(900);
  const topRef=useRef(null);
  const [filter,setFilter]=useState("all");
  const [source,setSource]=useState("all");
  const [query,setQuery]=useState("");
  const [sort,setSort]=useState("newest");
  const [detailOpen,setDetailOpen]=useState(false);

  const byTicker=holdingsByTicker(portfolio);
  const holdingOf=r=>byTicker.get(cleanSym(r.symbol).toUpperCase())||null;
  const defs=[
    ["all","All",()=>true],
    ["held","My holdings",r=>!!holdingOf(r)],
    ["buy","Buy",r=>BULL_SIGNALS.has(r.action)],
    ["hold","Hold",r=>r.action==="HOLD"],
    ["sell","Sell / Avoid",r=>BEAR_SIGNALS.has(r.action)],
  ];
  const fn=(defs.find(d=>d[0]===filter)||defs[0])[2];
  const srcFn=r=>source==="all"||(source==="discovery")===!!r.isDiscovery;
  const q=query.trim().toLowerCase();
  const matches=r=>!q||[cleanSym(r.symbol),r.name,r.headline,holdingOf(r)?.name].some(v=>v&&String(v).toLowerCase().includes(q));
  const num=v=>v==null||isNaN(v)?-Infinity:v;
  const sortFns={
    newest:(a,b)=>(b.createdAt||"").localeCompare(a.createdAt||""),
    conf:(a,b)=>num(b.confidence)-num(a.confidence),
    risk:(a,b)=>num(b.riskScore)-num(a.riskScore),
    upside:(a,b)=>num(b.targetNum!=null?b.upsidePct:null)-num(a.targetNum!=null?a.upsidePct:null),
    sym:(a,b)=>cleanSym(a.symbol).localeCompare(cleanSym(b.symbol)),
  };
  const base=recs.filter(srcFn);
  const rows=base.filter(fn).filter(matches).sort(sortFns[sort]);
  const selected=rows.find(r=>r.id===selRecId)||(narrow?null:rows[0])||null;
  const showDetail=!!selected&&(!narrow||detailOpen);
  const showList=!narrow||!detailOpen;
  const pick=r=>{setSelRecId(r.id);setDetailOpen(true);if(narrow&&topRef.current) topRef.current.scrollIntoView({block:"start"});};
  const clearFilters=()=>{setFilter("all");setSource("all");setQuery("");};
  const heldCount=recs.filter(r=>holdingOf(r)).length;
  const loading=IS_LIVE&&!apiLoaded&&recs.length===0;

  let empty=null;
  if(rows.length===0&&recs.length>0){
    empty=filter==="held"&&!q&&source==="all"
      ?{t:"No calls on your holdings yet",p:"The 06:00 IST pipeline analyses your holdings each morning. If none appear, the run may have been suppressed. Check Governance → Data sources.",b:"Show all recommendations",a:()=>setFilter("all")}
      :{t:"No calls match",p:`Nothing matches these filters${q?` and the search “${query.trim()}”`:""}.`,b:"Clear filters",a:clearFilters};
  }

  return(
    <div ref={topRef} style={{animation:"fadeUp .3s ease",maxWidth:1440,margin:"0 auto",scrollMarginTop:16}}>
      {showList&&(
        <header style={{display:"flex",flexDirection:"column",gap:14,marginBottom:18}}>
          <div>
            <h1 style={{fontFamily:SERIF,fontSize:narrow?26:32,fontWeight:600,margin:0,letterSpacing:-.3,lineHeight:1.15}}>Portfolio recommendations</h1>
            <p style={{margin:"4px 0 0",fontSize:14,color:C.textDim}}>
              Agent calls on your holdings and new ideas · updated daily at 06:00 IST{recs.length?` · ${recs.length} calls`:""}
            </p>
          </div>
          {recs.length>0&&(
            <div style={{display:"flex",gap:10,flexWrap:"wrap",alignItems:"center"}}>
              <div role="group" aria-label="Filter recommendations" style={{display:"flex",gap:6,flexWrap:"wrap"}}>
                {defs.map(([id,label,f])=>(
                  <button key={id} onClick={()=>setFilter(id)} aria-pressed={filter===id} style={edChip(filter===id)}>
                    {label}<span style={{fontFamily:MONO,fontSize:11,color:C.textDim}}>{base.filter(f).length}</span>
                  </button>
                ))}
              </div>
              <label style={{display:"flex",alignItems:"center",gap:8,flex:"1 1 220px",maxWidth:narrow?"none":340,minHeight:38,
                padding:"0 4px 0 12px",border:`1px solid ${C.border}`,borderRadius:6,background:C.surface,color:C.textDim}}>
                <Icon name="search" size={15}/>
                <span style={{position:"absolute",width:1,height:1,overflow:"hidden",clip:"rect(0 0 0 0)"}}>Search recommendations</span>
                <input type="search" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search symbol, company or headline"
                  style={{flex:1,minWidth:0,border:0,background:"transparent",color:C.text,fontSize:13.5,outline:"none",height:36}}/>
                {query&&<button onClick={()=>setQuery("")} aria-label="Clear search"
                  style={{width:32,height:32,display:"inline-grid",placeItems:"center",border:0,background:"transparent",color:C.textDim,cursor:"pointer",borderRadius:4}}><Icon name="close" size={14}/></button>}
              </label>
              <label style={{display:"flex",alignItems:"center",gap:8,fontSize:13,color:C.textDim}}>Source
                <select value={source} onChange={e=>setSource(e.target.value)} style={edSelect}>
                  <option value="all">All sources</option>
                  <option value="pipeline">Daily pipeline</option>
                  <option value="discovery">Discovery</option>
                </select>
              </label>
              <label style={{display:"flex",alignItems:"center",gap:8,fontSize:13,color:C.textDim}}>Sort
                <select value={sort} onChange={e=>setSort(e.target.value)} style={edSelect}>
                  {RECS_SORTS.map(([v,l])=><option key={v} value={v}>{l}</option>)}
                </select>
              </label>
            </div>
          )}
          {recs.length>0&&heldCount===0&&(portfolio||[]).length>0&&(
            <p role="note" style={{margin:0,display:"flex",gap:8,alignItems:"center",fontSize:13,color:C.textDim}}>
              <Icon name="info" style={{color:C.blue}}/>None of today's calls cover your holdings yet.
            </p>
          )}
        </header>
      )}

      {loading&&(
        <div aria-busy="true" style={{display:"flex",flexDirection:"column",gap:12,maxWidth:400}}>
          {[0,1,2,3,4].map(i=><div key={i} style={{height:120,borderRadius:6,background:C.panel,animation:"pulse 1.4s ease-in-out infinite"}}/>)}
        </div>
      )}

      {IS_LIVE&&apiLoaded&&recs.length===0&&(
        <div style={{display:"flex",flexDirection:"column",alignItems:"center",textAlign:"center",gap:10,padding:"48px 20px",border:`1px dashed ${C.borderHi}`,borderRadius:8,color:C.textDim}}>
          <div style={{fontFamily:SERIF,fontSize:20,fontWeight:600,color:C.text}}>No recommendations yet</div>
          <p style={{margin:0,maxWidth:460,fontSize:13.5,lineHeight:1.55}}>The pipeline publishes BUY / HOLD / SELL calls at 06:00 IST. If none appear by 06:30, check Governance → Data sources to see whether the run failed or every call was suppressed.</p>
        </div>
      )}

      {recs.length>0&&(
        <div style={{display:narrow?"block":"grid",gridTemplateColumns:"minmax(300px,400px) minmax(0,1fr)",gap:24,alignItems:"start"}}>
          {showList&&(
            <div aria-label="Recommendations" style={{display:"flex",flexDirection:"column",borderBottom:`1px solid ${C.borderHi}`}}>
              {empty?(
                <div style={{padding:"36px 16px",textAlign:"center",color:C.textDim,display:"flex",flexDirection:"column",gap:10,alignItems:"center",borderTop:`1px solid ${C.borderHi}`}}>
                  <div style={{fontFamily:SERIF,fontSize:18,fontWeight:600,color:C.text}}>{empty.t}</div>
                  <p style={{margin:0,fontSize:13,lineHeight:1.5}}>{empty.p}</p>
                  <button onClick={empty.a} style={edBtn()}>{empty.b}</button>
                </div>
              ):rows.map(r=>(
                <RecCard key={r.id} rec={r} held={!!holdingOf(r)} selected={!!selected&&selected.id===r.id&&(!narrow||detailOpen)} onSelect={()=>pick(r)}/>
              ))}
            </div>
          )}
          {showDetail&&(
            <div style={narrow?{}:{position:"sticky",top:0,maxHeight:"calc(100vh - 150px)",overflowY:"auto",
              border:`1px solid ${C.borderHi}`,borderRadius:8,background:C.surface}}>
              <RecDetail key={selected.id} r={selected} holding={holdingOf(selected)} narrow={narrow}
                onBack={()=>setDetailOpen(false)}
                onAsk={()=>onAsk(selected,holdingOf(selected))}
                onAdd={()=>onAdd(selected)}
                onGoPortfolio={onGoPortfolio}
                valuationCache={valuationCache} setValuationCache={setValuationCache}/>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── RESEARCH DISCOVERY TAB ───────────────────────────────────────────────────
const DISCOVERY_PAGE=20;
const DISCOVERY_SORTS=[
  ["newest","Newest first"],["upside","Highest upside"],["hit","Highest hit rate"],
  ["score","Signal score"],["since","Best since call"],
];
function ResearchDiscoveryTab({portfolio, onAddToPortfolio, onAskAbout, onOpenARIA, onOpenRunSummary, discoveryUniverse: _du, discoveryRuns: _dr, apiLoaded, valuationCache, setValuationCache}){
  const universe=Array.isArray(_du)?_du:[];
  const runs=Array.isArray(_dr)?_dr:[];
  const narrow=useNarrow(900);
  const topRef=useRef(null);
  const [status,setStatus]=useState("all");
  const [sector,setSector]=useState("all");
  const [query,setQuery]=useState("");
  const [sort,setSort]=useState("newest");
  const [limit,setLimit]=useState(DISCOVERY_PAGE);
  const [selectedId,setSelectedId]=useState(null);
  const [detailOpen,setDetailOpen]=useState(false);
  const [howOpen,setHowOpen]=useState(false);

  // One card per symbol (keep the highest-scoring row if the API ever sends duplicates).
  const deduped=[...universe].sort((a,b)=>(b.discoveryScore||0)-(a.discoveryScore||0))
    .filter((s,i,arr)=>arr.findIndex(x=>x.symbol===s.symbol)===i);
  const heldSyms=new Set((portfolio||[]).filter(h=>h.status!=="exited").map(h=>cleanSym(h.symbol)));
  const criticalStocks=deduped.filter(isCriticalDiscovery);

  const statusDefs=[
    ["all","All",()=>true],
    ...(criticalStocks.length?[["critical","Critical",isCriticalDiscovery]]:[]),
    ["active","Active",s=>s.callStatus!=="WEAKENED"],
    ["weakened","Weakened",s=>s.callStatus==="WEAKENED"],
  ];
  const statusFn=(statusDefs.find(d=>d[0]===status)||statusDefs[0])[2];
  const sectors=[...new Set(deduped.map(sectorOf).filter(Boolean))].sort();
  const q=query.trim().toLowerCase();
  const matches=s=>!q||[cleanSym(s.symbol),s.name,sectorOf(s)].some(v=>v&&v.toLowerCase().includes(q));
  const num=v=>v==null||isNaN(v)?-Infinity:v;
  const sortFns={
    newest:(a,b)=>(b.lastDiscoveredAt||b.callDate||"").localeCompare(a.lastDiscoveredAt||a.callDate||""),
    upside:(a,b)=>num(b.upsidePct)-num(a.upsidePct),
    hit:(a,b)=>num(b.confidence)-num(a.confidence),
    score:(a,b)=>num(b.signalScore)-num(a.signalScore),
    since:(a,b)=>num(b.returnSinceCallPct)-num(a.returnSinceCallPct),
  };
  const rows=deduped.filter(statusFn).filter(s=>sector==="all"||sectorOf(s)===sector).filter(matches)
    .sort((a,b)=>(isCriticalDiscovery(b)?1:0)-(isCriticalDiscovery(a)?1:0)||sortFns[sort](a,b));
  const shown=rows.slice(0,limit);
  // Desktop always shows a detail: fall back to the first row when the
  // selection is filtered out. On a phone the detail opens only on tap.
  const selected=rows.find(s=>s.id===selectedId)||(narrow?null:rows[0])||null;
  const showDetail=!!selected&&(!narrow||detailOpen);
  const showList=!narrow||!detailOpen;

  const resetPaging=()=>setLimit(DISCOVERY_PAGE);
  const pick=s=>{
    setSelectedId(s.id); setDetailOpen(true);
    if(narrow&&topRef.current) topRef.current.scrollIntoView({block:"start"});
  };
  const clearFilters=()=>{setStatus("all");setSector("all");setQuery("");resetPaging();};
  const today=new Date().toISOString().slice(0,10);
  const newest=deduped.reduce((m,s)=>(s.lastDiscoveredAt||"")>m?s.lastDiscoveredAt:m,"");
  const loading=IS_LIVE&&!apiLoaded&&universe.length===0;

  return(
    <div ref={topRef} style={{animation:"fadeUp .3s ease",maxWidth:1440,margin:"0 auto",scrollMarginTop:16}}>
      <CriticalOpportunityBanner stocks={criticalStocks} onSelect={id=>{setSelectedId(id);setStatus("all");setDetailOpen(true);}} onOpenARIA={onOpenARIA}/>

      {showList&&(
        <header style={{display:"flex",flexDirection:"column",gap:14,marginBottom:18}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-end",gap:12,flexWrap:"wrap"}}>
            <div>
              <h1 style={{fontFamily:SERIF,fontSize:narrow?26:32,fontWeight:600,margin:0,letterSpacing:-.3,lineHeight:1.15}}>Discovery</h1>
              <p style={{margin:"4px 0 0",fontSize:14,color:C.textDim}}>
                New ideas from the NSE universe, confirmed by 7 agents{deduped.length?` · ${deduped.length} live calls`:""}
              </p>
            </div>
            <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
              {onOpenRunSummary&&<button onClick={onOpenRunSummary} style={edBtn()}><Icon name="run"/>What ran today?</button>}
              <button onClick={()=>onOpenARIA("discovery")} style={edBtn()}><Icon name="spark"/>Ask ARIA</button>
            </div>
          </div>

          <div>
            <button onClick={()=>setHowOpen(o=>!o)} aria-expanded={howOpen}
              style={{...edBtn("ghost"),minHeight:32,padding:"0 4px",fontSize:13,color:C.accent}}>
              <Icon name="chevron" size={14} style={{transform:howOpen?"rotate(90deg)":"none",transition:"transform .15s"}}/>How discovery works
            </button>
            {howOpen&&(
              <ol style={{margin:"8px 0 0",padding:"0 0 0 20px",display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",gap:"8px 24px",fontSize:13,color:C.textDim,lineHeight:1.5}}>
                <li><b style={{color:C.text}}>Scan.</b> At 10:30 IST the screener checks 200 of about 1,700 NSE stocks, so every stock is seen roughly every 9 days.</li>
                <li><b style={{color:C.text}}>Screen.</b> Stocks passing several fast filters (momentum, valuation vs sector, growth, institutional holding) make the shortlist.</li>
                <li><b style={{color:C.text}}>Analyse.</b> Each shortlisted stock goes through all 7 agents, the same as your portfolio stocks.</li>
                <li><b style={{color:C.text}}>Track.</b> Calls last 90 days and are re-checked daily at 16:45; weakened calls aren't extended.</li>
              </ol>
            )}
          </div>

          {deduped.length>0&&(
            <div style={{display:"flex",gap:10,flexWrap:"wrap",alignItems:"center"}}>
              <div role="group" aria-label="Filter by status" style={{display:"flex",gap:6,flexWrap:"wrap"}}>
                {statusDefs.map(([id,label,fn])=>(
                  <button key={id} onClick={()=>{setStatus(id);resetPaging();}} aria-pressed={status===id} style={edChip(status===id)}>
                    {label}<span style={{fontFamily:MONO,fontSize:11,color:C.textDim}}>{deduped.filter(fn).length}</span>
                  </button>
                ))}
              </div>
              <label style={{display:"flex",alignItems:"center",gap:8,flex:"1 1 220px",maxWidth:narrow?"none":340,minHeight:38,
                padding:"0 4px 0 12px",border:`1px solid ${C.border}`,borderRadius:6,background:C.surface,color:C.textDim}}>
                <Icon name="search" size={15}/>
                <span style={{position:"absolute",width:1,height:1,overflow:"hidden",clip:"rect(0 0 0 0)"}}>Search discoveries</span>
                <input type="search" value={query} onChange={e=>{setQuery(e.target.value);resetPaging();}}
                  placeholder="Search symbol, company or sector"
                  style={{flex:1,minWidth:0,border:0,background:"transparent",color:C.text,fontSize:13.5,outline:"none",height:36}}/>
                {query&&<button onClick={()=>{setQuery("");resetPaging();}} aria-label="Clear search"
                  style={{width:32,height:32,display:"inline-grid",placeItems:"center",border:0,background:"transparent",color:C.textDim,cursor:"pointer",borderRadius:4}}><Icon name="close" size={14}/></button>}
              </label>
              {sectors.length>1&&(
                <label style={{display:"flex",alignItems:"center",gap:8,fontSize:13,color:C.textDim}}>Sector
                  <select value={sector} onChange={e=>{setSector(e.target.value);resetPaging();}} style={edSelect}>
                    <option value="all">All sectors</option>
                    {sectors.map(x=><option key={x} value={x}>{x}</option>)}
                  </select>
                </label>
              )}
              <label style={{display:"flex",alignItems:"center",gap:8,fontSize:13,color:C.textDim}}>Sort
                <select value={sort} onChange={e=>setSort(e.target.value)} style={edSelect}>
                  {DISCOVERY_SORTS.map(([v,l])=><option key={v} value={v}>{l}</option>)}
                </select>
              </label>
            </div>
          )}

          {IS_LIVE&&newest&&newest<today&&(
            <p role="note" style={{margin:0,display:"flex",gap:8,alignItems:"center",fontSize:13,color:C.textDim}}>
              <Icon name="info" style={{color:C.blue}}/>No new finds yet today (the screener runs at 10:30 IST). Latest find: {fmtDate(newest)}.
            </p>
          )}
        </header>
      )}

      {loading&&(
        <div aria-busy="true" style={{display:"flex",flexDirection:"column",gap:12,maxWidth:400}}>
          {[0,1,2,3,4].map(i=><div key={i} style={{height:110,borderRadius:6,background:C.panel,animation:"pulse 1.4s ease-in-out infinite"}}/>)}
        </div>
      )}

      {IS_LIVE&&apiLoaded&&universe.length===0&&(
        <div style={{display:"flex",flexDirection:"column",alignItems:"center",textAlign:"center",gap:10,padding:"48px 20px",border:`1px dashed ${C.borderHi}`,borderRadius:8,color:C.textDim}}>
          <Icon name="search" size={28}/>
          <div style={{fontFamily:SERIF,fontSize:20,fontWeight:600,color:C.text}}>No live discovery calls</div>
          <p style={{margin:0,maxWidth:460,fontSize:13.5,lineHeight:1.55}}>The screener runs at 10:30 IST. If nothing appears by 11:30, check Governance → Data sources, or run <code style={{fontFamily:MONO}}>python -m agents.discovery_screener</code>.</p>
          {onOpenRunSummary&&<button onClick={onOpenRunSummary} style={edBtn()}>Ask ARIA what ran today</button>}
        </div>
      )}

      {deduped.length>0&&(
        <div style={{display:narrow?"block":"grid",gridTemplateColumns:"minmax(300px,400px) minmax(0,1fr)",gap:24,alignItems:"start"}}>
          {showList&&(
            <div aria-label="Discovery calls" style={{display:"flex",flexDirection:"column",borderBottom:`1px solid ${C.borderHi}`}}>
              {rows.length===0?(
                <div style={{padding:"36px 16px",textAlign:"center",color:C.textDim,display:"flex",flexDirection:"column",gap:10,alignItems:"center",borderTop:`1px solid ${C.borderHi}`}}>
                  <div style={{fontFamily:SERIF,fontSize:18,fontWeight:600,color:C.text}}>No calls match</div>
                  <p style={{margin:0,fontSize:13}}>Nothing matches these filters{q?` and the search “${query.trim()}”`:""}.</p>
                  <button onClick={clearFilters} style={edBtn()}>Clear filters</button>
                </div>
              ):shown.map(s=>(
                <DiscoveryCard key={s.id} stock={s} selected={!!selected&&selected.id===s.id&&(!narrow||detailOpen)} onSelect={()=>pick(s)}/>
              ))}
              {rows.length>shown.length&&(
                <button onClick={()=>setLimit(l=>l+DISCOVERY_PAGE)} style={{...edBtn("ghost"),margin:"8px 0",color:C.accent}}>
                  Show {Math.min(DISCOVERY_PAGE,rows.length-shown.length)} more of {rows.length-shown.length}
                </button>
              )}
            </div>
          )}
          {showDetail&&(
            <div style={narrow?{}:{position:"sticky",top:0,maxHeight:"calc(100vh - 150px)",overflowY:"auto",
              border:`1px solid ${C.borderHi}`,borderRadius:8,background:C.surface}}>
              <DiscoveryDetail
                key={selected.id}
                s={selected} narrow={narrow}
                isHeld={heldSyms.has(cleanSym(selected.symbol))}
                onBack={()=>setDetailOpen(false)}
                onAdd={()=>onAddToPortfolio(selected)}
                onAsk={()=>(onAskAbout||onAddToPortfolio)(selected)}
                valuationCache={valuationCache} setValuationCache={setValuationCache}
              />
            </div>
          )}
          {!narrow&&!selected&&rows.length>0&&(
            <div style={{padding:40,textAlign:"center",color:C.textDim}}>Pick a call from the list to see the full breakdown.</div>
          )}
        </div>
      )}

      {showList&&(
        <div style={{marginTop:28}}>
          {deduped.length>0&&<DiscoveryRecsTable universe={deduped}/>}
          <DiscoveryRunsPanel runs={runs} apiLoaded={apiLoaded}/>
        </div>
      )}
    </div>
  );
}

/* Consolidated table of all current discovery recommendations, sorted by date desc.
   Shows entry / target / stoploss so users can see the actionable levels at a glance. */
function DiscoveryRecsTable({universe}){
  const PAGE=30;
  const [page,setPage]=useState(0);
  const [collapsed,setCollapsed]=useState(false);
  const sorted=[...universe].sort((a,b)=>(b.createdAt||"").localeCompare(a.createdAt||""));
  const total=sorted.length;
  const pages=Math.ceil(total/PAGE);
  const rows=sorted.slice(page*PAGE,(page+1)*PAGE);
  const today=new Date().toISOString().slice(0,10);
  const ac=s=>BULL_SIGNALS.has(s)?C.green:BEAR_SIGNALS.has(s)?C.red:C.accent;
  return(
    <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,marginTop:14,marginBottom:14}}>
      <div role="button" tabIndex={0} aria-expanded={!collapsed} onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();e.currentTarget.click();}}} onClick={()=>setCollapsed(p=>!p)} style={{display:"flex",alignItems:"center",gap:8,padding:"12px 14px",cursor:"pointer",minHeight:48}}>
        <span style={{fontFamily:SERIF,fontSize:18,fontWeight:600,color:C.text}}>All discovery calls</span>
        <span style={{fontSize:11,color:C.muted,marginLeft:2}}>{total} unique ideas · sorted by date</span>
        <span style={{marginLeft:"auto",fontSize:12.5,color:C.muted}}>{collapsed?"▶":"▼"}</span>
      </div>
      {!collapsed&&(
        <div style={{overflowX:"auto"}}>
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
            <thead>
              <tr style={{borderBottom:`1px solid ${C.border}`}}>
                {["Symbol","Action","Tier","Conf%","Upside%","Entry","Target","Stop","Sector","Date","Valid Till"].map(h=>(
                  <th key={h} style={{padding:"5px 8px",textAlign:"left",color:C.muted,fontWeight:600,whiteSpace:"nowrap"}}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(s=>{
                const isStale=s.validTill&&s.validTill<today;
                const isCrit=isCriticalDiscovery(s);
                return(
                  <tr key={s.id} style={{borderBottom:`1px solid ${C.border}22`,opacity:isStale?0.55:1,background:isCrit?C.green+"08":"transparent"}}>
                    <td style={{padding:"5px 8px",fontFamily:MONO,color:C.text,fontWeight:700,whiteSpace:"nowrap"}}>
                      {s.symbol}{isCrit&&<span style={{marginLeft:6}}><Tag color={C.accent} small>Critical</Tag></span>}
                    </td>
                    <td style={{padding:"5px 8px"}}><span style={{color:ac(s.action),fontWeight:700}}>{s.action}</span></td>
                    <td style={{padding:"5px 8px"}}><span style={{color:isCrit?C.green:C.cyan,fontSize:10.5}}>{s.opportunityTier||"STANDARD"}</span></td>
                    <td style={{padding:"5px 8px",fontFamily:MONO,color:s.confidence>=75?C.green:s.confidence>=60?C.accent:C.muted}}>{s.confidence?.toFixed(0)}%</td>
                    <td style={{padding:"5px 8px",fontFamily:MONO,color:C.green,fontWeight:700}}>+{s.upsidePct?.toFixed(0)}%</td>
                    <td style={{padding:"5px 8px",fontFamily:MONO,color:C.accent}}>{s.entry||"—"}</td>
                    <td style={{padding:"5px 8px",fontFamily:MONO,color:C.green}}>{s.target||"—"}</td>
                    <td style={{padding:"5px 8px",fontFamily:MONO,color:C.red}}>{s.stoploss||"—"}</td>
                    <td style={{padding:"5px 8px",color:C.textDim,maxWidth:100,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{s.sector||"—"}</td>
                    <td style={{padding:"5px 8px",color:C.muted,whiteSpace:"nowrap"}}>{(s.createdAt||"").slice(0,10)}</td>
                    <td style={{padding:"5px 8px",color:isStale?C.red:C.muted,whiteSpace:"nowrap"}}>{s.validTill||"—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {pages>1&&(
            <div style={{display:"flex",alignItems:"center",gap:6,padding:"8px 14px",borderTop:`1px solid ${C.border}`}}>
              <button disabled={page===0} onClick={()=>setPage(p=>p-1)} style={{background:C.panel,border:`1px solid ${C.border}`,borderRadius:4,padding:"3px 9px",color:C.muted,cursor:page===0?"not-allowed":"pointer",fontSize:12}}>← Prev</button>
              <span style={{fontSize:11,color:C.muted}}>Page {page+1} / {pages}</span>
              <button disabled={page>=pages-1} onClick={()=>setPage(p=>p+1)} style={{background:C.panel,border:`1px solid ${C.border}`,borderRadius:4,padding:"3px 9px",color:C.muted,cursor:page>=pages-1?"not-allowed":"pointer",fontSize:12}}>Next →</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* Collapsible panel showing the list of symbols pre-screened by the discovery
   screener each day. Opens to show: all slice symbols, which passed filters,
   which became discovery recommendations. One accordion item per run day. */
function DiscoveryRunsPanel({runs,apiLoaded}){
  const [open,setOpen]=useState(false);
  const [expandedDay,setExpandedDay]=useState(null);
  const hasRuns=runs&&runs.length>0;

  const panelStyle={
    background:C.surface,
    border:`1px solid ${C.border}`,
    borderRadius:10,
    marginTop:16,
    overflow:"hidden",
  };
  const headerStyle={
    display:"flex",alignItems:"center",justifyContent:"space-between",
    padding:"10px 14px",cursor:"pointer",userSelect:"none",
    background:C.panel,
    borderBottom:open?`1px solid ${C.border}`:"none",
  };

  const latestCov=hasRuns?(runs[0].coverageStats||{}):{};

  return(
    <div style={panelStyle}>
      {/* Header — always visible, click to expand */}
      <div role="button" tabIndex={0} aria-expanded={open} onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();e.currentTarget.click();}}} style={headerStyle} onClick={()=>setOpen(o=>!o)}>
        <div style={{display:"flex",alignItems:"center",gap:8}}>
          
          <span style={{fontSize:12.5,fontWeight:700,color:C.text}}>Daily Screened Stocks</span>
          {hasRuns&&(
            <span style={{fontSize:11,color:C.muted,fontFamily:MONO,marginLeft:4}}>
              {runs[0].runDate} — {runs[0].totalScreened} screened / {runs[0].totalPassed} passed / {runs[0].totalDiscoveries} promoted
            </span>
          )}
          {!hasRuns&&IS_LIVE&&apiLoaded&&(
            <span style={{fontSize:11,color:C.muted}}>no runs yet — runs at 06:00 IST</span>
          )}
        </div>
        <div style={{display:"flex",alignItems:"center",gap:10}}>
          {hasRuns&&latestCov.cycle_pct_complete!=null&&(
            <span style={{fontSize:11,color:C.cyan,fontFamily:MONO}}>
              cycle {latestCov.cycle_pct_complete}% · est. full coverage {latestCov.est_full_coverage}
            </span>
          )}
          <span style={{fontSize:14,color:C.muted,transform:open?"rotate(180deg)":"none",transition:"transform .2s"}}>▾</span>
        </div>
      </div>

      {/* Body — only when open */}
      {open&&(
        <div style={{padding:"10px 14px",maxHeight:"70vh",overflowY:"auto"}}>
          {!hasRuns&&(
            <EmptyState icon="🔭" title="No screener runs recorded yet"
              sub={"The discovery screener logs daily run details here after each 06:00 IST run.\n\nIf you just deployed, run: python -m agents.discovery_screener --no-save"}/>
          )}
          {hasRuns&&runs.map(run=>{
            const isExp=expandedDay===run.runDate;
            const discSet=new Set(run.discoverySymbols||[]);
            const passSet=new Set(run.passedSymbols||[]);
            return(
              <div key={run.runDate} style={{marginBottom:8,border:`1px solid ${C.border}`,borderRadius:7,overflow:"hidden"}}>
                {/* Day header */}
                <div
                  style={{display:"flex",alignItems:"center",justifyContent:"space-between",
                    padding:"7px 10px",cursor:"pointer",background:C.panel,
                    borderBottom:isExp?`1px solid ${C.border}`:"none"}}
                  onClick={()=>setExpandedDay(isExp?null:run.runDate)}>
                  <div style={{display:"flex",alignItems:"center",gap:10}}>
                    <span style={{fontSize:12,fontWeight:700,color:C.cyan,fontFamily:MONO}}>{run.runDate}</span>
                    <span style={{fontSize:11,color:C.muted}}>{run.totalScreened} screened</span>
                    <span style={{fontSize:11,color:C.green}}>{run.totalPassed} passed filters</span>
                    {run.totalDiscoveries>0&&(
                      <span style={{fontSize:11,color:C.accent,fontWeight:700}}>⚡ {run.totalDiscoveries} promoted</span>
                    )}
                  </div>
                  <span style={{fontSize:13,color:C.muted,transform:isExp?"rotate(180deg)":"none",transition:"transform .2s"}}>▾</span>
                </div>
                {/* Symbol grid — only when day is expanded */}
                {isExp&&(
                  <div style={{padding:"8px 10px"}}>
                    {/* Coverage stats mini-bar */}
                    {run.coverageStats&&run.coverageStats.universe_size&&(
                      <div style={{display:"flex",gap:12,marginBottom:8,flexWrap:"wrap"}}>
                        {[
                          ["Universe",run.coverageStats.universe_size],
                          ["Slice",run.coverageStats.slice_size],
                          ["Cycle day",`${run.coverageStats.today_position}/${run.coverageStats.cycle_length_days}`],
                          ["Coverage",`${run.coverageStats.cycle_pct_complete}%`],
                          ["~passes/mo",run.coverageStats.monthly_passes],
                        ].map(([l,v])=>(
                          <div key={l} style={{background:C.bg,border:`1px solid ${C.border}`,borderRadius:5,padding:"3px 8px",minWidth:70}}>
                            <div style={{fontSize:10.5,color:C.muted}}>{l}</div>
                            <div style={{fontSize:12,fontWeight:700,color:C.cyan,fontFamily:MONO}}>{v}</div>
                          </div>
                        ))}
                      </div>
                    )}
                    {/* Legend */}
                    <div style={{display:"flex",gap:12,marginBottom:6}}>
                      {[["⚡",C.accent,"promoted to rec"],["✓",C.green,"passed filters"],["·",C.textDim,"screened"]].map(([ic,col,lbl])=>(
                        <span key={lbl} style={{fontSize:11,color:col}}>{ic} {lbl}</span>
                      ))}
                    </div>
                    {/* Symbol pills */}
                    <div style={{display:"flex",flexWrap:"wrap",gap:4}}>
                      {(run.sliceSymbols||[]).map(sym=>{
                        const base=sym.replace(".NS","").replace(".BO","");
                        const isDisc=discSet.has(sym)||discSet.has(base);
                        const isPass=passSet.has(sym)||passSet.has(base);
                        return(
                          <span key={sym} style={{
                            fontSize:10.5,fontFamily:MONO,padding:"2px 6px",borderRadius:3,
                            background: isDisc?C.accent+"22": isPass?C.green+"18":C.bg,
                            color:      isDisc?C.accent:        isPass?C.green:   C.muted,
                            border:`1px solid ${isDisc?C.accent+"55":isPass?C.green+"33":C.border}`,
                            fontWeight: isDisc?700:400,
                          }}>{isDisc?"⚡ ":isPass?"✓ ":""}{base}</span>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── GOVERNANCE RESEARCH AGENT TAB ───────────────────────────────────────────
export function GovernanceResearchTab({onOpenARIA, researchFeed: _rf, apiLoaded, liveAttribution}){
  const _feed   = Array.isArray(_rf) ? _rf : [];
  const govNarrow = useNarrow(900);
  // Agent debate log entries live inside each research proposal's debate_log JSONB field.
  // We extract and flatten them from the live feed — no separate mock array needed.
  const _debate = _feed.flatMap(r=>(r.debateLog||r.debate_log||[]));
  const [sec,setSec]=useState("research");
  const [selectedPaper,setSelectedPaper]=useState(_feed[0]?.id || "r1");
  const paper = _feed.find(r=>r.id===selectedPaper);
  const tagColors={Architecture:C.blue,"Signal Quality":C.green,Governance:C.purple,Performance:C.teal};
  const debateColors={for:C.green,against:C.red,abstain:C.muted};
  const statusColors={approved:C.green,debating:C.accent,pending_review:C.blue};

  return(
    <div style={{animation:"fadeUp .3s ease"}}>
      <div style={{display:"flex",alignItems:"center",gap:9,marginBottom:16}}>
        <div>
          <h1 style={{fontFamily:SERIF,fontSize:28,fontWeight:600,color:C.text,letterSpacing:-.3,lineHeight:1.2,margin:0}}>Governance Research & Self-Improvement Agent</h1>
          <div style={{fontSize:11,color:C.muted}}>Continuously monitors AI research, whitepapers, and market analysis literature to propose system upgrades · Agent debate before user review</div>
        </div>
        <div style={{marginLeft:"auto",background:C.purple+"12",border:`1px solid ${C.purple}33`,borderRadius:5,padding:"3px 9px",display:"flex",alignItems:"center",gap:4}}>
          <Dot color={C.purple} pulse/><span style={{fontSize:11,color:C.purple,fontWeight:600}}>Runs daily · 04:00 IST</span>
        </div>
      </div>

      <div style={{display:"flex",gap:3,marginBottom:14,borderBottom:`1px solid ${C.border}`}}>
        {[["research",`Research Feed (${_feed.length})`],["debate",`Agent Debates (${_debate.length} entries)`],["health","Agent Health"]].map(([id,lbl])=>(
          <button key={id} onClick={()=>setSec(id)} style={{background:sec===id?C.panel:"transparent",border:`1px solid ${sec===id?C.purple+"44":"transparent"}`,borderRadius:"5px 5px 0 0",padding:"6px 12px",color:sec===id?C.purple:C.muted,fontSize:12,fontWeight:sec===id?700:400,cursor:"pointer",marginBottom:-1}}>{lbl}</button>
        ))}
      </div>

      {/* RESEARCH FEED */}
      {sec==="research"&&(
        IS_LIVE && apiLoaded && _feed.length===0
          ? <EmptyState icon="🧬" title="No research proposals yet"
              sub="The governance research agent scans arXiv, SSRN, and finance journals daily at 04:00 IST. Proposals appear here after the first successful scheduler run." />
          : <div style={{display:"grid",gridTemplateColumns:govNarrow?"minmax(0,1fr)":"340px minmax(0,1fr)",gap:16,alignItems:"start"}}>
          <div>
            <div style={{fontSize:12,color:C.muted,marginBottom:8}}>Sources monitored: arXiv, SSRN, Google Scholar, Anthropic blog, Morgan Stanley Research, SEBI circulars, RBI papers, DeepMind, OpenAI, academic finance journals</div>
            {_feed.map(r=>{
              const tc=tagColors[r.tag]||C.muted;
              const sc2=statusColors[r.debateStatus]||C.muted;
              return(
                <div key={r.id} onClick={()=>setSelectedPaper(r.id)} style={{
                  background:selectedPaper===r.id?C.panel:C.surface,
                  border:`1px solid ${selectedPaper===r.id?C.purple+"55":C.border}`,
                  borderRadius:8,padding:11,cursor:"pointer",marginBottom:7,
                  borderLeft:`3px solid ${tc}`,
                }}>
                  <div style={{display:"flex",gap:5,marginBottom:4,flexWrap:"wrap"}}>
                    <Tag color={tc} small>{r.tag}</Tag>
                    <Tag color={sc2} small>{r.debateStatus.replace("_"," ").toUpperCase()}</Tag>
                    <span style={{fontSize:11,color:C.muted,marginLeft:"auto"}}>{r.date}</span>
                  </div>
                  <div style={{fontSize:12.5,fontWeight:600,color:C.text,marginBottom:2,lineHeight:1.4}}>{r.title}</div>
                  <div style={{fontSize:11,color:C.muted,marginBottom:5}}>{r.source} · Relevance: <span style={{color:C.accent,fontWeight:700}}>{r.relevance}%</span></div>
                  <div style={{display:"flex",gap:10}}>
                    <span style={{fontSize:12,color:C.green}}>For {r.votes.for}</span>
                    <span style={{fontSize:12,color:C.red}}>Against {r.votes.against}</span>
                    <span style={{fontSize:12,color:C.textDim}}>Abstain {r.votes.abstain}</span>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Paper detail */}
          {paper&&(
            <div style={{animation:"fadeUp .2s ease"}}>
              <div style={{display:"flex",gap:6,marginBottom:8,flexWrap:"wrap"}}>
                <Tag color={tagColors[paper.tag]||C.muted}>{paper.tag}</Tag>
                <Tag color={statusColors[paper.debateStatus]||C.muted}>{paper.debateStatus.replace("_"," ").toUpperCase()}</Tag>
                <span style={{fontSize:11,color:C.muted,alignSelf:"center"}}>{paper.source} · {paper.date}</span>
              </div>
              <div style={{fontSize:14,fontWeight:700,color:C.text,marginBottom:5,lineHeight:1.4}}>{paper.title}</div>

              <div style={{background:C.bg,border:`1px solid ${C.border}`,borderRadius:7,padding:10,marginBottom:10}}>
                <div style={{fontSize:12,fontWeight:700,color:C.textDim,marginBottom:4}}>Summary</div>
                <div style={{fontSize:12.5,color:C.textDim,lineHeight:1.65}}>{paper.summary}</div>
                {(paper.authors?.length>0||paper.venue||paper.publishedAt)&&(
                  <div style={{fontSize:11,color:C.muted,marginTop:6}}>
                    {[paper.authors?.slice(0,3).join(", ")+(paper.authors?.length>3?" et al.":""),paper.venue,paper.publishedAt?.slice(0,10)].filter(Boolean).join(" · ")}
                  </div>
                )}
                {paper.relevanceReason&&(
                  <div style={{fontSize:12,color:C.textDim,marginTop:6}}><span style={{color:C.accent,fontWeight:700}}>Why relevant: </span>{paper.relevanceReason}</div>
                )}
              </div>

              <div style={{background:C.purple+"08",border:`1px solid ${C.purple}33`,borderRadius:7,padding:10,marginBottom:10}}>
                <div style={{fontFamily:SERIF,fontSize:16,fontWeight:600,color:C.text,marginBottom:4}}>Proposed system change</div>
                <div style={{fontSize:12.5,color:C.text,lineHeight:1.65,marginBottom:7}}>{paper.proposedChange}</div>
                <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
                  <div>
                    <div style={{fontSize:11,color:C.muted,marginBottom:3}}>Impacted Agents</div>
                    <div style={{display:"flex",gap:4,flexWrap:"wrap"}}>
                      {paper.impactedAgents.map(a=><Tag key={a} color={C.blue} small>{a}</Tag>)}
                    </div>
                  </div>
                  <div>
                    <div style={{fontSize:11,color:C.muted,marginBottom:3}}>Cost Impact</div>
                    <Tag color={paper.costImpact.includes("Free")||paper.costImpact.includes("Saves")?C.green:C.accent}>{paper.costImpact}</Tag>
                  </div>
                  {paper.fileToChange&&(
                    <div>
                      <div style={{fontSize:11,color:C.muted,marginBottom:3}}>File to Change</div>
                      <span style={{fontSize:12,color:C.text,fontFamily:MONO}}>{paper.fileToChange}</span>
                    </div>
                  )}
                  {paper.effortHours!=null&&(
                    <div>
                      <div style={{fontSize:11,color:C.muted,marginBottom:3}}>Effort</div>
                      <span style={{fontSize:12,color:C.text}}>{`~${paper.effortHours}h`}</span>
                    </div>
                  )}
                  {paper.requiresPaidData&&(
                    <div style={{alignSelf:"flex-end"}}><Tag color={C.red} small>Requires paid data</Tag></div>
                  )}
                </div>
                {paper.expectedImprovement&&(
                  <div style={{fontSize:12,color:C.textDim,marginTop:7}}><span style={{color:C.green,fontWeight:700}}>Expected improvement: </span>{paper.expectedImprovement}</div>
                )}
              </div>

              {/* Agent votes */}
              <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:7,padding:10,marginBottom:10}}>
                <div style={{fontSize:12,fontWeight:700,color:C.accent,marginBottom:6}}>Agent Vote Tally</div>
                <div style={{display:"flex",gap:12,marginBottom:8}}>
                  {[["For",paper.votes.for,C.green],["Against",paper.votes.against,C.red],["Abstain",paper.votes.abstain,C.muted]].map(([l,v,col])=>(
                    <div key={l} style={{textAlign:"center"}}>
                      <div style={{fontSize:20,fontWeight:800,color:col,fontFamily:MONO}}>{v}</div>
                      <div style={{fontSize:11,color:C.muted}}>{l}</div>
                    </div>
                  ))}
                </div>
                <Bar pct={(paper.votes.for/(paper.votes.for+paper.votes.against+paper.votes.abstain))*100} color={C.green} h={4}/>
              </div>

              {/* Actions */}
              <div style={{display:"flex",gap:7}}>
                {paper.debateStatus==="pending"&&(
                  <button onClick={()=>onOpenARIA("research_paper",paper)} style={{background:`${C.purple}`,border:"none",borderRadius:6,padding:"8px 14px",color:C.onFill,fontSize:12.5,fontWeight:700,cursor:"pointer",flex:1}}>
                    ✦ Discuss with ARIA
                  </button>
                )}
                {paper.debateStatus==="debating"&&(
                  <button onClick={()=>onOpenARIA("research_debate",paper)} style={{background:`linear-gradient(135deg,${C.accent},${C.accentDim})`,border:"none",borderRadius:6,padding:"8px 14px",color:C.bg,fontSize:12.5,fontWeight:700,cursor:"pointer",flex:1}}>
                    Cast your vote
                  </button>
                )}
                {paper.debateStatus==="approved"&&(
                  <button onClick={()=>onOpenARIA("research_approved",paper)} style={{background:`${C.green}`,border:"none",borderRadius:6,padding:"8px 14px",color:C.onFill,fontSize:12.5,fontWeight:700,cursor:"pointer",flex:1}}>
                    View implementation steps
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* AGENT DEBATE LOG */}
      {sec==="debate"&&(
        <div>
          <div style={{fontSize:12.5,color:C.textDim,lineHeight:1.7,marginBottom:12}}>
            When a research proposal receives split votes, agents formally debate before it reaches you. Each agent provides a structured argument based on its domain expertise. You are the final decision-maker.
          </div>
          {_debate.length>0 ? (
            <div style={{display:"flex",flexDirection:"column",gap:7}}>
              {_debate.map((entry,i)=>(
                <div key={i} style={{background:C.bg,border:`1px solid ${debateColors[entry.stance]||C.muted}33`,borderRadius:7,padding:10,borderLeft:`3px solid ${debateColors[entry.stance]||C.muted}`}}>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:4}}>
                    <span style={{fontSize:12.5,fontWeight:700,color:C.text}}>{entry.agent||entry.agent_name}</span>
                    <Tag color={debateColors[entry.stance]||C.muted} small>{(entry.stance||"").toUpperCase()}</Tag>
                  </div>
                  <div style={{fontSize:12,color:C.textDim,lineHeight:1.6}}>{entry.argument}</div>
                </div>
              ))}
              {_feed.some(r=>r.debateStatus==="debating")&&(
                <button onClick={()=>onOpenARIA("research_debate",_feed.find(r=>r.debateStatus==="debating"))} style={{background:`linear-gradient(135deg,${C.accent},${C.accentDim})`,border:"none",borderRadius:6,padding:"8px 14px",color:C.bg,fontSize:12.5,fontWeight:700,cursor:"pointer",width:"100%",marginTop:6}}>
                  You're the tiebreaker: discuss with ARIA to decide
                </button>
              )}
            </div>
          ) : (
            <EmptyState icon="⚖️" title="No active debates"
              sub="Agent debates are triggered when a research proposal receives split votes (tie between FOR and AGAINST). They appear here in real-time once the governance agent has been running for several days." />
          )}
        </div>
      )}

      {/* AGENT HEALTH */}
      {sec==="health"&&(
        <div>
          {/* Live attribution if available */}
          {liveAttribution?.agents?.length>0
            ? <AgentAttributionPanel attribution={null} liveAttribution={liveAttribution}/>
            : (
              <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:12}}>
                <div style={{fontSize:12,fontWeight:700,color:C.muted,marginBottom:6}}>Live Attribution</div>
                <div style={{fontSize:11,color:C.textDim}}>
                  Live alpha vs NIFTY 50 per agent will appear here after today's 16:30 IST Forward Poller run.
                  Resolved 90-day accuracy arrives after recommendations mature (≈6 months).
                </div>
              </div>
            )
          }
          {/* Agent registry — always visible */}
          <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12}}>
            <div style={{fontSize:12,fontWeight:700,color:C.accent,marginBottom:8}}>Active Agents (10)</div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6}}>
              {[
                ["technical","TA indicators, RSI, EMA, momentum"],
                ["fundamental","Valuation, PE, ROCE, DCF, screener"],
                ["sentiment","News NLP, BSE filings, FinBERT ensemble"],
                ["institutional","FII/DII flows, promoter holdings"],
                ["macro","RBI, inflation, currency, global macro"],
                ["historical_rag","Semantic similarity on past market events"],
                ["sector_valuation","Live sector PE vs 5-yr structural median"],
                ["commodities","Gold, crude, silver MCX signals"],
                ["warren_bot","Buffett+Jhunjhunwala quality score (0–100)"],
                ["discovery_screener","Full NSE EQ universe daily screening"],
              ].map(([name,desc])=>(
                <div key={name} style={{background:C.bg,borderRadius:5,padding:"6px 9px",border:`1px solid ${C.border}`}}>
                  <div style={{fontSize:11,fontWeight:700,color:C.text,textTransform:"capitalize",marginBottom:2}}>
                    {name.replace(/_/g," ")}
                    <span style={{marginLeft:6,fontSize:10,background:C.green+"22",color:C.green,borderRadius:3,padding:"1px 4px"}}>ACTIVE</span>
                  </div>
                  <div style={{fontSize:10.5,color:C.muted}}>{desc}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── PORTFOLIO TAB ────────────────────────────────────────────────────────────
// ─── BROKEN SYMBOLS BANNER ────────────────────────────────────────────────────
// ── Options Market Signal Panel ───────────────────────────────────────────────
function OptionsSignalPanel({signal}){
  if(!signal) return null;
  const sig  = signal.signal||"—";
  const score= signal.score;
  const pcr  = signal.pcr;
  const vix  = signal.india_vix;
  const mp   = signal.max_pain;
  const src  = signal.source;
  const ivhv = signal.iv_hv_ratio;
  const sigColor = sig.includes("BULLISH")?C.green:sig.includes("BEARISH")?C.red:C.accent;
  const pcrColor = pcr==null?C.muted:pcr<0.9?C.green:pcr>1.3?C.red:C.accent;
  const vixColor = vix==null?C.muted:vix<16?C.green:vix>24?C.red:C.accent;
  return(
    <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:8,padding:16,marginBottom:16}}>
      <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:12}}>
        
        <span style={{fontWeight:700,color:C.text}}>NIFTY Options Sentiment</span>
        {src==='fallback'&&<span style={{fontSize:12.5,color:C.muted,background:C.surface,padding:'2px 6px',borderRadius:4}}>VIX-estimated</span>}
      </div>
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(110px,1fr))',gap:8,marginBottom:12}}>
        <div style={{background:C.surface,borderRadius:6,padding:'10px 8px',textAlign:'center'}}>
          <div style={{fontSize:12.5,color:C.muted,marginBottom:4}}>Signal</div>
          <div style={{fontWeight:700,fontSize:14,color:sigColor}}>{sig.replace('_',' ')}</div>
        </div>
        <div style={{background:C.surface,borderRadius:6,padding:'10px 8px',textAlign:'center'}}>
          <div style={{fontSize:12.5,color:C.muted,marginBottom:4}}>Score</div>
          <div style={{fontWeight:700,fontSize:14,color:score>=60?C.green:score<=35?C.red:C.accent}}>{score}/100</div>
        </div>
        <div style={{background:C.surface,borderRadius:6,padding:'10px 8px',textAlign:'center'}}>
          <div style={{fontSize:12.5,color:C.muted,marginBottom:4}}>PCR</div>
          <div style={{fontWeight:700,fontSize:14,color:pcrColor}}>{pcr!=null?pcr.toFixed(2):'—'}</div>
        </div>
        <div style={{background:C.surface,borderRadius:6,padding:'10px 8px',textAlign:'center'}}>
          <div style={{fontSize:12.5,color:C.muted,marginBottom:4}}>India VIX</div>
          <div style={{fontWeight:700,fontSize:14,color:vixColor}}>{vix!=null?vix.toFixed(1):'—'}</div>
        </div>
        <div style={{background:C.surface,borderRadius:6,padding:'10px 8px',textAlign:'center'}}>
          <div style={{fontSize:12.5,color:C.muted,marginBottom:4}}>Max Pain</div>
          <div style={{fontWeight:700,fontSize:14,color:C.text}}>{mp!=null?`₹${mp.toLocaleString('en-IN',{maximumFractionDigits:0})}`:'—'}</div>
        </div>
        <div style={{background:C.surface,borderRadius:6,padding:'10px 8px',textAlign:'center'}}>
          <div style={{fontSize:12.5,color:C.muted,marginBottom:4}}>IV/HV</div>
          <div style={{fontWeight:700,fontSize:14,color:ivhv!=null?(ivhv>1.3?C.red:ivhv<0.85?C.green:C.accent):C.muted}}>{ivhv!=null?ivhv.toFixed(2):'—'}</div>
        </div>
      </div>
      {signal.commentary&&<div style={{fontSize:13,color:C.muted,lineHeight:1.5}}>{signal.commentary}</div>}
    </div>
  );
}

function PortfolioRiskPanel({risk}){
  if(!risk) return null;
  const vol=risk.portfolio_vol, var95=risk.var_95, var99=risk.var_99, cvar95=risk.cvar_95;
  const sharpe=risk.sharpe, mdd=risk.max_drawdown_pct, hhi=risk.hhi;
  const conc=risk.concentration_risk;
  const concColor=conc==="HIGH"?C.red:conc==="MODERATE"?C.accent:C.green;
  const varColor=var95>2?C.red:var95>1?C.accent:C.green;
  const sharpeColor=sharpe!=null?(sharpe>=1?C.green:sharpe>=0?C.accent:C.red):C.muted;
  const pairs=(risk.top_correlated_pairs||[]).filter(p=>Math.abs(p.correlation)>0.7);
  return(
    <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:14}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
        <span style={{fontFamily:SERIF,fontSize:18,fontWeight:600,color:C.text}}>Portfolio risk</span>
        <span style={{fontSize:11,color:C.muted}}>{risk.snapshot_date} · {risk.holdings_count} holdings</span>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(96px,1fr))",gap:7,marginBottom:10}}>
        {[
          ["Ann. Vol",vol!=null?`${vol.toFixed(1)}%`:"—",vol>25?C.red:vol>15?C.accent:C.green],
          ["VaR 95%",var95!=null?`${var95.toFixed(2)}%`:"—",varColor],
          ["VaR 99%",var99!=null?`${var99.toFixed(2)}%`:"—",var99>3?C.red:C.accent],
          ["CVaR 95%",cvar95!=null?`${cvar95.toFixed(2)}%`:"—",cvar95>3?C.red:C.accent],
          ["Sharpe",sharpe!=null?sharpe.toFixed(2):"—",sharpeColor],
          ["Max DD",mdd!=null?`${mdd.toFixed(1)}%`:"—",mdd>20?C.red:mdd>10?C.accent:C.green],
        ].map(([l,v,col])=>(
          <div key={l} style={{background:C.bg,borderRadius:5,padding:"6px 8px",textAlign:"center"}}>
            <div style={{fontSize:10.5,color:C.muted,marginBottom:2}}>{l}</div>
            <div style={{fontSize:13,fontWeight:800,color:col,fontFamily:MONO}}>{v}</div>
          </div>
        ))}
      </div>
      <div style={{display:"flex",gap:8,flexWrap:"wrap",alignItems:"center"}}>
        <span style={{fontSize:11,color:C.muted}}>Concentration:</span>
        <Tag color={concColor} small>{conc} (HHI {hhi!=null?hhi.toFixed(3):"—"})</Tag>
        {Object.entries(risk.sector_weights||{}).slice(0,4).map(([s,w])=>(
          <span key={s} style={{fontSize:11,color:C.textDim}}>{s} {(w*100).toFixed(0)}%</span>
        ))}
      </div>
      {pairs.length>0&&(
        <div style={{marginTop:8,fontSize:11,color:C.muted}}>
          <span style={{fontWeight:700,color:C.accent}}>Correlated pairs: </span>
          {pairs.map(p=>(
            <span key={p.a+p.b} style={{marginRight:10,color:Math.abs(p.correlation)>0.85?C.red:C.accent}}>
              {p.a.replace(".NS","")} ↔ {p.b.replace(".NS","")} ({p.correlation>0?"+":""}{p.correlation.toFixed(2)})
            </span>
          ))}
        </div>
      )}
      {(risk.warnings||[]).map((w,i)=>(
        <div key={i} style={{marginTop:5,fontSize:11,color:C.accent,background:C.accent+"0d",borderRadius:4,padding:"3px 8px"}}>⚠ {w}</div>
      ))}
    </div>
  );
}

// ─── PORTFOLIO ────────────────────────────────────────────────────────────────
const EXIT_TONE={EXIT:"sell",BOOK_PARTIAL:"warn",REVIEW:"hold",TRAIL:"buy",HOLD:"mute"};
const EXIT_LABEL={EXIT:"Exit",BOOK_PARTIAL:"Book partial",REVIEW:"Review",TRAIL:"Trail",HOLD:"Hold"};
const RATCHET_LABEL={
  TRAIL:["Trailing stop","Follows 15% below the price and only moves up"],
  LOCK_20:["Stop locks +20%","Stop-loss raised to lock in a 20% gain"],
  BREAKEVEN:["Stop at breakeven","Stop-loss raised to your buy price, so this position can't lose money"],
};
const SECTOR_SWATCH=[C.accent,C.blue,C.green,C.purple,C.orange,C.teal,C.cyan,C.lime,C.textDim];
const holdingNumbers=h=>{
  const price=h.currentPrice||0, avg=h.avgBuy||0, qty=h.qty||0;
  const value=price*qty, cost=avg*qty, pnl=value-cost;
  const pnlPct=cost>0?pnl/cost*100:0;
  const toStop=h.stoplossPrice&&price?(price-h.stoplossPrice)/price*100:null;
  const toTarget=h.targetPrice&&price?(h.targetPrice-price)/price*100:null;
  return {price,value,cost,pnl,pnlPct,toStop,toTarget};
};
const needsAction=(h,n)=>isCriticalDanger(h)||["EXIT","BOOK_PARTIAL","REVIEW"].includes(h.exitSignal)||(n.toStop!=null&&n.toStop<8);

function BrokenSymbolsBanner({broken, onFixed}){
  const [fixing,setFixing]=useState({});   // symbol → loading|done|error|empty
  const [edits,setEdits]=useState({});     // symbol → typed ticker
  if(!IS_LIVE||!broken||broken.length===0) return null;
  const applyFix=async item=>{
    const yf=(edits[item.symbol]??item.suggested_yf??"").trim();
    if(!yf){ setFixing(f=>({...f,[item.symbol]:"empty"})); return; }
    setFixing(f=>({...f,[item.symbol]:"loading"}));
    try{
      await apiFetch("/api/symbol/override",{method:"POST",body:JSON.stringify({symbol:item.symbol,yf_symbol:yf})});
      setFixing(f=>({...f,[item.symbol]:"done"}));
      if(onFixed) onFixed();
    }catch(e){ setFixing(f=>({...f,[item.symbol]:"error"})); }
  };
  return(
    <div style={{border:`1px solid ${C.orange}`,background:C.orange+"10",borderRadius:8,padding:"14px 16px"}}>
      <div style={{display:"flex",gap:10,alignItems:"flex-start"}}>
        <Icon name="info" style={{color:C.orange,marginTop:2}}/>
        <div style={{flex:1,minWidth:0}}>
          <div style={{fontWeight:600}}>{broken.length} holding{broken.length>1?"s aren't":" isn't"} getting live prices</div>
          <div style={{fontSize:13,color:C.textDim,marginTop:2,lineHeight:1.5}}>The symbol doesn't match a Yahoo Finance ticker, so its value uses your buy price. Enter the right ticker (NSE code + .NS, BSE + .BO). Fixes are saved for good.</div>
          <div style={{display:"flex",flexDirection:"column",gap:8,marginTop:12}}>
            {broken.map(item=>{
              const st=fixing[item.symbol];
              const val=edits[item.symbol]??(item.suggested_yf||"");
              const id=`fix-${item.symbol}`;
              return(
                <div key={item.symbol} style={{display:"flex",alignItems:"center",gap:10,flexWrap:"wrap"}}>
                  <label htmlFor={id} style={{minWidth:130,fontSize:13}}>
                    <span style={{fontFamily:SERIF,fontWeight:600,fontSize:16}}>{item.symbol}</span>
                    {item.name&&<span style={{color:C.textDim}}> · {item.name}</span>}
                  </label>
                  {st==="done"?<span style={{color:C.green,fontWeight:600,fontSize:13}}>Fixed. Prices will refresh.</span>:(
                    <>
                      <input id={id} value={val} onChange={e=>{setEdits(ed=>({...ed,[item.symbol]:e.target.value}));if(st==="empty"||st==="error")setFixing(f=>({...f,[item.symbol]:null}));}}
                        placeholder="e.g. BHARATSE.NS" aria-invalid={st==="empty"||st==="error"}
                        style={{fontFamily:MONO,fontSize:13,minHeight:38,padding:"0 10px",width:170,borderRadius:6,background:C.surface,color:C.text,
                          border:`1px solid ${st==="empty"||st==="error"?C.red:C.borderHi}`}}/>
                      <button onClick={()=>applyFix(item)} disabled={st==="loading"} style={{...edBtn(),minHeight:38,cursor:st==="loading"?"wait":"pointer"}}>
                        {st==="loading"?"Checking…":"Fix price"}
                      </button>
                      {item.suggested_yf&&val===item.suggested_yf&&<span style={{fontSize:12,color:C.textDim}}>Suggested</span>}
                      {st==="empty"&&<span role="alert" style={{fontSize:12,color:C.red}}>Enter a ticker first.</span>}
                      {st==="error"&&<span role="alert" style={{fontSize:12,color:C.red}}>That ticker wasn't found. Check it on finance.yahoo.com.</span>}
                    </>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function DangerCard({h,onAsk}){
  const [open,setOpen]=useState(false);
  return(
    <div role="alert" style={{border:`1px solid ${C.red}`,background:C.red+"0c",borderRadius:8,padding:"14px 16px"}}>
      <div style={{display:"flex",gap:10,alignItems:"flex-start",flexWrap:"wrap"}}>
        <Icon name="info" style={{color:C.red,marginTop:2}}/>
        <div style={{flex:"1 1 260px",minWidth:0}}>
          <div style={{fontWeight:600}}>{h.symbol}: agents predict a fall of about {h.dangerDropPct}%{h.dangerWindow?` within ${h.dangerWindow}`:""}</div>
          <div style={{fontSize:13,color:C.textDim,marginTop:2,lineHeight:1.5}}>
            {h.dangerConfidence}% confidence · {inr(h.currentPrice*h.qty,0)} at risk · predicted floor about {inr(h.currentPrice*(1-h.dangerDropPct/100),0)}
          </div>
          {open&&(
            <div style={{marginTop:10,fontSize:13,lineHeight:1.55}}>
              {h.dangerTrigger&&<p style={{margin:0}}>{h.dangerTrigger}</p>}
              {(h.dangerSources||[]).length>0&&<p style={{margin:"8px 0 0",color:C.textDim}}>Sources: {h.dangerSources.join(" · ")}</p>}
            </div>
          )}
          <div style={{display:"flex",gap:8,flexWrap:"wrap",marginTop:10}}>
            <button onClick={onAsk} style={{...edBtn(),minHeight:36}}>Review with ARIA</button>
            {(h.dangerTrigger||(h.dangerSources||[]).length>0)&&
              <button onClick={()=>setOpen(o=>!o)} aria-expanded={open} style={{...edBtn("ghost"),minHeight:36}}>{open?"Hide reasons":"Why?"}</button>}
          </div>
        </div>
      </div>
    </div>
  );
}

function AddHoldingForm({portfolio,onSubmit,onCancel}){
  const [sym,setSym]=useState(""); const [qty,setQty]=useState(""); const [avg,setAvg]=useState("");
  const [tried,setTried]=useState(false);
  const symU=sym.trim().toUpperCase();
  const errs={};
  if(!symU) errs.sym="Enter a symbol.";
  else if(!/^[A-Z0-9&.-]{2,24}$/.test(symU)) errs.sym="Use the NSE symbol: letters and numbers only.";
  const q=Number(qty), a=Number(avg);
  if(!qty.trim()) errs.qty="Enter a quantity.";
  else if(!Number.isInteger(q)||q<=0) errs.qty="Must be a whole number above 0.";
  if(!avg.trim()) errs.avg="Enter your average price.";
  else if(!(a>0)) errs.avg="Must be more than ₹0.";
  const existing=symU&&(portfolio||[]).find(h=>h.status!=="exited"&&h.symbol.toUpperCase()===symU);
  const submit=()=>{
    setTried(true);
    if(Object.keys(errs).length) return;
    onSubmit({symbol:symU,qty:q,avgBuy:a});
  };
  const field=(label,key,val,set,props={})=>(
    <label style={{display:"flex",flexDirection:"column",gap:6,fontSize:12.5,color:C.textDim,fontWeight:500,minWidth:0,...(props.wrap||{})}}>
      {label}
      <input value={val} onChange={e=>set(e.target.value)} onKeyDown={e=>{if(e.key==="Enter")submit();}}
        aria-invalid={tried&&!!errs[key]} placeholder={props.placeholder} inputMode={props.inputMode}
        style={{fontFamily:key==="sym"?SANS:MONO,fontSize:14,minHeight:42,padding:"0 12px",borderRadius:6,background:C.surface,color:C.text,width:"100%",
          border:`1px solid ${tried&&errs[key]?C.red:C.borderHi}`}}/>
      {tried&&errs[key]&&<span role="alert" style={{color:C.red,fontSize:12}}>{errs[key]}</span>}
      {key==="sym"&&existing&&!errs.sym&&<span style={{color:C.blue,fontSize:12}}>You hold {existing.qty} {symU}. This will be averaged into that position.</span>}
    </label>
  );
  return(
    <section style={{...edBox,padding:"18px 20px"}} aria-label="Add a holding">
      <h3 style={{...edH3,fontSize:18}}>Add a holding <span style={{fontFamily:SANS,fontSize:12.5,color:C.textDim,fontWeight:400}}>The live price is fetched after you save.</span></h3>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:12,alignItems:"start"}}>
        {field("Symbol","sym",sym,setSym,{placeholder:"e.g. RELIANCE or IHCL",wrap:{gridColumn:"span 2"}})}
        {field("Quantity","qty",qty,setQty,{placeholder:"0",inputMode:"numeric"})}
        {field("Average buy price (₹)","avg",avg,setAvg,{placeholder:"0.00",inputMode:"decimal"})}
      </div>
      <div style={{display:"flex",gap:8,flexWrap:"wrap",marginTop:14,alignItems:"center"}}>
        <button onClick={submit} style={edBtn("accent")}>Save holding</button>
        <button onClick={onCancel} style={edBtn("ghost")}>Cancel</button>
        <span style={{fontSize:12.5,color:C.textDim}}>Or tell ARIA “I bought 15 Reliance at 2850”.</span>
      </div>
    </section>
  );
}

function HoldingDetail({h,n,rec,onAsk,onViewRec,narrow}){
  const has=h.targetPrice>0&&h.stoplossPrice>0&&n.price>0;
  let nowPos=50;
  if(has) nowPos=Math.max(2,Math.min(98,4+(n.price-h.stoplossPrice)/(h.targetPrice-h.stoplossPrice)*92));
  const ratchet=RATCHET_LABEL[h.stoplossRatchetLevel];
  const facts=[
    ["Sector",h.sector||"—"],
    ["Invested",inr(n.cost,0)],["Value now",inr(n.value,0)],
    ["Plan",h.exitReason||(EXIT_META[h.exitSignal]?.hint)||"—"],
    ...(ratchet?[["Stop-loss",ratchet[1]]]:[]),
    ...(h.targetUpdateCount>0?[["Target",`Raised ${h.targetUpdateCount}× by the target updater${h.originalTarget?` (was ${inr(h.originalTarget)})`:""}`]]:[]),
    ...(h.protectGainsFlag?[["Protect gains","Valuation looks stretched. Target locked; consider protecting profits."]]:[]),
    ...(h.earningsAlert?.days_until!=null?[["Earnings",`In ${h.earningsAlert.days_until} day${h.earningsAlert.days_until===1?"":"s"}${h.earningsAlert.quarter?` (${h.earningsAlert.quarter})`:""}`]]:[]),
    ...(h.fairValue?[["Fair value",inr(h.fairValue)]]:[]),
    ...(h.notes?[["Notes",h.notes]]:[]),
  ];
  return(
    <div style={{display:"grid",gridTemplateColumns:narrow?"minmax(0,1fr)":"minmax(0,1.2fr) minmax(0,1fr)",gap:24,padding:narrow?"14px 12px 18px":"18px 20px 20px",background:C.bg,borderTop:`1px solid ${C.border}`}}>
      <div>
        <h3 style={{...edH3,fontSize:16}}>Stop-loss to target</h3>
        {has?(
          <>
            <div aria-hidden="true" style={{position:"relative",height:10,borderRadius:5,background:C.panel,margin:"14px 6px 18px"}}>
              <span style={{position:"absolute",top:-7,left:"4%",width:4,height:24,borderRadius:2,background:C.red,transform:"translateX(-50%)"}}/>
              <span style={{position:"absolute",top:-7,left:`${nowPos}%`,width:4,height:24,borderRadius:2,background:C.text,transform:"translateX(-50%)"}}/>
              <span style={{position:"absolute",top:-7,left:"96%",width:4,height:24,borderRadius:2,background:C.green,transform:"translateX(-50%)"}}/>
            </div>
            <div style={{display:"grid",gridTemplateColumns:"repeat(3,minmax(0,1fr))",gap:10,fontSize:12,color:C.textDim}}>
              <div><div>Stop-loss</div><div style={{fontFamily:MONO,fontSize:14,fontWeight:600,color:C.text}}>{inr(h.stoplossPrice)}</div>{n.toStop>=0?<div>{n.toStop.toFixed(1)}% below the price</div>:<div style={{color:C.red,fontWeight:600}}>Price is {Math.abs(n.toStop).toFixed(1)}% under it</div>}</div>
              <div><div>Now</div><div style={{fontFamily:MONO,fontSize:14,fontWeight:600,color:C.text}}>{inr(n.price)}</div></div>
              <div><div>Target</div><div style={{fontFamily:MONO,fontSize:14,fontWeight:600,color:C.text}}>{inr(h.targetPrice)}</div><div>{n.toTarget>0?`${signedPct(n.toTarget)} away`:"Past target"}</div></div>
            </div>
          </>
        ):<p style={{margin:0,fontSize:13,color:C.textDim}}>No target or stop-loss is set for this holding yet.</p>}
        {isCriticalDanger(h)&&h.dangerTrigger&&<p style={{margin:"14px 0 0",fontSize:13,lineHeight:1.5,color:C.red}}>{h.dangerTrigger}</p>}
      </div>
      <div>
        {facts.map(([k,v])=>(
          <div key={k} style={{display:"flex",justifyContent:"space-between",gap:16,padding:"8px 0",borderTop:`1px solid ${C.border}`,fontSize:13}}>
            <span style={{color:C.textDim,flexShrink:0}}>{k}</span><span style={{textAlign:"right"}}>{v}</span>
          </div>
        ))}
        <div style={{display:"flex",gap:8,flexWrap:"wrap",marginTop:12}}>
          <button onClick={onAsk} style={{...edBtn(),minHeight:36}}><Icon name="spark"/>Ask ARIA</button>
          {rec&&<button onClick={onViewRec} style={{...edBtn("ghost"),minHeight:36,color:C.accent}}>Latest call: {rec.action} →</button>}
        </div>
      </div>
    </div>
  );
}

const PF_COLS=[
  ["sym","Holding",""],["qty","Qty","r opt"],["avg","Avg buy","r opt"],["ltp","Price","r"],
  ["value","Value","r opt"],["pnl","P&L","r opt"],["pnlPct","P&L %","r"],["plan","Plan","r opt"],
];
function PortfolioTab({portfolio,onOpenARIA,brokenSymbols,onFixBroken,portfolioRisk,optionsSignal,paperPortfolio,paperHistory,apiLoaded,onAddHolding,recs,onViewRec}){
  const narrow=useNarrow(760);
  const [showAdd,setShowAdd]=useState(false);
  const [filter,setFilter]=useState("all");
  const [query,setQuery]=useState("");
  const [sortK,setSortK]=useState("plan");
  const [sortDir,setSortDir]=useState(1);
  const [expanded,setExpanded]=useState(null);
  const [showAllAlerts,setShowAllAlerts]=useState(false);
  const [flash,setFlash]=useState(null);

  const holdings=(portfolio||[]).filter(h=>h.status!=="exited");
  const rows=holdings.map(h=>({h,n:holdingNumbers(h)}));
  const recByTicker=new Map();
  (recs||[]).forEach(r=>{ if(!r.isDiscovery) recByTicker.set(cleanSym(r.symbol).toUpperCase(),r); });
  const recFor=h=>{
    const s=cleanSym(h.symbol).toUpperCase();
    return recByTicker.get(s)||recByTicker.get(HOLDING_TICKER_ALIASES[s])||(h.yfSymbol&&recByTicker.get(cleanSym(h.yfSymbol).toUpperCase()))||null;
  };

  const invested=rows.reduce((a,r)=>a+r.n.cost,0), current=rows.reduce((a,r)=>a+r.n.value,0);
  const pnl=current-invested, pnlPct=invested>0?pnl/invested*100:0;
  const exitCounts=holdings.reduce((m,h)=>{if(h.exitSignal&&h.exitSignal!=="HOLD")m[h.exitSignal]=(m[h.exitSignal]||0)+1;return m;},{});
  const danger=holdings.filter(isCriticalDanger);
  const alerts=computePortfolioAlerts(holdings).filter(a=>a.severity!=="critical_danger");
  const shownAlerts=showAllAlerts?alerts:alerts.slice(0,4);

  const filters=[
    ["all","All",()=>true],
    ["action","Needs action",r=>needsAction(r.h,r.n)],
    ["gain","Gainers",r=>r.n.pnl>0],
    ["loss","Losers",r=>r.n.pnl<0],
  ];
  const fFn=(filters.find(f=>f[0]===filter)||filters[0])[2];
  const q=query.trim().toLowerCase();
  const urgency=r=>isCriticalDanger(r.h)?-1:(EXIT_META[r.h.exitSignal]?.rank??9);
  const key={sym:r=>r.h.symbol,qty:r=>r.h.qty,avg:r=>r.h.avgBuy,ltp:r=>r.n.price,value:r=>r.n.value,pnl:r=>r.n.pnl,pnlPct:r=>r.n.pnlPct,plan:urgency}[sortK];
  const view=rows.filter(fFn)
    .filter(r=>!q||[r.h.symbol,r.h.name,r.h.sector].some(v=>v&&String(v).toLowerCase().includes(q)))
    .sort((a,b)=>{const x=key(a),y=key(b);return (x<y?-1:x>y?1:0)*sortDir||b.n.value-a.n.value;});
  const sortBy=k=>{ if(k===sortK) setSortDir(d=>-d); else {setSortK(k); setSortDir(k==="sym"||k==="plan"?1:-1);} };

  const secMap={};
  rows.forEach(r=>{const s=r.h.sector||"Unclassified";secMap[s]=(secMap[s]||0)+r.n.value;});
  const secs=Object.entries(secMap).sort((a,b)=>b[1]-a[1]);
  const top=secs[0];
  const topPct=top&&current>0?top[1]/current*100:0;

  const cols=narrow?"minmax(0,1fr) auto auto 20px":"minmax(170px,1.6fr) 64px 96px 96px 112px 112px 80px 112px 20px";
  const loading=IS_LIVE&&!apiLoaded&&holdings.length===0;

  return(
    <div style={{animation:"fadeUp .3s ease",maxWidth:1440,margin:"0 auto",display:"flex",flexDirection:"column",gap:20}}>
      <header style={{display:"flex",justifyContent:"space-between",alignItems:"flex-end",gap:12,flexWrap:"wrap"}}>
        <div>
          <h1 style={{fontFamily:SERIF,fontSize:narrow?26:32,fontWeight:600,margin:0,letterSpacing:-.3,lineHeight:1.15}}>My portfolio</h1>
          <p style={{margin:"4px 0 0",fontSize:14,color:C.textDim}}>{holdings.length?`${holdings.length} holdings · prices refresh on every visit · nothing is sold automatically`:"Track live prices, P&L, stop-losses and agent signals"}</p>
        </div>
        <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
          <button onClick={()=>setShowAdd(s=>!s)} aria-expanded={showAdd} style={edBtn("accent")}><Icon name="plus"/>Add holding</button>
          <button onClick={()=>onOpenARIA("portfolio")} style={edBtn()}><Icon name="spark"/>Tell ARIA</button>
        </div>
      </header>

      {flash&&(
        <div role="status" style={{display:"flex",gap:10,alignItems:"center",padding:"10px 14px",borderRadius:6,background:C.green+"12",fontSize:13.5}}>
          <span style={{flex:1}}>{flash}</span>
          <button onClick={()=>setFlash(null)} aria-label="Dismiss" style={{border:0,background:"transparent",cursor:"pointer",color:C.textDim,width:32,height:32,display:"inline-grid",placeItems:"center"}}><Icon name="close" size={14}/></button>
        </div>
      )}

      {showAdd&&<AddHoldingForm portfolio={holdings} onCancel={()=>setShowAdd(false)} onSubmit={v=>{
        const how=onAddHolding(v);
        setShowAdd(false);
        setFlash(how==="averaged"?`Added ${v.qty} more ${v.symbol} to your position.`:`${v.symbol} added. Fetching its live price…`);
      }}/>}

      {loading&&(
        <div aria-busy="true" style={{display:"flex",flexDirection:"column",gap:12}}>
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:12}}>{[0,1,2,3].map(i=><div key={i} style={{height:92,borderRadius:8,background:C.panel,animation:"pulse 1.4s ease-in-out infinite"}}/>)}</div>
          <div style={{height:320,borderRadius:8,background:C.panel,animation:"pulse 1.4s ease-in-out infinite"}}/>
        </div>
      )}

      {!loading&&holdings.length===0&&!showAdd&&(
        <div style={{display:"flex",flexDirection:"column",alignItems:"center",textAlign:"center",gap:10,padding:"48px 20px",border:`1px dashed ${C.borderHi}`,borderRadius:8,color:C.textDim}}>
          <div style={{fontFamily:SERIF,fontSize:20,fontWeight:600,color:C.text}}>No holdings yet</div>
          <p style={{margin:0,maxWidth:440,fontSize:13.5,lineHeight:1.55}}>Add the stocks you own and the agents will track prices, P&amp;L, stop-losses and fresh signals for each one.</p>
          <div style={{display:"flex",gap:8,flexWrap:"wrap",justifyContent:"center"}}>
            <button onClick={()=>setShowAdd(true)} style={edBtn("accent")}>Add your first holding</button>
            <button onClick={()=>onOpenARIA("portfolio")} style={edBtn()}>Tell ARIA instead</button>
          </div>
        </div>
      )}

      {holdings.length>0&&(
        <>
          {(danger.length>0||(brokenSymbols||[]).length>0||alerts.length>0)&&(
            <section aria-label="Needs attention" style={{display:"flex",flexDirection:"column",gap:10}}>
              <h2 style={{...edH3,fontSize:20,margin:0}}>Needs attention</h2>
              {danger.map(h=><DangerCard key={h.id} h={h} onAsk={()=>onOpenARIA("holding",h)}/>)}
              <BrokenSymbolsBanner broken={brokenSymbols} onFixed={onFixBroken}/>
              {alerts.length>0&&(
                <div style={{border:`1px solid ${C.border}`,borderRadius:8,background:C.surface}}>
                  {shownAlerts.map((a,i)=>{
                    const tone=a.severity==="critical"?C.red:a.severity==="warning"?C.orange:C.blue;
                    return(
                      <div key={a.id} style={{display:"flex",gap:12,alignItems:"center",padding:"11px 14px",borderTop:i?`1px solid ${C.border}`:"none",flexWrap:"wrap"}}>
                        <span aria-hidden="true" style={{width:8,height:8,borderRadius:"50%",background:tone,flexShrink:0}}/>
                        <div style={{flex:"1 1 240px",minWidth:0}}>
                          <div style={{fontWeight:600,fontSize:13.5}}>{a.title}</div>
                          <div style={{fontSize:12.5,color:C.textDim}}>{a.detail}</div>
                        </div>
                        <button onClick={()=>onOpenARIA("holding",holdings.find(h=>h.id===a.portfolioId))} style={{...edBtn("ghost"),minHeight:34,color:C.accent}}>Ask ARIA</button>
                      </div>
                    );
                  })}
                  {alerts.length>4&&(
                    <button onClick={()=>setShowAllAlerts(s=>!s)} style={{...edBtn("ghost"),width:"100%",borderTop:`1px solid ${C.border}`,borderRadius:0,color:C.accent}}>
                      {showAllAlerts?"Show fewer":`Show all ${alerts.length} alerts`}
                    </button>
                  )}
                </div>
              )}
            </section>
          )}

          <div style={{display:"grid",gridTemplateColumns:narrow?"repeat(2,minmax(0,1fr))":"repeat(4,minmax(0,1fr))",gap:12}}>
            {[
              ["Invested",inr(invested,0),`${holdings.length} holdings`,null],
              ["Current value",inr(current,0),(brokenSymbols||[]).length?`${brokenSymbols.length} price${brokenSymbols.length>1?"s":""} unavailable`:"Live prices",null],
              ["Total P&L",(pnl>=0?"+":"−")+inr(Math.abs(pnl),0),signedPct(pnlPct),dirColor(pnl)],
              ["Plan",Object.keys(exitCounts).length?Object.entries(exitCounts).sort((a,b)=>(EXIT_META[a[0]]?.rank??9)-(EXIT_META[b[0]]?.rank??9)).map(([k,c])=>`${c} ${EXIT_LABEL[k]||k}`).join(" · "):"All on plan","Rules-based markers · advisory only",null],
            ].map(([k,v,sub,color])=>(
              <div key={k} style={{...edBox,padding:"14px 16px",minWidth:0}}>
                <div style={{fontSize:11,color:C.textDim,textTransform:"uppercase",letterSpacing:".06em"}}>{k}</div>
                <div style={{fontFamily:k==="Plan"?SANS:SERIF,fontSize:k==="Plan"?16:narrow?21:28,fontWeight:600,marginTop:4,color:color||C.text,lineHeight:1.2}}>{v}</div>
                <div style={{fontSize:12.5,marginTop:4,color:color||C.textDim,fontFamily:k==="Total P&L"?MONO:SANS}}>{sub}</div>
              </div>
            ))}
          </div>

          <section aria-label="Holdings">
            <div style={{display:"flex",gap:10,flexWrap:"wrap",alignItems:"center",marginBottom:10}}>
              <div role="group" aria-label="Filter holdings" style={{display:"flex",gap:6,flexWrap:"wrap"}}>
                {filters.map(([id,label,fn])=>(
                  <button key={id} onClick={()=>{setFilter(id);setExpanded(null);}} aria-pressed={filter===id} style={edChip(filter===id)}>
                    {label}<span style={{fontFamily:MONO,fontSize:11,color:C.textDim}}>{rows.filter(fn).length}</span>
                  </button>
                ))}
              </div>
              <label style={{display:"flex",alignItems:"center",gap:8,flex:"1 1 200px",maxWidth:narrow?"none":300,minHeight:38,padding:"0 4px 0 12px",border:`1px solid ${C.border}`,borderRadius:6,background:C.surface,color:C.textDim}}>
                <Icon name="search" size={15}/>
                <span style={{position:"absolute",width:1,height:1,overflow:"hidden",clip:"rect(0 0 0 0)"}}>Search holdings</span>
                <input type="search" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search holdings"
                  style={{flex:1,minWidth:0,border:0,background:"transparent",color:C.text,fontSize:13.5,outline:"none",height:36}}/>
                {query&&<button onClick={()=>setQuery("")} aria-label="Clear search" style={{width:32,height:32,display:"inline-grid",placeItems:"center",border:0,background:"transparent",color:C.textDim,cursor:"pointer"}}><Icon name="close" size={14}/></button>}
              </label>
              {!narrow&&<span style={{fontSize:12.5,color:C.textDim,marginLeft:"auto"}}>Select a row for targets and reasons</span>}
            </div>

            <div style={{border:`1px solid ${C.borderHi}`,borderRadius:8,background:C.surface,overflowX:"auto"}}>
              <div style={{minWidth:narrow?0:980}}>
                <div style={{display:"grid",gridTemplateColumns:cols,gap:10,alignItems:"center",padding:"0 16px",minHeight:42,borderBottom:`1px solid ${C.text}`,position:"sticky",top:0,background:C.surface,zIndex:1}}>
                  {PF_COLS.filter(c=>!narrow||!c[2].includes("opt")).map(([k,label,cls])=>{
                    const on=sortK===k;
                    return(
                      <button key={k} onClick={()=>sortBy(k)} aria-label={`Sort by ${label}${on?(sortDir<0?", descending":", ascending"):""}`}
                        style={{border:0,background:"transparent",cursor:"pointer",padding:0,minHeight:40,display:"flex",gap:4,alignItems:"center",
                          justifyContent:cls.includes("r")?"flex-end":"flex-start",fontSize:11,fontWeight:600,letterSpacing:".05em",textTransform:"uppercase",
                          color:on?C.accent:C.textDim,fontFamily:SANS,whiteSpace:"nowrap"}}>
                        {label}{on&&<span aria-hidden="true">{sortDir<0?"↓":"↑"}</span>}
                      </button>
                    );
                  })}
                  <span/>
                </div>
                {view.length===0&&(
                  <div style={{padding:"32px 16px",textAlign:"center",color:C.textDim,display:"flex",flexDirection:"column",gap:10,alignItems:"center"}}>
                    <div style={{fontFamily:SERIF,fontSize:18,fontWeight:600,color:C.text}}>Nothing in this view</div>
                    <p style={{margin:0,fontSize:13}}>No holdings match{q?` “${query.trim()}”`:" this filter"}.</p>
                    <button onClick={()=>{setFilter("all");setQuery("");}} style={edBtn()}>Show all holdings</button>
                  </div>
                )}
                {view.map(({h,n})=>{
                  const open=expanded===h.id, dangerous=isCriticalDanger(h);
                  const exit=h.exitSignal&&h.exitSignal!=="HOLD"?h.exitSignal:null;
                  const pc=dirColor(n.pnl);
                  return(
                    <div key={h.id} style={{borderTop:`1px solid ${C.border}`}}>
                      <button onClick={()=>setExpanded(open?null:h.id)} aria-expanded={open} className="ed-card"
                        style={{display:"grid",gridTemplateColumns:cols,gap:10,alignItems:"center",width:"100%",textAlign:"left",border:0,cursor:"pointer",
                          padding:"0 16px",minHeight:narrow?64:58,background:open?C.bg:"transparent",color:C.text,fontFamily:SANS}}>
                        <span style={{display:"flex",flexDirection:"column",minWidth:0,padding:"8px 0"}}>
                          <span style={{display:"flex",alignItems:"center",gap:6,flexWrap:"wrap"}}>
                            <span style={{fontFamily:SERIF,fontSize:17,fontWeight:600,color:dangerous?C.red:C.text}}>{h.symbol}</span>
                            {dangerous&&<EdTag tone="sell">Danger</EdTag>}
                            {narrow&&exit&&<EdTag tone={EXIT_TONE[exit]}>{EXIT_LABEL[exit]}</EdTag>}
                            {h.earningsAlert?.warning_level&&<EdTag tone={h.earningsAlert.warning_level==="CRITICAL"?"warn":"mute"} title={`Results in ${h.earningsAlert.days_until} days`}>Results {h.earningsAlert.days_until}d</EdTag>}
                            {h._saving&&<EdTag tone="mute">Saving…</EdTag>}
                            {h._error&&<EdTag tone="sell">Not saved</EdTag>}
                          </span>
                          <span style={{fontSize:12,color:C.textDim,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                            {narrow?`${h.qty} sh · avg ${inr(h.avgBuy)}`:(h.name&&h.name!==h.symbol?h.name:h.sector)}
                          </span>
                        </span>
                        {!narrow&&<span style={{fontFamily:MONO,fontSize:13,textAlign:"right"}}>{h.qty?.toLocaleString("en-IN")}</span>}
                        {!narrow&&<span style={{fontFamily:MONO,fontSize:13,textAlign:"right"}}>{inr(h.avgBuy)}</span>}
                        <span style={{fontFamily:MONO,fontSize:13,textAlign:"right"}}>{inr(n.price)}</span>
                        {!narrow&&<span style={{fontFamily:MONO,fontSize:13,textAlign:"right"}}>{inr(n.value,0)}</span>}
                        {!narrow&&<span style={{fontFamily:MONO,fontSize:13,textAlign:"right",color:pc}}>{(n.pnl>=0?"+":"−")+inr(Math.abs(n.pnl),0)}</span>}
                        <span style={{fontFamily:MONO,fontSize:13,textAlign:"right",color:pc,fontWeight:600}}>{signedPct(n.pnlPct)}</span>
                        {!narrow&&<span style={{textAlign:"right"}}>{exit?<EdTag tone={EXIT_TONE[exit]} title={h.exitReason}>{EXIT_LABEL[exit]}</EdTag>:<span style={{fontSize:12,color:C.textDim}}>On plan</span>}</span>}
                        <Icon name="chevron" size={15} style={{color:C.textDim,transform:open?"rotate(90deg)":"none",transition:"transform .15s"}}/>
                      </button>
                      {open&&<HoldingDetail h={h} n={n} rec={recFor(h)} narrow={narrow}
                        onAsk={()=>onOpenARIA("holding",h)} onViewRec={()=>onViewRec(recFor(h))}/>}
                    </div>
                  );
                })}
              </div>
            </div>
          </section>

          {secs.length>0&&current>0&&(
            <section style={edBox} aria-label="Sector allocation">
              <h3 style={edH3}>Sector allocation <span style={{fontFamily:SANS,fontSize:12,color:C.textDim,fontWeight:400}}>by current value</span></h3>
              <div aria-hidden="true" style={{display:"flex",height:12,borderRadius:6,overflow:"hidden",gap:2}}>
                {secs.map(([s,v],i)=><span key={s} title={s} style={{width:`${v/current*100}%`,background:SECTOR_SWATCH[i%SECTOR_SWATCH.length]}}/>)}
              </div>
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(220px,1fr))",gap:"8px 20px",marginTop:14}}>
                {secs.map(([s,v],i)=>(
                  <div key={s} style={{display:"flex",alignItems:"center",gap:8,fontSize:13,minWidth:0}}>
                    <span style={{width:10,height:10,borderRadius:2,flexShrink:0,background:SECTOR_SWATCH[i%SECTOR_SWATCH.length]}}/>
                    <span style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{s}</span>
                    <span style={{marginLeft:"auto",fontFamily:MONO,color:C.textDim}}>{(v/current*100).toFixed(1)}%</span>
                  </div>
                ))}
              </div>
              {topPct>30&&<div style={{marginTop:14}}><DetailNote tone="warn">{top[0]} is {topPct.toFixed(0)}% of your portfolio, above the 30% concentration guideline.</DetailNote></div>}
            </section>
          )}

          <PortfolioRiskPanel risk={portfolioRisk}/>
          <OptionsSignalPanel signal={optionsSignal}/>
        </>
      )}

      <PaperPortfolioPanel data={paperPortfolio} history={paperHistory} apiLoaded={apiLoaded}/>
      <LongHorizonPanel C={C} apiFetch={apiFetch} isLive={IS_LIVE}/>
    </div>
  );
}

// ─── ARIA PANEL ───────────────────────────────────────────────────────────────
function ARIAPanel({selectedRec,ariaContext,onClearContext,portfolio,onPortfolioUpdate,discoveryStock,discoveryUniverse:_ariaDu,discoveryRuns:_ariaRuns,marketPulse:_mktPulse}){
  const _mktStr = (_mktPulse&&_mktPulse.length>0)
    ? _mktPulse.map(m=>`${m.key} ${m.value} (${m.change})`).join(", ")
    : "Market data unavailable — do not cite specific index or price values.";
  const [messages,setMessages]=useState([{role:"assistant",text:"Hello — I'm **ARIA**, your Adaptive Research Intelligence Assistant.\n\nI'm the bridge between you and every module of Bharat Intelligence:\n\n• **Explain** any recommendation or discovery idea\n• **Update your portfolio** — just tell me what you traded\n• **Vote or decide** on Governance research proposals\n• **Deep dive** on any stock, sector, or macro theme\n• **Fact-check** any claim in real time\n\nWhat would you like to explore?"}]);
  const [input,setInput]=useState("");
  const [loading,setLoading]=useState(false);
  const [analyseResolvedInfo,setAnalyseResolvedInfo]=useState(null); // {raw,yf,price,name} from /api/symbol/resolve
  const bottomRef=useRef(null);
  const prevCtxRef=useRef(null);

  useEffect(()=>{bottomRef.current?.scrollIntoView({behavior:"smooth"});},[messages]);

  useEffect(()=>{
    const key=ariaContext?.type+ariaContext?.id;
    if(!ariaContext||key===prevCtxRef.current)return;
    prevCtxRef.current=key;
    let intro="";
    if(ariaContext.type==="discovery"&&discoveryStock){
      intro=`I've pulled up the discovery idea for **${discoveryStock.symbol}** (${discoveryStock.name}).\n\nThis is a **new idea not yet in your portfolio** — surfaced by the research agents because: ${discoveryStock.discoveryReason}\n\n**Discovery Score: ${discoveryStock.discoveryScore}/100** · Confidence: ${discoveryStock.confidence}% · Risk: ${discoveryStock.riskScore}/100\n\nEntry: ${discoveryStock.entry} · Target: ${discoveryStock.target} · Stop-loss: ${discoveryStock.stoploss}\n\nWould you like me to walk through the full thesis, explain the risk score, or compare it to your existing holdings?`;
    } else if(ariaContext.type==="research_paper"&&ariaContext.paper){
      const p=ariaContext.paper;
      intro=`The Governance Research Agent has flagged a new paper for your review:\n\n**${p.title}** (${p.source})\n\nRelevance score: **${p.relevance}%**\n\nProposed change: ${p.proposedChange}\n\nCost impact: **${p.costImpact}**\n\nAgent votes so far: ${p.votes.for} FOR · ${p.votes.against} AGAINST · ${p.votes.abstain} ABSTAIN\n\nWould you like me to summarise the paper's key finding, explain the proposed change in plain terms, or walk you through the agent debate?`;
    } else if(ariaContext.type==="research_debate"&&ariaContext.paper){
      const p=ariaContext.paper;
      const dl=p.debateLog||p.debate_log||[];
      const forAgents=dl.filter(d=>d.stance==="for"||d.stance==="FOR").map(d=>d.agent||d.agent_name).filter(Boolean);
      const againstAgents=dl.filter(d=>d.stance==="against"||d.stance==="AGAINST").map(d=>d.agent||d.agent_name).filter(Boolean);
      intro=`This proposal is currently **tied in agent debate** — your vote is the tiebreaker.\n\n**${p.title}**\n\nThe core disagreement:\n• **FOR camp**: ${forAgents.length?forAgents.join(", "):"agents voting for this change"} argue it improves system accuracy\n• **AGAINST camp**: ${againstAgents.length?againstAgents.join(", "):"agents voting against"} raise concerns about latency or complexity\n\nTell me "approve", "reject", or ask me to walk through each side's argument before you decide.`;
    } else if(ariaContext.type==="research_approved"&&ariaContext.paper){
      intro=`This enhancement has been **approved by the agents**: **${ariaContext.paper.title}**\n\nReady to walk through the implementation steps. Cost: **${ariaContext.paper.costImpact}**\n\nShall I begin with Step 1?`;
    } else if(ariaContext.type==="daily_run"){
      const run=ariaContext.run;
      if(run){
        const dateStr=(run.runDate||run.run_date||"today").slice(0,10);
        const screened=run.totalScreened||(run.sliceSymbols||run.slice_symbols||[]).length||run.total_screened||0;
        const passed=run.totalPassed||(run.passedSymbols||run.passed_symbols||[]).length||run.total_passed||0;
        const discovered=run.totalDiscoveries||(run.discoverySymbols||run.discovery_symbols||[]).length||run.total_discoveries||0;
        const topDisc=(run.discoverySymbols||run.discovery_symbols||[]).slice(0,5).join(", ")||"none";
        const cov=run.coverageStats||run.coverage_stats||{};
        intro=`Here's what the discovery engine ran on **${dateStr}**:\n\n• **${screened}** stocks screened from NSE universe\n• **${passed}** passed fundamental pre-screening filters\n• **${discovered}** promoted to discovery recommendations\n${topDisc!=="none"?`\nTop new finds: **${topDisc}**\n`:""}\nCoverage: ${cov.cycle_pct_complete||"?"}% of full NSE universe this cycle · ${cov.monthly_passes||"?"}× monthly passes\n\nWould you like me to explain the screening methodology, deep-dive on any of the new discoveries, or compare them to your portfolio?`;
      }else{
        intro="The discovery engine runs daily at **06:00 IST** — screening 200 stocks from the full NSE universe each day, rotating through ~9-day cycles for full coverage.\n\nRun data isn't available yet for today. Check back after 06:00 IST, or ask me about yesterday's discovered stocks from the Discovery tab.";
      }
    } else if(ariaContext.type==="portfolio"){
      intro=`Portfolio context open. Your ${portfolio.length} holdings have a total value of ₹${portfolio.reduce((s,h)=>s+(h.currentPrice*h.qty),0).toLocaleString("en-IN",{maximumFractionDigits:0})}.\n\nYou can:\n• Tell me about a trade ("I bought DIXON at 15,800 — 20 shares")\n• Ask me to analyse any position\n• Tell me you've exited ("Sold my GOLDBEES at 6,100")\n\nWhat would you like to do?`;
    } else if(ariaContext.type==="holding"&&ariaContext.holding){
      const h=ariaContext.holding;
      const pct=((h.currentPrice-h.avgBuy)/h.avgBuy*100).toFixed(1);
      intro=`Looking at your **${h.symbol}** position:\n• ${h.qty} shares at avg ₹${h.avgBuy} → now ₹${h.currentPrice} (${pct>=0?"+":""}${pct}%)\n• P&L: ₹${Math.round((h.currentPrice-h.avgBuy)*h.qty).toLocaleString()}\n• Target ₹${h.targetPrice} · Stop ₹${h.stoplossPrice}\n\nWhat would you like to know — or have you made a change to this position?`;
    }
    if(intro)setMessages(p=>[...p,{role:"assistant",text:intro}]);
  },[ariaContext,discoveryStock,portfolio]);

  const portfolioSummary=(portfolio||[]).map(h=>`${h.symbol}:${h.qty}@₹${h.avgBuy}(now ₹${h.currentPrice},${((h.currentPrice-h.avgBuy)/h.avgBuy*100).toFixed(1)}%),tgt₹${h.targetPrice},stop₹${h.stoplossPrice}`).join("|");
  const discoverySummary=(_ariaDu||[]).map(s=>`${s.symbol}:${s.action} conf${s.confidence}% tgt${s.target} risk${s.riskScore}`).join("|")||"No discovery ideas loaded yet.";
  const latestRun=(_ariaRuns||[])[0];
  const runsSummary=latestRun
    ?`Latest discovery run ${latestRun.runDate||latestRun.run_date||""}: screened ${latestRun.totalScreened??latestRun.total_screened??0} stocks, ${latestRun.totalPassed??latestRun.total_passed??0} passed filters, ${latestRun.totalDiscoveries??latestRun.total_discoveries??0} new discoveries (${(latestRun.discoverySymbols||latestRun.discovery_symbols||[]).slice(0,5).join(",")||"none"}). Cycle: ${(latestRun.coverageStats||latestRun.coverage_stats)?.cycle_pct_complete||"?"}% complete.`
    :"No discovery run data available yet.";

  const SYSTEM=`You are ARIA (Adaptive Research Intelligence Assistant) for Bharat Intelligence — Indian stock and commodity market multi-agent system.

Personality: Senior analyst + trusted advisor. Precise, data-driven, warm, never sensational.

YOUR 7 ROLES:

1. DISCOVERY EXPLAINER: Explain stocks not in the user's portfolio that the research engine has surfaced. Highlight WHY the system found them interesting, the specific screen triggers, full risk/reward. Compare to existing portfolio for overlap or diversification. Help user decide whether to add to watchlist or portfolio.

2. RECOMMENDATION EXPLAINER: Explain portfolio stock recommendations, risk scores (0=safest, 100=highest risk), agent signals.

3. PORTFOLIO UPDATER: When user mentions a trade, extract and update portfolio. Output <portfolio_action> JSON at end of your response.
SYMBOL RULES — the backend auto-resolves symbols, so use the plain NSE ticker (no ".NS" suffix needed):
  • "I bought Reliance" → symbol:"RELIANCE"
  • "Added 50 shares of HDFC Bank at 1650" → symbol:"HDFCBANK", qty:50, avgBuy:1650
  • "Bought Zomato 100 shares ₹220" → symbol:"ZOMATO", qty:100, avgBuy:220
  • For indices/ETFs use the common name: NIFTY, SENSEX, GOLDBEES, NIFTYBEES
  • Never invent .NS/.BO suffixes; just use the raw NSE code.

BUY action JSON shape (use when user mentions buying/adding a position):
{"action":"add","symbol":"DIXON","qty":20,"avgBuy":15800,"targetPrice":19500,"stoplossPrice":13800,"sector":"Electronics/PLI","name":"Dixon Technologies","notes":"Added via ARIA","linkedRecId":null}

SELL / EXIT action JSON shape (use when user says they sold, exited, booked profit, or squared off):
{"action":"exit","symbol":"DIXON","exitPrice":18500,"qty":20,"notes":"Booked profit at target"}
  • exitPrice = the price they sold at (required)
  • qty = number of shares sold (required). Ask the user if unclear how many shares they sold.
  • symbol = plain NSE code of the stock they exited
  • PARTIAL SELL: if user sold FEWER shares than their full holding (e.g. "sold 125 of my 140 Voltas"), set qty to the number they sold (125). The remaining shares (15) will stay in portfolio automatically.
  • FULL EXIT: if user sold all shares or says "exited", set qty to their full holding size.
  • Only output exit action when user clearly states they have already sold (past tense). Do NOT output exit for "should I sell?" questions.

IMPORTANT: Output ONLY ONE <portfolio_action> tag per response, at the very end.

4. GOVERNANCE RESEARCH GUIDE: Explain AI research papers and their proposed system changes in plain English. Walk through agent debate arguments. When user approves/rejects, confirm and explain next step (GitHub PR creation, deployment, etc.). NEVER deploy without user saying "approve".

5. ENHANCEMENT GUIDE: Paid = state cost, get approval, walk steps. Free = guide directly.

6. FACT CHECKER: Reason carefully, flag uncertainty.

7. WARRENBOT EXPLAINER: When the user asks about WarrenBot scores, explain what they mean in plain English. Key points to always convey:
— WarrenBot is a long-term quality lens (3–10 year horizon), not a short-term momentum signal
— A score above 80 means the business has Buffett/Jhunjhunwala quality: strong moat, high ROCE, honest management, consistent earnings, and reasonable price
— A score below 50 does not mean avoid — it means the opportunity is driven by momentum or sentiment, not underlying business quality. This is fine for shorter time horizons.
— promoter_quality DISQUALIFIED is the single most important red flag — Jhunjhunwala rule: never invest with a promoter who has pledged more than 30% of shares
— Margin of Safety is the gap between intrinsic value and market price. Positive = buying at a discount. Negative = paying a premium.
— The india_consumption_play flag means this business benefits from India's rising income story — a multi-decade tailwind
— The jhunjhunwala_cyclical_flag means the business is in a cyclical sector trading near its historical trough valuation — high risk but Jhunjhunwala made his biggest returns here
— When comparing a high WarrenBot score stock to a low one, always ask the user: what is your time horizon? If 6–12 months, WarrenBot matters less. If 3–5 years, it matters a lot.

ON-DEMAND FULL ANALYSIS:
When the user types "/analyse SYMBOL" or "run analysis on SYMBOL" or "full analysis of SYMBOL" or "analyse SYMBOL now" or similar intent to trigger a full agent analysis:
1. ALWAYS normalise the company name to its NSE ticker symbol first. Use your knowledge of Indian stocks:
   — "tata steel" / "tata steel limited" / "tata steel ltd" → TATASTEEL
   — "hdfc bank" / "hdfcbank" / "hdfc" → HDFCBANK
   — "reliance" / "ril" / "reliance industries" → RELIANCE
   — "infosys" / "infy" → INFY
   — "wipro limited" → WIPRO
   — "tata motors" → TATAMOTORS
   — "bajaj finance" → BAJFINANCE
   — "asian paints" → ASIANPAINT
   — "zomato" → ETERNAL (rebranded 2025)
   — "l&t" / "larsen and toubro" → LT
   — If unsure, use your best guess at the NSE ticker — the system will validate it
2. Ask for confirmation showing the normalised ticker: "I'll run a full 10-agent analysis on **TATASTEEL** — this takes 2–4 minutes and queries all agents. Confirm? (yes/no)"
3. Output <confirm_analyse>TATASTEEL</confirm_analyse> using the NORMALISED ticker (not the raw user text)
4. If the user says yes/confirm/proceed, output <run_analyse>TATASTEEL</run_analyse> with the same normalised ticker
5. The system will run the analysis and show the result automatically
6. If the user says no/cancel, acknowledge and move on

WARREN BOT ON-DEMAND ANALYSIS:
When the user asks to "analyse [stock] like Buffett", "what would Jhunjhunwala think of [stock]", "Buffett analysis of [stock]", or similar:
1. Output <fetch_warren_bot>SYMBOL</fetch_warren_bot> on its own line (use plain NSE code, no .NS suffix, e.g. DIXON not DIXON.NS)
2. The system will fetch the Warren Bot data and provide it to you automatically
3. When you receive the data, present a structured plain-English response covering:
   — Conviction rating and overall score (what it means for this specific stock)
   — The single strongest reason to like it (from why_buffett_would_like)
   — The single strongest reason to pass (from why_buffett_would_pass)
   — Whether it suits long-term (3–5yr) vs short-term (6–12m) based on the score
   — If promoter_quality is DISQUALIFIED, make this the headline warning

CURRENT PORTFOLIO: ${portfolioSummary||"None"}
DISCOVERY IDEAS TODAY: ${discoverySummary}
TODAY'S SCREENER RUN: ${runsSummary}
ACTIVE CONTEXT: ${ariaContext?JSON.stringify({type:ariaContext.type,paper:ariaContext.paper?.title,holding:ariaContext.holding?.symbol,discovery:discoveryStock?.symbol}):"none"}
Market snapshot: ${_mktStr}.

FORMAT: 150-250 words normally. Use **bold** for key numbers. Output <portfolio_action> JSON only when certain of trade intent.`;

  const send=async()=>{
    if(!input.trim()||loading)return;
    const txt=input.trim();setInput("");
    setMessages(p=>[...p,{role:"user",text:txt}]);setLoading(true);
    try{
      const history=messages.slice(-14).map(m=>({role:m.role,content:m.text}));
      const ariaCall=(msgs)=>fetch("/api/aria",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({max_tokens:1000,system:SYSTEM,messages:msgs})});  // model is set server-side in api/aria.js

      // ── First pass ────────────────────────────────────────────────────────
      const res=await ariaCall([...history,{role:"user",content:txt}]);
      const data=await res.json();
      let raw=ariaText(data)||"Error.";

      // ── Warren Bot on-demand fetch ────────────────────────────────────────
      // ARIA outputs <fetch_warren_bot>SYMBOL</fetch_warren_bot> when it wants
      // live Warren Bot data. We fetch it and do a second-pass so ARIA can
      // present structured results without the user needing to do anything.
      const wbTagMatch=raw.match(/<fetch_warren_bot>([^<]+)<\/fetch_warren_bot>/i);
      if(wbTagMatch&&API_URL){
        const wbSymbol=wbTagMatch[1].trim().toUpperCase();
        let wbPayload="Warren Bot data unavailable — continue without it.";
        try{
          const wbRes=await apiFetch(`/api/warren_bot/${encodeURIComponent(wbSymbol)}`);
          if(wbRes?.analysis){
            const a=wbRes.analysis;
            // Summarise the most relevant fields into a compact context string
            // to avoid bloating the Anthropic context window with the full JSON.
            wbPayload=`Warren Bot data for ${wbSymbol} (score ${a.score}/100, ${a.conviction_rating}):
- Moat: ${a.moat_type} (strength ${a.moat_strength_score}/20)
- ROCE avg 10yr: ${a.roce_avg_10yr??'N/A'}%   Valuation score: ${a.valuation_score}/20
- Intrinsic value: ₹${a.intrinsic_value_per_share??'N/A'}   Margin of safety: ${a.margin_of_safety_pct??'N/A'}%
- 10yr EPS CAGR: ${a.ten_year_eps_cagr??'N/A'}%
- Promoter quality: ${a.promoter_quality}
- India consumption play: ${a.india_consumption_play}   Cyclical trough flag: ${a.jhunjhunwala_cyclical_flag}
- Why like: ${a.why_buffett_would_like||'—'}
- Why pass: ${a.why_buffett_would_pass||'—'}
- Key risks: ${(a.key_risks||[]).join('; ')}
- Data gaps: ${(a.data_gaps||[]).join(', ')||'none'}`;
          }
        }catch(e){/* silent — wbPayload stays as unavailable message */}

        // Strip the fetch tag from ARIA's first reply, then do a second pass
        // with the data injected so ARIA delivers the final structured response.
        const strippedFirst=(raw.replace(/<fetch_warren_bot>[^<]+<\/fetch_warren_bot>/gi,"").trim())||"Analysing…";
        const res2=await ariaCall([
          ...history,
          {role:"user",content:txt},
          {role:"assistant",content:strippedFirst},
          {role:"user",content:`${wbPayload}\n\nNow give me the full structured plain-English analysis as per Role 7.`},
        ]);
        const data2=await res2.json();
        raw=ariaText(data2)||raw;
      }

      // ── Confirm-analyse tag: resolve symbol in background, store for run step ─
      const confirmMatch=raw.match(/<confirm_analyse>([^<]+)<\/confirm_analyse>/i);
      if(confirmMatch){
        const rawSym=confirmMatch[1].trim().toUpperCase();
        if(API_URL){
          apiFetch(`/api/symbol/resolve?q=${encodeURIComponent(rawSym)}`)
            .then(r=>{if(r?.yf_symbol)setAnalyseResolvedInfo({raw:rawSym,yf:r.yf_symbol,price:r.price,name:r.name||""});})
            .catch(()=>{});
        }
      }

      // ── Run-analyse tag: use pre-resolved symbol, fall back to live resolve ──
      const runAnalyseMatch=raw.match(/<run_analyse>([^<]+)<\/run_analyse>/i);
      if(runAnalyseMatch&&API_URL){
        const rawSymbol=runAnalyseMatch[1].trim().toUpperCase();

        // Resolve symbol — use stored info from confirm step if available
        let yfSym=rawSymbol;
        let dispLabel=rawSymbol;
        const stored=analyseResolvedInfo?.raw===rawSymbol?analyseResolvedInfo:null;
        if(stored){
          yfSym=stored.yf;
          const namePart=stored.name&&stored.name!==rawSymbol?` (${stored.name})`:"";
          const pricePart=stored.price?` · ₹${Number(stored.price).toLocaleString("en-IN",{maximumFractionDigits:1})}`:"";
          dispLabel=yfSym!==rawSymbol?`${rawSymbol} → **${yfSym}**${namePart}${pricePart}`:`${rawSymbol}${namePart}${pricePart}`;
        }else{
          // Live resolve as fallback
          try{
            const r=await apiFetch(`/api/symbol/resolve?q=${encodeURIComponent(rawSymbol)}`);
            if(r?.yf_symbol){
              yfSym=r.yf_symbol;
              const namePart=r.name&&r.name!==rawSymbol?` (${r.name})`:"";
              const pricePart=r.price?` · ₹${Number(r.price).toLocaleString("en-IN",{maximumFractionDigits:1})}`:"";
              dispLabel=yfSym!==rawSymbol?`${rawSymbol} → **${yfSym}**${namePart}${pricePart}`:`${rawSymbol}${namePart}${pricePart}`;
            }
          }catch(e){/* use raw symbol as-is */}
        }
        setAnalyseResolvedInfo(null);

        setMessages(p=>[...p,{role:"assistant",text:`⏳ Running full 10-agent analysis on **${dispLabel}**… this takes 2–4 minutes (sentiment and synthesis are the slow steps). Please keep this tab open.`}]);
        try{
          const aRes=await apiFetch("/api/analyse",{method:"POST",body:JSON.stringify({symbol:yfSym})});
          if(aRes?.status==="OK"&&aRes?.analysis){
            const a=aRes.analysis;
            const displaySym=aRes.yf_symbol||yfSym;
            const agentLines=Object.entries(aRes.agents||{}).map(([k,v])=>`• ${k}: **${v.signal||"—"}** (${v.score??'?'}/100)`).join("\n");
            const analysisText=`## Full Analysis — ${rawSymbol}${displaySym!==rawSymbol?` (${displaySym})`:""}

**${a.action}** | Confidence: **${a.confidence}%** | Risk: ${a.risk_score}/100
Entry: ₹${a.entry_low??'—'}–₹${a.entry_high??'—'} | Target: ₹${a.target??'—'} | Stoploss: ₹${a.stoploss??'—'}
Upside: **${a.upside_pct??'—'}%** | Horizon: ${a.horizon_days??180} days

**Bull case:**
${(a.bull_case||[]).map(b=>`• ${b}`).join("\n")||"—"}

**Bear case:**
${(a.bear_case||[]).map(b=>`• ${b}`).join("\n")||"—"}

**Synthesis:** ${a.synthesis||"—"}

**Agent signals:**
${agentLines||"—"}`;
            setMessages(p=>[...p,{role:"assistant",text:analysisText}]);
          }else{
            setMessages(p=>[...p,{role:"assistant",text:`Analysis for **${rawSymbol}** returned no recommendation — ${aRes?.detail||"data may be insufficient or synthesis was suppressed."}`}]);
          }
        }catch(e){
          setMessages(p=>[...p,{role:"assistant",text:`⚠ Analysis failed for **${rawSymbol}** (resolved: ${yfSym}) — ${e.message}.\n\nIf the ticker looks wrong, try telling ARIA the exact NSE symbol, e.g. *"analyse TATASTEEL"*.`}]);
        }
        setLoading(false);return;
      }

      // ── Portfolio action tag ──────────────────────────────────────────────
      const actionMatch=raw.match(/<portfolio_action>([\s\S]*?)<\/portfolio_action>/);
      const displayText=raw
        .replace(/<portfolio_action>[\s\S]*?<\/portfolio_action>/g,"")
        .replace(/<fetch_warren_bot>[^<]+<\/fetch_warren_bot>/gi,"")
        .replace(/<confirm_analyse>[^<]+<\/confirm_analyse>/gi,"")
        .replace(/<run_analyse>[^<]+<\/run_analyse>/gi,"")
        .trim();
      if(actionMatch){try{onPortfolioUpdate(JSON.parse(actionMatch[1].trim()));}catch(e){console.error("portfolio_action parse failed:",e,actionMatch[1]);}}
      setMessages(p=>[...p,{role:"assistant",text:displayText,hasAction:!!actionMatch}]);
    }catch(e){setMessages(p=>[...p,{role:"assistant",text:"Connection error — check API configuration."}]);}
    setLoading(false);
  };

  const QUICK=ariaContext?.type==="discovery"
    ?["Explain why it screened","Compare to my portfolio","What's the main risk?","Add to my portfolio","Explain the WarrenBot score","Would Buffett buy this?","What does the moat score mean?"]
    :ariaContext?.type?.includes("research")
    ?["Summarise the paper","Walk me through the debate","Approve this change","What's the implementation cost?"]
    :ariaContext?.type==="holding"
    ?["Should I hold or sell?","How close is my stop-loss?","I sold this today","Add more at current price?","Explain the WarrenBot score","Would Buffett buy this?","What does the moat score mean?"]
    :ariaContext?.type==="daily_run"
    ?["Explain the screening methodology","Which new find looks best?","Compare discoveries to my portfolio","How many stocks are left in this cycle?","Show me the top discovery idea"]
    :["Show me today's discovery ideas","Analyse my portfolio","What's the best new opportunity?","Explain a governance proposal","What ran today?"];

  const renderMsg=txt=>txt.split('\n').map((line,i)=>{
    const parts=line.split(/(\*\*[^*]+\*\*)/g);
    return<div key={i} style={{marginBottom:line===''?4:1,lineHeight:1.65}}>
      {parts.map((p,j)=>p.startsWith("**")?<b key={j} style={{color:C.text}}>{p.slice(2,-2)}</b>:<span key={j}>{p}</span>)}
    </div>;
  });

  return(
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:C.bg}}>
      <div style={{padding:"10px 13px",borderBottom:`1px solid ${C.border}`,background:`linear-gradient(135deg,${C.purple}08,${C.blue}08)`,flexShrink:0}}>
        <div style={{display:"flex",alignItems:"center",gap:8}}>
          <div className="aria-glow" style={{width:28,height:28,borderRadius:"50%",background:`linear-gradient(135deg,${C.purple},${C.blue})`,display:"flex",alignItems:"center",justifyContent:"center",fontSize:13}}>✦</div>
          <div style={{flex:1}}><div style={{fontSize:12.5,fontWeight:700,color:C.text}}>ARIA</div><div style={{fontSize:10.5,color:C.purple}}>Adaptive Research Intelligence Assistant</div></div>
          <Dot color={C.purple} pulse/>
        </div>
        {ariaContext&&(
          <div style={{display:"flex",gap:5,marginTop:5,flexWrap:"wrap",alignItems:"center"}}>
            {ariaContext.type==="discovery"&&discoveryStock&&<Tag color={C.cyan} small>Discovery · {discoveryStock.symbol}</Tag>}
            {ariaContext.type?.includes("research")&&ariaContext.paper&&<Tag color={C.purple} small>Research</Tag>}
            {ariaContext.type==="holding"&&ariaContext.holding&&<Tag color={C.teal} small>Holding · {ariaContext.holding.symbol}</Tag>}
            {ariaContext.type==="portfolio"&&<Tag color={C.teal} small>Portfolio</Tag>}
            {ariaContext.type==="daily_run"&&<Tag color={C.orange} small>Today's run</Tag>}
            <button onClick={onClearContext} style={{background:"none",border:"none",color:C.muted,cursor:"pointer",fontSize:12,marginLeft:"auto"}}>✕</button>
          </div>
        )}
      </div>

      <div style={{flex:1,overflowY:"auto",padding:11,display:"flex",flexDirection:"column",gap:8}}>
        {messages.map((m,i)=>(
          <div key={i} style={{display:"flex",justifyContent:m.role==="user"?"flex-end":"flex-start",animation:"fadeUp .2s ease"}}>
            {m.role==="assistant"&&<div style={{width:17,height:17,borderRadius:"50%",background:`linear-gradient(135deg,${C.purple},${C.blue})`,display:"flex",alignItems:"center",justifyContent:"center",fontSize:10.5,marginRight:5,marginTop:3,flexShrink:0}}>✦</div>}
            <div style={{maxWidth:"88%",padding:"7px 10px",borderRadius:m.role==="user"?"10px 10px 2px 10px":"2px 10px 10px 10px",
              background:m.role==="user"?`linear-gradient(135deg,${C.accent}ee,${C.accentDim}ee)`:C.panel,
              color:m.role==="user"?C.onFill:C.text,fontSize:12.5,border:m.role==="assistant"?`1px solid ${C.border}`:"none"}}>
              {m.role==="assistant"?renderMsg(m.text):m.text}
              {m.hasAction&&<div style={{marginTop:5,background:C.teal+"12",border:`1px solid ${C.teal}33`,borderRadius:4,padding:"3px 7px",fontSize:11,color:C.teal,fontWeight:700}}>✓ Portfolio updated</div>}
            </div>
          </div>
        ))}
        {loading&&<div style={{display:"flex",gap:3,paddingLeft:23}}>{[0,1,2].map(i=><div key={i} style={{width:6,height:6,borderRadius:"50%",background:C.purple,animation:`dotBounce 1.2s ${i*.15}s infinite`}}/>)}</div>}
        <div ref={bottomRef}/>
      </div>

      <div style={{padding:"4px 9px",display:"flex",gap:4,flexWrap:"wrap",borderTop:`1px solid ${C.border}`,flexShrink:0}}>
        {QUICK.map(q=><button key={q} onClick={()=>setInput(q)} style={{background:C.panel,border:`1px solid ${C.borderHi}`,borderRadius:20,padding:"2px 8px",color:C.textDim,fontSize:11,cursor:"pointer"}}>{q}</button>)}
      </div>
      <div style={{padding:"5px 9px 9px",display:"flex",gap:5,flexShrink:0}}>
        <input value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>e.key==="Enter"&&send()}
          placeholder={ariaContext?.type==="discovery"?"Ask about this discovery idea...":ariaContext?.type?.includes("research")?"Discuss or approve this research proposal...":"Ask ARIA anything..."}
          style={{flex:1,background:C.panel,border:`1px solid ${C.borderHi}`,borderRadius:6,padding:"7px 10px",color:C.text,fontSize:12.5,outline:"none",fontFamily:SANS}}/>
        <button onClick={send} disabled={loading||!input.trim()} style={{background:loading?C.panel:`linear-gradient(135deg,${C.purple},${C.blue})`,border:"none",borderRadius:6,padding:"7px 12px",color:loading?C.muted:C.onFill,fontWeight:700,fontSize:13,cursor:loading?"default":"pointer"}}>→</button>
      </div>
    </div>
  );
}

// ─── SYSTEM HEALTH PANEL ─────────────────────────────────────────────────────
function DataSourceQualityPanel({data}){
  if(!data?.days?.length) return null;
  const days = [...data.days].reverse(); // oldest first for left-to-right chart
  const maxTotal = Math.max(...days.map(d=>d.full+d.fallback+d.estimated+d.unknown), 1);
  return(
    <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:12}}>
      <div style={{fontSize:12.5,fontWeight:700,color:C.text,marginBottom:10}}>Data Source Quality — Last {data.days.length} Runs</div>
      <div style={{display:"flex",gap:3,alignItems:"flex-end",height:60,marginBottom:6}}>
        {days.map((d,i)=>{
          const pctDeg=d.pctDegraded||0;
          const barColor=pctDeg>50?C.red:pctDeg>30?C.accent:C.green;
          return(
            <div key={i} title={`${d.date}: Full=${d.full} Fallback=${d.fallback} Est=${d.estimated} Unknown=${d.unknown} (${pctDeg}% degraded)`}
              style={{flex:1,display:"flex",flexDirection:"column",gap:1,alignItems:"stretch",height:"100%",justifyContent:"flex-end"}}>
              <div style={{background:C.green,height:`${d.full/maxTotal*100}%`,borderRadius:"2px 2px 0 0",opacity:.85}}/>
              <div style={{background:C.accent,height:`${d.fallback/maxTotal*100}%`}}/>
              <div style={{background:C.red,height:`${(d.estimated+d.unknown)/maxTotal*100}%`}}/>
              {pctDeg>30&&<div style={{width:"100%",height:2,background:barColor,borderRadius:1}}/>}
            </div>
          );
        })}
      </div>
      <div style={{display:"flex",gap:10,fontSize:10.5,color:C.muted,marginBottom:6}}>
        <span style={{color:C.green}}>■ Full</span>
        <span style={{color:C.accent}}>■ Fallback</span>
        <span style={{color:C.red}}>■ Est/Unknown</span>
        <span style={{marginLeft:"auto"}}>Red bar = &gt;30% degraded</span>
      </div>
      {data.days[0]?.pctDegraded>30&&(
        <div style={{background:C.red+"15",border:`1px solid ${C.red}44`,borderRadius:5,padding:"5px 8px",fontSize:11,color:C.red}}>
          ⚠ Latest run: {data.days[0].pctDegraded}% of symbols used fallback/estimated data — check screener.in cookies or proxy health
        </div>
      )}
    </div>
  );
}

function SystemHealthPanel({health}){
  if(!health) return null;
  const {checks=[], errors=0, warnings=0, checked_at} = health;
  if(!checks.length) return null;

  // INFO = deprecated/note items (e.g. Breeze marked for removal) — shown like OK but muted
  const sevColor = s => s==="error"?C.red : s==="warning"?C.accent : s==="info"?C.muted : C.green;
  const sevIcon  = s => s==="error"?"✕" : s==="warning"?"⚠" : s==="info"?"ℹ" : "✓";
  const hasIssues = errors>0 || warnings>0;
  const infoCount = checks.filter(c=>c.severity==="info").length;

  return(
    <div style={{
      background: hasIssues ? C.accent+"0a" : C.green+"08",
      border:`1px solid ${hasIssues?(errors>0?C.red:C.accent):C.green}33`,
      borderRadius:8, padding:12, marginBottom:14,
    }}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
        <div style={{display:"flex",alignItems:"center",gap:8}}>
          <span style={{fontSize:12.5,fontWeight:700,color:hasIssues?(errors>0?C.red:C.accent):C.green}}>
            Data source health
          </span>
          {errors>0 && <span style={{background:C.red,color:C.onFill,borderRadius:8,padding:"0 5px",fontSize:11,fontWeight:700}}>{errors} error{errors>1?"s":""}</span>}
          {warnings>0 && <span style={{background:C.accent,color:C.onFill,borderRadius:8,padding:"0 5px",fontSize:11,fontWeight:700}}>{warnings} warning{warnings>1?"s":""}</span>}
          {!hasIssues && <span style={{fontSize:11,color:C.green}}>All systems nominal</span>}
          {infoCount>0 && !hasIssues && <span style={{fontSize:11,color:C.muted}}>{infoCount} note{infoCount>1?"s":""}</span>}
        </div>
        <span style={{fontSize:10.5,color:C.muted}}>{checked_at ? new Date(checked_at).toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit"}) + " IST" : ""}</span>
      </div>
      <div style={{display:"flex",flexDirection:"column",gap:5}}>
        {checks.map((c,i)=>{
          const col = sevColor(c.severity);
          // OK and INFO: compact single-line display
          if(c.severity==="ok"||c.severity==="info") return(
            <div key={i} style={{display:"flex",alignItems:"center",gap:6,padding:"3px 0",borderBottom:`1px solid ${C.border}22`,opacity:c.severity==="info"?0.65:1}}>
              <span style={{fontSize:11,color:col,width:10,flexShrink:0,fontWeight:700}}>{sevIcon(c.severity)}</span>
              <span style={{fontSize:11,fontWeight:600,color:C.textDim,width:130,flexShrink:0}}>{c.name}</span>
              <span style={{fontSize:11,color:C.muted,flex:1}}>{c.detail}</span>
              {c.severity==="info" && <span style={{fontSize:10.5,color:C.muted,fontStyle:"italic",flexShrink:0}}>deprecated</span>}
            </div>
          );
          // WARNING / ERROR: highlighted card
          return(
            <div key={i} style={{background:col+"0d",border:`1px solid ${col}33`,borderRadius:5,padding:"6px 9px"}}>
              <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:c.action?3:0}}>
                <span style={{fontSize:12,color:col,fontWeight:700,flexShrink:0}}>{sevIcon(c.severity)}</span>
                <span style={{fontSize:11,fontWeight:700,color:col,flex:1}}>{c.name}</span>
                <Tag color={col} small>{c.severity.toUpperCase()}</Tag>
              </div>
              <div style={{fontSize:11,color:C.textDim,marginLeft:16,marginBottom:c.action?3:0}}>{c.detail}</div>
              {c.action && <div style={{fontSize:10.5,color:col,background:col+"10",borderRadius:3,padding:"2px 7px",marginLeft:16}}>{c.action}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── PAPER PORTFOLIO PANEL (P5-B) ────────────────────────────────────────────
function PaperPortfolioPanel({data, history, apiLoaded}){
  const [expanded, setExpanded] = useState(false);
  const snap   = data?.summary   || {};
  const open   = data?.open_positions || [];
  const closed = data?.trade_history   || [];
  const pnlPct = snap.total_pnl_pct ?? null;
  const alpha  = snap.alpha_pct       ?? null;
  const pnlCol = v => v == null ? C.muted : v >= 0 ? C.green : C.red;
  const fmt    = v => v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;

  return(
    <div style={{border:`1px solid ${C.border}`,borderRadius:8,marginTop:14,overflow:"hidden"}}>
      <button
        aria-expanded={expanded}
        onClick={()=>setExpanded(e=>!e)}
        style={{width:"100%",background:C.surface,border:"none",padding:"9px 12px",
          display:"flex",justifyContent:"space-between",alignItems:"center",cursor:"pointer"}}
      >
        <div style={{display:"flex",alignItems:"center",gap:8}}>
          <span style={{fontFamily:SERIF,fontSize:18,fontWeight:600,color:C.text}}>Paper portfolio <span style={{fontFamily:SANS,fontSize:12,fontWeight:400,color:C.textDim}}>simulation</span></span>
          <span style={{fontSize:10.5,color:C.muted}}>auto-follows BUY signals · validates system before live trading</span>
        </div>
        <div style={{display:"flex",gap:12,alignItems:"center"}}>
          {pnlPct!=null&&<span style={{fontSize:11,fontWeight:700,color:pnlCol(pnlPct),fontFamily:MONO}}>{fmt(pnlPct)} total</span>}
          {alpha!=null&&<span style={{fontSize:11,color:pnlCol(alpha),fontFamily:MONO}}>alpha {fmt(alpha)}</span>}
          {data?.win_rate!=null&&<span style={{fontSize:11,color:C.muted}}>{data.win_rate.toFixed(0)}% win rate</span>}
          <span style={{fontSize:11,color:C.muted}}>{expanded?"▲":"▼"}</span>
        </div>
      </button>

      {expanded&&(
        <div style={{padding:"10px 12px",background:C.bg}}>
          {!apiLoaded&&<div style={{fontSize:11,color:C.muted,textAlign:"center",padding:16}}>Loading paper portfolio…</div>}
          {apiLoaded&&!snap.total_invested&&open.length===0&&(
            <EmptyState icon="📋" title="Paper portfolio initialising"
              sub="Paper positions open automatically for each new BUY recommendation. Run: python -m agents.paper_portfolio --run --backfill to seed historical recs." />
          )}

          {/* Summary stats row */}
          {snap.total_invested>0&&(
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(120px,1fr))",gap:8,marginBottom:12}}>
              {[
                ["Invested",`₹${(snap.total_invested||0).toLocaleString("en-IN",{maximumFractionDigits:0})}`,C.text],
                // total_pnl_pct = realised + unrealised over ALL capital deployed
                // (it used to be unrealised / open capital and hid realised losses)
                ["Total P&L",`${fmt(pnlPct)} · ₹${Math.round(snap.total_pnl||0).toLocaleString("en-IN")}`,pnlCol(pnlPct)],
                ["Open positions",fmt(snap.unrealized_pnl_pct??null),pnlCol(snap.unrealized_pnl_pct)],
                ["Realized P&L",`₹${(snap.realized_pnl||0).toLocaleString("en-IN",{maximumFractionDigits:0})}`,pnlCol(snap.realized_pnl)],
                ["vs Nifty (alpha)",fmt(alpha),pnlCol(alpha)],
                ["Win Rate",data?.win_rate!=null?`${data.win_rate.toFixed(0)}%`:"—",data?.win_rate>=50?C.green:C.red],
              ].map(([l,v,col])=>(
                <div key={l} style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:6,padding:"7px 10px"}}>
                  <div style={{fontSize:10,color:C.muted,textTransform:"uppercase",marginBottom:2}}>{l}</div>
                  <div style={{fontSize:14,fontWeight:800,color:col,fontFamily:MONO}}>{v}</div>
                </div>
              ))}
            </div>
          )}

          {/* P&L chart — simple bar representation */}
          {Array.isArray(history?.snapshots)&&history.snapshots.length>1&&(()=>{
            const snaps = history.snapshots;
            const maxAbs = Math.max(...snaps.map(s=>Math.abs(s.total_pnl_pct||0)), 0.1);
            return(
              <div style={{marginBottom:12}}>
                <div style={{fontSize:11,fontWeight:700,color:C.muted,marginBottom:6}}>Portfolio P&L vs Nifty</div>
                <div style={{display:"flex",gap:1,alignItems:"flex-end",height:40}}>
                  {snaps.slice(-60).map((s,i)=>{
                    const pct = s.total_pnl_pct || 0;
                    const nif = s.nifty_return_pct || 0;
                    const h   = Math.min(36, Math.abs(pct) / maxAbs * 36);
                    const hn  = Math.min(36, Math.abs(nif) / maxAbs * 36);
                    return(
                      <div key={i} title={`${s.snapshot_date}: P&L ${fmt(pct)}, Nifty ${fmt(nif)}`}
                        style={{flex:1,display:"flex",flexDirection:"column",justifyContent:"flex-end",gap:1,cursor:"help"}}>
                        <div style={{height:h,background:pct>=0?C.green:C.red,borderRadius:"1px 1px 0 0",opacity:0.8}}/>
                        <div style={{height:hn,background:C.muted,borderRadius:"1px 1px 0 0",opacity:0.3}}/>
                      </div>
                    );
                  })}
                </div>
                <div style={{display:"flex",justifyContent:"space-between",marginTop:3}}>
                  <span style={{fontSize:10,color:C.muted}}>{snaps[0]?.snapshot_date}</span>
                  <div style={{display:"flex",gap:8}}>
                    <span style={{fontSize:10,color:C.green}}>■ Portfolio</span>
                    <span style={{fontSize:10,color:C.muted}}>■ Nifty</span>
                  </div>
                  <span style={{fontSize:10,color:C.muted}}>{snaps[snaps.length-1]?.snapshot_date}</span>
                </div>
              </div>
            );
          })()}

          {/* Open positions */}
          {open.length>0&&(
            <div style={{marginBottom:10}}>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:5}}>
                <div style={{fontSize:11,fontWeight:700,color:C.muted,textTransform:"uppercase"}}>Open Positions ({open.length})</div>
                {open.length>15&&<button onClick={()=>setExpanded(e=>!e)} style={{fontSize:10,color:C.purple,background:"none",border:"none",cursor:"pointer",padding:0}}>showing all {open.length}</button>}
              </div>
              <div style={{border:`1px solid ${C.border}`,borderRadius:6,overflow:"auto",maxHeight:400}}>
                <div style={{display:"grid",gridTemplateColumns:"1fr 55px 65px 65px 70px 80px",minWidth:460,background:C.panel,padding:"5px 9px",gap:4,position:"sticky",top:0,zIndex:1}}>
                  {["Symbol","Qty","Entry","Current","P&L%","Tier"].map(h=>(
                    <div key={h} style={{fontSize:10,color:C.muted,fontWeight:700,textTransform:"uppercase"}}>{h}</div>
                  ))}
                </div>
                {open.map((p,i)=>{
                  const pct = p.unrealized_pnl_pct??0;
                  return(
                    <div key={p.id} style={{
                      display:"grid",gridTemplateColumns:"1fr 55px 65px 65px 70px 80px",minWidth:460,
                      padding:"5px 9px",gap:4,alignItems:"center",
                      background:i%2===0?C.bg:C.surface,
                      borderTop:`1px solid ${C.border}`,
                    }}>
                      <div style={{fontSize:11,fontWeight:600,color:C.text}}>{p.symbol}</div>
                      <div style={{fontSize:11,color:C.muted,fontFamily:MONO}}>{p.quantity}</div>
                      <div style={{fontSize:11,color:C.muted,fontFamily:MONO}}>₹{(p.entry_price||0).toLocaleString("en-IN",{maximumFractionDigits:0})}</div>
                      <div style={{fontSize:11,color:C.text,fontFamily:MONO}}>₹{(p.current_price||0).toLocaleString("en-IN",{maximumFractionDigits:0})}</div>
                      <div style={{fontSize:11,fontWeight:700,color:pnlCol(pct),fontFamily:MONO}}>{fmt(pct)}</div>
                      <div style={{fontSize:10,color:C.muted,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{(p.position_label||"").replace(" position","").replace(" (5%)","").replace(" (2.5%)","").replace(" (1.25%","")}</div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Trade history table */}
          {closed.length>0&&(
            <div>
              <div style={{fontSize:11,fontWeight:700,color:C.muted,marginBottom:5,textTransform:"uppercase"}}>
                Trade History ({closed.length})
              </div>
              <div style={{border:`1px solid ${C.border}`,borderRadius:6,overflow:"hidden"}}>
                <div style={{overflowX:"auto",maxHeight:320,overflowY:"auto"}}>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:11,fontFamily:MONO}}>
                    <thead>
                      <tr style={{background:C.panel,position:"sticky",top:0,zIndex:1}}>
                        {["Symbol","Entry","Exit","Entry ₹","Exit ₹","P&L%","Alpha","Alloc ₹","Reason"].map(h=>(
                          <th key={h} style={{padding:"5px 8px",textAlign:"left",fontSize:10,color:C.muted,
                            fontWeight:700,textTransform:"uppercase",whiteSpace:"nowrap",borderBottom:`1px solid ${C.border}`}}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {closed.map((p,i)=>{
                        const pct = p.realized_pnl_pct??0;
                        const alp = p.alpha_pct;
                        const entryD = p.entry_date ? p.entry_date.slice(0,10) : "—";
                        const exitD  = p.exit_date  ? p.exit_date.slice(0,10)  : "—";
                        const entryP = p.entry_price ? `₹${Number(p.entry_price).toLocaleString("en-IN",{maximumFractionDigits:0})}` : "—";
                        const exitP  = p.exit_price  ? `₹${Number(p.exit_price).toLocaleString("en-IN",{maximumFractionDigits:0})}`  : "—";
                        const alloc  = p.allocation_inr ? `₹${Number(p.allocation_inr).toLocaleString("en-IN",{maximumFractionDigits:0})}` : "—";
                        const reason = (p.exit_reason||"").replace("_"," ").toLowerCase();
                        return(
                          <tr key={p.symbol+(p.exit_date||i)}
                            style={{background:i%2===0?C.bg:C.surface,borderTop:`1px solid ${C.border}`}}>
                            <td style={{padding:"5px 8px",fontWeight:700,color:C.text,whiteSpace:"nowrap"}}>{p.symbol}</td>
                            <td style={{padding:"5px 8px",color:C.muted,whiteSpace:"nowrap"}}>{entryD}</td>
                            <td style={{padding:"5px 8px",color:C.muted,whiteSpace:"nowrap"}}>{exitD}</td>
                            <td style={{padding:"5px 8px",color:C.muted}}>{entryP}</td>
                            <td style={{padding:"5px 8px",color:C.text}}>{exitP}</td>
                            <td style={{padding:"5px 8px",fontWeight:700,color:pnlCol(pct)}}>{fmt(pct)}</td>
                            <td style={{padding:"5px 8px",color:alp!=null?pnlCol(alp):C.muted}}>{alp!=null?fmt(alp):"—"}</td>
                            <td style={{padding:"5px 8px",color:C.muted}}>{alloc}</td>
                            <td style={{padding:"5px 8px",color:C.muted,fontSize:10.5,whiteSpace:"nowrap"}}>{reason||"—"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// ─── MARKET DIGEST PANEL (P6-C) ───────────────────────────────────────────────
// ─── MARKET ───────────────────────────────────────────────────────────────────
const MOOD_TONE={BULLISH:"buy",BEARISH:"sell",VOLATILE:"warn",MIXED:"hold",NEUTRAL:"mute"};
const IMPACT_TONE={POSITIVE:"buy",NEGATIVE:"sell",WATCH:"warn",NEUTRAL:"mute"};
const REGIME_LABEL={BULL:"Bull market",BEAR:"Bear market",SIDEWAYS:"Range-bound",HIGH_VOLATILITY:"High volatility"};
const REGIME_TONE={BULL:"buy",BEAR:"sell",SIDEWAYS:"hold",HIGH_VOLATILITY:"warn"};
const NEWS_TOPICS=["NIFTY","SENSEX","FII","RBI","BUDGET"];
const prettyState=v=>v?String(v).replace(/_/g," ").toLowerCase().replace(/^\w/,c=>c.toUpperCase()):"—";
const isoDaysAgo=n=>{const d=new Date();d.setDate(d.getDate()-n);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;};

function MarketTab({pulse,regime,digestData,apiLoaded,news,newsTopic,onNewsTopic,onWhatRan,onAskAria}){
  const narrow=useNarrow(760);
  const [kind,setKind]=useState(null);              // MORNING | CLOSING
  const [fallback,setFallback]=useState(null);      // {date,digests} when today has none
  const [fallbackState,setFallbackState]=useState("idle"); // idle|loading|done|none
  const [newsState,setNewsState]=useState("idle");  // idle|loading|error
  const [newsLimit,setNewsLimit]=useState(8);
  const [showAllEvents,setShowAllEvents]=useState(false);

  const todays=digestData?.digests||[];
  // Weekends and holidays have no brief: walk back up to a week for the latest one.
  // A ref (not state) guards the one-off search, so flipping the status to
  // "loading" doesn't re-run the effect and cancel the search it just started.
  const fallbackStarted=useRef(false);
  const mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{
    if(!IS_LIVE||!apiLoaded||!digestData||todays.length>0||fallbackStarted.current) return;
    fallbackStarted.current=true;
    setFallbackState("loading");
    (async()=>{
      for(let i=1;i<=7;i++){
        const d=isoDaysAgo(i);
        try{
          const r=await apiFetch(`/api/market/digest?digest_date=${d}`);
          if(r?.digests?.length){ if(mounted.current){setFallback({date:d,digests:r.digests});setFallbackState("done");} return; }
        }catch(e){ break; }
      }
      if(mounted.current) setFallbackState("none");
    })();
  },[apiLoaded,digestData,todays.length]);

  const digests=todays.length?todays:(fallback?.digests||[]);
  const fromDate=todays.length?null:fallback?.date;
  const byType=t=>digests.find(d=>d.digestType===t);
  const active=kind||(byType("CLOSING")?"CLOSING":"MORNING");
  const dg=byType(active);
  const events=dg?.keyEvents||[];
  const shownEvents=showAllEvents?events:events.slice(0,5);

  const pickTopic=t=>{
    if(t===newsTopic) return;
    setNewsState("loading"); setNewsLimit(8);
    Promise.resolve(onNewsTopic(t)).then(ok=>setNewsState(ok===false?"error":"idle")).catch(()=>setNewsState("error"));
  };
  const shownNews=(news||[]).slice(0,newsLimit);
  const loadingPulse=IS_LIVE&&!apiLoaded&&(pulse||[]).length===0;

  return(
    <div style={{animation:"fadeUp .3s ease",maxWidth:1440,margin:"0 auto",display:"flex",flexDirection:"column",gap:22}}>
      <header style={{display:"flex",justifyContent:"space-between",alignItems:"flex-end",gap:12,flexWrap:"wrap"}}>
        <div>
          <h1 style={{fontFamily:SERIF,fontSize:narrow?26:32,fontWeight:600,margin:0,letterSpacing:-.3,lineHeight:1.15}}>Market</h1>
          <p style={{margin:"4px 0 0",fontSize:14,color:C.textDim}}>Indices, commodities and flows · prices refresh every 60 seconds</p>
        </div>
        {IS_LIVE&&<button onClick={onWhatRan} style={edBtn()}><Icon name="run"/>What ran today?</button>}
      </header>

      <section aria-label="Prices">
        {loadingPulse?(
          <div aria-busy="true" style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(150px,1fr))",gap:12}}>
            {[0,1,2,3,4,5,6,7].map(i=><div key={i} style={{height:92,borderRadius:8,background:C.panel,animation:"pulse 1.4s ease-in-out infinite"}}/>)}
          </div>
        ):(pulse||[]).length===0?(
          <p style={{margin:0,fontSize:13.5,color:C.textDim}}>Prices aren't available right now. They need a live backend connection.</p>
        ):(
          <div style={{display:"grid",gridTemplateColumns:`repeat(auto-fill,minmax(${narrow?140:170}px,1fr))`,gap:12}}>
            {pulse.map(m=>{
              const flat=!m.change||/^[+-]?0(\.0+)?%$/.test(m.change)||/flat|unch/i.test(m.change);
              const col=flat?C.textDim:m.up?C.green:C.red;
              return(
                <div key={m.key} style={{...edBox,padding:"14px 16px",minWidth:0}}>
                  <div style={{fontSize:11,color:C.textDim,textTransform:"uppercase",letterSpacing:".06em",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{m.key}</div>
                  <div style={{fontFamily:MONO,fontSize:narrow?18:21,fontWeight:600,marginTop:4,whiteSpace:"nowrap"}}>{m.value}</div>
                  <div style={{fontFamily:MONO,fontSize:12.5,marginTop:2,color:col}}>{flat?"■":m.up?"▲":"▼"} {m.change||"—"}</div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {regime&&(
        <section aria-label="Market regime" style={{...edBox,display:"flex",flexWrap:"wrap",gap:"14px 28px",alignItems:"center"}}>
          <div style={{display:"flex",alignItems:"center",gap:10,flexWrap:"wrap"}}>
            <span style={{fontFamily:SERIF,fontSize:19,fontWeight:600}}>Market regime</span>
            <EdTag tone={REGIME_TONE[regime.regime]||"mute"}>{REGIME_LABEL[regime.regime]||prettyState(regime.regime)}</EdTag>
            {regime.confidence!=null&&<span style={{fontFamily:MONO,fontSize:13,color:C.textDim}}>{regime.confidence}% confidence</span>}
          </div>
          {[["Nifty trend",regime.nifty_trend],["Volatility (VIX)",regime.vix_state],["FII flows",regime.fii_trend],["Breadth",regime.breadth_state],["Momentum",regime.momentum_state]].filter(([,v])=>v).map(([k,v])=>(
            <div key={k} style={{display:"flex",flexDirection:"column",fontSize:12,color:C.textDim}}>
              {k}<b style={{color:C.text,fontSize:13.5,fontWeight:600}}>{prettyState(v)}</b>
            </div>
          ))}
          {regime.regime_date&&<span style={{fontSize:12,color:C.textDim,marginLeft:narrow?0:"auto"}}>As of {fmtDate(regime.regime_date)}</span>}
        </section>
      )}

      <div style={{display:"grid",gridTemplateColumns:narrow?"minmax(0,1fr)":"minmax(0,1.6fr) minmax(0,1fr)",gap:16,alignItems:"start"}}>
        <section aria-label="Market brief" style={{...edBox,padding:narrow?"16px":"22px 24px"}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:10,flexWrap:"wrap",marginBottom:14}}>
            <h2 style={{...edH3,fontSize:21,margin:0}}>{active==="MORNING"?"Morning brief":"Closing digest"}
              {dg&&<span style={{fontFamily:SANS,fontSize:12,color:C.textDim,fontWeight:400}}>{fromDate?`${fmtDate(fromDate)} · `:""}{dg.headlineCount?`${dg.headlineCount} headlines`:""}</span>}
            </h2>
            <div role="group" aria-label="Brief" style={{display:"flex",gap:6}}>
              {[["MORNING","Morning"],["CLOSING","Closing"]].map(([t,l])=>(
                <button key={t} onClick={()=>{setKind(t);setShowAllEvents(false);}} aria-pressed={active===t} style={edChip(active===t)}>{l}</button>
              ))}
            </div>
          </div>

          {fromDate&&dg&&<div style={{marginBottom:14}}><DetailNote tone="info">There's no brief yet today, so this is the latest one, from {new Date(fromDate+"T00:00:00").toLocaleDateString("en-IN",{weekday:"long",day:"numeric",month:"short"})}.</DetailNote></div>}

          {!apiLoaded||fallbackState==="loading"?(
            <div aria-busy="true" style={{display:"flex",flexDirection:"column",gap:10}}>
              {[90,70,85,60].map((w,i)=><div key={i} style={{height:14,width:`${w}%`,borderRadius:4,background:C.panel,animation:"pulse 1.4s ease-in-out infinite"}}/>)}
            </div>
          ):!dg?(
            <div style={{padding:"18px 0",color:C.textDim,fontSize:13.5,lineHeight:1.55}}>
              {digests.length
                ?<>The {active==="MORNING"?"morning brief":"closing digest"} isn't out for this day. {active==="CLOSING"?"It publishes at 16:20 IST, after the close.":"It publishes at 05:30 IST."}
                    <div style={{marginTop:10}}><button onClick={()=>setKind(active==="MORNING"?"CLOSING":"MORNING")} style={edBtn()}>Show the {active==="MORNING"?"closing digest":"morning brief"}</button></div></>
                :"No brief has been published in the last week. Briefs publish at 05:30 and 16:20 IST on trading days. If they're missing, check Governance → Data sources."}
            </div>
          ):(
            <div style={{display:"flex",flexDirection:"column",gap:16}}>
              <div style={{display:"flex",gap:8,alignItems:"center"}}><span style={{fontSize:13,color:C.textDim}}>Mood</span><EdTag tone={MOOD_TONE[dg.marketMood]||"mute"}>{prettyState(dg.marketMood||"NEUTRAL")}</EdTag></div>
              {dg.summary&&<p style={{margin:0,fontFamily:SERIF,fontSize:narrow?16.5:18,lineHeight:1.55,whiteSpace:"pre-line"}}>{dg.summary}</p>}
              {dg.niftySignal&&(
                <div style={{padding:"12px 14px",borderRadius:6,background:C.accent+"10",fontSize:14,lineHeight:1.5}}>
                  <b>Nifty signal:</b> {dg.niftySignal}
                </div>
              )}
              {events.length>0&&(
                <div>
                  <h3 style={{...edH3,fontSize:16,marginBottom:4}}>Key events</h3>
                  <ul style={{margin:0,padding:0,listStyle:"none"}}>
                    {shownEvents.map((e,i)=>(
                      <li key={i} style={{display:"grid",gridTemplateColumns:narrow?"minmax(0,1fr)":"84px minmax(0,1fr)",gap:narrow?4:12,padding:"10px 0",borderTop:`1px solid ${C.border}`,fontSize:13.5,lineHeight:1.5}}>
                        <span><EdTag tone={IMPACT_TONE[e.impact]||"mute"}>{prettyState(e.impact||"Neutral")}</EdTag></span>
                        <span>{e.event||e.title||String(e)}</span>
                      </li>
                    ))}
                  </ul>
                  {events.length>5&&<button onClick={()=>setShowAllEvents(s=>!s)} style={{...edBtn("ghost"),color:C.accent,paddingLeft:0}}>{showAllEvents?"Show fewer":`Show all ${events.length} events`}</button>}
                </div>
              )}
            </div>
          )}
        </section>

        <div style={{display:"flex",flexDirection:"column",gap:16}}>
          {dg&&(dg.topThemes||[]).length>0&&(
            <section style={edBox} aria-label="Themes">
              <h3 style={{...edH3,fontSize:17}}>Themes</h3>
              <ul style={{margin:0,padding:"0 0 0 18px",display:"flex",flexDirection:"column",gap:6,fontSize:13.5,lineHeight:1.45}}>
                {dg.topThemes.map((t,i)=><li key={i}>{t}</li>)}
              </ul>
            </section>
          )}
          {dg&&(dg.sectorsInFocus||[]).length>0&&(
            <section style={edBox} aria-label="Sectors in focus">
              <h3 style={{...edH3,fontSize:17}}>Sectors in focus</h3>
              <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
                {dg.sectorsInFocus.map((s,i)=><span key={i} style={{display:"inline-flex",alignItems:"center",minHeight:28,padding:"0 11px",borderRadius:999,background:C.panel,fontSize:12.5}}>{s}</span>)}
              </div>
            </section>
          )}
          <section style={edBox} aria-label="Ask ARIA">
            <h3 style={{...edH3,fontSize:17}}>Ask about the market</h3>
            <p style={{margin:"0 0 12px",fontSize:13,color:C.textDim,lineHeight:1.5}}>ARIA can explain today's moves, the regime, or how a theme affects your holdings.</p>
            <button onClick={onAskAria} style={edBtn()}><Icon name="spark"/>Ask ARIA</button>
          </section>
        </div>
      </div>

      <section aria-label="India market news">
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:10,flexWrap:"wrap",marginBottom:6}}>
          <h2 style={{...edH3,fontSize:21,margin:0}}>India market news</h2>
          <div role="group" aria-label="News topic" style={{display:"flex",gap:6,flexWrap:"wrap"}}>
            {NEWS_TOPICS.map(t=>(
              <button key={t} onClick={()=>pickTopic(t)} aria-pressed={newsTopic===t} style={edChip(newsTopic===t)}>{t}</button>
            ))}
          </div>
        </div>
        {newsState==="loading"?(
          <div aria-busy="true" style={{display:"flex",flexDirection:"column",gap:12,paddingTop:12}}>
            {[92,70,85,60,78].map((w,i)=><div key={i} style={{height:16,width:`${w}%`,borderRadius:4,background:C.panel,animation:"pulse 1.4s ease-in-out infinite"}}/>)}
          </div>
        ):newsState==="error"?(
          <div style={{padding:"20px 0"}}><DetailNote tone="warn">Couldn't load {newsTopic} headlines. Check the backend and try again.</DetailNote>
            <button onClick={()=>{setNewsState("loading");Promise.resolve(onNewsTopic(newsTopic,true)).then(ok=>setNewsState(ok===false?"error":"idle")).catch(()=>setNewsState("error"));}} style={{...edBtn(),marginTop:10}}>Try again</button></div>
        ):shownNews.length===0?(
          <p style={{margin:"16px 0 0",fontSize:13.5,color:C.textDim}}>{IS_LIVE?`No ${newsTopic} headlines in the feed right now.`:"News needs a live backend connection."}</p>
        ):(
          <div style={{display:"flex",flexDirection:"column"}}>
            {shownNews.map((n,i)=>{
              const parts=String(n.title||"").split(" - ");
              const outlet=parts.length>1?parts.pop():null;
              return(
                <a key={i} href={n.url} target="_blank" rel="noreferrer" className="ed-card"
                  style={{display:"flex",flexDirection:"column",gap:4,padding:"14px 6px",borderTop:`1px solid ${C.border}`,color:C.text,textDecoration:"none"}}>
                  <span style={{fontFamily:SERIF,fontSize:narrow?16:18,lineHeight:1.4,fontWeight:500}}>{parts.join(" - ")}</span>
                  <span style={{fontSize:12,color:C.textDim,display:"flex",gap:6,alignItems:"center",flexWrap:"wrap"}}>
                    {outlet||n.source}{n.published?` · ${fmtDate(n.published)}`:""}<Icon name="up" size={13}/>
                    <span style={{position:"absolute",width:1,height:1,overflow:"hidden",clip:"rect(0 0 0 0)"}}>(opens in a new tab)</span>
                  </span>
                </a>
              );
            })}
            {(news||[]).length>shownNews.length&&(
              <button onClick={()=>setNewsLimit(l=>l+8)} style={{...edBtn("ghost"),color:C.accent,borderTop:`1px solid ${C.border}`,borderRadius:0}}>
                Show {Math.min(8,news.length-shownNews.length)} more headlines
              </button>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

// ─── CONFIDENCE CALIBRATION CHART (P6-A) ─────────────────────────────────────
function ConfidenceCalibrationChart({ calibration }) {
  const buckets = calibration?.buckets || [];
  const resolved = calibration?.total_resolved || 0;
  const anyData = buckets.some(b => b.total > 0);

  return (
    <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:14}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:9}}>
        <div>
          <div style={{fontSize:12,fontWeight:700,color:C.accent}}>Confidence Calibration</div>
          <div style={{fontSize:10.5,color:C.muted,marginTop:1}}>
            Does our stated confidence predict actual hit rates?
          </div>
        </div>
        {resolved > 0 && (
          <div style={{fontSize:10.5,color:C.muted,background:C.bg,borderRadius:5,padding:"3px 7px"}}>
            {resolved} resolved
          </div>
        )}
      </div>

      {!anyData ? (
        <div style={{textAlign:"center",padding:"16px 0"}}>
          <div style={{fontSize:12,color:C.textDim}}>Calibration data will appear once</div>
          <div style={{fontSize:11,color:C.muted}}>the first 90-day outcomes are resolved.</div>
          <div style={{fontSize:10.5,color:C.textDim,marginTop:4}}>First results ~90 days after the system starts generating recommendations.</div>
        </div>
      ) : (
        <>
          {/* Bar chart: expected vs actual per tier */}
          <div style={{display:"flex",alignItems:"flex-end",gap:6,height:80,marginBottom:6}}>
            {buckets.filter(b=>b.total>0).map((b,i)=>{
              const actual   = b.hit_rate_pct;
              const expected = b.expected_pct;
              const hActual  = actual   != null ? Math.round(actual   * 0.72) : 0;
              const hExpected= expected != null ? Math.round(expected * 0.72) : 0;
              const diff     = actual != null ? actual - expected : null;
              const color    = diff == null ? C.muted
                             : diff >= -5  ? C.green
                             : diff >= -15 ? C.orange
                             : C.red;
              return (
                <div key={b.label} style={{flex:1,display:"flex",flexDirection:"column",alignItems:"center",gap:1}}>
                  {/* Actual bar */}
                  <div style={{width:"100%",display:"flex",gap:2,alignItems:"flex-end",height:72}}>
                    <div title={`Expected: ${expected}%`} style={{flex:1,height:hExpected,background:`${C.accent}55`,borderRadius:"2px 2px 0 0",minHeight:2}}/>
                    <div title={`Actual: ${actual != null ? actual+"%" : "—"} (${b.hits}/${b.total} hits)`}
                      style={{flex:1,height:hActual,background:color+"cc",borderRadius:"2px 2px 0 0",minHeight:actual!=null?2:0}}/>
                  </div>
                  <div style={{fontSize:10,color:C.muted,whiteSpace:"nowrap"}}>{b.label}</div>
                  {actual != null && (
                    <div style={{fontSize:10,fontWeight:700,color}}>
                      {actual}%
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <div style={{display:"flex",gap:12,marginTop:4}}>
            <div style={{display:"flex",alignItems:"center",gap:4}}>
              <div style={{width:8,height:8,background:C.accent+"55",borderRadius:1}}/>
              <span style={{fontSize:10,color:C.muted}}>Expected (midpoint)</span>
            </div>
            <div style={{display:"flex",alignItems:"center",gap:4}}>
              <div style={{width:8,height:8,background:C.green+"cc",borderRadius:1}}/>
              <span style={{fontSize:10,color:C.muted}}>Actual hit rate</span>
            </div>
            <div style={{fontSize:10,color:C.muted,marginLeft:"auto"}}>
              Well-calibrated: actual ≈ expected per tier
            </div>
          </div>
        </>
      )}
    </div>
  );
}


// ─── TOP / WORST CALLS PANEL (P6-A) ──────────────────────────────────────────
function TopCallsPanel({ outcomes }) {
  const all = Array.isArray(outcomes?.outcomes)
    ? outcomes.outcomes.filter(r => r.alpha_t90 != null && r.outcome_t90 !== "PENDING")
    : [];

  if (all.length === 0) {
    return (
      <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:14}}>
        <div style={{fontSize:12,fontWeight:700,color:C.accent,marginBottom:6}}>Best & Worst Calls</div>
        <div style={{fontSize:11,color:C.muted,textAlign:"center",padding:"12px 0"}}>
          Top & worst performers will appear once 90-day outcomes are resolved.
        </div>
      </div>
    );
  }

  const sorted  = [...all].sort((a,b) => b.alpha_t90 - a.alpha_t90);
  const best5   = sorted.slice(0,5);
  const worst5  = sorted.slice(-5).reverse();

  const Row = ({r, rank, good}) => {
    const pct = r.alpha_t90 != null ? (r.alpha_t90*100).toFixed(1) : null;
    return (
      <div style={{display:"flex",alignItems:"center",gap:6,padding:"4px 0",borderBottom:`1px solid ${C.border}22`}}>
        <span style={{fontSize:10.5,color:C.muted,width:14,textAlign:"right"}}>{rank}</span>
        <span style={{fontSize:11,fontWeight:700,color:C.text,fontFamily:MONO,flex:1,minWidth:0,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{r.symbol}</span>
        <Tag color={r.action==="BUY"?C.green:r.action==="SELL"?C.red:r.action==="HOLD"?C.teal:C.orange}>{r.action}</Tag>
        <span style={{fontSize:10.5,color:C.muted,minWidth:44,textAlign:"right"}}>{(r.rec_date||"").slice(0,10)}</span>
        {pct != null && (
          <span style={{fontSize:11,fontWeight:700,color:good?C.green:C.red,minWidth:44,textAlign:"right"}}>
            {good?"+":""}{pct}%
          </span>
        )}
      </div>
    );
  };

  return (
    <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:14}}>
      <div style={{fontSize:12,fontWeight:700,color:C.accent,marginBottom:10}}>Best & Worst Calls (90-day alpha vs NIFTY)</div>
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16}}>
        <div>
          <div style={{fontSize:10.5,fontWeight:700,color:C.green,marginBottom:5,display:"flex",alignItems:"center",gap:4}}>
            <span>▲</span><span>TOP 5 CALLS</span>
          </div>
          {best5.map((r,i) => <Row key={r.id||i} r={r} rank={i+1} good={true}/>)}
        </div>
        <div>
          <div style={{fontSize:10.5,fontWeight:700,color:C.red,marginBottom:5,display:"flex",alignItems:"center",gap:4}}>
            <span>▼</span><span>WORST 5 CALLS</span>
          </div>
          {worst5.map((r,i) => <Row key={r.id||i} r={r} rank={i+1} good={false}/>)}
        </div>
      </div>
    </div>
  );
}


// ─── BACKTEST RESULTS PANEL (P6-B) ───────────────────────────────────────────
function BacktestPanel({ backtestData, apiLoaded }) {
  const [split, setSplit] = useState("TEST");
  const SPLITS = ["TEST","TRAIN","FULL"];
  const SPLIT_LABEL = { TEST:"Out-of-sample (2023–2024)", TRAIN:"In-sample (2020–2022)", FULL:"Full period (2020–2024)" };

  const rows = Array.isArray(backtestData?.results) ? backtestData.results : [];
  const splitRows = rows.filter(r => r.split_type === split);

  const avgMetric = (key) => {
    const vals = splitRows.map(r => r[key]).filter(v => v != null);
    return vals.length ? (vals.reduce((a,b)=>a+b,0)/vals.length) : null;
  };

  const avgHR    = avgMetric("hit_rate_90d");
  const avgAlpha = avgMetric("avg_alpha_90d");
  const avgSharpe= avgMetric("sharpe_ratio");
  const avgDD    = avgMetric("max_drawdown");

  return (
    <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:14}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:10}}>
        <div>
          <div style={{fontSize:12,fontWeight:700,color:C.accent}}>Walk-Forward Backtest</div>
          <div style={{fontSize:10.5,color:C.muted,marginTop:1}}>
            Monthly runs on NSE universe — signal quality validation
          </div>
        </div>
        {/* Split selector */}
        <div style={{display:"flex",gap:4}}>
          {SPLITS.map(s => (
            <button key={s} onClick={()=>setSplit(s)} style={{
              background: split===s ? C.accent+"33" : "transparent",
              border: `1px solid ${split===s ? C.accent : C.border}`,
              borderRadius:5, padding:"3px 8px", color: split===s ? C.accent : C.muted,
              fontSize:10.5, fontWeight:700, cursor:"pointer"
            }}>{s}</button>
          ))}
        </div>
      </div>

      {/* Split description */}
      <div style={{fontSize:10.5,color:C.textDim,marginBottom:10,padding:"4px 8px",background:C.bg,borderRadius:5}}>
        {SPLIT_LABEL[split]}
        {split==="TEST" && <span style={{color:C.green,marginLeft:6}}>← most meaningful (unseen data)</span>}
      </div>

      {splitRows.length === 0 ? (
        <div style={{textAlign:"center",padding:"20px 0"}}>
          <div style={{fontSize:12,color:C.textDim}}>Backtest runs on the 1st of each month.</div>
          <div style={{fontSize:10.5,color:C.textDim,marginTop:4}}>
            {apiLoaded
              ? "First results available after the next monthly backtest run."
              : "Loading..."}
          </div>
          <div style={{fontSize:10.5,color:C.textDim,marginTop:2}}>
            Manual trigger: <code style={{color:C.accent}}>python -m agents.backtester --dry-run</code>
          </div>
        </div>
      ) : (
        <>
          {/* Summary tiles */}
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(140px,1fr))",gap:8,marginBottom:12}}>
            {[
              { label:"Avg Hit Rate", value: avgHR!=null ? avgHR.toFixed(1)+"%":"-", good: avgHR!=null&&avgHR>=55, icon:"🎯" },
              { label:"Avg Alpha 90d", value: avgAlpha!=null ? (avgAlpha>=0?"+":"")+avgAlpha.toFixed(2)+"%":"-", good: avgAlpha!=null&&avgAlpha>=3, icon:"📈" },
              { label:"Avg alpha ÷ σ (per trade)", value: avgSharpe!=null ? avgSharpe.toFixed(2):"-", good: avgSharpe!=null&&avgSharpe>=1.0, icon:"" },
              { label:"Avg worst single trade", value: avgDD!=null ? (avgDD*100).toFixed(1)+"%":"-", good: avgDD!=null&&avgDD>-0.15, icon:"" },
            ].map(({label,value,good,icon})=>(
              <div key={label} style={{background:C.bg,borderRadius:7,padding:9,textAlign:"center",border:`1px solid ${good?C.green+"44":C.border}`}}>
                <div style={{fontSize:13,fontWeight:800,color:good?C.green:value==="-"?C.muted:C.orange,fontFamily:MONO}}>{value}</div>
                <div style={{fontSize:10,color:C.muted,marginTop:2}}>{label}</div>
              </div>
            ))}
          </div>

          {/* Runs table */}
          <div style={{fontSize:11,fontWeight:700,color:C.muted,marginBottom:6}}>Monthly runs ({splitRows.length})</div>
          <div style={{background:C.bg,borderRadius:6,overflow:"auto",maxHeight:"60vh"}}>
            <div style={{display:"grid",gridTemplateColumns:"80px 100px 60px 70px 70px 60px 70px",gap:0,padding:"5px 10px",borderBottom:`1px solid ${C.border}`,background:C.bg,position:"sticky",top:0,zIndex:1}}>
              {["Run Date","Period","Signals","Hit Rate","Alpha 90d","Alpha÷σ","Worst trade"].map(h=>(
                <span key={h} style={{fontSize:10,color:C.muted,fontWeight:700,textTransform:"uppercase"}}>{h}</span>
              ))}
            </div>
            {splitRows.map((r,i)=>{
              const hrGood = r.hit_rate_90d!=null && r.hit_rate_90d>=55;
              const alphaGood = r.avg_alpha_90d!=null && r.avg_alpha_90d>=3;
              const ddBad = r.max_drawdown!=null && r.max_drawdown<-0.20;
              return (
                <div key={r.run_date||i} style={{display:"grid",gridTemplateColumns:"80px 100px 60px 70px 70px 60px 70px",gap:0,padding:"5px 10px",borderBottom:i<splitRows.length-1?`1px solid ${C.border}22`:"none",background:i%2===0?"transparent":C.surface+"44"}}>
                  <span style={{fontSize:10.5,color:C.text,fontFamily:MONO}}>{(r.run_date||"").slice(0,10)}</span>
                  <span style={{fontSize:10,color:C.muted}}>{(r.period_start||"").slice(0,7)} – {(r.period_end||"").slice(0,7)}</span>
                  <span style={{fontSize:10.5,color:C.muted}}>{r.total_signals??"-"}</span>
                  <span style={{fontSize:11,fontWeight:700,color:hrGood?C.green:r.hit_rate_90d!=null?C.orange:C.muted}}>
                    {r.hit_rate_90d!=null ? r.hit_rate_90d.toFixed(1)+"%" : "—"}
                  </span>
                  <span style={{fontSize:11,fontWeight:700,color:alphaGood?C.green:r.avg_alpha_90d!=null?C.orange:C.muted}}>
                    {r.avg_alpha_90d!=null ? (r.avg_alpha_90d>=0?"+":"")+r.avg_alpha_90d.toFixed(2)+"%" : "—"}
                  </span>
                  <span style={{fontSize:11,fontWeight:700,color:r.sharpe_ratio!=null&&r.sharpe_ratio>=1.0?C.green:r.sharpe_ratio!=null?C.orange:C.muted}}>
                    {r.sharpe_ratio!=null ? r.sharpe_ratio.toFixed(2) : "—"}
                  </span>
                  <span style={{fontSize:11,fontWeight:700,color:ddBad?C.red:C.green}}>
                    {r.max_drawdown!=null ? (r.max_drawdown*100).toFixed(1)+"%" : "—"}
                  </span>
                </div>
              );
            })}
          </div>
          <div style={{marginTop:8,fontSize:10,color:C.muted}}>
            Target: hit rate &gt;55% · avg alpha &gt;+3% · alpha÷σ &gt;1.0 · worst trade above -20%
          </div>
          {/* WP-022: say what this backtest is — and is not */}
          <div style={{marginTop:6,fontSize:10.5,color:C.textDim,lineHeight:1.5}}>
            ⚠ {backtestData?.caveat || "Technical rule only, survivorship-biased universe — not a test of the live system."}
          </div>
        </>
      )}
    </div>
  );
}


// ─── AGENT ATTRIBUTION PANEL (P5-A) ──────────────────────────────────────────
function AgentAttributionPanel({attribution, liveAttribution}){
  // Prefer resolved 90d data; fall back to live attribution if not yet available
  const resolved = attribution?.agents || [];
  const live     = liveAttribution?.agents || [];
  const useLive  = resolved.length === 0 && live.length > 0;
  const agents   = useLive ? live : resolved;

  if(!agents.length) return(
    <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:14,marginBottom:12}}>
      <div style={{fontSize:12,fontWeight:700,color:C.muted,marginBottom:4}}>Agent Attribution</div>
      <div style={{fontSize:11,color:C.textDim}}>
        Attribution data will populate after 16:30 IST today (when the Forward Poller first runs).
        Each agent's current alpha vs NIFTY 50 will appear here daily.
      </div>
    </div>
  );

  const isLiveMode = useLive;
  return(
    <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:12}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:2}}>
        <div style={{fontSize:12,fontWeight:700,color:C.accent}}>Agent Attribution</div>
        {isLiveMode && (
          <span style={{fontSize:10,background:C.teal+"22",color:C.teal,borderRadius:4,padding:"2px 7px",fontWeight:700}}>
            LIVE · updates 16:30 IST daily
          </span>
        )}
        {!isLiveMode && (
          <span style={{fontSize:10,background:C.green+"22",color:C.green,borderRadius:4,padding:"2px 7px",fontWeight:700}}>
            RESOLVED · 90-day outcomes
          </span>
        )}
      </div>
      <div style={{fontSize:10.5,color:C.muted,marginBottom:8}}>
        {isLiveMode ? liveAttribution?.note : attribution?.note}
      </div>
      <div style={{border:`1px solid ${C.border}`,borderRadius:6,overflow:"auto",maxHeight:"60vh"}}>
        <div style={{display:"grid",gridTemplateColumns:"1fr 60px 60px 70px 80px",minWidth:400,background:C.panel,padding:"5px 9px",gap:4,position:"sticky",top:0,zIndex:1}}>
          {["Agent","Signals","Bullish",
            isLiveMode?"Live Alpha":"Hit Rate",
            isLiveMode?"Beat NIFTY%":"Avg Alpha"
          ].map(h=>(
            <div key={h} style={{fontSize:10,color:C.muted,fontWeight:700,textTransform:"uppercase"}}>{h}</div>
          ))}
        </div>
        {agents.map((a,i)=>{
          const hr  = isLiveMode ? null : a.hit_rate_90d;
          const alp = isLiveMode ? (a.avg_bull_alpha_live != null ? a.avg_bull_alpha_live * 100 : null)
                                 : a.avg_alpha_90d;
          const beat = isLiveMode ? a.positive_rate_live : null;
          const hrCol = hr==null?(beat!=null?(beat>=55?C.green:beat>=45?C.orange:C.red):C.muted)
                                :(hr>=55?C.green:hr>=45?C.orange:C.red);
          const alpCol= alp==null?C.muted:alp>=0?C.green:C.red;
          const col1  = isLiveMode ? alpCol : hrCol;
          const col2  = alpCol;
          const val1  = isLiveMode
            ? (alp!=null?`${alp>=0?"+":""}${alp.toFixed(1)}%`:"—")
            : (hr!=null?`${hr.toFixed(0)}%`:"—");
          const val2  = isLiveMode
            ? (beat!=null?`${beat.toFixed(0)}%`:"—")
            : (alp!=null?`${alp>=0?"+":""}${alp.toFixed(1)}%`:"—");
          return(
            <div key={a.agent_name} style={{
              display:"grid",gridTemplateColumns:"1fr 60px 60px 70px 80px",minWidth:400,
              padding:"5px 9px",gap:4,alignItems:"center",
              background:i%2===0?C.bg:C.surface,
              borderTop:`1px solid ${C.border}`,
            }}>
              <div style={{fontSize:11,fontWeight:600,color:C.text,textTransform:"capitalize"}}>
                {a.agent_name.replace(/_/g," ")}
                {i===0&&<span style={{marginLeft:5,fontSize:10,background:C.green+"22",color:C.green,borderRadius:3,padding:"1px 5px"}}>top</span>}
              </div>
              <div style={{fontSize:11,color:C.muted,fontFamily:MONO}}>{a.signal_count}</div>
              <div style={{fontSize:11,color:C.muted,fontFamily:MONO}}>{a.bullish_count}</div>
              <div style={{fontSize:11,fontWeight:700,color:col1,fontFamily:MONO}}>{val1}</div>
              <div style={{fontSize:11,fontWeight:700,color:col2,fontFamily:MONO}}>{val2}</div>
            </div>
          );
        })}
      </div>
      {isLiveMode
        ? <div style={{fontSize:10,color:C.textDim,marginTop:5}}>Live Alpha = avg alpha vs NIFTY 50 since rec date for BULLISH votes. Beat NIFTY% = % of bullish votes currently outperforming.</div>
        : <div style={{fontSize:10,color:C.textDim,marginTop:5}}>Hit rate = % of BULLISH votes where stock beat Nifty 50 at 90 days. Target: ≥55%.</div>
      }
    </div>
  );
}


// ─── LIVE PERFORMANCE PANEL (P5-D) ───────────────────────────────────────────
function LivePerformancePanel({livePerf}){
  if(!livePerf || !livePerf.has_live_data) return(
    <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:14,marginBottom:14}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:4}}>
        <div style={{fontFamily:SERIF,fontSize:18,fontWeight:600,color:C.text}}>Live open positions</div>
        <span style={{fontSize:10,background:C.orange+"22",color:C.orange,borderRadius:4,padding:"2px 7px"}}>awaiting 16:30 IST poller</span>
      </div>
      <div style={{fontSize:11,color:C.textDim}}>
        {(livePerf?.total_open||0) > 0
          ? `${livePerf.total_open} open recommendations tracked — live prices load at 16:30 IST daily.`
          : "No open recommendations being tracked yet. New recommendations are seeded automatically at 06:55 IST."}
      </div>
    </div>
  );

  const open   = livePerf.total_open || 0;
  const avgRet = livePerf.avg_live_return_pct;
  const avgAlp = livePerf.avg_live_alpha_pct;
  const posC   = livePerf.positive_count || 0;
  const byAct  = livePerf.by_action || {};
  const recs   = (livePerf.recs || []).slice(0, 20);

  const retCol  = avgRet == null ? C.muted : avgRet >= 0 ? C.green : C.red;
  const alpCol  = avgAlp == null ? C.muted : avgAlp >= 0 ? C.green : C.red;

  return(
    <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:14}}>
      {/* Header */}
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
        <div>
          <div style={{fontFamily:SERIF,fontSize:18,fontWeight:600,color:C.text}}>Live open positions</div>
          <div style={{fontSize:10.5,color:C.muted,marginTop:1}}>{open} recs tracked · {posC} beating NIFTY · updates 16:30 IST</div>
        </div>
        <div style={{display:"flex",gap:12}}>
          <div style={{textAlign:"right"}}>
            <div style={{fontSize:10,color:C.muted}}>Avg Return</div>
            <div style={{fontSize:14,fontWeight:700,color:retCol}}>{avgRet!=null?`${avgRet>=0?"+":""}${avgRet.toFixed(1)}%`:"—"}</div>
          </div>
          <div style={{textAlign:"right"}}>
            <div style={{fontSize:10,color:C.muted}}>Avg Alpha vs NIFTY</div>
            <div style={{fontSize:14,fontWeight:700,color:alpCol}}>{avgAlp!=null?`${avgAlp>=0?"+":""}${avgAlp.toFixed(1)}%`:"—"}</div>
          </div>
        </div>
      </div>

      {/* By-action summary tiles */}
      {Object.keys(byAct).length > 0 && (
        <div style={{display:"flex",gap:8,marginBottom:12,flexWrap:"wrap"}}>
          {["BUY","HOLD","SELL","AVOID"].filter(a=>byAct[a]).map(action=>{
            const d    = byAct[action];
            const aCol = action==="BUY"?C.green:action==="SELL"?C.red:action==="HOLD"?C.teal:C.orange;
            const alpC = d.avg_alpha_pct==null?C.muted:d.avg_alpha_pct>=0?C.green:C.red;
            return(
              <div key={action} style={{flex:"1 1 120px",background:C.bg,border:`1px solid ${C.border}`,borderRadius:6,padding:"8px 10px"}}>
                <div style={{display:"flex",justifyContent:"space-between",marginBottom:5}}>
                  <Tag color={aCol}>{action}</Tag>
                  <span style={{fontSize:10.5,color:C.muted}}>{d.count}</span>
                </div>
                <div style={{fontSize:12.5,fontWeight:700,color:alpC}}>
                  {d.avg_alpha_pct!=null?`${d.avg_alpha_pct>=0?"+":""}${d.avg_alpha_pct.toFixed(1)}%`:"—"}
                </div>
                <div style={{fontSize:10,color:C.muted}}>alpha vs NIFTY</div>
                {d.has_live_data&&<div style={{fontSize:10,color:C.textDim,marginTop:2}}>{d.positive_count}/{d.count} beating</div>}
              </div>
            );
          })}
        </div>
      )}

      {/* Per-rec table (top 20 by alpha) */}
      {recs.length > 0 && (
        <div>
          <div style={{fontSize:10.5,fontWeight:700,color:C.muted,marginBottom:5,textTransform:"uppercase"}}>Top Open Recs by Live Alpha</div>
          <div style={{border:`1px solid ${C.border}`,borderRadius:6,overflow:"auto",maxHeight:"60vh"}}>
            <div style={{display:"grid",gridTemplateColumns:"150px 100px 72px 72px 96px 56px",gap:10,minWidth:596,padding:"5px 10px",borderBottom:`1px solid ${C.border}`,background:C.bg,position:"sticky",top:0,zIndex:1}}>
              {["Symbol","Action","Return","Alpha","Entry","Days"].map(h=>(
                <span key={h} style={{fontSize:10,color:C.muted,fontWeight:700,textTransform:"uppercase"}}>{h}</span>
              ))}
            </div>
            {recs.map((r,i)=>{
              const ret  = r.return_live != null ? r.return_live * 100 : null;
              const alp  = r.alpha_live  != null ? r.alpha_live  * 100 : null;
              const retC = ret==null?C.muted:ret>=0?C.green:C.red;
              const alpC = alp==null?C.muted:alp>=0?C.green:C.red;
              const aCol = r.action==="BUY"?C.green:r.action==="SELL"?C.red:r.action==="HOLD"?C.teal:C.orange;
              return(
                <div key={r.id||i} style={{
                  display:"grid",gridTemplateColumns:"150px 100px 72px 72px 96px 56px",minWidth:596,
                  gap:10,padding:"5px 10px",
                  borderBottom:i<recs.length-1?`1px solid ${C.border}33`:"none",
                  background:i%2===0?"transparent":C.bg+"44",
                }}>
                  <span style={{fontSize:11,fontWeight:700,color:C.text,fontFamily:MONO}}>{r.symbol}</span>
                  <Tag color={aCol}>{r.action}</Tag>
                  <span style={{fontSize:11,fontWeight:600,color:retC,fontFamily:MONO}}>{ret!=null?`${ret>=0?"+":""}${ret.toFixed(1)}%`:"—"}</span>
                  <span style={{fontSize:11,fontWeight:700,color:alpC,fontFamily:MONO}}>{alp!=null?`${alp>=0?"+":""}${alp.toFixed(1)}%`:"—"}</span>
                  <span style={{fontSize:10.5,color:C.muted,fontFamily:MONO}}>{r.entry_price!=null?`₹${Number(r.entry_price).toLocaleString("en-IN")}`:"—"}</span>
                  <span style={{fontSize:10.5,color:C.muted,fontFamily:MONO}}>{r.days_live!=null?`${r.days_live}d`:"—"}</span>
                </div>
              );
            })}
          </div>
          {livePerf.total_open > 20 && (
            <div style={{fontSize:10,color:C.textDim,marginTop:4}}>Showing top 20 of {livePerf.total_open} open positions.</div>
          )}
        </div>
      )}
      <div style={{fontSize:10,color:C.textDim,marginTop:6}}>
        Alpha = stock return − NIFTY 50 return since recommendation date. Refreshed daily at 16:30 IST.
      </div>
    </div>
  );
}


// ─── PERFORMANCE TAB ─────────────────────────────────────────────────────────
function PerformanceTab({accuracy, outcomes, alphaChart, attribution, livePerf, liveAttribution, calibration, backtestData, apiLoaded}){
  const byAction = accuracy?.by_action || {};
  const total    = accuracy?.total_tracked || 0;
  const ACTIONS  = ["BUY","HOLD","SELL","AVOID"];

  const outcomeColor = o =>
    o==="HIT" ? C.green : o==="MISS" ? C.red : o==="PARTIAL" ? C.orange : C.muted;

  const recent = Array.isArray(outcomes?.outcomes) ? outcomes.outcomes.slice(0,20) : [];

  return(
    <div style={{animation:"fadeUp .3s ease"}}>
      <div style={{marginBottom:14}}>
        <h1 style={{fontFamily:SERIF,fontSize:28,fontWeight:600,color:C.text,letterSpacing:-.3,lineHeight:1.2,margin:0}}>Recommendation Performance</h1>
        <div style={{fontSize:11,color:C.muted,marginTop:1}}>
          Track record — how accurate have our recommendations been vs NIFTY 50?
          {total>0 && <span style={{marginLeft:6,color:C.textDim}}>{total} recommendations tracked</span>}
        </div>
      </div>

      {/* Live Performance Panel — always shown, shows "awaiting poller" until 16:30 IST */}
      <LivePerformancePanel livePerf={livePerf} />

      {IS_LIVE && apiLoaded && !accuracy && !livePerf?.has_live_data && (
        <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:14}}>
          <div style={{fontSize:11,color:C.muted}}>
            <strong style={{color:C.text}}>90-day outcomes</strong> will appear here once the first recommendations are 90+ days old.
            Live alpha data above updates daily at 16:30 IST.
          </div>
        </div>
      )}

      {/* Accuracy Scorecard */}
      {accuracy && total > 0 && (
        <div style={{marginBottom:16}}>
          <div style={{fontSize:12,fontWeight:700,color:C.accent,marginBottom:8}}>Accuracy by Signal Type</div>
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(200px,1fr))",gap:8}}>
            {ACTIONS.map(action=>{
              const d = byAction[action];
              if(!d || d.total_recs===0) return null;
              const hr90   = d.hit_rate_90d;
              const alpha90= d.avg_alpha_90d;
              const good90 = hr90 != null && hr90 >= 55;
              const alphaGood = alpha90 != null && alpha90 >= 3;
              return(
                <div key={action} style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:11}}>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:7}}>
                    <Tag color={action==="BUY"?C.green:action==="SELL"?C.red:action==="HOLD"?C.teal:C.orange}>{action}</Tag>
                    <span style={{fontSize:10.5,color:C.muted}}>{d.total_recs} recs</span>
                  </div>
                  {hr90!=null ? (
                    <>
                      <div style={{display:"flex",justifyContent:"space-between",marginBottom:4}}>
                        <span style={{fontSize:10.5,color:C.muted}}>Hit rate (90d)</span>
                        <span style={{fontSize:12.5,fontWeight:700,color:good90?C.green:C.red}}>{hr90}%</span>
                      </div>
                      <div style={{background:C.border,borderRadius:3,height:3,marginBottom:7}}>
                        <div style={{width:`${Math.min(hr90,100)}%`,height:"100%",borderRadius:3,background:good90?C.green:C.red}}/>
                      </div>
                    </>
                  ) : (
                    <div style={{fontSize:10.5,color:C.muted,marginBottom:7}}>90d not reached yet</div>
                  )}
                  {alpha90!=null && (
                    <div style={{display:"flex",justifyContent:"space-between"}}>
                      <span style={{fontSize:10.5,color:C.muted}}>Avg alpha (90d)</span>
                      <span style={{fontSize:12,fontWeight:600,color:alphaGood?C.green:alpha90>=0?C.accent:C.red}}>
                        {alpha90>=0?"+":""}{alpha90}% vs NIFTY
                      </span>
                    </div>
                  )}
                  {d.resolved_90d>0 && (
                    <div style={{marginTop:5,fontSize:10.5,color:C.muted}}>{d.resolved_90d} resolved at 90d</div>
                  )}
                </div>
              );
            })}
          </div>
          <div style={{marginTop:8,fontSize:10.5,color:C.muted,background:C.surface,borderRadius:5,padding:"4px 8px",display:"inline-block"}}>
            Target: BUY hit rate &gt;55% · avg alpha &gt;+3% at 90 days vs NIFTY 50
          </div>
        </div>
      )}

      {/* Alpha chart (weekly rolling) */}
      {alphaChart && alphaChart.series && alphaChart.series.length>0 && (
        <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:14}}>
          <div style={{fontSize:12,fontWeight:700,color:C.accent,marginBottom:9}}>Rolling Alpha (weekly avg, vs NIFTY 50)</div>
          <div style={{display:"flex",alignItems:"flex-end",gap:3,height:60}}>
            {alphaChart.series.slice(-20).map((pt,i)=>{
              const v   = pt.avg_alpha_pct;
              const pos = v >= 0;
              const h   = Math.min(Math.abs(v)*4, 56);
              return(
                <div key={i} style={{flex:1,display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"flex-end",position:"relative",height:60}}>
                  {pos ? (
                    <div title={`${pt.week}: ${v>=0?"+":""}${v}% (n=${pt.n})`}
                      style={{width:"100%",height:h,background:`${C.green}aa`,borderRadius:"2px 2px 0 0",cursor:"default"}}/>
                  ) : (
                    <div title={`${pt.week}: ${v}% (n=${pt.n})`}
                      style={{width:"100%",height:h,background:`${C.red}aa`,borderRadius:"0 0 2px 2px",position:"absolute",top:0,cursor:"default"}}/>
                  )}
                </div>
              );
            })}
          </div>
          <div style={{display:"flex",justifyContent:"space-between",marginTop:4}}>
            <span style={{fontSize:10,color:C.muted}}>{alphaChart.series.slice(-20)[0]?.week||""}</span>
            <span style={{fontSize:10,color:C.muted}}>{alphaChart.series.slice(-1)[0]?.week||""}</span>
          </div>
          <div style={{display:"flex",gap:10,marginTop:5}}>
            <div style={{display:"flex",alignItems:"center",gap:3}}><div style={{width:8,height:8,background:C.green,borderRadius:1}}/><span style={{fontSize:10,color:C.muted}}>Outperformed NIFTY</span></div>
            <div style={{display:"flex",alignItems:"center",gap:3}}><div style={{width:8,height:8,background:C.red,borderRadius:1}}/><span style={{fontSize:10,color:C.muted}}>Underperformed NIFTY</span></div>
          </div>
        </div>
      )}

      {/* Confidence Calibration + Top/Worst Calls (P6-A) */}
      <ConfidenceCalibrationChart calibration={calibration} />
      <TopCallsPanel outcomes={outcomes} />

      {/* Agent Attribution (P5-A) */}
      <AgentAttributionPanel attribution={attribution} liveAttribution={liveAttribution} />

      {/* Walk-Forward Backtest (P6-B) */}
      <BacktestPanel backtestData={backtestData} apiLoaded={apiLoaded} />

      {/* Recent outcomes table */}
      {recent.length > 0 && (
        <div>
          <div style={{fontSize:12,fontWeight:700,color:C.accent,marginBottom:8}}>Recent Resolved Outcomes (last 20)</div>
          <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,overflow:"auto",maxHeight:"60vh"}}>
            <div style={{display:"grid",gridTemplateColumns:"80px 55px 70px 80px 80px 80px",gap:0,padding:"6px 10px",borderBottom:`1px solid ${C.border}`,background:C.bg,position:"sticky",top:0,zIndex:1}}>
              {["Symbol","Action","Date","t90 Outcome","t90 Alpha","t180 Outcome"].map(h=>(
                <span key={h} style={{fontSize:10,color:C.muted,fontWeight:700,textTransform:"uppercase"}}>{h}</span>
              ))}
            </div>
            {recent.map((r,i)=>(
              <div key={r.id||i} style={{display:"grid",gridTemplateColumns:"80px 55px 70px 80px 80px 80px",gap:0,padding:"6px 10px",borderBottom:i<recent.length-1?`1px solid ${C.border}33`:"none",background:i%2===0?"transparent":C.bg+"44"}}>
                <span style={{fontSize:11,fontWeight:700,color:C.text,fontFamily:MONO}}>{r.symbol}</span>
                <Tag color={r.action==="BUY"?C.green:r.action==="SELL"?C.red:r.action==="HOLD"?C.teal:C.orange}>{r.action}</Tag>
                <span style={{fontSize:10.5,color:C.muted}}>{(r.rec_date||"").slice(0,10)}</span>
                {r.outcome_t90&&r.outcome_t90!=="PENDING" ? (
                  <Tag color={outcomeColor(r.outcome_t90)} small>{r.outcome_t90}</Tag>
                ) : (
                  <span style={{fontSize:10.5,color:C.muted}}>Pending</span>
                )}
                {r.alpha_t90!=null ? (
                  <span style={{fontSize:10.5,fontWeight:600,color:r.alpha_t90>=0?C.green:C.red}}>
                    {r.alpha_t90>=0?"+":""}{(r.alpha_t90*100).toFixed(1)}%
                  </span>
                ) : <span style={{fontSize:10.5,color:C.muted}}>—</span>}
                {r.outcome_t180&&r.outcome_t180!=="PENDING" ? (
                  <Tag color={outcomeColor(r.outcome_t180)} small>{r.outcome_t180}</Tag>
                ) : (
                  <span style={{fontSize:10.5,color:C.muted}}>Pending</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


// ─── MAIN APP ─────────────────────────────────────────────────────────────────
export default function App(){
  const [tab,setTab]=useState("discovery");
  const [selDiscoveryId,setSelDiscoveryId]=useState(null);
  const [selRecId,setSelRecId]=useState(null);
  const [ariaOpen,setAriaOpen]=useState(false);
  const [moreOpen,setMoreOpen]=useState(false);
  const narrow=useNarrow(760);
  const [ariaContext,setAriaContext]=useState(null);
  // When IS_LIVE, start with empty arrays so users see proper empty-state placeholders
  // rather than convincing-looking fabricated data.
  // When NOT IS_LIVE (local dev, no backend), seed from mock constants so the UI
  // is functional out-of-the-box without a running API server.
  const [portfolio,         setPortfolio]         = useState(IS_LIVE ? [] : DEFAULT_PORTFOLIO);
  const [govBanner,setGovBanner]=useState(true);
  const [now,setNow]=useState(new Date());

  // ── Live data state ──────────────────────────────────────────────────────────
  const [discoveryUniverse, setDiscoveryUniverse] = useState(IS_LIVE ? [] : DISCOVERY_UNIVERSE);
  const [discoveryRuns,     setDiscoveryRuns]     = useState([]);
  const [brokenSymbols,     setBrokenSymbols]     = useState([]);
  const [portfolioRecs,     setPortfolioRecs]     = useState(IS_LIVE ? [] : PORTFOLIO_RECOMMENDATIONS);
  const [marketPulse,       setMarketPulse]       = useState(IS_LIVE ? [] : MARKET_PULSE);
  const [govAlerts,         setGovAlerts]         = useState(IS_LIVE ? [] : GOV_ALERTS);
  const [researchFeed,      setResearchFeed]      = useState(IS_LIVE ? [] : AI_RESEARCH_FEED);
  // Market regime
  const [marketRegime,      setMarketRegime]      = useState(null);
  // Performance / outcome tracking
  const [perfAccuracy,      setPerfAccuracy]      = useState(null);
  const [perfOutcomes,      setPerfOutcomes]      = useState(null);
  const [perfAlphaChart,    setPerfAlphaChart]    = useState(null);
  const [portfolioRisk,     setPortfolioRisk]     = useState(null);
  const [optionsSignal,     setOptionsSignal]     = useState(null);
  const [valuationCache,    setValuationCache]    = useState({});   // keyed by symbol
  const [systemHealth,      setSystemHealth]      = useState(null); // /api/system/health
  const [dataQualityHistory,setDataQualityHistory]= useState(null); // /api/governance/data-quality
  const [marketNews,        setMarketNews]        = useState([]);   // DB-7: India market news
  const [newsSymbol,        setNewsSymbol]        = useState("NIFTY"); // DB-7: which symbol's news to show
  // P5-B: Paper portfolio simulation
  const [paperPortfolio,    setPaperPortfolio]    = useState(null);
  const [paperHistory,      setPaperHistory]      = useState(null);
  // P5-A: Agent attribution (resolved 90d)
  const [agentAttribution,  setAgentAttribution]  = useState(null);
  // P5-D/E: Live performance + live attribution
  const [livePerf,          setLivePerf]          = useState(null);
  const [liveAttribution,   setLiveAttribution]   = useState(null);
  // P6-A: Confidence calibration + P6-B: Backtest results
  const [calibrationData,   setCalibrationData]   = useState(null);
  const [backtestData,      setBacktestData]      = useState(null);
  // P6-C: Daily market digest (Morning Brief + Closing Digest)
  const [marketDigest,      setMarketDigest]      = useState(null);
  // apiLoaded: false until the initial Promise.allSettled() round-trip completes.
  // Used to distinguish "loading" from "loaded + empty".
  const [apiLoaded,         setApiLoaded]         = useState(!IS_LIVE);
  const wsRef = useRef(null);

  useEffect(()=>{const t=setInterval(()=>setNow(new Date()),30000);return()=>clearInterval(t);},[]);

  // ── API data loading + WebSocket ──────────────────────────────────────────────
  useEffect(()=>{
    if(!API_URL) return; // no backend set — keep mock data for local dev

    // Initial parallel load.
    // NOTE: always set state regardless of whether the API returned rows.
    //       The old `if(d&&d.length)` guard kept mock data visible when the API
    //       was configured but had no rows yet — replaced with proper empty states.
    Promise.allSettled([
      apiFetch("/api/discovery")
        .then(d=>{ const arr=Array.isArray(d)?d:[]; setDiscoveryUniverse(arr); if(arr[0]?.id) setSelDiscoveryId(arr[0].id); })
        .catch(()=>{}),
      apiFetch("/api/discovery/runs")
        .then(d=>{ if(Array.isArray(d)) setDiscoveryRuns(d); })
        .catch(()=>{}),
      apiFetch("/api/recommendations")
        .then(d=>{ const arr=Array.isArray(d)?d:[]; setPortfolioRecs(arr); if(arr[0]?.id) setSelRecId(arr[0].id); })
        .catch(()=>{}),
      apiFetch("/api/portfolio")
        .then(d=>{ if(Array.isArray(d)) setPortfolio(d); })
        .catch(()=>{}),
      apiFetch("/api/market/pulse")
        .then(d=>{ if(Array.isArray(d)&&d.length) setMarketPulse(d); })
        .catch(()=>{}),
      apiFetch("/api/governance/alerts")
        .then(d=>{ if(Array.isArray(d)) setGovAlerts(d); })
        .catch(()=>{}),
      apiFetch("/api/governance/research")
        .then(d=>{ const arr=d?.proposals||d; if(Array.isArray(arr)) setResearchFeed(arr); })
        .catch(()=>{}),
      apiFetch("/api/portfolio/broken")
        .then(d=>{ if(d?.broken && Array.isArray(d.broken)) setBrokenSymbols(d.broken); })
        .catch(()=>{}),
      apiFetch("/api/market/regime?days=1")
        .then(d=>{ if(d?.current) setMarketRegime(d.current); })
        .catch(()=>{}),
      apiFetch("/api/performance/accuracy")
        .then(d=>{ if(d?.by_action) setPerfAccuracy(d); })
        .catch(()=>{}),
      apiFetch("/api/performance/outcomes?days=180")
        .then(d=>{ if(d) setPerfOutcomes(d); })
        .catch(()=>{}),
      apiFetch("/api/performance/alpha_chart?weeks=26")
        .then(d=>{ if(d?.series) setPerfAlphaChart(d); })
        .catch(()=>{}),
      apiFetch("/api/portfolio/risk")
        .then(d=>{ if(d&&!d.error) setPortfolioRisk(d); })
        .catch(()=>{}),
      apiFetch("/api/options/NIFTY")
        .then(d=>{ if(d&&d.signal&&d.signal!=="NO_DATA") setOptionsSignal(d); })
        .catch(()=>{}),
      apiFetch("/api/system/health")
        .then(d=>{ if(d?.checks) setSystemHealth(d); })
        .catch(()=>{}),
      apiFetch("/api/news/NIFTY")   // DB-7: pre-fetch market news
        .then(d=>{ if(d?.news) setMarketNews(d.news); })
        .catch(()=>{}),
      apiFetch("/api/paper/portfolio")    // P5-B: paper portfolio
        .then(d=>{ if(d) setPaperPortfolio(d); })
        .catch(()=>{}),
      apiFetch("/api/paper/history?days=180")  // P5-B: paper P&L history
        .then(d=>{ if(d) setPaperHistory(d); })
        .catch(()=>{}),
      apiFetch("/api/attribution/agents")  // P5-A: agent attribution (resolved 90d)
        .then(d=>{ if(d) setAgentAttribution(d); })
        .catch(()=>{}),
      apiFetch("/api/performance/live")    // P5-D/E: live snapshot
        .then(d=>{ if(d) setLivePerf(d); })
        .catch(()=>{}),
      apiFetch("/api/attribution/live")    // P5-E: live agent attribution
        .then(d=>{ if(d) setLiveAttribution(d); })
        .catch(()=>{}),
      apiFetch("/api/performance/calibration")  // P6-A: confidence calibration
        .then(d=>{ if(d) setCalibrationData(d); })
        .catch(()=>{}),
      apiFetch("/api/backtest/summary?split=ALL&limit=12")  // P6-B: all splits — TRAIN/FULL toggles were empty when only TEST was loaded
        .then(d=>{ if(d) setBacktestData(d); })
        .catch(()=>{}),
      apiFetch("/api/market/digest")  // P6-C: Morning Brief + Closing Digest
        .then(d=>{ if(d) setMarketDigest(d); })
        .catch(()=>{}),
      apiFetch("/api/governance/data-quality?days=14")  // P7-F: data quality history
        .then(d=>{ if(d?.days) setDataQualityHistory(d); })
        .catch(()=>{}),
    ]).then(()=>setApiLoaded(true));

    // Refresh market pulse every 60 s
    const pulseTimer = setInterval(()=>{
      apiFetch("/api/market/pulse").then(d=>{ if(d&&d.length) setMarketPulse(d); }).catch(()=>{});
    }, 60000);

    // WebSocket for real-time critical-danger alerts
    try{
      const wsProto = API_URL.startsWith("https") ? "wss" : "ws";
      const wsBase  = API_URL.replace(/^https?/, wsProto);
      const wsUrl   = `${wsBase}/ws/alerts${API_KEY ? `?api_key=${API_KEY}` : ""}`;
      const ws      = new WebSocket(wsUrl);
      wsRef.current = ws;
      ws.onmessage  = evt => {
        try{
          const msg = JSON.parse(evt.data);
          if(msg.type === "critical_alert" && msg.alerts){
            setGovAlerts(prev=>{
              const newOnes = msg.alerts.filter(a => !prev.find(p => p.id === a.id));
              return newOnes.length ? [...newOnes, ...prev] : prev;
            });
          }
        }catch(_){}
      };
      ws.onerror = ()=>{};
    }catch(_){}

    return()=>{ clearInterval(pulseTimer); wsRef.current?.close(); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[]);

  const openAlerts=(govAlerts||[]).filter(a=>!a.resolved).length;
  const portfolioAlerts=computePortfolioAlerts(portfolio||[]);
  const discoveryStock=(discoveryUniverse||[]).find(s=>s.id===selDiscoveryId);

  const openARIA=useCallback((type,extra=null)=>{
    if(type==="discovery"){setAriaContext({type:"discovery",id:selDiscoveryId});setSelDiscoveryId(selDiscoveryId);}
    else if(type==="research_paper"||type==="research_debate"||type==="research_approved"){setAriaContext({type,id:extra?.id,paper:extra});}
    else if(type==="holding"){setAriaContext({type:"holding",id:extra?.id,holding:extra});}
    else if(type==="portfolio"){setAriaContext({type:"portfolio",id:"portfolio",alert:extra});}
    setAriaOpen(true);
  },[selDiscoveryId]);

  // DB-9: "What ran today?" — fetch latest discovery run and open ARIA with context
  const openARIAWithRun=useCallback(()=>{
    const latest=discoveryRuns&&discoveryRuns[0];
    if(latest){
      setAriaContext({type:"daily_run",run:latest});
      setAriaOpen(true);
    }else if(API_URL){
      apiFetch("/api/discovery/runs?days=1")
        .then(d=>{
          const run=Array.isArray(d)&&d[0];
          setAriaContext({type:"daily_run",run:run||null});
          setAriaOpen(true);
        })
        .catch(()=>{setAriaContext({type:"daily_run",run:null});setAriaOpen(true);});
    }else{
      setAriaContext({type:"daily_run",run:null});
      setAriaOpen(true);
    }
  },[discoveryRuns]);

  const handlePortfolioUpdate=useCallback(action=>{
    if(action.action==="add"){
      const tempId="p"+Date.now();
      const newHolding={
        id:tempId, symbol:action.symbol, name:action.name||action.symbol,
        sector:action.sector||"—", qty:Number(action.qty)||1,
        avgBuy:Number(action.avgBuy),
        currentPrice:Number(action.avgBuy), // temporary until API returns live price
        buyDate:new Date().toISOString().slice(0,10),
        linkedRecId:action.linkedRecId||null,
        notes:action.notes||"Added via ARIA",
        targetPrice:Number(action.targetPrice)||Number(action.avgBuy)*1.25,
        stoplossPrice:Number(action.stoplossPrice)||Number(action.avgBuy)*0.88,
        status:"holding",
        dangerDropPct:0, dangerConfidence:0, dangerTrigger:null, dangerWindow:null, dangerSources:[],
        _saving:true,
      };
      setPortfolio(p=>{
        if(p.find(h=>h.symbol===action.symbol&&h.status==="holding"))return p;
        return [...p, newHolding];
      });
      // Persist to backend — replace optimistic row with real saved row (gets real id + live price)
      if(API_URL){
        apiFetch("/api/portfolio",{
          method:"POST",
          body:JSON.stringify({
            symbol:        newHolding.symbol,
            name:          newHolding.name,
            sector:        newHolding.sector,
            qty:           newHolding.qty,
            avg_buy:       newHolding.avgBuy,
            target_price:  newHolding.targetPrice,
            stoploss_price:newHolding.stoplossPrice,
            notes:         newHolding.notes,
            linked_rec_id: newHolding.linkedRecId,
          }),
        }).then(saved=>{
          // Replace temp optimistic row with the real persisted row from Supabase
          setPortfolio(p=>p.map(h=>h.id===tempId ? {...saved, _saving:false} : h));
        }).catch(err=>{
          // Mark the row as failed so the user can see it didn't save
          console.error("Portfolio save failed:", err);
          setPortfolio(p=>p.map(h=>h.id===tempId
            ? {...h, _saving:false, _error:true, notes:"Not saved. Check the connection and try again."}
            : h));
        });
      }
    } else if(action.action==="exit"){
      const soldQty   = action.qty ? Number(action.qty) : null;
      const exitPrice = Number(action.exitPrice) || null;
      // Find the live holding to determine partial vs full exit
      setPortfolio(prev=>{
        const holding = prev.find(h=>h.symbol===action.symbol&&h.status==="holding");
        const isPartial = holding && soldQty && soldQty < holding.qty;
        if(isPartial){
          // PARTIAL SELL — reduce qty, keep holding status
          const newQty = holding.qty - soldQty;
          const note   = action.notes||`Partial sell: ${soldQty} shares at ₹${exitPrice||"?"}`;
          if(API_URL){
            apiFetch("/api/portfolio",{
              method:"POST",
              body:JSON.stringify({
                symbol:   action.symbol,
                qty:      newQty,
                notes:    note,
                // status omitted → backend keeps OPEN
              }),
            }).then(()=>{
              setPortfolio(p=>p.map(h=>h.symbol===action.symbol&&h.status==="holding"
                ?{...h,_saving:false}:h));
            }).catch(err=>{
              console.error("Partial sell save failed:",err);
              setPortfolio(p=>p.map(h=>h.symbol===action.symbol&&h.status==="holding"
                ?{...h,_saving:false,_error:true}:h));
            });
          }
          return prev.map(h=>h.symbol===action.symbol&&h.status==="holding"
            ?{...h,qty:newQty,notes:note,_saving:!!API_URL}:h);
        } else {
          // FULL EXIT — close position
          const note = action.notes||"Exited via ARIA";
          if(API_URL){
            apiFetch("/api/portfolio",{
              method:"POST",
              body:JSON.stringify({
                symbol:        action.symbol,
                status:        "CLOSED",
                current_price: exitPrice||undefined,
                notes:         note,
              }),
            }).then(()=>{
              setPortfolio(p=>p.map(h=>h.symbol===action.symbol&&h.status==="exited"
                ?{...h,_saving:false}:h));
            }).catch(err=>{
              console.error("Portfolio exit save failed:",err);
              setPortfolio(p=>p.map(h=>h.symbol===action.symbol&&h.status==="exited"
                ?{...h,_saving:false,_error:true,notes:"Exit not saved. Check the connection and try again."}:h));
            });
          }
          return prev.map(h=>h.symbol===action.symbol&&h.status==="holding"
            ?{...h,status:"exited",currentPrice:exitPrice||h.currentPrice,notes:note,_saving:!!API_URL}:h);
        }
      });
    }
  },[]);

  // Add-holding form on the Portfolio tab. A symbol you already hold is averaged
  // in (qty + weighted avg); current_price is sent so the backend doesn't reset
  // it to the new average until the next price refresh.
  const addOrAverageHolding=useCallback(({symbol,qty,avgBuy})=>{
    const ex=portfolio.find(h=>h.status==="holding"&&String(h.symbol).toUpperCase()===symbol);
    if(!ex){ handlePortfolioUpdate({action:"add",symbol,qty,avgBuy,notes:"Added from the Portfolio tab"}); return "added"; }
    const newQty=ex.qty+qty, newAvg=(ex.qty*ex.avgBuy+qty*avgBuy)/newQty;
    setPortfolio(p=>p.map(h=>h.id===ex.id?{...h,qty:newQty,avgBuy:newAvg,_saving:!!API_URL,_error:false}:h));
    if(API_URL){
      apiFetch("/api/portfolio",{method:"POST",body:JSON.stringify({symbol:ex.symbol,qty:newQty,avg_buy:newAvg,current_price:ex.currentPrice||undefined})})
        .then(()=>setPortfolio(p=>p.map(h=>h.id===ex.id?{...h,_saving:false}:h)))
        .catch(err=>{ console.error("Average-in save failed:",err); setPortfolio(p=>p.map(h=>h.id===ex.id?{...h,_saving:false,_error:true}:h)); });
    }
    return "averaged";
  },[portfolio,handlePortfolioUpdate]);

  const TABS=[
    {id:"discovery",icon:"🔍",label:"Discovery",badge:(discoveryUniverse||[]).length,badgeColor:C.cyan},
    {id:"recommendations",icon:"🎯",label:"Portfolio Recs"},
    {id:"portfolio",icon:"💼",label:"Portfolio",badge:portfolioAlerts.length,badgeColor:C.orange},
    {id:"performance",icon:"📊",label:"Performance"},
    {id:"market",icon:"📡",label:"Market"},
    {id:"governance_research",icon:"🧬",label:"Gov Research",badge:(researchFeed||[]).filter(r=>r.debateStatus==="pending").length,badgeColor:C.purple},
    {id:"governance",icon:"🛡",label:"Governance",badge:openAlerts,badgeColor:C.red},
  ];

  const PRIMARY_TABS=[["discovery","Discover","search"],["recommendations","Recs","target"],["portfolio","Portfolio","briefcase"],["market","Market","activity"]];
  const moreActive=!PRIMARY_TABS.some(([id])=>id===tab);
  const moreBadge=TABS.filter(t=>!PRIMARY_TABS.some(([id])=>id===t.id)).reduce((a,t)=>a+(t.badge||0),0);
  const goTab=id=>{setTab(id);setMoreOpen(false);};
  const tickerItems=marketPulse.slice(0,6).map(m=>{
    // Same flat rule as the Market tab cards: a 0.00% / "flat" move is neutral, not red.
    const flat=!m.change||/^[+-]?0(\.0+)?%$/.test(m.change)||/flat|unch/i.test(m.change);
    return(
      <div key={m.key} title={m.note||undefined} style={{display:"flex",gap:4,alignItems:"center",whiteSpace:"nowrap",flexShrink:0}}>
        <span style={{fontSize:10.5,color:C.muted}}>{m.key}</span>
        <span style={{fontSize:11,fontWeight:700,color:C.text,fontFamily:MONO}}>{m.value}</span>
        <span style={{fontSize:10.5,color:flat?C.textDim:m.up?C.green:C.red}}>{m.change}</span>
      </div>
    );
  });

  return(
    <div style={{fontFamily:SANS,background:C.bg,color:C.text,height:"100vh",display:"flex",flexDirection:"column",overflow:"hidden",position:"relative"}}>
      <style>{FONT_STYLE}</style>

      {/* TOP BAR */}
      <div style={{background:C.bg,borderBottom:`1px solid ${C.text}`,padding:narrow?"0 14px":"0 20px",display:"flex",alignItems:"center",gap:narrow?10:18,height:narrow?54:60,flexShrink:0}}>
        <div style={{display:"flex",alignItems:"baseline",gap:8,flexShrink:0,minWidth:0}}>
          <span style={{fontFamily:SERIF,fontSize:narrow?20:23,fontWeight:600,color:C.text,letterSpacing:-.3,whiteSpace:"nowrap"}}>Bharat Intelligence</span>
          {!narrow&&<span style={{fontSize:11,color:C.muted}}>v3.0</span>}
        </div>
        {narrow?<div style={{flex:1}}/>:<div className="ticker-scroll" style={{flex:1,display:"flex",gap:12,overflowX:"auto",msOverflowStyle:"none",scrollbarWidth:"none",minWidth:0}}>
          {tickerItems}
          {marketRegime&&(()=>{
            const regimeColor={BULL:C.green,BEAR:C.red,HIGH_VOLATILITY:C.orange,SIDEWAYS:C.muted}[marketRegime.regime]||C.muted;
                        const tip=[
              `NIFTY: ${marketRegime.nifty_trend}`,
              `VIX: ${marketRegime.vix_state}`,
              `FII: ${marketRegime.fii_trend}`,
              `Breadth: ${marketRegime.breadth_state}`,
              `Momentum: ${marketRegime.momentum_state}`,
              `Confidence: ${marketRegime.confidence}%`,
            ].join(" | ");
            return(
              <div title={tip} style={{display:"flex",gap:3,alignItems:"center",whiteSpace:"nowrap",cursor:"help",borderLeft:`1px solid ${C.border}`,paddingLeft:10,flexShrink:0}}>
                                <span style={{fontSize:11,fontWeight:700,color:regimeColor}}>{marketRegime.regime}</span>
                <span style={{fontSize:10,color:C.muted}}>{marketRegime.confidence}%</span>
              </div>
            );
          })()}
        </div>}
        <div style={{display:"flex",alignItems:"center",gap:7,flexShrink:0}}>
          {!narrow&&openAlerts>0&&<button onClick={()=>setTab("governance")} aria-label={`${openAlerts} governance alerts`} style={{background:C.red+"12",border:`1px solid ${C.red}44`,borderRadius:4,padding:narrow?"8px 8px":"2px 7px",color:C.red,fontSize:11,fontWeight:700,cursor:"pointer"}}>⚠ {openAlerts}</button>}
          {!narrow&&portfolioAlerts.length>0&&<button onClick={()=>setTab("portfolio")} aria-label={`${portfolioAlerts.length} portfolio alerts`} style={{background:C.orange+"12",border:`1px solid ${C.orange}44`,borderRadius:4,padding:narrow?"8px 8px":"2px 7px",color:C.orange,fontSize:11,fontWeight:700,cursor:"pointer"}}>💼 {portfolioAlerts.length}</button>}
          {!narrow&&<div style={{fontSize:10.5,color:C.green,display:"flex",alignItems:"center",gap:2}}><Dot color={C.green} pulse/>Open</div>}
          {!narrow&&<span style={{fontSize:10.5,color:C.muted}}>{now.toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit"})} IST</span>}
          <button onClick={()=>setAriaOpen(o=>!o)} aria-pressed={ariaOpen} style={{background:ariaOpen?C.accentDim:C.accent,border:`1px solid ${C.accent}`,borderRadius:6,padding:narrow?"8px 12px":"7px 14px",minHeight:narrow?40:undefined,color:C.onFill,fontSize:12,fontWeight:700,cursor:"pointer",boxShadow:ariaOpen?`0 0 12px ${C.purple}44`:"none"}}>{ariaOpen?"✕ ARIA":"✦ ARIA"}</button>
        </div>
      </div>

      {narrow&&marketPulse.length>0&&(
        <div className="ticker-scroll" aria-label="Market ticker" style={{display:"flex",gap:16,overflowX:"auto",padding:"7px 14px",borderBottom:`1px solid ${C.borderHi}`,flexShrink:0,scrollbarWidth:"none"}}>
          {tickerItems}
        </div>
      )}

      {govBanner&&openAlerts>0&&(
        <div style={{background:C.red+"0d",borderBottom:`1px solid ${C.red}33`,padding:"3px 15px",display:"flex",alignItems:"center",gap:8,flexShrink:0}}>
          <span style={{fontSize:11,color:C.red,fontWeight:700}}>⚠ Governance:</span>
          <span style={{fontSize:11,color:C.textDim,flex:1}}>{govAlerts.find(a=>!a.resolved)?.title}</span>
          <button onClick={()=>setTab("governance")} style={{background:C.red+"18",border:`1px solid ${C.red}44`,borderRadius:3,padding:"1px 6px",color:C.red,fontSize:10.5,cursor:"pointer"}}>View</button>
          <button onClick={()=>setGovBanner(false)} style={{background:"none",border:"none",color:C.muted,cursor:"pointer",fontSize:11}}>✕</button>
        </div>
      )}

      <div style={{flex:1,display:"flex",overflow:"hidden"}}>
        <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
          {/* TABS */}
          {!narrow&&<div className="tabs-scroll" style={{
            padding:"0 12px",display:"flex",gap:4,
            background:C.bg,borderBottom:`1px solid ${C.borderHi}`,
            flexShrink:0,overflowX:"auto",overflowY:"visible",
          }}>
            {TABS.map(t=>(
              <button key={t.id} onClick={()=>setTab(t.id)} style={{
                background:"transparent",border:"none",
                borderBottom:`2px solid ${tab===t.id?C.accent:"transparent"}`,
                borderRadius:0,padding:"11px 8px 9px",margin:"0 4px -1px",
                color:tab===t.id?C.text:C.textDim,
                fontSize:14,fontWeight:tab===t.id?600:500,cursor:"pointer",
                flexShrink:0,whiteSpace:"nowrap",
              }}>
                {t.label}
                {t.badge>0&&<span style={{marginLeft:6,background:t.id==="discovery"?C.panel:C.red+"14",color:t.id==="discovery"?C.text:C.red,borderRadius:9,padding:"1px 7px",fontSize:11.5,fontWeight:600,fontFamily:MONO}}>{t.badge}</span>}
              </button>
            ))}
          </div>}

          <div style={{flex:1,overflowY:"auto",padding:narrow?"16px 14px 24px":"22px 24px"}}>

            {/* DISCOVERY TAB — THE PRIMARY TAB */}
            {tab==="discovery"&&(
              <ResearchDiscoveryTab
                portfolio={portfolio}
                discoveryUniverse={discoveryUniverse}
                discoveryRuns={discoveryRuns}
                apiLoaded={apiLoaded}
                valuationCache={valuationCache}
                setValuationCache={setValuationCache}
                onAddToPortfolio={(stock)=>{
                  setAriaContext({type:"discovery",id:stock.id});
                  setSelDiscoveryId(stock.id);
                  setAriaOpen(true);
                }}
                onAskAbout={(stock)=>{
                  setAriaContext({type:"discovery",id:stock.id});
                  setSelDiscoveryId(stock.id);
                  setAriaOpen(true);
                }}
                onOpenARIA={(type,extra)=>openARIA(type,extra)}
                onOpenRunSummary={IS_LIVE?openARIAWithRun:null}
              />
            )}

            {/* PORTFOLIO RECOMMENDATIONS */}
            {tab==="recommendations"&&(
              <RecsTab
                recs={portfolioRecs}
                portfolio={portfolio}
                apiLoaded={apiLoaded}
                selRecId={selRecId}
                setSelRecId={setSelRecId}
                valuationCache={valuationCache}
                setValuationCache={setValuationCache}
                onAsk={(r,holding)=>{
                  if(holding){ openARIA("holding",holding); return; }
                  if(r.isDiscovery&&(discoveryUniverse||[]).some(s=>s.id===r.id)){
                    setAriaContext({type:"discovery",id:r.id}); setSelDiscoveryId(r.id); setAriaOpen(true); return;
                  }
                  setAriaContext(null); setAriaOpen(true);
                }}
                onAdd={(r)=>{
                  if(r.isDiscovery&&(discoveryUniverse||[]).some(s=>s.id===r.id)){
                    setAriaContext({type:"discovery",id:r.id}); setSelDiscoveryId(r.id);
                  } else setAriaContext(null);
                  setAriaOpen(true);
                }}
                onGoPortfolio={()=>setTab("portfolio")}
              />
            )}

            {tab==="portfolio"&&<PortfolioTab
              portfolio={portfolio}
              onOpenARIA={openARIA}
              onAddHolding={addOrAverageHolding}
              recs={portfolioRecs}
              onViewRec={rec=>{ if(!rec) return; setSelRecId(rec.id); setTab("recommendations"); }}
              brokenSymbols={brokenSymbols}
              portfolioRisk={portfolioRisk}
              optionsSignal={optionsSignal}
              paperPortfolio={paperPortfolio}
              paperHistory={paperHistory}
              apiLoaded={apiLoaded}
              onFixBroken={()=>{
                // Re-fetch portfolio prices and broken-symbols list after a fix
                apiFetch("/api/portfolio").then(d=>{ if(Array.isArray(d)) setPortfolio(d); }).catch(()=>{});
                apiFetch("/api/portfolio/broken").then(d=>{ if(d?.broken) setBrokenSymbols(d.broken); }).catch(()=>{});
              }}
            />}

            {tab==="performance"&&(
              <PerformanceTab
                accuracy={perfAccuracy}
                outcomes={perfOutcomes}
                alphaChart={perfAlphaChart}
                attribution={agentAttribution}
                livePerf={livePerf}
                liveAttribution={liveAttribution}
                calibration={calibrationData}
                backtestData={backtestData}
                apiLoaded={apiLoaded}
              />
            )}

            {tab==="market"&&(
              <MarketTab
                pulse={marketPulse}
                regime={marketRegime}
                digestData={marketDigest}
                apiLoaded={apiLoaded}
                news={marketNews}
                newsTopic={newsSymbol}
                onNewsTopic={t=>{
                  setNewsSymbol(t);
                  return apiFetch(`/api/news/${t}`)
                    .then(d=>{ setMarketNews(Array.isArray(d?.news)?d.news:[]); return true; })
                    .catch(()=>false);
                }}
                onWhatRan={openARIAWithRun}
                onAskAria={()=>{ setAriaContext(null); setAriaOpen(true); }}
              />
            )}

            {tab==="governance_research"&&<GovernanceResearchTab onOpenARIA={openARIA} researchFeed={researchFeed} apiLoaded={apiLoaded} liveAttribution={liveAttribution}/>}

            {tab==="governance"&&(
              <div style={{animation:"fadeUp .3s ease"}}>
                <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:14}}>
                  <GovShield size={18}/><h1 style={{fontFamily:SERIF,fontSize:28,fontWeight:600,color:C.text,letterSpacing:-.3,lineHeight:1.2,margin:0}}>Governance Engine</h1>
                  <div style={{marginLeft:"auto",background:C.green+"12",border:`1px solid ${C.green}33`,borderRadius:5,padding:"3px 8px",display:"flex",alignItems:"center",gap:4}}><Dot color={C.green} pulse/><span style={{fontSize:11,color:C.green,fontWeight:600}}>Running</span></div>
                </div>

                {/* Data source health — shown first so issues requiring manual action are obvious */}
                <DataSourceQualityPanel data={dataQualityHistory}/>
                <SystemHealthPanel health={systemHealth}/>

                {govAlerts.length>0 ? (
                  <div style={{display:"flex",flexDirection:"column",gap:7,marginBottom:14}}>
                    {govAlerts.map(a=>{const sc={critical:C.red,warning:C.accent,info:C.blue}[a.severity]||C.muted;return(
                      <div key={a.id} style={{background:C.surface,border:`1px solid ${sc}33`,borderRadius:7,padding:11}}>
                        <div style={{display:"flex",justifyContent:"space-between",marginBottom:5}}><div style={{display:"flex",gap:5,alignItems:"center"}}><Tag color={sc}>{(a.severity||"info").toUpperCase()}</Tag><span style={{fontSize:11,color:C.muted}}>{a.module} · {a.time}</span></div><Tag color={a.resolved?C.green:C.red}>{a.resolved?"RESOLVED":"OPEN"}</Tag></div>
                        <div style={{fontSize:12.5,fontWeight:600,color:C.text,marginBottom:3}}>{a.title}</div>
                        <div style={{fontSize:11,color:C.textDim,lineHeight:1.6,marginBottom:4}}>{a.detail}</div>
                        {a.action&&<div style={{fontSize:10.5,color:sc,background:sc+"0f",borderRadius:3,padding:"2px 6px"}}>{a.action}</div>}
                      </div>
                    );})}
                  </div>
                ) : (
                  <div style={{background:C.green+"0a",border:`1px solid ${C.green}22`,borderRadius:7,padding:"10px 14px",marginBottom:14,display:"flex",alignItems:"center",gap:8}}>
                    <Dot color={C.green}/>
                    <span style={{fontSize:13,color:C.green}}>All systems normal. No governance alerts.</span>
                  </div>
                )}
                <div style={{fontSize:12.5,fontWeight:700,color:C.accent,marginBottom:8}}>Enhancement Proposals</div>
                <EmptyState icon="💡" title="No enhancement proposals yet"
                  sub="Enhancement proposals are surfaced by the governance layer after reviewing agent performance trends. They will appear here once the scheduler has run for several days." />
              </div>
            )}
          </div>
        </div>

        {ariaOpen&&(
          <div role={narrow?"dialog":undefined} aria-label={narrow?"ARIA assistant":undefined} style={narrow
            ?{position:"absolute",inset:0,zIndex:60,background:C.bg,display:"flex",flexDirection:"column"}
            :{width:340,borderLeft:`1px solid ${C.border}`,flexShrink:0,display:"flex",flexDirection:"column",animation:"slideIn .2s ease"}}>
            {narrow&&(
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"6px 8px 6px 16px",borderBottom:`1px solid ${C.border}`,flexShrink:0}}>
                <span style={{fontFamily:SERIF,fontSize:18,fontWeight:600}}>ARIA</span>
                <button onClick={()=>setAriaOpen(false)} style={edBtn("ghost")}><Icon name="close"/>Close</button>
              </div>
            )}
            <ARIAPanel
              selectedRec={(portfolioRecs||[]).find(r=>r.id===selRecId)}
              ariaContext={ariaContext}
              onClearContext={()=>setAriaContext(null)}
              portfolio={portfolio||[]}
              onPortfolioUpdate={handlePortfolioUpdate}
              discoveryUniverse={discoveryUniverse||[]}
              discoveryRuns={discoveryRuns||[]}
              marketPulse={marketPulse}
              discoveryStock={ariaContext?.type==="discovery"?discoveryStock:null}
            />
          </div>
        )}
      </div>

      {narrow&&(
        <nav aria-label="Sections" style={{display:"flex",flexShrink:0,borderTop:`1px solid ${C.borderHi}`,background:C.surface,padding:"2px 4px env(safe-area-inset-bottom)"}}>
          {[...PRIMARY_TABS,["__more","More","more"]].map(([id,label,icon])=>{
            const isMore=id==="__more";
            const on=isMore?(moreActive||moreOpen):tab===id;
            const badge=isMore?moreBadge:id==="portfolio"?portfolioAlerts.length:0;
            return(
              <button key={id} onClick={()=>isMore?setMoreOpen(o=>!o):goTab(id)} aria-current={on&&!isMore?"page":undefined}
                aria-expanded={isMore?moreOpen:undefined}
                style={{flex:1,minWidth:0,minHeight:58,display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",gap:3,
                  border:0,background:"transparent",cursor:"pointer",color:on?C.accent:C.textDim,fontSize:11,fontWeight:on?600:500,position:"relative",fontFamily:SANS}}>
                <Icon name={icon} size={20}/>
                <span>{label}</span>
                {badge>0&&<span style={{position:"absolute",top:5,left:"calc(50% + 5px)",minWidth:18,height:18,padding:"0 5px",borderRadius:9,background:C.red+"18",color:C.red,fontSize:10.5,fontWeight:700,display:"grid",placeItems:"center",fontFamily:MONO}}>{badge}</span>}
              </button>
            );
          })}
        </nav>
      )}

      {narrow&&moreOpen&&(
        <>
          <button aria-label="Close menu" onClick={()=>setMoreOpen(false)} style={{position:"absolute",inset:0,zIndex:40,border:0,background:"rgba(28,26,22,.35)",cursor:"default"}}/>
          <div role="dialog" aria-label="More sections" style={{position:"absolute",left:0,right:0,bottom:0,zIndex:41,background:C.surface,borderRadius:"14px 14px 0 0",padding:"10px 12px 18px",boxShadow:"0 -10px 30px rgba(28,26,22,.15)"}}>
            <div aria-hidden="true" style={{width:40,height:4,borderRadius:2,background:C.borderHi,margin:"2px auto 10px"}}/>
            {TABS.filter(t=>!PRIMARY_TABS.some(([id])=>id===t.id)).map(t=>(
              <button key={t.id} onClick={()=>goTab(t.id)} aria-current={tab===t.id?"page":undefined}
                style={{display:"flex",alignItems:"center",gap:10,width:"100%",minHeight:52,padding:"0 12px",border:0,borderRadius:8,cursor:"pointer",textAlign:"left",fontFamily:SANS,
                  background:tab===t.id?C.accent+"12":"transparent",color:tab===t.id?C.accent:C.text,fontSize:15,fontWeight:500}}>
                {t.label}
                {t.badge>0&&<span style={{marginLeft:"auto",background:C.red+"14",color:C.red,borderRadius:9,padding:"1px 8px",fontSize:12,fontWeight:600,fontFamily:MONO}}>{t.badge}</span>}
              </button>
            ))}
            <button onClick={()=>{setMoreOpen(false);setAriaOpen(true);}} style={{display:"flex",alignItems:"center",gap:10,width:"100%",minHeight:52,padding:"0 12px",border:0,borderRadius:8,cursor:"pointer",background:"transparent",color:C.text,fontSize:15,fontFamily:SANS}}>
              <Icon name="spark"/>Ask ARIA
            </button>
          </div>
        </>
      )}
    </div>
  );
}
