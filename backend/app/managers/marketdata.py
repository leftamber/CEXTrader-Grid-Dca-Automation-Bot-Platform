import asyncio

from ..exchanges.base import Ticker, tf_seconds, FEED_FALLBACK, NON_FALLBACK
from ..core.utils import now_ms


class MarketData:
    def __init__(self, bus, storage=None):
        self.bus = bus
        self.storage = storage
        self.public: dict = {}
        self._candles: dict = {}
        self._tickers: dict = {}
        self._pollers: dict = {}
        self._poll_refs: dict = {}
        self._markets: dict = {}
        self._failed: dict = {}
        self._preferred: dict = {}
        self._lock = asyncio.Lock()

    def _fallback_chain(self, exchange_id: str) -> list:
        chain = [exchange_id]
        for ex in FEED_FALLBACK:
            if ex not in chain and ex not in NON_FALLBACK:
                chain.append(ex)
        return chain

    def public_adapter(self, exchange_id: str, symbol: str = None):
        key = exchange_id
        if symbol and ":" in symbol:
            key = exchange_id + "_perp"
        if key not in self.public:
            from ..exchanges import create_public_adapter
            self.public[key] = create_public_adapter(exchange_id, perp=bool(symbol and ":" in symbol))
        return self.public[key]

    def _mark_failed(self, exchange_id: str):
        self._failed[exchange_id] = now_ms()

    def _mark_ok(self, exchange_id: str):
        self._failed.pop(exchange_id, None)
        self._preferred[exchange_id] = exchange_id

    def is_failed(self, exchange_id: str) -> bool:
        ts = self._failed.get(exchange_id)
        return ts is not None and now_ms() - ts < 120000

    def preferred_source(self, exchange_id: str) -> str:
        """The exchange actually serving data for the requested feed."""
        return self._preferred.get(exchange_id, exchange_id)

    async def _load_markets_retry(self, ad):
        if ad._markets:
            return True
        try:
            await ad.load_markets()
            return bool(ad._markets)
        except Exception:
            await asyncio.sleep(1.0)
        try:
            await ad.load_markets()
            return bool(ad._markets)
        except Exception:
            return False

    async def ensure_markets(self, exchange_id: str, symbol: str = None):
        """Load markets for exchange_id, falling back to other public feeds."""
        chain = self._fallback_chain(exchange_id)
        preferred = self._preferred.get(exchange_id)
        order = ([preferred] if preferred and preferred in chain else []) + \
                [e for e in chain if e != preferred]
        last_err = None
        for ex in order:
            if self.is_failed(ex) and ex != exchange_id:
                continue
            ad = self.public_adapter(ex, symbol)
            if await self._load_markets_retry(ad):
                self._mark_ok(ex)
                self._preferred.setdefault(exchange_id, ex)
                return ad
            self._mark_failed(ex)
            last_err = ex
        raise RuntimeError(f"no reachable market data source (tried: {', '.join(order)})")

    async def candles(self, exchange_id: str, symbol: str, timeframe: str, limit: int = 300,
                      since: int = 0) -> list:
        key = (exchange_id, symbol, timeframe, limit)
        cached = self._candles.get(key)
        interval = max(tf_seconds(timeframe), 10)
        if cached and not since and now_ms() - cached["ts"] < interval * 1000 * 0.5:
            return cached["data"]
        try:
            ad = await self.ensure_markets(exchange_id, symbol)
            data = await ad.ohlcv(symbol, timeframe, limit, since)
            if data:
                self._candles[key] = {"ts": now_ms(), "data": data}
                self._mark_ok(self.preferred_source(exchange_id))
            elif cached:
                return cached["data"]
            return data
        except Exception:
            if cached:
                return cached["data"]
            return []

    async def ticker(self, exchange_id: str, symbol: str) -> Ticker:
        key = (exchange_id, symbol)
        t = self._tickers.get(key)
        if t and now_ms() - t.ts < 5000:
            return t
        try:
            ad = await self.ensure_markets(exchange_id, symbol)
            t = await ad.ticker(symbol)
            if t.last or t.bid:
                self._tickers[key] = t
                self._mark_ok(self.preferred_source(exchange_id))
                return t
            raise RuntimeError("empty ticker")
        except Exception:
            if t:
                return t
            raise

    def cached_ticker(self, exchange_id: str, symbol: str):
        return self._tickers.get((exchange_id, symbol))

    async def subscribe_ticker(self, exchange_id: str, symbol: str, interval_s: float = 3.0):
        key = (exchange_id, symbol)
        if key in self._pollers:
            return
        try:
            await self.ensure_markets(exchange_id, symbol)
        except Exception:
            pass

        async def poll():
            fails = 0
            while True:
                try:
                    t = await self.ticker(exchange_id, symbol)
                    if t.last or t.bid:
                        src = self.preferred_source(exchange_id)
                        self.bus.publish("price", {"exchange": exchange_id, "symbol": symbol,
                                                   "source": src, "bid": t.bid, "ask": t.ask,
                                                   "last": t.last, "high": t.high, "low": t.low,
                                                   "ts": t.ts or now_ms()})
                        fails = 0
                    else:
                        fails += 1
                except asyncio.CancelledError:
                    raise
                except Exception:
                    fails += 1
                await asyncio.sleep(min(interval_s * (1 + min(fails, 4)), interval_s * 5))

        self._pollers[key] = asyncio.create_task(poll())
        self._poll_refs[key] = 1

    def unsubscribe_ticker(self, exchange_id: str, symbol: str):
        key = (exchange_id, symbol)
        refs = self._poll_refs.get(key, 0) - 1
        if refs > 0:
            self._poll_refs[key] = refs
            return
        self._poll_refs.pop(key, None)
        task = self._pollers.pop(key, None)
        if task:
            task.cancel()

    def price_map(self) -> dict:
        out = {}
        for (ex, sym), t in self._tickers.items():
            p = t.mid() or t.last
            if p and sym not in out:
                out[sym] = p
        return out

    async def feed_health(self, exchange_id: str = None) -> dict:
        """Probe the fallback chain and report which sources answer."""
        chain = [exchange_id] if exchange_id else []
        chain += [e for e in self._fallback_chain(exchange_id or "binance") if e not in chain]
        out = {}
        for ex in chain[:6]:
            if ex in NON_FALLBACK:
                continue
            t0 = now_ms()
            try:
                ad = self.public_adapter(ex)
                if not await self._load_markets_retry(ad):
                    raise RuntimeError("load failed")
                out[ex] = {"ok": True, "ms": now_ms() - t0, "markets": len(ad.symbols())}
            except Exception as e:
                out[ex] = {"ok": False, "ms": now_ms() - t0, "error": str(e)[:120]}
        return out

    async def close(self):
        for task in self._pollers.values():
            task.cancel()
        for ad in self.public.values():
            try:
                await ad.client.close()
            except Exception:
                pass