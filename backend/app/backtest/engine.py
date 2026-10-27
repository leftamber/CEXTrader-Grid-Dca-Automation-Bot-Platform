import math

from ..core.models import GridConfig, DcaConfig, MomentumConfig, VwapConfig, BacktestRequest
from ..core.utils import compute_levels, ema, rsi, dca_safety_deviations, rolling_vwap, atr


class SimResult:
    def __init__(self):
        self.trades = []
        self.equity = []
        self.stats = {}

    def equity_at(self, ts, value):
        if not self.equity or self.equity[-1][0] != ts:
            self.equity.append([ts, value])


class GridSim:
    def __init__(self, cfg: GridConfig, maker_fee: float, taker_fee: float, mid: float = 0.0):
        self.cfg = cfg
        self.maker_fee = maker_fee / 100.0
        self.taker_fee = taker_fee / 100.0
        if cfg.lower_price <= 0 or cfg.upper_price <= 0 or cfg.lower_price >= cfg.upper_price:
            base = mid or 100.0
            span = base * 0.08
            cfg.lower_price = base - span
            cfg.upper_price = base + span
        self.levels = compute_levels(cfg.lower_price, cfg.upper_price, cfg.levels, cfg.grid_type)
        self.level_state = {i: {"buy": False, "sell": False} for i in range(len(self.levels))}
        self.qty_per_level = self._qty()
        self.quote = cfg.total_budget_quote
        self.base = 0.0
        self.realized = 0.0
        self.fees = 0.0
        self.roundtrips = 0
        self.wins = 0
        self.lots = {}
        self.equity = []

    def _qty(self):
        c = self.cfg
        if c.per_level_mode == "fixed_qty":
            return c.per_level_qty
        q = c.per_level_quote if c.per_level_mode == "fixed_quote" else c.total_budget_quote / c.levels
        return q

    def run(self, candles: list, res: SimResult):
        last_price = candles[-1][4] if candles else 0
        for candle in candles:
            ts, o, h, l, cl, v = candle
            self._step(ts, o, h, l, cl, res)
        inv_value = self.base * last_price
        return {"final_equity": self.quote + inv_value, "realized_pnl": self.realized,
                "fees": self.fees, "roundtrips": self.roundtrips, "wins": self.wins,
                "inventory_base": self.base}

    def _step(self, ts, o, h, l, cl, res: SimResult):
        up_moves = cl >= o
        for i, price in enumerate(self.levels):
            st = self.level_state[i]
            q = self.qty_per_level / price
            if not st["buy"] and price < o:
                if l <= price:
                    cost = price * q
                    fee = cost * self.maker_fee
                    if self.quote >= cost + fee and self._inventory_ok(q):
                        self.quote -= cost + fee
                        self.base += q
                        self.fees += fee
                        st["buy"] = True
                        self.lots[i] = q
                        res.trades.append({"ts": ts, "side": "buy", "price": price, "qty": q,
                                           "level": i, "fee": fee, "role": "grid_buy"})
            if st["buy"] and not st["sell"] and i + 1 < len(self.levels):
                sp = self.levels[i + 1]
                if h >= sp:
                    q = self.lots.get(i, self.qty_per_level / sp)
                    proceeds = sp * q
                    fee = proceeds * self.maker_fee
                    pnl = proceeds - price * q - fee - price * q * self.maker_fee
                    self.quote += proceeds - fee
                    self.base -= q
                    self.fees += fee
                    self.realized += pnl
                    self.roundtrips += 1
                    if pnl > 0:
                        self.wins += 1
                    st["sell"] = True
                    st["buy"] = False
                    self.lots.pop(i, None)
                    res.trades.append({"ts": ts, "side": "sell", "price": sp, "qty": q,
                                       "level": i + 1, "fee": fee, "role": "grid_sell",
                                       "pnl": pnl})
            if up_moves and st["buy"] and h >= price and price >= o and False:
                pass
        res.equity_at(ts, self.quote + self.base * cl)

    def _inventory_ok(self, q):
        if self.cfg.max_inventory_base and self.base + q > self.cfg.max_inventory_base:
            return False
        return True


