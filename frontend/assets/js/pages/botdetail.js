import { get, post, onWs } from "../api.js";
import { el, fmtNum, fmtMoney, fmtQty, fmtDate, pnlCls, toast, tabs, statusPill, pageHeader, statCard } from "../ui.js";
import { CandleChart } from "../chart.js";

let cleanups = [];

export async function render(container, botId) {
  let bot = (await get(`/bots/${botId}`)).bot;
  const feed = feedExchange(bot);
  let tf = bot.config.timeframe || "5m";
  let chart = null;
  let candles = [];
  let markers = [];
  let priceLines = [];

  const head = el("div", { class: "detail-head" });
  const chartCard = el("div", { class: "card", style: "margin-bottom:16px" });
  const obCard = el("div", { class: "card", style: "margin-bottom:16px" });
  const tabsCard = el("div", { class: "grid", style: "grid-template-columns: 2fr 1fr; align-items:start" });
  const leftCard = el("div", { class: "card" });
  const rightCol = el("div", { style: "display:flex; flex-direction:column; gap:16px" });

  container.append(head, chartCard, obCard, el("div", { style: "height:16px" }), tabsCard);
  tabsCard.append(leftCard, rightCol);

  const chartHost = el("div");
  chartCard.append(el("div", { class: "card-head" },
    el("div", { class: "card-title" }, "Market",
      el("span", { class: "tag" }, `${bot.config.symbol} · ${feed}`)),
    tfSelect()), el("div", { class: "card-body" }, chartHost));

  chart = new CandleChart(chartHost, {
    onClickMarker: (m) => showTradeInfo(m),
  });

  const obHost = el("div");
  obCard.append(el("div", { class: "card-head" },
    el("div", { class: "card-title" }, "Order Book",
      el("span", { class: "tag" }, "LIVE DEPTH")),
    el("div", { id: "ob-spread", class: "chip" }, "—")),
    el("div", { class: "card-body" }, obHost));
  drawOrderBook(null);

  function drawOrderBook(snap) {
    obHost.innerHTML = "";
    if (!snap || !snap.bids || (!snap.bids.length && !snap.asks.length)) {
      obHost.append(el("div", { class: "empty", style: "padding:22px" }, "waiting for depth data…"));
      return;
    }
    const asks = (snap.asks || []).slice(0, 9).reverse();
    const bids = (snap.bids || []).slice(0, 9);
    const maxCum = Math.max(
      asks.reduce((s, x) => s + x[1], 0), bids.reduce((s, x) => s + x[1], 0), 1e-9);
    const row = (p, q, cum, side) => {
      const w = Math.min(100, cum / maxCum * 100);
      return el("div", { style: `position:relative; display:grid; grid-template-columns: 1.1fr 1fr .8fr; padding:2.5px 8px; font-family:var(--mono); font-size:11.5px` },
        el("div", { style: `position:absolute; inset:0; ${side === "ask" ? "right:0" : "left:0"}; background:${side === "ask" ? "rgba(255,77,94,.13)" : "rgba(47,191,143,.13)"}; width:${w}%` }),
        el("span", { style: `position:relative; color:${side === "ask" ? "var(--red)" : "var(--green)"}` }, fmtNum(p, p > 100 ? 2 : 5)),
        el("span", { style: "position:relative; text-align:right; color:var(--text)" }, fmtNum(q, 4)),
        el("span", { style: "position:relative; text-align:right; color:var(--dim)" }, fmtNum(cum, 2)));
    };
    const headRow = el("div", { style: "display:grid; grid-template-columns: 1.1fr 1fr .8fr; padding:0 8px 4px; font-size:9.5px; text-transform:uppercase; letter-spacing:.8px; color:var(--dim)" },
      el("span", {}, "Price"), el("span", { style: "text-align:right" }, "Size"), el("span", { style: "text-align:right" }, "Sum"));
    let cum = 0;
    const askRows = asks.map((x) => { cum += x[1]; return row(x[0], x[1], cum, "ask"); });
    cum = 0;
    const bidRows = bids.map((x) => { cum += x[1]; return row(x[0], x[1], cum, "bid"); });
    obHost.append(
      el("div", { style: "display:grid; grid-template-columns: 1fr 1fr; gap:18px" },
        el("div", {},
          el("div", { class: "sect-title", style: "margin-bottom:6px; color:var(--red)" }, "Asks (sell)"),
          headRow, ...askRows),
        el("div", {},
          el("div", { class: "sect-title", style: "margin-bottom:6px; color:var(--green)" }, "Bids (buy)"),
          headRow, ...bidRows)));
    const sp = document.getElementById("ob-spread");
    if (sp) sp.textContent = `spread ${fmtNum(snap.spread_bps || 0, 2)} bps · mid ${fmtNum(snap.mid || 0, 2)}`;
  }

  async function pollOrderBook() {
    try {
      const snap = await get(`/market/orderbook?exchange=${feed}&symbol=${encodeURIComponent(bot.config.symbol)}`);
      drawOrderBook(snap);
    } catch { }
  }
  pollOrderBook();

  function tfSelect() {
    return el("select", {
      style: "background:var(--panel-2); color:var(--text); border:1px solid var(--border-2); border-radius:8px; padding:6px 10px; font-family:var(--mono)",
      onchange: (e) => { tf = e.target.value; loadChart(); },
    }, ...["1m", "5m", "15m", "1h", "4h", "1d"].map((t) =>
      el("option", { value: t, ...(t === tf ? { selected: true } : {}) }, t)));
  }

  function feedExchange(b) {
    const acc = b.account || {};
    if (acc.paper) return acc.underlying || "bybit";
    return acc.exchange || "bybit";
  }

  async function loadChart() {
    try {
      const res = await get(`/market/ohlcv?exchange=${feed}&symbol=${encodeURIComponent(bot.config.symbol)}&timeframe=${tf}&limit=300`);
      candles = res.candles.map((c) => ({ t: c[0], o: c[1], h: c[2], l: c[3], c: c[4], v: c[5] }));
      chart.setCandles(candles);
      applyOverlays();
    } catch (e) { toast("candles: " + e.message, "err"); }
  }

  function applyOverlays() {
    const st = bot.strategy_state || {};
    priceLines = [];
    markers = [];
    if (bot.config.type === "grid" || bot.config.type === "combo") {
      for (const o of bot.open_orders || []) {
        priceLines.push({
          price: o.price,
          color: o.side === "buy" ? "rgba(47,191,143,.65)" : "rgba(255,77,94,.65)",
          label: `${o.side === "buy" ? "B" : "S"}${fmtNum(o.price, o.price > 100 ? 1 : 4)}`,
        });
      }
      const range = st.range;
      if (range && range[0]) {
        priceLines.push({ price: range[0], color: "rgba(91,140,255,.5)", label: "LOW", width: 1 });
        priceLines.push({ price: range[1], color: "rgba(91,140,255,.5)", label: "HIGH", width: 1 });
      }
    }
    if (bot.config.type === "dca" || bot.config.type === "combo") {
      const cyc = st.cycle;
      if (cyc && cyc.avg_price) {
        priceLines.push({ price: cyc.avg_price, color: "rgba(255,106,26,.8)", label: "AVG", width: 1.4 });
        if (cyc.tp_price) priceLines.push({ price: cyc.tp_price, color: "rgba(47,191,143,.8)", label: "TP", width: 1.4 });
        if (cyc.sl_price) priceLines.push({ price: cyc.sl_price, color: "rgba(255,77,94,.8)", label: "SL", width: 1.4 });
      }
    }
    markers = (botState.trades || []).map((t) => ({
      ts: t.ts,
      price: t.price,
      side: t.side,
      title: `${t.side.toUpperCase()} · ${roleName(t.role)}`,
      infoRows: [
        ["Price", fmtNum(t.price, t.price > 100 ? 2 : 6)],
        ["Qty", fmtQty(t.qty)],
        ["Notional", fmtNum(t.notional, 2)],
        ...(t.pnl ? [["PnL", fmtMoney(t.pnl)]] : []),
        ["Fee", fmtNum(t.fee, 4)],
        ["Time", fmtDate(t.ts)],
        ...(t.role === "grid_sell" || t.pnl ? [["Role", roleName(t.role)]] : []),
      ],
    }));
    chart.setPriceLines(priceLines);
    chart.setMarkers(markers);
    if (bot.price) chart.setLastPrice(bot.price);
  }

  function roleName(r) {
    return ({ grid_buy: "Grid Buy", grid_sell: "Grid Sell", grid_sell_init: "Grid Sell (inv)",
      dca_base: "DCA Base", dca_safety: "DCA Safety", dca_close: "DCA Close",
      grid_stoploss_close: "SL Close", manual_close: "Manual Close" })[r] || r || "Trade";
  }

  function showTradeInfo(m) {
    toast(`${m.title} @ ${fmtNum(m.price, 2)} — ${m.infoRows.map(([k, v]) => `${k}: ${v}`).slice(0, 4).join(" · ")}`, "");
  }

  function drawHead() {
    head.innerHTML = "";
    const st = bot.strategy_state || {};
    const pnl = st.realized_pnl || 0;
    const pos = bot.position || {};
    head.append(pageHeader("",
      statusPill(bot.status),
      el("button", { class: "btn sm primary", onclick: () => act("start"), disabled: bot.status === "running" }, bot.status === "paused" ? "Resume" : "Start"),
      el("button", { class: "btn sm", onclick: () => act("pause"), disabled: bot.status !== "running" }, "Pause"),
      el("button", { class: "btn sm danger", onclick: () => act("stop"), disabled: bot.status === "stopped" }, "Stop")),
      el("div", { class: "stat-row" },
        statCard("Realized PnL", fmtMoney(pnl), `fees ~${fmtNum(st.fees_estimated || pos.fees_paid || 0, 3)}`, pnlCls(pnl)),
        statCard("Position", fmtQty(pos.qty || 0) + " " + bot.config.symbol.split("/")[0],
          pos.avg_entry ? `avg ${fmtNum(pos.avg_entry, 2)} · uPnL ${fmtMoney(pos.unrealized_pnl || 0)}` : "flat", pnlCls(pos.unrealized_pnl || 0)),
        bot.config.type === "dca" || bot.config.type === "combo"
          ? statCard("Cycles", `${st.cycles_done ?? 0}`, `${st.wins || 0}W / ${st.losses || 0}L`)
          : statCard("Roundtrips", `${st.roundtrips ?? 0}`, `grid profit ${fmtMoney(st.grid_profit || 0)}`),
        statCard("Open Orders", String((bot.open_orders || []).length),
          (() => {
            const oo = bot.open_orders || [];
            const buys = st.open_buys ?? oo.filter((o) => o.side === "buy").length;
            const sells = st.open_sells ?? oo.filter((o) => o.side === "sell").length;
            return `${buys} buys · ${sells} sells`;
          })()),
        statCard("Mode", bot.paper ? "PAPER" : "LIVE", (() => {
          const f = bot.feed || {};
          const feedTxt = f.status && f.status !== "live" ? ` · feed ${f.status}` : (f.source && f.exchange && f.source !== f.exchange ? ` · via ${f.source}` : "");
          return `${bot.account?.name || "—"} · ${bot.account?.exchange || ""}${feedTxt}`;
        })())));

    const titleRow = el("div", { class: "detail-top", style: "margin-bottom:14px" },
      el("div", { class: "detail-title" },
        el("span", { class: `statusdot ${bot.status}` }),
        el("h2", {}, bot.config.name),
        el("span", { class: `type-badge ${bot.config.type}` }, bot.config.type.toUpperCase()),
        el("span", { class: "chip" }, bot.config.symbol),
        el("span", { class: "chip acc" }, `#${bot.id}`)),
      el("div", { class: "btn-row" },
        ...(bot.config.type === "grid" || bot.config.type === "combo" ? [el("button", { class: "btn sm", onclick: () => action("rebuild") }, "Rebuild grid")] : []),
        ...(bot.config.type === "dca" || bot.config.type === "combo" ? [el("button", { class: "btn sm", onclick: () => action("close_cycle") }, "Close cycle")] : []),
        el("button", { class: "btn sm danger", onclick: () => action("close_all") }, "Close all")));
    head.prepend(titleRow);

    const mods = bot.modules || {};
    if (mods.entry_ok === false && bot.status === "running") {
      const why = mods.trend_ok === false ? "trend filter" : mods.vol_ok === false ? "volatility guard" : "regime control";
      head.append(el("div", { class: "card", style: "margin-top:12px; border-color: rgba(255,106,26,.5)" },
        el("div", { class: "card-body", style: "color:var(--accent-bright); font-size:12.5px" },
          `MODULE FILTER ACTIVE — new entries are on hold by the ${why}. Existing orders, exits and safety orders keep working.`)));
    }
    if (bot.error) {
      head.append(el("div", { class: "card", style: "margin-top:12px; border-color: rgba(255,77,94,.5)" },
        el("div", { class: "card-body", style: "color:var(--red); font-family:var(--mono); font-size:12px" }, "ERROR — " + bot.error)));
    }
    if (bot.price_stale && bot.status === "running") {
      head.append(el("div", { class: "card", style: "margin-top:12px; border-color: rgba(255,106,26,.5)" },
        el("div", { class: "card-body", style: "color:var(--accent-bright); font-size:12.5px; display:flex; justify-content:space-between; gap:12px; align-items:center; flex-wrap:wrap" },
          el("span", {}, "NO MARKET DATA — the account's data source is unreachable, the bot cannot trade."),
          el("button", { class: "btn sm", onclick: () => { location.hash = "#/accounts"; } }, "Fix source in Accounts"))));
    }
    const f = bot.feed || {};
    if (f.status === "synthetic") {
      head.append(el("div", { class: "card", style: "margin-top:12px; border-color: rgba(255,106,26,.5)" },
        el("div", { class: "card-body", style: "color:var(--accent-bright); font-size:12.5px" },
          `SIMULATED PRICES — feed "${f.exchange}" is unreachable, the paper engine trades on a synthetic random walk. Bots keep working; reconnect the source for real market prices.`)));
    } else if (f.status === "cached") {
      head.append(el("div", { class: "card", style: "margin-top:12px; border-color: rgba(255,106,26,.5)" },
        el("div", { class: "card-body", style: "color:var(--accent-bright); font-size:12.5px" },
          `STALE PRICES — feed "${f.exchange}" is unreachable, trading on last known prices.`)));
    }
  }

  async function act(op) {
    try { await post(`/bots/${botId}/${op}`); await refresh(); } catch (e) { toast(e.message, "err"); }
  }
  async function action(name) {
    try { await post(`/bots/${botId}/action`, { action: name }); toast(`${name} executed`, "ok"); await refresh(); } catch (e) { toast(e.message, "err"); }
  }

  const tabDefs = [
    { label: "Orders", render: () => ordersPane() },
    { label: "Trades", render: () => tradesPane() },
    ...(bot.config.type === "dca" || bot.config.type === "combo" ? [{ label: "DCA Cycles", render: () => cyclesPane() }] : []),
    { label: "Logs", render: () => logsPane() },
    { label: "Settings", render: () => settingsPane() },
  ];
  const tabWrap = el("div", {});
  const tabsCtl = tabs(tabWrap, tabDefs);
  leftCard.append(el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Activity")),
    el("div", { class: "card-body", style: "padding-top:0" }, tabWrap));

  const stratBody = el("div", { class: "card-body", style: "padding-top:6px" });
  const riskBody = el("div", { class: "card-body", style: "padding-top:6px" });
  rightCol.append(
    el("div", { class: "card" }, el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Strategy")), stratBody),
    el("div", { class: "card" }, el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Risk")), riskBody));

  function drawSide() {
    stratBody.innerHTML = "";
    stratBody.append(strategyInfo());
    riskBody.innerHTML = "";
    riskBody.append(riskInfo());
  }

  function strategyInfo() {
    const st = bot.strategy_state || {};
    const rows = [];
    if (bot.config.type === "grid" || bot.config.type === "combo") {
      const g = bot.config.grid || {};
      const gs = st.strategy === "grid" ? st : (st.grid || {});
      rows.push(["Levels", gs.levels ?? g.levels], ["Type", g.grid_type],
        ["Range", gs.range ? `${fmtNum(gs.range[0], 2)} — ${fmtNum(gs.range[1], 2)}` : "—"],
        ["Budget used", `${fmtNum(gs.budget_used ?? 0, 2)} / ${fmtNum(g.total_budget_quote, 0)}`],
        ["Rebuilds", gs.rebuilds ?? 0], ["Skew shift", gs.skew_shift ?? 0],
        ["Held lots", gs.held_lots ?? 0]);
    }
    if (bot.config.type === "dca" || bot.config.type === "combo") {
      const d = bot.config.dca || {};
      const ds = st.strategy === "dca" ? st : (st.dca || {});
      rows.push(["Direction", d.short_mode ? "SHORT" : "LONG"], ["Max SO", d.max_safety_orders],
        ["Deviation", `${d.safety_deviation_pct}% ×${d.safety_step_scale}`],
        ["Volume scale", `×${d.volume_scale}`], ["TP", `${d.take_profit_pct}%`],
        ["SL", d.stop_loss_pct ? `${d.stop_loss_pct}%` : "off"],
        ["Entry condition", d.entry_condition !== "immediate" ? `${d.entry_condition.toUpperCase()} ${d.trend_filter_timeframe}` : "immediate"]);
      if (ds.cycle && ds.cycle.avg_price) rows.push(["Active cycle", `${ds.cycle.entries} entries · avg ${fmtNum(ds.cycle.avg_price, 2)}`]);
    }
    const mc = bot.config.modules || {};
    if (mc && Object.keys(mc).length) {
      const mods = bot.modules || {};
      rows.push(["Entry filter", mods.entry_ok === false ? "BLOCKED — new entries on hold"
        : (mods.trend_ok === false ? "trend-blocked" : mods.vol_ok === false ? "vol-blocked"
          : mods.regime_ok === false ? "regime-blocked" : "allowed")]);
      if (mc.trend_filter?.enabled) rows.push(["Trend module", `EMA${mc.trend_filter.ema_period} ${mc.trend_filter.timeframe} · ${mods.trend_ok ? "pass" : "block"}`]);
      if (mc.vol_guard?.enabled) rows.push(["Vol guard", `ATR limit ${mc.vol_guard.threshold_pct}% · ${mods.vol_ok ? "pass" : "block"}`]);
      if (mc.regime?.enabled) rows.push(["Regime control", mods.regime ? `${(mods.regime.regime || "—").toUpperCase()} (ADX ${mods.regime.adx ?? "—"})` : "…"]);
    }
    return kvList(rows);
  }

  function riskInfo() {
    const r = bot.risk || {};
    return kvList([
      ["Exposure", fmtNum(r.position_quote || 0, 2)],
      ["Realized today", fmtMoney(r.realized_today || 0)],
      ["Kill switch", r.killed ? "TRIGGERED" : "armed"],
      ["Peak equity", fmtNum(r.peak_equity || 0, 2)],
    ]);
  }

  function kvList(rows) {
    return el("div", {}, ...rows.map(([k, v]) =>
      el("div", { class: "kv" }, el("span", { class: "k" }, k), el("span", { class: "v" }, String(v ?? "—")))));
  }

  function ordersPane() {
    const host = el("div", { class: "scroll-x", style: "max-height: 460px; overflow-y:auto" });
    fill();
    function fill() {
      const open = bot.open_orders || [];
      host.innerHTML = "";
      if (!open.length) { host.append(el("div", { class: "empty" }, "No open orders")); return; }
      host.append(el("table", { class: "tbl" },
        el("thead", {}, el("tr", {}, el("th", {}, "Side"), el("th", { class: "num" }, "Price"), el("th", { class: "num" }, "Qty"),
          el("th", { class: "num" }, "Notional"), el("th", {}, "Role"), el("th", {}, "Created"), el("th", {}, ""))),
        el("tbody", {}, ...open.map((o) => el("tr", {},
          el("td", {}, el("span", { class: o.side === "buy" ? "pos" : "neg", style: "font-weight:700" }, o.side.toUpperCase())),
          el("td", { class: "num" }, fmtNum(o.price, 2)),
          el("td", { class: "num" }, fmtQty(o.qty)),
          el("td", { class: "num muted" }, fmtNum(o.price * o.qty, 2)),
          el("td", { class: "muted" }, roleName(o.meta?.role)),
          el("td", { class: "muted mono" }, fmtDate(o.created_at)),
          el("td", {}, el("button", {
            class: "btn sm ghost", onclick: async () => {
              await post(`/bots/${botId}/action`, { action: "cancel_order", args: { oid: o.oid } }).catch(() => { });
              refresh();
            },
          }, "✕")))))));
    }
    return host;
  }

  function tradesPane() {
    const host = el("div", { class: "scroll-x", style: "max-height: 460px; overflow-y:auto" });
    get(`/bots/${botId}/trades?limit=500`).then((res) => fill(res.trades));
    function fill(trades) {
      host.innerHTML = "";
      if (!trades.length) { host.append(el("div", { class: "empty" }, "No trades yet — they will appear here with chart markers")); return; }
      const sorted = [...trades].reverse();
      host.append(el("table", { class: "tbl" },
        el("thead", {}, el("tr", {}, el("th", {}, "Time"), el("th", {}, "Side"), el("th", { class: "num" }, "Price"),
          el("th", { class: "num" }, "Qty"), el("th", { class: "num" }, "Notional"), el("th", { class: "num" }, "Fee"),
          el("th", { class: "num" }, "PnL"), el("th", {}, "Role"))),
        el("tbody", {}, ...sorted.map((t) => el("tr", {},
          el("td", { class: "mono muted" }, fmtDate(t.ts)),
          el("td", {}, el("span", { class: t.side === "buy" ? "pos" : "neg", style: "font-weight:700" }, t.side.toUpperCase())),
          el("td", { class: "num" }, fmtNum(t.price, 2)),
          el("td", { class: "num" }, fmtQty(t.qty)),
          el("td", { class: "num muted" }, fmtNum(t.notional, 2)),
          el("td", { class: "num muted" }, fmtNum(t.fee, 4)),
          el("td", { class: `num ${pnlCls(t.pnl)}` }, t.pnl ? fmtMoney(t.pnl) : "—"),
          el("td", { class: "muted" }, roleName(t.role)))))));
    }
    return host;
  }

  function cyclesPane() {
    const host = el("div", { class: "scroll-x", style: "max-height: 460px; overflow-y:auto" });
    get(`/bots/${botId}/cycles`).then((res) => fill(res.cycles));
    function fill(cycles) {
      host.innerHTML = "";
      if (!cycles.length) { host.append(el("div", { class: "empty" }, "No DCA cycles yet")); return; }
      host.append(el("table", { class: "tbl" },
        el("thead", {}, el("tr", {}, el("th", {}, "Cycle"), el("th", {}, "Status"), el("th", {}, "Opened"),
          el("th", { class: "num" }, "Avg entry"), el("th", { class: "num" }, "TP target"),
          el("th", { class: "num" }, "Orders"), el("th", { class: "num" }, "Fees"), el("th", { class: "num" }, "PnL"))),
        el("tbody", {}, ...cycles.map((c) => el("tr", {},
          el("td", { class: "mono muted" }, c.id),
          el("td", {}, statusPill(c.status === "active" ? "running" : c.status === "closed" ? "stopped" : c.status)),
          el("td", { class: "mono muted" }, fmtDate(c.opened_at)),
          el("td", { class: "num" }, fmtNum(c.meta?.avg_price, 2)),
          el("td", { class: "num pos" }, c.meta?.tp_price ? fmtNum(c.meta.tp_price, 2) : "—"),
          el("td", { class: "num muted" }, String((c.meta?.entries || []).length)),
          el("td", { class: "num muted" }, fmtNum(c.fees, 4)),
          el("td", { class: `num ${pnlCls(c.realized_pnl)}` }, c.closed_at ? fmtMoney(c.realized_pnl) : "—"))))));
    }
    return host;
  }

  function logsPane() {
    const host = el("div", { style: "max-height: 460px; overflow-y:auto" });
    get(`/bots/${botId}/events?limit=200`).then((res) => fill(res.events));
    function fill(events) {
      host.innerHTML = "";
      if (!events.length) { host.append(el("div", { class: "empty" }, "No logs")); return; }
      events.forEach((e) => host.append(logRow(e)));
    }
    return host;
  }

  function logRow(e) {
    return el("div", { class: "log-line" },
      el("span", { class: "ts" }, fmtDate(e.ts)),
      el("span", { class: `lv ${e.level}` }, e.level),
      el("span", { class: "msg" }, e.message));
  }

  function settingsPane() {
    const g = bot.config;
    return el("div", { class: "card-body", style: "padding: 8px 4px" },
      kvList([
        ["Type", g.type], ["Symbol", g.symbol], ["Timeframe", g.timeframe],
        ["Tick interval", `${g.tick_interval_s}s`],
        ...(g.grid ? Object.entries(g.grid).filter(([k]) => ["total_budget_quote", "levels", "grid_type", "post_only", "infinite", "on_exit", "stop_loss_pct", "take_profit_pct"].includes(k)).map(([k, v]) => [k, String(v)]) : []),
        ...(g.dca ? Object.entries(g.dca).filter(([k]) => ["base_order_pct", "max_safety_orders", "safety_deviation_pct", "volume_scale", "take_profit_pct", "stop_loss_pct", "short_mode"].includes(k)).map(([k, v]) => [k, String(v)]) : []),
        ...(g.modules && Object.keys(g.modules).length ? [["modules", Object.entries(g.modules).filter(([, v]) => v && v.enabled).map(([k]) => k).join(", ") || "none"]] : []),
        ["max position", g.risk.max_position_quote || "off"],
        ["daily max loss", g.risk.daily_max_loss_quote || "off"],
      ]));
  }

  let botState = { trades: [] };
  async function refresh() {
    bot = (await get(`/bots/${botId}`)).bot;
    const tr = await get(`/bots/${botId}/trades?limit=500`);
    botState.trades = tr.trades;
    drawHead();
    drawSide();
    tabsCtl.rerender();
    applyOverlays();
    chart.draw();
  }

  await loadChart();
  await refresh();

  const poller = setInterval(refresh, 3000);
  const obPoller = setInterval(pollOrderBook, 5000);
  const unsub = onWs((topic, p) => {
    if (topic === "price" && p.symbol === bot.config.symbol && p.exchange === feed) {
      chart.setLastPrice(p.last || (p.bid + p.ask) / 2);
    }
    if (topic === "orderbook" && p.symbol === bot.config.symbol && p.exchange === feed) {
      drawOrderBook(p);
    }
    if (topic === "bot_event" && p.bot_id === botId && (p.type === "trade" || p.type === "order_placed" || p.type === "status")) {
      refresh();
    }
  });

  cleanups.push(() => { clearInterval(poller); clearInterval(obPoller); unsub(); chart.destroy(); });
  return () => { cleanups.forEach((f) => f()); cleanups = []; };
}