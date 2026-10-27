import json
import sqlite3
import threading
from .utils import now_ms, uid

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY, name TEXT, exchange TEXT, paper INTEGER, config TEXT, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS bots (
  id TEXT PRIMARY KEY, name TEXT, type TEXT, account_id TEXT, symbol TEXT,
  config TEXT, state TEXT, status TEXT, created_at INTEGER, updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS orders (
  oid TEXT PRIMARY KEY, bot_id TEXT, exchange_oid TEXT, symbol TEXT, side TEXT,
  type TEXT, price REAL, qty REAL, filled REAL, avg_price REAL, status TEXT,
  post_only INTEGER, created_at INTEGER, updated_at INTEGER, meta TEXT
);
CREATE TABLE IF NOT EXISTS trades (
  id TEXT PRIMARY KEY, bot_id TEXT, ts INTEGER, symbol TEXT, side TEXT, role TEXT,
  price REAL, qty REAL, fee REAL, fee_currency TEXT, notional REAL, cycle_id TEXT,
  order_id TEXT, pnl REAL, meta TEXT
);
CREATE TABLE IF NOT EXISTS cycles (
  id TEXT PRIMARY KEY, bot_id TEXT, strategy TEXT, status TEXT, opened_at INTEGER,
  closed_at INTEGER, realized_pnl REAL, fees REAL, meta TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, level TEXT, bot_id TEXT,
  category TEXT, message TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS equity (
  id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT, ts INTEGER, value REAL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_trades_bot ON trades(bot_id, ts);
CREATE INDEX IF NOT EXISTS idx_orders_bot ON orders(bot_id, status);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_equity ON equity(account_id, ts);
"""


class Storage:
    def __init__(self, path: str = "data/trader.db"):
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def _exec(self, sql, params=(), fetch=None):
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()
            if fetch == "all":
                return [dict(r) for r in cur.fetchall()]
            if fetch == "one":
                r = cur.fetchone()
                return dict(r) if r else None
            return cur.lastrowid

    def save_account(self, cfg: dict):
        self._exec(
            "INSERT INTO accounts(id,name,exchange,paper,config,created_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=?, exchange=?, paper=?, config=?",
            (cfg["id"], cfg["name"], cfg["exchange"], 1 if cfg.get("paper") else 0,
             json.dumps(cfg), now_ms(), cfg["name"], cfg["exchange"],
             1 if cfg.get("paper") else 0, json.dumps(cfg)))

    def delete_account(self, account_id: str):
        self._exec("DELETE FROM accounts WHERE id=?", (account_id,))

    def list_accounts(self) -> list:
        rows = self._exec("SELECT * FROM accounts", fetch="all") or []
        out = []
        for r in rows:
            c = json.loads(r["config"])
            c["created_at"] = r["created_at"]
            out.append(c)
        return out

    def save_bot(self, bot_id, name, type_, account_id, symbol, config: dict, state: dict, status: str):
        self._exec(
            "INSERT INTO bots(id,name,type,account_id,symbol,config,state,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=?, type=?, account_id=?, "
            "symbol=?, config=?, state=?, status=?, updated_at=?",
            (bot_id, name, type_, account_id, symbol, json.dumps(config), json.dumps(state),
             status, now_ms(), now_ms(), name, type_, account_id, symbol,
             json.dumps(config), json.dumps(state), status, now_ms()))

    def update_bot(self, bot_id, *, status=None, state=None, config=None, name=None, symbol=None):
        sets, params = [], []
        for col, val in (("status", status), ("state", json.dumps(state) if state is not None else None),
                         ("config", json.dumps(config) if config is not None else None),
                         ("name", name), ("symbol", symbol)):
            if val is not None:
                sets.append(f"{col}=?")
                params.append(val)
        if not sets:
            return
        sets.append("updated_at=?")
        params.append(now_ms())
        params.append(bot_id)
        self._exec(f"UPDATE bots SET {', '.join(sets)} WHERE id=?", tuple(params))

    def get_bot(self, bot_id: str):
        r = self._exec("SELECT * FROM bots WHERE id=?", (bot_id,), fetch="one")
        if not r:
            return None
        r["config"] = json.loads(r["config"] or "{}")
        r["state"] = json.loads(r["state"] or "{}")
        return r

    def list_bots(self) -> list:
        rows = self._exec("SELECT * FROM bots ORDER BY created_at DESC", fetch="all") or []
        for r in rows:
            r["config"] = json.loads(r["config"] or "{}")
            r["state"] = json.loads(r["state"] or "{}")
        return rows

    def delete_bot(self, bot_id: str):
        for t in ("bots",):
            self._exec(f"DELETE FROM {t} WHERE id=?", (bot_id,))
        for t in ("orders", "trades", "cycles"):
            self._exec(f"DELETE FROM {t} WHERE bot_id=?", (bot_id,))
        self._exec("DELETE FROM events WHERE bot_id=?", (bot_id,))

    def upsert_order(self, o: dict):
        self._exec(
            "INSERT INTO orders(oid,bot_id,exchange_oid,symbol,side,type,price,qty,filled,avg_price,"
            "status,post_only,created_at,updated_at,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(oid) DO UPDATE SET exchange_oid=?, filled=?, avg_price=?, status=?, updated_at=?, meta=?",
            (o["oid"], o["bot_id"], o.get("exchange_oid", ""), o.get("symbol", ""), o["side"], o["type"],
             o.get("price", 0), o.get("qty", 0), o.get("filled", 0), o.get("avg_price", 0), o["status"],
             1 if o.get("post_only") else 0, o.get("created_at", now_ms()), now_ms(),
             json.dumps(o.get("meta", {})),
             o.get("exchange_oid", ""), o.get("filled", 0), o.get("avg_price", 0), o["status"],
             now_ms(), json.dumps(o.get("meta", {}))))

    def list_orders(self, bot_id: str, status: str = None, limit: int = 500) -> list:
        if status:
            rows = self._exec("SELECT * FROM orders WHERE bot_id=? AND status=? ORDER BY created_at DESC LIMIT ?",
                              (bot_id, status, limit), fetch="all")
        else:
            rows = self._exec("SELECT * FROM orders WHERE bot_id=? ORDER BY created_at DESC LIMIT ?",
                              (bot_id, limit), fetch="all")
        for r in rows or []:
            r["meta"] = json.loads(r["meta"] or "{}")
        return rows or []

    def open_orders(self, bot_id: str) -> list:
        rows = self._exec("SELECT * FROM orders WHERE bot_id=? AND status IN ('open','partially_filled')",
                          (bot_id,), fetch="all") or []
        for r in rows:
            r["meta"] = json.loads(r["meta"] or "{}")
        return rows

    def insert_trade(self, t: dict):
        self._exec(
            "INSERT OR REPLACE INTO trades(id,bot_id,ts,symbol,side,role,price,qty,fee,fee_currency,"
            "notional,cycle_id,order_id,pnl,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t["id"], t["bot_id"], t["ts"], t.get("symbol", ""), t["side"], t.get("role", ""),
             t["price"], t["qty"], t.get("fee", 0), t.get("fee_currency", ""), t.get("notional", 0),
             t.get("cycle_id", ""), t.get("order_id", ""), t.get("pnl", 0), json.dumps(t.get("meta", {}))))

    def list_trades(self, bot_id: str, limit: int = 500, since: int = 0) -> list:
        rows = self._exec(
            "SELECT * FROM trades WHERE bot_id=? AND ts>=? ORDER BY ts ASC LIMIT ?",
            (bot_id, since, limit), fetch="all") or []
        for r in rows:
            r["meta"] = json.loads(r["meta"] or "{}")
        return rows

    def insert_cycle(self, c: dict):
        self._exec(
            "INSERT OR REPLACE INTO cycles(id,bot_id,strategy,status,opened_at,closed_at,realized_pnl,fees,meta) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (c["id"], c["bot_id"], c.get("strategy", ""), c["status"], c.get("opened_at", now_ms()),
             c.get("closed_at", 0), c.get("realized_pnl", 0), c.get("fees", 0), json.dumps(c.get("meta", {}))))

    def list_cycles(self, bot_id: str, limit: int = 100) -> list:
        rows = self._exec("SELECT * FROM cycles WHERE bot_id=? ORDER BY opened_at DESC LIMIT ?",
                          (bot_id, limit), fetch="all") or []
        for r in rows:
            r["meta"] = json.loads(r["meta"] or "{}")
        return rows

    def insert_event(self, level: str, bot_id: str, category: str, message: str, data: dict = None):
        self._exec("INSERT INTO events(ts,level,bot_id,category,message,data) VALUES(?,?,?,?,?,?)",
                   (now_ms(), level, bot_id or "", category or "", message, json.dumps(data or {})))

    def list_events(self, bot_id: str = None, level: str = None, limit: int = 300, before: int = 0) -> list:
        conds, params = [], []
        if bot_id:
            conds.append("bot_id=?")
            params.append(bot_id)
        if level:
            conds.append("level=?")
            params.append(level)
        if before:
            conds.append("ts<?")
            params.append(before)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        params.append(limit)
        rows = self._exec(f"SELECT * FROM events {where} ORDER BY ts DESC LIMIT ?", tuple(params),
                          fetch="all") or []
        for r in rows:
            r["data"] = json.loads(r["data"] or "{}")
        return rows

    def insert_equity(self, account_id: str, value: float):
        self._exec("INSERT INTO equity(account_id,ts,value) VALUES(?,?,?)", (account_id, now_ms(), value))

    def list_equity(self, account_id: str, limit: int = 2000) -> list:
        rows = self._exec("SELECT ts,value FROM equity WHERE account_id=? ORDER BY ts DESC LIMIT ?",
                          (account_id, limit), fetch="all") or []
        return list(reversed(rows))

    def set_setting(self, key: str, value):
        self._exec("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=?",
                   (key, json.dumps(value), json.dumps(value)))

    def get_setting(self, key: str, default=None):
        r = self._exec("SELECT value FROM settings WHERE key=?", (key,), fetch="one")
        return json.loads(r["value"]) if r else default

    def new_trade_id(self) -> str:
        return "t" + uid()

    def new_cycle_id(self) -> str:
        return "c" + uid()
