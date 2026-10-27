import asyncio
import traceback

from ..core.utils import now_ms, ema, atr
from ..core.models import BotStatus
from ..managers.orders import OrderManager
from ..managers.risk import RiskManager
from ..managers.balance import PositionTracker
from ..strategies import create_strategy
from ..strategies.base import BotContext
from ..strategies.regime import RegimeDetector


class BotRuntime:
    def __init__(self, bot_id: str, config: dict, app):
        self.bot_id = bot_id
        self.config = dict(config)
        self.app = app
        self.bus = app.bus
        self.storage = app.storage
        self.market_data = app.market_data
        self.status = BotStatus.STOPPED
        self.error = ""
        self.account_cfg = app.get_account(config.get("account_id", ""))
        self.adapter = None
        self.orders: OrderManager = None
        self.risk: RiskManager = None
        self.position = PositionTracker(bot_id)
        self.strategy = None
        self.ctx = None
        self._task = None
        self._fill_sub = None
        self._started_at = 0
        self._last_fill_poll = 0
        self._processed_trade_ids: set = set()
        self._seen_fills: set = set()
        self._paused_flag = False
        self._last_price = 0.0
        self._last_price_ts = 0
        self._last_tick_err_log = 0
        self._module_state = {"trend_ok": True, "vol_ok": True, "regime_ok": True,
                              "entry_ok": True, "regime": None}
        self._trend_ema = None
        self._trend_tf = ""
        self._trend_ts = 0
        self._vol_atr_pct = None
        self._vol_ts = 0
        self._regime_ts = 0

    @property
    def paper(self) -> bool:
        return bool(self.account_cfg and self.account_cfg.get("paper", False))

    @property
    def symbol(self) -> str:
        return self.config.get("symbol", "")

    async def start(self):
        if self.status == BotStatus.RUNNING:
            return
        self.status = BotStatus.STARTING
        self.error = ""
        self.storage.update_bot(self.bot_id, status=self.status.value)
        try:
            if not self.account_cfg:
                raise RuntimeError("account not found")
            self.adapter = self.app.get_adapter(self.account_cfg["id"])
            if not self.adapter._markets:
                await self.adapter.load_markets()
            mi = self.adapter.market(self.symbol)
            self.risk = RiskManager(self.bot_id, self.config.get("risk", {}), self.bus, self.storage)
            self.orders = OrderManager(self.bot_id, self.adapter, self.storage, self.bus, self.risk)
            self._restore_open_orders()
            ctx = BotContext(self.bot_id, self.config, self.adapter, self.market_data,
                             self.orders, self.position, self.risk, self.bus, self.storage,
                             self.account_cfg)
            self.ctx = ctx
            self.strategy = create_strategy(ctx, self.config)
            self._fill_sub = self.bus.on("fill", self._on_bus_fill)
            await self.market_data.subscribe_ticker(self._feed_exchange(), self.symbol,
                                                    interval_s=max(self.config.get("tick_interval_s", 1.0), 1.0))
            await self.app.orderbooks.subscribe(self._feed_exchange(), self.symbol)
            try:
                t0 = await self._ticker()
                p0 = t0.mid() or t0.last
                if p0:
                    await self._update_modules(p0)
            except Exception:
                pass
            await self.strategy.start()
            self._started_at = now_ms()
            self.status = BotStatus.RUNNING
            self.storage.update_bot(self.bot_id, status=self.status.value)
            if self.paper and self.account_cfg:
                prices = {}
                try:
                    t = await self._ticker()
                    if t and (t.mid() or t.last):
                        prices[self.symbol] = t.mid() or t.last
                except Exception:
                    pass
                snap = await self.app.balances.snapshot(self.account_cfg["id"], self.adapter, prices, force=True)
                if snap.get("equity"):
                    self.app.balances.register_start_equity(self.account_cfg["id"], snap["equity"])
                    self.app.balances.start_sampler(self.account_cfg["id"], self.adapter,
                                                    lambda: self.app.market_data.price_map())
            self._task = asyncio.create_task(self._loop())
            self.ctx_log("bot started")
            self.bus.publish("bot_event", {"bot_id": self.bot_id, "type": "status",
                                           "status": "running", "ts": now_ms()})
        except Exception as e:
            self.status = BotStatus.ERROR
            self.error = str(e)
            self.storage.update_bot(self.bot_id, status=self.status.value)
            self.ctx_log(f"start failed: {e}", "error")
            self.ctx_log(traceback.format_exc()[-800:], "error")

    def _restore_open_orders(self):
        try:
            stored = self.storage.open_orders(self.bot_id)
            for o in stored:
                if o["status"] in ("open", "partially_filled"):
                    self.orders.open_map[o["oid"]] = o
        except Exception:
            pass

    def _feed_exchange(self) -> str:
        if self.paper:
            return self.account_cfg.get("underlying", "bybit")
        ex = self.account_cfg.get("exchange", "bybit")
        return "bybit" if ex == "paper" else ex

    def _feed_info(self) -> dict:
        feed = self._feed_exchange()
        info = {"exchange": feed, "source": self.market_data.preferred_source(feed),
                "status": "live"}
        if self.paper and self.adapter is not None:
            info["status"] = getattr(self.adapter, "feed_status", "live") or "live"
        return info

    def _on_bus_fill(self, topic, payload):
        if self.status != BotStatus.RUNNING:
            return
        cid = payload.get("client_id", "")
        if not cid.startswith(f"kb:{self.bot_id}:"):
            return
        oid = cid.split(":")[-1]
        asyncio.get_event_loop().create_task(self._handle_fill(oid, payload))

    async def _handle_fill(self, oid: str, payload: dict):
        key = f"{oid}:{payload['ts']}:{payload['qty']}:{payload['price']}:{payload['side']}"
        if key in self._seen_fills:
            return
        self._seen_fills.add(key)
        if len(self._seen_fills) > 5000:
            self._seen_fills.clear()
        o = self.orders.on_fill(oid, payload["side"], payload["price"], payload["qty"],
                                payload.get("fee", 0), payload["ts"], payload.get("taker", False))
        meta = (o or {}).get("meta", {})
        fill = {**payload, "order_id": oid, "role": meta.get("role", ""),
                "cycle_id": meta.get("cycle_id", "")}
        if self.strategy:
            await self.strategy.on_fill(fill)

    async def _poll_live_fills(self):
        if self.paper or not self.adapter:
            return
        if now_ms() - self._last_fill_poll < 5000:
            return
        self._last_fill_poll = now_ms()
        try:
            trades = await self.adapter.my_trades(self.symbol, since=self._trade_cursor)
            for t in trades:
                tid = t.get("id") or f"{t.get('ts')}{t.get('price')}{t.get('qty')}"
                if tid in self._processed_trade_ids:
                    continue
                self._processed_trade_ids.add(tid)
                if len(self._processed_trade_ids) > 5000:
                    self._processed_trade_ids.clear()
                oid = t.get("order_id", "")
                local = None
                for o in list(self.orders.open_map.values()):
                    if str(o.get("exchange_oid")) == str(oid):
                        local = o
                        break
                meta = (local or {}).get("meta", {})
                fee = t.get("fee", 0)
                fill = {"symbol": self.symbol, "side": t["side"], "price": t["price"],
                        "qty": t["qty"], "fee": fee, "fee_currency": t.get("fee_currency", ""),
                        "notional": t["price"] * t["qty"], "ts": t["ts"], "taker": t.get("taker", True),
                        "order_id": local["oid"] if local else oid,
                        "role": meta.get("role", ""), "cycle_id": meta.get("cycle_id", ""),
                        "paper": False}
                if local:
                    self.orders.on_fill(local["oid"], t["side"], t["price"], t["qty"], fee, t["ts"], True)
                if self.strategy:
                    await self.strategy.on_fill(fill)
        except Exception:
            pass

    _trade_cursor = 0

    async def _loop(self):
        interval = max(float(self.config.get("tick_interval_s", 1.0)), 0.2)
        consecutive_errors = 0
        while self.status == BotStatus.RUNNING:
            try:
                await asyncio.sleep(interval)
                if self._paused_flag:
                    continue
                t = await self._ticker()
                price = t.mid() or t.last
                if price:
                    self._last_price = price
                    self._last_price_ts = now_ms()
                await self._poll_live_fills()
                await self._reconcile()
                await self._update_modules(price)
                if not self._paused_flag and self.strategy:
                    candles = await self.market_data.candles(self._feed_exchange(), self.symbol,
                                                             self.config.get("timeframe", "5m"), 200)
                    await self.strategy.on_tick(price, candles)
                self._update_risk()
                consecutive_errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                consecutive_errors += 1
                if now_ms() - self._last_tick_err_log > 30000:
                    self._last_tick_err_log = now_ms()
                    self.ctx_log(f"tick error: {e}", "warning")
                if consecutive_errors > 120:
                    self.status = BotStatus.ERROR
                    self.error = str(e)
                    self.ctx_log("too many consecutive errors, bot halted", "error")
                    break

    async def _ticker(self):
        try:
            return await self.market_data.ticker(self._feed_exchange(), self.symbol)
        except Exception:
            t = self.market_data.cached_ticker(self._feed_exchange(), self.symbol)
            if t:
                return t
            return await self.adapter.ticker(self.symbol)

    async def _reconcile(self):
        if self.paper or not self.adapter:
            return
        if now_ms() - getattr(self, "_last_reconcile", 0) < 30000:
            return
        self._last_reconcile = now_ms()
        try:
            live = await self.adapter.open_orders(self.symbol)
            self.orders.reconcile(live)
        except Exception:
            pass

    def _entry_direction(self) -> str:
        cfg = self.config
        t = cfg.get("type", "grid")
        grid_short = (cfg.get("grid") or {}).get("grid_mode") == "short"
        dca_short = (cfg.get("dca") or {}).get("short_mode")
        if t == "dca":
            return "short" if dca_short else "long"
        if t == "grid":
            return "short" if grid_short else "long"
        return "short" if grid_short and dca_short else "long"

    def _flag(self, key: str, ok: bool, msg: str = ""):
        if self._module_state.get(key) != ok:
            self._module_state[key] = ok
            if msg:
                self.ctx_log(msg, "info" if ok else "warning")
        if self.ctx is not None:
            self.ctx.flags[key] = ok

    async def _update_modules(self, price: float):
        """Compute add-on filters (trend / volatility / regime) that gate new entries."""
        mods = self.config.get("modules") or {}
        try:
            trend = mods.get("trend_filter") or {}
            if trend.get("enabled"):
                tf = trend.get("timeframe", "1h")
                period = int(trend.get("ema_period", 50) or 50)
                if self._trend_ema is None or self._trend_tf != tf or now_ms() - self._trend_ts > 60000:
                    candles = await self.market_data.candles(self._feed_exchange(), self.symbol, tf, period + 20)
                    closes = [c[4] for c in candles]
                    e = ema(closes, period)
                    self._trend_ema = e[-1] if e else None
                    self._trend_tf = tf
                    self._trend_ts = now_ms()
                ok = True
                if self._trend_ema and price:
                    ok = (price >= self._trend_ema) if self._entry_direction() == "long" \
                        else (price <= self._trend_ema)
                self._flag("trend_ok", ok,
                           f"trend filter: new entries {'allowed' if ok else 'BLOCKED'} "
                           f"(price {price:.6g} vs EMA{period} {self._trend_ema:.6g} on {tf})")
            else:
                self._flag("trend_ok", True)

            vol = mods.get("vol_guard") or {}
            if vol.get("enabled"):
                tf = vol.get("timeframe", "1h")
                period = int(vol.get("atr_period", 14) or 14)
                threshold = float(vol.get("threshold_pct", 4) or 4)
                if self._vol_atr_pct is None or now_ms() - self._vol_ts > 60000:
                    candles = await self.market_data.candles(self._feed_exchange(), self.symbol, tf, period + 60)
                    close = candles[-1][4] if candles else 0
                    a = atr(candles, period) if len(candles) > period + 5 else 0
                    self._vol_atr_pct = a / close * 100 if a and close else None
                    self._vol_ts = now_ms()
                ok = True if self._vol_atr_pct is None else self._vol_atr_pct <= threshold
                self._flag("vol_ok", ok,
                           f"volatility guard: new entries {'allowed' if ok else 'BLOCKED'} "
                           f"(ATR {self._vol_atr_pct:.2f}% vs limit {threshold:.2f}% on {tf})")
            else:
                self._flag("vol_ok", True)

            regime = mods.get("regime") or {}
            if regime.get("enabled"):
                tf = regime.get("timeframe", "1h")
                if self._module_state.get("regime") is None or now_ms() - self._regime_ts > 60000:
                    candles = await self.market_data.candles(self._feed_exchange(), self.symbol, tf, 150)
                    det = RegimeDetector(int(regime.get("adx_period", 14) or 14),
                                         float(regime.get("adx_trend_threshold", 23) or 23),
                                         int(regime.get("atr_period", 14) or 14),
                                         float(regime.get("atr_high_pct", 4) or 4),
                                         int(regime.get("bb_period", 20) or 20))
                    self._module_state["regime"] = det.detect(candles)
                    self._regime_ts = now_ms()
                rg = self._module_state.get("regime") or {}
                r = rg.get("regime", "unknown")
                if r == "volatile":
                    ok = regime.get("volatile_action", "stand_aside") == "continue"
                elif r == "trending":
                    ok = regime.get("in_trend_action", "pause") == "continue"
                else:
                    ok = True
                self._flag("regime_ok", ok,
                           f"regime control: {r} (ADX {rg.get('adx')}), new entries "
                           f"{'allowed' if ok else 'BLOCKED'}")
            else:
                self._module_state["regime"] = None
                self._flag("regime_ok", True)

            self._flag("entry_ok", bool(self._module_state["trend_ok"] and self._module_state["vol_ok"]
                                        and self._module_state["regime_ok"]))
            if self.ctx is not None:
                self.ctx.flags["regime"] = self._module_state.get("regime")
        except Exception as e:
            if now_ms() - getattr(self, "_last_mod_err_log", 0) > 60000:
                self._last_mod_err_log = now_ms()
                self.ctx_log(f"modules check error: {e}", "warning")

    def _update_risk(self):
        price = self._last_price
        snap = self.app.balances.cached.get(self.account_cfg["id"]) if self.account_cfg else None
        equity = (snap or {}).get("equity") or 0.0
        bot_pnl = self.position.realized_pnl + self.position.unrealized(price)
        if not equity:
            equity = 10000 + bot_pnl
        self.risk.update_equity(equity)
        start_eq = self.app.balances.day_start_equity(self.account_cfg["id"]) if self.account_cfg else 0
        if self.risk.check_daily_loss(equity, start_eq):
            self.ctx_log("daily max loss reached, pausing bot", "warning")
            self.bus.publish("bot_event", {"bot_id": self.bot_id, "type": "status",
                                           "status": "paused", "ts": now_ms()})
            self._paused_flag = True
            self.status = BotStatus.PAUSED
            self.bus.publish("bot_event", {"bot_id": self.bot_id, "type": "bot_paused",
                                           "reason": "daily loss limit", "ts": now_ms()})

    async def pause(self):
        self._paused_flag = True
        self.status = BotStatus.PAUSED
        self.storage.update_bot(self.bot_id, status="paused")

    async def resume(self):
        self._paused_flag = False
        self.risk.killed = False
        self.status = BotStatus.RUNNING
        self.storage.update_bot(self.bot_id, status="running")

    async def stop(self, reason: str = "user"):
        self.status = BotStatus.STOPPED
        self.storage.update_bot(self.bot_id, status="stopped")
        if self._fill_sub:
            self.bus.off("fill", self._fill_sub)
            self._fill_sub = None
        if self.strategy:
            try:
                await self.strategy.stop(reason)
            except Exception:
                pass
            self.strategy = None
        if self.orders:
            await self.orders.cancel_all()
        if self._task:
            self._task.cancel()
            self._task = None
        self.market_data.unsubscribe_ticker(self._feed_exchange(), self.symbol)
        self.ctx_log(f"bot stopped: {reason}")
        self.bus.publish("bot_event", {"bot_id": self.bot_id, "type": "status",
                                       "status": "stopped", "ts": now_ms()})

    def ctx_log(self, msg, level="info", data=None):
        self.storage.insert_event(level, self.bot_id, "runtime", msg, data)
        self.bus.publish("log", {"ts": now_ms(), "level": level, "bot_id": self.bot_id,
                                 "message": msg, "data": data or {}})

    async def run_action(self, name: str, args: dict = None):
        if name == "cancel_order":
            await self.orders.cancel((args or {}).get("oid"))
            return {"ok": True}
        if name == "close_all":
            await self.orders.cancel_all()
            if self.position.qty > 0:
                side = "sell" if self.position.qty > 0 else "buy"
                await self.orders.place(self.symbol, side, "market", abs(self.position.qty), None,
                                        role="manual_close")
            return {"ok": True}
        if not self.strategy:
            raise RuntimeError("bot not running")
        actions = self.strategy.actions()
        if name not in actions:
            raise RuntimeError(f"unknown action {name}")
        await actions[name](**(args or {}))
        return {"ok": True}

    def state(self) -> dict:
        price = self._last_price
        open_orders = []
        for o in self.orders.open_map.values() if self.orders else []:
            open_orders.append({**o})
        return {"id": self.bot_id, "config": self.config, "status": self.status.value,
                "error": self.error, "paper": self.paper, "account": self.account_cfg,
                "price": price, "started_at": self._started_at,
                "feed": self._feed_info(),
                "price_stale": bool(self._started_at) and now_ms() - self._last_price_ts > 60000,
                "position": self.position.state(price),
                "risk": self.risk.state() if self.risk else {},
                "strategy_state": self.strategy.state() if self.strategy else {},
                "open_orders": open_orders,
                "modules": dict(self._module_state),
                "paused": self._paused_flag}
