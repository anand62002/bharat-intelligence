"""
seed_historical_events.py
Inserts 50 key NSE/India-market historical events into Supabase.

Usage:
    SUPABASE_URL=https://xxx.supabase.co \
    SUPABASE_SERVICE_KEY=eyJ... \
    python db/seed_historical_events.py

Requires:
    pip install supabase
    (optional, for embeddings) pip install openai
"""

import os
import sys
from datetime import date

# ---------------------------------------------------------------------------
# 50 curated NSE / macro events
# ---------------------------------------------------------------------------
EVENTS = [
    # ── GLOBAL CRISES ───────────────────────────────────────────────────────
    {
        "event_type": "GLOBAL",
        "description": (
            "Lehman Brothers collapse triggers global financial crisis. "
            "FII outflows from India exceed ₹52,000 Cr; Sensex falls ~60% peak-to-trough."
        ),
        "event_date": date(2008, 9, 15),
        "affected_sectors": ["BANKING", "REALTY", "INFRASTRUCTURE", "METALS", "IT"],
        "market_impact": "SEVERE_NEGATIVE",
        "outcome": (
            "Nifty bottomed at ~2,250 in Mar 2009; recovered fully by late 2010. "
            "RBI cut rates aggressively; fiscal stimulus package announced."
        ),
    },
    {
        "event_type": "GLOBAL",
        "description": (
            "US Fed taper tantrum: Ben Bernanke hints at QE tapering. "
            "INR crashes to 68/USD; FII debt outflows ₹45,000 Cr in weeks."
        ),
        "event_date": date(2013, 5, 22),
        "affected_sectors": ["BANKING", "NBFC", "REALTY", "CONSUMER"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "RBI raised rates; INR stabilised by end-2013. "
            "Nifty recovered to new highs by May 2014 post election results."
        ),
    },
    {
        "event_type": "GLOBAL",
        "description": (
            "COVID-19 pandemic declared; India announces 21-day lockdown. "
            "Nifty crashes 38% in 40 trading sessions — fastest bear market ever."
        ),
        "event_date": date(2020, 3, 23),
        "affected_sectors": [
            "AVIATION", "HOSPITALITY", "RETAIL", "REALTY", "AUTO", "BANKING",
        ],
        "market_impact": "SEVERE_NEGATIVE",
        "outcome": (
            "Nifty hit all-time low of 7,511 on 23 Mar 2020. "
            "RBI cut repo to 4%; TLTRO, moratorium announced. "
            "Full recovery and new ATH by Nov 2020; Nifty doubled by Oct 2021."
        ),
    },
    {
        "event_type": "GLOBAL",
        "description": (
            "US Fed begins aggressive rate-hike cycle (+75 bps) to fight 40-year-high inflation. "
            "Global equity sell-off; FII outflows from India ₹2.8 lakh Cr in 2022."
        ),
        "event_date": date(2022, 6, 15),
        "affected_sectors": ["IT", "FINTECH", "STARTUPS", "REALTY", "CONSUMER"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Nifty corrected ~17% (Oct 2021–Jun 2022). "
            "India outperformed most EMs; recovered to new ATH by Dec 2022."
        ),
    },
    # ── DOMESTIC CRISES ─────────────────────────────────────────────────────
    {
        "event_type": "CRISIS",
        "description": (
            "IL&FS defaults on CP and NCDs; ₹91,000 Cr debt unravels. "
            "Triggers systemic NBFC liquidity crisis; mutual fund redemption pressure."
        ),
        "event_date": date(2018, 9, 21),
        "affected_sectors": ["NBFC", "BANKING", "REALTY", "INFRASTRUCTURE"],
        "market_impact": "SEVERE_NEGATIVE",
        "outcome": (
            "DHFL, ADAG group, Yes Bank subsequently stressed. "
            "RBI opened liquidity windows; government superseded IL&FS board. "
            "NBFC sector took 18+ months to stabilise."
        ),
    },
    {
        "event_type": "CRISIS",
        "description": (
            "Yes Bank placed under moratorium; RBI caps withdrawals at ₹50,000. "
            "SBI-led rescue plan executed within weeks."
        ),
        "event_date": date(2020, 3, 5),
        "affected_sectors": ["BANKING", "NBFC", "FINTECH"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "SBI acquired 49% stake; moratorium lifted in 3 weeks. "
            "Yes Bank stock fell 85%; took years to partially recover."
        ),
    },
    {
        "event_type": "CRISIS",
        "description": (
            "Demonetisation: PM Modi announces withdrawal of ₹500 & ₹1,000 notes, "
            "invalidating 86% of currency in circulation overnight."
        ),
        "event_date": date(2016, 11, 8),
        "affected_sectors": ["BANKING", "CONSUMER", "REALTY", "SME", "GOLD"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Nifty fell ~6% in first week; recovered by Dec 2016. "
            "Digital payments (Paytm, UPI) surged. "
            "Real estate and informal economy saw prolonged slowdown."
        ),
    },
    {
        "event_type": "CRISIS",
        "description": (
            "SEBI bans P-Note investments for overseas entities without KYC. "
            "FII panic triggers Sensex single-day crash of 1,744 pts (then record)."
        ),
        "event_date": date(2007, 10, 17),
        "affected_sectors": ["BANKING", "REALTY", "METALS", "IT"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": "Market recovered within 2 weeks; P-Note rules clarified.",
    },
    {
        "event_type": "CRISIS",
        "description": (
            "Franklin Templeton India winds up 6 debt mutual fund schemes "
            "citing liquidity crunch; ₹28,000 Cr investor money locked."
        ),
        "event_date": date(2020, 4, 23),
        "affected_sectors": ["NBFC", "DEBT_MARKETS"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "SEBI initiated investigation; SC-supervised repayment returned "
            "~₹25,000 Cr to investors by 2023."
        ),
    },
    # ── UNION BUDGETS ────────────────────────────────────────────────────────
    {
        "event_type": "BUDGET",
        "description": (
            "Union Budget 2018: LTCG tax of 10% re-introduced on equity gains >₹1 lakh. "
            "First time in 14 years; Sensex fell 840 pts on budget day."
        ),
        "event_date": date(2018, 2, 1),
        "affected_sectors": ["EQUITY_MARKETS", "BANKING", "CONSUMER"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": "Markets absorbed LTCG; index continued uptrend through mid-2018.",
    },
    {
        "event_type": "BUDGET",
        "description": (
            "Union Budget 2019 (interim): No major tax changes; "
            "₹6,000/yr PM-KISAN income support for farmers announced."
        ),
        "event_date": date(2019, 2, 1),
        "affected_sectors": ["AGRI", "RURAL_CONSUMER", "FMCG"],
        "market_impact": "MILD_POSITIVE",
        "outcome": "Rural FMCG stocks outperformed in H1 2019.",
    },
    {
        "event_type": "BUDGET",
        "description": (
            "Union Budget 2019 (full): Super-rich surcharge on FPIs treated as trusts; "
            "FII outflows ₹14,000 Cr in weeks. Market fell ~9% post budget."
        ),
        "event_date": date(2019, 7, 5),
        "affected_sectors": ["EQUITY_MARKETS", "BANKING", "IT"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": "Surcharge on FPIs rolled back in Sep 2019; markets rebounded sharply.",
    },
    {
        "event_type": "BUDGET",
        "description": (
            "Union Budget 2020: New optional income tax regime with lower rates "
            "but without exemptions introduced. DDT abolished."
        ),
        "event_date": date(2020, 2, 1),
        "affected_sectors": ["BANKING", "INSURANCE", "CONSUMER"],
        "market_impact": "NEUTRAL",
        "outcome": "Budget overshadowed by COVID-19 within weeks.",
    },
    {
        "event_type": "BUDGET",
        "description": (
            "Union Budget 2021: No income tax hike despite COVID stress; "
            "capex target doubled to ₹5.54 lakh Cr; privatisation of PSU banks & LIC announced."
        ),
        "event_date": date(2021, 2, 1),
        "affected_sectors": [
            "INFRASTRUCTURE", "DEFENCE", "PSU_BANKS", "INSURANCE", "METALS",
        ],
        "market_impact": "STRONG_POSITIVE",
        "outcome": "Sensex rallied 5% on budget day — one of the best budget-day reactions.",
    },
    {
        "event_type": "BUDGET",
        "description": (
            "Union Budget 2022: Capex raised to ₹7.5 lakh Cr (+35%); "
            "crypto taxed at 30% flat + 1% TDS; no change in income tax slabs."
        ),
        "event_date": date(2022, 2, 1),
        "affected_sectors": [
            "INFRASTRUCTURE", "DEFENCE", "RAILWAYS", "CRYPTO", "CEMENT",
        ],
        "market_impact": "MILD_POSITIVE",
        "outcome": "Infra stocks rallied; crypto exchanges saw volume drop post TDS.",
    },
    {
        "event_type": "BUDGET",
        "description": (
            "Union Budget 2023: New tax regime made default; capex at ₹10 lakh Cr; "
            "outlay for Railways ₹2.4 lakh Cr (highest ever); no change in LTCG/STCG."
        ),
        "event_date": date(2023, 2, 1),
        "affected_sectors": [
            "RAILWAYS", "INFRASTRUCTURE", "DEFENCE", "HOUSING", "CONSUMER",
        ],
        "market_impact": "MILD_POSITIVE",
        "outcome": "Consumption stocks dipped; infra & defence rallied strongly.",
    },
    {
        "event_type": "BUDGET",
        "description": (
            "Union Budget 2024 (interim): Fiscal deficit target 5.1% of GDP; "
            "no populist giveaways; capex maintained at ₹11.1 lakh Cr."
        ),
        "event_date": date(2024, 2, 1),
        "affected_sectors": ["INFRASTRUCTURE", "DEFENCE", "RAILWAYS"],
        "market_impact": "NEUTRAL",
        "outcome": "Markets largely flat; focus shifted to general election outcome.",
    },
    {
        "event_type": "BUDGET",
        "description": (
            "Union Budget 2024 (full): LTCG raised to 12.5% (from 10%); "
            "STCG raised to 20% (from 15%); STT on F&O doubled. "
            "Fiscal deficit target 4.9% of GDP."
        ),
        "event_date": date(2024, 7, 23),
        "affected_sectors": ["EQUITY_MARKETS", "DERIVATIVES", "BANKING", "CONSUMER"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": "Nifty fell ~1.5% on budget day; F&O volumes declined ~15% over next quarter.",
    },
    {
        "event_type": "BUDGET",
        "description": (
            "Union Budget 2025: Income tax exemption limit raised to ₹12 lakh; "
            "capex at ₹11.2 lakh Cr; MSME credit guarantee enhanced."
        ),
        "event_date": date(2025, 2, 1),
        "affected_sectors": ["CONSUMER", "FMCG", "AUTO", "HOUSING", "MSME"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": "Consumer and auto stocks rallied; broad market positive.",
    },
    # ── RBI POLICY / RATE CYCLES ─────────────────────────────────────────────
    {
        "event_type": "RBI_POLICY",
        "description": (
            "RBI begins rate-cut cycle: repo cut to 6.25% (first cut in 5 years) "
            "amid slowing growth and benign inflation."
        ),
        "event_date": date(2019, 2, 7),
        "affected_sectors": ["BANKING", "NBFC", "REALTY", "AUTO"],
        "market_impact": "MILD_POSITIVE",
        "outcome": "Repo eventually cut to 4% by May 2020; transmission remained sluggish.",
    },
    {
        "event_type": "RBI_POLICY",
        "description": (
            "Emergency off-cycle rate cut of 75 bps to 4.4%; moratorium on loans "
            "for 3 months announced in response to COVID-19 lockdown."
        ),
        "event_date": date(2020, 3, 27),
        "affected_sectors": ["BANKING", "NBFC", "REALTY", "AUTO", "CONSUMER"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": "Liquidity injection ₹3.74 lakh Cr; mortgage EMI freeze averted NPA spike.",
    },
    {
        "event_type": "RBI_POLICY",
        "description": (
            "RBI begins rate-hike cycle; off-cycle 40 bps hike to 4.4% repo "
            "amid surging inflation (CPI >7%)."
        ),
        "event_date": date(2022, 5, 4),
        "affected_sectors": ["BANKING", "REALTY", "NBFC", "AUTO"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Repo hiked from 4% to 6.5% over 8 meetings (May 2022–Feb 2023). "
            "Home loan rates rose 2.5%; realty stocks fell 20% in cycle."
        ),
    },
    {
        "event_type": "RBI_POLICY",
        "description": (
            "RBI pauses rate hikes at 6.5%; shifts stance to 'withdrawal of accommodation'. "
            "Signals data-dependent approach amid sticky core inflation."
        ),
        "event_date": date(2023, 4, 6),
        "affected_sectors": ["BANKING", "REALTY", "CONSUMER"],
        "market_impact": "MILD_POSITIVE",
        "outcome": "Banking stocks rallied; rate-sensitive sectors stabilised.",
    },
    {
        "event_type": "RBI_POLICY",
        "description": (
            "RBI introduces Prompt Corrective Action (PCA) framework revision, "
            "placing multiple PSU banks under restrictions on lending and dividends."
        ),
        "event_date": date(2017, 12, 15),
        "affected_sectors": ["PSU_BANKS"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "IDBI, IOB, Central Bank among banks under PCA. "
            "Capital infusion by government; most banks exited PCA by 2021."
        ),
    },
    {
        "event_type": "RBI_POLICY",
        "description": (
            "RBI demonetisation operational: banks flooded with ₹15.4 lakh Cr deposits; "
            "CRR requirement hiked 100% on incremental deposits to absorb liquidity."
        ),
        "event_date": date(2016, 11, 26),
        "affected_sectors": ["BANKING", "PAYMENTS"],
        "market_impact": "MIXED",
        "outcome": "Bank CASA ratios improved short-term; NIM compressed; CASA normalised by Q2 FY18.",
    },
    # ── SEBI CIRCULARS / REGULATION ──────────────────────────────────────────
    {
        "event_type": "REGULATION",
        "description": (
            "SEBI mandates T+1 settlement for top 100 stocks by market cap "
            "effective Jan 2023; extended to all listed securities by Jan 2023."
        ),
        "event_date": date(2022, 1, 1),
        "affected_sectors": ["EQUITY_MARKETS", "BROKERS", "CLEARING"],
        "market_impact": "MILD_POSITIVE",
        "outcome": "India became first major market to adopt T+1 broadly; FPI concerns resolved.",
    },
    {
        "event_type": "REGULATION",
        "description": (
            "SEBI's new peak margin norms: traders must maintain margin throughout the day; "
            "intraday leverage sharply reduced across all segments."
        ),
        "event_date": date(2021, 9, 1),
        "affected_sectors": ["BROKERS", "EQUITY_MARKETS", "DERIVATIVES"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Zerodha, Groww reported 30–50% drop in intraday volumes initially. "
            "Market structure shifted toward delivery-based trading."
        ),
    },
    {
        "event_type": "REGULATION",
        "description": (
            "SEBI bans entry load on mutual funds, mandating advisory fees be paid "
            "directly by investors; revolutionises distribution economics."
        ),
        "event_date": date(2009, 8, 1),
        "affected_sectors": ["ASSET_MANAGEMENT", "DISTRIBUTION"],
        "market_impact": "LONG_TERM_POSITIVE",
        "outcome": "MF AUM grew from ₹5 lakh Cr (2009) to ₹50+ lakh Cr (2024).",
    },
    {
        "event_type": "REGULATION",
        "description": (
            "SEBI circular on multi-cap fund re-categorisation: funds must hold "
            "minimum 25% each in large/mid/small caps; massive rebalancing forced."
        ),
        "event_date": date(2020, 9, 11),
        "affected_sectors": ["SMALL_CAP", "MID_CAP", "ASSET_MANAGEMENT"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": "Small-cap index rallied ~30% in 3 months as funds were forced to buy.",
    },
    {
        "event_type": "REGULATION",
        "description": (
            "SEBI bans unsolicited SMS/call-based tips and requires registration "
            "of investment advisors; cracks down on tip-provider ecosystem."
        ),
        "event_date": date(2016, 7, 1),
        "affected_sectors": ["EQUITY_MARKETS"],
        "market_impact": "NEUTRAL",
        "outcome": "Over 1,000 unregistered advisors penalised; SEBI IA regulations strengthened.",
    },
    {
        "event_type": "REGULATION",
        "description": (
            "SEBI issues new F&O rules: lot sizes increased, weekly contracts "
            "restricted to 1 index per exchange, upfront premium collection mandated."
        ),
        "event_date": date(2024, 10, 1),
        "affected_sectors": ["DERIVATIVES", "BROKERS"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": "F&O turnover fell ~40% post implementation; NSE revenue impacted.",
    },
    # ── INDEX RECONSTITUTIONS ────────────────────────────────────────────────
    {
        "event_type": "INDEX_EVENT",
        "description": (
            "Nifty 50 reconstitution: Shree Cement, SBI Life added; "
            "Bharti Infratel, Vedanta removed — major passive fund rebalancing."
        ),
        "event_date": date(2020, 9, 25),
        "affected_sectors": ["CEMENT", "INSURANCE", "TELECOM", "METALS"],
        "market_impact": "MIXED",
        "outcome": "Shree Cement +8%; Bharti Infratel -5% around effective date.",
    },
    {
        "event_type": "INDEX_EVENT",
        "description": (
            "MSCI India weight increased in MSCI Emerging Markets Index; "
            "estimated FII inflow ₹15,000–20,000 Cr required from passive trackers."
        ),
        "event_date": date(2023, 8, 31),
        "affected_sectors": [
            "BANKING", "IT", "CONSUMER", "RELIANCE",
        ],
        "market_impact": "MILD_POSITIVE",
        "outcome": "Nifty rose ~2% in weeks ahead of rebalancing; FII flows positive.",
    },
    {
        "event_type": "INDEX_EVENT",
        "description": (
            "BSE introduces Nifty 500 as the broadest broad-market index; "
            "followed by AMFI launching Nifty Midcap 150 and Smallcap 250 as standard benchmarks."
        ),
        "event_date": date(2019, 4, 1),
        "affected_sectors": ["SMALL_CAP", "MID_CAP"],
        "market_impact": "MILD_POSITIVE",
        "outcome": "Benchmarking revolution; small/mid-cap fund launches accelerated.",
    },
    # ── GOVERNMENT POLICY / STRUCTURAL ───────────────────────────────────────
    {
        "event_type": "POLICY",
        "description": (
            "GST rollout: India transitions to unified Goods & Services Tax, "
            "replacing ~17 indirect taxes; implemented midnight 1 Jul 2017."
        ),
        "event_date": date(2017, 7, 1),
        "affected_sectors": [
            "FMCG", "AUTO", "LOGISTICS", "SME", "CEMENT", "CONSUMER",
        ],
        "market_impact": "MIXED",
        "outcome": (
            "Short-term: channel destocking, SME disruption. "
            "Long-term: formalisation, logistics efficiency; GST collections crossed ₹2 lakh Cr/month by 2024."
        ),
    },
    {
        "event_type": "POLICY",
        "description": (
            "Corporate tax rate cut: Base rate slashed to 22% (from 30%); "
            "new manufacturing companies get 15% rate. "
            "Fiscal cost ~₹1.45 lakh Cr."
        ),
        "event_date": date(2019, 9, 20),
        "affected_sectors": ["MANUFACTURING", "AUTO", "METALS", "CONSUMER"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Nifty surged 5.3% on day — largest single-day gain in a decade. "
            "EPS upgrades of 10–25% across sectors; FDI in manufacturing accelerated."
        ),
    },
    {
        "event_type": "POLICY",
        "description": (
            "IBC (Insolvency & Bankruptcy Code) enacted; provides time-bound "
            "resolution of corporate insolvencies replacing old BIFR process."
        ),
        "event_date": date(2016, 5, 28),
        "affected_sectors": ["BANKING", "NBFC", "INFRASTRUCTURE", "STEEL"],
        "market_impact": "LONG_TERM_POSITIVE",
        "outcome": (
            "₹3+ lakh Cr recovered by banks from IBC resolutions by 2024. "
            "Arcelor-Mittal acquired Essar Steel; improved bank NPA trajectory."
        ),
    },
    {
        "event_type": "POLICY",
        "description": (
            "PLI (Production Linked Incentive) scheme announced across 13 sectors "
            "with total outlay of ₹1.97 lakh Cr over 5 years."
        ),
        "event_date": date(2020, 11, 11),
        "affected_sectors": [
            "PHARMA", "ELECTRONICS", "AUTO", "TELECOM", "TEXTILE", "FOOD_PROCESSING",
        ],
        "market_impact": "STRONG_POSITIVE",
        "outcome": "₹1+ lakh Cr investments committed; electronics exports grew 50%+ by FY24.",
    },
    {
        "event_type": "POLICY",
        "description": (
            "UPI launch by NPCI: Unified Payments Interface goes live with 21 banks, "
            "enabling real-time inter-bank transfers via mobile."
        ),
        "event_date": date(2016, 8, 25),
        "affected_sectors": ["FINTECH", "BANKING", "PAYMENTS"],
        "market_impact": "LONG_TERM_POSITIVE",
        "outcome": "UPI transactions reached 15 billion/month by 2024; India leads global digital payments.",
    },
    {
        "event_type": "POLICY",
        "description": (
            "RERA (Real Estate Regulatory Authority) comes into effect; "
            "mandates project registration, escrow accounts, delivery timelines."
        ),
        "event_date": date(2017, 5, 1),
        "affected_sectors": ["REALTY", "CEMENT", "STEEL"],
        "market_impact": "MIXED",
        "outcome": (
            "Short-term consolidation; unorganised players exited. "
            "Listed realty companies gained market share; sector consolidated."
        ),
    },
    {
        "event_type": "POLICY",
        "description": (
            "National Monetisation Pipeline (NMP) announced: ₹6 lakh Cr asset "
            "monetisation over 4 years across highways, railways, pipelines."
        ),
        "event_date": date(2021, 8, 23),
        "affected_sectors": [
            "INFRASTRUCTURE", "REALTY", "LOGISTICS", "ENERGY",
        ],
        "market_impact": "MILD_POSITIVE",
        "outcome": "Target partially met; highway TOT and InvIT structures activated.",
    },
    # ── ELECTION / POLITICAL EVENTS ──────────────────────────────────────────
    {
        "event_type": "POLITICAL",
        "description": (
            "NDA wins 2014 general elections with absolute majority (282 seats); "
            "markets celebrate 'acche din' narrative and reform expectations."
        ),
        "event_date": date(2014, 5, 16),
        "affected_sectors": ["PSU", "INFRASTRUCTURE", "DEFENCE", "BANKING"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": "Sensex rose 6% on result day; sustained bull run through 2017.",
    },
    {
        "event_type": "POLITICAL",
        "description": (
            "2019 General Elections: NDA returns with even larger majority (303 seats). "
            "Exit polls correctly predicted outcome."
        ),
        "event_date": date(2019, 5, 23),
        "affected_sectors": ["PSU", "DEFENCE", "INFRASTRUCTURE"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": "Nifty hit new ATH on 3 Jun 2019; then FPI surcharge budget triggered correction.",
    },
    {
        "event_type": "POLITICAL",
        "description": (
            "2024 General Elections: NDA wins but with reduced majority (240 seats vs 303 expected); "
            "exit polls overestimated BJP majority."
        ),
        "event_date": date(2024, 6, 4),
        "affected_sectors": ["PSU", "DEFENCE", "INFRASTRUCTURE", "CONSUMER"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Nifty fell 4.7% on result day — single-day wipe-out of ₹30 lakh Cr market cap. "
            "PSU stocks fell 10–20%. Market recovered within 2 weeks."
        ),
    },
    # ── SECTOR-SPECIFIC SHOCKS ───────────────────────────────────────────────
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "Satyam Computer accounting fraud revealed by founder Ramalinga Raju; "
            "₹7,136 Cr falsified cash on books."
        ),
        "event_date": date(2009, 1, 7),
        "affected_sectors": ["IT"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Satyam delisted; acquired by Tech Mahindra for ₹1,756 Cr. "
            "SEBI/ICAI tightened audit standards; sector recovered rapidly."
        ),
    },
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "Adani Group stocks crash 20–60% after Hindenburg Research short-seller report "
            "alleges stock manipulation and accounting fraud across group companies."
        ),
        "event_date": date(2023, 1, 25),
        "affected_sectors": [
            "ADANI_GROUP", "PORTS", "AIRPORTS", "ENERGY", "CEMENT",
        ],
        "market_impact": "SEVERE_NEGATIVE",
        "outcome": (
            "Adani Enterprises FPO pulled; group raised $2.5 Bn from GQG Partners (Mar 2023). "
            "Supreme Court committee found no regulatory failure; stocks partially recovered."
        ),
    },
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "Crude oil surges to $147/barrel (all-time high); India's import bill balloons, "
            "CAD widens, OMC under-recoveries exceed ₹2 lakh Cr."
        ),
        "event_date": date(2008, 7, 11),
        "affected_sectors": ["OIL_GAS", "AVIATION", "PAINTS", "CHEMICALS", "TYRE"],
        "market_impact": "SEVERE_NEGATIVE",
        "outcome": "Oil retreated to $35 by Dec 2008 post-Lehman; OMC losses crystallised.",
    },
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "China's surprise yuan devaluation triggers EM sell-off; "
            "India rupee weakens, FII outflows pressure midcaps."
        ),
        "event_date": date(2015, 8, 24),
        "affected_sectors": ["METALS", "EXPORT", "CHEMICALS", "IT"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": "Nifty fell ~7% in Aug 2015; recovered by Dec 2015.",
    },
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "Pharma sector USFDA import alerts: Sun Pharma Halol plant, Ranbaxy multiple plants "
            "face warning letters; US generic approvals freeze for top Indian pharma."
        ),
        "event_date": date(2015, 3, 1),
        "affected_sectors": ["PHARMA"],
        "market_impact": "SECTOR_NEGATIVE",
        "outcome": (
            "Nifty Pharma index underperformed Nifty by 40% over 2015–2018. "
            "Remediation investment >$500 Mn across industry; quality compliance improved."
        ),
    },
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "COVID-19 pharma boom: India becomes 'pharmacy of the world' narrative; "
            "API, vaccine, generic exports surge. Nifty Pharma outperforms by 60% in FY21."
        ),
        "event_date": date(2020, 4, 1),
        "affected_sectors": ["PHARMA", "DIAGNOSTICS", "HOSPITALS"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": "Sun, Dr Reddy's, Divi's hit ATH; sector re-rated on supply-chain diversification theme.",
    },
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "2G spectrum allocation scam exposed by CAG (₹1.76 lakh Cr notional loss); "
            "SC cancels 122 telecom licences in Feb 2012."
        ),
        "event_date": date(2011, 11, 16),
        "affected_sectors": ["TELECOM"],
        "market_impact": "SECTOR_NEGATIVE",
        "outcome": (
            "Unitech, Loop, S-Tel exit telecom. "
            "Sector consolidation accelerated; eventual Jio disruption in 2016."
        ),
    },
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "Reliance Jio launch with free voice and near-free data disrupts telecom; "
            "ARPU for incumbents collapses 60–70% within 12 months."
        ),
        "event_date": date(2016, 9, 5),
        "affected_sectors": ["TELECOM", "MEDIA", "DTH", "TOWER"],
        "market_impact": "SEVERE_SECTOR_DISRUPTION",
        "outcome": (
            "Airtel, Idea/Voda stocks lost 60–80% over 3 years. "
            "Vodafone India AGR dues (₹58,254 Cr) nearly bankrupted the company."
        ),
    },
]

