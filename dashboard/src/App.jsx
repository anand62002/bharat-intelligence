import { useState, useEffect, useRef, useCallback } from "react";

const C = {
  bg:"#06060f", surface:"#0c0c1e", panel:"#10102a", border:"#1a1a35", borderHi:"#2a2a55",
  accent:"#F0B429", accentDim:"#c0860f", green:"#10B981", red:"#EF4444",
  blue:"#6366F1", purple:"#A855F7", teal:"#14B8A6", orange:"#F97316",
  cyan:"#06B6D4", lime:"#84CC16",
  muted:"#5a5a7a", text:"#dde0f0", textDim:"#8888aa",
};

const FONT_STYLE = `
  @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
  *{box-sizing:border-box;margin:0;padding:0;}
  ::-webkit-scrollbar{width:3px;height:3px;}
  ::-webkit-scrollbar-track{background:transparent;}
  ::-webkit-scrollbar-thumb{background:#2a2a55;border-radius:2px;}
  @keyframes fadeUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
  @keyframes dotBounce{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-6px)}}
  @keyframes slideIn{from{opacity:0;transform:translateX(20px)}to{opacity:1;transform:translateX(0)}}
  @keyframes glow{0%,100%{box-shadow:0 0 8px #F0B42933}50%{box-shadow:0 0 20px #F0B42966}}
  @keyframes scanline{0%{transform:translateY(-100%)}100%{transform:translateY(400%)}}
  @keyframes criticalPulse{0%,100%{box-shadow:0 0 0 0 #10B98144,0 0 12px #10B98133}50%{box-shadow:0 0 0 3px #10B98122,0 0 24px #10B98166}}
  @keyframes dangerPulse{0%,100%{box-shadow:0 0 0 0 #EF444444,0 0 12px #EF444433}50%{box-shadow:0 0 0 3px #EF444422,0 0 24px #EF444466}}
  @keyframes criticalBadge{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.85;transform:scale(1.04)}}
  .aria-glow{box-shadow:0 0 24px #A855F733,0 0 48px #A855F711;}
  .portfolio-alert{animation:glow 2s infinite;}
  .research-card:hover{border-color:#F0B42966!important;transform:translateY(-1px);transition:all .15s;}
  .critical-discovery{animation:criticalPulse 2.4s ease-in-out infinite;}
  .critical-danger{animation:dangerPulse 2s ease-in-out infinite;}
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

// ─── CRITICAL THRESHOLDS ─────────────────────────────────────────────────────
// Discovery: flag if upsidePct >= 100 AND upsideConfidence >= 70
// Portfolio danger: flag if dangerDropPct >= 70 AND dangerConfidence >= 65 within 2-3 months
const isCriticalDiscovery = (s) => s.upsidePct >= 100 && s.upsideConfidence >= 70;
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
    if(toStop < 8 && toStop > 0) alerts.push({id:`stop-${h.id}`,symbol:h.symbol,severity:"critical",type:"stoploss_proximity",title:`${h.symbol} approaching stop-loss`,detail:`Current ₹${h.currentPrice} is ${toStop.toFixed(1)}% above stop ₹${h.stoplossPrice}.`,action:"Review or reduce position",portfolioId:h.id});
    if(toTarget < 12 && toTarget > 0) alerts.push({id:`tgt-${h.id}`,symbol:h.symbol,severity:"info",type:"target_proximity",title:`${h.symbol} approaching target`,detail:`${toTarget.toFixed(1)}% from target ₹${h.targetPrice}. Consider partial profits.`,action:"Review profit booking",portfolioId:h.id});
    if(pnlPct > 15 && h.linkedRecId) alerts.push({id:`mile-${h.id}`,symbol:h.symbol,severity:"info",type:"rec_milestone",title:`${h.symbol} +${pnlPct.toFixed(1)}% since recommendation`,detail:`P&L: ₹${((h.currentPrice-h.avgBuy)*h.qty).toLocaleString("en-IN",{maximumFractionDigits:0})}. Original target still ahead.`,action:"Review thesis validity",portfolioId:h.id});
  });
  return alerts;
};

// ─── ATOMS ───────────────────────────────────────────────────────────────────
const Tag=({children,color=C.accent,small=false})=>(
  <span style={{background:color+"22",color,border:`1px solid ${color}44`,borderRadius:4,padding:small?"1px 5px":"2px 7px",fontSize:small?9:11,fontWeight:700,letterSpacing:.3,whiteSpace:"nowrap"}}>{children}</span>
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
const EmptyState=({icon="📡",title,sub,mono=false})=>(
  <div style={{textAlign:"center",padding:"52px 24px",color:C.muted}}>
    <div style={{fontSize:34,marginBottom:10}}>{icon}</div>
    <div style={{fontSize:12,fontWeight:600,color:C.textDim,marginBottom:6}}>{title}</div>
    {sub&&<div style={{fontSize:10,color:C.muted,maxWidth:380,margin:"0 auto",lineHeight:1.7,fontFamily:mono?"JetBrains Mono":undefined}}>{sub}</div>}
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
        <span style={{fontSize:10,color:C.muted}}>📐 Valuation Scenarios — not loaded</span>
        {onFetch&&<button onClick={onFetch} style={{fontSize:10,background:C.accent,color:'#000',border:'none',borderRadius:4,padding:'3px 8px',cursor:'pointer'}}>Load</button>}
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
        <span style={{fontSize:13}}>📐</span>
        <span style={{fontWeight:700,fontSize:12,color:C.text}}>Valuation Scenarios</span>
        <span style={{fontSize:11,color:recColor,fontWeight:700}}>{rec.replace('_',' ')}</span>
        {val.data_quality==='ESTIMATED'&&<span style={{fontSize:9,color:C.muted,background:C.card,padding:'1px 5px',borderRadius:3}}>estimated</span>}
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
              <div style={{fontSize:9,fontWeight:700,color,marginBottom:4}}>{sn}</div>
              <div style={{fontWeight:800,fontSize:13,color:C.text}}>
                {iv!=null?`₹${iv.toLocaleString('en-IN',{maximumFractionDigits:0})}`:'—'}
              </div>
              <div style={{fontSize:9,color:mosColor,marginTop:3}}>
                MOS {mos!=null?`${mos>0?'+':''}${mos.toFixed(0)}%`:'—'}
              </div>
              <div style={{fontSize:9,color:C.muted,marginTop:2}}>
                g={s.growth_rate?.toFixed(1)}% w={s.wacc?.toFixed(1)}%
              </div>
            </div>
          );
        })}
      </div>

      {/* Fair value range bar */}
      {fv.low&&fv.high&&cp&&(
        <div style={{marginBottom:10}}>
          <div style={{fontSize:9,color:C.muted,marginBottom:4}}>
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
          <div style={{fontSize:9,fontWeight:700,color:C.muted,marginBottom:4,textTransform:'uppercase'}}>Sensitivity Tornado</div>
          {tornado.map(row=>{
            const barW=Math.round((row.impact/maxImpact)*100);
            return(
              <div key={row.assumption} style={{display:'flex',alignItems:'center',gap:6,marginBottom:4}}>
                <div style={{fontSize:9,color:C.textDim,width:90,flexShrink:0}}>{row.assumption}</div>
                <div style={{flex:1,background:C.card,borderRadius:3,height:6,overflow:'hidden'}}>
                  <div style={{width:`${barW}%`,height:'100%',background:C.accent,borderRadius:3}}/>
                </div>
                <div style={{fontSize:9,color:C.muted,width:45,textAlign:'right'}}>
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
      <span style={{fontSize:10,color:C.muted}}>🏦 WarrenBot — data not yet available for this stock</span>
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
          <div style={{fontSize:11,fontWeight:700,color:C.accent}}>🏦 WarrenBot</div>
          <div style={{fontSize:9,color:C.muted,marginTop:2}}>Long-term quality lens · Not a momentum signal</div>
        </div>
        <div style={{display:"flex",alignItems:"center",gap:8}}>
          <Tag color={convColor} small>{wb.conviction_rating}</Tag>
          <span style={{fontSize:20,fontWeight:800,color:convColor,fontFamily:"JetBrains Mono",lineHeight:1}}>
            {wb.score}<span style={{fontSize:10,color:C.muted,fontWeight:400}}>/100</span>
          </span>
        </div>
      </div>

      {/* Five sub-score boxes */}
      <div style={{display:"grid",gridTemplateColumns:"repeat(5,1fr)",gap:5,marginBottom:9}}>
        {subScores.map(({label,val})=>(
          <div key={label} style={{background:C.bg,border:`1px solid ${C.border}`,borderRadius:5,padding:"5px 6px"}}>
            <div style={{fontSize:8,color:C.muted,marginBottom:2}}>{label}</div>
            <div style={{fontSize:12,fontWeight:700,color:C.accent,fontFamily:"JetBrains Mono",marginBottom:3}}>
              {val??0}<span style={{fontSize:8,color:C.muted,fontWeight:400}}>/20</span>
            </div>
            <Bar pct={(val??0)/20*100} color={C.accent} h={3}/>
          </div>
        ))}
      </div>

      {/* Three metric boxes */}
      <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:5,marginBottom:9}}>
        {[
          {label:"Intrinsic Value",  val:inrFmt(wb.intrinsic_value_per_share), color:"white"},
          {label:"Margin of Safety", val:wb.margin_of_safety_pct!=null?`${wb.margin_of_safety_pct.toFixed(1)}%`:"—",
           color:wb.margin_of_safety_pct>0?C.green:wb.margin_of_safety_pct<0?C.red:C.muted},
          {label:"10yr EPS CAGR",   val:wb.ten_year_eps_cagr!=null?`${wb.ten_year_eps_cagr.toFixed(1)}%`:"—", color:"white"},
        ].map(({label,val,color})=>(
          <div key={label} style={{background:C.bg,border:`1px solid ${C.border}`,borderRadius:5,padding:"5px 7px"}}>
            <div style={{fontSize:8,color:C.muted,marginBottom:2}}>{label}</div>
            <div style={{fontSize:12,fontWeight:700,color,fontFamily:"JetBrains Mono"}}>{val}</div>
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
          <div style={{fontSize:9,fontWeight:700,color:C.green,marginBottom:4}}>✅ Why Buffett/RJ would like it</div>
          <div style={{fontSize:9,color:C.green+"cc",lineHeight:1.55}}>{wb.why_like||"—"}</div>
        </div>
        <div style={{background:C.red+"08",border:`1px solid ${C.red}22`,borderRadius:5,padding:"6px 8px"}}>
          <div style={{fontSize:9,fontWeight:700,color:C.red,marginBottom:4}}>⛔ Why they would pass</div>
          <div style={{fontSize:9,color:C.red+"cc",lineHeight:1.55}}>{wb.why_pass||"—"}</div>
        </div>
      </div>

      {/* Data gaps notice */}
      {wb.data_gaps?.length>0&&(
        <div style={{background:C.accent+"0f",border:`1px solid ${C.accent}33`,borderRadius:4,padding:"4px 8px",marginBottom:7,fontSize:9,color:C.accent}}>
          ⚠ Data gaps: {wb.data_gaps.join(", ")} — score may be understated
        </div>
      )}

      {/* Bottom disclaimer */}
      <div style={{fontSize:8,color:C.muted,fontStyle:"italic",lineHeight:1.55}}>
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
            <span style={{fontSize:13,fontWeight:800,color:C.green,letterSpacing:-.2}}>CRITICAL OPPORTUNITY</span>
            <span style={{background:C.green,color:"#031a0e",borderRadius:4,padding:"1px 7px",fontSize:10,fontWeight:800,animation:"criticalBadge 2s ease-in-out infinite"}}>
              100%+ UPSIDE · HIGH CONVICTION
            </span>
          </div>
          <div style={{fontSize:10,color:"#80d0a0",marginTop:1}}>
            {stocks.length} idea{stocks.length>1?"s":""} flagged with 100%+ predicted growth in 6–12 months at ≥70% confidence — requires immediate attention
          </div>
        </div>
        <button onClick={()=>onOpenARIA("discovery_critical")} style={{background:C.green+"22",border:`1px solid ${C.green}55`,borderRadius:6,padding:"5px 11px",color:C.green,fontSize:10,fontWeight:700,cursor:"pointer",whiteSpace:"nowrap"}}>
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
                  <span style={{fontSize:13,fontWeight:800,color:"white",fontFamily:"JetBrains Mono"}}>{s.symbol}</span>
                  <span style={{background:C.green+"22",color:C.green,border:`1px solid ${C.green}44`,borderRadius:4,padding:"1px 6px",fontSize:10,fontWeight:800}}>+{s.upsidePct}%</span>
                </div>
                <div style={{fontSize:10,color:C.textDim}}>{s.name}</div>
              </div>
              <div style={{textAlign:"right"}}>
                <div style={{fontSize:11,fontWeight:700,color:C.green,fontFamily:"JetBrains Mono"}}>{s.upsideConfidence}%</div>
                <div style={{fontSize:8,color:C.muted}}>confidence</div>
              </div>
            </div>
            <div style={{fontSize:9,color:"#80d0a0",lineHeight:1.5,marginBottom:7}}>{s.upsideBasis.slice(0,90)}...</div>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
              <span style={{fontSize:9,color:C.muted}}>⏱ {s.upsideHorizon}</span>
              <span style={{fontSize:9,color:C.muted}}>Entry: {s.entry}</span>
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
function CriticalDangerBanner({holdings, onOpenARIA}){
  if(!holdings.length) return null;
  return(
    <div className="critical-danger" style={{
      background:`linear-gradient(135deg,${C.red}12,#7f1d1d08)`,
      border:`1.5px solid ${C.red}99`,
      borderRadius:10,padding:14,marginBottom:14,
    }}>
      <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:10}}>
        <div style={{width:28,height:28,borderRadius:6,background:C.red,display:"flex",alignItems:"center",justifyContent:"center",fontSize:14,flexShrink:0,animation:"criticalBadge 1.5s ease-in-out infinite"}}>🚨</div>
        <div style={{flex:1}}>
          <div style={{display:"flex",alignItems:"center",gap:7}}>
            <span style={{fontSize:13,fontWeight:800,color:C.red,letterSpacing:-.2}}>CRITICAL DANGER</span>
            <span style={{background:C.red,color:"white",borderRadius:4,padding:"1px 7px",fontSize:10,fontWeight:800,animation:"criticalBadge 1.5s ease-in-out infinite"}}>
              ACT NOW — HIGH CONFIDENCE DROP ALERT
            </span>
          </div>
          <div style={{fontSize:10,color:"#fca5a5",marginTop:1}}>
            {holdings.length} holding{holdings.length>1?"s":""} flagged with 70%+ predicted drop in 2–3 months at ≥65% confidence — immediate review required
          </div>
        </div>
        <button onClick={()=>onOpenARIA("portfolio_danger")} style={{background:C.red+"22",border:`1px solid ${C.red}55`,borderRadius:6,padding:"5px 11px",color:C.red,fontSize:10,fontWeight:700,cursor:"pointer",whiteSpace:"nowrap"}}>
          ✦ Get ARIA Advice
        </button>
      </div>
      {holdings.map(h=>(
        <div key={h.id} style={{background:C.bg,border:`1px solid ${C.red}55`,borderRadius:8,padding:12,marginBottom:8}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:8}}>
            <div>
              <div style={{display:"flex",alignItems:"center",gap:7,marginBottom:2}}>
                <span style={{fontSize:13,fontWeight:800,color:"white",fontFamily:"JetBrains Mono"}}>{h.symbol}</span>
                <span style={{background:C.red+"22",color:C.red,border:`1px solid ${C.red}44`,borderRadius:4,padding:"1px 7px",fontSize:11,fontWeight:800}}>-{h.dangerDropPct}% PREDICTED</span>
                <span style={{background:C.red+"15",color:"#fca5a5",borderRadius:4,padding:"1px 6px",fontSize:9,fontWeight:700}}>{h.dangerWindow}</span>
              </div>
              <div style={{fontSize:10,color:C.textDim}}>{h.name} · {h.qty} shares @ avg ₹{h.avgBuy} · Current ₹{h.currentPrice}</div>
              <div style={{fontSize:9,color:"#fca5a5",marginTop:1}}>
                Predicted floor: ~₹{Math.round(h.currentPrice*(1-h.dangerDropPct/100)).toLocaleString()} · Portfolio at risk: ₹{(h.currentPrice*h.qty).toLocaleString("en-IN",{maximumFractionDigits:0})}
              </div>
            </div>
            <div style={{textAlign:"right",flexShrink:0}}>
              <div style={{fontSize:16,fontWeight:800,color:C.red,fontFamily:"JetBrains Mono"}}>{h.dangerConfidence}%</div>
              <div style={{fontSize:8,color:C.muted}}>confidence</div>
            </div>
          </div>
          {/* Danger detail */}
          <div style={{background:C.red+"09",border:`1px solid ${C.red}22`,borderRadius:6,padding:"8px 10px",marginBottom:8}}>
            <div style={{fontSize:9,fontWeight:700,color:C.red,marginBottom:3}}>Why the agents flagged this:</div>
            <div style={{fontSize:10,color:"#fca5a5",lineHeight:1.65}}>{h.dangerTrigger}</div>
          </div>
          {/* Sources */}
          {h.dangerSources&&(
            <div style={{marginBottom:8}}>
              <div style={{fontSize:9,color:C.muted,marginBottom:3}}>Verified sources:</div>
              <div style={{display:"flex",flexWrap:"wrap",gap:4}}>
                {h.dangerSources.map(src=>(
                  <span key={src} style={{background:C.red+"0f",border:`1px solid ${C.red}22`,borderRadius:3,padding:"1px 6px",fontSize:8,color:"#fca5a5"}}>{src}</span>
                ))}
              </div>
            </div>
          )}
          {/* Confidence bar */}
          <div style={{display:"flex",alignItems:"center",gap:8}}>
            <span style={{fontSize:8,color:C.muted,whiteSpace:"nowrap"}}>Danger confidence</span>
            <div style={{flex:1,background:C.border,borderRadius:3,height:5,overflow:"hidden"}}>
              <div style={{width:`${h.dangerConfidence}%`,height:"100%",background:C.red,borderRadius:3}}/>
            </div>
            <span style={{fontSize:9,fontWeight:700,color:C.red,fontFamily:"JetBrains Mono"}}>{h.dangerConfidence}%</span>
          </div>
        </div>
      ))}
    </div>
  );
}
function RiskGauge({score}){
  const color=score<33?C.green:score<66?C.accent:C.red;
  const rad=((score/100)*180-90)*Math.PI/180;
  return(
    <div style={{textAlign:"center",flexShrink:0}}>
      <svg width="66" height="40" viewBox="0 0 66 40">
        <defs><linearGradient id={`rg${score}`} x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor={C.green}/><stop offset="50%" stopColor={C.accent}/><stop offset="100%" stopColor={C.red}/>
        </linearGradient></defs>
        <path d="M4 34 A29 29 0 0 1 62 34" fill="none" stroke={`url(#rg${score})`} strokeWidth="6" strokeLinecap="round"/>
        <line x1="33" y1="34" x2={33+19*Math.cos(rad)} y2={34+19*Math.sin(rad)} stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
        <circle cx="33" cy="34" r="3" fill="white"/>
      </svg>
      <div style={{fontSize:13,fontWeight:800,color,marginTop:-3,fontFamily:"JetBrains Mono"}}>{score}</div>
      <div style={{fontSize:8,color:C.muted}}>{score<33?"Low":score<66?"Mod":"High"}</div>
    </div>
  );
}

