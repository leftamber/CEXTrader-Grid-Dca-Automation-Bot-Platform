import asyncio
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from ..core.models import AccountConfig, BotConfig, BacktestRequest
from ..core.utils import now_ms
from ..exchanges import SUPPORTED_EXCHANGES, create_adapter
from ..backtest.engine import run_backtest

router = APIRouter(prefix="/api")


class AccountIn(BaseModel):
    config: dict


class BotIn(BaseModel):
    config: dict


class ActionIn(BaseModel):
    action: str = ""
    args: dict = {}


class SettingsIn(BaseModel):
    values: dict


def app_state():
    from ..main import STATE
    return STATE


def _validation_message(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err.get("loc", []) if x not in ("config",))
        parts.append(f"{loc or 'config'}: {err.get('msg', 'invalid')}")
    return "invalid config — " + "; ".join(parts[:6])


def _clean_account_cfg(cfg: dict) -> dict:
    if not (cfg.get("name") or "").strip():
        if cfg.get("paper"):
            cfg["name"] = f"Paper {cfg.get('underlying') or 'sim'}".strip()
        else:
            cfg["name"] = f"{cfg.get('exchange') or 'exchange'} LIVE"
    return cfg


@router.get("/health")
async def health():
    return {"ok": True, "ts": now_ms()}


@router.get("/exchanges")
async def exchanges():
    return {"exchanges": SUPPORTED_EXCHANGES}


@router.get("/accounts")
async def list_accounts():
    s = app_state()
    out = []
    for a in s.storage.list_accounts():
        cfg = {k: ("***" if k in ("secret", "mnemonic") and a.get(k) else v) for k, v in a.items()}
        out.append(cfg)
    return {"accounts": out}


@router.post("/accounts")
async def create_account(body: AccountIn):
    s = app_state()
    try:
        cfg = AccountConfig(**body.config).model_dump()
    except ValidationError as e:
        raise HTTPException(400, _validation_message(e))
    _clean_account_cfg(cfg)
    if not cfg["id"]:
        cfg["id"] = "acc" + str(now_ms())[-8:]
    existing = {a["id"] for a in s.storage.list_accounts()}
    if cfg["id"] in existing:
        raise HTTPException(400, "account id exists")
    s.storage.save_account(cfg)
    try:
        s.register_account(cfg)
    except Exception as e:
        raise HTTPException(400, f"account saved but adapter failed to start: {str(e)[:200]}")
    return {"ok": True, "id": cfg["id"], "name": cfg["name"]}


@router.put("/accounts/{account_id}")
async def update_account(account_id: str, body: AccountIn):
    s = app_state()
    accounts = {a["id"]: a for a in s.storage.list_accounts()}
    if account_id not in accounts:
        raise HTTPException(404, "not found")
    old = accounts[account_id]
    try:
        cfg = AccountConfig(**body.config).model_dump()
    except ValidationError as e:
        raise HTTPException(400, _validation_message(e))
    _clean_account_cfg(cfg)
    cfg["id"] = account_id
    for k in ("secret", "mnemonic", "api_key"):
        if cfg.get(k) == "***":
            cfg[k] = old.get(k, "")
    s.storage.save_account(cfg)
    s.register_account(cfg)
    return {"ok": True}


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str):
    s = app_state()
    for bot in s.bot_manager.list_bots():
        if bot["config"].get("account_id") == account_id and bot["status"] in ("running", "starting"):
            raise HTTPException(400, "stop bots using this account first")
    s.storage.delete_account(account_id)
    s.remove_adapter(account_id)
    return {"ok": True}


@router.post("/accounts/{account_id}/test")
async def test_account(account_id: str):
    s = app_state()
    cfg = next((a for a in s.storage.list_accounts() if a["id"] == account_id), None)
    if not cfg:
        raise HTTPException(404, "not found")
    ad = s.adapters.get(account_id)
    own = False
    if not ad:
        try:
            ad = create_adapter(cfg, bus=s.bus, storage=s.storage)
            own = True
        except Exception as e:
            return {"ok": False, "error": str(e)[:300]}
    try:
        await asyncio.wait_for(ad.load_markets(), timeout=25)
        bal = await asyncio.wait_for(ad.fetch_balances(), timeout=15)
        feed = getattr(ad, "feed_status", "") or ("live" if ad.symbols() else "unreachable")
        result = {"ok": True, "markets": len(ad.symbols()), "balances": bal,
                  "paper": bool(cfg.get("paper")), "feed": feed,
                  "source": s.market_data.preferred_source(cfg.get("underlying") or cfg.get("exchange", ""))}
        if own:
            await ad.close()
        return result
    except asyncio.TimeoutError:
        if own:
            try:
                await ad.close()
            except Exception:
                pass
        return {"ok": False, "error": "connection test timed out (exchange unreachable from this machine)"}
    except Exception as e:
        if own:
            try:
                await ad.close()
            except Exception:
                pass
        return {"ok": False, "error": str(e)[:300]}


