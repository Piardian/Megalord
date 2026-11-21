import json

from megalord.research.inventory import inspect_source


def test_inventory_observes_missing_evidence_without_filling_it(tmp_path):
    path = tmp_path / "audit.json"
    path.write_text(json.dumps([
        {"symbol": "EURUSD", "timestamp": 1787784300000, "actualOutcome": "TP", "realizedR": 0},
        {"symbol": "GBPUSD", "exit_time": "2026-10-01T00:00Z", "realizedR": -1},
    ]), encoding="utf-8")
    before = path.read_bytes()
    report = inspect_source(path)
    sample = report["record_sets"]["root"]
    assert sample["missing_explicit_exit_time"] == 1
    assert sample["numeric_millisecond_timestamp"] == 1
    assert sample["checklist_imputed_tp_r_candidates"] == 1
    assert len(report["sha256"]) == 64
    assert path.read_bytes() == before


def test_missing_and_malformed_sources_are_reported(tmp_path):
    assert inspect_source(tmp_path / "missing.json")["exists"] is False
    path = tmp_path / "broken.json"
    path.write_text("broken", encoding="utf-8")
    assert "parse_error" in inspect_source(path)
