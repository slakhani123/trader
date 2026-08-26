"""Buy-candidate gates: configurable minimum-quality thresholds that must
pass before any buy-side alert is issued. Failing a gate means abstention
(recorded with reasons), never a weaker alert."""

from __future__ import annotations

from vigil.config import Settings
from vigil.schemas.core import EngineResult, GateResult, InstrumentSnapshot


def universe_eligible(snapshot: InstrumentSnapshot, settings: Settings) -> tuple[bool, list[str]]:
    """Universe-level eligibility (liquidity, size, type, exclusions)."""
    u = settings.universe
    reasons: list[str] = []
    info = snapshot.info
    if info.security_type not in u.allowed_security_types:
        reasons.append(f"security type '{info.security_type}' not in universe")
    if u.exclude_shell_companies and info.is_shell:
        reasons.append("shell company excluded")
    if info.industry in u.excluded_industries or info.sector in u.excluded_industries:
        reasons.append(f"industry '{info.industry}' excluded by configuration")
    if not info.is_active:
        reasons.append("instrument is delisted")
    mcap = snapshot.liquidity.market_cap_base
    if mcap is not None and mcap < u.min_market_cap:
        reasons.append(
            f"market cap {mcap / 1e6:,.0f}m below minimum {u.min_market_cap / 1e6:,.0f}m"
        )
    price = snapshot.last_close
    if price is not None and price < u.min_price:
        reasons.append(f"price {price:.2f} below minimum {u.min_price:.2f}")
    traded = snapshot.liquidity.median_daily_traded_value_base
    if traded is not None and traded < u.min_median_daily_traded_value:
        reasons.append(
            f"median daily traded value {traded:,.0f} below minimum "
            f"{u.min_median_daily_traded_value:,.0f}"
        )
    return (not reasons, reasons)


def buy_gate(
    opportunity: float,
    confidence: float,
    risk: float,
    data_quality: float,
    engines_reporting: int,
    reward_risk: float | None,
    snapshot: InstrumentSnapshot,
    settings: Settings,
) -> GateResult:
    g = settings.gates
    failures: list[str] = []
    if opportunity < g.min_opportunity:
        failures.append(f"opportunity {opportunity:.1f} < minimum {g.min_opportunity:.1f}")
    if confidence < g.min_confidence:
        failures.append(f"confidence {confidence:.1f} < minimum {g.min_confidence:.1f}")
    if risk > g.max_risk:
        failures.append(f"risk {risk:.1f} > maximum {g.max_risk:.1f}")
    if data_quality < g.min_data_quality:
        failures.append(f"data quality {data_quality:.2f} < minimum {g.min_data_quality:.2f}")
    if engines_reporting < g.min_engines_reporting:
        failures.append(
            f"only {engines_reporting} engines reported (minimum {g.min_engines_reporting})"
        )
    if reward_risk is not None and reward_risk < g.min_reward_risk:
        failures.append(f"reward/risk {reward_risk:.1f} < minimum {g.min_reward_risk:.1f}")
    if snapshot.liquidity.price_staleness_days > g.max_price_staleness_days:
        failures.append(
            f"price stale by {snapshot.liquidity.price_staleness_days} trading days"
        )
    return GateResult(passed=not failures, failures=failures, reward_risk=reward_risk)


def engines_reporting(results: dict[str, EngineResult]) -> int:
    return sum(1 for r in results.values() if r.score is not None)
