"""Narrative synthesis: template composer (default) and optional LLM.

Rules (brief principles #2 and the technology section):
* The calculation engine never depends on this module's output.
* The LLM receives ONLY the structured evidence packet and must return
  strict JSON matching ``NarrativeOut``. Any parse failure, schema
  violation, or numeric claim not present in the packet's allowed numbers
  (within tolerance) rejects the narrative and falls back to templates.
* The deterministic template composer produces the same fields from the
  same packet, so alerts are complete without any API key.
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, ValidationError

from vigil.config import Settings
from vigil.llm.packet import EvidencePacket
from vigil.schemas.alerts import ThesisQA

log = logging.getLogger(__name__)


class NarrativeOut(BaseModel):
    thesis_summary: str
    why_this_company: str
    why_now: str
    what_market_misunderstands: str
    what_would_prove_wrong: str
    expected_holding_period: str
    early_trim_or_exit_causes: str
    three_largest_risks: list[str]


HOLDING_PERIOD = {
    "short": "approximately 2–20 trading days",
    "medium": "approximately 1–6 months",
    "long": "approximately 1–5 years",
}

_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def validate_numbers(text: str, allowed: list[float], tolerance_pct: float) -> list[str]:
    """Return the list of numeric claims in ``text`` that do not match any
    allowed number within tolerance. Empty list = valid.

    Trivial small integers (0–12) are permitted: they appear in ordinary
    prose ("three risks", "two quarters") without being data claims.
    """
    violations: list[str] = []
    allowed_set = allowed or []
    for m in _NUM_RE.finditer(text):
        raw = m.group().replace(",", "")
        try:
            val = float(raw)
        except ValueError:
            continue
        if val.is_integer() and 0 <= abs(val) <= 12:
            continue
        ok = any(
            abs(val - a) <= max(abs(a) * tolerance_pct / 100.0, 1e-9) for a in allowed_set
        )
        if not ok:
            violations.append(raw)
    return violations


# ---------------------------------------------------------------------------
# Deterministic template composer
# ---------------------------------------------------------------------------

FAMILY_ANGLE = {
    "deep_value": "the market is pricing this business for a worse outcome than its "
    "cash generation and balance sheet indicate",
    "quality_compounder": "a durable high-return business is available at a valuation "
    "that does not require heroic assumptions",
    "oversold_at_support": "a sharp drawdown has reached statistically tested support "
    "while the underlying business evidence is intact",
    "constructive_pullback": "an established uptrend has pulled back to support without "
    "deterioration in the underlying evidence",
    "breakout_continuation": "price has broken out of a long consolidation on volume, "
    "with fundamental confirmation",
    "fundamental_inflection": "profitability is inflecting and consensus estimates are "
    "still catching up",
    "estimate_momentum": "analyst estimates are being revised up broadly and materially, "
    "which historically resolves with continued strength",
    "watch_setup": "a setup is forming but has not yet met the confirmation conditions",
    "hold": "the existing position's evidence still supports holding",
    "avoid": "the apparent opportunity fails critical risk checks",
    "trim": "the reward remaining no longer justifies the full position",
    "full_exit": "core assumptions behind holding this position no longer hold",
    "thesis_invalidated": "the original thesis has been contradicted by new evidence",
}


def compose_template(packet: EvidencePacket) -> tuple[ThesisQA, str]:
    top_support = [s["statement"] for s in packet.supporting[:3]]
    top_contra = [c["statement"] for c in packet.contradicting[:2]]
    angle = FAMILY_ANGLE.get(packet.family, "the weight of evidence is asymmetric")

    why_company = (
        f"{packet.name} ({packet.ticker}) screens as a "
        f"{packet.family.replace('_', ' ')} candidate on the {packet.horizon} horizon: "
        + ("; ".join(top_support) if top_support else "see the evidence list")
        + "."
    )
    trigger_lines = [r.lstrip("✓✗· ") for r in packet.rationale if r.startswith("✓")][:3]
    why_now = (
        "Triggering conditions met on this scan: " + "; ".join(trigger_lines) + "."
        if trigger_lines
        else "The composite score crossed the alerting gate on this scan."
    )
    misunderstood = f"Possible mispricing: {angle}."
    if top_contra:
        misunderstood += f" The bear case rests on: {'; '.join(top_contra)}."
    inv = packet.entry_plan.get("invalidation_conditions", []) + packet.entry_plan.get(
        "fundamental_invalidation", []
    )
    prove_wrong = (
        "The thesis is wrong if: " + "; ".join(inv[:4]) + "."
        if inv
        else "The thesis is wrong if the supporting evidence above reverses."
    )
    trim_causes = "; ".join(packet.entry_plan.get("trim_conditions", [])[:2])
    exit_causes = "; ".join(packet.entry_plan.get("exit_conditions", [])[:2])
    early = (
        f"Trim early when: {trim_causes}. Exit early when: {exit_causes}."
        if trim_causes or exit_causes
        else "Trim or exit early if invalidation conditions approach."
    )
    risks = [c["statement"] for c in packet.contradicting[:3]]
    while len(risks) < 3:
        fallback = [
            f"composite risk score is {packet.risk:.1f}/10",
            "general market and sector drawdown risk",
            "estimate/consensus data may lag reported developments",
        ]
        risks.append(fallback[len(risks) % 3])

    qa = ThesisQA(
        why_this_company=why_company,
        why_now=why_now,
        what_market_misunderstands=misunderstood,
        what_would_prove_wrong=prove_wrong,
        expected_holding_period=HOLDING_PERIOD.get(packet.horizon, "unspecified"),
        early_trim_or_exit_causes=early,
        three_largest_risks=risks[:3],
    )
    summary = (
        f"{packet.ticker}: {packet.family.replace('_', ' ')} ({packet.state}, "
        f"{packet.horizon} horizon). Opportunity {packet.opportunity:.1f}, "
        f"confidence {packet.confidence:.1f}, risk {packet.risk:.1f}. "
        f"{why_now}"
    )
    return qa, summary


# ---------------------------------------------------------------------------
# LLM synthesiser (optional)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the narrative writer for a personal equity research tool.
You receive a structured evidence packet. Write concise, plain-English research
narrative STRICTLY grounded in the packet.

Absolute rules:
- Use ONLY facts and numbers present in the packet. Do not introduce any price,
  metric, event, probability, or citation that is not in it.
- Never promise or imply guaranteed returns.
- Attribute analyst targets/sentiment as opinions, not facts.
- Return ONLY a JSON object with keys: thesis_summary, why_this_company, why_now,
  what_market_misunderstands, what_would_prove_wrong, expected_holding_period,
  early_trim_or_exit_causes, three_largest_risks (array of 3 strings).
"""