class DcaSim:
    def __init__(self, cfg: DcaConfig, maker_fee: float, taker_fee: float):
        self.cfg = cfg
        self.maker_fee = maker_fee / 100.0
        self.taker_fee = taker_fee / 100.0
        self.quote = cfg.total_budget_quote
        self.realized = 0.0
        self.fees = 0.0
        self.cycles = []
        self.wins = 0
        self.closes = []

    def base_order_quote(self):
        return self.cfg.base_order_quote or self.cfg.total_budget_quote * self.cfg.base_order_pct / 100.0

    def run(self, candles: list, res: SimResult):
        devs = dca_safety_deviations(self.cfg.safety_deviation_pct, self.cfg.max_safety_orders,
                                     self.cfg.safety_step_scale)
        trend_on = self.cfg.entry_condition in ("ema", "rsi")
        closes_all = [c[4] for c in candles]
        ema_series = ema(closes_all, self.cfg.ema_period) if trend_on and self.cfg.entry_condition == "ema" else None
        rsi_on = trend_on and self.cfg.entry_condition == "rsi"
        cycle = None
        cooldown_until = 0
        for idx, (ts, o, h, l, cl, v) in enumerate(candles):
            if trend_on and (ema_series or rsi_on) and not cycle:
                ok = True
                if rsi_on:
                    ok = rsi(closes_all[:idx + 1], self.cfg.rsi_period) < self.cfg.rsi_max
                elif ema_series and idx < len(ema_series):
                    e = ema_series[idx]
                    ok = cl > e if not self.cfg.short_mode else cl < e
                if not ok:
                    res.equity_at(ts, self.quote)
                    continue
            if not cycle:
                if ts < cooldown_until or self.quote < self.base_order_quote():
                    res.equity_at(ts, self.quote)
                    continue
                price = cl
                qty = self.base_order_quote() / price
                fee = qty * price * self.taker_fee
                self.quote -= fee
                self.fees += fee
                cycle = {"opened": ts, "entries": [{"price": price, "qty": qty, "fee": fee}],
                         "base_price": price, "safety_count": 0, "ts": ts}
                res.trades.append({"ts": ts, "side": "sell" if self.cfg.short_mode else "buy",
                                   "price": price, "qty": qty, "fee": fee, "role": "dca_base"})
            else:
                entries = cycle["entries"]
                total_qty = sum(e["qty"] for e in entries)
                avg = sum(e["price"] * e["qty"] for e in entries) / total_qty
                n = cycle["safety_count"]
                if n < len(devs) and n < self.cfg.max_safety_orders:
                    trigger = cycle["base_price"] * (1 - devs[n] / 100.0) if not self.cfg.short_mode \
                        else cycle["base_price"] * (1 + devs[n] / 100.0)
                    if (l <= trigger and not self.cfg.short_mode) or (h >= trigger and self.cfg.short_mode):
                        sq = (self.cfg.safety_order_quote or self.base_order_quote()) * (self.cfg.volume_scale ** n)
                        price = trigger
                        qty = sq / price
                        fee = qty * price * self.taker_fee
                        self.quote -= fee
                        self.fees += fee
                        entries.append({"price": price, "qty": qty, "fee": fee})
                        cycle["safety_count"] += 1
                        res.trades.append({"ts": ts, "side": "buy" if not self.cfg.short_mode else "sell",
                                           "price": price, "qty": qty, "fee": fee, "role": "dca_safety"})
                        total_qty = sum(e["qty"] for e in entries)
                        avg = sum(e["price"] * e["qty"] for e in entries) / total_qty
                tp = avg * (1 + self.cfg.take_profit_pct / 100.0) if not self.cfg.short_mode \
                    else avg * (1 - self.cfg.take_profit_pct / 100.0)
                sl = avg * (1 - self.cfg.stop_loss_pct / 100.0) if self.cfg.stop_loss_pct and not self.cfg.short_mode \
                    else (avg * (1 + self.cfg.stop_loss_pct / 100.0) if self.cfg.stop_loss_pct else 0)
                hit_tp = h >= tp if not self.cfg.short_mode else l <= tp
                hit_sl = bool(sl) and ((l <= sl and not self.cfg.short_mode) or (h >= sl and self.cfg.short_mode))
                close_price = None
                reason = ""
                if hit_tp:
                    close_price, reason = tp, "tp"
                elif hit_sl:
                    close_price, reason = sl, "sl"
                if close_price:
                    pnl = (close_price - avg) * total_qty * (1 if not self.cfg.short_mode else -1)
                    fee = close_price * total_qty * self.taker_fee
                    pnl -= fee
                    self.quote += pnl
                    self.fees += fee
                    self.realized += pnl
                    self.wins += 1 if pnl > 0 else 0
                    res.trades.append({"ts": ts, "side": "buy" if self.cfg.short_mode else "sell",
                                       "price": close_price, "qty": total_qty, "fee": fee,
                                       "role": "dca_close", "pnl": pnl, "avg_entry": avg})
                    self.closes.append({"ts": ts, "pnl": pnl, "reason": reason})
                    cycle = None
                    cooldown_until = ts + self.cfg.cooldown_minutes * 60000
            total_qty_now = sum(e["qty"] for e in cycle["entries"]) if cycle else 0
            unreal = 0
            if cycle:
                avg = sum(e["price"] * e["qty"] for e in cycle["entries"]) / total_qty_now
                unreal = (cl - avg) * total_qty_now * (1 if not self.cfg.short_mode else -1)
            res.equity_at(ts, self.quote + unreal)
        return {"final_equity": self.quote, "realized_pnl": self.realized, "fees": self.fees,
                "cycles": self.wins, "closes": self.closes}