// ─── DISCOVERY CARD ───────────────────────────────────────────────────────────
function DiscoveryCard({stock, selected, onClick, onAddToPortfolio}){
  const ac = stock.action==="BUY"?C.green:stock.action==="SELL"?C.red:C.accent;
  const dsColor = stock.discoveryScore>=80?C.green:stock.discoveryScore>=65?C.accent:C.muted;
  const isCritical = isCriticalDiscovery(stock);
  return(
    <div className={`research-card${isCritical?" critical-discovery":""}`} onClick={onClick} style={{
      background:selected?C.panel:C.surface,
      border:`1.5px solid ${selected?(isCritical?C.green+"99":C.accent+"66"):(isCritical?C.green+"66":C.border)}`,
      borderRadius:10,padding:13,cursor:"pointer",marginBottom:9,transition:"all .15s",
      borderLeft:`3px solid ${isCritical?C.green:C.cyan}`,
    }}>
      {/* Critical badge strip */}
      {isCritical&&(
        <div style={{
          background:`linear-gradient(90deg,${C.green}22,${C.lime}11)`,
          border:`1px solid ${C.green}44`,borderRadius:5,
          padding:"4px 9px",marginBottom:8,
          display:"flex",alignItems:"center",justifyContent:"space-between",
        }}>
          <div style={{display:"flex",alignItems:"center",gap:6}}>
            <span style={{fontSize:11,fontWeight:800,color:C.green,animation:"criticalBadge 2s ease-in-out infinite"}}>🚀 CRITICAL OPPORTUNITY</span>
            <span style={{background:C.green,color:"#031a0e",borderRadius:3,padding:"0px 6px",fontSize:10,fontWeight:800}}>+{stock.upsidePct}% predicted</span>
          </div>
          <span style={{fontSize:9,color:"#80d0a0"}}>{stock.upsideConfidence}% confidence · {stock.upsideHorizon}</span>
        </div>
      )}
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:8}}>
        <div style={{flex:1}}>
          <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:3,flexWrap:"wrap"}}>
            <span style={{fontSize:13,fontWeight:700,color:"white",fontFamily:"JetBrains Mono"}}>{stock.symbol}</span>
            <Tag color={ac}>{stock.action}</Tag>
            {isCritical
              ? <Tag color={C.green} small>🚀 100%+ UPSIDE</Tag>
              : <Tag color={C.cyan} small>🔍 NEW IDEA</Tag>
            }
            <span style={{display:"flex",alignItems:"center",gap:3,background:dsColor+"15",border:`1px solid ${dsColor}44`,borderRadius:4,padding:"1px 5px"}}>
              <span style={{fontSize:9,color:dsColor,fontWeight:700}}>Score {stock.discoveryScore}</span>
            </span>
            {stock.govCheck.flags.length>0&&<Tag color={C.accent} small>⚠ flagged</Tag>}
            {stock.liquidityTier==="ILLIQUID"&&<Tag color={C.red} small>🚫 ILLIQUID</Tag>}
            {stock.liquidityTier==="LOW"&&<Tag color="#f59e0b" small>⚡ LOW LIQ</Tag>}
            {stock.suggestedPositionPct!=null&&stock.suggestedPositionPct>0&&(
              <span style={{background:C.green+"18",color:C.green,border:`1px solid ${C.green}44`,borderRadius:3,padding:"1px 5px",fontSize:9,fontWeight:700}}>
                📐 {stock.suggestedPositionPct}% alloc
              </span>
            )}
          </div>
          <div style={{fontSize:11,color:C.textDim,marginBottom:2}}>{stock.name} · {stock.sector}</div>
          <div style={{fontSize:10,color:isCritical?C.green:C.cyan,lineHeight:1.5}}>💡 {stock.discoveryReason}</div>
          <div style={{fontSize:9,color:C.muted,marginTop:2}}>conf {stock.confidence}% · valid till {stock.validTill} · {stock.horizon}{stock.impactCostPct!=null?` · impact ${stock.impactCostPct.toFixed(2)}%`:""}</div>
        </div>
        <RiskGauge score={stock.riskScore}/>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr 1fr",gap:5,marginBottom:8}}>
        {[["Price",`₹${stock.price.toLocaleString()}`,"white"],["Entry",stock.entry,C.accent],["Target",stock.target,C.green],["Stop",stock.stoploss,C.red]].map(([l,v,col])=>(
          <div key={l} style={{background:C.bg,borderRadius:4,padding:"4px 7px"}}>
            <div style={{fontSize:8,color:C.muted,textTransform:"uppercase"}}>{l}</div>
            <div style={{fontSize:9,fontWeight:700,color:col,fontFamily:"JetBrains Mono"}}>{v}</div>
          </div>
        ))}
      </div>
      {/* Forward estimates mini-row */}
      {(stock.forwardPe!=null||stock.pegRatio!=null||stock.epsGrowthPct!=null)&&(
        <div style={{display:"flex",gap:5,marginBottom:7,flexWrap:"wrap"}}>
          {stock.forwardPe!=null&&(
            <span style={{background:C.panel,border:`1px solid ${C.border}`,borderRadius:4,padding:"2px 7px",fontSize:9,color:C.accent}}>
              <span style={{color:C.muted}}>Fwd PE </span>{stock.forwardPe.toFixed(1)}x
            </span>
          )}
          {stock.pegRatio!=null&&(
            <span style={{background:C.panel,border:`1px solid ${stock.pegRatio<1?C.green+"55":stock.pegRatio>2?C.red+"55":C.border}`,borderRadius:4,padding:"2px 7px",fontSize:9,color:stock.pegRatio<1?C.green:stock.pegRatio>2?C.red:C.accent}}>
              <span style={{color:C.muted}}>PEG </span>{stock.pegRatio.toFixed(2)}
            </span>
          )}
          {stock.epsGrowthPct!=null&&(
            <span style={{background:C.panel,border:`1px solid ${C.border}`,borderRadius:4,padding:"2px 7px",fontSize:9,color:stock.epsGrowthPct>0?C.green:C.red}}>
              <span style={{color:C.muted}}>EPS gr </span>{stock.epsGrowthPct>0?"+":""}{stock.epsGrowthPct.toFixed(1)}%
            </span>
          )}
        </div>
      )}
      {/* Screen triggers */}
      <div style={{marginBottom:7}}>
        <div style={{fontSize:9,color:C.muted,marginBottom:3}}>Why it screened today:</div>
        <div style={{display:"flex",flexWrap:"wrap",gap:4}}>
          {stock.screenTriggers.map(t=>(
            <span key={t} style={{background:C.cyan+"12",border:`1px solid ${C.cyan}33`,borderRadius:3,padding:"1px 6px",fontSize:9,color:C.cyan}}>{t}</span>
          ))}
        </div>
      </div>
      {/* Confidence bar */}
      <div style={{marginBottom:8}}>
        <Bar pct={stock.confidence} color={isCritical?C.green:ac}/>
      </div>
      <div style={{display:"flex",gap:6}}>
        <button onClick={e=>{e.stopPropagation();onAddToPortfolio(stock);}} style={{
          background:`linear-gradient(135deg,${C.teal},#0d9488)`,border:"none",
          borderRadius:5,padding:"5px 10px",color:"white",fontSize:10,fontWeight:700,cursor:"pointer",flex:1}}>
          + Add to Portfolio
        </button>
        <button onClick={e=>{e.stopPropagation();onClick();}} style={{
          background:isCritical?C.green+"18":C.cyan+"18",border:`1px solid ${isCritical?C.green:C.cyan}44`,
          borderRadius:5,padding:"5px 10px",color:isCritical?C.green:C.cyan,fontSize:10,fontWeight:700,cursor:"pointer",flex:1}}>
          Deep Dive →
        </button>
      </div>
    </div>
  );
}

