"""Narrative layer: numeric validation, template completeness, LLM fallback."""

from __future__ import annotations

from vigil.config import Settings
from vigil.llm.narrative import compose, compose_template, validate_numbers
from vigil.llm.packet import EvidencePacket


def packet(**overrides) -> EvidencePacket:
    base = dict(
        ticker="NVLT", name="Novalight Systems", sector="Technology",
        family="quality_compounder", horizon="long", state="TRIGGERED",
        transition="NEW→TRIGGERED", price=566.89, currency="USD",
        opportunity=7.8, confidence=6.9, risk=3.2,
        components={"quality": 8.5, "valuation": 5.2},
        rationale=["✓ quality 8.5 ≥ 7.0", "✓ long gate passed"],
        supporting=[{"key": "roic", "statement": "ROIC (TTM) is 18.4%", "value": 0.184,
                     "pillar": "quality", "source": "vigil · formula:roic"}],
        contradicting=[{"key": "pe", "statement": "P/E of 41.2 is above the 5y median",
                        "value": 41.2, "pillar": "valuation", "source": "vigil · formula:pe"}],
        entry_plan={"zone_low": 520.0, "zone_high": 545.0,
                    "invalidation_conditions": ["Two closes below 500.00"],
                    "fundamental_invalidation": ["TTM revenue declines"],
                    "trim_conditions": ["Price reaches 700.00"],
                    "exit_conditions": ["Risk exceeds maximum"]},
        warnings=[],
        catalysts=[{"kind": "earnings", "date": "2026-11-13", "days": 80}],
        change_lines=[],
    )
    base.update(overrides)
    p = EvidencePacket(**base)
    p.collect_numbers()
    return p


class TestNumericValidation:
    def test_packet_numbers_pass(self):
        p = packet()
        text = "Opportunity is 7.8 with ROIC at 18.4% and a P/E of 41.2."
        assert validate_numbers(text, p.allowed_numbers, 0.5) == []

    def test_invented_number_rejected(self):
        p = packet()
        text = "Revenue will grow 37.5% next year and the stock will hit 1234.56."
        violations = validate_numbers(text, p.allowed_numbers, 0.5)
        assert "37.5" in violations and "1234.56" in violations

    def test_small_prose_integers_allowed(self):
        p = packet()
        assert validate_numbers("three of the 4 conditions held over 2 quarters",
                                p.allowed_numbers, 0.5) == []

    def test_tolerance_respected(self):
        p = packet()
        # 566.9 vs allowed 566.89 well within 0.5%
        assert validate_numbers("price near 566.9", p.allowed_numbers, 0.5) == []


class TestTemplateComposer:
    def test_all_seven_questions_answered(self):
        qa, summary = compose_template(packet())
        assert "Novalight" in qa.why_this_company
        assert "Triggering conditions" in qa.why_now
        assert "quality" in qa.what_market_misunderstands or "valuation" in qa.what_market_misunderstands.lower() or "heroic" in qa.what_market_misunderstands
        assert "wrong if" in qa.what_would_prove_wrong
        assert "1–5 years" in qa.expected_holding_period
        assert qa.early_trim_or_exit_causes
        assert len(qa.three_largest_risks) == 3
        assert "NVLT" in summary

    def test_template_never_invents_numbers(self):
        p = packet()
        qa, summary = compose_template(p)
        joined = " ".join([summary, qa.why_this_company, qa.why_now,
                           qa.what_market_misunderstands, qa.what_would_prove_wrong,
                           qa.early_trim_or_exit_causes, *qa.three_largest_risks])
        assert validate_numbers(joined, p.allowed_numbers, 0.5) == []

    def test_compose_falls_back_to_template_without_llm(self):
        settings = Settings(debug=True)  # llm disabled by default
        qa, _summary, source = compose(packet(), settings)
        assert source == "template"
        assert qa.three_largest_risks
