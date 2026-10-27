from ..core.utils import now_ms, ema, rsi as rsi_calc
from ..core.models import MomentumConfig
from .base import Strategy, BotContext


class MomentumStrategy(Strategy):
    name = "momentum"

    def __init__(self, ctx: BotContext, params: dict):
        super().__init__(ctx, params)
        self.cfg = MomentumConfig(**params)
        self.entry_price = 0.0
        self.entry_qty = 0.0
        self.direction = "long"
        self.peak = 0.0
        self.opened_at = 0
        self.cooldown_until = 0
        self.trades_won = 0
        self.trades_lost = 0
        self.realized_pnl = 0.0
        self.last_signal = ""
        self._pending_exit = False

    def position_qty(self) -> float:
        return abs(self.ctx.position.qty)

    async def _candles(self, limit: int):
        return await self.ctx.candles(self.ctx.config.get("timeframe", "1h"), limit)

    def _signal(self, candles: list) -> str:
        if len(candles) < max(self.cfg.fast_ema, self.cfg.slow_ema) + 3:
            return ""
        closes = [c[4] for c in candles]
        fast = ema(closes, self.cfg.fast_ema)
        slow = ema(closes, self.cfg.slow_ema)
        f1, f2, s1, s2 = fast[-2], fast[-1], slow[-2], slow[-1]
        if f2 <= s2 and f1 > s1:
            return "cross_up"
        if f2 >= s2 and f1 < s1:
            return "cross_down"
        return ""

    def _volume_ok(self, candles: list) -> bool:
        if not self.cfg.volume_filter_enabled:
            return True
        vols = [c[5] or 0 for c in candles[-self.cfg.volume_ma_period - 1:-1]]
        if len(vols) < self.cfg.volume_ma_period:
            return True
        ma = sum(vols) / len(vols)
        return (candles[-1][5] or 0) >= ma * self.cfg.volume_factor

    def _rsi_ok(self, candles: list, direction: str) -> bool:
        if not self.cfg.rsi_filter_enabled:
            return True
        r = rsi_calc([c[4] for c in candles], self.cfg.rsi_period)
        if direction == "long":
            return r <= self.cfg.rsi_long_max
        return r >= self.cfg.rsi_short_min

    def _qty(self, price: float) -> float:
        return (self.cfg.total_budget_quote * self.cfg.position_pct / 100.0) / price if price else 0.0

    async def start(self):
        self.ctx.log(f"momentum started: EMA {self.cfg.fast_ema}/{self.cfg.slow_ema}, "
                     f"TP {self.cfg.take_profit_pct}% SL {self.cfg.stop_loss_pct}%, "
                     f"short={'on' if self.cfg.allow_short else 'off'}")

    async def on_tick(self, price: float, candles: list):
        if not price:
            return
        candles = candles or await self._candles(150)
        if not candles:
            return
        if self.position_qty() > 0:
            await self._manage_position(price, candles)
            return
        if now_ms() < self.cooldown_until or self._pending_exit:
            return
        signal = self._signal(candles)
        if not signal:
            return
        self.last_signal = signal
        if signal == "cross_up":
            await self._enter("long", price, candles)
        elif signal == "cross_down" and self.cfg.allow_short:
            await self._enter("short", price, candles)

    async def _enter(self, direction: str, price: float, candles: list):
        if not self._volume_ok(candles) or not self._rsi_ok(candles, direction):
            return
        qty = self._qty(price)
        if qty <= 0:
            return
        side = "buy" if direction == "long" else "sell"
        try:
            await self.ctx.orders.place(self.ctx.symbol, side, "market", qty, None,
                                        role=f"momentum_entry")
        except Exception as e:
            self.ctx.log(f"entry failed: {e}", "warning")
            self.cooldown_until = now_ms() + 10000
            return
        self.direction = direction
        self.entry_price = price
        self.entry_qty = qty
        self.peak = price
        self.opened_at = now_ms()
        self.ctx.emit("momentum_entry", {"direction": direction, "price": price, "qty": qty})
        self.ctx.log(f"momentum {direction} entry @ {price:.6g}", "info")

    async def _manage_position(self, price: float, candles: list):
        c = self.cfg
        close_hit = ""
        if self.direction == "long":
            self.peak = max(self.peak, price)
            if price <= self.entry_price * (1 - c.stop_loss_pct / 100.0):
                close_hit = "stop loss"
            elif price >= self.entry_price * (1 + c.take_profit_pct / 100.0):
                if c.trailing_enabled:
                    if price <= self.peak * (1 - c.trailing_dev_pct / 100.0):
                        close_hit = "trailing tp"
                else:
                    close_hit = "take profit"
        else:
            self.peak = min(self.peak, price)
            if price >= self.entry_price * (1 + c.stop_loss_pct / 100.0):
                close_hit = "stop loss"
            elif price <= self.entry_price * (1 - c.take_profit_pct / 100.0):
                if c.trailing_enabled:
                    if price >= self.peak * (1 + c.trailing_dev_pct / 100.0):
                        close_hit = "trailing tp"
                else:
                    close_hit = "take profit"
        if not close_hit and c.exit_on_cross:
            sig = self._signal(candles)
            if (self.direction == "long" and sig == "cross_down") or \
               (self.direction == "short" and sig == "cross_up"):
                close_hit = "cross exit"
        if not close_hit and c.max_hold_hours and \
                now_ms() - self.opened_at > c.max_hold_hours * 3600000:
            close_hit = "max hold"
        if close_hit:
            await self._exit(price, close_hit)

    async def _exit(self, price: float, reason: str):
        self._pending_exit = True
        try:
            side = "sell" if self.direction == "long" else "buy"
            await self.ctx.orders.place(self.ctx.symbol, side, "market", self.entry_qty,
                                        None, role="momentum_exit")
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
            self.trades_won += 1
        else:
            self.trades_lost += 1
        self.ctx.emit("momentum_exit", {"price": price, "pnl": pnl, "reason": reason})
        self.ctx.log(f"momentum exit ({reason}): pnl={pnl:.2f}", "info")
        self.entry_price = 0.0
        self.entry_qty = 0.0
        self._pending_exit = False
        self.cooldown_until = now_ms() + self.cfg.cooldown_minutes * 60000

    async def on_fill(self, fill: dict):
        role = fill.get("role", "")
        if role == "momentum_entry":
            self.ctx.position.apply_fill(fill["side"], fill["price"], fill["qty"], fill.get("fee", 0))
            await self.ctx.record_fill(fill, role)
        elif role == "momentum_exit":
            self.ctx.position.apply_fill(fill["side"], fill["price"], fill["qty"], fill.get("fee", 0))
            await self.ctx.record_fill(fill, role)
        else:
            self.ctx.position.apply_fill(fill["side"], fill["price"], fill["qty"], fill.get("fee", 0))
            await self.ctx.record_fill(fill, role or "manual")

    async def stop(self, reason: str = ""):
        if self.position_qty() > 0 and self.entry_qty > 0:
            try:
                side = "sell" if self.direction == "long" else "buy"
                await self.ctx.orders.place(self.ctx.symbol, side, "market", self.entry_qty,
                                            None, role="momentum_exit")
            except Exception as e:
                self.ctx.log(f"close on stop failed: {e}", "error")
        await self.ctx.orders.cancel_all(self.ctx.symbol)
        self.ctx.log(f"momentum stopped: {reason}", "warning")
        self.ctx.emit("strategy_stopped", {"strategy": "momentum", "reason": reason})

    async def action_close_position(self):
        if self.position_qty() > 0:
            t = await self.ctx.ticker()
            await self._exit(t.mid() or t.last, "manual close")

    def actions(self) -> dict:
        return {"close_position": self.action_close_position}

    def state(self) -> dict:
        return {"strategy": "momentum", "in_position": self.position_qty() > 0,
                "direction": self.direction if self.position_qty() > 0 else "",
                "entry_price": self.entry_price, "qty": self.entry_qty,
                "peak": self.peak, "realized_pnl": round(self.realized_pnl, 4),
                "wins": self.trades_won, "losses": self.trades_lost,
                "last_signal": self.last_signal,
                "cooldown_until": self.cooldown_until}