class MomentumSim:
    def __init__(self, cfg: MomentumConfig, maker_fee: float, taker_fee: float):
        self.cfg = cfg
        self.taker_fee = taker_fee / 100.0
        self.quote = cfg.total_budget_quote
        self.entry_price = 0.0
        self.entry_qty = 0.0
        self.direction = "long"
        self.peak = 0.0
        self.realized = 0.0
        self.fees = 0.0
        self.wins = 0
        self.losses = 0
        self.cooldown_until = 0

    def _signal(self, closes: list, fast_ema: list, slow_ema: list, i: int) -> str:
        if i < 1:
            return ""
        f1, f2, s1, s2 = fast_ema[i - 1], fast_ema[i], slow_ema[i - 1], slow_ema[i]
        if f2 <= s2 and f1 > s1:
            return "cross_up"
        if f2 >= s2 and f1 < s1:
            return "cross_down"
        return ""

    def run(self, candles: list, res: SimResult):
        closes = [c[4] for c in candles]
        fast = ema(closes, self.cfg.fast_ema)
        slow = ema(closes, self.cfg.slow_ema)
        need = max(self.cfg.fast_ema, self.cfg.slow_ema) + 2
        for i, (ts, o, h, l, cl, v) in enumerate(candles):
            if self.entry_qty > 0:
                hit = ""
                if self.direction == "long":
                    self.peak = max(self.peak, h)
                    if l <= self.entry_price * (1 - self.cfg.stop_loss_pct / 100.0):
                        hit = "sl"
                    elif self.cfg.trailing_enabled:
                        if self.peak >= self.entry_price * (1 + self.cfg.take_profit_pct / 100.0) \
                                and cl <= self.peak * (1 - self.cfg.trailing_dev_pct / 100.0):
                            hit = "trail"
                    elif h >= self.entry_price * (1 + self.cfg.take_profit_pct / 100.0):
                        hit = "tp"
                else:
                    self.peak = min(self.peak, l)
                    if h >= self.entry_price * (1 + self.cfg.stop_loss_pct / 100.0):
                        hit = "sl"
                    elif self.cfg.trailing_enabled:
                        if self.peak <= self.entry_price * (1 - self.cfg.take_profit_pct / 100.0) \
                                and cl >= self.peak * (1 + self.cfg.trailing_dev_pct / 100.0):
                            hit = "trail"
                    elif l <= self.entry_price * (1 - self.cfg.take_profit_pct / 100.0):
                        hit = "tp"
                if not hit and self.cfg.exit_on_cross and i + 1 < len(candles):
                    sig = self._signal(closes, fast, slow, i)
                    if (self.direction == "long" and sig == "cross_down") or \
                       (self.direction == "short" and sig == "cross_up"):
                        hit = "cross"
                if hit:
                    px = self.entry_price * (1 - self.cfg.stop_loss_pct / 100.0) if hit == "sl" and self.direction == "long" else \
                        self.entry_price * (1 + self.cfg.stop_loss_pct / 100.0) if hit == "sl" else cl
                    fee = px * self.entry_qty * self.taker_fee
                    pnl = (px - self.entry_price) * self.entry_qty * (1 if self.direction == "long" else -1) - fee
                    self.quote += pnl
                    self.realized += pnl
                    self.fees += fee
                    if pnl > 0:
                        self.wins += 1
                    else:
                        self.losses += 1
                    res.trades.append({"ts": ts, "side": "sell" if self.direction == "long" else "buy",
                                       "price": px, "qty": self.entry_qty, "fee": fee,
                                       "role": "momentum_exit", "pnl": pnl})
                    self.entry_qty = 0.0
                    self.cooldown_until = ts + self.cfg.cooldown_minutes * 60000
                unreal = (cl - self.entry_price) * self.entry_qty * (1 if self.direction == "long" else -1)
                res.equity_at(ts, self.quote + unreal)
                continue
            if ts < self.cooldown_until or i < need:
                res.equity_at(ts, self.quote)
                continue
            sig = self._signal(closes, fast, slow, i)
            direction = "long" if sig == "cross_up" else ("short" if sig == "cross_down" and self.cfg.allow_short else "")
            if not direction:
                res.equity_at(ts, self.quote)
                continue
            if self.cfg.volume_filter_enabled and i > self.cfg.volume_ma_period:
                vols = [candles[j][5] or 0 for j in range(i - self.cfg.volume_ma_period, i)]
                ma = sum(vols) / len(vols)
                if v < ma * self.cfg.volume_factor:
                    res.equity_at(ts, self.quote)
                    continue
            if self.cfg.rsi_filter_enabled:
                r = rsi(closes[:i + 1], self.cfg.rsi_period)
                if direction == "long" and r > self.cfg.rsi_long_max:
                    res.equity_at(ts, self.quote)
                    continue
                if direction == "short" and r < self.cfg.rsi_short_min:
                    res.equity_at(ts, self.quote)
                    continue
            qty = (self.cfg.total_budget_quote * self.cfg.position_pct / 100.0) / cl
            fee = qty * cl * self.taker_fee
            self.quote -= fee
            self.fees += fee
            self.entry_price = cl
            self.entry_qty = qty
            self.direction = direction
            self.peak = cl
            res.trades.append({"ts": ts, "side": "buy" if direction == "long" else "sell",
                               "price": cl, "qty": qty, "fee": fee, "role": "momentum_entry"})
            res.equity_at(ts, self.quote)
        final = self.quote
        if self.entry_qty > 0:
            cl = candles[-1][4]
            final += (cl - self.entry_price) * self.entry_qty * (1 if self.direction == "long" else -1)
        return {"final_equity": final, "realized_pnl": self.realized, "fees": self.fees,
                "wins": self.wins, "losses": self.losses}