@router.post("/accounts/{account_id}/reset")
async def reset_account(account_id: str, body: AccountIn):
    s = app_state()
    ad = s.adapters.get(account_id)
    if ad and getattr(ad, "is_paper", False):
        ad.reset(body.config.get("initial_balances"))
        return {"ok": True, "balances": await ad.fetch_balances()}
    raise HTTPException(400, "not a paper account")


@router.get("/accounts/{account_id}/balances")
async def account_balances(account_id: str):
    s = app_state()
    cached = s.balances.cached.get(account_id)
    ad = s.adapters.get(account_id)
    if ad and getattr(ad, "is_paper", False):
        return {"balances": await ad.fetch_balances(), "positions": await ad.positions(),
                "cached": False, "equity": ad.equity(s.market_data.price_map()),
                "paper": True, "feed": getattr(ad, "feed_status", "")}
    if ad:
        # live account: try a fresh fetch, fall back to cache
        try:
            raw = await asyncio.wait_for(ad.fetch_balances(), timeout=12)
            eq = s.balances.equity_from(raw, s.market_data.price_map())
            s.balances.cached[account_id] = {"ts": now_ms(), "balances": raw, "equity": eq}
            return {"balances": raw, "positions": await ad.positions(),
                    "cached": False, "equity": eq, "paper": False}
        except Exception:
            pass
    if cached:
        return {"balances": cached["balances"], "positions": [], "cached": True,
                "equity": cached.get("equity", 0), "paper": False}
    return {"balances": {}, "positions": [], "cached": False, "paper": bool(
        (s.get_account(account_id) or {}).get("paper", False))}


@router.get("/bots")
async def list_bots():
    s = app_state()
    return {"bots": s.bot_manager.list_bots()}


