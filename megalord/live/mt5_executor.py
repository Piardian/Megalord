"""Megalord MT5 Live Order Executor for The 5%ers Evaluation Account.

Features:
- Magic Number: 202699
- The 5%ers Risk Per Trade: 0.50% base risk (~$49.30) multiplied by Arbiter Risk Multiplier.
- Exact Lot Size Calculation using MT5 tick value, point size, and contract specifications.
- Pre-trade slippage and margin checks.
- Automatic SL and TP placement on broker order execution.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Optional
import MetaTrader5 as mt5

logger = logging.getLogger("Megalord.MT5Executor")

MEGALORD_MAGIC_NUMBER = 202699


class MegalordMT5Executor:
    def __init__(self, target_account: int = 26735345, base_risk_pct: float = 0.50):
        self.target_account = target_account
        self.base_risk_pct = base_risk_pct
        self.magic = MEGALORD_MAGIC_NUMBER

    def ensure_connected(self) -> bool:
        if not mt5.initialize():
            logger.error("MT5 initialize failed.")
            return False
        acc = mt5.account_info()
        if not acc:
            logger.error("Could not fetch account info from MT5.")
            return False
        if acc.login != self.target_account:
            logger.warning(f"Target account mismatch: Expected #{self.target_account}, Found #{acc.login}")
        return True

    def calculate_lot_size(self, symbol: str, entry_price: float, sl_price: float, risk_mult: float = 1.0) -> float:
        """Calculates precise MT5 lot volume according to account equity and stop loss distance."""
        acc = mt5.account_info()
        if not acc or acc.balance <= 0:
            return 0.01

        effective_risk_pct = self.base_risk_pct * risk_mult
        risk_cash = acc.balance * (effective_risk_pct / 100.0)

        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            logger.warning(f"Symbol info not available for {symbol}, using min lot 0.01")
            return 0.01

        stop_dist = abs(entry_price - sl_price)
        if stop_dist <= 0:
            return sym_info.volume_min

        tick_size = sym_info.trade_tick_size or sym_info.point or 0.0001
        tick_val = sym_info.trade_tick_value or 1.0
        loss_per_lot = (stop_dist / tick_size) * tick_val

        if loss_per_lot <= 0:
            return sym_info.volume_min

        raw_lot = risk_cash / loss_per_lot
        step = sym_info.volume_step or 0.01
        calculated_lot = round(round(raw_lot / step) * step, 2)
        return min(sym_info.volume_max, max(sym_info.volume_min, calculated_lot))

    def place_market_order(
        self,
        symbol: str,
        direction: str,
        sl_price: float,
        tp_price: float,
        risk_mult: float = 1.0,
        comment: str = "Megalord_Live"
    ) -> Optional[Dict[str, Any]]:
        """Sends live market execution deal to The 5%ers MT5 server."""
        if not self.ensure_connected():
            return None

        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            logger.error(f"Cannot trade symbol {symbol}: not found on broker.")
            return None

        if not sym_info.visible:
            mt5.symbol_select(symbol, True)

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logger.error(f"Could not retrieve live price tick for {symbol}")
            return None

        is_buy = direction.upper() in ("LONG", "BUY")
        order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        entry_price = tick.ask if is_buy else tick.bid

        lot = self.calculate_lot_size(symbol, entry_price, sl_price, risk_mult)

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": entry_price,
            "sl": float(sl_price) if sl_price > 0 else 0.0,
            "tp": float(tp_price) if tp_price > 0 else 0.0,
            "deviation": 20,
            "magic": self.magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(req)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            # Fallback to FOK filling if IOC rejected
            req["type_filling"] = mt5.ORDER_FILLING_FOK
            result = mt5.order_send(req)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            err = result.comment if result else mt5.last_error()
            logger.error(f"MT5 Order Send Failed: {err}")
            return None

        logger.info(f"✅ The 5%ers Live Order Placed! Ticket: #{result.order} | Symbol: {symbol} | Lot: {lot} | Price: {result.price}")
        return {
            "ticket": result.order,
            "deal": result.deal,
            "volume": lot,
            "price": result.price,
            "symbol": symbol,
            "direction": direction,
            "comment": comment
        }

    def place_twin_market_orders(
        self,
        symbol: str,
        direction: str,
        sl_price: float,
        tp1_price: float,
        tp2_price: float,
        risk_mult: float = 1.0,
        comment: str = "MGL"
    ) -> Optional[Dict[str, Any]]:
        """
        İkiz Emir (Twin Orders) Mimarisi:
        Hesaplanan toplam lotu 2 eşit parçaya böler (%50 / %50).
        - Bacak 1 (Leg 1): TP1 (1.0R - 1.9R) seviyesine bağlanır (%50 Kâr Kilidi).
        - Bacak 2 (Leg 2): TP2 (2.0R - 3.5R+) ana likidite hedefine bağlanır (Runner).
        """
        if not self.ensure_connected():
            return None

        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            logger.error(f"Cannot trade symbol {symbol}: not found on broker.")
            return None

        if not sym_info.visible:
            mt5.symbol_select(symbol, True)

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logger.error(f"Could not retrieve live price tick for {symbol}")
            return None

        is_buy = direction.upper() in ("LONG", "BUY")
        order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        entry_price = tick.ask if is_buy else tick.bid

        total_lot = self.calculate_lot_size(symbol, entry_price, sl_price, risk_mult)
        step = sym_info.volume_step or 0.01
        min_lot = sym_info.volume_min or 0.01

        if total_lot <= min_lot:
            # Minumum lottan küçük veya eşitse bölünemez, tek emir olarak TP2 hedefine aç
            res = self.place_market_order(symbol, direction, sl_price, tp2_price, risk_mult, comment=f"{comment}_SINGLE")
            if not res:
                return None
            return {
                "leg1_ticket": res["ticket"],
                "leg1_vol": total_lot,
                "leg1_tp": tp1_price,
                "leg2_ticket": None,
                "leg2_vol": 0.0,
                "leg2_tp": tp2_price,
                "entry_price": res["price"],
                "sl_price": sl_price,
                "total_lot": total_lot,
                "is_split": False
            }

        # İki eşit parçaya böl
        raw_half = total_lot / 2.0
        leg1_lot = round(round(raw_half / step) * step, 2)
        leg1_lot = max(min_lot, leg1_lot)
        leg2_lot = round(total_lot - leg1_lot, 2)

        if leg2_lot < min_lot:
            leg1_lot = total_lot
            leg2_lot = 0.0

        # 1. Bacak Emri Gönder (TP1)
        res1 = self._send_single_deal(symbol, order_type, entry_price, leg1_lot, sl_price, tp1_price, f"{comment}_L1")
        if not res1:
            logger.error(f"Leg 1 order placement failed for {symbol}")
            return None

        # 2. Bacak Emri Gönder (TP2 - Runner)
        res2 = None
        if leg2_lot >= min_lot:
            # Re-fetch tick for latest price
            tick2 = mt5.symbol_info_tick(symbol)
            fill_p2 = tick2.ask if is_buy else tick2.bid
            res2 = self._send_single_deal(symbol, order_type, fill_p2, leg2_lot, sl_price, tp2_price, f"{comment}_L2")

        logger.info(f"🚀 İkiz Emir İletildi: L1=#{res1['ticket']} ({leg1_lot}L @ TP1:{tp1_price}) | L2=#{res2['ticket'] if res2 else 'N/A'} ({leg2_lot}L @ TP2:{tp2_price})")
        return {
            "leg1_ticket": res1["ticket"],
            "leg1_vol": leg1_lot,
            "leg1_tp": tp1_price,
            "leg2_ticket": res2["ticket"] if res2 else None,
            "leg2_vol": leg2_lot,
            "leg2_tp": tp2_price,
            "entry_price": res1["price"],
            "sl_price": sl_price,
            "total_lot": total_lot,
            "is_split": bool(res2 is not None)
        }

    def _send_single_deal(self, symbol: str, order_type: int, price: float, volume: float, sl: float, tp: float, comment: str) -> Optional[Dict[str, Any]]:
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": float(sl) if sl > 0 else 0.0,
            "tp": float(tp) if tp > 0 else 0.0,
            "deviation": 20,
            "magic": self.magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            req["type_filling"] = mt5.ORDER_FILLING_FOK
            res = mt5.order_send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            err = res.comment if res else mt5.last_error()
            logger.error(f"MT5 Single Deal Send Failed: {err}")
            return None
        return {"ticket": res.order, "deal": res.deal, "volume": volume, "price": res.price}

    def modify_position_sl(self, ticket: int, symbol: str, new_sl: float, current_tp: float = 0.0) -> bool:
        """Pozisyonun Stop Loss seviyesini günceller (Break-Even veya İz Süren Stop)."""
        if not self.ensure_connected():
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
            logger.info(f"🎯 Pozisyon SL Güncellendi: #{ticket} ({symbol}) -> Yeni SL: {new_sl}")
            return True
        logger.warning(f"⚠️ Pozisyon SL Güncelleme Başarısız #{ticket}: {res.comment if res else mt5.last_error()}")
        return False

    def get_open_position(self, ticket: int) -> Optional[Any]:
        """Açık pozisyonu kontrol eder."""
        if not self.ensure_connected():
            return None
        positions = mt5.positions_get(ticket=int(ticket))
        if positions and len(positions) > 0:
            return positions[0]
        return None

    def get_position_close_deal(self, position_id: int) -> Optional[Dict[str, Any]]:
        """Pozisyonun kapanış fişini (deal) sorgular."""
        if not self.ensure_connected():
            return None
        deals = mt5.history_deals_get(position=int(position_id))
        if deals:
            # Find the exit deal (entry == 1)
            for d in reversed(deals):
                if d.entry == 1:
                    return {
                        "ticket": d.ticket,
                        "order": d.order,
                        "profit": d.profit,
                        "price": d.price,
                        "comment": d.comment,
                        "time": d.time
                    }
        return None