class VwapSim:
    def __init__(self, cfg: VwapConfig, maker_fee: float, taker_fee: float):
        self.cfg = cfg
        self.taker_fee = taker_fee / 100.0
        self.quote = cfg.total_budget_quote
        self.realized = 0.0
        self.fees = 0.0
        self.wins = 0
        self.losses = 0
        self.cooldown_until = 0
        self.trades_today = 0
        self.day = 0

    def run(self, candles: list, res: SimResult):
        vwaps = rolling_vwap(candles, self.cfg.vwap_window or 0)
        closes = [c[4] for c in candles]
        ema_c = ema(closes, self.cfg.confirm_ema_period) if self.cfg.confirm_ema_period else None
        in_pos = False
        direction = "long"
        entry = 0.0
        qty = 0.0
        for i, (ts, o, h, l, cl, v) in enumerate(candles):
            day = ts // 86400000
            if day != self.day:
                self.day = day
                self.trades_today = 0
            if in_pos:
                hit = ""
                if direction == "long":
                    if l <= entry * (1 - self.cfg.stop_loss_pct / 100.0):
                        hit = "sl"
                    elif cl >= vwaps[i] and self.cfg.mode == "mean_reversion":
                        hit = "vwap"
                    elif h >= entry * (1 + self.cfg.take_profit_pct / 100.0):
                        hit = "tp"
                    elif self.cfg.mode == "reclaim" and cl < vwaps[i] * 0.999:
                        hit = "lost"
                else:
                    if h >= entry * (1 + self.cfg.stop_loss_pct / 100.0):
                        hit = "sl"
                    elif cl <= vwaps[i] and self.cfg.mode == "mean_reversion":
                        hit = "vwap"
                    elif l <= entry * (1 - self.cfg.take_profit_pct / 100.0):
                        hit = "tp"
                if hit:
                    px = entry * (1 - self.cfg.stop_loss_pct / 100.0) if hit == "sl" and direction == "long" else \
                        entry * (1 + self.cfg.stop_loss_pct / 100.0) if hit == "sl" else \
                        vwaps[i] if hit == "vwap" else cl
                    fee = px * qty * self.taker_fee
                    pnl = (px - entry) * qty * (1 if direction == "long" else -1)
                    pnl -= fee
                    self.quote += pnl
                    self.realized += pnl
                    self.fees += fee
                    if pnl > 0:
                        self.wins += 1
                    else:
                        self.losses += 1
                    res.trades.append({"ts": ts, "side": "sell" if direction == "long" else "buy",
                                       "price": px, "qty": qty, "fee": fee, "role": "vwap_exit", "pnl": pnl})
                    in_pos = False
                    self.cooldown_until = ts + self.cfg.cooldown_minutes * 60000
                unreal = (cl - entry) * qty * (1 if direction == "long" else -1)
                res.equity_at(ts, self.quote + unreal)
                continue
            if ts < self.cooldown_until or self.trades_today >= self.cfg.max_trades_per_day:
                res.equity_at(ts, self.quote)
                continue
            entry_sig = ""
            if self.cfg.mode == "mean_reversion":
                if l <= vwaps[i] * (1 - self.cfg.deviation_pct / 100.0):
                    entry_sig = "long"
                elif self.cfg.allow_short and h >= vwaps[i] * (1 + self.cfg.deviation_pct / 100.0):
                    entry_sig = "short"
            else:
                if i >= 1 and closes[i - 1] < vwaps[i - 1] and cl > vwaps[i]:
                    if not ema_c or cl > ema_c[i]:
                        entry_sig = "long"
                elif self.cfg.allow_short and i >= 1 and closes[i - 1] > vwaps[i - 1] and cl < vwaps[i]:
                    if not ema_c or cl < ema_c[i]:
                        entry_sig = "short"
            if entry_sig:
                direction = entry_sig
                entry = vwaps[i] * (1 - self.cfg.deviation_pct / 100.0) if self.cfg.mode == "mean_reversion" and direction == "long" else \
                    vwaps[i] * (1 + self.cfg.deviation_pct / 100.0) if self.cfg.mode == "mean_reversion" else cl
                qty = (self.cfg.total_budget_quote * self.cfg.position_pct / 100.0) / entry
                fee = entry * qty * self.taker_fee
                self.quote -= fee
                self.fees += fee
                in_pos = True
                self.trades_today += 1
                res.trades.append({"ts": ts, "side": "buy" if direction == "long" else "sell",
                                   "price": entry, "qty": qty, "fee": fee, "role": "vwap_entry"})
            res.equity_at(ts, self.quote)
        return {"final_equity": self.quote, "realized_pnl": self.realized, "fees": self.fees,
                "wins": self.wins, "losses": self.losses}