// ─── RESEARCH DISCOVERY TAB ───────────────────────────────────────────────────
function ResearchDiscoveryTab({portfolio, onAddToPortfolio, onOpenARIA, onOpenRunSummary, discoveryUniverse: _du, discoveryRuns: _dr, apiLoaded, valuationCache, setValuationCache}){
  const _universe = Array.isArray(_du) ? _du : [];
  const _runs     = Array.isArray(_dr) ? _dr : [];
  const [selectedId, setSelectedId] = useState(_universe[0]?.id || null);
  const [filter, setFilter] = useState("All");
  const selected = _universe.find(s=>s.id===selectedId);
  const sectors = ["All",...new Set(_universe.map(s=>s.sector))];
  const criticalStocks = _universe.filter(isCriticalDiscovery);
  // Sort: critical first, then by discoveryScore
  const sorted = [..._universe].sort((a,b)=>{
    const ac=isCriticalDiscovery(a)?1:0, bc=isCriticalDiscovery(b)?1:0;
    if(ac!==bc) return bc-ac;
    return b.discoveryScore-a.discoveryScore;
  });
  const filtered = filter==="All"?sorted:sorted.filter(s=>s.sector===filter);
  const icons={technical:"📊",fundamental:"🏭",sentiment:"📰",institutional:"🏛",macro:"🌍",historical:"📜"};
  const sc=s=>({BUY:C.green,SELL:C.red,NEUTRAL:C.muted,POSITIVE:C.green,"VERY POSITIVE":C.green,HOLD:C.accent}[s]||C.muted);

  return(
    <div style={{animation:"fadeUp .3s ease"}}>
      {/* Critical opportunity banner — always first */}
      <CriticalOpportunityBanner
        stocks={criticalStocks}
        onSelect={id=>{setSelectedId(id); setFilter("All");}}
        onOpenARIA={onOpenARIA}
      />

      {/* Header */}
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:14}}>
        <div>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:2}}>
            <div style={{width:7,height:7,borderRadius:"50%",background:C.cyan,animation:"pulse 2s infinite"}}/>
            <span style={{fontSize:14,fontWeight:700,color:"white"}}>Research Discovery Engine</span>
            <Tag color={C.cyan} small>PROACTIVE SCREENING</Tag>
          </div>
          <div style={{fontSize:10,color:C.muted}}>Stocks NOT in your portfolio — surfaced daily by the agent network scanning NSE/BSE universe · {_universe.length} ideas today</div>
        </div>
        <div style={{display:"flex",gap:6}}>
          {onOpenRunSummary&&<button onClick={onOpenRunSummary} style={{background:C.orange+"22",border:`1px solid ${C.orange}44`,borderRadius:7,padding:"6px 12px",color:C.orange,fontSize:11,fontWeight:700,cursor:"pointer"}}>📊 What ran today?</button>}
          <button onClick={()=>onOpenARIA("discovery")} style={{background:C.purple+"22",border:`1px solid ${C.purple}44`,borderRadius:7,padding:"6px 12px",color:C.purple,fontSize:11,fontWeight:700,cursor:"pointer"}}>✦ Ask ARIA</button>
        </div>
      </div>

      {/* How it works */}
      <div style={{background:C.cyan+"08",border:`1px solid ${C.cyan}33`,borderRadius:8,padding:10,marginBottom:14}}>
        <div style={{fontSize:11,fontWeight:700,color:C.cyan,marginBottom:6}}>🔍 How the Discovery Engine Works</div>
        <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:8}}>
          {[
            ["Universe Scan","Every morning, Technical + Fundamental agents scan 200+ NSE/BSE stocks across all sectors"],
            ["Multi-Signal Screen","Stocks passing 4+ simultaneous signals (momentum, FII buying, earnings quality, macro tailwind) make the shortlist"],
            ["Full Agent Analysis","Each shortlisted stock runs through all 7 agents — same rigour as portfolio stocks"],
            ["Governance Verified","Governance fact-checks every claim. Only verified, high-conviction ideas reach you"],
          ].map(([t,d])=>(
            <div key={t} style={{background:C.bg,borderRadius:6,padding:8}}>
              <div style={{fontSize:10,fontWeight:700,color:C.cyan,marginBottom:2}}>{t}</div>
              <div style={{fontSize:9,color:C.textDim,lineHeight:1.5}}>{d}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Sector filter */}
      <div style={{display:"flex",gap:5,marginBottom:12,flexWrap:"wrap"}}>
        {sectors.map(s=>(
          <button key={s} onClick={()=>setFilter(s)} style={{
            background:filter===s?C.cyan+"22":C.panel,
            border:`1px solid ${filter===s?C.cyan+"55":C.border}`,
            borderRadius:20,padding:"3px 10px",color:filter===s?C.cyan:C.muted,fontSize:10,cursor:"pointer"}}>
            {s}
          </button>
        ))}
      </div>

      {/* Empty state — shown when API is live but no discoveries yet */}
      {IS_LIVE && apiLoaded && _universe.length===0 && (
        <EmptyState
          icon="🔍"
          title="No discovery ideas yet"
          sub="The discovery screener scans 200+ NSE stocks every morning at 06:00 IST. If no ideas appear, check Governance → Data Source Health to confirm the pipeline ran. To run manually: python -m agents.discovery_screener"
        />
      )}

      {/* Stale recs notice — when ideas are from previous days */}
      {IS_LIVE && _universe.length>0 && (()=>{
        const today = new Date().toISOString().slice(0,10);
        const newest = _universe.reduce((best,s)=>{
          const d = s.validTill || "";
          return d > best ? d : best;
        }, "");
        // If newest validTill is more than 3 days out from today, assume it's from today's run
        // Otherwise show a stale warning
        const latestCreated = _universe.find(s=>s.validTill)?.validTill;
        if(latestCreated && latestCreated < today){
          return(
            <div style={{background:C.accent+"0d",border:`1px solid ${C.accent}33`,borderRadius:6,padding:"6px 12px",marginBottom:10,display:"flex",alignItems:"center",gap:8}}>
              <span style={{fontSize:9,color:C.accent,fontWeight:700}}>⚠</span>
              <span style={{fontSize:9,color:C.accent}}>
                Showing ideas from a previous run (most recent valid_till: {latestCreated}).
                Today's 06:00 IST run may not have completed yet — check the <b>Daily Screened Stocks</b> panel below.
              </span>
            </div>
          );
        }
        return null;
      })()}

      {/* Two-column layout — only shown when there is data */}
      {_universe.length>0&&<div style={{display:"grid",gridTemplateColumns:"360px 1fr",gap:16}}>
        {/* Left: cards */}
        <div>
          {filtered.map(s=>(
            <DiscoveryCard key={s.id} stock={s}
              selected={selectedId===s.id}
              onClick={()=>setSelectedId(s.id)}
              onAddToPortfolio={onAddToPortfolio}
            />
          ))}
        </div>

        {/* Right: deep dive */}
        {selected&&(
          <div style={{animation:"fadeUp .2s ease"}}>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:10}}>
              <div>
                <div style={{fontSize:14,fontWeight:700,color:"white",marginBottom:1}}>{selected.symbol} — {selected.name}</div>
                <div style={{fontSize:10,color:C.textDim,lineHeight:1.5,maxWidth:420}}>{selected.discoveryReason}</div>
              </div>
              <div style={{display:"flex",gap:6}}>
                <button onClick={()=>onAddToPortfolio(selected)} style={{background:`linear-gradient(135deg,${C.teal},#0d9488)`,border:"none",borderRadius:6,padding:"6px 12px",color:"white",fontSize:10,fontWeight:700,cursor:"pointer",whiteSpace:"nowrap"}}>
                  + Add to Portfolio
                </button>
              </div>
            </div>

            {/* 6-agent breakdown */}
            <div style={{fontSize:10,fontWeight:700,color:C.accent,marginBottom:7,textTransform:"uppercase",letterSpacing:1}}>6-Agent Analysis</div>
            <div style={{display:"flex",flexDirection:"column",gap:6,marginBottom:12}}>
              {Object.entries(selected.agents).map(([k,a])=>(
                <div key={k} style={{background:C.bg,border:`1px solid ${C.border}`,borderRadius:6,padding:9}}>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:3}}>
                    <span style={{fontSize:10,fontWeight:600,color:"white"}}>{icons[k]} {k.charAt(0).toUpperCase()+k.slice(1)}</span>
                    <div style={{display:"flex",gap:5,alignItems:"center"}}>
                      <Tag color={sc(a.signal)} small>{a.signal}</Tag>
                      <span style={{fontSize:9,color:C.muted,fontFamily:"JetBrains Mono"}}>{a.score}</span>
                    </div>
                  </div>
                  <div style={{fontSize:10,color:C.textDim,lineHeight:1.5,marginBottom:3}}>{a.detail}</div>
                  <Bar pct={a.score}/>
                </div>
              ))}
            </div>

            {/* Warren Bot panel */}
            <WarrenBotPanel wb={selected.warrenBot}/>

            {/* Valuation Scenarios panel */}
            <ValuationScenariosPanel
              val={valuationCache[selected.symbol]}
              symbol={selected.symbol}
              onFetch={()=>{
                apiFetch(`/api/valuation/${encodeURIComponent(selected.symbol)}`)
                  .then(d=>{if(d&&!d.error)setValuationCache(prev=>({...prev,[selected.symbol]:d}));})
                  .catch(()=>{});
              }}
            />

            {/* Risks + Catalysts */}
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8,marginBottom:10}}>
              <div>
                <div style={{fontSize:10,fontWeight:700,color:C.red,marginBottom:4}}>⚠ Risks</div>
                {selected.risks.map(r=><div key={r} style={{fontSize:9,color:"#f0a0a0",background:C.red+"0f",border:`1px solid ${C.red}22`,borderRadius:3,padding:"2px 6px",marginBottom:3}}>{r}</div>)}
              </div>
              <div>
                <div style={{fontSize:10,fontWeight:700,color:C.green,marginBottom:4}}>🚀 Catalysts</div>
                {selected.catalysts.map(c=><div key={c} style={{fontSize:9,color:"#80d0a0",background:C.green+"0f",border:`1px solid ${C.green}22`,borderRadius:3,padding:"2px 6px",marginBottom:3}}>{c}</div>)}
              </div>
            </div>

            {/* Gov check */}
            <div style={{background:C.blue+"08",border:`1px solid ${C.blue}33`,borderRadius:7,padding:9}}>
              <div style={{display:"flex",alignItems:"center",gap:5,marginBottom:5}}>
                <GovShield size={12}/><span style={{fontSize:10,fontWeight:700,color:C.blue}}>Governance Check</span>
                <span style={{fontSize:8,color:C.muted,marginLeft:"auto"}}>{selected.govCheck.lastChecked}</span>
              </div>
              <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:5}}>
                {[["Source Quality",`${selected.govCheck.sourceQuality}%`],["Hallucinations",`${selected.govCheck.hallucinations}`],["Status",selected.govCheck.verified?"Verified":"Pending"]].map(([l,v])=>(
                  <div key={l} style={{background:C.surface,borderRadius:4,padding:"4px 7px"}}>
                    <div style={{fontSize:8,color:C.muted}}>{l}</div>
                    <div style={{fontSize:11,fontWeight:700,color:C.blue,fontFamily:"JetBrains Mono"}}>{v}</div>
                  </div>
                ))}
              </div>
              {selected.govCheck.flags.length>0&&<div style={{marginTop:5,fontSize:9,color:C.accent,background:C.accent+"0f",borderRadius:3,padding:"2px 6px"}}>⚠ {selected.govCheck.flags[0]}</div>}
            </div>
          </div>
        )}
      </div>}{/* closes _universe.length>0 wrapper */}

      {/* ── Daily Screened Stocks panel ─────────────────────────────────────── */}
      <DiscoveryRunsPanel runs={_runs} apiLoaded={apiLoaded}/>
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
      <div style={headerStyle} onClick={()=>setOpen(o=>!o)}>
        <div style={{display:"flex",alignItems:"center",gap:8}}>
          <span style={{fontSize:13}}>🔭</span>
          <span style={{fontSize:11,fontWeight:700,color:"white"}}>Daily Screened Stocks</span>
          {hasRuns&&(
            <span style={{fontSize:9,color:C.muted,fontFamily:"JetBrains Mono",marginLeft:4}}>
              {runs[0].runDate} — {runs[0].totalScreened} screened / {runs[0].totalPassed} passed / {runs[0].totalDiscoveries} promoted
            </span>
          )}
          {!hasRuns&&IS_LIVE&&apiLoaded&&(
            <span style={{fontSize:9,color:C.muted}}>no runs yet — runs at 06:00 IST</span>
          )}
        </div>
        <div style={{display:"flex",alignItems:"center",gap:10}}>
          {hasRuns&&latestCov.cycle_pct_complete!=null&&(
            <span style={{fontSize:9,color:C.cyan,fontFamily:"JetBrains Mono"}}>
              cycle {latestCov.cycle_pct_complete}% · est. full coverage {latestCov.est_full_coverage}
            </span>
          )}
          <span style={{fontSize:14,color:C.muted,transform:open?"rotate(180deg)":"none",transition:"transform .2s"}}>▾</span>
        </div>
      </div>

      {/* Body — only when open */}
      {open&&(
        <div style={{padding:"10px 14px",maxHeight:480,overflowY:"auto"}}>
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
                    <span style={{fontSize:10,fontWeight:700,color:C.cyan,fontFamily:"JetBrains Mono"}}>{run.runDate}</span>
                    <span style={{fontSize:9,color:C.muted}}>{run.totalScreened} screened</span>
                    <span style={{fontSize:9,color:C.green}}>{run.totalPassed} passed filters</span>
                    {run.totalDiscoveries>0&&(
                      <span style={{fontSize:9,color:C.accent,fontWeight:700}}>⚡ {run.totalDiscoveries} promoted</span>
                    )}
                  </div>
                  <span style={{fontSize:12,color:C.muted,transform:isExp?"rotate(180deg)":"none",transition:"transform .2s"}}>▾</span>
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
                            <div style={{fontSize:8,color:C.muted}}>{l}</div>
                            <div style={{fontSize:10,fontWeight:700,color:C.cyan,fontFamily:"JetBrains Mono"}}>{v}</div>
                          </div>
                        ))}
                      </div>
                    )}
                    {/* Legend */}
                    <div style={{display:"flex",gap:12,marginBottom:6}}>
                      {[["⚡",C.accent,"promoted to rec"],["✓",C.green,"passed filters"],["·",C.textDim,"screened"]].map(([ic,col,lbl])=>(
                        <span key={lbl} style={{fontSize:9,color:col}}>{ic} {lbl}</span>
                      ))}
                    </div>
                    {/* Symbol pills */}
                    <div style={{display:"flex",flexWrap:"wrap",gap:4,maxHeight:200,overflowY:"auto"}}>
                      {(run.sliceSymbols||[]).map(sym=>{
                        const base=sym.replace(".NS","").replace(".BO","");
                        const isDisc=discSet.has(sym)||discSet.has(base);
                        const isPass=passSet.has(sym)||passSet.has(base);
                        return(
                          <span key={sym} style={{
                            fontSize:8,fontFamily:"JetBrains Mono",padding:"2px 6px",borderRadius:3,
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
function GovernanceResearchTab({onOpenARIA, researchFeed: _rf, apiLoaded}){
  const _feed   = Array.isArray(_rf) ? _rf : [];
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
        <div style={{width:34,height:34,borderRadius:8,background:C.purple+"18",border:`1px solid ${C.purple}44`,display:"flex",alignItems:"center",justifyContent:"center",fontSize:16}}>🧬</div>
        <div>
          <div style={{fontSize:13,fontWeight:700,color:"white"}}>Governance Research & Self-Improvement Agent</div>
          <div style={{fontSize:9,color:C.muted}}>Continuously monitors AI research, whitepapers, and market analysis literature to propose system upgrades · Agent debate before user review</div>
        </div>
        <div style={{marginLeft:"auto",background:C.purple+"12",border:`1px solid ${C.purple}33`,borderRadius:5,padding:"3px 9px",display:"flex",alignItems:"center",gap:4}}>
          <Dot color={C.purple} pulse/><span style={{fontSize:9,color:C.purple,fontWeight:600}}>Runs daily · 04:00 IST</span>
        </div>
      </div>

      <div style={{display:"flex",gap:3,marginBottom:14,borderBottom:`1px solid ${C.border}`}}>
        {[["research",`Research Feed (${_feed.length})`],["debate",`Agent Debates (${_debate.length} entries)`],["health","Agent Health"]].map(([id,lbl])=>(
          <button key={id} onClick={()=>setSec(id)} style={{background:sec===id?C.panel:"transparent",border:`1px solid ${sec===id?C.purple+"44":"transparent"}`,borderRadius:"5px 5px 0 0",padding:"6px 12px",color:sec===id?C.purple:C.muted,fontSize:10,fontWeight:sec===id?700:400,cursor:"pointer",marginBottom:-1}}>{lbl}</button>
        ))}
      </div>

      {/* RESEARCH FEED */}
      {sec==="research"&&(
        IS_LIVE && apiLoaded && _feed.length===0
          ? <EmptyState icon="🧬" title="No research proposals yet"
              sub="The governance research agent scans arXiv, SSRN, and finance journals daily at 04:00 IST. Proposals appear here after the first successful scheduler run." />
          : <div style={{display:"grid",gridTemplateColumns:"340px 1fr",gap:16}}>
          <div>
            <div style={{fontSize:10,color:C.muted,marginBottom:8}}>Sources monitored: arXiv, SSRN, Google Scholar, Anthropic blog, Morgan Stanley Research, SEBI circulars, RBI papers, DeepMind, OpenAI, academic finance journals</div>
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
                    <span style={{fontSize:9,color:C.muted,marginLeft:"auto"}}>{r.date}</span>
                  </div>
                  <div style={{fontSize:11,fontWeight:600,color:"white",marginBottom:2,lineHeight:1.4}}>{r.title}</div>
                  <div style={{fontSize:9,color:C.muted,marginBottom:5}}>{r.source} · Relevance: <span style={{color:C.accent,fontWeight:700}}>{r.relevance}%</span></div>
                  <div style={{display:"flex",gap:10}}>
                    <span style={{fontSize:9,color:C.green}}>👍 {r.votes.for}</span>
                    <span style={{fontSize:9,color:C.red}}>👎 {r.votes.against}</span>
                    <span style={{fontSize:9,color:C.muted}}>🤷 {r.votes.abstain}</span>
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
                <span style={{fontSize:9,color:C.muted,alignSelf:"center"}}>{paper.source} · {paper.date}</span>
              </div>
              <div style={{fontSize:13,fontWeight:700,color:"white",marginBottom:5,lineHeight:1.4}}>{paper.title}</div>

              <div style={{background:C.bg,border:`1px solid ${C.border}`,borderRadius:7,padding:10,marginBottom:10}}>
                <div style={{fontSize:10,fontWeight:700,color:C.textDim,marginBottom:4}}>Summary</div>
                <div style={{fontSize:11,color:C.textDim,lineHeight:1.65}}>{paper.summary}</div>
              </div>

              <div style={{background:C.purple+"08",border:`1px solid ${C.purple}33`,borderRadius:7,padding:10,marginBottom:10}}>
                <div style={{fontSize:10,fontWeight:700,color:C.purple,marginBottom:4}}>🔧 Proposed System Change</div>
                <div style={{fontSize:11,color:C.text,lineHeight:1.65,marginBottom:7}}>{paper.proposedChange}</div>
                <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
                  <div>
                    <div style={{fontSize:9,color:C.muted,marginBottom:3}}>Impacted Agents</div>
                    <div style={{display:"flex",gap:4,flexWrap:"wrap"}}>
                      {paper.impactedAgents.map(a=><Tag key={a} color={C.blue} small>{a}</Tag>)}
                    </div>
                  </div>
                  <div>
                    <div style={{fontSize:9,color:C.muted,marginBottom:3}}>Cost Impact</div>
                    <Tag color={paper.costImpact.includes("Free")||paper.costImpact.includes("Saves")?C.green:C.accent}>{paper.costImpact}</Tag>
                  </div>
                </div>
              </div>

              {/* Agent votes */}
              <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:7,padding:10,marginBottom:10}}>
                <div style={{fontSize:10,fontWeight:700,color:C.accent,marginBottom:6}}>Agent Vote Tally</div>
                <div style={{display:"flex",gap:12,marginBottom:8}}>
                  {[["For",paper.votes.for,C.green],["Against",paper.votes.against,C.red],["Abstain",paper.votes.abstain,C.muted]].map(([l,v,col])=>(
                    <div key={l} style={{textAlign:"center"}}>
                      <div style={{fontSize:20,fontWeight:800,color:col,fontFamily:"JetBrains Mono"}}>{v}</div>
                      <div style={{fontSize:9,color:C.muted}}>{l}</div>
                    </div>
                  ))}
                </div>
                <Bar pct={(paper.votes.for/(paper.votes.for+paper.votes.against+paper.votes.abstain))*100} color={C.green} h={4}/>
              </div>

              {/* Actions */}
              <div style={{display:"flex",gap:7}}>
                {paper.debateStatus==="pending"&&(
                  <button onClick={()=>onOpenARIA("research_paper",paper)} style={{background:`linear-gradient(135deg,${C.purple},#7e22ce)`,border:"none",borderRadius:6,padding:"8px 14px",color:"white",fontSize:11,fontWeight:700,cursor:"pointer",flex:1}}>
                    ✦ Discuss with ARIA
                  </button>
                )}
                {paper.debateStatus==="debating"&&(
                  <button onClick={()=>onOpenARIA("research_debate",paper)} style={{background:`linear-gradient(135deg,${C.accent},${C.accentDim})`,border:"none",borderRadius:6,padding:"8px 14px",color:C.bg,fontSize:11,fontWeight:700,cursor:"pointer",flex:1}}>
                    ⚡ Cast Your Vote / Approve
                  </button>
                )}
                {paper.debateStatus==="approved"&&(
                  <button onClick={()=>onOpenARIA("research_approved",paper)} style={{background:`linear-gradient(135deg,${C.green},#059669)`,border:"none",borderRadius:6,padding:"8px 14px",color:"white",fontSize:11,fontWeight:700,cursor:"pointer",flex:1}}>
                    📋 View Implementation Steps
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
          <div style={{fontSize:11,color:C.textDim,lineHeight:1.7,marginBottom:12}}>
            When a research proposal receives split votes, agents formally debate before it reaches you. Each agent provides a structured argument based on its domain expertise. You are the final decision-maker.
          </div>
          {_debate.length>0 ? (
            <div style={{display:"flex",flexDirection:"column",gap:7}}>
              {_debate.map((entry,i)=>(
                <div key={i} style={{background:C.bg,border:`1px solid ${debateColors[entry.stance]||C.muted}33`,borderRadius:7,padding:10,borderLeft:`3px solid ${debateColors[entry.stance]||C.muted}`}}>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:4}}>
                    <span style={{fontSize:11,fontWeight:700,color:"white"}}>{entry.agent||entry.agent_name}</span>
                    <Tag color={debateColors[entry.stance]||C.muted} small>{(entry.stance||"").toUpperCase()}</Tag>
                  </div>
                  <div style={{fontSize:10,color:C.textDim,lineHeight:1.6}}>{entry.argument}</div>
                </div>
              ))}
              {_feed.some(r=>r.debateStatus==="debating")&&(
                <button onClick={()=>onOpenARIA("research_debate",_feed.find(r=>r.debateStatus==="debating"))} style={{background:`linear-gradient(135deg,${C.accent},${C.accentDim})`,border:"none",borderRadius:6,padding:"8px 14px",color:C.bg,fontSize:11,fontWeight:700,cursor:"pointer",width:"100%",marginTop:6}}>
                  ⚡ You are the Tiebreaker — Discuss with ARIA to Decide
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
        <EmptyState icon="📊" title="Agent performance data not yet available"
          sub={"Accuracy and hallucination metrics are logged to the agent_performance table after each daily scheduler run.\n\nData will appear here after the first complete scheduler cycle (runs at 06:00 IST).\n\nTo run manually: python -m scheduler.orchestrator"} />
      )}
    </div>
  );
}

// ─── PORTFOLIO TAB ────────────────────────────────────────────────────────────
// ─── BROKEN SYMBOLS BANNER ────────────────────────────────────────────────────
function BrokenSymbolsBanner({broken, onFixed}){
  const [fixing, setFixing] = useState({});    // symbol → loading|done|error
  const [edits,  setEdits]  = useState({});    // symbol → custom yf_symbol input

  if(!IS_LIVE || !broken || broken.length===0) return null;

  const applyFix = async (item) => {
    const yf = (edits[item.symbol] || item.suggested_yf || "").trim();
    if(!yf){ alert("Enter a valid Yahoo Finance ticker (e.g. INDHOTEL.NS)"); return; }
    setFixing(f=>({...f,[item.symbol]:"loading"}));
    try {
      await apiFetch("/api/symbol/override", {
        method:"POST",
        body: JSON.stringify({symbol: item.symbol, yf_symbol: yf}),
      });
      setFixing(f=>({...f,[item.symbol]:"done"}));
      // Notify parent to re-fetch portfolio prices
      if(onFixed) onFixed();
    } catch(e) {
      setFixing(f=>({...f,[item.symbol]:"error"}));
    }
  };

  return(
    <div style={{
      background:`${C.accent}0d`,border:`1px solid ${C.accent}55`,
      borderRadius:8,padding:"10px 13px",marginBottom:14,
      borderLeft:`3px solid ${C.accent}`,
    }}>
      <div style={{display:"flex",alignItems:"center",gap:7,marginBottom:8}}>
        <span style={{fontSize:13}}>⚠️</span>
        <div>
          <div style={{fontSize:11,fontWeight:700,color:C.accent}}>
            {broken.length} holding{broken.length>1?"s":""} not updating prices
          </div>
          <div style={{fontSize:9,color:C.textDim}}>
            The symbol name doesn't match Yahoo Finance. Apply the suggested fix below or enter the correct ticker.
          </div>
        </div>
      </div>
      <div style={{display:"flex",flexDirection:"column",gap:7}}>
        {broken.map(item=>{
          const st = fixing[item.symbol];
          const suggested = item.suggested_yf;
          const editVal = edits[item.symbol] ?? (suggested || "");
          return(
            <div key={item.symbol} style={{
              background:C.bg,border:`1px solid ${C.border}`,borderRadius:6,
              padding:"7px 10px",display:"flex",alignItems:"center",gap:8,flexWrap:"wrap",
            }}>
              <div style={{flex:1,minWidth:120}}>
                <div style={{display:"flex",alignItems:"center",gap:5}}>
                  <span style={{fontSize:10,fontWeight:700,color:"white",fontFamily:"JetBrains Mono"}}>{item.symbol}</span>
                  <span style={{fontSize:9,color:C.muted}}>({item.name})</span>
                  {item.yf_symbol && (
                    <span style={{fontSize:8,color:C.red,background:C.red+"15",borderRadius:3,padding:"1px 5px"}}>
                      ❌ {item.yf_symbol} (broken)
                    </span>
                  )}
                </div>
                {item.avg_buy && <div style={{fontSize:8,color:C.muted}}>Avg buy: ₹{item.avg_buy.toLocaleString()}</div>}
              </div>
              {st==="done" ? (
                <span style={{fontSize:9,color:C.green,fontWeight:700}}>✓ Fixed!</span>
              ) : (
                <>
                  <input
                    value={editVal}
                    onChange={e=>setEdits(ed=>({...ed,[item.symbol]:e.target.value}))}
                    placeholder={suggested || "e.g. INDHOTEL.NS"}
                    style={{
                      background:C.surface,border:`1px solid ${suggested?C.green+"55":C.border}`,
                      borderRadius:4,padding:"3px 8px",color:"white",fontSize:10,
                      fontFamily:"JetBrains Mono",width:140,outline:"none",
                    }}
                  />
                  {suggested && editVal===suggested && (
                    <span style={{fontSize:8,color:C.green}}>💡 auto-suggested</span>
                  )}
                  <button
                    onClick={()=>applyFix(item)}
                    disabled={st==="loading"}
                    style={{
                      background:`linear-gradient(135deg,${C.teal},#0d9488)`,border:"none",
                      borderRadius:4,padding:"4px 10px",color:"white",fontSize:9,
                      fontWeight:700,cursor:st==="loading"?"wait":"pointer",whiteSpace:"nowrap",
                    }}
                  >
                    {st==="loading"?"Fixing...":"Apply Fix"}
                  </button>
                  {st==="error" && (
                    <span style={{fontSize:8,color:C.red}}>✗ Ticker not found — check it's correct</span>
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>
      <div style={{fontSize:8,color:C.muted,marginTop:7}}>
        Tip: Fixes are saved permanently — you won't need to do this again for the same symbol.
        To find the correct ticker, search <a href="https://finance.yahoo.com" target="_blank" rel="noreferrer" style={{color:C.accent}}>finance.yahoo.com</a>.
      </div>
    </div>
  );
}

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
        <span style={{fontSize:18}}>📊</span>
        <span style={{fontWeight:700,color:C.text}}>NIFTY Options Sentiment</span>
        {src==='fallback'&&<span style={{fontSize:11,color:C.muted,background:C.surface,padding:'2px 6px',borderRadius:4}}>VIX-estimated</span>}
      </div>
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(110px,1fr))',gap:8,marginBottom:12}}>
        <div style={{background:C.surface,borderRadius:6,padding:'10px 8px',textAlign:'center'}}>
          <div style={{fontSize:11,color:C.muted,marginBottom:4}}>Signal</div>
          <div style={{fontWeight:700,fontSize:13,color:sigColor}}>{sig.replace('_',' ')}</div>
        </div>
        <div style={{background:C.surface,borderRadius:6,padding:'10px 8px',textAlign:'center'}}>
          <div style={{fontSize:11,color:C.muted,marginBottom:4}}>Score</div>
          <div style={{fontWeight:700,fontSize:13,color:score>=60?C.green:score<=35?C.red:C.accent}}>{score}/100</div>
        </div>
        <div style={{background:C.surface,borderRadius:6,padding:'10px 8px',textAlign:'center'}}>
          <div style={{fontSize:11,color:C.muted,marginBottom:4}}>PCR</div>
          <div style={{fontWeight:700,fontSize:13,color:pcrColor}}>{pcr!=null?pcr.toFixed(2):'—'}</div>
        </div>
        <div style={{background:C.surface,borderRadius:6,padding:'10px 8px',textAlign:'center'}}>
          <div style={{fontSize:11,color:C.muted,marginBottom:4}}>India VIX</div>
          <div style={{fontWeight:700,fontSize:13,color:vixColor}}>{vix!=null?vix.toFixed(1):'—'}</div>
        </div>
        <div style={{background:C.surface,borderRadius:6,padding:'10px 8px',textAlign:'center'}}>
          <div style={{fontSize:11,color:C.muted,marginBottom:4}}>Max Pain</div>
          <div style={{fontWeight:700,fontSize:13,color:C.text}}>{mp!=null?`₹${mp.toLocaleString('en-IN',{maximumFractionDigits:0})}`:'—'}</div>
        </div>
        <div style={{background:C.surface,borderRadius:6,padding:'10px 8px',textAlign:'center'}}>
          <div style={{fontSize:11,color:C.muted,marginBottom:4}}>IV/HV</div>
          <div style={{fontWeight:700,fontSize:13,color:ivhv!=null?(ivhv>1.3?C.red:ivhv<0.85?C.green:C.accent):C.muted}}>{ivhv!=null?ivhv.toFixed(2):'—'}</div>
        </div>
      </div>
      {signal.commentary&&<div style={{fontSize:12,color:C.muted,lineHeight:1.5}}>{signal.commentary}</div>}
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
        <span style={{fontSize:11,fontWeight:700,color:C.accent}}>⚡ Portfolio Risk Dashboard</span>
        <span style={{fontSize:9,color:C.muted}}>{risk.snapshot_date} · {risk.holdings_count} holdings</span>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(6,1fr)",gap:7,marginBottom:10}}>
        {[
          ["Ann. Vol",vol!=null?`${vol.toFixed(1)}%`:"—",vol>25?C.red:vol>15?C.accent:C.green],
          ["VaR 95%",var95!=null?`${var95.toFixed(2)}%`:"—",varColor],
          ["VaR 99%",var99!=null?`${var99.toFixed(2)}%`:"—",var99>3?C.red:C.accent],
          ["CVaR 95%",cvar95!=null?`${cvar95.toFixed(2)}%`:"—",cvar95>3?C.red:C.accent],
          ["Sharpe",sharpe!=null?sharpe.toFixed(2):"—",sharpeColor],
          ["Max DD",mdd!=null?`${mdd.toFixed(1)}%`:"—",mdd>20?C.red:mdd>10?C.accent:C.green],
        ].map(([l,v,col])=>(
          <div key={l} style={{background:C.bg,borderRadius:5,padding:"6px 8px",textAlign:"center"}}>
            <div style={{fontSize:8,color:C.muted,marginBottom:2}}>{l}</div>
            <div style={{fontSize:12,fontWeight:800,color:col,fontFamily:"JetBrains Mono"}}>{v}</div>
          </div>
        ))}
      </div>
      <div style={{display:"flex",gap:8,flexWrap:"wrap",alignItems:"center"}}>
        <span style={{fontSize:9,color:C.muted}}>Concentration:</span>
        <Tag color={concColor} small>{conc} (HHI {hhi!=null?hhi.toFixed(3):"—"})</Tag>
        {Object.entries(risk.sector_weights||{}).slice(0,4).map(([s,w])=>(
          <span key={s} style={{fontSize:9,color:C.textDim}}>{s} {(w*100).toFixed(0)}%</span>
        ))}
      </div>
      {pairs.length>0&&(
        <div style={{marginTop:8,fontSize:9,color:C.muted}}>
          <span style={{fontWeight:700,color:C.accent}}>Correlated pairs: </span>
          {pairs.map(p=>(
            <span key={p.a+p.b} style={{marginRight:10,color:Math.abs(p.correlation)>0.85?C.red:C.accent}}>
              {p.a.replace(".NS","")} ↔ {p.b.replace(".NS","")} ({p.correlation>0?"+":""}{p.correlation.toFixed(2)})
            </span>
          ))}
        </div>
      )}
      {(risk.warnings||[]).map((w,i)=>(
        <div key={i} style={{marginTop:5,fontSize:9,color:C.accent,background:C.accent+"0d",borderRadius:4,padding:"3px 8px"}}>⚠ {w}</div>
      ))}
    </div>
  );
}

function PortfolioTab({portfolio,setPortfolio,onOpenARIA,brokenSymbols,onFixBroken,portfolioRisk,optionsSignal,paperPortfolio,paperHistory,apiLoaded}){
  const alerts=computePortfolioAlerts(portfolio);
  const dangerHoldings=portfolio.filter(isCriticalDanger);
  // Sort portfolio: danger holdings first
  const sortedPortfolio=[...portfolio].sort((a,b)=>{
    const ad=isCriticalDanger(a)?1:0, bd=isCriticalDanger(b)?1:0;
    return bd-ad;
  });
  const totalInvested=portfolio.reduce((s,h)=>s+(h.avgBuy*h.qty),0);
  const totalCurrent=portfolio.reduce((s,h)=>s+(h.currentPrice*h.qty),0);
  const totalPnL=totalCurrent-totalInvested;
  const totalPnLPct=totalInvested>0?(totalPnL/totalInvested)*100:0;
  const sectorMap={};
  portfolio.forEach(h=>{const v=h.currentPrice*h.qty;sectorMap[h.sector]=(sectorMap[h.sector]||0)+v;});
  const sectorColors=[C.accent,C.blue,C.teal,C.purple,C.orange,C.green,C.cyan];
  return(
    <div style={{animation:"fadeUp .3s ease"}}>
      {/* Critical danger banner — always first */}
      <CriticalDangerBanner holdings={dangerHoldings} onOpenARIA={onOpenARIA}/>
      {/* Broken symbol fix banner — shown when prices can't be fetched */}
      <BrokenSymbolsBanner broken={brokenSymbols} onFixed={onFixBroken}/>

      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14}}>
        <div>
          <div style={{fontSize:13,fontWeight:700,color:"white"}}>My Portfolio</div>
          <div style={{fontSize:9,color:C.muted,marginTop:1}}>System monitors holdings against agent signals · Critical danger detection active</div>
        </div>
        <div style={{display:"flex",gap:7}}>
          <button onClick={()=>onOpenARIA("portfolio")} style={{background:C.teal+"22",border:`1px solid ${C.teal}44`,borderRadius:7,padding:"6px 12px",color:C.teal,fontSize:11,fontWeight:700,cursor:"pointer"}}>+ Add Holding</button>
          <button onClick={()=>onOpenARIA("portfolio")} style={{background:C.purple+"22",border:`1px solid ${C.purple}44`,borderRadius:7,padding:"6px 12px",color:C.purple,fontSize:11,fontWeight:700,cursor:"pointer"}}>✦ Tell ARIA</button>
        </div>
      </div>

      {/* Non-danger alerts */}
      {alerts.filter(a=>a.severity!=="critical_danger").length>0&&(
        <div style={{marginBottom:14}}>
          <div style={{fontSize:10,fontWeight:700,color:C.accent,marginBottom:6}}>⚡ Portfolio Alerts ({alerts.filter(a=>a.severity!=="critical_danger").length})</div>
          {alerts.filter(a=>a.severity!=="critical_danger").map(a=>{
            const sc={critical:C.red,info:C.blue,warning:C.accent}[a.severity];
            return(
              <div key={a.id} style={{background:sc+"0d",border:`1px solid ${sc}44`,borderRadius:6,padding:"8px 11px",marginBottom:5,display:"flex",gap:8,alignItems:"flex-start"}}>
                <Tag color={sc} small>{a.severity.toUpperCase()}</Tag>
                <div style={{flex:1}}><div style={{fontSize:10,fontWeight:600,color:"white"}}>{a.title}</div><div style={{fontSize:9,color:C.textDim}}>{a.detail}</div></div>
                <button onClick={()=>onOpenARIA("holding",portfolio.find(h=>h.id===a.portfolioId))} style={{background:C.purple+"22",border:`1px solid ${C.purple}44`,borderRadius:4,padding:"2px 7px",color:C.purple,fontSize:9,cursor:"pointer"}}>✦ ARIA</button>
              </div>
            );
          })}
        </div>
      )}

      <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:9,marginBottom:14}}>
        {[["Invested",`₹${totalInvested.toLocaleString("en-IN",{maximumFractionDigits:0})}`,"white"],["Current",`₹${totalCurrent.toLocaleString("en-IN",{maximumFractionDigits:0})}`,"white"],["P&L",`${totalPnL>=0?"+":""}₹${Math.abs(totalPnL).toLocaleString("en-IN",{maximumFractionDigits:0})}`,totalPnL>=0?C.green:C.red],["Return",`${totalPnLPct>=0?"+":""}${totalPnLPct.toFixed(1)}%`,totalPnLPct>=0?C.green:C.red]].map(([l,v,col])=>(
          <div key={l} style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:7,padding:"9px 12px"}}>
            <div style={{fontSize:8,color:C.muted,textTransform:"uppercase",marginBottom:3}}>{l}</div>
            <div style={{fontSize:15,fontWeight:800,color:col,fontFamily:"JetBrains Mono"}}>{v}</div>
          </div>
        ))}
      </div>

      {/* Options market signal panel */}
      <OptionsSignalPanel signal={optionsSignal}/>

      {/* Portfolio risk panel */}
      <PortfolioRiskPanel risk={portfolioRisk}/>

      <div style={{display:"grid",gridTemplateColumns:"180px 1fr",gap:14}}>
        <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:11}}>
          <div style={{fontSize:9,fontWeight:700,color:C.muted,textTransform:"uppercase",marginBottom:8}}>Allocation</div>
          {Object.entries(sectorMap).sort((a,b)=>b[1]-a[1]).map(([sector,val],i)=>{
            const pct=(val/totalCurrent*100);
            return(<div key={sector} style={{marginBottom:7}}>
              <div style={{display:"flex",justifyContent:"space-between",marginBottom:2}}><span style={{fontSize:9,color:C.textDim}}>{sector}</span><span style={{fontSize:9,color:"white",fontFamily:"JetBrains Mono"}}>{pct.toFixed(0)}%</span></div>
              <Bar pct={pct} color={sectorColors[i%sectorColors.length]} h={4}/>
            </div>);
          })}
        </div>
        <div>
          <div style={{border:`1px solid ${C.border}`,borderRadius:8,overflow:"hidden"}}>
            <div style={{display:"grid",gridTemplateColumns:"1.4fr 50px 65px 65px 75px 80px 55px",background:C.panel,padding:"6px 11px",gap:4}}>
              {["Symbol","Qty","Avg Buy","Current","P&L%","Status / Risk","Action"].map(h=><div key={h} style={{fontSize:8,color:C.muted,fontWeight:700,textTransform:"uppercase"}}>{h}</div>)}
            </div>
            {sortedPortfolio.length===0&&(
              <div style={{padding:"28px 11px",textAlign:"center",color:C.muted,fontSize:10}}>
                No holdings yet — click <b style={{color:C.teal}}>+ Add Holding</b> or tell ARIA: "I bought RELIANCE 50 shares at ₹2,800"
              </div>
            )}
            {sortedPortfolio.map((h,i)=>{
              const pnlPct=((h.currentPrice-h.avgBuy)/h.avgBuy)*100;
              const toTarget=((h.targetPrice-h.currentPrice)/h.currentPrice)*100;
              const toStop=((h.currentPrice-h.stoplossPrice)/h.currentPrice)*100;
              const isDanger=isCriticalDanger(h);
              return(
                <div key={h.id} className={isDanger?"critical-danger":""} style={{
                  display:"grid",gridTemplateColumns:"1.4fr 50px 65px 65px 75px 80px 55px",
                  padding:"8px 11px",
                  background:isDanger?`${C.red}0d`:i%2===0?C.bg:C.surface,
                  borderTop:`1px solid ${isDanger?C.red+"55":C.border}`,
                  gap:4,alignItems:"center",
                  borderLeft:isDanger?`3px solid ${C.red}`:toStop<8?`2px solid ${C.red}`:toTarget<12&&toTarget>0?`2px solid ${C.green}`:"2px solid transparent",
                }}>
                  <div>
                    <div style={{display:"flex",alignItems:"center",gap:4,flexWrap:"wrap"}}>
                      <span style={{fontSize:10,fontWeight:700,color:isDanger?"#fca5a5":"white",fontFamily:"JetBrains Mono"}}>{h.symbol}</span>
                      {isDanger&&<span style={{background:C.red,color:"white",borderRadius:3,padding:"0px 4px",fontSize:8,fontWeight:800,animation:"criticalBadge 1.5s ease-in-out infinite"}}>🚨</span>}
                      {h.earningsAlert?.warning_level==="CRITICAL"&&<span title={`Earnings ${h.earningsAlert.days_until}d away${h.earningsAlert.quarter?" ("+h.earningsAlert.quarter+")":""} — CRITICAL`} style={{fontSize:11,cursor:"default"}}>🗓</span>}
                      {h.earningsAlert?.warning_level==="WARNING"&&<span title={`Earnings ${h.earningsAlert.days_until}d away${h.earningsAlert.quarter?" ("+h.earningsAlert.quarter+")":""} — WARNING`} style={{fontSize:11,cursor:"default",opacity:0.75}}>🗓</span>}
                    </div>
                    <div style={{fontSize:8,color:C.muted}}>{h.sector}</div>
                  </div>
                  <span style={{fontSize:10,color:C.textDim,fontFamily:"JetBrains Mono"}}>{h.qty}</span>
                  <span style={{fontSize:10,color:C.textDim,fontFamily:"JetBrains Mono"}}>₹{h.avgBuy.toLocaleString()}</span>
                  <span style={{fontSize:10,color:isDanger?"#fca5a5":"white",fontFamily:"JetBrains Mono"}}>₹{h.currentPrice.toLocaleString()}</span>
                  <div>
                    <div style={{fontSize:10,fontWeight:700,color:pnlPct>=0?C.green:C.red,fontFamily:"JetBrains Mono"}}>{pnlPct>=0?"+":""}{pnlPct.toFixed(1)}%</div>
                    <div style={{fontSize:8,color:C.muted}}>₹{Math.round((h.currentPrice-h.avgBuy)*h.qty).toLocaleString()}</div>
                  </div>
                  <div>
                    {isDanger
                      ? <div><div style={{fontSize:9,fontWeight:700,color:C.red}}>-{h.dangerDropPct}% risk</div><div style={{fontSize:8,color:"#fca5a5"}}>{h.dangerWindow}</div></div>
                      : <div><div style={{fontSize:9,color:toTarget>0?C.textDim:C.green}}>{toTarget>0?`+${toTarget.toFixed(0)}% to tgt`:"✓ past tgt"}</div><div style={{fontSize:8,color:toStop<8?C.red:C.muted}}>{toStop.toFixed(0)}% to stop</div></div>
                    }
                  </div>
                  <button onClick={()=>onOpenARIA("holding",h)} style={{
                    background:isDanger?C.red+"22":C.purple+"22",
                    border:`1px solid ${isDanger?C.red:C.purple}33`,
                    borderRadius:4,padding:"3px 6px",
                    color:isDanger?C.red:C.purple,fontSize:9,cursor:"pointer",fontWeight:isDanger?800:400,
                  }}>{isDanger?"🚨 ACT":"✦ ARIA"}</button>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Paper Portfolio Panel (P5-B) */}
      <PaperPortfolioPanel data={paperPortfolio} history={paperHistory} apiLoaded={apiLoaded}/>
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
        const screened=run.totalScreened??run.total_screened??0;
        const passed=run.totalPassed??run.total_passed??0;
        const discovered=run.totalDiscoveries??run.total_discoveries??0;
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
        body:JSON.stringify({model:"claude-sonnet-4-20250514",max_tokens:1000,system:SYSTEM,messages:msgs})});

      // ── First pass ────────────────────────────────────────────────────────
      const res=await ariaCall([...history,{role:"user",content:txt}]);
      const data=await res.json();
      let raw=data.content?.[0]?.text||"Error.";

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
        raw=data2.content?.[0]?.text||raw;
      }

      // ── Portfolio action tag ──────────────────────────────────────────────
      const actionMatch=raw.match(/<portfolio_action>([\s\S]*?)<\/portfolio_action>/);
      const displayText=raw
        .replace(/<portfolio_action>[\s\S]*?<\/portfolio_action>/g,"")
        .replace(/<fetch_warren_bot>[^<]+<\/fetch_warren_bot>/gi,"")
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
      {parts.map((p,j)=>p.startsWith("**")?<b key={j} style={{color:"white"}}>{p.slice(2,-2)}</b>:<span key={j}>{p}</span>)}
    </div>;
  });

  return(
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:C.bg}}>
      <div style={{padding:"10px 13px",borderBottom:`1px solid ${C.border}`,background:`linear-gradient(135deg,${C.purple}08,${C.blue}08)`,flexShrink:0}}>
        <div style={{display:"flex",alignItems:"center",gap:8}}>
          <div className="aria-glow" style={{width:28,height:28,borderRadius:"50%",background:`linear-gradient(135deg,${C.purple},${C.blue})`,display:"flex",alignItems:"center",justifyContent:"center",fontSize:12}}>✦</div>
          <div style={{flex:1}}><div style={{fontSize:11,fontWeight:700,color:"white"}}>ARIA</div><div style={{fontSize:8,color:C.purple}}>Adaptive Research Intelligence Assistant</div></div>
          <Dot color={C.purple} pulse/>
        </div>
        {ariaContext&&(
          <div style={{display:"flex",gap:5,marginTop:5,flexWrap:"wrap",alignItems:"center"}}>
            {ariaContext.type==="discovery"&&discoveryStock&&<Tag color={C.cyan} small>🔍 {discoveryStock.symbol}</Tag>}
            {ariaContext.type?.includes("research")&&ariaContext.paper&&<Tag color={C.purple} small>📄 Research</Tag>}
            {ariaContext.type==="holding"&&ariaContext.holding&&<Tag color={C.teal} small>💼 {ariaContext.holding.symbol}</Tag>}
            {ariaContext.type==="portfolio"&&<Tag color={C.teal} small>💼 Portfolio</Tag>}
            {ariaContext.type==="daily_run"&&<Tag color={C.orange} small>📊 Today's Run</Tag>}
            <button onClick={onClearContext} style={{background:"none",border:"none",color:C.muted,cursor:"pointer",fontSize:10,marginLeft:"auto"}}>✕</button>
          </div>
        )}
      </div>

      <div style={{flex:1,overflowY:"auto",padding:11,display:"flex",flexDirection:"column",gap:8}}>
        {messages.map((m,i)=>(
          <div key={i} style={{display:"flex",justifyContent:m.role==="user"?"flex-end":"flex-start",animation:"fadeUp .2s ease"}}>
            {m.role==="assistant"&&<div style={{width:17,height:17,borderRadius:"50%",background:`linear-gradient(135deg,${C.purple},${C.blue})`,display:"flex",alignItems:"center",justifyContent:"center",fontSize:8,marginRight:5,marginTop:3,flexShrink:0}}>✦</div>}
            <div style={{maxWidth:"88%",padding:"7px 10px",borderRadius:m.role==="user"?"10px 10px 2px 10px":"2px 10px 10px 10px",
              background:m.role==="user"?`linear-gradient(135deg,${C.accent}ee,${C.accentDim}ee)`:C.panel,
              color:m.role==="user"?"#0a0a1a":C.text,fontSize:11,border:m.role==="assistant"?`1px solid ${C.border}`:"none"}}>
              {m.role==="assistant"?renderMsg(m.text):m.text}
              {m.hasAction&&<div style={{marginTop:5,background:C.teal+"12",border:`1px solid ${C.teal}33`,borderRadius:4,padding:"3px 7px",fontSize:9,color:C.teal,fontWeight:700}}>✓ Portfolio updated</div>}
            </div>
          </div>
        ))}
        {loading&&<div style={{display:"flex",gap:3,paddingLeft:23}}>{[0,1,2].map(i=><div key={i} style={{width:6,height:6,borderRadius:"50%",background:C.purple,animation:`dotBounce 1.2s ${i*.15}s infinite`}}/>)}</div>}
        <div ref={bottomRef}/>
      </div>

      <div style={{padding:"4px 9px",display:"flex",gap:4,flexWrap:"wrap",borderTop:`1px solid ${C.border}`,flexShrink:0}}>
        {QUICK.map(q=><button key={q} onClick={()=>setInput(q)} style={{background:C.panel,border:`1px solid ${C.borderHi}`,borderRadius:20,padding:"2px 8px",color:C.textDim,fontSize:9,cursor:"pointer"}}>{q}</button>)}
      </div>
      <div style={{padding:"5px 9px 9px",display:"flex",gap:5,flexShrink:0}}>
        <input value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>e.key==="Enter"&&send()}
          placeholder={ariaContext?.type==="discovery"?"Ask about this discovery idea...":ariaContext?.type?.includes("research")?"Discuss or approve this research proposal...":"Ask ARIA anything..."}
          style={{flex:1,background:C.panel,border:`1px solid ${C.borderHi}`,borderRadius:6,padding:"7px 10px",color:"white",fontSize:11,outline:"none",fontFamily:"Space Grotesk"}}/>
        <button onClick={send} disabled={loading||!input.trim()} style={{background:loading?C.panel:`linear-gradient(135deg,${C.purple},${C.blue})`,border:"none",borderRadius:6,padding:"7px 12px",color:loading?C.muted:"white",fontWeight:700,fontSize:12,cursor:loading?"default":"pointer"}}>→</button>
      </div>
    </div>
  );
}

// ─── SYSTEM HEALTH PANEL ─────────────────────────────────────────────────────
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
          <span style={{fontSize:11,fontWeight:700,color:hasIssues?(errors>0?C.red:C.accent):C.green}}>
            🔌 Data Source Health
          </span>
          {errors>0 && <span style={{background:C.red,color:"white",borderRadius:8,padding:"0 5px",fontSize:9,fontWeight:700}}>{errors} error{errors>1?"s":""}</span>}
          {warnings>0 && <span style={{background:C.accent,color:"#0a0a1a",borderRadius:8,padding:"0 5px",fontSize:9,fontWeight:700}}>{warnings} warning{warnings>1?"s":""}</span>}
          {!hasIssues && <span style={{fontSize:9,color:C.green}}>All systems nominal</span>}
          {infoCount>0 && !hasIssues && <span style={{fontSize:9,color:C.muted}}>{infoCount} note{infoCount>1?"s":""}</span>}
        </div>
        <span style={{fontSize:8,color:C.muted}}>{checked_at ? new Date(checked_at).toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit"}) + " IST" : ""}</span>
      </div>
      <div style={{display:"flex",flexDirection:"column",gap:5}}>
        {checks.map((c,i)=>{
          const col = sevColor(c.severity);
          // OK and INFO: compact single-line display
          if(c.severity==="ok"||c.severity==="info") return(
            <div key={i} style={{display:"flex",alignItems:"center",gap:6,padding:"3px 0",borderBottom:`1px solid ${C.border}22`,opacity:c.severity==="info"?0.65:1}}>
              <span style={{fontSize:9,color:col,width:10,flexShrink:0,fontWeight:700}}>{sevIcon(c.severity)}</span>
              <span style={{fontSize:9,fontWeight:600,color:C.textDim,width:130,flexShrink:0}}>{c.name}</span>
              <span style={{fontSize:9,color:C.muted,flex:1}}>{c.detail}</span>
              {c.severity==="info" && <span style={{fontSize:8,color:C.muted,fontStyle:"italic",flexShrink:0}}>deprecated</span>}
            </div>
          );
          // WARNING / ERROR: highlighted card
          return(
            <div key={i} style={{background:col+"0d",border:`1px solid ${col}33`,borderRadius:5,padding:"6px 9px"}}>
              <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:c.action?3:0}}>
                <span style={{fontSize:10,color:col,fontWeight:700,flexShrink:0}}>{sevIcon(c.severity)}</span>
                <span style={{fontSize:9,fontWeight:700,color:col,flex:1}}>{c.name}</span>
                <Tag color={col} small>{c.severity.toUpperCase()}</Tag>
              </div>
              <div style={{fontSize:9,color:C.textDim,marginLeft:16,marginBottom:c.action?3:0}}>{c.detail}</div>
              {c.action && <div style={{fontSize:8,color:col,background:col+"10",borderRadius:3,padding:"2px 7px",marginLeft:16}}>⚡ {c.action}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── PAPER PORTFOLIO PANEL (P5-B) ────────────────────────────────────────────
function PaperPortfolioPanel({data, history, apiLoaded}){
  const [expanded, setExpanded] = React.useState(false);
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
        onClick={()=>setExpanded(e=>!e)}
        style={{width:"100%",background:C.surface,border:"none",padding:"9px 12px",
          display:"flex",justifyContent:"space-between",alignItems:"center",cursor:"pointer"}}
      >
        <div style={{display:"flex",alignItems:"center",gap:8}}>
          <span style={{fontSize:10,fontWeight:700,color:C.purple}}>📋 Paper Portfolio (Simulation)</span>
          <span style={{fontSize:8,color:C.muted}}>auto-follows BUY signals · validates system before live trading</span>
        </div>
        <div style={{display:"flex",gap:12,alignItems:"center"}}>
          {pnlPct!=null&&<span style={{fontSize:9,fontWeight:700,color:pnlCol(pnlPct),fontFamily:"JetBrains Mono"}}>{fmt(pnlPct)} unrealized</span>}
          {alpha!=null&&<span style={{fontSize:9,color:pnlCol(alpha),fontFamily:"JetBrains Mono"}}>alpha {fmt(alpha)}</span>}
          {data?.win_rate!=null&&<span style={{fontSize:9,color:C.muted}}>{data.win_rate.toFixed(0)}% win rate</span>}
          <span style={{fontSize:9,color:C.muted}}>{expanded?"▲":"▼"}</span>
        </div>
      </button>

      {expanded&&(
        <div style={{padding:"10px 12px",background:C.bg}}>
          {!apiLoaded&&<div style={{fontSize:9,color:C.muted,textAlign:"center",padding:16}}>Loading paper portfolio…</div>}
          {apiLoaded&&!snap.total_invested&&open.length===0&&(
            <EmptyState icon="📋" title="Paper portfolio initialising"
              sub="Paper positions open automatically for each new BUY recommendation. Run: python -m agents.paper_portfolio --run --backfill to seed historical recs." />
          )}

          {/* Summary stats row */}
          {snap.total_invested>0&&(
            <div style={{display:"grid",gridTemplateColumns:"repeat(5,1fr)",gap:8,marginBottom:12}}>
              {[
                ["Invested",`₹${(snap.total_invested||0).toLocaleString("en-IN",{maximumFractionDigits:0})}`,C.text],
                ["Unrealized P&L",fmt(pnlPct),pnlCol(pnlPct)],
                ["Realized P&L",`₹${(snap.realized_pnl||0).toLocaleString("en-IN",{maximumFractionDigits:0})}`,pnlCol(snap.realized_pnl)],
                ["vs Nifty (alpha)",fmt(alpha),pnlCol(alpha)],
                ["Win Rate",data?.win_rate!=null?`${data.win_rate.toFixed(0)}%`:"—",data?.win_rate>=50?C.green:C.red],
              ].map(([l,v,col])=>(
                <div key={l} style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:6,padding:"7px 10px"}}>
                  <div style={{fontSize:7,color:C.muted,textTransform:"uppercase",marginBottom:2}}>{l}</div>
                  <div style={{fontSize:13,fontWeight:800,color:col,fontFamily:"JetBrains Mono"}}>{v}</div>
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
                <div style={{fontSize:9,fontWeight:700,color:C.muted,marginBottom:6}}>Portfolio P&L vs Nifty</div>
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
                  <span style={{fontSize:7,color:C.muted}}>{snaps[0]?.snapshot_date}</span>
                  <div style={{display:"flex",gap:8}}>
                    <span style={{fontSize:7,color:C.green}}>■ Portfolio</span>
                    <span style={{fontSize:7,color:C.muted}}>■ Nifty</span>
                  </div>
                  <span style={{fontSize:7,color:C.muted}}>{snaps[snaps.length-1]?.snapshot_date}</span>
                </div>
              </div>
            );
          })()}

          {/* Open positions */}
          {open.length>0&&(
            <div style={{marginBottom:10}}>
              <div style={{fontSize:9,fontWeight:700,color:C.muted,marginBottom:5,textTransform:"uppercase"}}>Open Positions ({open.length})</div>
              <div style={{border:`1px solid ${C.border}`,borderRadius:6,overflow:"hidden"}}>
                <div style={{display:"grid",gridTemplateColumns:"1fr 55px 65px 65px 70px 80px",background:C.panel,padding:"5px 9px",gap:4}}>
                  {["Symbol","Qty","Entry","Current","P&L%","Tier"].map(h=>(
                    <div key={h} style={{fontSize:7,color:C.muted,fontWeight:700,textTransform:"uppercase"}}>{h}</div>
                  ))}
                </div>
                {open.slice(0,10).map((p,i)=>{
                  const pct = p.unrealized_pnl_pct??0;
                  return(
                    <div key={p.id} style={{
                      display:"grid",gridTemplateColumns:"1fr 55px 65px 65px 70px 80px",
                      padding:"5px 9px",gap:4,alignItems:"center",
                      background:i%2===0?C.bg:C.surface,
                      borderTop:`1px solid ${C.border}`,
                    }}>
                      <div style={{fontSize:9,fontWeight:600,color:"white"}}>{p.symbol}</div>
                      <div style={{fontSize:9,color:C.muted,fontFamily:"JetBrains Mono"}}>{p.quantity}</div>
                      <div style={{fontSize:9,color:C.muted,fontFamily:"JetBrains Mono"}}>₹{(p.entry_price||0).toLocaleString("en-IN",{maximumFractionDigits:0})}</div>
                      <div style={{fontSize:9,color:"white",fontFamily:"JetBrains Mono"}}>₹{(p.current_price||0).toLocaleString("en-IN",{maximumFractionDigits:0})}</div>
                      <div style={{fontSize:9,fontWeight:700,color:pnlCol(pct),fontFamily:"JetBrains Mono"}}>{fmt(pct)}</div>
                      <div style={{fontSize:7,color:C.muted,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{(p.position_label||"").replace(" position","").replace(" (5%)","").replace(" (2.5%)","").replace(" (1.25%","")}</div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Trade history table */}
          {closed.length>0&&(
            <div>
              <div style={{fontSize:9,fontWeight:700,color:C.muted,marginBottom:5,textTransform:"uppercase"}}>
                Trade History ({closed.length})
              </div>
              <div style={{border:`1px solid ${C.border}`,borderRadius:6,overflow:"hidden"}}>
                <div style={{overflowX:"auto",maxHeight:320,overflowY:"auto"}}>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:9,fontFamily:"JetBrains Mono"}}>
                    <thead>
                      <tr style={{background:C.panel,position:"sticky",top:0,zIndex:1}}>
                        {["Symbol","Entry","Exit","Entry ₹","Exit ₹","P&L%","Alpha","Alloc ₹","Reason"].map(h=>(
                          <th key={h} style={{padding:"5px 8px",textAlign:"left",fontSize:7,color:C.muted,
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
                            <td style={{padding:"5px 8px",fontWeight:700,color:"white",whiteSpace:"nowrap"}}>{p.symbol}</td>
                            <td style={{padding:"5px 8px",color:C.muted,whiteSpace:"nowrap"}}>{entryD}</td>
                            <td style={{padding:"5px 8px",color:C.muted,whiteSpace:"nowrap"}}>{exitD}</td>
                            <td style={{padding:"5px 8px",color:C.muted}}>{entryP}</td>
                            <td style={{padding:"5px 8px",color:"white"}}>{exitP}</td>
                            <td style={{padding:"5px 8px",fontWeight:700,color:pnlCol(pct)}}>{fmt(pct)}</td>
                            <td style={{padding:"5px 8px",color:alp!=null?pnlCol(alp):C.muted}}>{alp!=null?fmt(alp):"—"}</td>
                            <td style={{padding:"5px 8px",color:C.muted}}>{alloc}</td>
                            <td style={{padding:"5px 8px",color:C.muted,fontSize:8,whiteSpace:"nowrap"}}>{reason||"—"}</td>
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


// ─── AGENT ATTRIBUTION PANEL (P5-A) ──────────────────────────────────────────
function AgentAttributionPanel({attribution}){
  const agents = attribution?.agents || [];
  if(!agents.length) return(
    <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:14,marginBottom:12}}>
      <div style={{fontSize:10,fontWeight:700,color:C.muted,marginBottom:4}}>Agent Attribution</div>
      <div style={{fontSize:9,color:C.textDim}}>Attribution data will populate after recommendations are 90+ days old. Each agent's hit rate and average alpha will be shown here.</div>
    </div>
  );
  return(
    <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:12}}>
      <div style={{fontSize:10,fontWeight:700,color:C.accent,marginBottom:2}}>Agent Attribution</div>
      <div style={{fontSize:8,color:C.muted,marginBottom:8}}>{attribution?.note}</div>
      <div style={{border:`1px solid ${C.border}`,borderRadius:6,overflow:"hidden"}}>
        <div style={{display:"grid",gridTemplateColumns:"1fr 60px 60px 70px 80px",background:C.panel,padding:"5px 9px",gap:4}}>
          {["Agent","Signals","Bullish","Hit Rate","Avg Alpha"].map(h=>(
            <div key={h} style={{fontSize:7,color:C.muted,fontWeight:700,textTransform:"uppercase"}}>{h}</div>
          ))}
        </div>
        {agents.map((a,i)=>{
          const hr  = a.hit_rate_90d;
          const alp = a.avg_alpha_90d;
          const hrCol = hr==null?C.muted:hr>=55?C.green:hr>=45?C.orange:C.red;
          const alpCol= alp==null?C.muted:alp>=0?C.green:C.red;
          return(
            <div key={a.agent_name} style={{
              display:"grid",gridTemplateColumns:"1fr 60px 60px 70px 80px",
              padding:"5px 9px",gap:4,alignItems:"center",
              background:i%2===0?C.bg:C.surface,
              borderTop:`1px solid ${C.border}`,
            }}>
              <div style={{fontSize:9,fontWeight:600,color:"white",textTransform:"capitalize"}}>
                {a.agent_name.replace(/_/g," ")}
                {i===0&&<span style={{marginLeft:5,fontSize:7,background:C.green+"22",color:C.green,borderRadius:3,padding:"1px 5px"}}>top</span>}
              </div>
              <div style={{fontSize:9,color:C.muted,fontFamily:"JetBrains Mono"}}>{a.signal_count}</div>
              <div style={{fontSize:9,color:C.muted,fontFamily:"JetBrains Mono"}}>{a.bullish_count}</div>
              <div style={{fontSize:9,fontWeight:700,color:hrCol,fontFamily:"JetBrains Mono"}}>{hr!=null?`${hr.toFixed(0)}%`:"—"}</div>
              <div style={{fontSize:9,fontWeight:700,color:alpCol,fontFamily:"JetBrains Mono"}}>{alp!=null?`${alp>=0?"+":""}${alp.toFixed(1)}%`:"—"}</div>
            </div>
          );
        })}
      </div>
      <div style={{fontSize:7,color:C.textDim,marginTop:5}}>Hit rate = % of BULLISH votes where stock beat Nifty 50 at 90 days. Target: ≥55%.</div>
    </div>
  );
}


// ─── PERFORMANCE TAB ─────────────────────────────────────────────────────────
function PerformanceTab({accuracy, outcomes, alphaChart, attribution, apiLoaded}){
  const byAction = accuracy?.by_action || {};
  const total    = accuracy?.total_tracked || 0;
  const ACTIONS  = ["BUY","HOLD","SELL","AVOID"];

  const outcomeColor = o =>
    o==="HIT" ? C.green : o==="MISS" ? C.red : o==="PARTIAL" ? C.orange : C.muted;

  const recent = Array.isArray(outcomes?.outcomes) ? outcomes.outcomes.slice(0,20) : [];

  return(
    <div style={{animation:"fadeUp .3s ease"}}>
      <div style={{marginBottom:14}}>
        <div style={{fontSize:13,fontWeight:700,color:"white"}}>📊 Recommendation Performance</div>
        <div style={{fontSize:9,color:C.muted,marginTop:1}}>
          Track record — how accurate have our recommendations been vs NIFTY 50?
          {total>0 && <span style={{marginLeft:6,color:C.textDim}}>{total} recommendations tracked</span>}
        </div>
      </div>

      {IS_LIVE && apiLoaded && !accuracy && (
        <EmptyState icon="📊" title="No performance data yet"
          sub="Outcomes are tracked at 90, 180, and 365 day horizons. Come back after the first recommendations are 90+ days old. The tracker runs daily at 18:30 IST." />
      )}

      {/* Accuracy Scorecard */}
      {accuracy && total > 0 && (
        <div style={{marginBottom:16}}>
          <div style={{fontSize:10,fontWeight:700,color:C.accent,marginBottom:8}}>Accuracy by Signal Type</div>
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
                    <span style={{fontSize:8,color:C.muted}}>{d.total_recs} recs</span>
                  </div>
                  {hr90!=null ? (
                    <>
                      <div style={{display:"flex",justifyContent:"space-between",marginBottom:4}}>
                        <span style={{fontSize:8,color:C.muted}}>Hit rate (90d)</span>
                        <span style={{fontSize:11,fontWeight:700,color:good90?C.green:C.red}}>{hr90}%</span>
                      </div>
                      <div style={{background:C.border,borderRadius:3,height:3,marginBottom:7}}>
                        <div style={{width:`${Math.min(hr90,100)}%`,height:"100%",borderRadius:3,background:good90?C.green:C.red}}/>
                      </div>
                    </>
                  ) : (
                    <div style={{fontSize:8,color:C.muted,marginBottom:7}}>90d not reached yet</div>
                  )}
                  {alpha90!=null && (
                    <div style={{display:"flex",justifyContent:"space-between"}}>
                      <span style={{fontSize:8,color:C.muted}}>Avg alpha (90d)</span>
                      <span style={{fontSize:10,fontWeight:600,color:alphaGood?C.green:alpha90>=0?C.accent:C.red}}>
                        {alpha90>=0?"+":""}{alpha90}% vs NIFTY
                      </span>
                    </div>
                  )}
                  {d.resolved_90d>0 && (
                    <div style={{marginTop:5,fontSize:8,color:C.muted}}>{d.resolved_90d} resolved at 90d</div>
                  )}
                </div>
              );
            })}
          </div>
          <div style={{marginTop:8,fontSize:8,color:C.muted,background:C.surface,borderRadius:5,padding:"4px 8px",display:"inline-block"}}>
            Target: BUY hit rate &gt;55% · avg alpha &gt;+3% at 90 days vs NIFTY 50
          </div>
        </div>
      )}

      {/* Alpha chart (weekly rolling) */}
      {alphaChart && alphaChart.series && alphaChart.series.length>0 && (
        <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:14}}>
          <div style={{fontSize:10,fontWeight:700,color:C.accent,marginBottom:9}}>Rolling Alpha (weekly avg, vs NIFTY 50)</div>
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
            <span style={{fontSize:7,color:C.muted}}>{alphaChart.series.slice(-20)[0]?.week||""}</span>
            <span style={{fontSize:7,color:C.muted}}>{alphaChart.series.slice(-1)[0]?.week||""}</span>
          </div>
          <div style={{display:"flex",gap:10,marginTop:5}}>
            <div style={{display:"flex",alignItems:"center",gap:3}}><div style={{width:8,height:8,background:C.green,borderRadius:1}}/><span style={{fontSize:7,color:C.muted}}>Outperformed NIFTY</span></div>
            <div style={{display:"flex",alignItems:"center",gap:3}}><div style={{width:8,height:8,background:C.red,borderRadius:1}}/><span style={{fontSize:7,color:C.muted}}>Underperformed NIFTY</span></div>
          </div>
        </div>
      )}

      {/* Agent Attribution (P5-A) */}
      <AgentAttributionPanel attribution={attribution} />

      {/* Recent outcomes table */}
      {recent.length > 0 && (
        <div>
          <div style={{fontSize:10,fontWeight:700,color:C.accent,marginBottom:8}}>Recent Resolved Outcomes (last 20)</div>
          <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,overflow:"hidden"}}>
            <div style={{display:"grid",gridTemplateColumns:"80px 55px 70px 80px 80px 80px",gap:0,padding:"6px 10px",borderBottom:`1px solid ${C.border}`,background:C.bg}}>
              {["Symbol","Action","Date","t90 Outcome","t90 Alpha","t180 Outcome"].map(h=>(
                <span key={h} style={{fontSize:7,color:C.muted,fontWeight:700,textTransform:"uppercase"}}>{h}</span>
              ))}
            </div>
            {recent.map((r,i)=>(
              <div key={r.id||i} style={{display:"grid",gridTemplateColumns:"80px 55px 70px 80px 80px 80px",gap:0,padding:"6px 10px",borderBottom:i<recent.length-1?`1px solid ${C.border}33`:"none",background:i%2===0?"transparent":C.bg+"44"}}>
                <span style={{fontSize:9,fontWeight:700,color:"white",fontFamily:"JetBrains Mono"}}>{r.symbol}</span>
                <Tag color={r.action==="BUY"?C.green:r.action==="SELL"?C.red:r.action==="HOLD"?C.teal:C.orange}>{r.action}</Tag>
                <span style={{fontSize:8,color:C.muted}}>{(r.rec_date||"").slice(0,10)}</span>
                {r.outcome_t90&&r.outcome_t90!=="PENDING" ? (
                  <Tag color={outcomeColor(r.outcome_t90)} small>{r.outcome_t90}</Tag>
                ) : (
                  <span style={{fontSize:8,color:C.muted}}>Pending</span>
                )}
                {r.alpha_t90!=null ? (
                  <span style={{fontSize:8,fontWeight:600,color:r.alpha_t90>=0?C.green:C.red}}>
                    {r.alpha_t90>=0?"+":""}{(r.alpha_t90*100).toFixed(1)}%
                  </span>
                ) : <span style={{fontSize:8,color:C.muted}}>—</span>}
                {r.outcome_t180&&r.outcome_t180!=="PENDING" ? (
                  <Tag color={outcomeColor(r.outcome_t180)} small>{r.outcome_t180}</Tag>
                ) : (
                  <span style={{fontSize:8,color:C.muted}}>Pending</span>
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
  const [recsPortfolioFilter, setRecsPortfolioFilter] = useState(false); // DB-8: filter recs to held symbols only
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
  const [marketNews,        setMarketNews]        = useState([]);   // DB-7: India market news
  const [newsSymbol,        setNewsSymbol]        = useState("NIFTY"); // DB-7: which symbol's news to show
  // P5-B: Paper portfolio simulation
  const [paperPortfolio,    setPaperPortfolio]    = useState(null);
  const [paperHistory,      setPaperHistory]      = useState(null);
  // P5-A: Agent attribution
  const [agentAttribution,  setAgentAttribution]  = useState(null);
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
      apiFetch("/api/attribution/agents")  // P5-A: agent attribution
        .then(d=>{ if(d) setAgentAttribution(d); })
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
            ? {...h, _saving:false, _error:true, notes:"⚠️ Not saved — check connection"}
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
                ?{...h,_saving:false,_error:true,notes:"⚠️ Exit not saved — check connection"}:h));
            });
          }
          return prev.map(h=>h.symbol===action.symbol&&h.status==="holding"
            ?{...h,status:"exited",currentPrice:exitPrice||h.currentPrice,notes:note,_saving:!!API_URL}:h);
        }
      });
    }
  },[]);

  const TABS=[
    {id:"discovery",icon:"🔍",label:"Discovery",badge:(discoveryUniverse||[]).length,badgeColor:C.cyan},
    {id:"recommendations",icon:"🎯",label:"Portfolio Recs"},
    {id:"portfolio",icon:"💼",label:"Portfolio",badge:portfolioAlerts.length,badgeColor:C.orange},
    {id:"performance",icon:"📊",label:"Performance"},
    {id:"market",icon:"📡",label:"Market"},
    {id:"governance_research",icon:"🧬",label:"Gov Research",badge:(researchFeed||[]).filter(r=>r.debateStatus==="pending").length,badgeColor:C.purple},
    {id:"governance",icon:"🛡",label:"Governance",badge:openAlerts,badgeColor:C.red},
  ];

  return(
    <div style={{fontFamily:"Space Grotesk,system-ui,sans-serif",background:C.bg,color:C.text,height:"100vh",display:"flex",flexDirection:"column",overflow:"hidden"}}>
      <style>{FONT_STYLE}</style>

      {/* TOP BAR */}
      <div style={{background:C.surface,borderBottom:`1px solid ${C.border}`,padding:"0 15px",display:"flex",alignItems:"center",gap:12,height:46,flexShrink:0}}>
        <div style={{display:"flex",alignItems:"center",gap:7}}>
          <div style={{width:26,height:26,borderRadius:6,background:`linear-gradient(135deg,${C.accent},${C.accentDim})`,display:"flex",alignItems:"center",justifyContent:"center",fontSize:13}}>⚡</div>
          <span style={{fontSize:12,fontWeight:800,color:"white",letterSpacing:-.3}}>Bharat Intelligence</span>
          <span style={{fontSize:8,color:C.muted}}>v3.0</span>
        </div>
        <div className="ticker-scroll" style={{flex:1,display:"flex",gap:12,overflowX:"auto",msOverflowStyle:"none",scrollbarWidth:"none"}}>
          {marketPulse.slice(0,6).map(m=>(
            <div key={m.key} style={{display:"flex",gap:4,alignItems:"center",whiteSpace:"nowrap"}}>
              <span style={{fontSize:8,color:C.muted}}>{m.key}</span>
              <span style={{fontSize:9,fontWeight:700,color:"white",fontFamily:"JetBrains Mono"}}>{m.value}</span>
              <span style={{fontSize:8,color:m.up?C.green:C.red}}>{m.change}</span>
            </div>
          ))}
          {marketRegime&&(()=>{
            const regimeColor={BULL:C.green,BEAR:C.red,HIGH_VOLATILITY:C.orange,SIDEWAYS:C.muted}[marketRegime.regime]||C.muted;
            const regimeIcon={BULL:"🟢",BEAR:"🔴",HIGH_VOLATILITY:"🟠",SIDEWAYS:"⚪"}[marketRegime.regime]||"⬜";
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
                <span style={{fontSize:9}}>{regimeIcon}</span>
                <span style={{fontSize:9,fontWeight:700,color:regimeColor}}>{marketRegime.regime}</span>
                <span style={{fontSize:7,color:C.muted}}>{marketRegime.confidence}%</span>
              </div>
            );
          })()}
        </div>
        <div style={{display:"flex",alignItems:"center",gap:7}}>
          {openAlerts>0&&<button onClick={()=>setTab("governance")} style={{background:C.red+"12",border:`1px solid ${C.red}44`,borderRadius:4,padding:"2px 7px",color:C.red,fontSize:9,fontWeight:700,cursor:"pointer"}}>⚠ {openAlerts}</button>}
          {portfolioAlerts.length>0&&<button onClick={()=>setTab("portfolio")} style={{background:C.orange+"12",border:`1px solid ${C.orange}44`,borderRadius:4,padding:"2px 7px",color:C.orange,fontSize:9,fontWeight:700,cursor:"pointer"}}>💼 {portfolioAlerts.length}</button>}
          <div style={{fontSize:8,color:C.green,display:"flex",alignItems:"center",gap:2}}><Dot color={C.green} pulse/>Open</div>
          <span style={{fontSize:8,color:C.muted}}>{now.toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit"})} IST</span>
          <button onClick={()=>setAriaOpen(o=>!o)} style={{background:ariaOpen?`linear-gradient(135deg,${C.purple},${C.blue})`:C.panel,border:`1px solid ${ariaOpen?C.purple+"66":C.borderHi}`,borderRadius:6,padding:"5px 11px",color:"white",fontSize:10,fontWeight:700,cursor:"pointer",boxShadow:ariaOpen?`0 0 12px ${C.purple}44`:"none"}}>{ariaOpen?"✕ ARIA":"✦ ARIA"}</button>
        </div>
      </div>

      {govBanner&&openAlerts>0&&(
        <div style={{background:C.red+"0d",borderBottom:`1px solid ${C.red}33`,padding:"3px 15px",display:"flex",alignItems:"center",gap:8,flexShrink:0}}>
          <span style={{fontSize:9,color:C.red,fontWeight:700}}>⚠ Governance:</span>
          <span style={{fontSize:9,color:C.textDim,flex:1}}>{govAlerts.find(a=>!a.resolved)?.title}</span>
          <button onClick={()=>setTab("governance")} style={{background:C.red+"18",border:`1px solid ${C.red}44`,borderRadius:3,padding:"1px 6px",color:C.red,fontSize:8,cursor:"pointer"}}>View</button>
          <button onClick={()=>setGovBanner(false)} style={{background:"none",border:"none",color:C.muted,cursor:"pointer",fontSize:9}}>✕</button>
        </div>
      )}

      <div style={{flex:1,display:"flex",overflow:"hidden"}}>
        <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
          {/* TABS */}
          <div className="tabs-scroll" style={{
            padding:"0 15px",display:"flex",gap:2,
            background:C.surface,borderBottom:`1px solid ${C.border}`,
            flexShrink:0,overflowX:"auto",overflowY:"visible",
          }}>
            {TABS.map(t=>(
              <button key={t.id} onClick={()=>setTab(t.id)} style={{
                background:tab===t.id?C.panel:"transparent",
                border:`1px solid ${tab===t.id?(t.badgeColor||C.accent)+"44":"transparent"}`,
                borderRadius:"5px 5px 0 0",padding:"6px 10px",
                color:tab===t.id?(t.badgeColor||C.accent):C.muted,
                fontSize:9,fontWeight:tab===t.id?700:400,cursor:"pointer",marginBottom:-1,
                flexShrink:0,whiteSpace:"nowrap",
              }}>
                {t.icon} {t.label}
                {t.badge>0&&<span style={{marginLeft:3,background:t.badgeColor||C.accent,color:t.id==="discovery"?"#0a0a1a":"white",borderRadius:8,padding:"0px 4px",fontSize:8,fontWeight:700}}>{t.badge}</span>}
              </button>
            ))}
          </div>

          <div style={{flex:1,overflowY:"auto",padding:15}}>

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
                onOpenARIA={(type,extra)=>openARIA(type,extra)}
                onOpenRunSummary={IS_LIVE?openARIAWithRun:null}
              />
            )}

            {/* PORTFOLIO RECOMMENDATIONS */}
            {tab==="recommendations"&&(()=>{
              const heldSymbols=new Set((portfolio||[]).filter(h=>h.status==="holding").map(h=>h.symbol));
              const filteredRecs=recsPortfolioFilter&&heldSymbols.size>0
                ?portfolioRecs.filter(r=>heldSymbols.has(r.symbol))
                :portfolioRecs;
              return(
              <div style={{animation:"fadeUp .3s ease"}}>
                <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:12}}>
                  <div>
                    <div style={{fontSize:13,fontWeight:700,color:"white"}}>Stock Recommendations</div>
                    <div style={{fontSize:9,color:C.muted,marginTop:1}}>Agent signals for NSE stocks · Updated daily at 06:00 IST</div>
                  </div>
                  {heldSymbols.size>0&&(
                    <div style={{display:"flex",alignItems:"center",gap:6,background:C.surface,border:`1px solid ${C.border}`,borderRadius:7,padding:"5px 10px"}}>
                      <span style={{fontSize:9,color:C.muted}}>Filter:</span>
                      <button
                        onClick={()=>setRecsPortfolioFilter(false)}
                        style={{background:!recsPortfolioFilter?C.accent+"22":"none",border:`1px solid ${!recsPortfolioFilter?C.accent+"66":"transparent"}`,borderRadius:4,padding:"2px 8px",color:!recsPortfolioFilter?C.accent:C.muted,fontSize:9,cursor:"pointer",fontWeight:!recsPortfolioFilter?700:400}}
                      >All</button>
                      <button
                        onClick={()=>setRecsPortfolioFilter(true)}
                        style={{background:recsPortfolioFilter?C.teal+"22":"none",border:`1px solid ${recsPortfolioFilter?C.teal+"66":"transparent"}`,borderRadius:4,padding:"2px 8px",color:recsPortfolioFilter?C.teal:C.muted,fontSize:9,cursor:"pointer",fontWeight:recsPortfolioFilter?700:400}}
                      >📌 My Holdings</button>
                    </div>
                  )}
                </div>
                {IS_LIVE && apiLoaded && portfolioRecs.length===0 && (
                  <EmptyState icon="🎯" title="No recommendations yet"
                    sub="The orchestrator generates BUY/HOLD/SELL signals daily at 06:00 IST for a rotating set of NSE symbols. If no recs appear, check the Governance tab → Data Source Health to confirm the pipeline ran successfully today." />
                )}
                {recsPortfolioFilter&&heldSymbols.size>0&&filteredRecs.length===0&&portfolioRecs.length>0&&(
                  <EmptyState icon="📌" title="No recommendations for your holdings yet"
                    sub={`The screener cycles through ~200 stocks/day from the NSE universe. Signals for your ${heldSymbols.size} held stocks will appear once the screener reaches them (typically within 1–9 days). Switch to "All" to see all available recommendations.`} />
                )}
                {filteredRecs.length>0&&<div style={{display:"grid",gridTemplateColumns:"300px 1fr",gap:14}}>
                  <div>
                    {filteredRecs.map(r=>{
                      const ac=r.action==="BUY"?C.green:r.action==="SELL"?C.red:C.accent;
                      const inPort=portfolio.some(h=>h.symbol===r.symbol&&h.status==="holding");
                      return(
                        <div key={r.id} onClick={()=>setSelRecId(r.id)} style={{background:selRecId===r.id?C.panel:C.surface,border:`1px solid ${selRecId===r.id?C.accent+"55":C.border}`,borderRadius:8,padding:11,cursor:"pointer",marginBottom:7}}>
                          <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:7}}>
                            <div style={{flex:1}}>
                              <div style={{display:"flex",alignItems:"center",gap:5,marginBottom:3,flexWrap:"wrap"}}>
                                <span style={{fontSize:12,fontWeight:700,color:"white",fontFamily:"JetBrains Mono"}}>{r.symbol}</span>
                                <Tag color={ac}>{r.action}</Tag>
                                {inPort&&<Tag color={C.teal} small>📌 held</Tag>}
                                <span style={{fontSize:9,color:C.muted}}>conf <b style={{color:C.accent}}>{r.confidence}%</b></span>
                                {r.suggestedPositionPct!=null&&r.suggestedPositionPct>0&&(
                                  <span style={{background:C.green+"18",color:C.green,border:`1px solid ${C.green}44`,borderRadius:3,padding:"1px 5px",fontSize:9,fontWeight:700}}>
                                    📐 {r.suggestedPositionPct}%
                                  </span>
                                )}
                              </div>
                              <div style={{fontSize:9,color:C.textDim}}>{r.headline}</div>
                              <div style={{fontSize:8,color:C.muted,marginTop:1}}>Till {r.validTill} · {r.horizon}{r.positionLabel?` · ${r.positionLabel}`:""}</div>
                            </div>
                            <RiskGauge score={r.riskScore}/>
                          </div>
                          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:4,marginBottom:6}}>
                            {[["Entry",r.entry,"white"],["Target",r.target,C.green],["Stop",r.stoploss,C.red]].map(([l,v,col])=>(
                              <div key={l} style={{background:C.bg,borderRadius:3,padding:"3px 6px"}}>
                                <div style={{fontSize:7,color:C.muted,textTransform:"uppercase"}}>{l}</div>
                                <div style={{fontSize:9,fontWeight:700,color:col,fontFamily:"JetBrains Mono"}}>{v}</div>
                              </div>
                            ))}
                          </div>
                          <Bar pct={r.confidence} color={ac}/>
                        </div>
                      );
                    })}
                  </div>
                  {portfolioRecs.find(r=>r.id===selRecId)&&(
                    <div style={{animation:"fadeUp .2s ease"}}>
                      {(()=>{const r=portfolioRecs.find(x=>x.id===selRecId);const icons={technical:"📊",fundamental:"🏭",sentiment:"📰",institutional:"🏛",macro:"🌍",historical:"📜"};const sc=s=>({BUY:C.green,SELL:C.red,NEUTRAL:C.muted,POSITIVE:C.green,"VERY POSITIVE":C.green,HOLD:C.accent}[s]||C.muted);return(<>
                        <div style={{fontSize:12,fontWeight:700,color:"white",marginBottom:2}}>{r.symbol} — Full Analysis</div>
                        <div style={{fontSize:10,color:C.textDim,lineHeight:1.55,marginBottom:11}}>{r.summary}</div>
                        <div style={{fontSize:9,fontWeight:700,color:C.accent,marginBottom:7,textTransform:"uppercase",letterSpacing:1}}>Agent Breakdown</div>
                        <div style={{display:"flex",flexDirection:"column",gap:5}}>
                          {Object.entries(r.agents).map(([k,a])=>(
                            <div key={k} style={{background:C.bg,border:`1px solid ${C.border}`,borderRadius:5,padding:8}}>
                              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:3}}>
                                <span style={{fontSize:9,fontWeight:600,color:"white"}}>{icons[k]} {k.charAt(0).toUpperCase()+k.slice(1)}</span>
                                <div style={{display:"flex",gap:4,alignItems:"center"}}><Tag color={sc(a.signal)} small>{a.signal}</Tag><span style={{fontSize:8,color:C.muted,fontFamily:"JetBrains Mono"}}>{a.score}</span></div>
                              </div>
                              <div style={{fontSize:9,color:C.textDim,lineHeight:1.5,marginBottom:3}}>{a.detail}</div>
                              <Bar pct={a.score}/>
                            </div>
                          ))}
                        </div>
                        <WarrenBotPanel wb={r.warrenBot}/>
                        <ValuationScenariosPanel
                          val={valuationCache[r.symbol]}
                          symbol={r.symbol}
                          onFetch={()=>{
                            apiFetch(`/api/valuation/${encodeURIComponent(r.symbol)}`)
                              .then(d=>{if(d&&!d.error)setValuationCache(prev=>({...prev,[r.symbol]:d}));})
                              .catch(()=>{});
                          }}
                        />
                      </>);})()}
                    </div>
                  )}
                </div>}{/* closes filteredRecs.length>0 wrapper */}
              </div>
              );
            })()}

            {tab==="portfolio"&&<PortfolioTab
              portfolio={portfolio}
              setPortfolio={setPortfolio}
              onOpenARIA={openARIA}
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
                apiLoaded={apiLoaded}
              />
            )}

            {tab==="market"&&(
              <div style={{animation:"fadeUp .3s ease"}}>
                <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:11}}>
                  <div style={{fontSize:12,fontWeight:700,color:"white"}}>Live Market Pulse</div>
                  {IS_LIVE&&(
                    <button onClick={openARIAWithRun} style={{background:C.orange+"22",border:`1px solid ${C.orange}44`,borderRadius:7,padding:"5px 11px",color:C.orange,fontSize:10,fontWeight:700,cursor:"pointer"}}>
                      📊 What ran today?
                    </button>
                  )}
                </div>
                <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:8,marginBottom:14}}>
                  {marketPulse.map(m=>(
                    <div key={m.key} style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:6,padding:9}}>
                      <div style={{fontSize:7,color:C.muted,marginBottom:2,textTransform:"uppercase"}}>{m.key}</div>
                      <div style={{fontSize:14,fontWeight:800,color:"white",fontFamily:"JetBrains Mono"}}>{m.value}</div>
                      <div style={{fontSize:8,color:m.up?C.green:C.red,marginTop:2}}>{m.change}</div>
                    </div>
                  ))}
                </div>
                {/* DB-7: India Market News Feed */}
                <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:8}}>
                  <div style={{fontSize:11,fontWeight:700,color:"white"}}>📰 India Market News</div>
                  <div style={{display:"flex",gap:5,alignItems:"center"}}>
                    {["NIFTY","SENSEX","FII","RBI","BUDGET"].map(sym=>(
                      <button key={sym} onClick={()=>{
                        setNewsSymbol(sym);
                        apiFetch(`/api/news/${sym}`)
                          .then(d=>{if(d?.news)setMarketNews(d.news);})
                          .catch(()=>{});
                      }} style={{
                        background:newsSymbol===sym?C.accent+"22":"none",
                        border:`1px solid ${newsSymbol===sym?C.accent+"55":C.border}`,
                        borderRadius:4,padding:"2px 8px",
                        color:newsSymbol===sym?C.accent:C.muted,
                        fontSize:9,cursor:"pointer",fontWeight:newsSymbol===sym?700:400,
                      }}>{sym}</button>
                    ))}
                  </div>
                </div>
                {marketNews.length>0?(
                  <div style={{display:"flex",flexDirection:"column",gap:6}}>
                    {marketNews.map((item,i)=>(
                      <a key={i} href={item.url} target="_blank" rel="noreferrer" style={{textDecoration:"none"}}>
                        <div style={{
                          background:C.surface,border:`1px solid ${C.border}`,borderRadius:7,
                          padding:"9px 12px",cursor:"pointer",
                          transition:"border-color 0.15s",
                        }}
                          onMouseEnter={e=>e.currentTarget.style.borderColor=C.accent+"55"}
                          onMouseLeave={e=>e.currentTarget.style.borderColor=C.border}
                        >
                          <div style={{fontSize:11,color:"white",lineHeight:1.4,marginBottom:4}}>{item.title}</div>
                          <div style={{display:"flex",gap:8,alignItems:"center"}}>
                            <span style={{fontSize:8,color:C.muted}}>{item.source}</span>
                            {item.published&&<span style={{fontSize:8,color:C.muted}}>· {item.published}</span>}
                            <span style={{fontSize:8,color:C.accent,marginLeft:"auto"}}>↗ Read</span>
                          </div>
                        </div>
                      </a>
                    ))}
                  </div>
                ):(
                  <EmptyState icon="📰" title="No news loaded yet"
                    sub={IS_LIVE?"Click a topic above to fetch headlines from Google News.":"News feed requires a live backend connection."} />
                )}
              </div>
            )}

            {tab==="governance_research"&&<GovernanceResearchTab onOpenARIA={openARIA} researchFeed={researchFeed} apiLoaded={apiLoaded}/>}

            {tab==="governance"&&(
              <div style={{animation:"fadeUp .3s ease"}}>
                <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:14}}>
                  <GovShield size={18}/><div style={{fontSize:13,fontWeight:700,color:"white"}}>Governance Engine</div>
                  <div style={{marginLeft:"auto",background:C.green+"12",border:`1px solid ${C.green}33`,borderRadius:5,padding:"3px 8px",display:"flex",alignItems:"center",gap:4}}><Dot color={C.green} pulse/><span style={{fontSize:9,color:C.green,fontWeight:600}}>Running</span></div>
                </div>

                {/* Data source health — shown first so issues requiring manual action are obvious */}
                <SystemHealthPanel health={systemHealth}/>

                {govAlerts.length>0 ? (
                  <div style={{display:"flex",flexDirection:"column",gap:7,marginBottom:14}}>
                    {govAlerts.map(a=>{const sc={critical:C.red,warning:C.accent,info:C.blue}[a.severity]||C.muted;return(
                      <div key={a.id} style={{background:C.surface,border:`1px solid ${sc}33`,borderRadius:7,padding:11}}>
                        <div style={{display:"flex",justifyContent:"space-between",marginBottom:5}}><div style={{display:"flex",gap:5,alignItems:"center"}}><Tag color={sc}>{(a.severity||"info").toUpperCase()}</Tag><span style={{fontSize:9,color:C.muted}}>{a.module} · {a.time}</span></div><Tag color={a.resolved?C.green:C.red}>{a.resolved?"RESOLVED":"OPEN"}</Tag></div>
                        <div style={{fontSize:11,fontWeight:600,color:"white",marginBottom:3}}>{a.title}</div>
                        <div style={{fontSize:9,color:C.textDim,lineHeight:1.6,marginBottom:4}}>{a.detail}</div>
                        {a.action&&<div style={{fontSize:8,color:sc,background:sc+"0f",borderRadius:3,padding:"2px 6px"}}>⚡ {a.action}</div>}
                      </div>
                    );})}
                  </div>
                ) : (
                  <div style={{background:C.green+"0a",border:`1px solid ${C.green}22`,borderRadius:7,padding:"10px 14px",marginBottom:14,display:"flex",alignItems:"center",gap:8}}>
                    <Dot color={C.green}/>
                    <span style={{fontSize:10,color:C.green}}>✅ All systems nominal — no governance alerts</span>
                  </div>
                )}
                <div style={{fontSize:11,fontWeight:700,color:C.accent,marginBottom:8}}>Enhancement Proposals</div>
                <EmptyState icon="💡" title="No enhancement proposals yet"
                  sub="Enhancement proposals are surfaced by the governance layer after reviewing agent performance trends. They will appear here once the scheduler has run for several days." />
              </div>
            )}
          </div>
        </div>

        {ariaOpen&&(
          <div style={{width:340,borderLeft:`1px solid ${C.border}`,flexShrink:0,display:"flex",flexDirection:"column",animation:"slideIn .2s ease"}}>
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
    </div>
  );
}
