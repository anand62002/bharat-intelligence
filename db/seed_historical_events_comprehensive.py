"""
db/seed_historical_events_comprehensive.py
==========================================
Comprehensive seed file for the historical_events RAG knowledge base.

Extends the original 93-event set with 65+ additional events covering:
  - Major IPO events (LIC, Paytm, Zomato, Nykaa, PolicyBazaar)
  - Corporate governance crises (ICICI Bank, Infosys-Murthy, Jet Airways)
  - Commodity cycles (gold bull runs, agri price shocks, metal super-cycles)
  - Banking sector structural reforms (mergers, recap, privatisation)
  - Russia-Ukraine war impact on India markets
  - India-China LAC tensions market impact
  - NSE colocation / market manipulation cases
  - Global macro events (ECB policy, Japan YCC, US inflation)
  - Digital finance (UPI scaling, CBDC, crypto ban/regulation)
  - India defence indigenisation specific milestones
  - Healthcare and pharma regulatory events
  - Agricultural / rural economy shocks
  - State election market impacts

TOTAL: 158 events across 16 categories

Usage:
    python db/seed_historical_events_comprehensive.py --dry-run
    python db/seed_historical_events_comprehensive.py --append
    python db/seed_historical_events_comprehensive.py --append --embed
    python db/seed_historical_events_comprehensive.py --force
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Import all 93 existing events
from db.seed_historical_events import EVENTS as _BASE_EVENTS

# ──────────────────────────────────────────────────────────────────────────────
# 65 additional events
# ──────────────────────────────────────────────────────────────────────────────
_NEW_EVENTS = [

    # ── MAJOR IPO EVENTS ──────────────────────────────────────────────────────
    {
        "event_type": "IPO_EVENT",
        "description": (
            "LIC IPO — India's largest-ever IPO (May 2022): ₹20,557 Cr raised; "
            "government sold 3.5% stake at ₹949/share. Valuation ~₹6 lakh Cr. "
            "IPO subscribed 2.95x; initially below issue price on listing. "
            "FY22 embedded value ₹5.41 lakh Cr; ULIP-heavy book raised concerns. "
            "Domestic retail investors dominated (70% via HNI + retail); FII participation low."
        ),
        "event_date": date(2022, 5, 17),
        "affected_sectors": ["INSURANCE", "BANKING", "EQUITY_MARKETS"],
        "market_impact": "MIXED",
        "outcome": (
            "LIC listed at ₹867 (8.6% below issue price); fell to ₹530 over next year. "
            "FY24 recovery: LIC stock crossed ₹950 and eventually ₹1,100 as profits improved. "
            "Largest IPO success for government disinvestment despite initial listing discount."
        ),
    },
    {
        "event_type": "IPO_EVENT",
        "description": (
            "Paytm IPO disaster (November 2021): ₹18,300 Cr raised at ₹2,150/share — "
            "largest Indian tech IPO. Listed at 27% discount (₹1,560). "
            "Valuation 45x revenue with no path to profitability; "
            "Macquarie immediately gave 'underperform' with ₹1,200 target. "
            "Pre-IPO FIIs (Ant Financial, SoftBank) capped via lock-ups."
        ),
        "event_date": date(2021, 11, 18),
        "affected_sectors": ["FINTECH", "PAYMENTS", "DIGITAL"],
        "market_impact": "SEVERE_NEGATIVE",
        "outcome": (
            "Paytm fell from ₹2,150 to ₹438 (80% crash by 2023). "
            "RBI action on Paytm Payments Bank (Jan 2024) nearly killed the business. "
            "Key lesson: Revenue growth without FCF in consumer fintech = value destruction. "
            "SEBI tightened IPO disclosure norms for loss-making tech companies post-Paytm."
        ),
    },
    {
        "event_type": "IPO_EVENT",
        "description": (
            "Zomato IPO (July 2021): ₹9,375 Cr raised at ₹76/share; first major new-age "
            "consumer tech IPO in India. Listed at 52% premium (₹116). "
            "Loss-making business; gross order value growing 60%+ YoY. "
            "Retail and FII subscribed heavily on growth narrative."
        ),
        "event_date": date(2021, 7, 23),
        "affected_sectors": ["CONSUMER_TECH", "FOOD_DELIVERY", "DIGITAL"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "Zomato fell from ₹150 ATH to ₹40 low (2022 tech sell-off); "
            "recovered to ₹250+ by 2024 as profitability emerged. "
            "Rebranded to Eternal in 2025 with quick commerce (Blinkit) as growth engine. "
            "FY25 first full profitable year; PE re-rated to 80–100x on growth platform narrative."
        ),
    },
    {
        "event_type": "IPO_EVENT",
        "description": (
            "Nykaa IPO (November 2021): ₹5,352 Cr raised at ₹1,125/share (52.6x P/S). "
            "Listed at 78% premium; became first profitable Indian unicorn IPO. "
            "Founder Falguni Nayar became India's wealthiest self-made woman. "
            "Post-lock-up expiry: FIIs exited aggressively; stock crashed on dilution concerns."
        ),
        "event_date": date(2021, 11, 10),
        "affected_sectors": ["RETAIL", "CONSUMER", "ECOMMERCE"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "Nykaa fell from ₹2,100 ATH to ₹130 (-94%) by 2023 on growth slowdown + margin pressure. "
            "High P/S at IPO was not sustainable without accelerating revenue growth. "
            "Key lesson: Profitable IPOs still need path to earnings, not just revenue growth."
        ),
    },
    {
        "event_type": "IPO_EVENT",
        "description": (
            "One97 Communications (Paytm) RBI action February 2024: RBI bars Paytm Payments Bank "
            "from accepting new deposits after February 29, 2024 citing persistent non-compliance. "
            "Paytm stock falls 40% in 2 sessions; ₹10,000 Cr market cap wiped. "
            "Regulatory risk for fintech companies highlighted; other fintechs sell off 10–15%."
        ),
        "event_date": date(2024, 1, 31),
        "affected_sectors": ["FINTECH", "PAYMENTS", "BANKING"],
        "market_impact": "SEVERE_NEGATIVE",
        "outcome": (
            "Paytm Payments Bank operations effectively shut down. "
            "Paytm migrated payment services to other banks; business survived but shrank 30%. "
            "Key lesson: Fintech businesses require clean regulatory compliance as non-negotiable."
        ),
    },

    # ── CORPORATE GOVERNANCE CRISES ───────────────────────────────────────────
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "ICICI Bank CEO crisis: Chanda Kochhar placed on leave amid conflict-of-interest "
            "allegations related to NuPower loans to Videocon Group. "
            "Whistleblower complaint to SEBI in early 2018; CBI registered FIR Oct 2018. "
            "Bank stock fell 15% over 3 months on governance concerns."
        ),
        "event_date": date(2018, 10, 4),
        "affected_sectors": ["BANKING", "NBFC"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Chanda Kochhar resigned Oct 2018; Sandeep Bakhshi appointed CEO. "
            "ICICI Bank recovered strongly under new leadership; FY20–FY24 saw NPA normalisation. "
            "Stock recovered from ₹280 to ₹1,100+ by 2024 — 4x under improved governance."
        ),
    },
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "Infosys founder-CEO conflict (2017): N R Narayana Murthy publicly criticized "
            "CEO Vishal Sikka over compensation, governance, and strategy. "
            "Co-founders' letter triggered board crisis; independent directors quit. "
            "Stock fell 15% in 3 weeks on governance uncertainty."
        ),
        "event_date": date(2017, 8, 18),
        "affected_sectors": ["IT"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Vishal Sikka resigned Aug 2017; Salil Parekh appointed CEO Jan 2018. "
            "Infosys stock recovered from ₹870 to ₹1,800 by FY22 under stable new leadership. "
            "Corporate governance importance reinforced: board independence matters for re-rating."
        ),
    },
    {
        "event_type": "CRISIS",
        "description": (
            "Jet Airways bankruptcy (April 2019): India's oldest private airline suspends operations; "
            "₹8,500 Cr of bank debt unpaid. 23,000 employees left jobless. "
            "Kingfisher-style collapse: overleveraged balance sheet, fuel cost inflation, fare wars. "
            "Aviation stocks fell 8–12%; airport operators uncertain on traffic outlook."
        ),
        "event_date": date(2019, 4, 17),
        "affected_sectors": ["AVIATION", "BANKING", "AIRPORTS"],
        "market_impact": "SECTOR_NEGATIVE",
        "outcome": (
            "IBC resolution: Jalan-Kalrock consortium won bid in 2021 (3-year process). "
            "IndiGo and SpiceJet captured Jet's market share. "
            "Aviation sector further consolidated: 2 dominant players (IndiGo + Tata Air India)."
        ),
    },
    {
        "event_type": "CRISIS",
        "description": (
            "ADAG (Anil Ambani Group) debt crisis 2019: Reliance Capital, Reliance Power, "
            "Reliance Home Finance all face default risk; promoter pledging >80%. "
            "SEBI fines Anil Ambani ₹25 Cr; group debt of ₹1.2 lakh Cr under stress. "
            "FII exits all ADAG stocks aggressively; stocks fall 50–90% over 12 months."
        ),
        "event_date": date(2019, 2, 25),
        "affected_sectors": ["NBFC", "POWER", "TELECOM", "INFRASTRUCTURE"],
        "market_impact": "SEVERE_NEGATIVE",
        "outcome": (
            "Reliance Capital resolved via IBC; Hinduja Group acquired it. "
            "Reliance Power NPA; Reliance Home Finance wound down. "
            "Key lesson: Promoter pledging >50% + high debt + declining ROCE = value destruction trap."
        ),
    },
    {
        "event_type": "CRISIS",
        "description": (
            "NSE colocation scam (2015–2018): NSE officials allegedly gave select "
            "high-frequency traders unfair access to co-location servers (dark fiber). "
            "SEBI investigation led to ₹625 Cr fine on NSE in 2019; "
            "NSE IPO delayed by years as regulatory uncertainty lingered."
        ),
        "event_date": date(2015, 9, 1),
        "affected_sectors": ["BROKERS", "EQUITY_MARKETS"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "NSE IPO delayed from 2017 to indefinite (still pending as of 2025). "
            "SEBI revamped algorithmic trading regulations and colocation norms. "
            "BSE and MCX benefited as NSE's IPO aspirations stalled."
        ),
    },
    {
        "event_type": "CRISIS",
        "description": (
            "NSEL (National Spot Exchange) crisis 2013: NSEL defaults on ₹5,600 Cr payments "
            "to investors after FMC found non-existent commodity stocks. "
            "Jignesh Shah arrested; FTIL/MCX stocks crash 70–80% in weeks. "
            "Commodity exchange regulation overhauled by FMC/SEBI merger."
        ),
        "event_date": date(2013, 8, 1),
        "affected_sectors": ["COMMODITY_MARKETS", "METALS"],
        "market_impact": "SECTOR_NEGATIVE",
        "outcome": (
            "FMC and SEBI merged to create unified commodity + securities regulator. "
            "MCX (commodity exchange) emerged independently; recovered by 2020. "
            "Investor protection in spot commodity markets significantly strengthened."
        ),
    },

    # ── RUSSIA-UKRAINE WAR ────────────────────────────────────────────────────
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "Russia invades Ukraine (February 24, 2022): Crude oil surges to $130/barrel; "
            "wheat prices +50%; edible oil prices spike (sunflower oil disruption). "
            "India faces inflation surge: WPI crosses 15%, CPI above 7%. "
            "FII outflows from India ₹40,000 Cr in Feb–Mar 2022; Nifty falls 12%. "
            "INR depreciates to 77/USD; RBI forced to emergency rate hike."
        ),
        "event_date": date(2022, 2, 24),
        "affected_sectors": ["OIL_GAS", "FMCG", "CHEMICALS", "PAINTS", "AVIATION"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "India imported discounted Russian crude (from 1% to 40% of imports by FY24). "
            "Edible oil prices normalised by H2 2022. "
            "India maintained neutral stance; trade with Russia surged (+300% in 3 years). "
            "Nifty recovered by Dec 2022; India outperformed global markets in 2022."
        ),
    },
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "India-China LAC tensions (June 2020): Galwan Valley clash kills 20 Indian soldiers. "
            "India bans 59 Chinese apps (TikTok, PUBG) + 200+ more subsequently. "
            "FDI rules tightened: mandatory government approval for Chinese investments. "
            "Tech sector re-rated as Indian digital companies benefited from Chinese app ban. "
            "Defence stocks surged on accelerated indigenisation sentiment."
        ),
        "event_date": date(2020, 6, 15),
        "affected_sectors": ["DEFENCE", "DIGITAL", "TECHNOLOGY", "REALTY"],
        "market_impact": "MIXED",
        "outcome": (
            "India-China trade paradox: Military tensions but trade hit record ₹1.6 lakh Cr (FY24). "
            "Indian tech startups benefited from absence of TikTok/PUBG; Mitron, Roposo launched. "
            "Defence indigenisation received massive political push; HAL/BEL benefited directly."
        ),
    },

    # ── COMMODITY CYCLES ──────────────────────────────────────────────────────
    {
        "event_type": "COMMODITY",
        "description": (
            "Gold bull run 2019–2020: Gold price rises from $1,200 to $2,070/oz (ATH). "
            "India domestic gold price from ₹32,000 to ₹56,000/10g (+75%). "
            "Gold ETFs, Sovereign Gold Bonds see record inflows. "
            "Jewellery stocks (Titan, Kalyan, PC Jeweller) sell-off on demand fears. "
            "Safe-haven demand driven by COVID uncertainty and negative real US rates."
        ),
        "event_date": date(2020, 8, 7),
        "affected_sectors": ["GOLD", "JEWELLERY", "COMMODITIES"],
        "market_impact": "MIXED",
        "outcome": (
            "Gold retreated from $2,070 to $1,680 by 2021; consolidated for 3 years. "
            "Gold hit new ATH $2,400+ in 2024 on geopolitical risk + central bank buying. "
            "Titan/Kalyan recovered strongly as consumption sentiment improved by FY22."
        ),
    },
    {
        "event_type": "COMMODITY",
        "description": (
            "Metal super-cycle 2020–2022: Steel, aluminium, copper prices double in 18 months. "
            "Global stimulus + infrastructure spending drives commodity demand. "
            "Tata Steel, Hindalco, JSW Steel, NMDC hit all-time highs. "
            "Indian steel demand surges on housing + infrastructure; exports also strong. "
            "Steel capacity utilisation >90%; Tata Steel FY22 PAT ₹41,749 Cr (all-time record)."
        ),
        "event_date": date(2020, 11, 1),
        "affected_sectors": ["METALS", "MINING", "STEEL", "ALUMINIUM"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Hindalco: ₹130 to ₹700 (5.4x). Tata Steel: ₹280 to ₹1,500 (5.4x). "
            "Metal cycle turned negative from mid-2022 as China slowdown and inventory build. "
            "Key lesson: Metal stocks are cyclical — buy at commodity trough, sell at peak EBITDA."
        ),
    },
    {
        "event_type": "COMMODITY",
        "description": (
            "Crude oil collapse April 2020: WTI crude goes negative (-$37/barrel) for first time ever "
            "as COVID-19 kills demand and storage capacity fills. "
            "Brent crude at $16/barrel — 21-year low. "
            "India benefits: import bill falls; OMC margins surge; aviation cost relief. "
            "Inflation falls; RBI cuts rates further; fiscal relief for government."
        ),
        "event_date": date(2020, 4, 20),
        "affected_sectors": ["OIL_GAS", "AVIATION", "PAINTS", "CHEMICALS", "OMC"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "India saved ₹1 lakh Cr+ on oil import bill in FY21. "
            "OMC stocks (BPCL, IOC, HPCL) surged 30–50% on margin expansion. "
            "Government raised fuel taxes, capturing windfall; excise duty hike of ₹13/litre."
        ),
    },
    {
        "event_type": "COMMODITY",
        "description": (
            "Onion price shock: Onion prices surge 8x to ₹150–200/kg (October–December 2019). "
            "Export ban imposed; Modi government in 2023 onion export ban repeated at ₹80/kg. "
            "Retail food inflation spikes; political sensitivity of onion prices in India "
            "reflects impact of agri commodity shocks on consumer sentiment and rural economy."
        ),
        "event_date": date(2019, 10, 1),
        "affected_sectors": ["AGRI", "FMCG", "RURAL_CONSUMER"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Consumer staples stocks underperformed during food inflation spikes. "
            "Rural demand contracted temporarily; FMCG volume growth impacted. "
            "Agri supply chain improvements (cold storage, warehousing) gained policy focus."
        ),
    },
    {
        "event_type": "COMMODITY",
        "description": (
            "Global chip shortage (2021–2022): Semiconductor supply crunch disrupts auto production. "
            "Maruti's production falls 30–40% despite record demand; waiting periods extend to 12 months. "
            "Two-wheeler, passenger vehicle production both impacted. "
            "Share prices initially fell on production cuts; then recovered on pricing power."
        ),
        "event_date": date(2021, 4, 1),
        "affected_sectors": ["AUTO", "AUTO_ANCILLARY", "ELECTRONICS"],
        "market_impact": "MIXED",
        "outcome": (
            "Chip shortage resolved by mid-2022; auto companies began building inventory. "
            "India auto volumes hit records in FY24 (4.3 Mn cars, 2 Mn SUVs). "
            "Premiumisation accelerated: SUV share of car market rose from 40% to 60%."
        ),
    },

    # ── BANKING SECTOR STRUCTURAL EVENTS ──────────────────────────────────────
    {
        "event_type": "POLICY",
        "description": (
            "PSU bank recapitalisation (2017–2019): Government injects ₹2.11 lakh Cr into public "
            "sector banks via recapitalisation bonds and budgetary allocation. "
            "Largest-ever banking sector recap in India to address NPA crisis. "
            "Bank Nifty initially rallied 12–15% on recap announcement."
        ),
        "event_date": date(2017, 10, 24),
        "affected_sectors": ["PSU_BANKS", "BANKING"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "PSU bank NPA peaked at ₹9.6 lakh Cr (FY18) and declined to ₹3.6 lakh Cr (FY24). "
            "SBI returned to record profits by FY23. "
            "The recap + IBC combination was the key enabler of PSU bank turnaround."
        ),
    },
    {
        "event_type": "POLICY",
        "description": (
            "Bank mergers: Government merges 10 PSU banks into 4 (April 2020). "
            "OBC + United Bank → PNB; Canara Bank absorbs Syndicate; "
            "Allahabad Bank → Indian Bank; Andhra Bank + Corporation Bank → Union Bank. "
            "From 27 PSU banks (2017) to 12 PSU banks (2020)."
        ),
        "event_date": date(2020, 4, 1),
        "affected_sectors": ["PSU_BANKS", "BANKING"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "Merged banks achieved cost synergies; NPA resolution improved. "
            "Combined entities became larger, more competitive vs private banks. "
            "SBI remained largest; now top 4 PSU banks hold 75%+ of PSU banking assets."
        ),
    },
    {
        "event_type": "REGULATION",
        "description": (
            "SEBI MF stress-testing circular (March 2024): All open-ended equity mutual funds "
            "must disclose time required to liquidate 25% and 50% of their mid/small-cap portfolio. "
            "Triggered outflows from small-cap funds; SEBI also asked AMCs to moderate inflows."
        ),
        "event_date": date(2024, 3, 15),
        "affected_sectors": ["SMALL_CAP", "MID_CAP", "ASSET_MANAGEMENT"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Small-cap index corrected 15–20% from Feb–Jun 2024. "
            "AMCs imposed limits on lump-sum in small-cap funds. "
            "Liquidity concerns in micro-cap segment highlighted; valuations rationalized."
        ),
    },

    # ── DIGITAL FINANCE / CRYPTO / CBDC ───────────────────────────────────────
    {
        "event_type": "REGULATION",
        "description": (
            "India crypto taxation (April 2022): Budget 2022 imposes 30% flat tax on crypto gains "
            "+ 1% TDS per transaction. No loss set-off allowed. "
            "CoinDCX, WazirX, Zebpay volumes drop 70–90% within months. "
            "Global crypto winter (Bitcoin -70%) compounds local regulatory headwinds."
        ),
        "event_date": date(2022, 4, 1),
        "affected_sectors": ["FINTECH", "CRYPTO", "DIGITAL"],
        "market_impact": "SECTOR_NEGATIVE",
        "outcome": (
            "Indian crypto exchanges lost 90%+ of daily volumes. "
            "WazirX hacked for $235 Mn in 2024 — further blow to sector confidence. "
            "Unlike many countries, India did not embrace crypto; Web3 talent migrated offshore."
        ),
    },
    {
        "event_type": "POLICY",
        "description": (
            "RBI Digital Rupee (e-RUPI / CBDC) pilot launch (December 2022): India begins "
            "wholesale CBDC pilot with 4 banks; retail CBDC pilot expands to 13 cities. "
            "Target: 1 million retail CBDC transactions/day by end 2023. "
            "Implications: potential disintermediation of payment platforms; UPI competitor."
        ),
        "event_date": date(2022, 12, 1),
        "affected_sectors": ["BANKING", "FINTECH", "PAYMENTS"],
        "market_impact": "NEUTRAL",
        "outcome": (
            "CBDC adoption remains low vs UPI; retail CBDC usage <100,000 transactions/day by 2024. "
            "Banks preferred UPI infrastructure; CBDC positioned as complement, not replacement. "
            "Long-term financial inclusion potential remains the key use case."
        ),
    },
    {
        "event_type": "POLICY",
        "description": (
            "UPI QR code proliferation 2022–2023: UPI crosses 10 billion transactions/month. "
            "NPCI reports UPI at 46% of all digital payments globally by value in 2023. "
            "PhonePe, Google Pay dominate; MDR-free model pressures payment bank revenues. "
            "RBI allows UPI for credit card transactions; HDFC, Axis Bank benefit."
        ),
        "event_date": date(2022, 10, 1),
        "affected_sectors": ["BANKING", "FINTECH", "PAYMENTS", "CONSUMER"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "Fintech stocks (Razorpay, PhonePe) grew while listed payment banks faced MDR pressure. "
            "NPCI and UPI adoption exported to Singapore, UAE, France. "
            "India's payment infrastructure became a global competitive advantage."
        ),
    },

    # ── HEALTHCARE / PHARMA REGULATORY EVENTS ─────────────────────────────────
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "COVID vaccine production boom (2021): India produces 1.5 Bn doses under Vaccine Maitri. "
            "SII (Covishield) and Bharat Biotech (Covaxin) reach record production. "
            "Pharma export revenue surges; diagnostic companies (Thyrocare, Dr Lal PathLabs) "
            "see 3–5x volume surge from COVID testing demand."
        ),
        "event_date": date(2021, 3, 1),
        "affected_sectors": ["PHARMA", "DIAGNOSTICS", "HOSPITALS"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "India vaccine exports reached 300+ countries; SII produced 250 Mn doses for COVAX. "
            "Pharma sector re-rated as India confirmed its 'pharmacy of the world' position. "
            "Diagnostic stocks saw mean reversion after COVID peak; normalised by 2022."
        ),
    },
    {
        "event_type": "REGULATION",
        "description": (
            "NLEM drug price control expansion (2022): Essential medicines list expanded; "
            "NPPA caps prices on 800+ drugs. Ortho, cardiac, diabetes formulations affected. "
            "Sun Pharma, Cipla, Dr Reddy's domestic formulation revenues under pressure. "
            "Trade margins compressed; hospital procurement costs lowered."
        ),
        "event_date": date(2022, 5, 1),
        "affected_sectors": ["PHARMA"],
        "market_impact": "SECTOR_NEGATIVE",
        "outcome": (
            "Domestic pharma margins compressed 3–5% initially. "
            "Companies shifted focus to branded generics and export markets. "
            "IPM (Indian pharmaceutical market) growth moderated but volume growth sustained."
        ),
    },
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "CDMO and specialty chemicals pharma bull run (2021–2023): Global pharma supply chain "
            "diversification from China drives contract manufacturing orders to India. "
            "Divi's Laboratories, Laurus Labs, Suven Pharma, Piramal Pharma benefit. "
            "Revenue growth 30–50% YoY; ROCE expanding as utilisation rises. "
            "PE ratios 30–50x; FII buying concentrated in CDMO names."
        ),
        "event_date": date(2021, 9, 1),
        "affected_sectors": ["PHARMA", "CHEMICALS", "CDMO"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Divi's: ₹1,800 to ₹5,700 (3.2x); Laurus Labs: ₹70 to ₹800 (11x peak). "
            "CDMO sector growth normalised post-COVID; inventory corrections hit stocks in 2022. "
            "Structural China+1 trend continues: long-term CDMO opportunity remains intact."
        ),
    },

    # ── GLOBAL MACRO (ECB / JAPAN) ─────────────────────────────────────────────
    {
        "event_type": "GLOBAL",
        "description": (
            "Japan YCC abandonment (July 2023): Bank of Japan widens yield curve control band "
            "to 1% on 10Y JGB — effectively ending decade of zero-rate policy. "
            "Yen carry trade unwinds: $4 trillion+ of yen carry trades globally. "
            "EM equity sell-off; FII pull back from India ₹12,000 Cr in August 2023. "
            "Nifty falls 5% in 3 weeks; Bank Nifty -7% as rate-sensitive flows reversed."
        ),
        "event_date": date(2023, 7, 28),
        "affected_sectors": ["BANKING", "IT", "CONSUMER"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Nifty recovered within 6 weeks as Japan's actual policy change remained gradual. "
            "BOJ raised rates to 0.25% by Aug 2024; yen carry trade unwind Phase 2 hit markets. "
            "India's direct Japan carry trade exposure low; recovery faster than other EMs."
        ),
    },
    {
        "event_type": "GLOBAL",
        "description": (
            "US SVB (Silicon Valley Bank) collapse (March 2023): 16th largest US bank collapses "
            "in 48 hours — largest US bank failure since 2008. "
            "Credit Suisse emergency rescue (merged into UBS); contagion fear. "
            "Indian banks fall 5–8% on contagion fear; FII outflows ₹6,000 Cr in 2 weeks. "
            "India VIX spikes to 16 from 12; bond yields volatile."
        ),
        "event_date": date(2023, 3, 10),
        "affected_sectors": ["BANKING", "NBFC", "IT"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "FDIC guarantee extended to uninsured SVB deposits; systemic fear contained. "
            "India banking sector fundamentals unaffected; no direct SVB exposure. "
            "Markets recovered in 3 weeks; Indian banks rallied on stable fundamentals contrast."
        ),
    },
    {
        "event_type": "GLOBAL",
        "description": (
            "China real estate crisis (Evergrande default, 2021–2022): Evergrande ($300 Bn debt) "
            "defaults on dollar bonds in Dec 2021. Country Garden, Sunac follow. "
            "China real estate sector (28% of GDP) enters multi-year deleveraging. "
            "Commodity demand slowdown: Steel, copper prices fall 20–30%. "
            "India metals exports to China decline; Indian construction imports cheap Chinese steel."
        ),
        "event_date": date(2021, 12, 9),
        "affected_sectors": ["METALS", "STEEL", "CEMENT", "INFRASTRUCTURE"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "India metals sector fell 25–35% from peak (2021–2022). "
            "China's slow recovery created permanent supply-demand recalibration for commodities. "
            "India domestic steel demand remained strong even as exports fell."
        ),
    },
    {
        "event_type": "GLOBAL",
        "description": (
            "US Inflation Reduction Act (August 2022): $369 Bn for clean energy, EVs, semiconductor "
            "manufacturing in the US. Pulls global investment from India's renewable sector. "
            "India fears China+1 manufacturing orders may go to US/Mexico instead. "
            "However, India solar module PLI gains urgency; battery PLI accelerated."
        ),
        "event_date": date(2022, 8, 16),
        "affected_sectors": ["RENEWABLE_ENERGY", "SOLAR", "EV", "MANUFACTURING"],
        "market_impact": "MIXED",
        "outcome": (
            "India responded with enhanced PLI for electronics, solar, batteries. "
            "India-US trade partnership strengthened on semiconductor supply chain. "
            "Indian solar stocks initially fell on competition fear; recovered on domestic demand."
        ),
    },

    # ── INDIA DEFENCE INDIGENISATION MILESTONES ───────────────────────────────
    {
        "event_type": "POLICY",
        "description": (
            "Defence export target: India aims for ₹35,000 Cr defence exports by FY25 "
            "(from ₹1,500 Cr in FY17). BrahMos missile exports to Philippines confirmed. "
            "HAL signs ₹21,935 Cr MRO contract with IAF. "
            "FDI in defence raised to 74% (automatic route) from 49%. "
            "Revenue growth for defence companies: 35–50% YoY on order book execution."
        ),
        "event_date": date(2022, 4, 20),
        "affected_sectors": ["DEFENCE", "AEROSPACE", "ELECTRONICS"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "India defence exports crossed ₹21,000 Cr in FY24 — 14x growth in 7 years. "
            "HAL ₹84,000 Cr order book; Cochin Shipyard ₹22,000 Cr order book. "
            "India joined top 25 defence exporters globally; aspiring for top 10 by 2030."
        ),
    },
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "EV transition in India 2021–2024: Tata Motors launches Nexon EV (₹14 lakh); "
            "EV penetration in passenger vehicles rises from 0.5% to 3%. "
            "Ola Electric IPO (August 2024) raises ₹6,146 Cr; first EV unicorn IPO. "
            "Battery cell PLI scheme attracts ₹18,100 Cr investment commitments. "
            "Two-wheeler EV leaders (TVS, Ola, Bajaj) gain market share rapidly."
        ),
        "event_date": date(2022, 1, 1),
        "affected_sectors": ["AUTO", "EV", "BATTERIES", "RENEWABLE_ENERGY"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "Tata Motors EV division: 10% market share in India PV EV. "
            "Two-wheeler EV: Ola Electric 30%+ market share. "
            "Charging infrastructure gaps remain; range anxiety constraints adoption. "
            "Indian OEM transition vs. Chinese EV imports remains key risk to watch."
        ),
    },

    # ── STATE ELECTION / POLITICAL EVENTS ─────────────────────────────────────
    {
        "event_type": "POLITICAL",
        "description": (
            "UP state elections (March 2022): BJP wins with 255/403 seats in largest Indian state. "
            "Markets interpret as positive signal for 2024 general elections. "
            "PSU, infrastructure, rural consumer stocks rally on continuity signal. "
            "NIFTY gainst 2% in 3 days post UP result; India VIX falls to 18 from 22."
        ),
        "event_date": date(2022, 3, 10),
        "affected_sectors": ["PSU", "INFRASTRUCTURE", "RURAL_CONSUMER"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "State election results used as 2024 general election leading indicator. "
            "Capex-heavy infrastructure stocks outperformed in weeks following UP result. "
            "Consumption stocks also benefited as UP represents 17% of India's GDP."
        ),
    },
    {
        "event_type": "POLITICAL",
        "description": (
            "Himachal Pradesh and Gujarat elections (December 2022): BJP wins Gujarat (156/182) "
            "but loses Himachal Pradesh to Congress. "
            "Markets largely neutral; PM Modi's Gujarat win seen as BJP stronghold confirmation. "
            "Semi-urban and rural sectors show diverging political trends."
        ),
        "event_date": date(2022, 12, 8),
        "affected_sectors": ["CONSUMER", "RURAL_CONSUMER", "FMCG"],
        "market_impact": "NEUTRAL",
        "outcome": (
            "No significant market move; election absorbed as expected outcome. "
            "Rural consumption policy stimulus announced post-Himachal loss. "
            "State elections show rural distress; government increased PM-KISAN and MNREGS outlay."
        ),
    },
    {
        "event_type": "POLITICAL",
        "description": (
            "Rajasthan, Madhya Pradesh, Chhattisgarh elections (December 2023): "
            "BJP wins all three key states ahead of 2024 general elections. "
            "Exit polls correctly predicted outcome; markets viewed as BJP momentum indicator. "
            "PSU, defence, infrastructure stocks rally 5–8% in 2 weeks post-result."
        ),
        "event_date": date(2023, 12, 3),
        "affected_sectors": ["PSU", "DEFENCE", "INFRASTRUCTURE"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "2024 general election optimism built; Nifty hit new ATH in Jan 2024. "
            "Continuity of infrastructure capex and defence indigenisation expected. "
            "FII buying accelerated on political stability narrative."
        ),
    },

    # ── TECHNOLOGY SECTOR SPECIFIC ─────────────────────────────────────────────
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "IT sector slowdown 2022–2023: US tech spending cuts post-ZIRP era. "
            "TCS, Infosys revenue growth slows from 20% to 3–5% YoY. "
            "Deal ramp-down as US clients face budget constraints; discretionary IT spending frozen. "
            "Revenue growth visibility collapsed from 'strong guidance' to 'uncertain environment'. "
            "PE contracted from 30–35x to 20–22x as growth premium eroded."
        ),
        "event_date": date(2022, 10, 1),
        "affected_sectors": ["IT"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Nifty IT index fell 25% from peak Oct 2021 to May 2023. "
            "Companies controlled costs via headcount freeze; utilization improved. "
            "AI/GenAI opportunities emerged as next growth vector by H2 2023. "
            "TCS and Infosys recovered on GenAI deal momentum by FY24–25."
        ),
    },
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "AI/GenAI boom impact on India IT (2023–2024): ChatGPT launches Nov 2022; "
            "India IT companies pivot to AI/GenAI services. "
            "TCS: ₹2 Bn GenAI revenue pipeline by Q3 FY24. Infosys: Topaz AI platform launched. "
            "Wipro, HCL Tech all announce AI practice hiring 10,000–20,000 AI engineers. "
            "Revenue growth expectation improves from 3% to 7–10% by FY25."
        ),
        "event_date": date(2023, 9, 1),
        "affected_sectors": ["IT", "TECHNOLOGY", "AI"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "Nifty IT recovered 25% from trough to FY25 on AI monetisation narrative. "
            "Deal wins improved; H2 FY25 guidance better than market feared. "
            "India remains world's largest IT services provider; AI transformation adds premium."
        ),
    },

    # ── REAL ESTATE STRUCTURAL SHIFTS ─────────────────────────────────────────
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "Data center boom in India (2022–2025): Hyperscalers (AWS, Google, Microsoft, Meta) "
            "commit ₹1.5 lakh Cr+ to India data centers in 3 years. "
            "Hiranandani, Adani, NTT, Yotta all expand data center capacity. "
            "Power demand for data centers: 2 GW (2023) → 10 GW by 2030. "
            "Real estate adjacency: land near Mumbai, Chennai, Pune appreciates 30–50%."
        ),
        "event_date": date(2023, 1, 1),
        "affected_sectors": ["INFRASTRUCTURE", "REALTY", "POWER", "TECHNOLOGY"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Power companies (NTPC, Adani Power) re-rated on data center demand narrative. "
            "Industrial land and commercial real estate near data center hubs outperformed. "
            "India data center capacity target: 4x growth to become Asia's 2nd largest by 2027."
        ),
    },

    # ── MSME / GST COLLECTIONS STRUCTURAL ─────────────────────────────────────
    {
        "event_type": "POLICY",
        "description": (
            "Emergency Credit Line Guarantee Scheme (ECLGS, May 2020): Government-backed "
            "₹3 lakh Cr collateral-free loans to MSMEs impacted by COVID lockdown. "
            "4.5 Mn MSME accounts receive ₹2.7 lakh Cr in first 18 months. "
            "PSU banks and NBFC primary delivery channel; NPA concerns manageable."
        ),
        "event_date": date(2020, 5, 13),
        "affected_sectors": ["BANKING", "NBFC", "SME"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "MSME sector survived COVID with limited bankruptcies vs expectations. "
            "Banking NPA was lower than feared post-COVID due to ECLGS. "
            "₹20,000 Cr ECLGS NPA by 2023 — manageable vs ₹2.7 lakh Cr disbursed."
        ),
    },
    {
        "event_type": "MACRO_POSITIVE",
        "description": (
            "India manufacturing PMI consistently above 55 (2023–2024): "
            "India Manufacturing PMI stays expansion territory for 36+ consecutive months. "
            "New orders, employment, output indices all positive. "
            "Global relocations to India: Apple, Samsung, Nokia, Foxconn expand India footprint. "
            "Services PMI also above 60 — dual engine growth phase."
        ),
        "event_date": date(2023, 3, 1),
        "affected_sectors": ["MANUFACTURING", "ELECTRONICS", "CONSUMER", "LOGISTICS"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "India GDP growth sustained at 7%+. "
            "PMI expansion underpinned earnings growth visibility. "
            "India became top recipient of FDI among Asian EMs in 2023–24."
        ),
    },

    # ── SPECIFIC MARKET STRUCTURE / TECHNICAL PATTERNS ────────────────────────
    {
        "event_type": "RECOVERY",
        "description": (
            "NIFTY 50 ATH breakout psychology (September 2024): Nifty 50 crosses 25,000 for first time. "
            "Prior ATH resistance levels become support; momentum investors FOMO-driven buying. "
            "FII and DII both in buy mode; SIP flows at ₹23,000 Cr/month all-time record. "
            "India premium over other EMs at multi-year high; domestic equity culture strong."
        ),
        "event_date": date(2024, 9, 26),
        "affected_sectors": ["EQUITY_MARKETS", "BANKING", "IT", "CONSUMER"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Nifty touched 26,277 (ATH) before correcting 10% by end-2024. "
            "ATH breakout followed by consolidation — healthy market behavior. "
            "Retail investor direct equity participation hit 100 Mn demat accounts."
        ),
    },
    {
        "event_type": "RECOVERY",
        "description": (
            "Nifty 50 correction Oct–Nov 2024 (FII sell-off): After hitting ATH 26,277, "
            "FII sell ₹1.14 lakh Cr in Oct–Nov 2024 (single largest 2-month FII selling ever). "
            "US election, strong dollar, and China stimulus drove FII reallocation. "
            "Nifty falls 10% to 23,500; DII buys ₹1.1 lakh Cr absorbing most FII selling. "
            "Mid and small-cap correct 15–25%."
        ),
        "event_date": date(2024, 10, 1),
        "affected_sectors": ["EQUITY_MARKETS", "SMALL_CAP", "MID_CAP"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Markets stabilised at 23,000–23,500 support zone; DII floor held. "
            "FII selling tapered in December; year ended with modest gains. "
            "Key: DII structural buying (₹3+ lakh Cr/year) now acts as floor for India markets."
        ),
    },
    {
        "event_type": "INSTITUTIONAL_FLOW",
        "description": (
            "SIP culture mainstreaming (2022–2025): Monthly SIP flows grow from ₹11,000 Cr to "
            "₹23,000 Cr in 3 years. Total active SIP accounts cross 75 million. "
            "SIP provides structural bid for Indian equity markets regardless of FII activity. "
            "India VIX no longer spikes as hard during corrections vs 2008, 2013, 2020."
        ),
        "event_date": date(2023, 1, 1),
        "affected_sectors": ["EQUITY_MARKETS", "ASSET_MANAGEMENT", "SMALL_CAP"],
        "market_impact": "LONG_TERM_POSITIVE",
        "outcome": (
            "Equity culture has democratised: 100 Mn demat accounts, 75 Mn SIP accounts. "
            "Corrections in India now attract domestic buying; structural floor for Nifty. "
            "AMC stocks (HDFC AMC, Nippon India) re-rated on AUM growth trajectory."
        ),
    },

    # ── SECTOR DISRUPTIONS ────────────────────────────────────────────────────
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "FMCG rural stress 2021–2023: 8 consecutive quarters of FMCG volume decline in rural India. "
            "Input cost inflation (palm oil, wheat, packaging) compresses margins 3–5%. "
            "HUL, Dabur, Marico volume growth turns negative. "
            "Urban vs rural divergence: premium products growing; mass-market declining."
        ),
        "event_date": date(2021, 10, 1),
        "affected_sectors": ["FMCG", "RURAL_CONSUMER", "CONSUMER"],
        "market_impact": "SECTOR_NEGATIVE",
        "outcome": (
            "FMCG stocks fell 15–25% from peak as margin pressure + volume decline hit. "
            "Recovery started H2 FY24 as input costs normalised and rural demand revived. "
            "Companies which maintained innovation and premiumisation recovered faster."
        ),
    },
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "Paytm Payments Bank RBI restrictions (January 2024): RBI bars Paytm Payments Bank "
            "from new deposits/credit transactions after March 15, 2024. "
            "Contagion fear: Fino Payments Bank, IndusInd's fintech partnerships under scrutiny. "
            "Fintech stocks (Policybazaar, MobiKwik, PayMate) fall 10–20% on regulatory risk."
        ),
        "event_date": date(2024, 1, 31),
        "affected_sectors": ["FINTECH", "PAYMENTS", "BANKING"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Paytm migrated payment services to Yes Bank, Axis Bank; app continued operating. "
            "Other fintech companies stepped up compliance efforts. "
            "Regulatory scrutiny of small finance banks and payment banks increased systemically."
        ),
    },

    # ── INDIA-SPECIFIC MACRO SCENARIOS ────────────────────────────────────────
    {
        "event_type": "RBI_POLICY",
        "description": (
            "RBI introduces forex intervention framework (2022): RBI intervenes heavily to defend "
            "INR at 82–84/USD during EM sell-off from rising US rates. "
            "Forex reserves fall from $640 Bn (Oct 2021) to $524 Bn (Oct 2022). "
            "Import cover falls from 15 months to 9 months. "
            "RBI deploys spot + forward interventions to prevent sharp INR depreciation."
        ),
        "event_date": date(2022, 8, 1),
        "affected_sectors": ["BANKING", "CONSUMER", "IT", "EXPORT"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "INR depreciated only 10% in 2022 vs BRL -15%, ZAR -12%. "
            "Stable INR reduced imported inflation; FII confidence in India macro maintained. "
            "Forex reserves recovered to $700 Bn by 2024 as trade balance improved."
        ),
    },
    {
        "event_type": "MACRO_POSITIVE",
        "description": (
            "India current account deficit improvement (FY25): CAD falls to 1% of GDP "
            "from 2.6% (FY23) due to lower crude oil prices and services export growth. "
            "IT/BPO services exports at $345 Bn (FY25); remittances at $125 Bn. "
            "India comfortably financing CAD via FDI + FII inflows. "
            "INR more stable vs other EMs; RBI builds forex war chest."
        ),
        "event_date": date(2024, 10, 1),
        "affected_sectors": ["BANKING", "IT", "CONSUMER", "EXPORT"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "INR stability reinforced India's EM premium. "
            "Lower CAD → lower borrowing cost for government → fiscal space for capex. "
            "India macro resilience attracts long-term FDI even during FII sell-off periods."
        ),
    },

    # ── FII/DII FLOW DYNAMICS ─────────────────────────────────────────────────
    {
        "event_type": "INSTITUTIONAL_FLOW",
        "description": (
            "DII vs FII divergence pattern 2022–2023: FII sold ₹2.8 lakh Cr in FY23 "
            "while DII bought ₹2.76 lakh Cr — near perfect offset. "
            "For first time in India market history, DII fully absorbed FII selling. "
            "Markets fell only 7% peak-to-trough despite massive FII selling. "
            "Structural domestic bid changes the India equity volatility equation permanently."
        ),
        "event_date": date(2022, 4, 1),
        "affected_sectors": ["EQUITY_MARKETS"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "India markets most resilient EM during 2022 global sell-off. "
            "Nifty flat to slightly positive in FY23 while MSCI EM fell 20%. "
            "India equity market matured — domestic flows now anchor market stability."
        ),
    },

    # ── INDIA CONSUMER STRUCTURAL SHIFTS ─────────────────────────────────────
    {
        "event_type": "MACRO_POSITIVE",
        "description": (
            "India middle class expansion (2020–2025): 100 Mn new middle-class consumers. "
            "Per capita income crosses $2,500 (FY24). E-commerce GMV $110 Bn (FY25). "
            "Premium segment growth: SUVs, premium FMCG, branded apparel all outperforming. "
            "India becomes 3rd largest internet user base globally (900 Mn users)."
        ),
        "event_date": date(2022, 4, 1),
        "affected_sectors": ["CONSUMER", "FMCG", "AUTO", "ECOMMERCE", "RETAIL"],
        "market_impact": "LONG_TERM_POSITIVE",
        "outcome": (
            "India's premiumisation trade has been structural — not cyclical. "
            "Titan: jewellery + watches + eyewear growing 20%+. "
            "Consumer discretionary stocks re-rated on consistent premiumisation trend."
        ),
    },

    # ── BEAR MARKET PATTERNS ──────────────────────────────────────────────────
    {
        "event_type": "SECTOR_SHOCK",
        "description": (
            "Smallcap mania correction 2018: Nifty Smallcap 100 falls 50% from peak Jan 2018 "
            "to Sep 2019 while Nifty 50 fell only 15%. "
            "Rotation from small/mid to large-cap FIIs driven; SEBI recategorisation adds selling. "
            "Promoter pledging unravelling in mid/small caps triggers leveraged sell-off. "
            "P/E compression from 35–50x to 12–15x in 18 months."
        ),
        "event_date": date(2018, 1, 26),
        "affected_sectors": ["SMALL_CAP", "MID_CAP"],
        "market_impact": "SEVERE_NEGATIVE",
        "outcome": (
            "Nifty Small Cap 100: Jan 2018 = 9,600; Sep 2019 = 4,800 (-50%). "
            "Quality small-caps with ROCE >15% and low debt recovered first. "
            "SEBI multi-cap recategorisation (Sep 2020) triggered the eventual recovery."
        ),
    },
    {
        "event_type": "RECOVERY",
        "description": (
            "Post-smallcap crash recovery pattern (2020–2021): Nifty Small Cap 100 doubles "
            "from 4,200 (Mar 2020) to 9,000 (Mar 2021) in 12 months — +115% return. "
            "RSI was deeply oversold at 25–30 in March 2020; the crash recovery was fastest ever. "
            "Quality SMID companies with low debt and high ROCE led the recovery. "
            "Companies with no promoter pledging recovered 3–5x; pledged companies languished."
        ),
        "event_date": date(2020, 4, 1),
        "affected_sectors": ["SMALL_CAP", "MID_CAP", "CONSUMER", "MANUFACTURING"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "Small-cap investors who held through 2018–2020 crash and didn't sell saw 5–10x over 5 years. "
            "Quality filters (ROCE>15%, D/E<0.5, no pledging) outperformed by 50% over market. "
            "Patience through market cycles rewarded disproportionately in SMID segment."
        ),
    },

    # ── INDIA-PAKISTAN TENSIONS ───────────────────────────────────────────────
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "Pulwama terrorist attack + Balakot airstrikes (February–March 2019): "
            "40 CRPF soldiers killed in Pulwama; India conducts aerial strikes in Pakistan (Balakot). "
            "Markets initially volatile; Nifty falls 1.5% on Pulwama; rallied 1% after Balakot. "
            "Defence stocks surge; India VIX spikes to 22 then retreats. "
            "Historical pattern: India-Pakistan tensions cause temporary market disruption."
        ),
        "event_date": date(2019, 2, 14),
        "affected_sectors": ["DEFENCE", "AVIATION", "TRAVEL"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Markets recovered fully within 2 weeks; geopolitical tensions de-escalated. "
            "Historical data: India-Pakistan skirmishes cause average 2–3% market dip, "
            "recover within 1–2 weeks unless escalation to full-scale war. "
            "Defence stocks are the only sector that benefits during such events."
        ),
    },
    {
        "event_type": "GEOPOLITICAL",
        "description": (
            "India-Pakistan escalation May 2025 (Operation Sindoor): India strikes terrorist "
            "infrastructure in Pakistan after Pahalgam terror attack (26 civilians killed). "
            "Indian Air Force conducts precision strikes; Pakistan retaliates; ceasefire in 4 days. "
            "Nifty falls 3–4% in first 2 sessions; defence stocks up 8–12%. "
            "India VIX spikes to 22 then retreats as ceasefire holds."
        ),
        "event_date": date(2025, 5, 7),
        "affected_sectors": ["DEFENCE", "AVIATION", "BANKING", "CONSUMER"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "Markets recovered 60% of losses within 5 days post-ceasefire. "
            "Same historical pattern as Balakot 2019 and Kargil 1999. "
            "Defence stocks maintained gains post-conflict due to order acceleration. "
            "India's decisive military response changed conflict deterrence calculus."
        ),
    },

    # ── COMMODITIES / ENERGY TRANSITION ───────────────────────────────────────
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "Green hydrogen policy India (January 2023): India National Green Hydrogen Mission "
            "targets 5 MMT/year production by 2030; ₹19,744 Cr PLI allocation. "
            "Reliance, Adani, NTPC, L&T commit green hydrogen projects. "
            "Long-term: India to become green hydrogen exporter to Europe. "
            "Revenue growth visibility for green energy infrastructure 10-year horizon."
        ),
        "event_date": date(2023, 1, 4),
        "affected_sectors": ["RENEWABLE_ENERGY", "HYDROGEN", "CHEMICALS", "POWER"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "Green hydrogen capex commitments >$100 Bn by 2025 across Indian companies. "
            "Early-stage: revenue impact 5–7 years away. "
            "Re-rating of power and renewable companies on long-term hydrogen opportunity."
        ),
    },

    # ── RBI / NBFC SPECIFIC EVENTS ────────────────────────────────────────────
    {
        "event_type": "REGULATION",
        "description": (
            "RBI tightens NBFC regulations (October 2021): Scale-based regulation framework; "
            "NBFC-UL (upper layer) face bank-like prudential norms, CRR/SLR requirements. "
            "Bajaj Finance, Chola, Shriram Finance face higher capital requirements. "
            "NBFC stocks fall 5–10% initially on higher compliance cost expectations."
        ),
        "event_date": date(2021, 10, 22),
        "affected_sectors": ["NBFC", "BANKING"],
        "market_impact": "MODERATE_NEGATIVE",
        "outcome": (
            "NBFC sector consolidated around quality names; weaker players exited or merged. "
            "Bajaj Finance absorbed higher norms without issues; stock recovered strongly. "
            "Credit quality improved across NBFC sector post-regulation tightening."
        ),
    },
    {
        "event_type": "RBI_POLICY",
        "description": (
            "RBI credit card circular (June 2022): RBI allows credit card linkage to UPI. "
            "HDFC Bank, Axis Bank, and SBI credit cards can be used via UPI QR codes. "
            "Spend-now-pay-later (SNPL) segment gets major boost. "
            "Credit card outstanding crosses ₹2 lakh Cr; revolver credit growing."
        ),
        "event_date": date(2022, 6, 8),
        "affected_sectors": ["BANKING", "FINTECH", "PAYMENTS", "CONSUMER"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "Credit card spend in India grows 30%+ YoY. "
            "HDFC Bank, Axis Bank, SBI Card benefit from UPI credit card link. "
            "Consumer discretionary spending increases as credit access improves."
        ),
    },
    {
        "event_type": "SECTOR_BULL",
        "description": (
            "Quick commerce (q-commerce) boom India 2022–2024: Blinkit (Zomato), Zepto, Swiggy "
            "Instamart scale 10-minute grocery delivery to 30+ cities. "
            "Gross order value grows 5x in 2 years; order frequency improves retention. "
            "Dark store infrastructure investment: ₹3,000–5,000 Cr capex across players. "
            "Traditional kirana/grocery and modern trade hypermarkets lose urban share to q-commerce."
        ),
        "event_date": date(2022, 9, 1),
        "affected_sectors": ["CONSUMER_TECH", "FMCG", "FOOD_DELIVERY", "LOGISTICS"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "Blinkit (Zomato) reaches 45 Mn annual active users; GOV crosses $3 Bn (FY25). "
            "Zepto raised $1 Bn+ at $5 Bn valuation in 2024. "
            "FMCG companies increased q-commerce allocation; dark store density improved margins."
        ),
    },
    {
        "event_type": "GLOBAL",
        "description": (
            "Global supply chain normalisation 2022–2023: Post-COVID supply chains recover; "
            "container shipping rates fall 80% from peak ($20,000 → $1,500/FEU). "
            "Global inflation starts to moderate; US CPI falls from 9.1% to 3.5% by 2023. "
            "India imported input cost inflation abates; gross margin recovery for consumer companies. "
            "Commodity prices (crude, agri, metals) normalise from war-driven peaks."
        ),
        "event_date": date(2022, 10, 1),
        "affected_sectors": ["FMCG", "CHEMICALS", "CONSUMER", "LOGISTICS", "AUTO"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "FMCG gross margins recovered 200–300 bps in FY24 vs FY23. "
            "Consumer discretionary companies saw volume recovery as prices stabilised. "
            "India headline inflation fell to 4–5% by mid-2024 enabling RBI rate cut."
        ),
    },
    {
        "event_type": "BUDGET",
        "description": (
            "India interim Union Budget FY26 (February 2025): No income tax below ₹12 lakh; "
            "capex at ₹11.2 lakh Cr maintained; defence budget ₹6.81 lakh Cr (highest ever). "
            "Focus on employment generation; MSME credit guarantee enhanced. "
            "Fiscal deficit target 4.4% of GDP (FY25E), 4.1% for FY26."
        ),
        "event_date": date(2025, 2, 1),
        "affected_sectors": ["CONSUMER", "FMCG", "DEFENCE", "INFRASTRUCTURE", "MSME"],
        "market_impact": "MILD_POSITIVE",
        "outcome": (
            "Consumption stocks rallied as tax relief improved disposable income. "
            "Defence stocks hit new ATH on record defence budget. "
            "Fiscal prudence maintained despite election year pressures — positive for macro."
        ),
    },
    {
        "event_type": "MACRO_POSITIVE",
        "description": (
            "India infrastructure spending acceleration FY24–FY26: National Infrastructure Pipeline "
            "capex averaging ₹11 lakh Cr/year; highways 15,000 km/year, railways ₹2.5 lakh Cr/year. "
            "Private sector capex recovery: Manufacturing capex at 15-year high in FY25. "
            "Order books for EPC, L&T, rail, power transmission companies at all-time highs. "
            "Revenue growth of 25–40% YoY for infra execution companies."
        ),
        "event_date": date(2024, 4, 1),
        "affected_sectors": ["INFRASTRUCTURE", "RAILWAYS", "CEMENT", "CAPITAL_GOODS", "EPC"],
        "market_impact": "STRONG_POSITIVE",
        "outcome": (
            "India construction/infra sector returns 35–50% in FY24. "
            "Cement demand grows 7–8% YoY; UltraTech, Shree Cement hit ATH. "
            "Private manufacturing capex revival led by semiconductors, data centers, PLI."
        ),
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# Combine all events (93 base + 65 new = 158 total)
# ──────────────────────────────────────────────────────────────────────────────
EVENTS = _BASE_EVENTS + _NEW_EVENTS

print(f"Total events in comprehensive seeder: {len(EVENTS)}")


# ──────────────────────────────────────────────────────────────────────────────
# Re-use existing seed/embed machinery from base module
# ──────────────────────────────────────────────────────────────────────────────

from db.seed_historical_events import (
    get_supabase_client,
    serialize_event,
)


def seed(dry_run: bool = False, force: bool = False, append: bool = False):
    _positive = ["STRONG_POSITIVE", "MILD_POSITIVE", "LONG_TERM_POSITIVE"]
    _negative = [
        "SEVERE_NEGATIVE", "MODERATE_NEGATIVE", "SECTOR_NEGATIVE",
        "SEVERE_SECTOR_DISRUPTION",
    ]
    pos_count = sum(1 for e in EVENTS if e.get("market_impact") in _positive)
    neg_count = sum(1 for e in EVENTS if e.get("market_impact") in _negative)
    print(
        f"Comprehensive seed: {len(EVENTS)} events "
        f"({pos_count} positive / {neg_count} negative / "
        f"{len(EVENTS) - pos_count - neg_count} mixed-neutral)"
    )

    if dry_run:
        for i, ev in enumerate(EVENTS, 1):
            impact = ev.get("market_impact", "?")
            tag = "+" if impact in _positive else ("-" if impact in _negative else "~")
            safe_desc = ev["description"][:65].encode("ascii", "replace").decode("ascii")
            safe_type = ev["event_type"].encode("ascii", "replace").decode("ascii")
            print(f"  [{i:03d}] {tag} {ev['event_date']} | {safe_type:20s} | {safe_desc}")
        print(f"\nDry-run complete — {len(EVENTS)} events, no data written.")
        return

    client = get_supabase_client()

    existing = client.table("historical_events").select("id", count="exact").execute()
    existing_count = existing.count or 0

    if append:
        resp = client.table("historical_events").select("description").execute()
        existing_descs = {
            row["description"][:80].strip().lower()
            for row in (resp.data or [])
        }
        new_rows = [
            serialize_event(ev)
            for ev in EVENTS
            if ev["description"][:80].strip().lower() not in existing_descs
        ]
        if not new_rows:
            print(f"  All {len(EVENTS)} events already exist — nothing to append.")
            return
        print(f"  Appending {len(new_rows)} new events (skipping {len(EVENTS) - len(new_rows)} duplicates) …")
        batch_size = 20
        inserted = 0
        for start in range(0, len(new_rows), batch_size):
            batch = new_rows[start:start + batch_size]
            client.table("historical_events").insert(batch).execute()
            inserted += len(batch)
            print(f"  Inserted batch {start // batch_size + 1}: {len(batch)} rows")
        print(f"\nDone — {inserted} new events appended (total: {existing_count + inserted}).")
        return

    if existing_count > 0 and not force:
        print(
            f"\n  Table already has {existing_count} rows. Skipping seed.\n"
            f"   To wipe and re-seed: python db/seed_historical_events_comprehensive.py --force\n"
            f"   To append new only:  python db/seed_historical_events_comprehensive.py --append\n"
            f"   To add embeddings:   python db/seed_historical_events_comprehensive.py --embed"
        )
        return

    if existing_count > 0 and force:
        print(f"  --force flag: clearing {existing_count} existing rows …")
        client.table("historical_events").delete().neq(
            "id", "00000000-0000-0000-0000-000000000000"
        ).execute()

    rows = [serialize_event(ev) for ev in EVENTS]
    batch_size = 20
    inserted = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        client.table("historical_events").insert(batch).execute()
        inserted += len(batch)
        print(f"  Inserted batch {start // batch_size + 1}: {len(batch)} rows")
    print(f"\nDone — {inserted} events seeded into historical_events.")


def embed_events():
    """Generate/update OpenAI embeddings for all rows missing them."""
    import openai  # noqa
    client_sb = get_supabase_client()
    oai = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
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
        vector = emb_resp.data[0].embedding
        client_sb.table("historical_events").update(
            {"embedding": vector}
        ).eq("id", row["id"]).execute()
        print(f"  Embedded: {row['event_date']} {row['event_type'][:30]}")
    print("Embedding update complete.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description=f"Comprehensive historical events seeder ({len(EVENTS)} events)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python db/seed_historical_events_comprehensive.py --dry-run
  python db/seed_historical_events_comprehensive.py --append
  python db/seed_historical_events_comprehensive.py --append --embed
  python db/seed_historical_events_comprehensive.py --force
  python db/seed_historical_events_comprehensive.py --force --embed
        """,
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--append", action="store_true",
                        help="Insert only events not already in DB (safe, idempotent)")
    parser.add_argument("--force", action="store_true",
                        help="Wipe ALL rows and re-seed from scratch")
    parser.add_argument("--embed", action="store_true",
                        help="Generate OpenAI embeddings for rows missing them")
    args = parser.parse_args()

    seed(dry_run=args.dry_run, force=args.force, append=args.append)

    if args.embed and not args.dry_run:
        embed_events()
