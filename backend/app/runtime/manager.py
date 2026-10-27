import asyncio

from ..core.utils import now_ms
from ..core.models import BotConfig
from .bot import BotRuntime


class BotManager:
    def __init__(self, app):
        self.app = app
        self.runtimes: dict = {}

    def create_bot(self, cfg: dict) -> dict:
        try:
            parsed = BotConfig(**cfg)
        except Exception as e:
            raise ValueError(f"invalid bot config: {str(e)[:400]}")
        account = self.app.get_account(parsed.account_id)
        if not account:
            raise ValueError("account not found")
        if parsed.type == "grid" and not parsed.grid:
            raise ValueError("grid settings required")
        if parsed.type == "dca" and not parsed.dca:
            raise ValueError("dca settings required")
        if parsed.type == "combo" and (not parsed.grid or not parsed.dca):
            raise ValueError("combo requires grid and dca settings")
        if parsed.type == "momentum" and not parsed.momentum:
            raise ValueError("momentum settings required")
        if parsed.type == "vwap" and not parsed.vwap:
            raise ValueError("vwap settings required")
        if parsed.type == "adaptive" and not parsed.adaptive:
            raise ValueError("adaptive settings required")
        bot_id = parsed.id or ("b" + now_ms().hex if False else "b" + str(now_ms())[-8:] + "x")
        data = parsed.model_dump()
        data["id"] = bot_id
        self.app.storage.save_bot(bot_id, parsed.name, parsed.type, parsed.account_id,
                                  parsed.symbol, data, {}, "stopped")
        runtime = BotRuntime(bot_id, data, self.app)
        self.runtimes[bot_id] = runtime
        self.app.storage.insert_event("info", bot_id, "bot", f"bot created: {parsed.name} "
                                      f"({parsed.type} {parsed.symbol})")
        return runtime.state()

    def update_bot(self, bot_id: str, cfg: dict) -> dict:
        rt = self.runtimes.get(bot_id)
        if rt and rt.status.value in ("running", "starting"):
            raise ValueError("stop bot before updating")
        current = self.app.storage.get_bot(bot_id)
        if not current:
            raise ValueError("bot not found")
        parsed = BotConfig(**{**current["config"], **cfg, "id": bot_id})
        self.app.storage.save_bot(bot_id, parsed.name, parsed.type, parsed.account_id,
                                  parsed.symbol, parsed.model_dump(), current["state"], "stopped")
        if rt:
            rt.config = parsed.model_dump()
        return self.get_state(bot_id)

    def delete_bot(self, bot_id: str):
        rt = self.runtimes.pop(bot_id, None)
        if rt and rt.status.value in ("running", "starting", "paused"):
            asyncio.get_event_loop().create_task(rt.stop("deleted"))
        self.app.storage.delete_bot(bot_id)

    async def start_bot(self, bot_id: str):
        rt = self.runtimes.get(bot_id) or self._load_runtime(bot_id)
        await rt.start()
        return rt.state()

    async def stop_bot(self, bot_id: str):
        rt = self.runtimes.get(bot_id)
        if not rt:
            raise ValueError("bot not found")
        await rt.stop("user")
        return rt.state()

    async def pause_bot(self, bot_id: str):
        rt = self.runtimes.get(bot_id)
        if not rt:
            raise ValueError("bot not found")
        await rt.pause()
        return rt.state()

    async def resume_bot(self, bot_id: str):
        rt = self.runtimes.get(bot_id)
        if not rt:
            raise ValueError("bot not found")
        await rt.resume()
        return rt.state()

    def _load_runtime(self, bot_id: str) -> BotRuntime:
        row = self.app.storage.get_bot(bot_id)
        if not row:
            raise ValueError("bot not found")
        rt = BotRuntime(bot_id, row["config"], self.app)
        self.runtimes[bot_id] = rt
        return rt

    def get_runtime(self, bot_id: str) -> BotRuntime:
        return self.runtimes.get(bot_id)

    def get_state(self, bot_id: str) -> dict:
        rt = self.runtimes.get(bot_id)
        if rt:
            return rt.state()
        row = self.app.storage.get_bot(bot_id)
        if not row:
            raise ValueError("bot not found")
        return {"id": bot_id, "config": row["config"], "status": row["status"], "error": "",
                "paper": True, "position": {}, "risk": {}, "strategy_state": {}, "open_orders": [],
                "feed": {"exchange": "", "source": "", "status": ""}}

    def list_bots(self) -> list:
        out = []
        for row in self.app.storage.list_bots():
            rt = self.runtimes.get(row["id"])
            if rt:
                st = rt.state()
            else:
                st = {"id": row["id"], "config": row["config"], "status": row["status"],
                      "error": "", "position": {}, "risk": {}, "strategy_state": {},
                      "open_orders": [], "paper": True, "price": 0,
                      "feed": {"exchange": "", "source": "", "status": ""}}
            out.append(st)
        return out

    async def run_action(self, bot_id: str, action: str, args: dict = None):
        rt = self.runtimes.get(bot_id)
        if not rt:
            raise ValueError("bot not running")
        return await rt.run_action(action, args)

    async def restore_bots(self):
        for row in self.app.storage.list_bots():
            if row["status"] in ("running", "starting", "paused"):
                try:
                    rt = self._load_runtime(row["id"])
                    await rt.start()
                    if row["status"] == "paused":
                        rt._paused_flag = True
                        rt.status = type(rt.status).PAUSED
                except Exception as e:
                    self.app.storage.insert_event("error", row["id"], "runtime",
                                                  f"restore failed: {e}")

    async def close(self):
        for rt in list(self.runtimes.values()):
            try:
                await rt.stop("shutdown")
            except Exception:
                pass