# ---------------------------------------------------------------------------
# Supabase insert logic
# ---------------------------------------------------------------------------

def get_supabase_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print(
            "ERROR: Set SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables.",
            file=sys.stderr,
        )
        sys.exit(1)
    from supabase import create_client
    return create_client(url, key)


def serialize_event(ev: dict) -> dict:
    """Convert Python date to ISO string and ensure correct types."""
    return {
        "event_type":       ev["event_type"],
        "description":      ev["description"],
        "event_date":       ev["event_date"].isoformat(),
        "affected_sectors": ev.get("affected_sectors", []),
        "market_impact":    ev.get("market_impact"),
        "outcome":          ev.get("outcome"),
        "embedding":        None,   # populate separately via embed_events.py
    }


def seed(dry_run: bool = False):
    print(f"Preparing to seed {len(EVENTS)} historical events …")

    if dry_run:
        for i, ev in enumerate(EVENTS, 1):
            print(f"  [{i:02d}] {ev['event_date']} | {ev['event_type']:20s} | {ev['description'][:70]}")
        print("\nDry-run complete — no data written.")
        return

    client = get_supabase_client()

    rows = [serialize_event(ev) for ev in EVENTS]

    # Upsert in batches of 20 to stay within Supabase request size limits
    batch_size = 20
    inserted = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        resp = (
            client.table("historical_events")
            .insert(batch)
            .execute()
        )
        inserted += len(batch)
        print(f"  Inserted batch {start // batch_size + 1}: {len(batch)} rows")

    print(f"\nDone — {inserted} events seeded into historical_events.")


