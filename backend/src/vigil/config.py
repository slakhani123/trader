"""Application settings.

Every tunable the brief names is configurable here rather than hard-coded.
Values come from (highest precedence first) environment variables prefixed
``VIGIL_``, an ``.env`` file, then the documented defaults below. Defaults
are deliberately conservative: they gate harder rather than alert more.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Horizon = Literal["short", "medium", "long"]
HORIZONS: tuple[Horizon, ...] = ("short", "medium", "long")


class UniverseConfig(BaseModel):
    """Which securities are eligible for scanning at all."""

    markets: list[str] = Field(default=["US", "UK"], description="ISO-ish market codes")
    # Liquid common stocks only: funds/ETFs, warrants, and shell companies are
    # excluded by security_type / flags at ingest and re-checked at scan time.
    allowed_security_types: list[str] = Field(default=["common"])
    exclude_shell_companies: bool = True
    excluded_industries: list[str] = Field(
        default=[], description="Industry names the user never wants alerts for"
    )
    min_market_cap: float = Field(default=250_000_000.0, description="In base currency (GBP)")
    min_price: float = Field(default=1.0, description="In local currency units")
    min_median_daily_traded_value: float = Field(
        default=1_000_000.0,
        description="Median of price*volume over the liquidity window, in base currency",
    )
    liquidity_window_days: int = 63


class HorizonConfig(BaseModel):
    """Approximate trading-day windows per horizon (documented defaults)."""

    short_min_days: int = 2
    short_max_days: int = 20
    medium_min_days: int = 21
    medium_max_days: int = 126  # ~6 months
    long_min_days: int = 127
    long_max_days: int = 1260  # ~5 years


class GateConfig(BaseModel):
    """Minimum-quality gates a buy-candidate alert must pass (else abstain)."""

    min_confidence: float = Field(default=5.5, ge=0, le=10)
    min_opportunity: float = Field(default=6.5, ge=0, le=10)
    max_risk: float = Field(default=8.0, ge=0, le=10)
    min_reward_risk: float = Field(default=2.0, description="Reward/risk ratio to entry zone")
    min_data_quality: float = Field(default=0.6, ge=0, le=1)
    min_engines_reporting: int = Field(
        default=4, description="Engines that must produce a score (not abstain)"
    )
    max_price_staleness_days: int = Field(
        default=3, description="Trading-day staleness beyond which prices are 'stale'"
    )


class AlertPolicyConfig(BaseModel):
    """Selective-alert policy: cooldowns and material-change thresholds."""

    cooldown_days: int = Field(default=5, description="Min trading days between same-signal alerts")
    material_score_delta: float = Field(default=0.7, description="Opportunity move that re-alerts")
    material_price_move_pct: float = Field(default=5.0)
    material_risk_delta: float = Field(default=1.0)
    watch_expiry_days: int = Field(default=40, description="WATCHING expires if never confirmed")
    max_daily_new_alerts: int = Field(default=10, description="Digest overflow beyond this")


class RiskPolicyConfig(BaseModel):
    """User risk tolerance and portfolio exposure limits."""

    risk_tolerance: Literal["conservative", "balanced", "aggressive"] = "balanced"
    max_position_exposure_pct: float = Field(default=10.0, description="Of portfolio value")
    max_sector_exposure_pct: float = Field(default=25.0)


class ScanConfig(BaseModel):
    scan_frequency: Literal["eod", "intraday"] = "eod"
    eod_scan_utc_hour: int = 21  # after US close
    intraday_interval_minutes: int = 30
    weekly_review_weekday: int = 6  # Sunday
    digest_utc_hour: int = 7


class BacktestCostConfig(BaseModel):
    commission_bps_per_side: float = 5.0
    slippage_half_spread: bool = True
    execution_delay_days: int = 1  # signal on close, fill next open


class LLMConfig(BaseModel):
    enabled: bool = False  # deterministic template narrative is the default
    model: str = "claude-sonnet-5"
    max_tokens: int = 2000
    numeric_tolerance_pct: float = Field(
        default=0.5, description="Numbers in narrative must match packet within this tolerance"
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VIGIL_", env_file=".env", env_nested_delimiter="__", extra="ignore"
    )

    # --- infrastructure ---
    database_url: str = "sqlite:///vigil.db"
    api_token: str = Field(
        default="",
        description="Bearer token for the private API. Empty disables auth ONLY in debug mode.",
    )
    debug: bool = False
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:5173"]

    # --- provider credentials (all optional; adapters report unavailable) ---
    provider_price: str = "synthetic"
    provider_fundamentals: str = "synthetic"
    provider_estimates: str = "synthetic"
    provider_news: str = "synthetic"
    provider_macro: str = "synthetic"
    provider_options: str = ""  # no default provider: options data marked unavailable
    edgar_user_agent: str = Field(
        default="", description="SEC EDGAR requires 'name email' UA; unset disables the adapter"
    )
    anthropic_api_key: str = ""

    # --- notification channels ---
    webhook_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""

    # --- research configuration ---
    base_currency: str = "GBP"
    universe: UniverseConfig = UniverseConfig()
    horizons: HorizonConfig = HorizonConfig()
    gates: GateConfig = GateConfig()
    alert_policy: AlertPolicyConfig = AlertPolicyConfig()
    risk_policy: RiskPolicyConfig = RiskPolicyConfig()
    scan: ScanConfig = ScanConfig()
    backtest_costs: BacktestCostConfig = BacktestCostConfig()
    llm: LLMConfig = LLMConfig()

    # Feature flags for experimental strategies (name -> enabled).
    feature_flags: dict[str, bool] = {}

    scoring_model_version: str = "v1.0.0"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test helper: force settings to be re-read from the environment."""
    get_settings.cache_clear()
