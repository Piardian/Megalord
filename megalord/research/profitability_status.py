"""Show the latest coupled quote result separately from verified net evidence."""
from datetime import datetime,timezone
from pathlib import Path
import json

from megalord.research.net_review import assess_net_evidence,REQUIRED_COSTS

ROOT=Path(__file__).resolve().parents[2]


def main():
    registry=json.loads((ROOT/'audit/forward_portfolio/current.json').read_text())
    home=Path(registry['home'])
    state=json.loads((home/'state.json').read_text())
    at=datetime.now(timezone.utc)
    # Broker-confirmed trades are deliberately not manufactured from model fills.
    evidence=dict(started_at=registry['started_at'],daily_equity_cash_change={},
        daily_stress_equity_cash_change={},closed_trade_count=0,
        broker_fills_reconciled=False,all_intended_decisions_recorded=False,
        open_positions_marked_to_market=False,cost_components_verified={k:None for k in REQUIRED_COSTS},
        raw_evidence_sha256=[])
    report=dict(observed_at=at.isoformat(),portfolio_state_asof=state['observed_at'],
        portfolio_heartbeat_fresh=(at-datetime.fromisoformat(state['observed_at'])).total_seconds()<60,
        source_health=state['source_health'],quote_model=state['summary'],
        net_review=assess_net_evidence(evidence,as_of=at),
        venue_execution_status='No linked broker fill/cash reconciliation for this Megalord segment',
        note='Model fills cannot substitute for broker fills. No net broker ledger is connected yet.')
    (home/'profitability_review.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
