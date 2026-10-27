from ..core.utils import now_ms, compute_levels, atr
from ..core.models import GridConfig
from .base import Strategy, BotContext


class GridStrategy(Strategy):
    name = "grid"

    def __init__(self, ctx: BotContext, params: dict):
        super().__init__(ctx, params)
        self.cfg = GridConfig(**params)
        self.levels: list = []
        self.level_orders: dict = {}
        self.buy_lots: dict = {}
        self.pending_lots: dict = {}
        self.realized_pnl = 0.0
        self.grid_profit = 0.0
        self.fees_estimated = 0.0
        self.roundtrips = 0
        self.rebuild_count = 0
        self.budget_used = 0.0
        self.rebuild_after = 0
        self.out_of_range_since = 0
        self.skew_shift = 0
        self.stopped = False
        self.stop_reason = ""
        self.peak_value = 0.0
        self.paused_by_vol = False

    def _px(self, t) -> float:
        if self.cfg.price_source == "last":
            return t.last or t.mid()
        return t.mid() or t.last

    def level_qty(self, price: float) -> float:
        if self.cfg.per_level_mode == "fixed_qty":
            return self.cfg.per_level_qty
        if self.cfg.per_level_mode == "fixed_quote":
            q = self.cfg.per_level_quote
        else:
            q = self.cfg.total_budget_quote / self.cfg.levels
        return q / price if price else 0.0

    def compute_levels(self, mid: float) -> list:
        lower, upper = self.cfg.lower_price, self.cfg.upper_price
        if lower <= 0 or upper <= 0 or lower >= upper:
            span = max(mid * 0.08, mid * 0.0001)
            lower, upper = mid - span, mid + span
        levels = compute_levels(lower, upper, self.cfg.levels, self.cfg.grid_type)
        if self.cfg.spacing_mode == "manual" and self.cfg.spacing_value > 0:
            step = self.cfg.spacing_value
            if self.cfg.grid_type == "geometric":
                ratio = 1 + step / 100.0
                levels = [lower * (ratio ** i) for i in range(self.cfg.levels)]
            else:
                levels = [lower + step * i for i in range(self.cfg.levels)]
            while len(levels) > 1 and levels[-1] > upper:
                levels.pop()
            if len(levels) < 2:
                levels = compute_levels(lower, upper, self.cfg.levels, self.cfg.grid_type)
        min_step = (levels[1] - levels[0]) if len(levels) > 1 else 0
        fee_min = mid * (self.ctx.fee_pct * 2 + self.cfg.fee_buffer_pct) / 100.0
        if min_step < fee_min and self.cfg.spacing_mode != "manual":
            span = fee_min * max(len(levels), 2)
            lower, upper = max(mid - span / 2, mid * 0.01), mid + span / 2
            levels = compute_levels(lower, upper, len(levels), self.cfg.grid_type)
        return levels

    def _order_meta(self, level: int, side: str) -> dict:
        return {"strategy": "grid", "level": level, "level_side": side}

    def _order_count(self) -> int:
        return len(self.ctx.orders.open_map)

    def _order_cap_reached(self) -> bool:
        return self.cfg.max_active_orders > 0 and self._order_count() >= self.cfg.max_active_orders

    async def start(self):
        t = await self.ctx.ticker()
        mid = self._px(t)
        if not mid:
            self.ctx.log("no market price, cannot start grid", "error")
            return
        self.levels = self.compute_levels(mid)
        self.level_orders = {i: {"buy_oid": None, "sell_oid": None, "buy_ts": 0, "sell_ts": 0, "lot": None}
                             for i in range(len(self.levels))}
        self.ctx.log(f"grid started ({self.cfg.grid_mode}): {len(self.levels)} levels "
                     f"[{self.levels[0]:.6g} .. {self.levels[-1]:.6g}] mid={mid:.6g}")
        if self.cfg.grid_mode != "short":
            await self._place_all_buys(mid)
        if self.cfg.grid_mode in ("neutral", "short") and self.cfg.use_inventory:
            await self._place_initial_sells(mid)

    async def _place_all_buys(self, mid: float):
        for i, price in enumerate(self.levels):
            await self._maybe_place_buy(i, mid)

    async def _place_initial_sells(self, mid: float):
        avail = self.ctx.position.qty
        if avail <= 0:
            return
        for i in range(len(self.levels) - 1, -1, -1):
            if self.levels[i] <= mid:
                continue
            st = self.level_orders[i]
            if st["sell_oid"]:
                continue
            qty = min(self.level_qty(self.levels[i]), avail)
            if qty <= 0:
                break
            try:
                o = await self.ctx.orders.place(self.ctx.symbol, "sell", "limit", qty,
                                                self.levels[i], post_only=self.cfg.post_only,
                                                role="grid_sell_init", meta=self._order_meta(i, "sell"))
                st["sell_oid"] = o["oid"]
                st["sell_ts"] = now_ms()
                avail -= qty
            except Exception as e:
                self.ctx.log(f"sell placement failed at {self.levels[i]:.6g}: {e}", "warning")
                break

    async def _maybe_place_buy(self, i: int, mid: float) -> bool:
        if self.stopped or i < 0 or i >= len(self.levels) or self.paused_by_vol or self._order_cap_reached():
            return False
        if not self.ctx.flags.get("entry_ok", True):
            return False
        price = self.levels[i]
        if price >= mid - (self.levels[1] - self.levels[0]) * 0.01:
            return False
        st = self.level_orders[i]
        if st["buy_oid"]:
            return False
        qty = self.level_qty(price)
        if qty <= 0:
            return False
        notional = qty * price
        if self.cfg.min_notional_quote and notional < self.cfg.min_notional_quote:
            return False
        if self.budget_used + notional > self.cfg.total_budget_quote * 1.0001:
            return False
        if self.cfg.max_inventory_base and self.ctx.position.qty + qty > self.cfg.max_inventory_base:
            return False
        try:
            o = await self.ctx.orders.place(self.ctx.symbol, "buy", "limit", qty, price,
                                            post_only=self.cfg.post_only,
                                            role="grid_buy", meta=self._order_meta(i, "buy"))
            st["buy_oid"] = o["oid"]
            st["buy_ts"] = now_ms()
            self.budget_used += notional
            return True
        except Exception as e:
            if "max position" in str(e) or "kill switch" in str(e) or "daily loss" in str(e):
                self.ctx.log(f"risk: {e}", "warning")
            else:
                self.ctx.log(f"buy placement failed at {price:.6g}: {str(e)[:160]}", "warning")
            return False

    async def on_tick(self, price: float, candles: list):
        if self.stopped:
            return
        if not self.levels:
            t = await self.ctx.ticker()
            mid = self._px(t)
            self.levels = self.compute_levels(mid)
            self.level_orders = {i: {"buy_oid": None, "sell_oid": None, "buy_ts": 0, "sell_ts": 0, "lot": None}
                                 for i in range(len(self.levels))}
            if self.cfg.grid_mode != "short":
                await self._place_all_buys(mid)
            if self.cfg.grid_mode in ("neutral", "short") and self.cfg.use_inventory:
                await self._place_initial_sells(mid)
            return
        if now_ms() < self.rebuild_after:
            return
        await self._check_volatility_pause(candles)
        await self._check_order_timeouts()
        await self._check_range(price)
        if self.stopped:
            return
        await self._check_exit_conditions(price, candles)
        if self.pending_lots:
            await self._retry_pending_sells()
        self._update_skew(price)
        self._track_drawdown(price)

    async def _check_volatility_pause(self, candles: list):
        if not self.cfg.pause_volatility_atr_pct or not candles or len(candles) < 20:
            self.paused_by_vol = False
            return
        close = candles[-1][4]
        a = atr(candles, 14)
        vol = a / close * 100 if close else 0
        new_state = vol > self.cfg.pause_volatility_atr_pct
        if new_state != self.paused_by_vol:
            self.paused_by_vol = new_state
            self.ctx.log(f"volatility pause {'ON' if new_state else 'OFF'} (atr {vol:.2f}%)", "warning" if new_state else "info")

    async def _check_order_timeouts(self):
        if not self.cfg.order_timeout_min:
            return
        limit = self.cfg.order_timeout_min * 60000
        for i, st in self.level_orders.items():
            if st["buy_oid"] and now_ms() - st["buy_ts"] > limit:
                await self.ctx.orders.cancel(st["buy_oid"])
                st["buy_oid"] = None
                self.budget_used = max(0.0, self.budget_used - self.level_qty(self.levels[i]) * self.levels[i])
            if st["sell_oid"] and now_ms() - st["sell_ts"] > limit and st["lot"]:
                lot = st["lot"]
                await self.ctx.orders.cancel(st["sell_oid"])
                st["sell_oid"] = None
                self.pending_lots[f"re{st['sell_oid'] or i}_{i}"] = lot

    def has_open_activity(self) -> bool:
        return any(s["buy_oid"] or s["sell_oid"] for s in self.level_orders.values()) \
            or bool(self.pending_lots) or self.ctx.position.qty > 0

    async def _check_range(self, mid: float):
        lower, upper = self.levels[0], self.levels[-1]
        if lower <= mid <= upper:
            self.out_of_range_since = 0
            return
        if not self.out_of_range_since:
            self.out_of_range_since = now_ms()
            return
        if now_ms() - self.out_of_range_since < 15000:
            return
        if self.cfg.on_exit == "hold":
            return
        if self.cfg.on_exit == "stop" or not self.cfg.infinite:
            await self.ctx.emit("grid_exit", {"price": mid})
            await self.stop(f"price left range: {mid:.6g} outside [{lower:.6g}, {upper:.6g}]")
            return
        if self.cfg.trailing_up and mid < lower:
            return
        await self.rebuild(mid)

    async def rebuild(self, mid: float):
        await self.ctx.orders.cancel_all(self.ctx.symbol)
        width = self.levels[-1] - self.levels[0]
        lower = max(mid - width / 2, mid * 0.02)
        upper = mid + width / 2
        self.cfg.lower_price, self.cfg.upper_price = lower, upper
        self.levels = compute_levels(lower, upper, self.cfg.levels, self.cfg.grid_type)
        self.level_orders = {i: {"buy_oid": None, "sell_oid": None, "buy_ts": 0, "sell_ts": 0, "lot": None}
                             for i in range(len(self.levels))}
        self.buy_lots = {}
        self.pending_lots = {}
        self.budget_used = 0.0
        self.rebuild_count += 1
        self.out_of_range_since = 0
        self.rebuild_after = now_ms() + max(self.cfg.reopen_delay_s * 1000, 3000)
        self.ctx.log(f"grid rebuilt around {mid:.6g} [{lower:.6g} .. {upper:.6g}]", "info",
                     {"rebuild": self.rebuild_count})
        self.ctx.emit("grid_rebuilt", {"mid": mid, "lower": lower, "upper": upper})
        if self.cfg.grid_mode != "short":
            await self._place_all_buys(mid)
        if self.cfg.grid_mode in ("neutral", "short") and self.cfg.use_inventory:
            await self._place_initial_sells(mid)

    def _tp_target(self) -> float:
        tp = self.cfg.take_profit_pct
        if not tp:
            return float("inf")
        if self.cfg.tp_includes_fees:
            tp += self.ctx.fee_pct * 2
        return self.cfg.total_budget_quote * tp / 100.0

    async def _check_exit_conditions(self, price: float, candles: list):
        if self.cfg.take_profit_pct and self.realized_pnl >= self._tp_target():
            await self.stop(f"take profit hit: {self.realized_pnl:.2f} quote")
            return
        sl_triggers = []
        if self.cfg.stop_loss_pct and self.levels:
            sl_triggers.append(self.levels[0] * (1 - self.cfg.stop_loss_pct / 100.0))
        if self.cfg.stop_loss_price:
            sl_triggers.append(self.cfg.stop_loss_price)
        for trig in sl_triggers:
            if price <= trig:
                if self.cfg.stop_loss_close_inventory and self.ctx.position.qty > 0:
                    try:
                        await self.ctx.orders.place(self.ctx.symbol, "sell", "market",
                                                    self.ctx.position.qty, None,
                                                    role="grid_stoploss_close")
                    except Exception as e:
                        self.ctx.log(f"stop-loss close failed: {e}", "error")
                await self.stop(f"stop loss hit at {price:.6g}")
                return

    def _track_drawdown(self, price: float):
        if not self.cfg.max_drawdown_pct:
            return
        value = self.realized_pnl + self.ctx.position.unrealized(price)
        if value > self.peak_value:
            self.peak_value = value
        if self.peak_value > 0 and value < self.peak_value * (1 - self.cfg.max_drawdown_pct / 100.0):
            self.ctx.risk.on_realized(value - self.peak_value)
            self.peak_value = value
            self.ctx.log(f"grid drawdown limit hit ({self.cfg.max_drawdown_pct}%), stopping", "warning")
            self.ctx.emit("risk_alert", {"type": "grid_drawdown"})
            self.stopped = True
            self.stop_reason = "max drawdown"

    def _update_skew(self, mid: float):
        if not self.cfg.skew_enabled:
            return
        total_buy_qty = sum(self.level_qty(p) for p in self.levels if p < mid)
        if total_buy_qty <= 0:
            return
        ratio = self.ctx.position.qty / total_buy_qty
        target = min(int(ratio * self.cfg.skew_max_shift), self.cfg.skew_max_shift)
        if target != self.skew_shift:
            self.skew_shift = target
            self.ctx.emit("grid_skew", {"shift": self.skew_shift, "ratio": round(ratio, 3)})

    async def on_fill(self, fill: dict):
        if self.stopped:
            return
        role = fill.get("role", "")
        price, qty = fill["price"], fill["qty"]
        fee = fill.get("fee", 0)
        oid = fill.get("order_id", "")
        level = None
        for i, st in self.level_orders.items():
            if st["buy_oid"] == oid:
                st["buy_oid"] = None
                level = i
                break
            if st["sell_oid"] == oid:
                st["sell_oid"] = None
                level = i
                break
        if level is None:
            level = self._nearest_level(price)
        t = await self.ctx.ticker()
        mid = self._px(t) or price
        if role in ("grid_buy", "") and fill["side"] == "buy":
            self.budget_used = max(0.0, self.budget_used - price * qty)
            self.pending_lots[oid] = {"price": price, "qty": qty, "fee": fee, "ts": fill["ts"]}
            self.ctx.position.apply_fill("buy", price, qty, fee)
            sell_level = min(level + 1 + self.skew_shift, len(self.levels) - 1)
            await self._place_sell_for_lot(sell_level, oid)
            await self.ctx.record_fill(fill, "grid_buy")
        elif role in ("grid_sell", "grid_sell_init", "") and fill["side"] == "sell":
            lot = self.buy_lots.pop(oid, None)
            sell_fee_est = price * qty * self.ctx.fee_pct / 100.0
            if lot:
                pnl = (price - lot["price"]) * qty - lot["fee"] - sell_fee_est
                self.realized_pnl += pnl
                self.grid_profit += pnl
                self.fees_estimated += lot["fee"] + sell_fee_est
                self.roundtrips += 1
                self.ctx.risk.on_realized(pnl)
                self.ctx.position.apply_fill("sell", price, qty, sell_fee_est)
                await self.ctx.record_fill(fill, "grid_sell", pnl=round(pnl, 8))
                self.ctx.emit("roundtrip", {"pnl": pnl, "price": price, "qty": qty,
                                            "roundtrips": self.roundtrips})
            else:
                self.ctx.position.apply_fill("sell", price, qty, sell_fee_est)
                await self.ctx.record_fill(fill, "grid_sell_init")
            if self.cfg.grid_mode != "short":
                buy_level = level - 1 - self.skew_shift
                await self._maybe_place_buy(buy_level, mid)
        else:
            self.ctx.position.apply_fill(fill["side"], price, qty, fee)
            await self.ctx.record_fill(fill, role or "manual")

    async def _place_sell_for_lot(self, sell_level: int, lot_oid: str):
        if self.stopped or lot_oid not in self.pending_lots:
            return
        if sell_level < 0 or sell_level >= len(self.levels) or self._order_cap_reached():
            return
        lot = self.pending_lots[lot_oid]
        if lot.get("placing"):
            return
        lot["placing"] = True
        price = self.levels[sell_level]
        if price <= lot["price"] * (1 + self.ctx.fee_pct * 2 / 100.0):
            price = lot["price"] * (1 + (self.ctx.fee_pct * 2 + self.cfg.fee_buffer_pct) / 100.0)
        st = self.level_orders[sell_level]
        sell_qty = lot["qty"] if self.cfg.rebalance_mode == "inventory" else self.level_qty(price)
        try:
            o = await self.ctx.orders.place(self.ctx.symbol, "sell", "limit", sell_qty, price,
                                            post_only=self.cfg.post_only, role="grid_sell",
                                            meta=self._order_meta(sell_level, "sell"))
            st["sell_oid"] = o["oid"]
            st["sell_ts"] = now_ms()
            st["lot"] = lot
            self.buy_lots[o["oid"]] = lot
            self.pending_lots.pop(lot_oid, None)
        except Exception as e:
            lot["placing"] = False
            self.ctx.log(f"sell placement failed: {e}", "warning")

    async def _retry_pending_sells(self):
        for lot_oid in list(self.pending_lots.keys()):
            lot = self.pending_lots[lot_oid]
            level = self._nearest_level(lot["price"])
            await self._place_sell_for_lot(level + 1, lot_oid)

    def _nearest_level(self, price: float) -> int:
        best, bd = 0, float("inf")
        for i, p in enumerate(self.levels):
            d = abs(p - price)
            if d < bd:
                bd, best = d, i
        return best

    async def stop(self, reason: str = ""):
        self.stopped = True
        self.stop_reason = reason
        await self.ctx.orders.cancel_all(self.ctx.symbol)
        self.ctx.log(f"grid stopped: {reason}", "warning")
        self.ctx.emit("strategy_stopped", {"strategy": "grid", "reason": reason})

    async def action_rebuild(self):
        t = await self.ctx.ticker()
        await self.rebuild(self._px(t))

    async def action_close_all(self):
        await self.ctx.orders.cancel_all()
        if self.ctx.position.qty > 0:
            try:
                await self.ctx.orders.place(self.ctx.symbol, "sell", "market", self.ctx.position.qty,
                                            None, role="manual_close")
            except Exception as e:
                self.ctx.log(f"close failed: {e}", "error")

    def actions(self) -> dict:
        return {"rebuild": self.action_rebuild, "close_all": self.action_close_all}

    def state(self) -> dict:
        lower = self.levels[0] if self.levels else self.cfg.lower_price
        upper = self.levels[-1] if self.levels else self.cfg.upper_price
        return {
            "strategy": "grid", "grid_mode": self.cfg.grid_mode,
            "realized_pnl": round(self.realized_pnl, 4),
            "grid_profit": round(self.grid_profit, 4), "roundtrips": self.roundtrips,
            "rebuilds": self.rebuild_count, "budget_used": round(self.budget_used, 2),
            "range": [lower, upper], "levels": len(self.levels), "skew_shift": self.skew_shift,
            "open_buys": sum(1 for s in self.level_orders.values() if s["buy_oid"]),
            "open_sells": sum(1 for s in self.level_orders.values() if s["sell_oid"]),
            "held_lots": len(self.buy_lots) + len(self.pending_lots),
            "paused_by_volatility": self.paused_by_vol,
            "stopped": self.stopped, "stop_reason": self.stop_reason,
            "fees_estimated": round(self.fees_estimated, 4),
        }
