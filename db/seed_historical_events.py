"""
seed_historical_events.py
Inserts 50 key NSE/India-market historical events into Supabase.

Usage:
    SUPABASE_URL=https://xxx.supabase.co \
    SUPABASE_SERVICE_KEY=eyJ... \
    python db/seed_historical_events.py

Requires:
    pip install "supabase==2.10.0" python-dotenv
    (optional, for embeddings) pip install openai
"""

import os
import sys
from datetime import date

from dotenv import load_dotenv
load_dotenv()  # loads SUPABASE_URL, SUPABASE_SERVICE_KEY etc. from .env

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

    # ── BULL MARKET CYCLES ────────────────────────────────────────────────────
    {
        "event_type": "BULL_MARKET",
        "description": (
            "India infrastructure super-cycle 2003–2007: GDP at 8–9%, FII buying ₹40,000 Cr+, "
            "composite score high across sectors. Nifty rose from 1,000 to 6,300. "
            "Revenue growth 25–40% YoY for capital goods, metals, banking stocks. "
            "RSI frequently overbought at 70–80 but momentum sustained throughout bull run. "
            "PE ratios expanded to 25–35x across industrials and metals; ROCE improving across sectors."
        ),
        "event_date": date(2003, 4, 25),
        "affected_sectors": [
            "INFRASTRUCTURE", "METALS", "BANKING", "CAPITAL_GOODS", "REALTY",
        ],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Nifty 500% gain over 4 years; infrastructure, metals, banking led. "
            "RSI stayed elevated 60–80+ for months in trending bull market. "
            "FII inflows consistently positive; domestic mutual fund SIP culture began."
        ),
    },
    {
        "event_type": "BULL_MARKET",
        "description": (
            "Post-COVID V-shape recovery: Nifty doubles from 7,511 low to 18,600 ATH in 18 months. "
            "FII buying ₹1.7 lakh Cr in Nov–Dec 2020 after Pfizer vaccine news. "
            "DII also buying; FII and DII convergence buying — rare critical opportunity signal. "
            "Revenue growth recovering strongly QoQ after initial COVID contraction. "
            "PE expanded to 25–30x; markets re-rated despite weak ROCE in early quarters."
        ),
        "event_date": date(2020, 9, 1),
        "affected_sectors": [
            "IT", "PHARMA", "BANKING", "AUTO", "CONSUMER", "METALS",
        ],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Nifty hit new ATH 18,604 in Oct 2021. "
            "Mid-cap index returned 90%+; small-cap index 130%+. "
            "FII cumulative buying >₹3 lakh Cr Nov 2020–Sep 2021; DII steady buyer throughout."
        ),
    },
    {
        "event_type": "BULL_MARKET",
        "description": (
            "IT services bull run 2020–2022: WFH-driven cloud migration, digital transformation surge. "
            "TCS, Infosys, HCL Tech hit all-time highs; Nifty IT index gained 120%. "
            "Revenue growth 20–30% YoY, ROCE >25% across top IT companies. "
            "PE ratios 30–45x; RSI consistently 60–75 in bull run, not a sell signal. "
            "FII buying concentrated in large-cap IT as India IT became global safe haven."
        ),
        "event_date": date(2020, 10, 1),
        "affected_sectors": ["IT", "TECHNOLOGY", "DIGITAL"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "TCS market cap crossed ₹14 lakh Cr. "
            "Infosys guided for 13–15% growth; dividend payouts at record. "
            "Nifty IT index doubled peak-to-trough from Jun 2020 to Jan 2022."
        ),
    },
    {
        "event_type": "BULL_MARKET",
        "description": (
            "India mid-cap and small-cap bull run 2023–2024: Nifty midcap 150 gained 65%, "
            "Nifty smallcap 250 gained 80% in 18 months. "
            "Domestic SIP flows consistently ₹17,000–20,000 Cr/month providing floor. "
            "Revenue growth 15–30% for select companies; PSU re-rating drove large moves. "
            "PE ratios elevated at 35–50x but justified by earnings growth acceleration. "
            "ROCE >15% stocks in defense, railways, capex themes saw 2–5x returns."
        ),
        "event_date": date(2023, 4, 1),
        "affected_sectors": [
            "PSU", "DEFENCE", "RAILWAYS", "MANUFACTURING", "CAPEX",
        ],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Nifty midcap 150 hit all-time high in Sep 2024. "
            "HAL, BEL, Cochin Shipyard: 3–5x returns. "
            "DII buying record ₹2+ lakh Cr in FY24 absorbing FII selling; markets resilient."
        ),
    },

    # ── RECOVERY PATTERNS (BEARISH → REVERSAL) ───────────────────────────────
    {
        "event_type": "RECOVERY",
        "description": (
            "Post-taper-tantrum India recovery 2013–2014: After INR crash to 68/USD and "
            "FII outflows of ₹45,000 Cr, India staged sharp recovery. "
            "RBI stabilisation + election optimism; FII returned as buyers in H1 2014. "
            "Revenue growth was declining YoY for export-heavy companies but macro stabilised. "
            "RSI was deeply oversold at 30–35; investors who bought at oversold levels saw 40%+ returns."
        ),
        "event_date": date(2014, 1, 1),
        "affected_sectors": ["BANKING", "IT", "CONSUMER", "AUTO"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Nifty rallied from 5,400 (Aug 2013) to 7,800 (May 2014) — 44% return. "
            "IT stocks recovered as INR weakness boosted USD revenues. "
            "Election win in May 2014 added further momentum."
        ),
    },
    {
        "event_type": "RECOVERY",
        "description": (
            "India market recovery post-NBFC crisis 2019–2020: After IL&FS, DHFL collapses "
            "dragged NBFC and banking stocks 30–60%, corporate tax cut (Sep 2019) triggered recovery. "
            "PE ratios were compressed to 12–15x in financials despite decent revenue growth. "
            "ROCE for quality NBFCs >15%; stocks with low PE and improving ROCE led the recovery. "
            "DII buying was consistent; FII gradually returned after the tax cut announcement."
        ),
        "event_date": date(2019, 9, 20),
        "affected_sectors": ["BANKING", "NBFC", "CONSUMER", "MANUFACTURING"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Nifty surged 5.3% on corporate tax cut day (Sep 2019). "
            "HDFC Bank, SBI recovered; quality NBFC stocks like Bajaj Finance doubled. "
            "Markets rallied from 10,600 (Sep 2019) to 12,300 (Jan 2020) pre-COVID."
        ),
    },
    {
        "event_type": "RECOVERY",
        "description": (
            "India recovery post-US Fed peak rate pivot (Nov 2023): After ₹2.8 lakh Cr FII outflows "
            "in 2022, FII returned as US 10Y yield peaked at 5% and Fed signalled rate cuts. "
            "India composite macro score improved; INR/USD stable at 83–84 despite dollar strength. "
            "Revenue growth recovered to 10–15% YoY for most sectors. "
            "RSI moved from 40–45 to 60–65 as trend changed; MACD bullish crossover confirmed recovery."
        ),
        "event_date": date(2023, 11, 1),
        "affected_sectors": [
            "BANKING", "IT", "CONSUMER", "REALTY", "AUTO",
        ],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Nifty 50 gained 24% in FY24; FII bought ₹1.7 lakh Cr in Jan–Mar 2024. "
            "Rate-sensitive sectors (banking, realty, auto) outperformed. "
            "India became top EM destination for FII flows in Asia."
        ),
    },
    {
        "event_type": "RECOVERY",
        "description": (
            "Overbought RSI does not mean sell: India bull markets 2003–2008, 2014–2018, 2020–2021 "
            "showed RSI staying 60–80 for months at a time during strong trends. "
            "ADX >40 combined with bullish EMA alignment signals trend continuation, not exhaustion. "
            "High PE (30–50x) with high revenue growth (20–30% YoY) and improving ROCE (>15%) "
            "is a growth premium, not a valuation trap. Bull runs reward staying invested."
        ),
        "event_date": date(2021, 5, 1),
        "affected_sectors": [
            "IT", "PHARMA", "CONSUMER", "BANKING", "METALS",
        ],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Multiple stocks stayed RSI >65 for 6–12 months during bull runs. "
            "Selling at RSI 70 in a bull market missed 30–50% additional upside. "
            "Volume above average with RSI 70+ and bullish MACD crossover = continuation signal."
        ),
    },
    {
        "event_type": "RECOVERY",
        "description": (
            "Capital-intensive sector re-rating: Telecom and renewables with low ROCE (<10%) "
            "during heavy capex cycles (5G rollout, solar/wind capacity addition) re-rated 2–3x "
            "once capex peaked and FCF inflected positive. "
            "Airtel ARPU expanded from ₹95 to ₹210 (2019–2024); ROCE improved to 15%+ post-5G capex peak. "
            "Investors who avoided elevated PE during capex phase missed multi-bagger returns."
        ),
        "event_date": date(2022, 4, 1),
        "affected_sectors": ["TELECOM", "RENEWABLE_ENERGY", "INFRASTRUCTURE"],
        "market_impact": "LONG_TERM_POSITIVE",
        "outcome": (
            "Airtel stock went from ₹350 to ₹1,700 (2019–2024) — nearly 5x. "
            "Tata Power, Adani Green re-rated 3–8x on renewable capacity expansion. "
            "Key insight: during capex supercycles, ROCE is temporarily depressed. "
            "Free cash flow yield and revenue CAGR are better metrics than ROCE alone."
        ),
    },

    # ── TRUMP / US TRADE POLICY EVENTS ───────────────────────────────────────
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "Trump wins 2016 US election: USD rallies 5%; US 10Y yield rises from 1.8% to 2.6%. "
            "IT sector initial concern as H1-B visa restrictions feared; INR weakens to 68–69. "
            "FII outflows from India ₹12,000 Cr in 3 weeks. "
            "Revenue growth outlook for IT companies uncertain due to visa costs. "
            "India VIX spikes to 18–20 on geopolitical uncertainty."
        ),
        "event_date": date(2016, 11, 9),
        "affected_sectors": ["IT", "BANKING", "PHARMA", "CONSUMER"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "IT sector fell 8–12% on visa fears; recovered fully within 3 months. "
            "Trump's US growth policies triggered global risk-on: Nifty recovered to new ATH by Apr 2017. "
            "H1-B restrictions were less severe than feared; IT revenues held up."
        ),
    },
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "US-China trade war Phase 1 (2018–2019): Trump imposes 25% tariffs on $250 Bn Chinese goods. "
            "China retaliates; global supply chains disrupted. "
            "India positioned as China+1 manufacturing alternative; FDI inflows rise. "
            "Export-oriented sectors (pharma, IT, chemicals, textiles) initially uncertain. "
            "INR depreciates to 72–74 as EM risk-off; FII selling ₹30,000 Cr in 2018."
        ),
        "event_date": date(2018, 7, 6),
        "affected_sectors": [
            "IT", "PHARMA", "CHEMICALS", "TEXTILES", "MANUFACTURING",
        ],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Short-term: Nifty fell 15% from peak; IT, pharma exports hurt by INR volatility. "
            "Long-term: India gained $8 Bn+ in manufacturing orders as China+1 narrative took hold. "
            "Chemical, specialty pharma stocks recovered and outperformed by 2019–2020."
        ),
    },
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "US-China Phase 1 trade deal signed January 2020: Markets relieved; "
            "global risk appetite returns, FII flows resume to EMs including India. "
            "India IT sector benefits as US tech spending resumes. "
            "Revenue growth expectations improve; composite scores rise across export sectors. "
            "US 10Y yield stable at 1.8%; DXY dollar index softens supporting INR."
        ),
        "event_date": date(2020, 1, 15),
        "affected_sectors": ["IT", "CHEMICALS", "PHARMA", "METALS", "BANKING"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "Nifty gained 4% in Jan 2020 before COVID interrupted. "
            "FII bought ₹8,000 Cr in Jan 2020. "
            "Trade deal set precedent for managed trade tensions; India manufacturing orders increased."
        ),
    },
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "Trump 'Liberation Day' tariffs — April 2, 2025: US announces sweeping reciprocal tariffs "
            "on all trading partners. India faces 26% tariff on exports to US. "
            "Nifty falls 3–5% in 2 sessions; FII outflows ₹18,000 Cr in April 2025. "
            "INR depreciates to 85–87/USD; India VIX spikes to 22–25. "
            "Export sectors (IT, pharma, textiles, engineering goods) sharply lower. "
            "Revenue growth outlook for India IT companies cut by 2–3% for FY26."
        ),
        "event_date": date(2025, 4, 2),
        "affected_sectors": [
            "IT", "PHARMA", "TEXTILES", "ENGINEERING", "AUTO",
        ],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Markets fell 8–10% peak-to-trough April 2025. "
            "India began negotiations for bilateral trade deal with US. "
            "Pharma given temporary exemption; IT services (not goods) less directly affected."
        ),
    },
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "Trump 90-day tariff pause announced April 9, 2025: Most countries except China given "
            "90-day reprieve from Liberation Day tariffs; India 26% tariff paused. "
            "Global markets stage sharp recovery; Nifty bounces 4–5% in 2 sessions. "
            "FII buyers return; DII which had been buying throughout provides floor. "
            "India VIX retreats from 22 to 16; composite scores recover across export sectors."
        ),
        "event_date": date(2025, 4, 9),
        "affected_sectors": [
            "IT", "PHARMA", "METALS", "BANKING", "AUTO",
        ],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Nifty 50 recovered to pre-tariff levels within 3 weeks. "
            "India used 90-day window to fast-track US bilateral trade deal. "
            "IT sector recovered as dollar-denominated revenues unaffected by goods tariffs."
        ),
    },
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "India-US bilateral trade negotiations 2025–2026: India offers tariff concessions on "
            "US goods (energy, defence, agriculture) to secure exemption from 26% tariff. "
            "FTA negotiations accelerated; India positioned as strategic manufacturing partner. "
            "Revenue growth expectations for export sectors stable despite tariff headline risk. "
            "India composite macro score improving; INR/USD stable at 85–87 despite uncertainty."
        ),
        "event_date": date(2025, 7, 1),
        "affected_sectors": [
            "IT", "PHARMA", "DEFENCE", "MANUFACTURING", "CHEMICALS",
        ],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "India secured partial tariff relief; IT and pharma exports continued growing. "
            "India FDI in manufacturing accelerated as US companies shifted supply chains. "
            "India-US trade volume grew despite tariff uncertainty."
        ),
    },

    # ── MIDDLE EAST CRISIS & OIL ─────────────────────────────────────────────
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "Hamas attack on Israel October 7, 2023: Crude oil spikes to $95/barrel; "
            "India VIX jumps to 14–15 from 12. "
            "India's import bill concerns: India imports 85% of crude oil needs. "
            "INR weakens to 83–84/USD on oil import cost fears. "
            "OMC (BPCL, IOC, HPCL) stocks fall 5–8% on under-recovery fears. "
            "Aviation and paint sectors fall on oil price pass-through risk."
        ),
        "event_date": date(2023, 10, 7),
        "affected_sectors": [
            "OIL_GAS", "AVIATION", "PAINTS", "CHEMICALS", "OMC",
        ],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Crude oil retreated from $95 to $75–80 by Dec 2023 as conflict remained contained. "
            "India market broadly resilient; Nifty ended 2023 up 20% despite Gaza war. "
            "OMC stocks recovered as oil stabilised; election spending supported consumption."
        ),
    },
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "Iran-Israel escalation April 2024: Iran launches direct drone and missile attack on Israel. "
            "Brent crude spikes to $92/barrel; gold hits $2,400/oz. "
            "India VIX jumps to 14–16; FII sell ₹8,000 Cr in 2 weeks. "
            "INR depreciates to 83.5/USD; India CAD risk rises with higher oil import costs. "
            "Defensive sectors (IT services, pharma) see rotation buying from cyclicals."
        ),
        "event_date": date(2024, 4, 14),
        "affected_sectors": [
            "OIL_GAS", "AVIATION", "GOLD", "DEFENCE", "CHEMICALS",
        ],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Crude oil retreated to $80–85 within 2 weeks as Iran-Israel situation de-escalated. "
            "India market fell only 2–3%; gold stocks and defence outperformed. "
            "ONGC, Oil India benefited from higher crude; OMC stocks volatile."
        ),
    },
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "Houthi Red Sea attacks (Dec 2023–ongoing): Yemen's Houthis attack commercial shipping "
            "in Red Sea; container shipping rates surge 200–300%. "
            "India-Europe trade routes disrupted; shipping through Red Sea drops 40%. "
            "Impact: Rising logistics costs affect India export competitiveness. "
            "Textile, pharma, auto ancillary exports face shipping cost inflation. "
            "Oil tankers rerouted via Cape of Good Hope; crude freight rates up."
        ),
        "event_date": date(2023, 12, 15),
        "affected_sectors": [
            "TEXTILES", "PHARMA", "AUTO_ANCILLARY", "LOGISTICS", "SHIPPING",
        ],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "India exporters absorbed 5–8% rise in logistics costs initially. "
            "Container rates normalised by H2 2024 as new shipping routes established. "
            "Indian shipping companies (SCI, Adani Ports) benefited from higher freight rates."
        ),
    },
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "Gaza ceasefire agreement January 2025: Six-week ceasefire reduces Middle East tension. "
            "Brent crude retreats from $80 to $72–75; shipping disruption eases. "
            "India import bill reduces; INR stable to slightly stronger at 84–85/USD. "
            "FII flows resume to India as EM risk appetite improves post-ceasefire. "
            "OMC stocks rally 5–8% on margin recovery expectations."
        ),
        "event_date": date(2025, 1, 19),
        "affected_sectors": [
            "OIL_GAS", "AVIATION", "OMC", "CONSUMER", "BANKING",
        ],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "Crude stabilised at $72–78 range through Q1 2025. "
            "India macro improved: CAD narrowed, inflation softened, enabling RBI rate cut. "
            "Markets broadly positive; consumer stocks re-rated on lower input costs."
        ),
    },
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "Iran-Israel ongoing tensions 2024–2025: Persistent Middle East risk keeps oil "
            "price floor elevated at $75–85/barrel. Gold at ATH $2,500–2,800/oz. "
            "India net oil importer: every $10/barrel rise in crude adds ~0.4% to CAD. "
            "India VIX stays elevated at 14–18 (above 2022–2023 average of 12–14). "
            "Defence sector stocks (HAL, BEL, Bharat Forge) re-rate on global arms demand."
        ),
        "event_date": date(2024, 10, 1),
        "affected_sectors": [
            "DEFENCE", "OIL_GAS", "GOLD", "AVIATION", "CHEMICALS",
        ],
        "market_impact": "MIXED",
        "outcome": (
            "India defence index returned 45% in FY25 despite elevated oil and geopolitical uncertainty. "
            "HAL, Cochin Shipyard, BEL hit ATH on record defence order book. "
            "Oil sensitivity: India markets underperform when Brent >$90; outperform when <$80."
        ),
    },

    # ── SECTOR-SPECIFIC BULL RUNS ─────────────────────────────────────────────
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "India defence sector re-rating 2022–2024: 'Atmanirbhar Bharat' defence indigenisation policy; "
            "₹1+ lakh Cr defence capex budget; 25% of procurement reserved for domestic. "
            "Revenue growth for HAL, BEL, Cochin Shipyard: 25–40% YoY. "
            "ROCE for defence PSUs improved from 12% to 20–25%. "
            "PE ratios expanded from 15x to 50–70x on earnings visibility and order book growth."
        ),
        "event_date": date(2022, 2, 1),
        "affected_sectors": ["DEFENCE", "AEROSPACE", "ELECTRONICS"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "HAL: ₹520 to ₹4,200 (8x in 3 years). "
            "BEL: 4x; Cochin Shipyard: 10x. "
            "PE of 50–70x was justified by 25–35% earnings CAGR and order book visibility. "
            "High PE with high ROCE and high revenue growth = quality growth premium, not overvaluation."
        ),
    },
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "PSU banking sector turnaround 2021–2023: After decade of NPA crisis and losses, "
            "IBC resolution improved bank balance sheets. "
            "SBI FY23 profit ₹50,232 Cr (all-time record); NPA fell from 10% to 2.7%. "
            "PE ratios were low at 8–12x (below sector average) with ROCE improving to 14–18%. "
            "FII started buying PSU banks from late 2022 after sustained DII accumulation. "
            "Revenue growth (NII) accelerated as rate hikes boosted margins."
        ),
        "event_date": date(2021, 6, 1),
        "affected_sectors": ["PSU_BANKS", "BANKING"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "SBI: ₹170 to ₹850 (5x in 3 years). "
            "Bank of Baroda: 4x; Canara Bank: 5x. "
            "Low PE + improving ROCE + revenue acceleration = classic multi-bagger setup. "
            "Nifty PSU Bank index gained 200%+ from trough (2021) to peak (2024)."
        ),
    },
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "India renewable energy capex supercycle 2020–2025: India targets 500 GW renewable by 2030. "
            "Solar and wind capacity addition accelerates; Adani Green, Tata Power, NTPC Green re-rate. "
            "Revenue growth for renewable developers: 30–50% YoY. "
            "ROCE temporarily depressed at 6–10% during heavy capex; free cash flow negative short-term. "
            "Long-term investors who ignored low ROCE during capex phase saw 3–8x returns."
        ),
        "event_date": date(2020, 11, 1),
        "affected_sectors": [
            "RENEWABLE_ENERGY", "SOLAR", "WIND", "INFRASTRUCTURE",
        ],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Tata Power: ₹50 to ₹450 (9x). "
            "Adani Green: IPO ₹920, ATH ₹2,200. "
            "Critical lesson: ROCE is a poor metric for capital-intensive growth sectors "
            "in heavy investment phase. Revenue growth and order backlog are better signals."
        ),
    },
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "India real estate super-cycle 2022–2025: Post-COVID urban housing demand surge; "
            "affordability at decade-best levels; millennials entering homebuying age. "
            "DLF, Godrej Properties, Prestige, Macrotech hit all-time highs. "
            "Pre-sales growth 30–50% YoY; ROCE improving from 8% to 15%+ as inventory clears. "
            "PE ratios 40–60x on NAV re-rating; FII and DII both accumulating real estate stocks."
        ),
        "event_date": date(2022, 1, 1),
        "affected_sectors": ["REALTY", "HOUSING", "CEMENT", "STEEL"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "DLF: ₹300 to ₹900 (3x in 2 years). "
            "Macrotech (Lodha): IPO ₹486, ATH ₹1,600. "
            "Nifty Realty index gained 150% from 2022 to 2024. "
            "Elevated PE in real estate not a sell signal when pre-sales and launches are accelerating."
        ),
    },
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "Auto sector super-cycle 2021–2023: Post-COVID pent-up demand + premiumisation trend. "
            "Maruti, M&M, Tata Motors, Eicher Motors hit all-time highs. "
            "Maruti Q3 FY23: volume growth 20% YoY, EBITDA margin 10%+. "
            "PE ratios expanded to 25–35x; ROCE >15% for Maruti and M&M. "
            "Revenue growth 25–40% YoY; waiting periods stretched to 6–12 months for top models."
        ),
        "event_date": date(2021, 7, 1),
        "affected_sectors": ["AUTO", "AUTO_ANCILLARY", "CONSUMER"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Maruti: ₹5,500 to ₹12,000 (2.2x). "
            "M&M: ₹650 to ₹2,800 (4.3x). "
            "Semi-conductor shortage resolved by 2022; inventory built-up triggered correction in 2023 "
            "but fundamentals remained intact — cycle resumed in H2 2023."
        ),
    },
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "Railway and capital goods bull run 2022–2025: PM Gati Shakti, National Infrastructure Pipeline; "
            "IRCTC, Rail Vikas Nigam, RVNL, Titagarh Wagons re-rate significantly. "
            "Revenue growth 35–60% YoY for EPC and railway stocks. "
            "ROCE improving from 12–15% to 18–25% as order books convert. "
            "Order book-to-revenue ratios of 3–5x providing earnings visibility for 3 years."
        ),
        "event_date": date(2022, 6, 1),
        "affected_sectors": ["RAILWAYS", "INFRASTRUCTURE", "CAPITAL_GOODS", "EPC"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "RVNL: ₹30 to ₹600 (20x). "
            "Rail Vikas Nigam, Titagarh Wagons: 5–10x returns. "
            "Nifty Capital Goods index gained 180% from 2022–2024. "
            "PE of 40–60x on high revenue growth and order book visibility was justified."
        ),
    },

    # ── INDIA MACRO STRUCTURAL POSITIVES ─────────────────────────────────────
    {
        "event_type": "MACRO_POSITIVE",
        "description": (
            "India surpasses UK as world's 5th largest economy (2023) and on track for 3rd by 2027. "
            "GDP at $3.7 trillion; GDP growth 6.5–7% annually. "
            "India's global weight in MSCI EM index rises from 8% (2020) to 18% (2024). "
            "Passive FII inflows of $3–4 Bn triggered by index weight increase. "
            "Structural re-rating of India premium over other EMs; India VIX lower than historical average."
        ),
        "event_date": date(2023, 6, 1),
        "affected_sectors": [
            "BANKING", "IT", "CONSUMER", "MANUFACTURING", "INFRASTRUCTURE",
        ],
        "market_impact": "LONG_TERM_POSITIVE",
        "outcome": (
            "India trades at premium to MSCI EM peers; PE 22–24x vs EM average 12–14x. "
            "DII domestic flows (SIP ₹20,000 Cr+/month) provide structural support. "
            "India has become the fastest-growing large economy; structural bull case intact."
        ),
    },
    {
        "event_type": "MACRO_POSITIVE",
        "description": (
            "China+1 manufacturing shift: Post-COVID global companies accelerate China supply-chain "
            "diversification. India receives record FDI in electronics, chemicals, pharma. "
            "Apple moves iPhone production to India (Foxconn, Tata Electronics); target 25% of "
            "global iPhone assembly by 2025. "
            "India electronics exports grow from $8 Bn to $29 Bn in 3 years. "
            "PLI schemes attract $15+ Bn in manufacturing commitments."
        ),
        "event_date": date(2022, 9, 1),
        "affected_sectors": [
            "ELECTRONICS", "PHARMA", "CHEMICALS", "MANUFACTURING",
        ],
        "market_impact": "LONG_TERM_POSITIVE",
        "outcome": (
            "Dixon Technologies, Kaynes, Tata Electronics emerge as major beneficiaries. "
            "API and specialty chemical stocks re-rated 2–3x. "
            "India FDI in manufacturing hit $25+ Bn in FY24 — highest ever."
        ),
    },
    {
        "event_type": "RBI_POLICY",
        "description": (
            "RBI begins new rate-cut cycle February 2025: Repo cut 25 bps to 6.25% — first cut in 5 years. "
            "Signals further cuts ahead as inflation softens to 4.5% and growth needs support. "
            "Bank Nifty rallies; realty and auto stocks outperform on rate sensitivity. "
            "Revenue growth for rate-sensitive sectors expected to improve 2–3% over next year. "
            "DII buying concentrated in banking, NBFC, and housing finance companies."
        ),
        "event_date": date(2025, 2, 7),
        "affected_sectors": [
            "BANKING", "NBFC", "REALTY", "AUTO", "CONSUMER",
        ],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "Bank Nifty gained 5% in 2 weeks post rate cut. "
            "HDFC Bank, SBI, Bajaj Finance outperformed. "
            "RBI cut rates to 6% by June 2025 (3 cuts); loan growth accelerated to 14%."
        ),
    },
    {
        "event_type": "MACRO_POSITIVE",
        "description": (
            "India forex reserves hit record $700 billion (September 2024): Provides 12+ months "
            "import cover and buffer against EM capital flight. "
            "INR stability improved vs other EMs; INR/USD in 83–86 range vs BRL, IDR volatility. "
            "RBI intervention capability reduces tail risk in INR. "
            "India composite macro score improves: stable currency + FX buffer = lower systemic risk."
        ),
        "event_date": date(2024, 9, 27),
        "affected_sectors": ["BANKING", "CONSUMER", "IMPORT_HEAVY"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "India outperformed EM peers during EM sell-offs. "
            "INR depreciated only 2% vs USD in 2024 while Brazilian Real fell 20%, IDR 5%. "
            "Lower currency risk premium in India equities contributed to sustained FII interest."
        ),
    },

    # ── FII FLOW PATTERN EVENTS ───────────────────────────────────────────────
    {
        "event_type": "INSTITUTIONAL_FLOW",
        "description": (
            "FII massive buying convergence Nov–Dec 2020: After US election and Pfizer vaccine results, "
            "FII bought ₹1.7 lakh Cr in just 2 months — largest-ever 2-month FII buying. "
            "DII also continued buying; rare FII + DII convergence buying pattern (critical opportunity). "
            "Nifty rallied from 12,000 to 14,000 in these 2 months (17% return). "
            "RSI moved from neutral 55 to 70+ during this rally; MACD confirmed bullish crossover."
        ),
        "event_date": date(2020, 11, 1),
        "affected_sectors": [
            "BANKING", "IT", "CONSUMER", "PHARMA", "METALS",
        ],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "FII + DII simultaneous net buying = historically strongest buy signal. "
            "When FII 10-day net > ₹1,000 Cr AND DII also buying: Nifty average return +12% next 3 months. "
            "Composite scores across sectors spiked; RSI overbought but momentum continued for months."
        ),
    },
    {
        "event_type": "INSTITUTIONAL_FLOW",
        "description": (
            "FII return to India after Fed pivot signals (Oct–Dec 2023): "
            "After 2022 FII exodus of ₹2.8 lakh Cr, FII return as US 10Y yield peaks at 5.1% "
            "and Fed signals rate cuts. FII buys ₹40,000 Cr in Q4 2023. "
            "DII had been steady buyer throughout; domestic retail SIP providing consistent floor. "
            "Revenue growth for corporate India recovering to 10–15% YoY. "
            "India VIX stable at 12–13; macro composite score improving."
        ),
        "event_date": date(2023, 10, 15),
        "affected_sectors": [
            "BANKING", "IT", "CONSUMER", "AUTO", "REALTY",
        ],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Nifty gained 20% in FY24. "
            "FII bought ₹1.7 lakh Cr in FY24 after selling ₹2.8 lakh Cr in FY23. "
            "India became top EM destination for equity flows in Asia."
        ),
    },
    {
        "event_type": "INSTITUTIONAL_FLOW",
        "description": (
            "FII selling during Trump tariff uncertainty (Apr–May 2025): "
            "FII sold ₹25,000 Cr in April 2025 on tariff announcement; "
            "DII bought ₹30,000 Cr absorbing FII selling (DII absorbing FII sell = +15 pts). "
            "Markets fell 5–8% peak-to-trough but DII support prevented deeper correction. "
            "India VIX spiked to 22–24 but structural domestic buying intact. "
            "Revenue growth outlook cautious at 8–12% for FY26 amid tariff uncertainty."
        ),
        "event_date": date(2025, 4, 3),
        "affected_sectors": ["IT", "PHARMA", "BANKING", "AUTO"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Markets recovered 80% of losses within 3 weeks after 90-day tariff pause. "
            "DII-buying-while-FII-selling pattern historically precedes market reversal within 4–6 weeks. "
            "India's strong domestic institutional base now provides structural market floor."
        ),
    },

    # ── CONGLOMERATE / HIGH-PE GROWTH PATTERNS ────────────────────────────────
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "Reliance Industries transformation 2016–2021: Jio disruption destroyed telecom ARPU "
            "but Reliance re-rated as a tech-led conglomerate. "
            "PE expanded from 15x (FY16) to 35–50x (FY21) as Jio and Retail businesses scaled. "
            "ROCE was compressed to 7–10% during Jio investment phase; QoQ revenue growth variable. "
            "FII holding in Reliance rose from 22% to 28%; Google and Facebook invested $10 Bn in Jio. "
            "Composite score improved from 40s to 65+ as new growth vectors emerged."
        ),
        "event_date": date(2019, 8, 1),
        "affected_sectors": ["TELECOM", "RETAIL", "OIL_GAS", "TECHNOLOGY"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Reliance: ₹900 to ₹2,360 (2.6x) from Aug 2019 to Sep 2020. "
            "Market cap crossed ₹15 lakh Cr. "
            "PE of 45–50x justified by Jio subscriber growth of 400 Mn and retail revenue tripling. "
            "High PE in conglomerates with diverse growth pillars ≠ valuation trap."
        ),
    },
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "India digital consumption bull run 2020–2024: Internet users grow from 550 Mn to 900 Mn. "
            "Data consumption per user at 20+ GB/month (highest globally). "
            "Airtel ARPU expands from ₹128 to ₹210; Jio ARPU from ₹130 to ₹180. "
            "Telecom stocks re-rated on improving ARPU and ROCE recovery post-capex. "
            "Revenue growth accelerated 12–18% YoY as tariff hikes stacked. "
            "Institutional investors returned to telecom after 5-year sector avoidance post-Jio disruption."
        ),
        "event_date": date(2021, 11, 1),
        "affected_sectors": ["TELECOM", "DIGITAL", "TOWER"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Airtel: ₹350 (2019) to ₹1,700 (2024) — nearly 5x. "
            "Indus Towers: strong rental growth as 5G deployment drove tower leasing. "
            "Key lesson: Telecom RE-RATING requires ARPU expansion, not just subscriber growth. "
            "Patience during capex trough rewarded with 5x return."
        ),
    },
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "India FMCG volume recovery 2023–2024: After 8-quarter volume decline due to "
            "rural stress and inflation, FMCG volumes recovered as inflation moderated. "
            "Revenue growth improved from 3–5% to 8–12% YoY; EBITDA margin expanded 150–200 bps. "
            "ROCE for FMCG consistently >25–40%; PE at 45–55x reflecting quality premium. "
            "High PE + high ROCE + recovering revenue growth = classic quality growth premium. "
            "FII and DII both buyers; Consumer index outperformed Nifty by 15% in FY24."
        ),
        "event_date": date(2023, 7, 1),
        "affected_sectors": ["FMCG", "CONSUMER", "RURAL"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "HUL, Nestle, Britannia outperformed. "
            "Dabur, Marico recovered volumes. "
            "FMCG at 45–55x PE with 25–35% ROCE is NOT overvalued — it is quality at a premium. "
            "The sector has traded at 40–55x PE for a decade and consistently re-rated."
        ),
    },
    {
        "event_type": "MACRO_POSITIVE",
        "description": (
            "India GST collections consistently crossing ₹2 lakh Cr/month (FY25): "
            "Signals formalization of economy, robust consumption, and strong GDP momentum. "
            "Government fiscal discipline: tax buoyancy reduces need for borrowing; "
            "bond yields stable; RBI has room to cut rates. "
            "Revenue growth for consumer and manufacturing sectors supported by strong demand. "
            "Composite macro score for India is structurally positive; domestic consumption resilient."
        ),
        "event_date": date(2024, 4, 1),
        "affected_sectors": [
            "CONSUMER", "BANKING", "FMCG", "AUTO", "MANUFACTURING",
        ],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "India GDP grew 7%+ for 3 consecutive years. "
            "Tax-GDP ratio improved, reducing fiscal deficit concerns. "
            "Consumption stocks maintained premium PE as earnings growth sustained."
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


def seed(dry_run: bool = False, force: bool = False, append: bool = False):
    """
    Seed or update the historical_events table.

    Modes:
      default  : Insert all EVENTS; skip if table already has rows.
      --force  : Wipe existing rows and re-seed from scratch with all EVENTS.
      --append : Only insert events whose description is not already present
                 (safe to run repeatedly; will not duplicate existing rows).
    """
    _positive = [
        "STRONG_POSITIVE", "MILD_POSITIVE", "LONG_TERM_POSITIVE",
    ]
    _negative = [
        "SEVERE_NEGATIVE", "MODERATE_NEGATIVE", "SECTOR_NEGATIVE",
        "SEVERE_SECTOR_DISRUPTION",
    ]
    pos_count = sum(1 for e in EVENTS if e.get("market_impact") in _positive)
    neg_count = sum(1 for e in EVENTS if e.get("market_impact") in _negative)
    print(
        f"Preparing to seed {len(EVENTS)} historical events "
        f"({pos_count} positive / {neg_count} negative / "
        f"{len(EVENTS) - pos_count - neg_count} mixed-neutral) …"
    )

    if dry_run:
        for i, ev in enumerate(EVENTS, 1):
            impact = ev.get("market_impact", "?")
            tag = "+" if impact in _positive else ("-" if impact in _negative else "~")
            # Safe ASCII for Windows terminals that don't support INR/Unicode chars
            safe_desc = ev["description"][:65].encode("ascii", "replace").decode("ascii")
            safe_type = ev["event_type"].encode("ascii", "replace").decode("ascii")
            print(f"  [{i:02d}] {tag} {ev['event_date']} | {safe_type:20s} | {safe_desc}")
        print("\nDry-run complete — no data written.")
        return

    client = get_supabase_client()

    existing = client.table("historical_events").select("id", count="exact").execute()
    existing_count = existing.count or 0

    # ── Append mode: only add genuinely new events ────────────────────────────
    if append:
        # Fetch existing descriptions to dedup (first 100 chars is enough)
        resp = client.table("historical_events").select("description").execute()
        existing_descs = {
            row["description"][:80].strip().lower()
            for row in (resp.data or [])
        }
        new_rows = []
        for ev in EVENTS:
            key = ev["description"][:80].strip().lower()
            if key not in existing_descs:
                new_rows.append(serialize_event(ev))

        if not new_rows:
            print(f"  All {len(EVENTS)} events already exist — nothing to append.")
            return

        print(f"  Appending {len(new_rows)} new events (skipping {len(EVENTS) - len(new_rows)} duplicates) …")
        batch_size = 20
        inserted = 0
        for start in range(0, len(new_rows), batch_size):
            batch = new_rows[start : start + batch_size]
            client.table("historical_events").insert(batch).execute()
            inserted += len(batch)
            print(f"  Inserted batch {start // batch_size + 1}: {len(batch)} rows")
        print(f"\nDone — {inserted} new events appended (total in DB: {existing_count + inserted}).")
        return

    # ── Force mode: wipe and full re-seed ────────────────────────────────────
    if existing_count > 0 and not force:
        print(
            f"\n  Table already has {existing_count} rows. Skipping seed.\n"
            f"   To wipe and re-seed, run:      python db/seed_historical_events.py --force\n"
            f"   To add only new events, run:   python db/seed_historical_events.py --append\n"
            f"   To add embeddings only, run:   python db/seed_historical_events.py --embed"
        )
        return

    if existing_count > 0 and force:
        print(f"  --force flag set: truncating {existing_count} existing rows …")
        client.table("historical_events").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        print("  Table cleared.")

    rows = [serialize_event(ev) for ev in EVENTS]

    # Insert in batches of 20 to stay within Supabase request size limits
    batch_size = 20
    inserted = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        client.table("historical_events").insert(batch).execute()
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

    parser = argparse.ArgumentParser(
        description="Seed or update the historical_events table",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python db/seed_historical_events.py --dry-run          # preview all events
  python db/seed_historical_events.py --append           # add only new events (safe, idempotent)
  python db/seed_historical_events.py --append --embed   # add new events + generate embeddings
  python db/seed_historical_events.py --force            # wipe and re-seed all 93 events
  python db/seed_historical_events.py --force --embed    # full re-seed + embeddings
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print all events with +/-/~ sentiment tags; no DB writes",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Only insert events not already in DB (dedup by description prefix). Safe to run repeatedly.",
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        help="Generate and store OpenAI embeddings for any rows missing them (run after seeding)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Wipe ALL existing rows and re-seed from scratch with all events",
    )
    args = parser.parse_args()

    seed(dry_run=args.dry_run, force=args.force, append=args.append)

    if args.embed and not args.dry_run:
        embed_events()
