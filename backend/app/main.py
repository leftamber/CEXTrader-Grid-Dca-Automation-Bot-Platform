import asyncio
import os
import logging
import sys

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .core.storage import Storage
from .core.utils import EventBus, now_ms
from .managers.marketdata import MarketData
from .managers.orderbook import OrderBookManager
from .managers.balance import BalanceTracker
from .runtime.manager import BotManager
from .api.routes import router as api_router
from .api.ws import WsHub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.abspath(os.path.join(ROOT, "..", "frontend"))


class AppState:
    def __init__(self):
        data_dir = os.path.join(ROOT, "..", "data")
        os.makedirs(data_dir, exist_ok=True)
        self.storage = Storage(os.path.join(data_dir, "trader.db"))
        self.bus = EventBus()
        self.market_data = MarketData(self.bus, self.storage)
        self.orderbooks = OrderBookManager(self.bus, self.market_data)
        self.balances = BalanceTracker(self.storage, self.bus)
        self.adapters: dict = {}
        self.bot_manager = BotManager(self)
        self.hub = WsHub()
        self.price_sub = self.bus.on("price", self._on_price)
        self.stream_sub = self.bus.on("*", self._on_stream)

    def _on_price(self, topic, payload):
        exchange = payload.get("exchange")
        symbol = payload.get("symbol")
        for ad in self.adapters.values():
            if getattr(ad, "is_paper", False) and ad.underlying == exchange:
                from .exchanges.base import Ticker
                ad.sync_ticker(symbol, Ticker(symbol=symbol, bid=payload.get("bid", 0),
                                              ask=payload.get("ask", 0), last=payload.get("last", 0),
                                              ts=payload.get("ts", 0)))

    def _on_stream(self, topic, payload):
        if topic in ("bot_event", "log", "price", "equity", "orderbook"):
            asyncio.get_event_loop().create_task(self.hub.broadcast(topic, payload))

    def get_account(self, account_id: str):
        if not account_id:
            return None
        for a in self.storage.list_accounts():
            if a["id"] == account_id:
                return a
        return None

    def get_adapter(self, account_id: str):
        if account_id in self.adapters:
            return self.adapters[account_id]
        cfg = self.get_account(account_id)
        if not cfg:
            raise RuntimeError(f"account {account_id} not found")
        from .exchanges import create_adapter
        ad = create_adapter(cfg, bus=self.bus, storage=self.storage)
        self.adapters[account_id] = ad
        return ad

    def register_account(self, cfg: dict):
        old = self.adapters.pop(cfg["id"], None)
        if old:
            try:
                asyncio.get_event_loop().create_task(old.close())
            except RuntimeError:
                pass
        if cfg.get("paper"):
            from .exchanges import create_adapter
            ad = create_adapter(cfg, bus=self.bus, storage=self.storage)
            self.adapters[cfg["id"]] = ad
            try:
                asyncio.get_event_loop().create_task(ad.start_persistence())
            except RuntimeError:
                pass

    def remove_adapter(self, account_id: str):
        ad = self.adapters.pop(account_id, None)
        if ad:
            asyncio.get_event_loop().create_task(ad.close())


STATE = None


def get_state() -> AppState:
    global STATE
    if STATE is None:
        STATE = AppState()
    return STATE


app = FastAPI(title="CEXTrader", version="1.0.0")
app.include_router(api_router)


@app.on_event("startup")
async def startup():
    global STATE
    STATE = AppState()
    for cfg in STATE.storage.list_accounts():
        try:
            STATE.register_account(cfg)
        except Exception as e:
            log.error("account %s adapter failed: %s", cfg.get("id"), e)
        if cfg.get("paper"):
            ad = STATE.adapters.get(cfg["id"])
            if ad:
                STATE.balances.register_start_equity(
                    cfg["id"], sum(float(v) for v in (cfg.get("initial_balances") or {}).values()))
                STATE.balances.start_sampler(cfg["id"], ad, STATE.market_data.price_map)
    await STATE.bot_manager.restore_bots()
    log.info("CEXTrader backend ready")


@app.on_event("shutdown")
async def shutdown():
    if STATE:
        await STATE.bot_manager.close()
        await STATE.market_data.close()
        await STATE.orderbooks.close()
        await STATE.balances.close()
        for ad in list(STATE.adapters.values()):
            try:
                await ad.close()
            except Exception:
                pass


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await get_state().hub.handler(ws)


if os.path.isdir(FRONTEND_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIR, "assets")), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"),
                            headers={"Cache-Control": "no-store"})
