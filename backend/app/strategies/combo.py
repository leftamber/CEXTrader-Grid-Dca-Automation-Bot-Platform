from ..core.models import GridConfig, DcaConfig, ComboConfig
from .grid import GridStrategy
from .dca import DcaStrategy
from .base import Strategy, BotContext


class ComboStrategy(Strategy):
    name = "combo"

    def __init__(self, ctx: BotContext, params: dict):
        super().__init__(ctx, params)
        self.combo = ComboConfig(**params.get("combo", {}))
        grid_cfg = GridConfig(**params["grid"])
        dca_cfg = DcaConfig(**params["dca"])
        gs = self.combo.grid_share_pct / 100.0
        grid_cfg.total_budget_quote = grid_cfg.total_budget_quote * gs
        dca_cfg.total_budget_quote = dca_cfg.total_budget_quote * (1 - gs)
        gctx = _SubContext(ctx, budget_cap=grid_cfg.total_budget_quote, leg="grid")
        dctx = _SubContext(ctx, budget_cap=dca_cfg.total_budget_quote, leg="dca")
        self.grid = GridStrategy(gctx, grid_cfg.model_dump())
        self.dca = DcaStrategy(dctx, dca_cfg.model_dump())

    async def start(self):
        await self.grid.start()
        await self.dca.start()

    async def on_tick(self, price, candles):
        await self.grid.on_tick(price, candles)
        await self.dca.on_tick(price, candles)

    async def on_fill(self, fill):
        role = fill.get("role", "")
        if role.startswith("grid"):
            await self.grid.on_fill(fill)
        else:
            await self.dca.on_fill(fill)

    async def stop(self, reason=""):
        await self.grid.stop(reason)
        await self.dca.stop(reason)

    def actions(self) -> dict:
        return {**{f"grid_{k}": v for k, v in self.grid.actions().items()},
                **{f"dca_{k}": v for k, v in self.dca.actions().items()}}

    def state(self) -> dict:
        return {"strategy": "combo", "grid": self.grid.state(), "dca": self.dca.state(),
                "realized_pnl": round(self.grid.realized_pnl + self.dca.realized_pnl, 4)}


class _SubContext(BotContext):
    def __init__(self, parent: BotContext, budget_cap: float, leg: str):
        self.__dict__["_parent"] = parent
        self._budget_cap = budget_cap
        self._leg = leg

    def __getattr__(self, name):
        return getattr(self._parent, name)

    def log(self, message, level="info", data=None):
        self._parent.log(f"[{self._leg}] {message}", level, data)