# Prompt template version — recorded on alerts produced via the LLM path.
PROMPT_VERSION = "narrative-v1"


def compose_llm(packet: EvidencePacket, settings: Settings) -> tuple[ThesisQA, str] | None:
    """Returns validated narrative or None (caller falls back to template)."""
    if not settings.llm.enabled or not settings.anthropic_api_key:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        msg = client.messages.create(
            model=settings.llm.model,
            max_tokens=settings.llm.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": "Evidence packet (JSON):\n"
                    + packet.model_dump_json(exclude={"allowed_numbers"}),
                }
            ],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            log.warning("LLM narrative rejected: no JSON object found")
            return None
        data = json.loads(text[start : end + 1])
        out = NarrativeOut.model_validate(data)
    except (ValidationError, json.JSONDecodeError) as exc:
        log.warning("LLM narrative rejected: schema violation: %s", exc)
        return None
    except Exception as exc:
        log.warning("LLM narrative unavailable: %s", exc)
        return None

    joined = " ".join(
        [
            out.thesis_summary, out.why_this_company, out.why_now,
            out.what_market_misunderstands, out.what_would_prove_wrong,
            out.early_trim_or_exit_causes, *out.three_largest_risks,
        ]
    )
    violations = validate_numbers(
        joined, packet.allowed_numbers, settings.llm.numeric_tolerance_pct
    )
    if violations:
        log.warning(
            "LLM narrative rejected: unsupported numeric claims %s", violations[:10]
        )
        return None
    qa = ThesisQA(
        why_this_company=out.why_this_company,
        why_now=out.why_now,
        what_market_misunderstands=out.what_market_misunderstands,
        what_would_prove_wrong=out.what_would_prove_wrong,
        expected_holding_period=out.expected_holding_period,
        early_trim_or_exit_causes=out.early_trim_or_exit_causes,
        three_largest_risks=out.three_largest_risks[:3],
    )
    return qa, out.thesis_summary


def compose(packet: EvidencePacket, settings: Settings) -> tuple[ThesisQA, str, str]:
    """Returns (thesis, summary, source) where source is 'llm' or 'template'."""
    result = compose_llm(packet, settings)
    if result is not None:
        qa, summary = result
        return qa, summary, "llm"
    qa, summary = compose_template(packet)
    return qa, summary, "template"
