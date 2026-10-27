import { get, post, del, onWs } from "../api.js";
import { el, fmtNum, fmtMoney, pnlCls, toast, openModal, statusPill, pageHeader, field, input, numInput, selectEl, checkbox } from "../ui.js";
import { openAccountForm } from "./accountform.js";

let unsub = null;
let bots = [];

export async function render(container) {
  const data = await get("/bots");
  bots = data.bots;
  container.append(pageHeader("Bots",
    el("button", { class: "btn primary", onclick: () => openWizard() }, "+ New Bot"),
    el("button", { class: "btn", onclick: refresh }, "Refresh")));

  const list = el("div", { class: "bot-list" });
  container.append(list);
  drawList();

  async function refresh() {
    bots = (await get("/bots")).bots;
    list.innerHTML = "";
    drawList();
  }

  function drawList() {
    list.innerHTML = "";
    if (!bots.length) {
      list.append(el("div", { class: "card" }, el("div", { class: "card-body" },
        el("div", { class: "empty" },
          el("div", { class: "big" }, "no bots yet"),
          "Automate Grid and DCA bots, extend them with Momentum / VWAP / Adaptive filter modules", el("br"),
          el("button", { class: "btn primary", style: "margin-top:14px", onclick: () => openWizard() }, "Create your first bot")))));
      return;
    }
    for (const b of bots) list.append(botCard(b));
  }

  function botCard(b) {
    const st = b.strategy_state || {};
    const pnl = st.realized_pnl || 0;
    const running = b.status === "running";
    const card = el("div", { class: "bot-card", onclick: () => { location.hash = `#/bot/${b.id}`; } },
      el("div", {},
        el("div", { class: "bot-name" }, el("span", { class: `statusdot ${b.status}` }), b.config.name,
          el("span", { class: `type-badge ${b.config.type}` }, b.config.type.toUpperCase()),
          el("span", { class: `pill ${b.paper ? "paper" : "live"}` }, b.paper ? "PAPER" : "LIVE")),
        el("div", { class: "bot-sub" }, `${b.config.symbol} · ${b.account?.name || "—"} · ${b.config.timeframe}`)),
      el("div", { class: "bot-metric" }, el("div", { class: "l" }, "Price"),
        el("div", { class: "v" }, fmtNum(b.price, 2))),
      el("div", { class: "bot-metric" }, el("div", { class: "l" }, "Realized PnL"),
        el("div", { class: `v ${pnlCls(pnl)}` }, fmtMoney(pnl))),
      el("div", { class: "bot-metric" }, el("div", { class: "l" },
        st.roundtrips !== undefined ? "Roundtrips" : st.wins !== undefined ? "W / L" : "Cycles"),
        el("div", { class: "v" }, st.roundtrips ?? (st.wins !== undefined ? `${st.wins}/${st.losses}` : st.cycles_done) ?? "—")),
      el("div", { class: "bot-metric" }, el("div", { class: "l" }, "Open Orders"),
        el("div", { class: "v" }, String((b.open_orders || []).length))),
      el("div", { class: "btn-row", onclick: (e) => e.stopPropagation() },
        running
          ? el("button", { class: "btn sm danger", onclick: () => act(b.id, "stop") }, "Stop")
          : el("button", { class: "btn sm primary", onclick: () => act(b.id, "start") }, "Start"),
        running
          ? el("button", { class: "btn sm", onclick: () => act(b.id, "pause") }, "Pause")
          : b.status === "paused"
            ? el("button", { class: "btn sm", onclick: () => act(b.id, "resume") }, "Resume")
            : null,
        el("button", { class: "btn sm ghost", title: "Delete", onclick: () => removeBot(b) }, "Delete")));
    return card;
  }

  async function act(id, op) {
    try {
      await post(`/bots/${id}/${op}`);
      toast(`Bot ${op}ed`, "ok");
      await refresh();
    } catch (e) { toast(e.message, "err"); }
  }

  async function removeBot(b) {
    if (!confirm(`Delete bot "${b.config.name}" and its history?`)) return;
    try {
      if (b.status === "running") await post(`/bots/${b.id}/stop`);
      await del(`/bots/${b.id}`);
      toast("Bot deleted", "ok");
      await refresh();
    } catch (e) { toast(e.message, "err"); }
  }

  unsub = onWs((topic, p) => {
    if (topic === "bot_event" && p.type === "status") refresh();
    if (topic === "bot_event" && p.type === "trade") refresh();
  });
  return () => { if (unsub) unsub(); unsub = null; };
}

let accountsCache = null;