def run_backtest(req: BacktestRequest, candles: list) -> dict:
    res = SimResult()
    start_eq = req.initial_balance_quote
    if req.type == "grid":
        sim = GridSim(GridConfig(**(req.grid.model_dump() if req.grid else {})),
                      req.maker_fee_pct, req.taker_fee_pct,
                      mid=candles[len(candles) // 2][4] if candles else 0.0)
        stats = sim.run(candles, res)
    elif req.type == "dca":
        sim = DcaSim(DcaConfig(**(req.dca.model_dump() if req.dca else {})),
                     req.maker_fee_pct, req.taker_fee_pct)
        stats = sim.run(candles, res)
    elif req.type == "momentum":
        sim = MomentumSim(MomentumConfig(**(req.momentum.model_dump() if req.momentum else {})),
                          req.maker_fee_pct, req.taker_fee_pct)
        stats = sim.run(candles, res)
    else:
        sim = VwapSim(VwapConfig(**(req.vwap.model_dump() if req.vwap else {})),
                      req.maker_fee_pct, req.taker_fee_pct)
        stats = sim.run(candles, res)
    equity = res.equity
    final = stats.get("final_equity", start_eq)
    pnl = final - start_eq
    roi = pnl / start_eq * 100 if start_eq else 0
    days = (candles[-1][0] - candles[0][0]) / 86400000 if candles else 0
    apr = roi * (365 / days) if days > 0 else 0
    peak, maxdd = start_eq, 0.0
    for _, v in equity:
        peak = max(peak, v)
        if peak:
            maxdd = max(maxdd, (peak - v) / peak * 100)
    buys = [t for t in res.trades if t["side"] == "buy"]
    sells = [t for t in res.trades if t["side"] == "sell"]
    return {"stats": {**{k: (round(v, 4) if isinstance(v, float) else v) for k, v in stats.items()},
                      "initial_balance": start_eq, "final_equity": round(final, 2),
                      "pnl": round(pnl, 2), "roi_pct": round(roi, 2), "apr_pct": round(apr, 2),
                      "max_drawdown_pct": round(maxdd, 2), "days": round(days, 1),
                      "trades": len(res.trades), "buy_trades": len(buys), "sell_trades": len(sells),
                      "symbol": req.symbol, "timeframe": req.timeframe,
                      "start": candles[0][0] if candles else 0, "end": candles[-1][0] if candles else 0},
            "trades": res.trades, "equity": equity, "candles": candles,
            "levels": getattr(sim, "levels", [])}
