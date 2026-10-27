from ..core.utils import now_ms


class RiskRejected(Exception):
    pass


class RiskManager:
    def __init__(self, bot_id: str, config: dict, bus, storage):
        self.bot_id = bot_id
        self.cfg = config
        self.bus = bus
        self.storage = storage
        self.position_quote = 0.0
        self.realized_today = 0.0
        self.day = now_ms() // 86400000
        self.peak_equity = 0.0
        self.killed = False

    def _roll_day(self):
        today = now_ms() // 86400000
        if today != self.day:
            self.day = today
            self.realized_today = 0.0

    def check_order(self, symbol: str, side: str, qty: float, price: float, open_orders_count: int):
        if self.killed:
            raise RiskRejected("kill switch active")
        max_orders = int(self.cfg.get("max_open_orders", 100))
        if open_orders_count >= max_orders:
            raise RiskRejected(f"max open orders reached ({max_orders})")
        max_pos = float(self.cfg.get("max_position_quote", 0))
        notional = qty * price if price else 0
        if max_pos and notional and self.position_quote + notional > max_pos:
            raise RiskRejected(f"max position {max_pos} exceeded")
        max_lev = float(self.cfg.get("max_leverage", 1))
        if max_lev < 1 and notional and self.position_quote + notional > max_pos * max_lev:
            raise RiskRejected("leverage cap exceeded")

    def add_exposure(self, notional: float):
        self.position_quote += notional

    def remove_exposure(self, notional: float):
        self.position_quote = max(0.0, self.position_quote - notional)

    def on_realized(self, pnl: float):
        self._roll_day()
        self.realized_today += pnl

    def update_equity(self, equity: float):
        if equity > self.peak_equity:
            self.peak_equity = equity
        max_dd = float(self.cfg.get("kill_switch_drawdown_pct", 0))
        if max_dd and self.peak_equity > 0 and equity < self.peak_equity * (1 - max_dd / 100):
            if not self.killed:
                self.killed = True
                self.bus.publish("bot_event", {"bot_id": self.bot_id, "type": "risk_kill_switch",
                                               "equity": equity, "peak": self.peak_equity,
                                               "ts": now_ms()})

    def check_daily_loss(self, equity: float, start_equity: float) -> bool:
        max_loss = float(self.cfg.get("daily_max_loss_quote", 0))
        if not max_loss or not start_equity:
            return False
        self._roll_day()
        return (start_equity - equity) >= max_loss

    def reset(self):
        self.position_quote = 0.0
        self.realized_today = 0.0
        self.peak_equity = 0.0
        self.killed = False

    def state(self) -> dict:
        return {"position_quote": round(self.position_quote, 2), "realized_today": round(self.realized_today, 2),
                "killed": self.killed, "peak_equity": round(self.peak_equity, 2)}
