import asyncio

from ..core.utils import now_ms

try:
    import ccxt.pro as ccxtpro
    HAS_PRO = True
except Exception:
    HAS_PRO = False


class LocalOrderBook:
    def __init__(self, symbol):
        self.symbol = symbol
        self.bids: list = []
        self.asks: list = []
        self.ts = 0

    def update(self, bids, asks):
        self.bids = [[float(p), float(q)] for p, q in (bids or [])]
        self.asks = [[float(p), float(q)] for p, q in (asks or [])]
        self.ts = now_ms()

    def mid(self) -> float:
        if self.bids and self.asks:
            return (self.bids[0][0] + self.asks[0][0]) / 2
        if self.bids:
            return self.bids[0][0]
        if self.asks:
            return self.asks[0][0]
        return 0.0

    def spread_bps(self) -> float:
        if self.bids and self.asks and self.asks[0][0]:
            return (self.asks[0][0] - self.bids[0][0]) / self.asks[0][0] * 10000
        return 0.0

    def depth_within_bps(self, side: str, bps: float) -> float:
        ref = self.bids[0][0] if side == "buy" and self.bids else \
            (self.asks[0][0] if self.asks else 0)
        if not ref:
            return 0.0
        levels = self.bids if side == "buy" else self.asks
        total, edge = 0.0, ref * (1 - bps / 10000) if side == "buy" else ref * (1 + bps / 10000)
        for price, qty in levels:
            if (side == "buy" and price < edge) or (side == "sell" and price > edge):
                break
            total += qty
        return total

    def snapshot(self) -> dict:
        return {"symbol": self.symbol, "bids": self.bids[:15], "asks": self.asks[:15],
                "mid": self.mid(), "spread_bps": self.spread_bps(), "ts": self.ts}


class OrderBookManager:
    def __init__(self, bus, market_data):
        self.bus = bus
        self.md = market_data
        self.books: dict = {}
        self._tasks: dict = {}
        self._ws_client = None
        self.poll_interval_s = 4.0

    def get(self, exchange_id: str, symbol: str) -> LocalOrderBook:
        return self.books.get((exchange_id, symbol))

    def snapshot(self, exchange_id, symbol) -> dict:
        b = self.books.get((exchange_id, symbol))
        return b.snapshot() if b else {"symbol": symbol, "bids": [], "asks": [], "mid": 0, "spread_bps": 0, "ts": 0}

    async def subscribe(self, exchange_id: str, symbol: str):
        key = (exchange_id, symbol)
        if key in self._tasks:
            return
        self.books[key] = LocalOrderBook(symbol)

        async def poll():
            while True:
                try:
                    ob = await self.md.public_adapter(exchange_id).orderbook(symbol, 25)
                    self.books[key].update(ob.bids, ob.asks)
                    self.bus.publish("orderbook", {"exchange": exchange_id,
                                                   **self.books[key].snapshot()})
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                await asyncio.sleep(self.poll_interval_s)

        self._tasks[key] = asyncio.create_task(poll())

    async def unsubscribe(self, exchange_id: str, symbol: str):
        task = self._tasks.pop((exchange_id, symbol), None)
        if task:
            task.cancel()
        self.books.pop((exchange_id, symbol), None)

    async def close(self):
        for t in self._tasks.values():
            t.cancel()
