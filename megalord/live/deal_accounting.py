"""Pure MT5 hedged-position cash reconciliation; never calls a terminal."""
from math import isfinite


def summarize_closed_position(deals, position_id):
    """Require balanced entry/exit volume and all four MT5 cash components.

    Netting reversals (INOUT) and unrelated separate account charges require
    their own attribution and are intentionally not guessed here.
    """
    try:
        records = [dict(d) if isinstance(d, dict) else d._asdict() if hasattr(d, "_asdict") else vars(d) for d in deals]
    except (TypeError, ValueError):
        return None
    if not records:
        return None
    seen = set()
    for d in records:
        if not isinstance(d.get("ticket"), int) or d["ticket"] <= 0:
            return None
        if d.get("position_id") != int(position_id) or d.get("ticket") in seen or d.get("entry") not in (0, 1, 3):
            return None
        seen.add(d["ticket"])
        required = ("volume", "price", "profit", "commission", "swap", "fee", "time")
        if any(k not in d or not isinstance(d[k], (int, float)) or not isfinite(d[k]) for k in required):
            return None
        if d["volume"] <= 0 or d["price"] <= 0:
            return None
    entries = [d for d in records if d["entry"] == 0]
    exits = [d for d in records if d["entry"] in (1, 3)]
    entered, exited = sum(d["volume"] for d in entries), sum(d["volume"] for d in exits)
    if not entries or not exits or abs(entered - exited) > max(1e-9, entered * 1e-8):
        return None
    last = max(exits, key=lambda d: (d.get("time_msc") or d["time"] * 1000, d["ticket"]))
    totals = {k: sum(d[k] for d in records) for k in ("profit", "commission", "swap", "fee")}
    # Keep the old public profit field NET so daemon callers do not lose costs.
    return {"ticket": last["ticket"], "order": last.get("order"), "position_id": int(position_id),
        "profit": sum(totals.values()), "gross_profit": totals["profit"],
        "commission": totals["commission"], "swap": totals["swap"], "fee": totals["fee"],
        "price": last["price"], "comment": last.get("comment", ""), "time": last["time"],
        "reason": last.get("reason"), "entered_volume": entered, "exited_volume": exited,
        "deal_tickets": sorted(seen), "deals": records,
        "position_deal_cash_complete": True,
        "unattributed_account_charges_covered": False}