@router.post("/bots")
async def create_bot(body: BotIn):
    s = app_state()
    try:
        state = s.bot_manager.create_bot(body.config)
    except ValidationError as e:
        raise HTTPException(400, _validation_message(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "bot": state}


@router.get("/bots/{bot_id}")
async def get_bot(bot_id: str):
    s = app_state()
    try:
        return {"bot": s.bot_manager.get_state(bot_id)}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.put("/bots/{bot_id}")
async def update_bot(bot_id: str, body: BotIn):
    s = app_state()
    try:
        return {"ok": True, "bot": s.bot_manager.update_bot(bot_id, body.config)}
    except ValidationError as e:
        raise HTTPException(400, _validation_message(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/bots/{bot_id}")
async def delete_bot(bot_id: str):
    s = app_state()
    try:
        s.bot_manager.delete_bot(bot_id)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/bots/{bot_id}/{op}")
async def bot_op(bot_id: str, op: str, body: ActionIn = None):
    s = app_state()
    try:
        if op == "start":
            state = await s.bot_manager.start_bot(bot_id)
        elif op == "stop":
            state = await s.bot_manager.stop_bot(bot_id)
        elif op == "pause":
            state = await s.bot_manager.pause_bot(bot_id)
        elif op == "resume":
            state = await s.bot_manager.resume_bot(bot_id)
        elif op == "action":
            state = await s.bot_manager.run_action(bot_id, (body or ActionIn(action="")).action,
                                                   (body or ActionIn(action="")).args)
        else:
            raise HTTPException(404, "unknown op")
        return {"ok": True, "state": state}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e)[:300])


@router.get("/bots/{bot_id}/orders")
async def bot_orders(bot_id: str, status: str = None, limit: int = 300):
    s = app_state()
    return {"orders": s.storage.list_orders(bot_id, status, limit)}


@router.get("/bots/{bot_id}/trades")
async def bot_trades(bot_id: str, limit: int = 500, since: int = 0):
    s = app_state()
    return {"trades": s.storage.list_trades(bot_id, limit, since)}


@router.get("/bots/{bot_id}/cycles")
async def bot_cycles(bot_id: str, limit: int = 100):
    s = app_state()
    return {"cycles": s.storage.list_cycles(bot_id, limit)}


@router.get("/bots/{bot_id}/events")
async def bot_events(bot_id: str, limit: int = 200):
    s = app_state()
    return {"events": s.storage.list_events(bot_id=bot_id, limit=limit)}


@router.get("/market/ohlcv")
async def ohlcv(exchange: str = "binance", symbol: str = "BTC/USDT", timeframe: str = "1h",
                limit: int = 300):
    s = app_state()
    data = await s.market_data.candles(exchange, symbol, timeframe, limit)
    return {"candles": data, "source": s.market_data.preferred_source(exchange)}


@router.get("/market/symbols")
async def symbols(exchange: str = "binance", q: str = "", kind: str = "spot"):
    s = app_state()
    try:
        ad = await s.market_data.ensure_markets(exchange, "BTC/USDT:USDT" if kind == "perp" else None)
    except Exception as e:
        raise HTTPException(502, f"market data unreachable: {str(e)[:200]}")
    syms = ad.symbols()
    if q:
        ql = q.lower()
        syms = [x for x in syms if ql in x.lower()]
    if kind == "perp":
        res = [x for x in syms if ":" in x and "-" not in x.split(":", 1)[1]
               and x.split(":", 1)[1] in ("USDT", "USDC", "USD")]
        res = [x for x in res if not x.endswith(":USD")]
    elif kind == "all":
        res = syms
    else:
        res = [x for x in syms if ":" not in x and "/" in x]
    return {"symbols": sorted(set(res))[:800],
            "source": s.market_data.preferred_source(exchange),
            "requested": exchange}


@router.get("/market/ticker")
async def ticker(exchange: str = "binance", symbol: str = "BTC/USDT"):
    s = app_state()
    try:
        t = await asyncio.wait_for(s.market_data.ticker(exchange, symbol), timeout=25)
        return {"bid": t.bid, "ask": t.ask, "last": t.last, "ts": t.ts,
                "source": s.market_data.preferred_source(exchange)}
    except asyncio.TimeoutError:
        raise HTTPException(504, f"market feed '{exchange}' timed out")
    except Exception as e:
        raise HTTPException(502, str(e)[:200])


@router.get("/feeds")
async def feeds(exchange: str = ""):
    s = app_state()
    return {"feeds": await s.market_data.feed_health(exchange)}


@router.get("/market/orderbook")
async def orderbook(exchange: str = "binance", symbol: str = "BTC/USDT"):
    s = app_state()
    return s.orderbooks.snapshot(exchange, symbol)


@router.post("/backtest")
async def backtest(body: BacktestRequest):
    s = app_state()
    try:
        candles = await s.market_data.candles(body.exchange, body.symbol, body.timeframe,
                                              body.candle_limit, since=body.start_ms or 0)
    except Exception as e:
        raise HTTPException(502, f"candles failed: {str(e)[:200]}")
    if not candles or len(candles) < 50:
        raise HTTPException(400, "not enough candles fetched")
    result = run_backtest(body, candles)
    return result


@router.get("/logs")
async def logs(bot_id: str = None, level: str = None, limit: int = 300, before: int = 0):
    s = app_state()
    return {"events": s.storage.list_events(bot_id, level, limit, before)}


@router.get("/settings")
async def get_settings():
    s = app_state()
    return s.storage.get_setting("app_settings", {})


@router.put("/settings")
async def put_settings(body: SettingsIn):
    s = app_state()
    cur = s.storage.get_setting("app_settings", {})
    cur.update(body.values)
    s.storage.set_setting("app_settings", cur)
    return {"ok": True, "settings": cur}


@router.get("/dashboard")
async def dashboard():
    s = app_state()
    bots = s.bot_manager.list_bots()
    total_realized = 0.0
    active = 0
    for b in bots:
        st = b.get("strategy_state") or {}
        rp = st.get("realized_pnl", 0)
        if isinstance(rp, (int, float)):
            total_realized += rp
        if b["status"] == "running":
            active += 1
    accounts = []
    for a in s.storage.list_accounts():
        cached = s.balances.cached.get(a["id"])
        accounts.append({"id": a["id"], "name": a["name"], "exchange": a["exchange"],
                         "paper": a.get("paper", True), "equity": (cached or {}).get("equity", 0)})
    equity_history = []
    for a in accounts:
        for row in s.storage.list_equity(a["id"], 3000):
            equity_history.append({"ts": row["ts"], "value": row["value"], "account": a["id"]})
    equity_history.sort(key=lambda x: x["ts"])
    recent = s.storage.list_events(limit=50)
    return {"bots": bots, "accounts": accounts, "active_bots": active,
            "total_realized_pnl": round(total_realized, 4), "equity_history": equity_history,
            "recent_events": recent}

