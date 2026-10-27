from abc import ABC, abstractmethod
from ..core.utils import now_ms


class BotContext:
    def __init__(self, bot_id: str, config: dict, adapter, market_data, order_manager,
                 position, risk, bus, storage, account_cfg: dict):
        self.bot_id = bot_id
        self.config = config
        self.adapter = adapter
        self.market_data = market_data
        self.orders = order_manager
        self.position = position
        self.risk = risk
        self.bus = bus
        self.storage = storage
        self.account_cfg = account_cfg
        self.symbol = config.get("symbol", "")
        self.paper = bool(account_cfg.get("paper", False))
        # Module flags maintained by the runtime (trend / volatility / regime filters).
        self.flags = {"entry_ok": True, "trend_ok": True, "vol_ok": True, "regime_ok": True,
                      "regime": None}

    def emit(self, event_type: str, data: dict = None):
        self.bus.publish("bot_event", {"bot_id": self.bot_id, "type": event_type,
                                       **(data or {}), "ts": now_ms()})

    def log(self, message: str, level: str = "info", data: dict = None):
        self.storage.insert_event(level, self.bot_id, "bot", message, data)
        self.bus.publish("log", {"ts": now_ms(), "level": level, "bot_id": self.bot_id,
                                 "message": message, "data": data or {}})

    async def record_fill(self, fill: dict, role: str, cycle_id: str = "", pnl: float = 0.0) -> dict:
        t = {"id": self.storage.new_trade_id(), "bot_id": self.bot_id, "ts": fill["ts"],
             "symbol": fill["symbol"], "side": fill["side"], "role": role, "price": fill["price"],
             "qty": fill["qty"], "fee": fill.get("fee", 0), "fee_currency": fill.get("fee_currency", ""),
             "notional": fill.get("notional", fill["price"] * fill["qty"]),
             "cycle_id": cycle_id, "order_id": fill.get("order_id", ""), "pnl": pnl,
             "meta": {"taker": fill.get("taker", False), "paper": fill.get("paper", False)}}
        self.storage.insert_trade(t)
        self.bus.publish("bot_event", {"bot_id": self.bot_id, "type": "trade", "trade": t})
        return t

    async def ticker(self):
        exchange_id = self.account_cfg.get("exchange", "paper")
        if self.paper:
            exchange_id = self.account_cfg.get("underlying", "bybit")
        elif exchange_id == "paper":
            exchange_id = "bybit"
        return await self.market_data.ticker(exchange_id, self.symbol)

    async def candles(self, timeframe: str = None, limit: int = 200):
        exchange_id = self.account_cfg.get("exchange", "paper")
        if self.paper:
            exchange_id = self.account_cfg.get("underlying", "bybit")
        elif exchange_id == "paper":
            exchange_id = "bybit"
        return await self.market_data.candles(exchange_id, self.symbol, timeframe or self.config.get("timeframe", "5m"), limit)

    @property
    def fee_pct(self) -> float:
        try:
            mi = self.adapter.market(self.symbol)
            return (mi.taker_fee_pct + mi.maker_fee_pct) / 2
        except Exception:
            return 0.1


class Strategy(ABC):
    name = "base"

    def __init__(self, ctx: BotContext, params: dict):
        self.ctx = ctx
        self.params = params

    @abstractmethod
    async def start(self):
        ...

    @abstractmethod
    async def on_tick(self, price: float, candles: list):
        ...

    @abstractmethod
    async def on_fill(self, fill: dict):
        ...

    async def stop(self, reason: str = ""):
        pass

    def state(self) -> dict:
        return {}

    def actions(self) -> dict:
        return {}
