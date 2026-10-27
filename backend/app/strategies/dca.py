from ..core.utils import now_ms, dca_safety_deviations, ema, rsi
from ..core.models import DcaConfig
from .base import Strategy, BotContext


class DcaStrategy(Strategy):
    name = "dca"

    def __init__(self, ctx: BotContext, params: dict):
        super().__init__(ctx, params)
        self.cfg = DcaConfig(**params)
        self.cycles: list = []
        self.cooldown_until = 0
        self.cycles_done = 0
        self.realized_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self._budget_warned = False
        self._entry_cache = {"ts": 0, "ok": False}

    def base_order_quote(self) -> float:
        return self.cfg.base_order_quote or self.cfg.total_budget_quote * self.cfg.base_order_pct / 100.0

    def safety_quote(self, n: int) -> float:
        base = self.base_order_quote()
        raw = self.cfg.safety_order_quote or base
        if self.cfg.safety_size_mode == "pct_of_base":
            raw = base * (raw / 100.0) if raw > 1 else base * raw
        elif self.cfg.safety_size_mode == "pct_of_capital":
            raw = self.cfg.total_budget_quote * (raw / 100.0) if raw > 1 else self.cfg.total_budget_quote * raw
        return raw * (self.cfg.volume_scale ** n)

    def safety_devs(self) -> list:
        return dca_safety_deviations(self.cfg.safety_deviation_pct, self.cfg.max_safety_orders,
                                     self.cfg.safety_step_scale)

    def planned_spend(self, n_safety: int = None) -> float:
        n = self.cfg.max_safety_orders if n_safety is None else n_safety
        if n <= 0:
            return self.base_order_quote()
        return self.base_order_quote() + sum(self.safety_quote(k) for k in range(n))

    def affordable_safety_orders(self) -> int:
        """How many safety orders fit into the cycle budget after the base order."""
        budget = self.cfg.total_budget_quote
        base = self.base_order_quote()
        if base > budget:
            return -1
        spend = base
        n = 0
        while n < self.cfg.max_safety_orders:
            q = self.safety_quote(n)
            if spend + q > budget * 1.0001:
                break
            spend += q
            n += 1
        return n

    async def _entry_allowed(self, price: float) -> bool:
        cond = self.cfg.entry_condition
        if cond == "immediate":
            return True
        if now_ms() - self._entry_cache["ts"] < 60000:
            return self._entry_cache["ok"]
        candles = await self.ctx.candles(self.cfg.trend_filter_timeframe,
                                         max(max(self.cfg.ema_period, self.cfg.rsi_period) + 10, 60))
        ok = True
        if candles and len(candles) > max(self.cfg.ema_period, self.cfg.rsi_period):
            closes = [c[4] for c in candles]
            last = closes[-1]
            if cond == "rsi":
                r = rsi(closes, self.cfg.rsi_period)
                ok = r < self.cfg.rsi_max if not self.cfg.short_mode else r > (100 - self.cfg.rsi_max)
            else:
                e = ema(closes, self.cfg.ema_period)[-1]
                if self.cfg.short_mode:
                    ok = last < e if self.cfg.ema_side == "above" else last > e
                else:
                    ok = last > e if self.cfg.ema_side == "above" else last < e
        self._entry_cache = {"ts": now_ms(), "ok": ok}
        return ok

    async def start(self):
        self.ctx.log(f"dca started ({'short' if self.cfg.short_mode else 'long'}), "
                     f"base={self.base_order_quote():.2f}, max SO={self.cfg.max_safety_orders}, "
                     f"TP={self.cfg.take_profit_pct}% from {self.cfg.tp_from}, "
                     f"entry={self.cfg.entry_condition}, max cycles={self.cfg.max_active_cycles}")

    async def on_tick(self, price: float, candles: list):
        if not price:
            return
        for c in list(self.cycles):
            await self._manage_cycle(c, price)
        await self._maybe_open_cycle(price)

    async def _maybe_open_cycle(self, price: float):
        if len(self.cycles) >= self.cfg.max_active_cycles:
            return
        if not self.ctx.flags.get("entry_ok", True):
            return
        if now_ms() < self.cooldown_until:
            return
        if self.cycles:
            last_base = self.cycles[-1]["base_price"]
            dev = self.cfg.new_cycle_deviation_pct
            if not ((price <= last_base * (1 - dev / 100.0)) if not self.cfg.short_mode
                    else (price >= last_base * (1 + dev / 100.0))):
                return
        if not price or price <= 0:
            return
        n_so = self.affordable_safety_orders()
        if n_so < 0:
            self.ctx.log(f"cycle budget {self.cfg.total_budget_quote:.2f} quote is smaller than the "
                         f"base order ({self.base_order_quote():.2f}) — increase budget or lower "
                         f"base order %", "error")
            self.cooldown_until = now_ms() + 60000
            return
        if n_so < self.cfg.max_safety_orders and not self._budget_warned:
            self._budget_warned = True
            self.ctx.log(f"cycle budget {self.cfg.total_budget_quote:.2f} fits only {n_so} of "
                         f"{self.cfg.max_safety_orders} safety orders (base "
                         f"{self.base_order_quote():.2f}, volume scale {self.cfg.volume_scale}) — "
                         f"cycles will use {n_so}", "warning")
        if not await self._entry_allowed(price):
            return
        qty = self.base_order_quote() / price
        cid = self.ctx.storage.new_cycle_id()
        cycle = {"id": cid, "status": "active", "direction": "short" if self.cfg.short_mode else "long",
                 "entries": [], "safety_count": 0, "base_price": price, "opened_at": now_ms(),
                 "trail_peak": price, "realized_pnl": 0.0, "max_so": n_so}
        self.cycles.append(cycle)
        side = "sell" if self.cfg.short_mode else "buy"
        try:
            o = await self.ctx.orders.place(self.ctx.symbol, side, "market", qty, None,
                                            role="dca_base", cycle_id=cid)
        except Exception as e:
            self.cycles.pop()
            self.ctx.log(f"base order failed: {e}", "error")
            self.cooldown_until = now_ms() + 10000
            return
        fee = o["qty"] * price * self.ctx.fee_pct / 100.0
        self._add_entry(cycle, price, o["qty"], fee, "dca_base")
        self.ctx.emit("cycle_opened", {"cycle_id": cid, "price": price, "qty": o["qty"]})

    def _recompute(self, c: dict):
        longs = [e for e in c["entries"] if (e["side"] == "buy") == (c["direction"] == "long")]
        total_qty = sum(e["qty"] for e in longs)
        c["total_qty"] = total_qty
        c["avg_price"] = sum(e["price"] * e["qty"] for e in longs) / total_qty if total_qty else 0
        ref = c["avg_price"] if self.cfg.tp_from == "avg" else c["base_price"]
        tp = self.cfg.take_profit_pct / 100.0
        if self.cfg.tp_includes_fees:
            tp += self.ctx.fee_pct * 2 / 100.0
        if c["direction"] == "long":
            c["tp_price"] = ref * (1 + tp) if ref else 0
            c["sl_price"] = c["avg_price"] * (1 - self.cfg.stop_loss_pct / 100.0) \
                if self.cfg.stop_loss_pct and c["avg_price"] else 0
        else:
            c["tp_price"] = ref * (1 - tp) if ref else 0
            c["sl_price"] = c["avg_price"] * (1 + self.cfg.stop_loss_pct / 100.0) \
                if self.cfg.stop_loss_pct and c["avg_price"] else 0

    def _add_entry(self, c: dict, price: float, qty: float, fee: float, role: str):
        side = "sell" if c["direction"] == "short" else "buy"
        c["entries"].append({"side": side, "price": price, "qty": qty, "fee": fee,
                             "ts": now_ms(), "role": role, "notional": price * qty})
        self._recompute(c)
        self._persist_cycle(c)

    def _persist_cycle(self, c: dict):
        self.ctx.storage.insert_cycle({"id": c["id"], "bot_id": self.ctx.bot_id,
                                       "strategy": "dca", "status": c["status"],
                                       "opened_at": c["opened_at"],
                                       "closed_at": c.get("closed_at", 0),
                                       "realized_pnl": round(c.get("realized_pnl", 0), 8),
                                       "fees": round(sum(e["fee"] for e in c["entries"]), 8),
                                       "meta": {"entries": c["entries"],
                                                "avg_price": c.get("avg_price", 0),
                                                "tp_price": c.get("tp_price", 0),
                                                "sl_price": c.get("sl_price", 0),
                                                "direction": c["direction"],
                                                "safety_count": c["safety_count"],
                                                "base_price": c["base_price"]}})

    async def _manage_cycle(self, c: dict, price: float):
        await self._place_safety(c, price)
        tp = c.get("tp_price", 0)
        d = c["direction"]
        if tp:
            if d == "long" and price >= tp:
                if self.cfg.min_profit_pct and c.get("avg_price") and \
                        (price / c["avg_price"] - 1) * 100 < self.cfg.min_profit_pct:
                    pass
                elif self.cfg.trailing_tp_enabled:
                    c["trail_peak"] = max(c.get("trail_peak", price), price)
                    if price <= c["trail_peak"] * (1 - self.cfg.trailing_tp_deviation_pct / 100.0):
                        await self._close_cycle(c, price, "trailing tp")
                        return
                else:
                    await self._close_cycle(c, price, "take profit")
                    return
            elif d == "short" and price <= tp:
                if self.cfg.min_profit_pct and c.get("avg_price") and \
                        (1 - price / c["avg_price"]) * 100 < self.cfg.min_profit_pct:
                    pass
                elif self.cfg.trailing_tp_enabled:
                    c["trail_peak"] = min(c.get("trail_peak", price), price)
                    if price >= c["trail_peak"] * (1 + self.cfg.trailing_tp_deviation_pct / 100.0):
                        await self._close_cycle(c, price, "trailing tp")
                        return
                else:
                    await self._close_cycle(c, price, "take profit")
                    return
        sl = c.get("sl_price", 0)
        if sl and ((d == "long" and price <= sl) or (d == "short" and price >= sl)):
            await self._close_cycle(c, price, "stop loss")
            return

    async def _place_safety(self, c: dict, price: float):
        n = c["safety_count"]
        max_so = c.get("max_so", self.cfg.max_safety_orders)
        if n >= self.cfg.max_safety_orders or n >= max_so:
            return
        devs = self.safety_devs()
        if n >= len(devs):
            return
        trigger = c["base_price"] * (1 - devs[n] / 100.0) if c["direction"] == "long" \
            else c["base_price"] * (1 + devs[n] / 100.0)
        hit = price <= trigger if c["direction"] == "long" else price >= trigger
        if not hit:
            return
        qty = self.safety_quote(n) / price
        side = "buy" if c["direction"] == "long" else "sell"
        otype = self.cfg.safety_order_type
        try:
            o = await self.ctx.orders.place(self.ctx.symbol, side, otype, qty,
                                            price if otype == "limit" else None, role="dca_safety",
                                            cycle_id=c["id"])
        except Exception as e:
            self.ctx.log(f"safety order failed: {e}", "warning")
            return
        c["safety_count"] += 1
        fee = price * qty * self.ctx.fee_pct / 100.0
        self._add_entry(c, o.get("avg_price") or price, qty, fee, "dca_safety")
        self.ctx.emit("dca_safety", {"cycle_id": c["id"], "n": c["safety_count"],
                                     "price": price, "qty": qty})

    async def _close_cycle(self, c: dict, price: float, reason: str):
        qty = c.get("total_qty", 0)
        if qty <= 0:
            if c in self.cycles:
                self.cycles.remove(c)
            return
        side = "sell" if c["direction"] == "long" else "buy"
        try:
            o = await self.ctx.orders.place(self.ctx.symbol, side, "market", qty, None,
                                            role="dca_close", cycle_id=c["id"])
            fill_price = o.get("avg_price") or price
        except Exception as e:
            self.ctx.log(f"cycle close failed: {e}", "error")
            return
        fee = fill_price * qty * self.ctx.fee_pct / 100.0
        if c["direction"] == "long":
            pnl = (fill_price - c["avg_price"]) * qty
        else:
            pnl = (c["avg_price"] - fill_price) * qty
        fees = sum(e["fee"] for e in c["entries"]) + fee
        pnl -= fees
        self.realized_pnl += pnl
        c["realized_pnl"] = pnl
        self.ctx.risk.on_realized(pnl)
        self.ctx.position.apply_fill(side, fill_price, qty, fee)
        c["status"] = "closed"
        c["closed_at"] = now_ms()
        self._persist_cycle(c)
        if pnl >= 0:
            self.wins += 1
        else:
            self.losses += 1
        self.cycles_done += 1
        for e in c["entries"]:
            t_side = "sell" if e["side"] == "buy" else "buy"
            close_pnl = (fill_price - e["price"]) * e["qty"] * (1 if c["direction"] == "long" else -1)
            await self.ctx.record_fill({"symbol": self.ctx.symbol, "side": t_side, "price": fill_price,
                                        "qty": e["qty"], "fee": fee * e["qty"] / max(qty, 1e-9),
                                        "notional": fill_price * e["qty"], "ts": now_ms(),
                                        "order_id": "", "taker": True},
                                       "dca_close", cycle_id=c["id"], pnl=round(close_pnl, 8))
        self.ctx.emit("cycle_closed", {"cycle_id": c["id"], "pnl": pnl, "reason": reason,
                                       "avg_price": c["avg_price"], "close_price": fill_price})
        self.ctx.log(f"cycle closed ({reason}): pnl={pnl:.2f} after {len(c['entries'])} orders")
        self.cooldown_until = now_ms() + self.cfg.cooldown_minutes * 60000
        if c in self.cycles:
            self.cycles.remove(c)

    async def on_fill(self, fill: dict):
        role = fill.get("role", "")
        if role == "dca_close":
            return
        cid = fill.get("cycle_id", "")
        c = next((x for x in self.cycles if x["id"] == cid), None)
        if role in ("dca_base", "dca_safety") and c:
            self.ctx.position.apply_fill(fill["side"], fill["price"], fill["qty"], fill.get("fee", 0))
            await self.ctx.record_fill(fill, role, cycle_id=c["id"])
        else:
            self.ctx.position.apply_fill(fill["side"], fill["price"], fill["qty"], fill.get("fee", 0))
            await self.ctx.record_fill(fill, role or "manual")

    async def stop(self, reason: str = ""):
        price = await self._last_price()
        for c in list(self.cycles):
            await self._close_cycle(c, price, "bot stopped")
        await self.ctx.orders.cancel_all(self.ctx.symbol)
        self.ctx.log(f"dca stopped: {reason}", "warning")
        self.ctx.emit("strategy_stopped", {"strategy": "dca", "reason": reason})

    async def _last_price(self) -> float:
        t = await self.ctx.ticker()
        return t.mid() or t.last or 0

    async def action_close_cycle(self):
        if self.cycles:
            await self._close_cycle(self.cycles[-1], await self._last_price(), "manual close")

    async def action_close_all_cycles(self):
        price = await self._last_price()
        for c in list(self.cycles):
            await self._close_cycle(c, price, "manual close all")

    async def action_force_cycle(self):
        self.cooldown_until = 0
        self._entry_cache = {"ts": 0, "ok": True}

    def actions(self) -> dict:
        return {"close_cycle": self.action_close_cycle, "force_cycle": self.action_force_cycle}

    def state(self) -> dict:
        active = [{
            "id": c["id"], "direction": c["direction"], "entries": len(c["entries"]),
            "avg_price": c.get("avg_price", 0), "tp_price": c.get("tp_price", 0),
            "sl_price": c.get("sl_price", 0), "total_qty": c.get("total_qty", 0),
            "safety_count": c["safety_count"], "base_price": c["base_price"],
            "opened_at": c["opened_at"]} for c in self.cycles]
        next_dev = 0
        if self.cycles:
            c = self.cycles[-1]
            if c["safety_count"] < len(self.safety_devs()):
                next_dev = round(self.safety_devs()[c["safety_count"]], 3)
        return {"strategy": "dca", "cycle_active": bool(self.cycles),
                "cycle": active[-1] if active else {},
                "active_cycles": active,
                "realized_pnl": round(self.realized_pnl, 4), "cycles_done": self.cycles_done,
                "wins": self.wins, "losses": self.losses,
                "next_safety_dev": next_dev,
                "planned_spend": round(self.planned_spend(), 2),
                "budget": self.cfg.total_budget_quote,
                "cooldown_until": self.cooldown_until}
