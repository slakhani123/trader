"""Structured evidence packet handed to the narrative layer.

The LLM (or the deterministic template composer) may ONLY use what is in
this packet. Every numeric value the narrative is allowed to mention is
collected in ``allowed_numbers`` so the validator can reject any invented
figure.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from vigil.schemas.core import Evidence, ScoreBundle, SignalCandidate


class EvidencePacket(BaseModel):
    ticker: str
    name: str
    sector: str
    family: str
    horizon: str
    state: str
    transition: str
    price: float | None
    currency: str
    opportunity: float
    confidence: float
    risk: float
    components: dict[str, float]
    rationale: list[str]
    supporting: list[dict]
    contradicting: list[dict]
    entry_plan: dict
    warnings: list[str]
    catalysts: list[dict] = Field(default_factory=list)
    change_lines: list[str] = Field(default_factory=list)
    allowed_numbers: list[float] = Field(default_factory=list)

    def collect_numbers(self) -> None:
        nums: set[float] = set()

        def add(value: Any) -> None:
            if isinstance(value, bool):
                return
            if isinstance(value, int | float):
                nums.add(round(float(value), 4))

        def walk(obj: Any) -> None:
            if isinstance(obj, dict):
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v)
            elif isinstance(obj, str):
                for m in _NUM_RE.finditer(obj):
                    try:
                        nums.add(round(float(m.group().replace(",", "")), 4))
                    except ValueError:
                        pass
            else:
                add(obj)

        walk(self.model_dump(exclude={"allowed_numbers"}))
        self.allowed_numbers = sorted(nums)


_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _ev_dict(e: Evidence) -> dict:
    return {
        "key": e.key,
        "statement": e.statement,
        "value": e.value,
        "pillar": e.pillar,
        "source": e.source.describe(),
    }


def build_packet(
    snapshot_info: dict,
    candidate: SignalCandidate | None,
    bundle: ScoreBundle,
    family: str,
    horizon: str,
    state: str,
    transition: str,
    reasons: list[str],
    changed: list[str],
    price: float | None,
    catalysts: list[dict],
) -> EvidencePacket:
    scores = bundle.horizons[horizon]
    supporting = candidate.supporting if candidate else [
        e for e in bundle.evidence if e.direction == "supports"
    ][:8]
    contradicting = candidate.contradicting if candidate else [
        e for e in bundle.evidence if e.direction == "contradicts"
    ][:8]
    packet = EvidencePacket(
        ticker=snapshot_info["ticker"],
        name=snapshot_info["name"],
        sector=snapshot_info["sector"],
        family=family,
        horizon=horizon,
        state=state,
        transition=transition,
        price=price,
        currency=snapshot_info["currency"],
        opportunity=scores.opportunity,
        confidence=scores.confidence,
        risk=scores.risk,
        components=scores.components,
        rationale=reasons,
        supporting=[_ev_dict(e) for e in supporting],
        contradicting=[_ev_dict(e) for e in contradicting],
        entry_plan=candidate.entry_plan.model_dump(mode="json") if candidate else {},
        warnings=bundle.warnings,
        catalysts=catalysts,
        change_lines=changed,
    )
    packet.collect_numbers()
    return packet
