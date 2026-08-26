"""Synthetic universe specification.

Every issuer is fictional. Archetypes are designed so that each research
engine and signal family has at least one textbook positive and one
textbook negative example, plus corporate-action and survivorship cases:

- compounder        : steady high-ROIC grower at fair value (Quality Compounder)
- deep_value        : cheap, stabilising, insider buys, catalyst (Deep Value)
- value_trap        : cheap on multiples but shrinking, levered (must NOT alert)
- breakout          : accumulation -> volume breakout, estimates rising
- oversold_quality  : strong franchise in a sharp correction near support
- parabolic         : crowded social-driven extension (penalised, Avoid/Trim)
- inflection        : loss-making -> margin inflection + guidance raise
- deteriorating     : guidance cut, accrual red flags, revisions down (Exit)
- bank              : sector-aware scoring path (NIM/CET1, not FCF)
- reit              : sector-aware scoring path (FFO, not EPS)
- illiquid_micro    : fails universe liquidity gates (must be filtered out)
- acquired          : taken over mid-history and delisted (survivorship)
- split_growth      : 4:1 share split mid-history (corporate-action maths)
- restatement       : revenue restated down after initial publication (PIT)
- steady            : unremarkable name (Hold / no alert)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

SEED = 20260826
HISTORY_START = date(2020, 7, 1)
WORLD_NOW = date(2026, 8, 25)  # the synthetic world's "today"
TIMELINE_END = date(2027, 3, 31)  # future catalysts live out here


@dataclass(frozen=True)
class AlphaSegment:
    """Annualised idiosyncratic drift between two fractions of the timeline."""

    start_frac: float
    end_frac: float
    annual_alpha: float


@dataclass(frozen=True)
class StockSpec:
    ticker: str
    name: str
    market: str  # US | UK
    exchange: str
    sector: str
    industry: str
    currency: str
    archetype: str
    base_price: float  # price at history start (raw, local ccy)
    shares: float  # shares outstanding at history end (post-split scale)
    beta: float = 1.0
    daily_vol: float = 0.018
    base_revenue_q: float = 500e6  # revenue in the first generated quarter
    revenue_growth_yoy: tuple[float, ...] = (0.08, 0.08, 0.08, 0.08, 0.08, 0.08, 0.08)
    gross_margin: tuple[float, ...] = (0.45, 0.45, 0.45, 0.45, 0.45, 0.45, 0.45)
    op_margin: tuple[float, ...] = (0.18, 0.18, 0.18, 0.18, 0.18, 0.18, 0.18)
    cash_conversion: float = 1.05  # OCF / net income
    capex_pct_rev: float = 0.05
    debt_to_rev: float = 0.3  # total debt as multiple of annual revenue
    cash_to_rev: float = 0.15
    dividend_yield: float = 0.0  # approx annual, paid quarterly
    buyback_pct: float = 0.0  # % of shares retired per year
    sbc_pct_rev: float = 0.02
    alpha_segments: tuple[AlphaSegment, ...] = ()
    events: dict = field(default_factory=dict)  # iso-date -> jump fraction
    volume_base: float = 2.0e6  # shares/day
    publication_lag_days: int = 45
    largest_customer_pct: float | None = None
    analyst_count: int = 12
    target_bias: float = 0.10  # consensus target premium over spot
    target_dispersion: float = 0.10
    estimate_trend: float = 0.0  # monthly drift in forward EPS consensus
    short_pct_float: float = 2.0
    insider_pattern: str = "none"  # none | buys_at_lows | selling
    social_hype: bool = False
    split: tuple[str, float] | None = None  # (iso ex_date, factor)
    acquired: tuple[str, str, float] | None = None  # (announce, delist, offer premium)
    restated: tuple[str, str, float] | None = None  # (period_end, publish, revenue haircut)
    special_catalysts: tuple = ()  # tuples: (kind, iso_date, description, binary)
    guidance_events: tuple = ()  # tuples: (iso_date, direction, text)


US = dict(market="US", exchange="NYSE", currency="USD")
UK = dict(market="UK", exchange="LSE", currency="GBP")

SPECS: list[StockSpec] = [
    # ---------------- US ----------------
    StockSpec(
        ticker="NVLT", name="Novalight Systems", sector="Technology",
        industry="Enterprise Software", archetype="compounder", base_price=88.0,
        shares=610e6, beta=1.1, daily_vol=0.016, base_revenue_q=1.9e9,
        revenue_growth_yoy=(0.16, 0.15, 0.14, 0.14, 0.13, 0.13, 0.12),
        gross_margin=(0.71, 0.71, 0.72, 0.72, 0.73, 0.73, 0.73),
        op_margin=(0.27, 0.27, 0.28, 0.28, 0.29, 0.30, 0.30),
        cash_conversion=1.18, capex_pct_rev=0.035, debt_to_rev=0.12, cash_to_rev=0.45,
        buyback_pct=0.012, sbc_pct_rev=0.05, dividend_yield=0.006,
        alpha_segments=(AlphaSegment(0.0, 1.0, 0.06),),
        volume_base=4.5e6, analyst_count=28, target_bias=0.12, target_dispersion=0.08,
        estimate_trend=0.15, insider_pattern="none",
        special_catalysts=(("investor_day", "2026-09-24", "Annual investor day: margin framework update", False),),
        **US,
    ),
    StockSpec(
        ticker="HGMT", name="Highgate Materials", sector="Industrials",
        industry="Specialty Chemicals", archetype="deep_value", base_price=64.0,
        shares=140e6, beta=1.0, daily_vol=0.019, base_revenue_q=820e6,
        revenue_growth_yoy=(0.05, 0.03, -0.04, -0.02, 0.01, 0.03, 0.04),
        gross_margin=(0.33, 0.32, 0.30, 0.30, 0.31, 0.32, 0.32),
        op_margin=(0.13, 0.12, 0.10, 0.10, 0.11, 0.12, 0.12),
        cash_conversion=1.10, capex_pct_rev=0.06, debt_to_rev=0.35, cash_to_rev=0.18,
        dividend_yield=0.034, buyback_pct=0.03,
        alpha_segments=(
            AlphaSegment(0.0, 0.45, 0.00), AlphaSegment(0.45, 0.75, -0.22),
            AlphaSegment(0.75, 0.93, 0.02), AlphaSegment(0.93, 1.0, 0.05),
        ),
        volume_base=1.6e6, analyst_count=9, target_bias=0.22, target_dispersion=0.16,
        estimate_trend=0.05, insider_pattern="buys_at_lows",
        special_catalysts=(
            ("capital_return", "2026-09-15", "Board review of expanded buyback programme", False),
        ),
        **US,
    ),
    StockSpec(
        ticker="CRBX", name="Carbonex Legacy Media", sector="Consumer",
        industry="Broadcast Media", archetype="value_trap", base_price=42.0,
        shares=210e6, beta=0.9, daily_vol=0.021, base_revenue_q=940e6,
        revenue_growth_yoy=(-0.02, -0.05, -0.08, -0.09, -0.10, -0.11, -0.12),
        gross_margin=(0.38, 0.36, 0.34, 0.32, 0.31, 0.30, 0.29),
        op_margin=(0.16, 0.14, 0.11, 0.09, 0.07, 0.06, 0.05),
        cash_conversion=0.72, capex_pct_rev=0.03, debt_to_rev=1.15, cash_to_rev=0.05,
        dividend_yield=0.09,
        alpha_segments=(AlphaSegment(0.0, 1.0, -0.16),),
        volume_base=1.2e6, analyst_count=6, target_bias=0.30, target_dispersion=0.35,
        estimate_trend=-0.30, short_pct_float=9.0,
        special_catalysts=(
            ("refinancing", "2026-11-30", "USD 1.4bn notes mature; refinancing not yet agreed", True),
        ),
        guidance_events=(("2025-11-06", "down", "FY guidance cut on accelerating cord-cutting"),),
        **US,
    ),
    StockSpec(
        ticker="ARWD", name="Arrowind Robotics", sector="Industrials",
        industry="Automation Equipment", archetype="breakout", base_price=31.0,
        shares=180e6, beta=1.25, daily_vol=0.024, base_revenue_q=310e6,
        revenue_growth_yoy=(0.09, 0.10, 0.11, 0.13, 0.17, 0.22, 0.26),
        gross_margin=(0.41, 0.41, 0.42, 0.43, 0.44, 0.45, 0.46),
        op_margin=(0.09, 0.10, 0.11, 0.13, 0.15, 0.17, 0.19),
        cash_conversion=1.05, capex_pct_rev=0.05, debt_to_rev=0.20, cash_to_rev=0.22,
        alpha_segments=(
            AlphaSegment(0.0, 0.70, 0.02), AlphaSegment(0.70, 0.90, 0.10),
            AlphaSegment(0.90, 1.0, 0.42),
        ),
        events={"2026-07-21": 0.075},
        volume_base=2.1e6, analyst_count=14, target_bias=0.15, target_dispersion=0.10,
        estimate_trend=0.45, insider_pattern="none",
        special_catalysts=(
            ("contract", "2026-07-21", "Multi-year automation contract with global logistics group", False),
        ),
        guidance_events=(("2026-07-30", "up", "FY revenue guidance raised 6% on contract ramp"),),
        **US,
    ),
    StockSpec(
        ticker="MERI", name="Meridian Health Devices", sector="Healthcare",
        industry="Medical Devices", archetype="oversold_quality", base_price=112.0,
        shares=320e6, beta=0.95, daily_vol=0.017, base_revenue_q=1.35e9,
        revenue_growth_yoy=(0.11, 0.11, 0.10, 0.10, 0.10, 0.09, 0.09),
        gross_margin=(0.63, 0.63, 0.64, 0.64, 0.64, 0.64, 0.64),
        op_margin=(0.24, 0.24, 0.25, 0.25, 0.25, 0.25, 0.25),
        cash_conversion=1.12, capex_pct_rev=0.045, debt_to_rev=0.25, cash_to_rev=0.30,
        dividend_yield=0.015, buyback_pct=0.015,
        alpha_segments=(AlphaSegment(0.0, 0.94, 0.045), AlphaSegment(0.94, 1.0, -0.95)),
        events={"2026-07-08": -0.13},
        volume_base=3.0e6, analyst_count=22, target_bias=0.18, target_dispersion=0.09,
        estimate_trend=0.05,
        special_catalysts=(
            ("regulatory", "2026-07-08", "FDA warning letter on single manufacturing site", False),
            ("regulatory", "2026-10-15", "FDA re-inspection of the affected site expected", True),
        ),
        **US,
    ),
    StockSpec(
        ticker="ZYPH", name="Zyphr Interactive", sector="Technology",
        industry="Consumer Internet", archetype="parabolic", base_price=18.0,
        shares=260e6, beta=1.6, daily_vol=0.042, base_revenue_q=240e6,
        revenue_growth_yoy=(0.30, 0.28, 0.22, 0.18, 0.16, 0.15, 0.15),
        gross_margin=(0.58, 0.58, 0.58, 0.58, 0.58, 0.58, 0.58),
        op_margin=(-0.06, -0.03, 0.00, 0.02, 0.04, 0.05, 0.06),
        cash_conversion=0.85, capex_pct_rev=0.04, debt_to_rev=0.10, cash_to_rev=0.25,
        sbc_pct_rev=0.12,
        alpha_segments=(
            AlphaSegment(0.0, 0.85, 0.00), AlphaSegment(0.85, 0.97, 1.60),
            AlphaSegment(0.97, 1.0, 0.80),
        ),
        volume_base=6.0e6, analyst_count=8, target_bias=-0.15, target_dispersion=0.40,
        estimate_trend=0.10, short_pct_float=14.0, insider_pattern="selling",
        social_hype=True,
        special_catalysts=(("earnings", "2026-09-02", "Q2 results — first quarter lapping viral growth", True),),
        **US,
    ),
    StockSpec(
        ticker="SLRT", name="Solarity Grid", sector="Energy",
        industry="Renewable Infrastructure", archetype="inflection", base_price=24.0,
        shares=290e6, beta=1.2, daily_vol=0.026, base_revenue_q=380e6,
        revenue_growth_yoy=(0.14, 0.13, 0.12, 0.13, 0.15, 0.18, 0.20),
        gross_margin=(0.24, 0.25, 0.26, 0.28, 0.30, 0.33, 0.35),
        op_margin=(-0.09, -0.06, -0.03, -0.01, 0.02, 0.05, 0.08),
        cash_conversion=1.00, capex_pct_rev=0.10, debt_to_rev=0.55, cash_to_rev=0.20,
        alpha_segments=(AlphaSegment(0.0, 0.80, -0.05), AlphaSegment(0.80, 1.0, 0.28)),
        volume_base=2.4e6, analyst_count=11, target_bias=0.20, target_dispersion=0.18,
        estimate_trend=0.55,
        guidance_events=(("2026-08-05", "up", "First positive operating-margin quarter; FY margin guidance raised"),),
        special_catalysts=(("guidance", "2026-08-05", "Margin inflection confirmed at Q2 results", False),),
        **US,
    ),
    StockSpec(
        ticker="DRFT", name="Driftline Logistics", sector="Industrials",
        industry="Freight & Logistics", archetype="deteriorating", base_price=57.0,
        shares=240e6, beta=1.05, daily_vol=0.02, base_revenue_q=1.1e9,
        revenue_growth_yoy=(0.09, 0.07, 0.02, -0.03, -0.06, -0.08, -0.10),
        gross_margin=(0.30, 0.29, 0.27, 0.25, 0.24, 0.23, 0.22),
        op_margin=(0.12, 0.11, 0.09, 0.07, 0.05, 0.04, 0.03),
        cash_conversion=0.65, capex_pct_rev=0.07, debt_to_rev=0.75, cash_to_rev=0.08,
        dividend_yield=0.02,
        alpha_segments=(AlphaSegment(0.0, 0.55, 0.03), AlphaSegment(0.55, 1.0, -0.30)),
        events={"2026-05-12": -0.11},
        volume_base=1.9e6, analyst_count=13, target_bias=0.05, target_dispersion=0.22,
        estimate_trend=-0.55, short_pct_float=6.5,
        guidance_events=(
            ("2026-05-12", "down", "FY EPS guidance cut 18% on volume weakness and pricing"),
        ),
        **US,
    ),
    StockSpec(
        ticker="KSTB", name="Keystone Bancorp", sector="Financials",
        industry="Regional Banks", archetype="bank", base_price=48.0,
        shares=380e6, beta=0.9, daily_vol=0.017, base_revenue_q=980e6,
        revenue_growth_yoy=(0.05, 0.06, 0.12, 0.10, 0.04, 0.03, 0.04),
        gross_margin=(1.0,) * 7, op_margin=(0.42, 0.42, 0.46, 0.45, 0.41, 0.40, 0.41),
        cash_conversion=1.0, capex_pct_rev=0.01, debt_to_rev=2.0, cash_to_rev=1.5,
        dividend_yield=0.041, buyback_pct=0.02,
        alpha_segments=(AlphaSegment(0.0, 1.0, 0.01),),
        volume_base=2.8e6, analyst_count=16, target_bias=0.10, target_dispersion=0.10,
        estimate_trend=0.02,
        **US,
    ),
    StockSpec(
        ticker="GRPT", name="Granite Point Storage REIT", sector="Real Estate",
        industry="Industrial REITs", archetype="reit", base_price=71.0,
        shares=190e6, beta=0.8, daily_vol=0.015, base_revenue_q=290e6,
        revenue_growth_yoy=(0.07, 0.07, 0.06, 0.05, 0.05, 0.05, 0.05),
        gross_margin=(0.72,) * 7, op_margin=(0.38, 0.38, 0.38, 0.37, 0.37, 0.37, 0.37),
        cash_conversion=1.35, capex_pct_rev=0.12, debt_to_rev=2.4, cash_to_rev=0.10,
        dividend_yield=0.048,
        alpha_segments=(AlphaSegment(0.0, 1.0, 0.005),),
        volume_base=1.1e6, analyst_count=10, target_bias=0.08, target_dispersion=0.08,
        estimate_trend=0.0,
        **US,
    ),
    StockSpec(
        ticker="PYNE", name="Pinebrook Analytics", sector="Technology",
        industry="Data Infrastructure", archetype="split_growth", base_price=180.0,
        shares=150e6, beta=1.3, daily_vol=0.022, base_revenue_q=520e6,
        revenue_growth_yoy=(0.24, 0.22, 0.20, 0.19, 0.18, 0.17, 0.16),
        gross_margin=(0.68, 0.68, 0.69, 0.69, 0.70, 0.70, 0.70),
        op_margin=(0.14, 0.15, 0.17, 0.19, 0.21, 0.22, 0.23),
        cash_conversion=1.15, capex_pct_rev=0.04, debt_to_rev=0.05, cash_to_rev=0.40,
        sbc_pct_rev=0.08,
        alpha_segments=(AlphaSegment(0.0, 1.0, 0.09),),
        volume_base=3.4e6, analyst_count=24, target_bias=0.10, target_dispersion=0.12,
        estimate_trend=0.20,
        split=("2024-06-10", 4.0),
        **US,
    ),
    StockSpec(
        ticker="TLLM", name="Tallmast Shipping", sector="Industrials",
        industry="Marine Transport", archetype="acquired", base_price=27.0,
        shares=95e6, beta=1.1, daily_vol=0.023, base_revenue_q=260e6,
        revenue_growth_yoy=(0.06, 0.05, 0.04, 0.04, 0.04, 0.04, 0.04),
        gross_margin=(0.35,) * 7, op_margin=(0.14, 0.14, 0.13, 0.13, 0.13, 0.13, 0.13),
        cash_conversion=1.05, capex_pct_rev=0.08, debt_to_rev=0.50, cash_to_rev=0.12,
        dividend_yield=0.025,
        alpha_segments=(AlphaSegment(0.0, 1.0, 0.0),),
        volume_base=0.9e6, analyst_count=5, target_bias=0.12, target_dispersion=0.14,
        acquired=("2025-02-18", "2025-05-30", 0.31),
        **US,
    ),
    StockSpec(
        ticker="VNTA", name="Ventara Consumer Brands", sector="Consumer",
        industry="Packaged Foods", archetype="restatement", base_price=54.0,
        shares=220e6, beta=0.75, daily_vol=0.016, base_revenue_q=780e6,
        revenue_growth_yoy=(0.06, 0.06, 0.07, 0.08, 0.05, 0.03, 0.02),
        gross_margin=(0.42, 0.42, 0.43, 0.44, 0.42, 0.41, 0.41),
        op_margin=(0.15, 0.15, 0.16, 0.17, 0.15, 0.14, 0.14),
        cash_conversion=0.80, capex_pct_rev=0.04, debt_to_rev=0.45, cash_to_rev=0.10,
        dividend_yield=0.028,
        alpha_segments=(AlphaSegment(0.0, 0.85, 0.02), AlphaSegment(0.85, 1.0, -0.18)),
        events={"2025-11-14": -0.09},
        volume_base=1.5e6, analyst_count=9, target_bias=0.10, target_dispersion=0.20,
        estimate_trend=-0.10,
        restated=("2025-06-30", "2025-11-14", 0.08),
        **US,
    ),
    StockSpec(
        ticker="MICR", name="Microvale Diagnostics", sector="Healthcare",
        industry="Diagnostics", archetype="illiquid_micro", base_price=2.1,
        shares=45e6, beta=1.4, daily_vol=0.05, base_revenue_q=9e6,
        revenue_growth_yoy=(0.20, 0.15, 0.10, 0.10, 0.10, 0.10, 0.10),
        gross_margin=(0.50,) * 7, op_margin=(-0.30, -0.25, -0.22, -0.20, -0.18, -0.15, -0.12),
        cash_conversion=0.9, capex_pct_rev=0.05, debt_to_rev=0.10, cash_to_rev=0.60,
        alpha_segments=(AlphaSegment(0.0, 1.0, -0.05),),
        volume_base=3.5e4, analyst_count=1, target_bias=0.50, target_dispersion=0.60,
        **US,
    ),
    StockSpec(
        ticker="ORCM", name="Orchard Micro Devices", sector="Technology",
        industry="Semiconductors", archetype="steady", base_price=95.0,
        shares=410e6, beta=1.15, daily_vol=0.02, base_revenue_q=2.2e9,
        revenue_growth_yoy=(0.10, 0.06, -0.02, 0.05, 0.12, 0.10, 0.08),
        gross_margin=(0.52, 0.51, 0.49, 0.50, 0.53, 0.54, 0.54),
        op_margin=(0.22, 0.20, 0.17, 0.19, 0.23, 0.24, 0.24),
        cash_conversion=1.08, capex_pct_rev=0.09, debt_to_rev=0.25, cash_to_rev=0.35,
        dividend_yield=0.012, buyback_pct=0.01,
        alpha_segments=(AlphaSegment(0.0, 1.0, 0.015),),
        volume_base=5.2e6, analyst_count=26, target_bias=0.08, target_dispersion=0.11,
        estimate_trend=0.05,
        **US,
    ),
    # ---------------- UK ----------------
    StockSpec(
        ticker="ASHW", name="Ashworth Engineering", sector="Industrials",
        industry="Aerospace Components", archetype="compounder", base_price=14.5,
        shares=520e6, beta=0.95, daily_vol=0.016, base_revenue_q=410e6,
        revenue_growth_yoy=(0.10, 0.11, 0.12, 0.12, 0.11, 0.11, 0.10),
        gross_margin=(0.48, 0.48, 0.49, 0.49, 0.50, 0.50, 0.50),
        op_margin=(0.19, 0.19, 0.20, 0.20, 0.21, 0.21, 0.21),
        cash_conversion=1.10, capex_pct_rev=0.05, debt_to_rev=0.20, cash_to_rev=0.25,
        dividend_yield=0.021, publication_lag_days=60,
        alpha_segments=(AlphaSegment(0.0, 1.0, 0.045),),
        volume_base=2.6e6, analyst_count=12, target_bias=0.12, target_dispersion=0.09,
        estimate_trend=0.10,
        **UK,
    ),
    StockSpec(
        ticker="BRNS", name="Barnes & Wexford Retail", sector="Consumer",
        industry="General Retail", archetype="deep_value", base_price=3.8,
        shares=900e6, beta=1.05, daily_vol=0.02, base_revenue_q=1.5e9,
        revenue_growth_yoy=(0.02, -0.01, -0.05, -0.02, 0.02, 0.03, 0.04),
        gross_margin=(0.51, 0.50, 0.49, 0.49, 0.50, 0.51, 0.51),
        op_margin=(0.07, 0.06, 0.04, 0.05, 0.06, 0.07, 0.07),
        cash_conversion=1.15, capex_pct_rev=0.035, debt_to_rev=0.18, cash_to_rev=0.20,
        dividend_yield=0.052, buyback_pct=0.04, publication_lag_days=60,
        alpha_segments=(
            AlphaSegment(0.0, 0.5, -0.05), AlphaSegment(0.5, 0.8, -0.15),
            AlphaSegment(0.8, 1.0, 0.08),
        ),
        volume_base=8.0e6, analyst_count=8, target_bias=0.25, target_dispersion=0.15,
        estimate_trend=0.08, insider_pattern="buys_at_lows",
        special_catalysts=(
            ("capital_return", "2026-10-01", "Interim results: surplus-cash return decision expected", False),
        ),
        **UK,
    ),
    StockSpec(
        ticker="THMV", name="Thamesview Property Group", sector="Real Estate",
        industry="Diversified REITs", archetype="value_trap", base_price=6.2,
        shares=750e6, beta=0.85, daily_vol=0.018, base_revenue_q=210e6,
        revenue_growth_yoy=(0.01, -0.01, -0.03, -0.04, -0.05, -0.05, -0.06),
        gross_margin=(0.65,) * 7, op_margin=(0.30, 0.28, 0.25, 0.22, 0.20, 0.18, 0.17),
        cash_conversion=1.2, capex_pct_rev=0.15, debt_to_rev=3.6, cash_to_rev=0.08,
        dividend_yield=0.075, publication_lag_days=60,
        alpha_segments=(AlphaSegment(0.0, 1.0, -0.12),),
        volume_base=3.2e6, analyst_count=7, target_bias=0.18, target_dispersion=0.28,
        estimate_trend=-0.20,
        special_catalysts=(
            ("refinancing", "2026-12-15", "GBP 600m facility renewal amid falling asset values", True),
        ),
        **UK,
    ),
    StockSpec(
        ticker="FDSC", name="Fieldscale Agritech", sector="Technology",
        industry="Agricultural Software", archetype="breakout", base_price=2.4,
        shares=640e6, beta=1.2, daily_vol=0.026, base_revenue_q=95e6,
        revenue_growth_yoy=(0.12, 0.13, 0.14, 0.16, 0.20, 0.25, 0.30),
        gross_margin=(0.55, 0.55, 0.56, 0.57, 0.58, 0.60, 0.61),
        op_margin=(0.02, 0.03, 0.05, 0.07, 0.10, 0.13, 0.16),
        cash_conversion=1.0, capex_pct_rev=0.04, debt_to_rev=0.10, cash_to_rev=0.30,
        publication_lag_days=60,
        alpha_segments=(
            AlphaSegment(0.0, 0.75, 0.03), AlphaSegment(0.75, 0.92, 0.12),
            AlphaSegment(0.92, 1.0, 0.5),
        ),
        events={"2026-07-28": 0.06},
        volume_base=4.1e6, analyst_count=6, target_bias=0.18, target_dispersion=0.12,
        estimate_trend=0.5,
        special_catalysts=(
            ("contract", "2026-07-28", "National farm-data platform contract win", False),
        ),
        guidance_events=(("2026-08-04", "up", "ARR guidance raised on platform contract"),),
        **UK,
    ),
    StockSpec(
        ticker="WYCH", name="Wychwood Spirits", sector="Consumer",
        industry="Beverages", archetype="oversold_quality", base_price=28.0,
        shares=300e6, beta=0.8, daily_vol=0.015, base_revenue_q=520e6,
        revenue_growth_yoy=(0.08, 0.08, 0.08, 0.07, 0.07, 0.07, 0.07),
        gross_margin=(0.60, 0.60, 0.61, 0.61, 0.61, 0.61, 0.61),
        op_margin=(0.26, 0.26, 0.27, 0.27, 0.27, 0.27, 0.27),
        cash_conversion=1.05, capex_pct_rev=0.05, debt_to_rev=0.40, cash_to_rev=0.15,
        dividend_yield=0.024, publication_lag_days=60,
        alpha_segments=(AlphaSegment(0.0, 0.92, 0.04), AlphaSegment(0.92, 1.0, -0.75)),
        events={"2026-06-17": -0.10},
        volume_base=1.8e6, analyst_count=15, target_bias=0.16, target_dispersion=0.08,
        estimate_trend=0.0,
        special_catalysts=(
            ("regulatory", "2026-06-17", "US tariff review names imported spirits category", False),
        ),
        **UK,
    ),
    StockSpec(
        ticker="CLDN", name="Caldon Energy Services", sector="Energy",
        industry="Oilfield Services", archetype="steady", base_price=9.5,
        shares=480e6, beta=1.3, daily_vol=0.024, base_revenue_q=460e6,
        revenue_growth_yoy=(0.15, 0.10, 0.02, -0.04, 0.03, 0.06, 0.05),
        gross_margin=(0.28, 0.28, 0.26, 0.25, 0.26, 0.27, 0.27),
        op_margin=(0.11, 0.11, 0.09, 0.08, 0.09, 0.10, 0.10),
        cash_conversion=1.0, capex_pct_rev=0.07, debt_to_rev=0.40, cash_to_rev=0.15,
        dividend_yield=0.035, publication_lag_days=60,
        alpha_segments=(AlphaSegment(0.0, 1.0, 0.0),),
        volume_base=3.9e6, analyst_count=9, target_bias=0.10, target_dispersion=0.14,
        **UK,
    ),
    StockSpec(
        ticker="HLDN", name="Halden Financial", sector="Financials",
        industry="Wealth Management", archetype="inflection", base_price=5.6,
        shares=560e6, beta=1.0, daily_vol=0.019, base_revenue_q=280e6,
        revenue_growth_yoy=(0.04, 0.03, 0.03, 0.05, 0.08, 0.11, 0.13),
        gross_margin=(1.0,) * 7, op_margin=(0.08, 0.08, 0.09, 0.12, 0.16, 0.20, 0.23),
        cash_conversion=1.05, capex_pct_rev=0.02, debt_to_rev=0.15, cash_to_rev=0.40,
        dividend_yield=0.03, publication_lag_days=60,
        alpha_segments=(AlphaSegment(0.0, 0.78, -0.02), AlphaSegment(0.78, 1.0, 0.22)),
        volume_base=2.2e6, analyst_count=7, target_bias=0.15, target_dispersion=0.12,
        estimate_trend=0.35,
        guidance_events=(("2026-07-22", "up", "Cost programme completing a year early; margin target raised"),),
        **UK,
    ),
]

INDEX_SPECS = [
    # (ticker, name, market, sector) — sector == "" is the market benchmark.
    ("USMKT", "US Market Composite (synthetic)", "US", ""),
    ("UKMKT", "UK Market Composite (synthetic)", "UK", ""),
]
# Sector indices are derived per (market, sector) present in SPECS.

MACRO_SERIES = [
    "us_policy_rate", "uk_policy_rate", "us_cpi_yoy", "uk_cpi_yoy",
    "us_10y_yield", "uk_10y_yield", "us_credit_spread_bps", "vix",
]
