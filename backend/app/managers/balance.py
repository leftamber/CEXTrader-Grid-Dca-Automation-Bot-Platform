import asyncio

from ..core.utils import now_ms


class BalanceTracker:
    def __init__(self, storage, bus):
        self.storage = storage
        self.bus = bus
        self.cached: dict = {}
        self._samplers: dict = {}
        self._equity_start_day: dict = {}
        self.day_equity: dict = {}

    async def snapshot(self, account_id: str, adapter, prices: dict, force: bool = False):
        cached = self.cached.get(account_id)
        if cached and not force and now_ms() - cached["ts"] < 10000:
            return cached
        try:
            if getattr(adapter, "is_paper", False) and hasattr(adapter, "equity"):
                raw = await adapter.fetch_balances()
                eq = adapter.equity(prices or {})
                self.cached[account_id] = {"ts": now_ms(), "balances": raw, "prices": prices, "equity": eq}
                return self.cached[account_id]
            raw = await adapter.fetch_balances()
            self.cached[account_id] = {"ts": now_ms(), "balances": raw, "prices": prices,
                                       "equity": self.equity_from(raw, prices)}
        except Exception:
            if cached:
                return cached
            return {"ts": 0, "balances": {}, "equity": 0}
        return self.cached[account_id]

    def equity_from(self, balances: dict, prices: dict) -> float:
        total = 0.0
        for cur, amt in balances.items():
            if cur in ("USDT", "USD", "USDC", "BUSD", "USDC:USDC"):
                total += float(amt or 0)
                continue
            px = 0.0
            for sym, p in prices.items():
                if sym.split("/")[0] == cur:
                    px = p
                    break
            if not px:
                for sym, p in prices.items():
                    if sym.startswith(cur + "/"):
                        px = p
                        break
            total += float(amt or 0) * px
        return total

    def start_sampler(self, account_id: str, adapter, prices_getter):
        if account_id in self._samplers:
            return

        async def loop():
            while True:
                try:
                    prices = prices_getter()
                    snap = await self.snapshot(account_id, adapter, prices, force=True)
                    if snap.get("equity"):
                        self.storage.insert_equity(account_id, snap["equity"])
                        today = now_ms() // 86400000
                        key = (account_id, today)
                        if key not in self._equity_start_day:
                            self._equity_start_day[key] = snap["equity"]
                        self.day_equity[account_id] = {
                            "value": snap["equity"],
                            "start": self._equity_start_day[key],
                        }
                        self.bus.publish("equity", {"account_id": account_id,
                                                    "equity": snap["equity"], "ts": now_ms()})
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                await asyncio.sleep(60)

        self._samplers[account_id] = asyncio.create_task(loop())

    def day_start_equity(self, account_id: str) -> float:
        today = now_ms() // 86400000
        return self._equity_start_day.get((account_id, today), 0.0)

    def day_change(self, account_id: str) -> float:
        d = self.day_equity.get(account_id)
        if not d or not d["start"]:
            return 0.0
        return d["value"] - d["start"]

    def register_start_equity(self, account_id: str, value: float):
        today = now_ms() // 86400000
        key = (account_id, today)
        if key not in self._equity_start_day and value:
            self._equity_start_day[key] = value

    async def close(self):
        for t in self._samplers.values():
            t.cancel()


class PositionTracker:
    def __init__(self, bot_id: str):
        self.bot_id = bot_id
        self.qty = 0.0
        self.avg_entry = 0.0
        self.realized_pnl = 0.0
        self.fees_paid = 0.0

    def apply_fill(self, side: str, price: float, qty: float, fee: float = 0.0):
        signed = qty if side == "buy" else -qty
        if side == "buy":
            if self.qty >= 0:
                total = self.avg_entry * self.qty + price * qty
                self.qty += qty
                self.avg_entry = total / self.qty if self.qty > 0 else 0.0
            else:
                self.realized_pnl += (price - self.avg_entry) * min(qty, -self.qty)
                self.qty += signed
                if self.qty > 0:
                    self.avg_entry = price
        else:
            if self.qty <= 0:
                total = self.avg_entry * abs(self.qty) + price * qty
                self.qty -= qty
                self.avg_entry = total / abs(self.qty) if self.qty < 0 else 0.0
            else:
                self.realized_pnl += (price - self.avg_entry) * min(qty, self.qty)
                self.qty += signed
                if self.qty < 0:
                    self.avg_entry = price
        self.fees_paid += fee

    def unrealized(self, price: float) -> float:
        if not price or self.qty == 0:
            return 0.0
        if self.qty > 0:
            return (price - self.avg_entry) * self.qty
        return (self.avg_entry - price) * abs(self.qty)

    def state(self, price: float = 0.0) -> dict:
        return {"qty": self.qty, "avg_entry": self.avg_entry,
                "unrealized_pnl": self.unrealized(price),
                "realized_pnl": self.realized_pnl, "fees_paid": self.fees_paid}

