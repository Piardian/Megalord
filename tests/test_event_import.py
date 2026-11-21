import json

from megalord.research.event_import import import_event_document
from megalord.research.trade_contract import ENGINES


def document():
    return {"schema_version": 1, "source_kind": "native_replay", "trades": [{
        "trade_id": engine, "engine": engine, "symbol": "EURUSD", "direction": "LONG",
        "initial_stop": 99, "initial_risk_cash": 100, "account_currency": "USD",
        "cost_treatment": {component: "in_fill_pnl" for component in ("spread", "slippage", "commission", "carry", "conversion")},
        "cost_basis": "Synthetic regression data only; all explicit component declarations remain the producer's responsibility",
        "events": [
            {"event_id": "entry", "kind": "ENTRY", "occurred_at": "2026-10-01T01:00Z", "available_at": "2026-10-01T01:00Z", "quantity": 1, "price": 100},
            {"event_id": "exit", "kind": "EXIT", "occurred_at": "2026-10-01T02:00Z", "available_at": "2026-10-01T02:00Z", "quantity": 1, "price": 101, "gross_cash": 100},
        ],
    } for engine in ENGINES]}


def test_all_six_producers_import_through_one_event_interface():
    result = import_event_document(json.dumps(document()).encode())
    assert len(result["closed"]) == 6
    assert result["live_approved"] is False
    assert all(row["source_kind"] == "native_replay" for row in result["closed"])


def test_one_missing_expense_does_not_silently_become_zero():
    data = document()
    data["trades"][0]["cost_treatment"]["commission"] = "cash_event"
    result = import_event_document(json.dumps(data).encode())
    assert len(result["closed"]) == 5
    assert "explicit evidence" in result["rejected"][0]["reason"]


def test_duplicate_trade_cannot_leave_one_outcome_selected_by_row_order():
    data = document()
    data["trades"].append(data["trades"][0])
    result = import_event_document(json.dumps(data).encode())
    assert len(result["closed"]) == 5
    assert result["duplicate_trade_ids"] == ["Trawma"]
