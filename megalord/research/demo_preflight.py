"""Read-only check for a separately registered, pinned free MT5 demo account.

Never logs in, changes accounts, selects symbols, sends orders, or enables the
production daemon. MT5 demo trade_mode alone is insufficient for prop accounts.
"""
from pathlib import Path
import argparse
import json

FREE_DEMO_SERVERS = frozenset({"MetaQuotes-Demo"})


def demo_account_check(account, *, expected_login, expected_server):
    result = {"eligible_account_profile": False, "orders_sent": 0, "live_approved": False}
    if type(expected_login) is not int or expected_login <= 0:
        return {**result, "reason": "Separate demo account is not pinned"}
    if expected_server not in FREE_DEMO_SERVERS:
        return {**result, "reason": "Server is not the selected free demo server"}
    if account is None:
        return {**result, "reason": "No account information available"}
    if getattr(account, "server", None) != expected_server or getattr(account, "login", None) != expected_login:
        return {**result, "reason": "Current account/server differs from pinned demo profile"}
    # MT5's documented demo enumeration is zero. A funded/evaluation account
    # can also report zero, hence the independent server and login restrictions.
    if getattr(account, "trade_mode", None) != 0:
        return {**result, "reason": "Account is not MT5 demo mode"}
    return {**result, "eligible_account_profile": True,
            "reason": "Pinned demo account profile matched; strategy/execution approval remains separate"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal-path", type=Path, required=True)
    parser.add_argument("--expected-login", type=int, required=True)
    parser.add_argument("--expected-server", default="MetaQuotes-Demo")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.expected_login <= 0 or args.expected_server not in FREE_DEMO_SERVERS:
        raise SystemExit("Pin a separately registered free demo account before connecting")
    import MetaTrader5 as mt5
    # No login/password/server parameters: initialization must not switch the
    # terminal's account. Only the dedicated demo terminal path should be used.
    if mt5.initialize(str(args.terminal_path), timeout=5000):
        try:
            result = demo_account_check(mt5.account_info(), expected_login=args.expected_login,
                                        expected_server=args.expected_server)
        finally:
            mt5.shutdown()
    else:
        result = {"eligible_account_profile": False, "orders_sent": 0, "live_approved": False,
                  "reason": "Terminal connection failed"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(result["reason"])