async function openWizard() {
  let accounts;
  try {
    accounts = (await get("/accounts")).accounts;
  } catch (e) {
    toast(e.message, "err");
    return;
  }
  if (!accounts.length) {
    toast("No accounts yet — create a paper account, it takes 10 seconds", "");
    await openAccountForm(null, () => openWizard());
    return;
  }

  const state = {
    step: 0,
    type: "grid",
    viewMode: "simple",
    account: accounts[0].id,
    symbol: "BTC/USDT",
    market_type: "spot",
    timeframe: "5m",
    tick: 1,
    grid: defaultGrid(),
    dca: defaultDca(),
    combo: { grid_share_pct: 60 },
    modules: defaultModules(),
    risk: { max_position_quote: 0, daily_max_loss_quote: 0, max_leverage: 1, max_open_orders: 100, kill_switch_drawdown_pct: 0 },
    execution: { leverage: 1, margin_mode: "isolated", time_in_force: "gtc", slippage_tolerance_pct: 0, log_level: "info" },
    startNow: true,
  };

  const TYPES = [
    { id: "grid", title: "Grid", desc: "Buy low / sell high on a price ladder. Arithmetic or geometric levels, infinity rebuild, inventory skew. Best in ranging markets. Extendable with trend / volatility / regime modules." },
    { id: "dca", title: "DCA", desc: "Base order + martingale safety orders, TP from average entry, trend filter, short mode. Best in corrections. Extendable with volatility / regime modules." },
    { id: "combo", title: "Combo", desc: "Grid and DCA on one pair with a split budget. Extendable with the same add-on modules." },
  ];

  const body = el("div", {});
  const steps = el("div", { class: "wizard-steps", style: "margin: 10px 22px 12px" });
  for (let i = 0; i < 5; i++) steps.append(el("div", { class: "wstep" }));
  const modal = openModal("New Bot — setup wizard", [steps, body], {
    wide: true,
    foot: [
      el("button", { class: "btn ghost", onclick: () => back() }, "Back"),
      el("button", { class: "btn primary", onclick: () => next() }, "Next"),
    ],
  });
  const backBtn = modal.buttons[0];
  const nextBtn = modal.buttons[1];

  // symbol search state at wizard scope — loadSymbols must always see it
  let symLoaded = "";
  let pairs = [];
  let symInput = null;
  let symList = null;
  let feedLine = null;

  function back() {
    if (state.step === 0) return modal.close();
    state.step--;
    draw();
  }

  function next() {
    if (state.step === 4) return finish();
    if (state.step === 1) {
      if (!state.symbol) { toast("Enter a symbol first", "err"); return; }
      loadSymbols(); // fire-and-forget — Next never waits on the network
    }
    state.step++;
    draw();
  }

  function draw() {
    steps.querySelectorAll(".wstep").forEach((s, i) => s.classList.toggle("on", i <= state.step));
    body.innerHTML = "";
    backBtn.textContent = state.step === 0 ? "Cancel" : "Back";
    nextBtn.textContent = state.step === 4 ? "Create Bot" : "Next";
    nextBtn.disabled = false;
    if (state.step === 0) drawType();
    if (state.step === 1) drawAccount();
    if (state.step === 2) drawParams();
    if (state.step === 3) drawRisk();
    if (state.step === 4) drawReview();
  }

  function drawType() {
    const grid = el("div", { class: "grid", style: "grid-template-columns: repeat(3, 1fr)" });
    for (const t of TYPES) {
      grid.append(el("div", {
        class: "card", style: `cursor:pointer; border-color: ${state.type === t.id ? "var(--border-strong)" : "var(--border)"}; box-shadow: ${state.type === t.id ? "var(--glow)" : "none"}`,
        onclick: () => { state.type = t.id; draw(); },
      },
        el("div", { class: "card-body" },
          el("div", { style: "display:flex; align-items:center; justify-content:space-between; margin-bottom:9px" },
            el("span", { class: `type-badge ${t.id}` }, t.title.toUpperCase()),
            state.type === t.id ? el("span", { style: "color:var(--accent); font-size:11px" }, "selected") : null),
          el("div", { class: "muted", style: "font-size:11.5px; line-height:1.55; color:var(--muted)" }, t.desc))));
    }
    body.append(grid);
  }

  function feedExchangeOf(acc) {
    return acc && acc.paper ? (acc.underlying || "bybit") : (acc ? acc.exchange : "bybit");
  }

  function drawAccount() {
    const accSel = selectEl(accounts.map((a) => ({ value: a.id, label: `${a.name || a.id} · ${a.exchange}${a.paper ? " (paper)" : " · LIVE"}` })), state.account,
      { onchange: (e) => { state.account = e.target.value; symLoaded = ""; pairs = []; loadSymbols(); checkFeed(); } });
    const mtSel = selectEl([{ value: "spot", label: "Spot" }, { value: "perp", label: "Perpetual (USDT-M)" }], state.market_type,
      { onchange: (e) => { state.market_type = e.target.value; state.symbol = state.symbol.split(":")[0]; symLoaded = ""; pairs = []; loadSymbols(); } });
    symInput = input({ value: state.symbol, placeholder: "BTC/USDT", onchange: (e) => { state.symbol = e.target.value.toUpperCase(); loadSymbols(); } });
    symList = el("div", { class: "hint", style: "max-height:150px; overflow-y:auto; margin-top:8px; display:flex; flex-wrap:wrap; gap:6px" });
    symInput.addEventListener("input", () => loadSymbols());
    feedLine = el("div", { class: "hint", style: "margin:0" }, "");
    body.append(
      el("div", { class: "form-grid" },
        field("Account", accSel),
        field("Market type", mtSel, "Perp requires a perp-capable account"),
        field("Symbol", symInput, "Type any symbol (e.g. BTC/USDT) or pick below")),
      el("div", { class: "hint", style: "display:flex; gap:8px; align-items:center; margin:2px 0 10px" },
        feedLine),
      el("div", { style: "display:flex; gap:10px; align-items:center; margin:4px 0 10px" },
        el("span", { class: "hint", style: "margin:0" }, "Only paper accounts? Add a live exchange account with your API keys:"),
        el("button", {
          class: "btn sm", onclick: async () => {
            await openAccountForm(null, async () => {
              accountsCache = (await get("/accounts")).accounts;
              accounts = accountsCache;
              state.account = accountsCache[accountsCache.length - 1].id;
              symLoaded = ""; pairs = [];
              draw();
            });
          },
        }, "+ Add live account")),
      el("div", { class: "sect-title" }, "Popular pairs"),
      symList,
      el("div", { class: "form-grid", style: "margin-top:8px" },
        field("Chart / filter timeframe", selectEl(["1m", "5m", "15m", "1h", "4h"], state.timeframe, { onchange: (e) => { state.timeframe = e.target.value; } }), "Used for candles & trend filters"),
        field("Tick interval (s)", numInput(state.tick, "0.1", { onchange: (e) => { state.tick = parseFloat(e.target.value) || 1; } }), "Bot loop frequency")));
    loadSymbols();
    checkFeed();
  }

  function checkFeed() {
    if (!feedLine) return;
    const acc = accounts.find((a) => a.id === state.account);
    const ex = feedExchangeOf(acc);
    feedLine.textContent = `checking ${ex} market data…`;
    feedLine.style.color = "";
    get(`/market/ticker?exchange=${ex}&symbol=BTC/USDT`)
      .then((r) => {
        if (!feedLine) return;
        const src = r.source && r.source !== ex ? `${r.last || r.bid} via ${r.source}` : `${r.last || r.bid}`;
        feedLine.textContent = `feed OK — BTC/USDT ${src}`;
        feedLine.style.color = "var(--green)";
      })
      .catch(() => {
        if (!feedLine) return;
        feedLine.textContent = `feed "${ex}" unreachable — paper bots fall back to another source or simulated prices`;
        feedLine.style.color = "var(--red)";
      });
  }

  function drawChips(msg, err) {
    symList.innerHTML = "";
    if (msg) symList.append(el("span", { class: "chip", style: err ? "color:var(--red); border-color:rgba(255,77,94,.5)" : "opacity:.6" }, msg));
  }

  async function loadSymbols() {
    if (!symList) return;
    const acc = accounts.find((a) => a.id === state.account);
    const ex = feedExchangeOf(acc);
    const kind = state.market_type === "perp" ? "perp" : "spot";
    if (symLoaded === ex + kind + state.symbol) return;
    symLoaded = ex + kind + state.symbol;
    drawChips("loading pairs…");
    try {
      const res = await get(`/market/symbols?exchange=${ex}&kind=${kind}&q=${encodeURIComponent(state.symbol || "")}`);
      if (!symList || symLoaded !== ex + kind + state.symbol) return;
      pairs = res.symbols || [];
      symList.innerHTML = "";
      if (res.source && res.source !== ex) {
        symList.append(el("span", { class: "chip", style: "color:var(--accent)" }, `source ${ex} unreachable — serving from ${res.source}`));
      }
      for (const s of pairs.slice(0, 40)) {
        symList.append(el("span", {
          class: "chip", style: "cursor:pointer",
          onclick: () => { state.symbol = s; if (symInput) symInput.value = s; },
        }, s.replace(":USDT", " ⚡")));
      }
      if (!pairs.length) {
        symList.append(el("span", { class: "chip", style: "color:var(--red); border-color:rgba(255,77,94,.5)" },
          `no pairs for "${state.symbol}" — you can still create the bot with a typed symbol`));
      }
    } catch (e) {
      if (!symList) return;
      pairs = [];
      drawChips(`${ex} unreachable — type the symbol manually (e.g. BTC/USDT), the bot will use a fallback feed`, true);
    }
  }

  const PRESETS = {
    grid: [
      { name: "Calm BTC sideways", patch: { levels: 24, grid_type: "geometric", post_only: true, fee_buffer_pct: 0.05, skew_enabled: true, on_exit: "rebuild", spacing_mode: "auto", upper_price: 0, lower_price: 0 } },
      { name: "Tight scalper", patch: { levels: 40, grid_type: "geometric", post_only: true, fee_buffer_pct: 0.02, on_exit: "rebuild", spacing_mode: "manual", spacing_value: 0.15 } },
      { name: "Wide swing net", patch: { levels: 15, grid_type: "arithmetic", post_only: false, on_exit: "hold", spacing_mode: "manual", spacing_value: 250 } },
    ],
    dca: [
      { name: "Moderate martingale", patch: { base_order_pct: 15, max_safety_orders: 10, safety_deviation_pct: 1.5, safety_step_scale: 1.3, volume_scale: 1.4, take_profit_pct: 1.5, stop_loss_pct: 0, entry_condition: "immediate" } },
      { name: "Deep dip hunter", patch: { base_order_pct: 10, max_safety_orders: 12, safety_deviation_pct: 2.5, safety_step_scale: 1.5, volume_scale: 1.6, take_profit_pct: 2, stop_loss_pct: 30 } },
      { name: "Trend rider only", patch: { base_order_pct: 20, max_safety_orders: 6, safety_deviation_pct: 1.2, volume_scale: 1.3, take_profit_pct: 1.2, entry_condition: "ema", ema_period: 50, trend_filter_timeframe: "4h" } },
    ],
  };

  function drawParams() {
    const modeBar = el("div", { class: "btn-row", style: "margin-bottom:16px" },
      el("span", { class: "hint", style: "margin:0; text-transform:uppercase; letter-spacing:1px" }, "Config mode:"),
      ...["simple", "advanced", "expert"].map((m) =>
        el("button", {
          class: `btn sm ${state.viewMode === m ? "primary" : ""}`,
          onclick: () => { state.viewMode = m; draw(); },
        }, m.toUpperCase())));
    body.append(modeBar);
    if (PRESETS[state.type]) {
      body.append(el("div", { class: "btn-row", style: "margin-bottom:16px" },
        el("span", { class: "hint", style: "margin:0; text-transform:uppercase; letter-spacing:1px" }, "Presets:"),
        ...PRESETS[state.type].map((p) =>
          el("span", {
            class: "chip", style: "cursor:pointer",
            onclick: () => { Object.assign(state[state.type], p.patch); draw(); toast(`Preset applied: ${p.name}`, "ok"); },
          }, p.name))));
    }
    if (state.viewMode === "expert") {
      drawExpert();
      body.append(el("div", { style: "height:14px" }));
    } else if (state.type === "grid") body.append(gridForm(state.grid));
    else if (state.type === "dca") body.append(dcaForm(state.dca));
    else {
      body.append(el("div", { class: "sect-title" }, "Combo split"));
      body.append(el("div", { class: "form-grid" },
        field("Grid share (%)", numInput(state.combo.grid_share_pct, "1", { onchange: (e) => { state.combo.grid_share_pct = parseFloat(e.target.value) || 60; } }), "Remaining budget goes to the DCA leg")));
      const g = defaultGrid(), d = defaultDca();
      body.append(el("div", { class: "sect-title" }, "Grid leg"));
      body.append(gridForm(g));
      state.grid = g;
      body.append(el("div", { class: "sect-title" }, "DCA leg"));
      body.append(dcaForm(d));
      state.dca = d;
    }
    body.append(modulesForm(state));
  }

  function strategyKey() {
    return { grid: "grid", dca: "dca" }[state.type] || "grid";
  }

  function drawExpert() {
    const key = strategyKey();
    let obj = {};
    if (state.type === "combo") {
      obj = { combo: state.combo, grid: state.grid, dca: state.dca };
    } else {
      obj = state[key];
    }
    const pre = el("textarea", {
      rows: 22, style: "width:100%; font-family:var(--mono); font-size:11.5px; background:rgba(255,255,255,.03); color:var(--text); border:1px solid rgba(255,255,255,.1); border-radius:7px; padding:12px",
      spellcheck: "false",
    });
    pre.value = JSON.stringify(obj, null, 2);
    pre.addEventListener("change", () => {
      try {
        const parsed = JSON.parse(pre.value);
        if (state.type === "combo") {
          if (parsed.combo) state.combo = parsed.combo;
          if (parsed.grid) state.grid = parsed.grid;
          if (parsed.dca) state.dca = parsed.dca;
        } else {
          state[key] = parsed;
        }
        toast("JSON applied", "ok");
      } catch (e) {
        toast("Invalid JSON: " + e.message, "err");
      }
    });
    body.append(el("div", { class: "hint", style: "margin-bottom:10px" },
      "Expert mode — edit the raw strategy config. Changes apply on blur. Modules are configured below."));
    body.append(pre);
  }

  function moduleToggle(label, mod, hint) {
    const cb = el("input", {
      type: "checkbox", ...(mod.enabled ? { checked: true } : {}),
      onchange: (e) => { mod.enabled = e.target.checked; draw(); },
    });
    const wrap = el("label", { class: "module-toggle" },
      el("span", { class: "switch" }, cb, el("span", { class: "slider" })),
      el("span", { style: "flex:1" },
        el("div", { style: "font-weight:600; font-size:13px" }, label),
        hint ? el("div", { class: "hint", style: "margin:0" }, hint) : null));
    return wrap;
  }

  function modulesForm(st) {
    const m = st.modules;
    const simple = state.viewMode === "simple";
    const wrap = el("div", {});
    wrap.append(el("div", { class: "sect-title" }, "Optional modules — add-ons on top of the strategy"));
    wrap.append(el("div", { class: "hint", style: "margin-bottom:10px" },
      "Flip a switch to enable a module — its settings expand below. Modules don't trade by themselves: they decide WHEN the bot may open new positions. Exits and safety orders always keep working."));

    const tf = m.trend_filter;
    wrap.append(moduleToggle("Trend filter (Momentum)", tf, "Enter only in the direction of the EMA trend"));
    if (tf.enabled) {
      const rows = [
        field("EMA period", numInput(tf.ema_period, "1", { onchange: (e) => tf.ema_period = parseInt(e.target.value) || 50 })),
        field("Timeframe", selectEl(["15m", "1h", "4h"], tf.timeframe, { onchange: (e) => tf.timeframe = e.target.value })),
      ];
      wrap.append(el("div", { class: "form-grid module-settings" }, ...rows));
      if (state.type === "dca") {
        wrap.append(el("div", { class: "hint", style: "margin:2px 0 10px" },
          "For DCA bots this mirrors the Entry condition section above — pick one place to configure it."));
      }
    }

    const vg = m.vol_guard;
    wrap.append(moduleToggle("Volatility guard", vg, "Pause new entries when ATR% spikes above the limit"));
    if (vg.enabled) {
      wrap.append(el("div", { class: "form-grid module-settings" },
        field("ATR limit %", numInput(vg.threshold_pct, "0.1", { onchange: (e) => vg.threshold_pct = parseFloat(e.target.value) || 4 }), "Entries pause when ATR% goes above"),
        field("ATR period", numInput(vg.atr_period, "1", { onchange: (e) => vg.atr_period = parseInt(e.target.value) || 14 })),
        field("Timeframe", selectEl(["15m", "1h", "4h"], vg.timeframe, { onchange: (e) => vg.timeframe = e.target.value }))));
    }

    const rg = m.regime;
    wrap.append(moduleToggle("Regime control (Adaptive)", rg, "ADX / ATR / Bollinger regime filter"));
    if (rg.enabled) {
      wrap.append(el("div", { class: "form-grid module-settings" },
        field("Regime timeframe", selectEl(["15m", "1h", "4h", "1d"], rg.timeframe, { onchange: (e) => rg.timeframe = e.target.value })),
        field("ADX trend threshold", numInput(rg.adx_trend_threshold, "0.5", { onchange: (e) => rg.adx_trend_threshold = parseFloat(e.target.value) || 23 }), "ADX above = trending"),
        field("In a trend", selectEl([
          { value: "pause", label: "Pause new entries" },
          { value: "continue", label: "Keep trading" }], rg.in_trend_action, { onchange: (e) => rg.in_trend_action = e.target.value })),
        field("On high volatility", selectEl([
          { value: "stand_aside", label: "Stand aside — no new entries" },
          { value: "continue", label: "Continue trading" }], rg.volatile_action, { onchange: (e) => rg.volatile_action = e.target.value }))));
    }

    if (simple) {
      wrap.append(el("div", { class: "hint", style: "margin-top:6px" },
        "Switch to ADVANCED to tune module parameters (EMA period, ATR limits, ADX thresholds)."));
    }
    return wrap;
  }

  function defaultModules() {
    return {
      trend_filter: { enabled: false, timeframe: "1h", ema_period: 50 },
      vol_guard: { enabled: false, timeframe: "1h", atr_period: 14, threshold_pct: 4 },
      regime: { enabled: false, timeframe: "1h", adx_period: 14, adx_trend_threshold: 23,
                atr_period: 14, atr_high_pct: 4, bb_period: 20,
                in_trend_action: "pause", volatile_action: "stand_aside" },
    };
  }

  function defaultGrid() {
    return {
      total_budget_quote: 1000, upper_price: 0, lower_price: 0, levels: 20,
      grid_type: "geometric", grid_mode: "neutral", spacing_mode: "auto", spacing_value: 0,
      per_level_mode: "budget_split", per_level_quote: 0, per_level_qty: 0,
      fee_buffer_pct: 0.05, post_only: true, infinite: true, use_inventory: true, on_exit: "rebuild",
      trailing_up: false, skew_enabled: true, skew_max_shift: 2, rebalance_mode: "inventory",
      max_active_orders: 0, max_inventory_base: 0, min_notional_quote: 0, price_source: "mid",
      order_timeout_min: 0, reopen_delay_s: 5, stop_loss_pct: 0, stop_loss_price: 0,
      stop_loss_close_inventory: true, take_profit_pct: 0, tp_includes_fees: true,
      max_drawdown_pct: 0, pause_volatility_atr_pct: 0,
    };
  }

  function defaultDca() {
    return {
      total_budget_quote: 1000, base_order_pct: 20, base_order_quote: 0,
      max_safety_orders: 5, safety_order_quote: 0, safety_size_mode: "fixed_quote",
      safety_deviation_pct: 2, safety_step_scale: 1.3, volume_scale: 1.5,
      take_profit_pct: 1.5, tp_from: "avg", min_profit_pct: 0, tp_includes_fees: true,
      trailing_tp_enabled: false, trailing_tp_deviation_pct: 0.4, stop_loss_pct: 0,
      short_mode: false, entry_condition: "immediate", cooldown_minutes: 0,
      trend_filter_timeframe: "1h", trend_filter_type: "ema", ema_period: 50, ema_side: "above",
      rsi_period: 14, rsi_max: 70, max_active_cycles: 1, new_cycle_deviation_pct: 3,
      safety_order_type: "market",
    };
  }

  function gridForm(g) {
    const simple = state.viewMode === "simple";
    const wrap = el("div", {});
    wrap.append(el("div", { class: "sect-title" }, "Budget & range"));
    wrap.append(el("div", { class: "form-grid" },
      field("Total budget (quote)", numInput(g.total_budget_quote, "10", { onchange: (e) => g.total_budget_quote = parseFloat(e.target.value) || 1000 }), "Quote currency allocated to the grid"),
      field("Upper price (0 = auto ±8%)", numInput(g.upper_price, "any", { onchange: (e) => g.upper_price = parseFloat(e.target.value) || 0 })),
      field("Lower price (0 = auto)", numInput(g.lower_price, "any", { onchange: (e) => g.lower_price = parseFloat(e.target.value) || 0 })),
      field("Levels", numInput(g.levels, "1", { onchange: (e) => g.levels = parseInt(e.target.value) || 20 }), "Number of grid lines"),
      field("Grid type", selectEl(["arithmetic", "geometric"], g.grid_type, { onchange: (e) => g.grid_type = e.target.value }), "Geometric = equal % steps (recommended)"),
      field("Grid mode", selectEl([
        { value: "neutral", label: "Neutral — buys below, sells above" },
        { value: "long", label: "Long — accumulate, sells after fills" },
        { value: "short", label: "Short — sells first (perp)" }], g.grid_mode, { onchange: (e) => g.grid_mode = e.target.value })),
      simple ? field("Post-only orders", selectEl([{ value: "yes", label: "Yes (maker fees)" }, { value: "no", label: "No" }], g.post_only ? "yes" : "no", { onchange: (e) => g.post_only = e.target.value === "yes" })) : null));
    if (!simple) {
      wrap.append(el("div", { class: "sect-title" }, "Grid spacing & sizing"));
      wrap.append(el("div", { class: "form-grid" },
        field("Spacing mode", selectEl([{ value: "auto", label: "Auto — even range split" }, { value: "manual", label: "Manual — fixed step" }], g.spacing_mode, { onchange: (e) => g.spacing_mode = e.target.value })),
        field("Spacing value (% geo / quote arith)", numInput(g.spacing_value, "any", { onchange: (e) => g.spacing_value = parseFloat(e.target.value) || 0 }), "e.g. 0.5 = 0.5% step for geometric"),
        field("Per-level size", selectEl([
          { value: "budget_split", label: "budget / levels" },
          { value: "fixed_quote", label: "fixed quote per level" },
          { value: "fixed_qty", label: "fixed base qty per level" }], g.per_level_mode, { onchange: (e) => g.per_level_mode = e.target.value })),
        field("Fixed quote per level", numInput(g.per_level_quote, "any", { onchange: (e) => g.per_level_quote = parseFloat(e.target.value) || 0 })),
        field("Fixed qty per level", numInput(g.per_level_qty, "any", { onchange: (e) => g.per_level_qty = parseFloat(e.target.value) || 0 })),
        field("Rebalance on fill", selectEl([
          { value: "inventory", label: "Inventory — sell exact bought qty" },
          { value: "classic", label: "Classic — fixed level size" }], g.rebalance_mode, { onchange: (e) => g.rebalance_mode = e.target.value }))));
      wrap.append(el("div", { class: "sect-title" }, "Execution"));
      wrap.append(el("div", { class: "form-grid" },
        field("Fee buffer (% per side)", numInput(g.fee_buffer_pct, "0.01", { onchange: (e) => g.fee_buffer_pct = parseFloat(e.target.value) ?? 0.05 }), "Extra step margin over fees"),
        field("Post-only orders", selectEl([{ value: "yes", label: "Yes (maker, ALO)" }, { value: "no", label: "No" }], g.post_only ? "yes" : "no", { onchange: (e) => g.post_only = e.target.value === "yes" })),
        field("Price source", selectEl([
          { value: "mid", label: "Mid price (bid/ask)" },
          { value: "last", label: "Last trade price" }], g.price_source, { onchange: (e) => g.price_source = e.target.value })),
        field("Max active orders (0=off)", numInput(g.max_active_orders, "1", { onchange: (e) => g.max_active_orders = parseInt(e.target.value) || 0 }), "Caps simultaneous orders"),
        field("Order timeout (min, 0=off)", numInput(g.order_timeout_min, "1", { onchange: (e) => g.order_timeout_min = parseInt(e.target.value) || 0 }), "Cancel & re-place stale orders"),
        field("Reopen delay (s)", numInput(g.reopen_delay_s, "1", { onchange: (e) => g.reopen_delay_s = parseInt(e.target.value) || 0 })),
        field("Min notional override (0=exchange)", numInput(g.min_notional_quote, "any", { onchange: (e) => g.min_notional_quote = parseFloat(e.target.value) || 0 }))));
      wrap.append(el("div", { class: "sect-title" }, "Range behaviour"));
      wrap.append(el("div", { class: "form-grid" },
        field("Out of range", selectEl([
          { value: "rebuild", label: "Infinity — rebuild around price" },
          { value: "hold", label: "Hold and wait" },
          { value: "stop", label: "Stop the bot" }], g.on_exit, { onchange: (e) => g.on_exit = e.target.value }),
          "Applied when price leaves the grid range"),
        field("Trailing up only", selectEl([{ value: "yes", label: "Yes — rebuild only upward" }, { value: "no", label: "Both directions" }], g.trailing_up ? "yes" : "no", { onchange: (e) => g.trailing_up = e.target.value === "yes" })),
        field("Skew (inventory shift)", selectEl([{ value: "yes", label: "Enabled" }, { value: "no", label: "Disabled" }], g.skew_enabled ? "yes" : "no", { onchange: (e) => g.skew_enabled = e.target.value === "yes" }), "Shift sell levels closer when inventory grows"),
        field("Max skew shift (levels)", numInput(g.skew_max_shift, "1", { onchange: (e) => g.skew_max_shift = parseInt(e.target.value) || 0 })),
        field("Max inventory (base, 0=off)", numInput(g.max_inventory_base, "any", { onchange: (e) => g.max_inventory_base = parseFloat(e.target.value) || 0 })),
        field("Initial sells from inventory", selectEl([{ value: "yes", label: "Yes — place sells if you hold base" }, { value: "no", label: "No — buys only at start" }], g.use_inventory ? "yes" : "no", { onchange: (e) => g.use_inventory = e.target.value === "yes" }))));
    }
    wrap.append(el("div", { class: "sect-title" }, "Exit conditions & risk"));
    wrap.append(el("div", { class: "form-grid" },
      field("Stop loss % below lower (0=off)", numInput(g.stop_loss_pct, "0.1", { onchange: (e) => g.stop_loss_pct = parseFloat(e.target.value) || 0 })),
      simple ? null : field("Stop loss price (absolute, 0=off)", numInput(g.stop_loss_price, "any", { onchange: (e) => g.stop_loss_price = parseFloat(e.target.value) || 0 })),
      simple ? null : field("Max drawdown % (0=off)", numInput(g.max_drawdown_pct, "0.5", { onchange: (e) => g.max_drawdown_pct = parseFloat(e.target.value) || 0 }), "Stops bot on PnL drawdown from peak"),
      simple ? null : field("Pause when ATR% above (0=off)", numInput(g.pause_volatility_atr_pct, "0.1", { onchange: (e) => g.pause_volatility_atr_pct = parseFloat(e.target.value) || 0 }), "Pauses new placements in volatility spikes"),
      field("Close inventory on SL", selectEl([{ value: "yes", label: "Yes — market sell" }, { value: "no", label: "Keep inventory" }], g.stop_loss_close_inventory ? "yes" : "no", { onchange: (e) => g.stop_loss_close_inventory = e.target.value === "yes" })),
      field("Take profit % of budget (0=off)", numInput(g.take_profit_pct, "0.1", { onchange: (e) => g.take_profit_pct = parseFloat(e.target.value) || 0 }), "Stops the bot when realized grid profit reaches this"),
      simple ? null : field("TP includes fees", selectEl([{ value: "yes", label: "Yes — target is net of fees" }, { value: "no", label: "No — raw PnL" }], g.tp_includes_fees ? "yes" : "no", { onchange: (e) => g.tp_includes_fees = e.target.value === "yes" }))));
    return wrap;
  }

  function dcaForm(d) {
    const simple = state.viewMode === "simple";
    const wrap = el("div", {});
    wrap.append(el("div", { class: "sect-title" }, "Base order"));
    wrap.append(el("div", { class: "form-grid" },
      field("Total cycle budget (quote)", numInput(d.total_budget_quote, "10", { onchange: (e) => d.total_budget_quote = parseFloat(e.target.value) || 1000 }), "Must fit base + safety orders, or safety orders get trimmed"),
      field("Base order (% of budget)", numInput(d.base_order_pct, "1", { onchange: (e) => d.base_order_pct = parseFloat(e.target.value) || 20 })),
      simple ? null : field("Base order (quote, overrides %)", numInput(d.base_order_quote, "any", { onchange: (e) => d.base_order_quote = parseFloat(e.target.value) || 0 })),
      field("Direction", selectEl([{ value: "long", label: "Long (spot/futures)" }, { value: "short", label: "Short (futures)" }], d.short_mode ? "short" : "long", { onchange: (e) => d.short_mode = e.target.value === "short" }))));

    wrap.append(el("div", { class: "sect-title" }, "Safety orders (martingale)"));
    wrap.append(el("div", { class: "form-grid" },
      field("Max safety orders", numInput(d.max_safety_orders, "1", { onchange: (e) => d.max_safety_orders = parseInt(e.target.value) || 0 })),
      field("Safety order size (0=base size)", numInput(d.safety_order_quote, "any", { onchange: (e) => d.safety_order_quote = parseFloat(e.target.value) || 0 }), d.safety_size_mode === "fixed_quote" ? "In quote" : "In % — see size mode"),
      simple ? null : field("Safety size mode", selectEl([
        { value: "fixed_quote", label: "Fixed quote" },
        { value: "pct_of_base", label: "% of base order" },
        { value: "pct_of_capital", label: "% of capital" }], d.safety_size_mode, { onchange: (e) => d.safety_size_mode = e.target.value })),
      field("Price deviation % (first SO)", numInput(d.safety_deviation_pct, "0.1", { onchange: (e) => d.safety_deviation_pct = parseFloat(e.target.value) || 2 })),
      field("Deviation step scale ×", numInput(d.safety_step_scale, "0.05", { onchange: (e) => d.safety_step_scale = parseFloat(e.target.value) || 1.3 }), "Each next SO triggers ×scale deeper"),
      field("Volume scale ×", numInput(d.volume_scale, "0.05", { onchange: (e) => d.volume_scale = parseFloat(e.target.value) || 1.5 }), "Each next SO size ×scale"),
      simple ? null : field("Safety order type", selectEl(["market", "limit"], d.safety_order_type, { onchange: (e) => d.safety_order_type = e.target.value }))));

    wrap.append(el("div", { class: "sect-title" }, "Exit & protection"));
    wrap.append(el("div", { class: "form-grid" },
      field("Take profit %", numInput(d.take_profit_pct, "0.1", { onchange: (e) => d.take_profit_pct = parseFloat(e.target.value) || 1.5 }), simple ? "From average entry" : null),
      simple ? null : field("TP from", selectEl([{ value: "avg", label: "Average entry price" }, { value: "base", label: "Base order price" }], d.tp_from, { onchange: (e) => d.tp_from = e.target.value })),
      simple ? null : field("Min profit to close %", numInput(d.min_profit_pct, "0.1", { onchange: (e) => d.min_profit_pct = parseFloat(e.target.value) || 0 }), "TP won't fire below this realized %"),
      simple ? null : field("TP includes fees", selectEl([{ value: "yes", label: "Yes — net of fees" }, { value: "no", label: "No" }], d.tp_includes_fees ? "yes" : "no", { onchange: (e) => d.tp_includes_fees = e.target.value === "yes" })),
      field("Trailing TP", selectEl([{ value: "yes", label: "Enabled" }, { value: "no", label: "Disabled" }], d.trailing_tp_enabled ? "yes" : "no", { onchange: (e) => d.trailing_tp_enabled = e.target.value === "yes" })),
      field("Trailing deviation %", numInput(d.trailing_tp_deviation_pct, "0.05", { onchange: (e) => d.trailing_tp_deviation_pct = parseFloat(e.target.value) || 0.4 })),
      field("Stop loss % (from avg, 0=off)", numInput(d.stop_loss_pct, "0.1", { onchange: (e) => d.stop_loss_pct = parseFloat(e.target.value) || 0 })),
      field("Cooldown between cycles (min)", numInput(d.cooldown_minutes, "1", { onchange: (e) => d.cooldown_minutes = parseInt(e.target.value) || 0 }))));

    wrap.append(el("div", { class: "sect-title" }, "Entry conditions"));
    wrap.append(el("div", { class: "form-grid" },
      field("Entry condition", selectEl([
        { value: "immediate", label: "Start cycle immediately" },
        { value: "ema", label: "EMA trend filter" },
        { value: "rsi", label: "RSI filter" }], d.entry_condition, { onchange: (e) => d.entry_condition = e.target.value }), "New cycles open only when condition passes"),
      d.entry_condition === "immediate" ? null : field("Filter timeframe", selectEl(["5m", "15m", "1h", "4h", "1d"], d.trend_filter_timeframe, { onchange: (e) => d.trend_filter_timeframe = e.target.value })),
      d.entry_condition === "ema" ? field("EMA period", numInput(d.ema_period, "1", { onchange: (e) => d.ema_period = parseInt(e.target.value) || 50 })) : null,
      d.entry_condition === "ema" ? field("Long allowed when price", selectEl([{ value: "above", label: "Above EMA" }, { value: "below", label: "Below EMA" }], d.ema_side, { onchange: (e) => d.ema_side = e.target.value })) : null,
      d.entry_condition === "rsi" ? field("RSI period", numInput(d.rsi_period, "1", { onchange: (e) => d.rsi_period = parseInt(e.target.value) || 14 })) : null,
      d.entry_condition === "rsi" ? field("RSI max (long entries below)", numInput(d.rsi_max, "1", { onchange: (e) => d.rsi_max = parseFloat(e.target.value) || 70 })) : null,
      simple ? null : field("Max active cycles", numInput(d.max_active_cycles, "1", { onchange: (e) => d.max_active_cycles = parseInt(e.target.value) || 1 }), "Concurrent DCA cycles"),
      simple ? null : field("New cycle deviation %", numInput(d.new_cycle_deviation_pct, "0.1", { onchange: (e) => d.new_cycle_deviation_pct = parseFloat(e.target.value) || 3 }), "Next cycle opens this % beyond the last cycle's base")));
    return wrap;
  }

  function drawRisk() {
    const r = state.risk;
    body.append(el("div", { class: "sect-title" }, "Risk management"));
    body.append(el("div", { class: "form-grid" },
      field("Max position (quote, 0=off)", numInput(r.max_position_quote, "10", { onchange: (e) => r.max_position_quote = parseFloat(e.target.value) || 0 })),
      field("Daily max loss (quote, 0=off)", numInput(r.daily_max_loss_quote, "10", { onchange: (e) => r.daily_max_loss_quote = parseFloat(e.target.value) || 0 }), "Bot pauses automatically when hit"),
      field("Max leverage", numInput(r.max_leverage, "0.1", { onchange: (e) => r.max_leverage = parseFloat(e.target.value) || 1 })),
      field("Max open orders", numInput(r.max_open_orders, "1", { onchange: (e) => r.max_open_orders = parseInt(e.target.value) || 100 })),
      field("Kill switch drawdown % (0=off)", numInput(r.kill_switch_drawdown_pct, "0.5", { onchange: (e) => r.kill_switch_drawdown_pct = parseFloat(e.target.value) || 0 }), "Stops the bot on equity drawdown from peak")));
    const ex = state.execution;
    body.append(el("div", { class: "sect-title" }, "Execution"));
    body.append(el("div", { class: "form-grid" },
      field("Leverage", numInput(ex.leverage, "1", { onchange: (e) => ex.leverage = parseFloat(e.target.value) || 1 }), "Perp only — margin usage multiplier"),
      field("Margin mode", selectEl(["isolated", "cross"], ex.margin_mode, { onchange: (e) => ex.margin_mode = e.target.value })),
      field("Time in force", selectEl([
        { value: "gtc", label: "GTC — good till canceled" },
        { value: "ioc", label: "IOC — immediate or cancel" },
        { value: "fok", label: "FOK — fill or kill" }], ex.time_in_force, { onchange: (e) => ex.time_in_force = e.target.value })),
      field("Slippage tolerance %", numInput(ex.slippage_tolerance_pct, "0.05", { onchange: (e) => ex.slippage_tolerance_pct = parseFloat(e.target.value) || 0 }), "Max acceptable slippage on market orders"),
      field("Log level", selectEl(["info", "debug"], ex.log_level, { onchange: (e) => ex.log_level = e.target.value }))));
    body.append(el("div", { class: "check" },
      el("input", { type: "checkbox", ...(state.startNow ? { checked: true } : {}), onchange: (e) => state.startNow = e.target.checked }),
      el("span", {}, "Start the bot immediately after creation")));
  }

  function drawReview() {
    const cfg = buildConfig();
    const pre = el("textarea", { rows: 16, style: "width:100%; font-family:var(--mono); font-size:11.5px; background:var(--panel-2); color:var(--text); border:1px solid var(--border-2); border-radius:9px; padding:12px" });
    pre.value = JSON.stringify(cfg, null, 2);
    body.append(el("div", { class: "sect-title" }, "Final configuration"), pre);
  }

  function buildConfig() {
    const cfg = {
      name: `${state.type.toUpperCase()} ${state.symbol.split(":")[0]}`,
      type: state.type,
      account_id: state.account,
      symbol: state.symbol,
      market_type: state.market_type,
      timeframe: state.timeframe,
      tick_interval_s: state.tick,
      risk: state.risk,
      execution: state.execution,
    };
    if (state.type === "grid") cfg.grid = state.grid;
    if (state.type === "dca") cfg.dca = state.dca;
    if (state.type === "combo") { cfg.grid = state.grid; cfg.dca = state.dca; cfg.combo = state.combo; }
    cfg.modules = state.modules;
    return cfg;
  }

  async function finish() {
    nextBtn.disabled = true;
    try {
      const res = await post("/bots", { config: buildConfig() });
      modal.close();
      toast("Bot created", "ok");
      if (state.startNow) {
        try { await post(`/bots/${res.bot.id}/start`); toast("Bot started", "ok"); } catch (e) { toast(e.message, "err"); }
      }
      location.hash = `#/bot/${res.bot.id}`;
    } catch (e) {
      toast(e.message, "err");
      nextBtn.disabled = false;
    }
  }

  draw();
}