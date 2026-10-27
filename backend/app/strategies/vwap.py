from ..core.utils import now_ms, rolling_vwap, ema
from ..core.models import VwapConfig
from .base import Strategy, BotContext


class VwapStrategy(Strategy):
    name = "vwap"

    def __init__(self, ctx: BotContext, params: dict):
        super().__init__(ctx, params)
        self.cfg = VwapConfig(**params)
        self.entry_price = 0.0
        self.entry_qty = 0.0
        self.direction = "long"
        self.opened_at = 0
        self.cooldown_until = 0
        self.realized_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.trades_today = 0
        self.day = now_ms() // 86400000
        self.current_vwap = 0.0
        self._pending_exit = False

    def position_qty(self) -> float:
        return abs(self.ctx.position.qty)

    async def _candles(self, limit: int):
        return await self.ctx.candles(self.ctx.config.get("timeframe", "1h"), limit)

    def _roll_day(self):
        today = now_ms() // 86400000
        if today != self.day:
            self.day = today
            self.trades_today = 0

    def _confirm_ok(self, closes: list, direction: str) -> bool:
        if not self.cfg.confirm_ema_period:
            return True
        if len(closes) < self.cfg.confirm_ema_period:
            return True
        e = ema(closes, self.cfg.confirm_ema_period)[-1]
        last = closes[-1]
        if direction == "long":
            return last > e
        return last < e

    def _volume_ok(self, candles: list) -> bool:
        if not self.cfg.volume_factor:
            return True
        vols = [c[5] or 0 for c in candles[-self.cfg.volume_ma_period - 1:-1]]
        if len(vols) < self.cfg.volume_ma_period:
            return True
        ma = sum(vols) / len(vols)
        return (candles[-1][5] or 0) >= ma * self.cfg.volume_factor

    def _qty(self, price: float) -> float:
        return (self.cfg.total_budget_quote * self.cfg.position_pct / 100.0) / price if price else 0.0

    async def start(self):
        self.ctx.log(f"vwap started: mode={self.cfg.mode}, window={self.cfg.vwap_window or 'anchored'}, "
                     f"dev={self.cfg.deviation_pct}%")

    async def on_tick(self, price: float, candles: list):
        if not price:
            return
        self._roll_day()
        candles = candles or await self._candles(max(self.cfg.vwap_window + 10, 60))
        if not candles or len(candles) < 5:
            return
        vwaps = rolling_vwap(candles, self.cfg.vwap_window or 0)
        self.current_vwap = vwaps[-1]
        if self.position_qty() > 0:
            await self._manage(price, candles, vwaps)
            return
        if now_ms() < self.cooldown_until or self._pending_exit:
            return
        if self.trades_today >= self.cfg.max_trades_per_day:
            return
        if self.cfg.mode == "mean_reversion":
            await self._check_mean_reversion(price, candles, vwaps)
        else:
            await self._check_reclaim(price, candles, vwaps)

    async def _check_mean_reversion(self, price: float, candles: list, vwaps: list):
        vwap = vwaps[-1]
        closes = [c[4] for c in candles]
        if price <= vwap * (1 - self.cfg.deviation_pct / 100.0):
            await self._enter("long", price)
        elif self.cfg.allow_short and price >= vwap * (1 + self.cfg.deviation_pct / 100.0):
            await self._enter("short", price)

    async def _check_reclaim(self, price: float, candles: list, vwaps: list):
        if len(candles) < 3:
            return
        prev_close, last_close = candles[-2][4], candles[-1][4]
        prev_vwap, last_vwap = vwaps[-2], vwaps[-1]
        up = prev_close < prev_vwap and last_close > last_vwap
        down = prev_close > prev_vwap and last_close < last_vwap
        if up and self._confirm_ok([c[4] for c in candles], "long") and self._volume_ok(candles):
            await self._enter("long", price)
        elif down and self.cfg.allow_short and \
                self._confirm_ok([c[4] for c in candles], "short") and self._volume_ok(candles):
            await self._enter("short", price)

    async def _enter(self, direction: str, price: float):
        qty = self._qty(price)
        if qty <= 0:
            return
        side = "buy" if direction == "long" else "sell"
        try:
            await self.ctx.orders.place(self.ctx.symbol, side, "market", qty, None,
                                        role="vwap_entry")
        except Exception as e:
            self.ctx.log(f"entry failed: {e}", "warning")
            self.cooldown_until = now_ms() + 10000
            return
        self.direction = direction
        self.entry_price = price
        self.entry_qty = qty
        self.opened_at = now_ms()
        self.trades_today += 1
        self.ctx.emit("vwap_entry", {"direction": direction, "price": price, "qty": qty,
                                     "vwap": self.current_vwap})
        self.ctx.log(f"vwap {direction} entry @ {price:.6g} (vwap {self.current_vwap:.6g})")

    async def _manage(self, price: float, candles: list, vwaps: list):
        c = self.cfg
        close_hit = ""
        if self.direction == "long":
            if price <= self.entry_price * (1 - c.stop_loss_pct / 100.0):
                close_hit = "stop loss"
            elif price >= self.entry_price * (1 + c.take_profit_pct / 100.0):
                close_hit = "take profit"
            elif self.cfg.mode == "mean_reversion" and self.current_vwap and price >= self.current_vwap:
                close_hit = "vwap touch"
        else:
            if price >= self.entry_price * (1 + c.stop_loss_pct / 100.0):
                close_hit = "stop loss"
            elif price <= self.entry_price * (1 - c.take_profit_pct / 100.0):
                close_hit = "take profit"
            elif self.cfg.mode == "mean_reversion" and self.current_vwap and price <= self.current_vwap:
                close_hit = "vwap touch"
        if not close_hit and self.cfg.mode == "reclaim":
            if self.direction == "long" and price < self.current_vwap * 0.999:
                close_hit = "lost vwap"
            elif self.direction == "short" and price > self.current_vwap * 1.001:
                close_hit = "lost vwap"
        if close_hit:
            await self._exit(price, close_hit)

    async def _exit(self, price: float, reason: str):
        self._pending_exit = True
        try:
            side = "sell" if self.direction == "long" else "buy"
            await self.ctx.orders.place(self.ctx.symbol, side, "market", self.entry_qty,
                                        None, role="vwap_exit")
        except Exception as e:
            self.ctx.log(f"exit failed: {e}", "error")
            self._pending_exit = False
            return
        pnl = (price - self.entry_price) * self.entry_qty * (1 if self.direction == "long" else -1)
        fee_est = price * self.entry_qty * self.ctx.fee_pct / 100.0 * 2
        pnl -= fee_est
        self.realized_pnl += pnl
        self.ctx.risk.on_realized(pnl)
        if pnl >= 0:
            self.wins += 1
        else:
            self.losses += 1
        self.ctx.emit("vwap_exit", {"price": price, "pnl": pnl, "reason": reason})
        self.ctx.log(f"vwap exit ({reason}): pnl={pnl:.2f}")
        self.entry_price = 0.0
        self.entry_qty = 0.0
        self._pending_exit = False
        self.cooldown_until = now_ms() + self.cfg.cooldown_minutes * 60000

    async def on_fill(self, fill: dict):
        self.ctx.position.apply_fill(fill["side"], fill["price"], fill["qty"], fill.get("fee", 0))
        await self.ctx.record_fill(fill, fill.get("role") or "manual")

    async def stop(self, reason: str = ""):
        if self.position_qty() > 0 and self.entry_qty > 0:
            try:
                side = "sell" if self.direction == "long" else "buy"
                await self.ctx.orders.place(self.ctx.symbol, side, "market", self.entry_qty,
                                            None, role="vwap_exit")
            except Exception as e:
                self.ctx.log(f"close on stop failed: {e}", "error")
        await self.ctx.orders.cancel_all(self.ctx.symbol)
        self.ctx.log(f"vwap stopped: {reason}", "warning")
        self.ctx.emit("strategy_stopped", {"strategy": "vwap", "reason": reason})

    async def action_close_position(self):
        if self.position_qty() > 0:
            t = await self.ctx.ticker()
            await self._exit(t.mid() or t.last, "manual close")

    def actions(self) -> dict:
        return {"close_position": self.action_close_position}

    def state(self) -> dict:
        return {"strategy": "vwap", "mode": self.cfg.mode, "vwap": round(self.current_vwap, 4),
                "in_position": self.position_qty() > 0,
                "direction": self.direction if self.position_qty() > 0 else "",
                "entry_price": self.entry_price, "qty": self.entry_qty,
                "realized_pnl": round(self.realized_pnl, 4),
                "wins": self.wins, "losses": self.losses,
                "trades_today": self.trades_today}
