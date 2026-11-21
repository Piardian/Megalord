import pytest

from megalord.research.fill_ledger import FillEvent, FillLedger
from megalord.research.trade_contract import ENGINES


def ledger(engine="Trawma", **changes):
    args = dict(engine=engine, symbol="EURUSD", direction="LONG", initial_stop=99,
                initial_risk_cash=100, account_currency="USD", source_sha256="a" * 64,
                cost_treatment={"spread": "in_fill_pnl", "slippage": "in_fill_pnl",
                                "commission": "cash_event", "carry": "cash_event", "conversion": "not_applicable"},
                cost_basis="Synthetic regression fills, not market observations")
    args.update(changes)
    return FillLedger(**args)


def fill(kind, time, quantity, price, gross=None, name=None):
    return FillEvent(name or kind + time, kind, time, time, quantity, price, gross)


def cash(component, amount, time="2026-10-01T02:00Z"):
    return FillEvent(component, "COST", time, time, cost_cash=amount, component=component)


@pytest.mark.parametrize("engine", ENGINES)
def test_all_six_engines_share_partial_fill_and_net_accounting(engine):
    book = ledger(engine)
    book.append(fill("ENTRY", "2026-10-01T01:00Z", 1, 100))
    book.append(fill("EXIT", "2026-10-01T02:00Z", .5, 101, 50))
    with pytest.raises(ValueError, match="fully closed"):
        book.closed_evidence()
    book.append(fill("EXIT", "2026-10-01T02:00Z", .5, 102, 100, name="tp2"))
    book.append(cash("commission", 5))
    book.append(cash("carry", 2))
    assert book.summary()["net_cash"] == 143
    assert book.closed_evidence().net_r == pytest.approx(1.43)


def test_embedded_spread_cannot_be_deducted_again():
    book = ledger()
    book.append(fill("ENTRY", "2026-10-01T01:00Z", 1, 100))
    with pytest.raises(ValueError, match="twice"):
        book.append(cash("spread", 10))


def test_credit_is_recorded_separately_and_does_not_become_a_negative_stress_cost():
    book = ledger()
    book.append(fill("ENTRY", "2026-10-01T01:00Z", 1, 100))
    book.append(fill("EXIT", "2026-10-01T02:00Z", 1, 99, -100))
    book.append(cash("commission", 5))
    book.append(cash("carry", -2))
    measured = book.closed_evidence()
    assert measured.net_r == pytest.approx(-1.03)
    assert measured.gross_r - 1.5 * measured.cost_r == pytest.approx(-1.055)


def test_zero_cost_needs_explicit_evidence_and_unknown_coverage_blocks_scoring():
    book = ledger()
    book.append(fill("ENTRY", "2026-10-01T01:00Z", 1, 100))
    book.append(fill("EXIT", "2026-10-01T02:00Z", 1, 100, 0))
    with pytest.raises(ValueError, match="explicit evidence"):
        book.closed_evidence()
    book.append(cash("commission", 0))
    book.append(cash("carry", 0))
    assert book.closed_evidence().net_r == 0


def test_overclose_duplicate_and_future_availability_are_rejected():
    book = ledger()
    event = fill("ENTRY", "2026-10-01T01:00Z", 1, 100)
    book.append(event)
    with pytest.raises(ValueError, match="Unique"):
        book.append(event)
    with pytest.raises(ValueError, match="exceeds"):
        book.append(fill("EXIT", "2026-10-01T02:00Z", 2, 99, -200))
    with pytest.raises(ValueError, match="before it happened"):
        book.append(FillEvent("future", "EXIT", "2026-10-01T03:00Z", "2026-10-01T02:00Z", 1, 99, -100))


def test_unknown_component_blocks_net_evidence_even_with_complete_fills():
    book = ledger(cost_treatment={"spread": "in_fill_pnl", "slippage": "unknown",
                                  "commission": "not_applicable", "carry": "not_applicable", "conversion": "not_applicable"})
    book.append(fill("ENTRY", "2026-10-01T01:00Z", 1, 100))
    book.append(fill("EXIT", "2026-10-01T02:00Z", 1, 101, 100))
    with pytest.raises(ValueError, match="Unresolved cost coverage"):
        book.closed_evidence()


def test_closed_result_cannot_be_reused_before_net_event_is_available():
    book = ledger(cost_treatment={key: "in_fill_pnl" for key in ("spread", "slippage", "commission", "carry", "conversion")})
    book.append(fill("ENTRY", "2026-10-01T01:00Z", 1, 100))
    book.append(FillEvent("exit", "EXIT", "2026-10-01T02:00Z", "2026-10-01T02:15Z", 1, 101, 100))
    assert book.closed_evidence().exit_time.isoformat() == "2026-10-01T02:15:00+00:00"


def test_native_aggregate_fee_is_preserved_without_inventing_a_component_split():
    book = ledger(cost_treatment={"spread": "not_applicable", "slippage": "bundled_cash",
                                  "commission": "bundled_cash", "carry": "not_applicable", "conversion": "not_applicable"},
                  cost_groups={"native_execution_fee": ["commission", "slippage"]})
    book.append(fill("ENTRY", "2026-10-01T01:00Z", 1, 100))
    book.append(fill("EXIT", "2026-10-01T02:00Z", 1, 101, 100))
    with pytest.raises(ValueError, match="group event"):
        book.closed_evidence()
    with pytest.raises(ValueError, match="twice"):
        book.append(cash("slippage", 2))
    book.append(cash("native_execution_fee", 8))
    assert book.closed_evidence().net_r == pytest.approx(.92)
