"""Fail-closed MT5 execution and risk-based volume sizing."""
from __future__ import annotations

import logging
import math
import time
from decimal import Decimal
from typing import Dict, Any, Optional

try:
    import MetaTrader5 as mt5
except ImportError:  # Allows unit-testing pure sizing decisions off Windows/MT5.
    mt5 = None  # type: ignore[assignment]

logger = logging.getLogger("Megalord.MT5Executor")
MEGALORD_MAGIC_NUMBER = 202699


class MegalordMT5Executor:
    def __init__(self, target_account: int = 26735345, base_risk_pct: float = 0.50):
        self.target_account = target_account
        self.base_risk_pct = float(base_risk_pct)
        self.magic = MEGALORD_MAGIC_NUMBER

    def ensure_connected(self) -> bool:
        if mt5 is None:
            logger.error("MetaTrader5 package is unavailable; live execution is disabled.")
            return False
        if not mt5.initialize():
            logger.error("MT5 initialize failed: %s", mt5.last_error())
            return False
        acc = mt5.account_info()
        if not acc:
            logger.error("Could not fetch account info from MT5.")
            return False
        if int(acc.login) != int(self.target_account):
            logger.error("Target account mismatch: expected #%s, found #%s; refusing to trade.", self.target_account, acc.login)
            return False
        if not math.isfinite(float(getattr(acc, "equity", 0.0))) or float(acc.equity) <= 0:
            logger.error("Account equity is unavailable or invalid; refusing to trade.")
            return False
        return True

    @staticmethod
    def _valid_price(value: Any) -> bool:
        try:
            x = float(value)
            return math.isfinite(x) and x > 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _volume_digits(step: float) -> int:
        normalized = Decimal(str(step)).normalize()
        return max(0, -normalized.as_tuple().exponent)

    @staticmethod
    def _floor_to_step(volume: float, step: float) -> float:
        # A small epsilon handles binary floating-point noise without rounding up.
        units = math.floor((volume / step) + 1e-10)
        return round(units * step, MegalordMT5Executor._volume_digits(step))

    def calculate_lot_size(self, symbol: str, entry_price: float, sl_price: float, risk_mult: float = 1.0) -> Optional[float]:
        """Return a broker-valid volume that cannot exceed the configured risk budget.

        None means the trade is not safely sizeable. There is deliberately no
        default/minimum-lot fallback.
        """
        if mt5 is None:
            logger.error("MT5 package unavailable; cannot calculate verified risk.")
            return None

        try:
            entry = float(entry_price)
            stop = float(sl_price)
            multiplier = float(risk_mult)
            base_pct = float(self.base_risk_pct)
        except (TypeError, ValueError):
            logger.error("Invalid entry, stop, or risk multiplier for %s.", symbol)
            return None

        if not math.isfinite(entry) or entry <= 0 or not math.isfinite(stop) or stop <= 0 or entry == stop:
            logger.error("Invalid entry/SL geometry for %s: entry=%s SL=%s.", symbol, entry_price, sl_price)
            return None
        if not math.isfinite(multiplier) or multiplier <= 0 or not math.isfinite(base_pct) or base_pct <= 0:
            logger.error("Invalid risk percentage/multiplier for %s.", symbol)
            return None

        acc = mt5.account_info()
        if not acc:
            logger.error("Account info unavailable; refusing to size %s.", symbol)
            return None
        equity = getattr(acc, "equity", None)
        if not self._valid_price(equity):
            logger.error("Account equity missing or invalid; refusing to size %s.", symbol)
            return None

        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            logger.error("Symbol metadata unavailable for %s; refusing to size.", symbol)
            return None

        tick_size = getattr(sym_info, "trade_tick_size", None)
        volume_min = getattr(sym_info, "volume_min", None)
        volume_max = getattr(sym_info, "volume_max", None)
        volume_step = getattr(sym_info, "volume_step", None)
        for label, value in (("tick_size", tick_size), ("volume_min", volume_min), ("volume_max", volume_max), ("volume_step", volume_step)):
            try:
                valid = math.isfinite(float(value)) and float(value) > 0
            except (TypeError, ValueError):
                valid = False
            if not valid:
                logger.error("Missing/invalid %s for %s; refusing to size.", label, symbol)
                return None
        tick_size = float(tick_size)
        volume_min = float(volume_min)
        volume_max = float(volume_max)
        volume_step = float(volume_step)
        if volume_max < volume_min or volume_step > volume_max:
            logger.error("Inconsistent broker volume constraints for %s.", symbol)
            return None

        # Direction-specific MT5 profit calculation uses account-currency P/L and
        # the broker's contract specification, avoiding assumptions about tick value.
        if stop < entry:
            order_type = mt5.ORDER_TYPE_BUY
        else:
            order_type = mt5.ORDER_TYPE_SELL
        try:
            one_lot_pnl = mt5.order_calc_profit(order_type, symbol, 1.0, entry, stop)
        except Exception as exc:
            logger.error("order_calc_profit failed for %s: %s", symbol, exc)
            return None
        if one_lot_pnl is None:
            logger.error("Broker could not calculate stop-loss P/L for %s; refusing to size.", symbol)
            return None
        try:
            loss_per_lot = abs(float(one_lot_pnl))
        except (TypeError, ValueError):
            loss_per_lot = 0.0
        if not math.isfinite(loss_per_lot) or loss_per_lot <= 0:
            logger.error("Invalid loss-per-lot result for %s: %r.", symbol, one_lot_pnl)
            return None

        risk_cash = float(equity) * (base_pct * multiplier / 100.0)
        if not math.isfinite(risk_cash) or risk_cash <= 0:
            logger.error("Calculated risk budget is invalid for %s.", symbol)
            return None

        raw_volume = risk_cash / loss_per_lot
        if not math.isfinite(raw_volume) or raw_volume <= 0:
            logger.error("Calculated volume is invalid for %s.", symbol)
            return None
        # Always round down. Never round to nearest and never raise to broker minimum.
        volume = min(volume_max, self._floor_to_step(raw_volume, volume_step))
        if volume + 1e-12 < volume_min:
            logger.warning(
                "Trade rejected for %s: safe volume %.8f is below broker minimum %.8f (risk budget %.2f).",
                symbol, volume, volume_min, risk_cash,
            )
            return None

        # Verify the actual normalized volume against broker-calculated loss.
        # Step down if broker P/L rounding makes the risk slightly exceed budget.
        tolerance = max(1e-8, risk_cash * 1e-9)
        while volume + 1e-12 >= volume_min:
            try:
                planned_pnl = mt5.order_calc_profit(order_type, symbol, volume, entry, stop)
            except Exception:
                planned_pnl = None
            if planned_pnl is None:
                logger.error("Could not verify risk at final volume for %s; refusing to size.", symbol)
                return None
            planned_loss = abs(float(planned_pnl))
            if math.isfinite(planned_loss) and planned_loss <= risk_cash + tolerance:
                logger.info(
                    "Risk sizing %s: equity=%.2f budget=%.2f stop_loss=%.2f volume=%.8f",
                    symbol, float(equity), risk_cash, planned_loss, volume,
                )
                return volume
            volume = self._floor_to_step(volume - volume_step, volume_step)

        logger.warning("No broker-valid volume for %s satisfies the risk budget; trade rejected.", symbol)
        return None

    def _valid_order_geometry(self, direction: str, entry: float, sl: float, tp: float) -> bool:
        side = str(direction).upper()
        if side not in ("LONG", "BUY", "SHORT", "SELL"):
            logger.error("Unsupported order direction %r.", direction)
            return False
        if not self._valid_price(entry) or not self._valid_price(sl) or not self._valid_price(tp):
            logger.error("Entry, SL, and TP must all be finite positive prices.")
            return False
        is_buy = side in ("LONG", "BUY")
        if is_buy and not (sl < entry < tp):
            logger.error("Invalid LONG geometry: require SL < entry < TP (SL=%s entry=%s TP=%s).", sl, entry, tp)
            return False
        if not is_buy and not (tp < entry < sl):
            logger.error("Invalid SHORT geometry: require TP < entry < SL (TP=%s entry=%s SL=%s).", tp, entry, sl)
            return False
        return True

    def _prepare_symbol_and_tick(self, symbol: str):
        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            logger.error("Cannot trade symbol %s: broker metadata not found.", symbol)
            return None, None
        if not getattr(sym_info, "visible", True):
            if not mt5.symbol_select(symbol, True):
                logger.error("Could not select symbol %s in Market Watch.", symbol)
                return None, None
            sym_info = mt5.symbol_info(symbol)
            if not sym_info:
                return None, None
        tick = mt5.symbol_info_tick(symbol)
        if not tick or not self._valid_price(getattr(tick, "bid", None)) or not self._valid_price(getattr(tick, "ask", None)):
            logger.error("Live bid/ask unavailable for %s.", symbol)
            return None, None
        return sym_info, tick

    def get_market_quality_context(self, symbol: str, direction: str) -> Optional[Dict[str, float]]:
        """Return a fresh broker quote for pre-trade spread and entry-drift checks."""
        if not self.ensure_connected():
            return None
        sym_info, tick = self._prepare_symbol_and_tick(symbol)
        if not sym_info or not tick:
            return None
        side = str(direction).upper()
        if side not in ("LONG", "BUY", "SHORT", "SELL"):
            return None
        bid, ask = float(tick.bid), float(tick.ask)
        spread = ask - bid
        point = getattr(sym_info, "point", None)
        if not math.isfinite(spread) or spread < 0 or not self._valid_price(point):
            logger.error("Invalid live spread/point for %s.", symbol)
            return None
        executable_price = ask if side in ("LONG", "BUY") else bid
        return {
            "current_price": executable_price,
            "spread_price": spread,
            "spread_points": spread / float(point),
            "point": float(point),
        }

    def _check_margin(self, symbol: str, order_type: int, volume: float, price: float) -> bool:
        acc = mt5.account_info()
        if not acc:
            logger.error("Account info unavailable during margin check.")
            return False
        try:
            required = mt5.order_calc_margin(order_type, symbol, volume, price)
        except Exception as exc:
            logger.error("Margin calculation failed for %s: %s", symbol, exc)
            return False
        if required is None or not math.isfinite(float(required)) or float(required) < 0:
            logger.error("Margin requirement unavailable for %s.", symbol)
            return False
        free_margin = getattr(acc, "margin_free", None)
        if free_margin is None or not math.isfinite(float(free_margin)) or float(free_margin) < float(required):
            logger.error("Insufficient or unknown free margin for %s: required=%s free=%s.", symbol, required, free_margin)
            return False
        return True

    def place_market_order(
        self,
        symbol: str,
        direction: str,
        sl_price: float,
        tp_price: float,
        risk_mult: float = 1.0,
        comment: str = "Megalord_Live",
    ) -> Optional[Dict[str, Any]]:
        if not self.ensure_connected():
            return None
        sym_info, tick = self._prepare_symbol_and_tick(symbol)
        if not sym_info or not tick:
            return None
        side = str(direction).upper()
        if side not in ("LONG", "BUY", "SHORT", "SELL"):
            logger.error("Unsupported order direction %r.", direction)
            return None
        is_buy = side in ("LONG", "BUY")
        order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        entry_price = float(tick.ask if is_buy else tick.bid)
        if not self._valid_order_geometry(side, entry_price, sl_price, tp_price):
            return None
        lot = self.calculate_lot_size(symbol, entry_price, sl_price, risk_mult)
        if lot is None:
            logger.warning("Order rejected before submission: safe lot sizing failed for %s.", symbol)
            return None
        if not self._check_margin(symbol, order_type, lot, entry_price):
            return None
        res = self._send_single_deal(symbol, order_type, entry_price, lot, float(sl_price), float(tp_price), comment)
        if not res:
            return None
        logger.info("Live order placed: ticket=%s symbol=%s lot=%s price=%s", res["ticket"], symbol, lot, res["price"])
        return {**res, "symbol": symbol, "direction": direction, "comment": comment}

    def place_twin_market_orders(
        self,
        symbol: str,
        direction: str,
        sl_price: float,
        tp1_price: float,
        tp2_price: float,
        risk_mult: float = 1.0,
        comment: str = "MGL",
    ) -> Optional[Dict[str, Any]]:
        """Place two risk-budgeted legs when both can meet broker minimum volume."""
        if not self.ensure_connected():
            return None
        sym_info, tick = self._prepare_symbol_and_tick(symbol)
        if not sym_info or not tick:
            return None
        side = str(direction).upper()
        if side not in ("LONG", "BUY", "SHORT", "SELL"):
            logger.error("Unsupported order direction %r.", direction)
            return None
        is_buy = side in ("LONG", "BUY")
        order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        entry_price = float(tick.ask if is_buy else tick.bid)
        if not self._valid_order_geometry(side, entry_price, sl_price, tp2_price):
            return None
        if not self._valid_order_geometry(side, entry_price, sl_price, tp1_price):
            logger.error("Twin order rejected: TP1 geometry is invalid.")
            return None

        total_lot = self.calculate_lot_size(symbol, entry_price, sl_price, risk_mult)
        if total_lot is None:
            logger.warning("Twin order rejected before submission: safe lot sizing failed for %s.", symbol)
            return None
        step = float(getattr(sym_info, "volume_step", 0.0) or 0.0)
        min_lot = float(getattr(sym_info, "volume_min", 0.0) or 0.0)
        if step <= 0 or min_lot <= 0:
            logger.error("Invalid broker volume step/minimum for %s.", symbol)
            return None
        if not self._check_margin(symbol, order_type, total_lot, entry_price):
            return None

        # If both legs cannot independently meet minimum volume, place one order
        # with the total risk-approved volume. Never increase volume to force a split.
        if total_lot < (2.0 * min_lot - 1e-12):
            single = self._send_single_deal(symbol, order_type, entry_price, total_lot, float(sl_price), float(tp2_price), f"{comment}_SINGLE")
            if not single:
                return None
            return {
                "leg1_ticket": single["ticket"], "leg1_vol": total_lot, "leg1_tp": tp2_price,
                "leg2_ticket": None, "leg2_vol": 0.0, "leg2_tp": tp2_price,
                "entry_price": single["price"], "sl_price": sl_price, "total_lot": total_lot,
                "is_split": False,
            }

        leg1_lot = self._floor_to_step(total_lot / 2.0, step)
        leg2_lot = self._floor_to_step(total_lot - leg1_lot, step)
        if leg1_lot < min_lot or leg2_lot < min_lot or leg1_lot + leg2_lot > total_lot + 1e-10:
            logger.error("Safe twin split could not satisfy broker constraints for %s.", symbol)
            return None

        if not self._check_margin(symbol, order_type, leg1_lot, entry_price):
            return None
        res1 = self._send_single_deal(symbol, order_type, entry_price, leg1_lot, float(sl_price), float(tp1_price), f"{comment}_L1")
        if not res1:
            logger.error("TP1 leg failed for %s.", symbol)
            return None

        tick2 = mt5.symbol_info_tick(symbol)
        if not tick2:
            logger.error("Could not refresh tick for TP2 leg %s; first leg remains open.", symbol)
            return {
                "leg1_ticket": res1["ticket"], "leg1_vol": leg1_lot, "leg1_tp": tp1_price,
                "leg2_ticket": None, "leg2_vol": 0.0, "leg2_tp": tp2_price,
                "entry_price": res1["price"], "sl_price": sl_price,
                "total_lot": leg1_lot, "is_split": False, "partial_execution": True,
            }
        entry2 = float(tick2.ask if is_buy else tick2.bid)
        if not self._valid_order_geometry(side, entry2, sl_price, tp2_price):
            logger.error("Price moved past valid TP2/SL geometry after TP1 leg; second leg not sent.")
            return {
                "leg1_ticket": res1["ticket"], "leg1_vol": leg1_lot, "leg1_tp": tp1_price,
                "leg2_ticket": None, "leg2_vol": 0.0, "leg2_tp": tp2_price,
                "entry_price": res1["price"], "sl_price": sl_price,
                "total_lot": leg1_lot, "is_split": False, "partial_execution": True,
            }
        if not self._check_margin(symbol, order_type, leg2_lot, entry2):
            logger.error("TP2 leg margin check failed after TP1 leg; first leg remains open.")
            return {
                "leg1_ticket": res1["ticket"], "leg1_vol": leg1_lot, "leg1_tp": tp1_price,
                "leg2_ticket": None, "leg2_vol": 0.0, "leg2_tp": tp2_price,
                "entry_price": res1["price"], "sl_price": sl_price,
                "total_lot": leg1_lot, "is_split": False, "partial_execution": True,
            }
        res2 = self._send_single_deal(symbol, order_type, entry2, leg2_lot, float(sl_price), float(tp2_price), f"{comment}_L2")
        if not res2:
            logger.error("TP2 leg failed after TP1 leg opened for %s.", symbol)
            return {
                "leg1_ticket": res1["ticket"], "leg1_vol": leg1_lot, "leg1_tp": tp1_price,
                "leg2_ticket": None, "leg2_vol": 0.0, "leg2_tp": tp2_price,
                "entry_price": res1["price"], "sl_price": sl_price,
                "total_lot": leg1_lot, "is_split": False, "partial_execution": True,
            }

        return {
            "leg1_ticket": res1["ticket"], "leg1_vol": leg1_lot, "leg1_tp": tp1_price,
            "leg2_ticket": res2["ticket"], "leg2_vol": leg2_lot, "leg2_tp": tp2_price,
            "entry_price": res1["price"], "sl_price": sl_price,
            "total_lot": leg1_lot + leg2_lot, "is_split": True,
        }

    def _find_filled_position(self, ticket: int, symbol: str, volume: float, order_type: int):
        """Find the filled position without guessing across unrelated positions."""
        positions = mt5.positions_get(ticket=int(ticket))
        if positions:
            return positions[0]
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return None
        expected_type = mt5.POSITION_TYPE_BUY if order_type == mt5.ORDER_TYPE_BUY else mt5.POSITION_TYPE_SELL
        sym_info = mt5.symbol_info(symbol)
        step = float(getattr(sym_info, "volume_step", 0.0) or 0.0) if sym_info else 0.0
        tolerance = max(1e-8, step / 2.0)
        candidates = []
        for pos in positions:
            try:
                if int(getattr(pos, "magic", -1)) != self.magic:
                    continue
                if int(getattr(pos, "type", -1)) != int(expected_type):
                    continue
                if float(getattr(pos, "volume", 0.0)) + tolerance < float(volume):
                    continue
                candidates.append(pos)
            except (TypeError, ValueError):
                continue
        return candidates[0] if len(candidates) == 1 else None

    def _verify_or_restore_stop_loss(self, ticket: int, symbol: str, volume: float, order_type: int, expected_sl: float) -> bool:
        """Verify broker-side SL after fill; repair it or attempt a position-specific emergency close."""
        expected_sl = float(expected_sl)
        sym_info = mt5.symbol_info(symbol)
        point = float(getattr(sym_info, "point", 0.0) or 0.0) if sym_info else 0.0
        tolerance = max(point * 0.5, 1e-8)
        position = None
        for _ in range(5):
            position = self._find_filled_position(ticket, symbol, volume, order_type)
            if position is not None:
                break
            time.sleep(0.2)

        if position is None:
            logger.critical(
                "STOP-LOSS SAFETY FAILURE: order %s for %s was filled but its position cannot be located. "
                "Attempting ticket-specific emergency close; check MT5 immediately.",
                ticket, symbol,
            )
            tick = mt5.symbol_info_tick(symbol)
            if tick:
                close_is_sell = order_type == mt5.ORDER_TYPE_BUY
                close_request = {
                    "action": mt5.TRADE_ACTION_DEAL, "position": int(ticket), "symbol": symbol,
                    "volume": float(volume),
                    "type": mt5.ORDER_TYPE_SELL if close_is_sell else mt5.ORDER_TYPE_BUY,
                    "price": float(tick.bid if close_is_sell else tick.ask), "deviation": 20,
                    "magic": self.magic, "comment": "MGL_NO_SL_EMERGENCY_CLOSE",
                    "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
                }
                try:
                    close_result = mt5.order_send(close_request)
                    if close_result is None or close_result.retcode != mt5.TRADE_RETCODE_DONE:
                        close_request["type_filling"] = mt5.ORDER_FILLING_FOK
                        close_result = mt5.order_send(close_request)
                    if close_result is None or close_result.retcode != mt5.TRADE_RETCODE_DONE:
                        logger.critical("EMERGENCY CLOSE FAILED for unverified order %s: %s. MANUAL ACTION REQUIRED NOW.", ticket, close_result.comment if close_result else mt5.last_error())
                    else:
                        logger.critical("Emergency close executed for unverified order %s; verify terminal.", ticket)
                except Exception:
                    logger.exception("EMERGENCY CLOSE ERROR for order %s. MANUAL ACTION REQUIRED NOW.", ticket)
            else:
                logger.critical("No quote for emergency close of order %s. MANUAL ACTION REQUIRED NOW.", ticket)
            return False

        current_sl = float(getattr(position, "sl", 0.0) or 0.0)
        if current_sl > 0 and abs(current_sl - expected_sl) <= tolerance:
            return True

        position_ticket = int(getattr(position, "ticket", ticket))
        current_tp = float(getattr(position, "tp", 0.0) or 0.0)
        logger.error("Filled position %s on %s has missing/wrong SL (actual=%s expected=%s); attempting repair.", position_ticket, symbol, current_sl, expected_sl)
        repair_request = {"action": mt5.TRADE_ACTION_SLTP, "position": position_ticket, "symbol": symbol, "sl": expected_sl, "tp": current_tp}
        try:
            repair_result = mt5.order_send(repair_request)
        except Exception as exc:
            repair_result = None
            logger.error("SL repair request raised for position %s: %s", position_ticket, exc)

        if repair_result is not None and repair_result.retcode == mt5.TRADE_RETCODE_DONE:
            time.sleep(0.1)
            refreshed = mt5.positions_get(ticket=position_ticket)
            if refreshed:
                repaired_sl = float(getattr(refreshed[0], "sl", 0.0) or 0.0)
                if repaired_sl > 0 and abs(repaired_sl - expected_sl) <= tolerance:
                    logger.warning("Stop-loss restored and verified for position %s on %s.", position_ticket, symbol)
                    return True

        logger.critical("SL REPAIR FAILED for position %s on %s. Attempting emergency close; verify terminal immediately.", position_ticket, symbol)
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            pos_type = int(getattr(position, "type", -1))
            close_is_sell = pos_type == int(mt5.POSITION_TYPE_BUY)
            close_request = {
                "action": mt5.TRADE_ACTION_DEAL, "position": position_ticket, "symbol": symbol,
                "volume": float(getattr(position, "volume", volume)),
                "type": mt5.ORDER_TYPE_SELL if close_is_sell else mt5.ORDER_TYPE_BUY,
                "price": float(tick.bid if close_is_sell else tick.ask), "deviation": 20,
                "magic": self.magic, "comment": "MGL_NO_SL_EMERGENCY_CLOSE",
                "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
            }
            try:
                close_result = mt5.order_send(close_request)
                if close_result is None or close_result.retcode != mt5.TRADE_RETCODE_DONE:
                    close_request["type_filling"] = mt5.ORDER_FILLING_FOK
                    close_result = mt5.order_send(close_request)
                if close_result is not None and close_result.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.critical("Unprotected position %s emergency-close request executed.", position_ticket)
                else:
                    logger.critical("EMERGENCY CLOSE FAILED for unprotected position %s: %s. MANUAL ACTION REQUIRED NOW.", position_ticket, close_result.comment if close_result else mt5.last_error())
            except Exception:
                logger.exception("EMERGENCY CLOSE ERROR for unprotected position %s. MANUAL ACTION REQUIRED NOW.", position_ticket)
        else:
            logger.critical("Cannot fetch quote to emergency-close unprotected position %s. MANUAL ACTION REQUIRED NOW.", position_ticket)
        return False

    def _send_single_deal(self, symbol: str, order_type: int, price: float, volume: float, sl: float, tp: float, comment: str) -> Optional[Dict[str, Any]]:
        if mt5 is None or not math.isfinite(float(volume)) or float(volume) <= 0:
            return None
        request = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(volume),
            "type": order_type, "price": float(price), "sl": float(sl), "tp": float(tp),
            "deviation": 20, "magic": self.magic, "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = None
        for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK):
            request["type_filling"] = filling
            try:
                check = mt5.order_check(request)
            except Exception as exc:
                logger.error("Order pre-check raised for %s: %s; refusing to send.", symbol, exc)
                return None
            if check is None or getattr(check, "retcode", None) != 0:
                logger.error("Order pre-check rejected %s (fill=%s): %s; refusing this attempt.", symbol, filling, getattr(check, "comment", mt5.last_error()) if check is not None else mt5.last_error())
                continue
            try:
                result = mt5.order_send(request)
            except Exception as exc:
                logger.error("MT5 order_send raised for %s: %s", symbol, exc)
                result = None
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                break
            logger.warning("MT5 order send failed for %s (fill=%s): %s", symbol, filling, result.comment if result is not None else mt5.last_error())
            result = None
        if result is None:
            return None
        if not self._verify_or_restore_stop_loss(int(result.order), symbol, float(volume), order_type, float(sl)):
            logger.critical("Order %s on %s is NOT confirmed protected. Inspect MT5 positions and logs immediately.", result.order, symbol)
            return None
        logger.info("Live order placed with verified SL: ticket=%s symbol=%s lot=%s price=%s SL=%s", result.order, symbol, volume, result.price, sl)
        return {"ticket": result.order, "deal": result.deal, "volume": float(volume), "price": result.price, "sl_verified": True}

    def modify_position_sl(self, ticket: int, symbol: str, new_sl: float, current_tp: float = 0.0) -> bool:
        if not self.ensure_connected():
            return False
        if not math.isfinite(float(new_sl)) or float(new_sl) <= 0:
            logger.error("Refusing to modify SL for %s #%s to non-positive value: %s", symbol, ticket, new_sl)
            return False
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": int(ticket),
            "symbol": symbol,
            "sl": float(new_sl),
            "tp": float(current_tp) if current_tp > 0 else 0.0,
        }
        res = mt5.order_send(req)
        if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info("Position SL updated: ticket=%s symbol=%s SL=%s", ticket, symbol, new_sl)
            return True
        logger.warning("Position SL update failed #%s: %s", ticket, res.comment if res else mt5.last_error())
        return False

    def get_open_position(self, ticket: int) -> Optional[Any]:
        if not self.ensure_connected():
            return None
        positions = mt5.positions_get(ticket=int(ticket))
        return positions[0] if positions else None

    def get_position_close_deal(self, position_id: int) -> Optional[Dict[str, Any]]:
        if not self.ensure_connected():
            return None
        deals = mt5.history_deals_get(position=int(position_id))
        if deals:
            # MT5 exit deals commonly have entry == 1.
            for deal in reversed(deals):
                if deal.entry == 1:
                    return {
                        "ticket": deal.ticket,
                        "order": deal.order,
                        "profit": deal.profit,
                        "price": deal.price,
                        "comment": deal.comment,
                        "time": deal.time,
                    }
        return None
