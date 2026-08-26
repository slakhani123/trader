"""Versioned scoring weights.

Every model version is immutable once used by a run: changing weights means
registering a NEW version. ``ensure_model_version`` persists the active
version (weights + hash) to the ``model_versions`` table so any historical
score can be traced to the exact configuration that produced it.

v1.0.0 rationale (research-informed starting point, to be recalibrated
out-of-sample — see docs/BACKTESTING.md):
- short horizon leans on technicals/momentum (documented evidence that
  price/volume structure dominates 2–20 day outcomes),
- medium blends momentum/revisions with valuation,
- long leans on quality/growth/valuation (mean reversion of multiples plus
  compounding of returns on capital).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

COMPONENT_KEYS = (
    "quality", "growth", "valuation", "technical", "momentum",
    "sentiment", "catalysts", "balance_sheet",
)

WEIGHTS: dict[str, dict[str, dict[str, float]]] = {
    "v1.0.0": {
        "short": {
            "technical": 0.30, "momentum": 0.25, "sentiment": 0.12,
            "catalysts": 0.13, "valuation": 0.05, "quality": 0.05,
            "growth": 0.02, "balance_sheet": 0.08,
        },
        "medium": {
            "technical": 0.15, "momentum": 0.20, "sentiment": 0.08,
            "catalysts": 0.12, "valuation": 0.20, "quality": 0.10,
            "growth": 0.08, "balance_sheet": 0.07,
        },
        "long": {
            "quality": 0.25, "growth": 0.17, "valuation": 0.25,
            "balance_sheet": 0.13, "momentum": 0.05, "technical": 0.03,
            "sentiment": 0.02, "catalysts": 0.10,
        },
    },
}


def get_weights(version: str) -> dict[str, dict[str, float]]:
    if version not in WEIGHTS:
        raise KeyError(
            f"Unknown scoring model version '{version}'. Known: {sorted(WEIGHTS)}"
        )
    tables = WEIGHTS[version]
    for horizon, table in tables.items():
        total = sum(table.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"{version}/{horizon} weights sum to {total}, expected 1.0")
    return tables


def config_hash(version: str) -> str:
    payload = json.dumps(WEIGHTS[version], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def ensure_model_version(session: Session, version: str, notes: str = "") -> None:
    """Persist the version row (idempotent) and mark it active."""
    from vigil.models import ModelVersion

    row = session.execute(
        select(ModelVersion).where(ModelVersion.version == version)
    ).scalar_one_or_none()
    if row is None:
        row = ModelVersion(
            version=version,
            created_at=datetime.now(UTC),
            weights=WEIGHTS[version],
            config_hash=config_hash(version),
            notes=notes or "Initial transparent research-informed weights (pre-calibration).",
            active=True,
        )
        session.add(row)
    else:
        row.active = True
    for other in session.execute(
        select(ModelVersion).where(ModelVersion.version != version)
    ).scalars():
        other.active = False