# ---------------------------------------------------------------------------
# Optional: generate OpenAI embeddings and update the table
# ---------------------------------------------------------------------------

def embed_events():
    """
    Generates 1536-dim embeddings using text-embedding-3-small
    and updates each row in historical_events.

    Requires: pip install openai
    Env var:  OPENAI_API_KEY
    """
    import openai  # noqa: PLC0415

    client_sb = get_supabase_client()
    oai = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    # Fetch all rows without embeddings
    resp = (
        client_sb.table("historical_events")
        .select("id, description, event_date, event_type")
        .is_("embedding", "null")
        .execute()
    )
    rows = resp.data
    if not rows:
        print("No rows without embeddings found.")
        return

    print(f"Generating embeddings for {len(rows)} rows …")
    for row in rows:
        text = f"{row['event_type']} {row['event_date']}: {row['description']}"
        emb_resp = oai.embeddings.create(model="text-embedding-3-small", input=text)
        vector = emb_resp.data[0].embedding  # list[float] of length 1536

        client_sb.table("historical_events").update(
            {"embedding": vector}
        ).eq("id", row["id"]).execute()
        print(f"  Embedded: {row['event_date']} {row['event_type'][:30]}")

    print("Embedding update complete.")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Seed historical_events table")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print events without writing to Supabase",
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        help="Generate and store OpenAI embeddings after seeding",
    )
    args = parser.parse_args()

    seed(dry_run=args.dry_run)

    if args.embed and not args.dry_run:
        embed_events()
