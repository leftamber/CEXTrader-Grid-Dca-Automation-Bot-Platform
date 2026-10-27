import { get, post } from "../api.js";
import { el, fmtNum, fmtMoney, fmtDate, pnlCls, toast, pageHeader, statCard, field, numInput, selectEl } from "../ui.js";
import { CandleChart } from "../chart.js";

export async function render(container) {
  container.append(pageHeader("Backtest",
    el("span", { class: "muted", style: "font-size:12.5px" }, "Historical simulation on real candles — validates strategy settings before going live")));

  const form = el("div", { class: "card" });
  const results = el("div", {});
  container.append(form, el("div", { style: "height:16px" }), results);

  const state = {
    type: "grid", exchange: "bybit", symbol: "BTC/USDT", timeframe: "1h",
    candle_limit: 1500, maker_fee: 0.1, taker_fee: 0.1, balance: 1000,
    grid: gridDefaults(), dca: dcaDefaults(), momentum: momentumDefaults(), vwap: vwapDefaults(),
  };

  function gridDefaults() {
    return { total_budget_quote: 1000, upper_price: 0, lower_price: 0, levels: 20, grid_type: "geometric",
      per_level_mode: "budget_split", fee_buffer_pct: 0.05, post_only: false, infinite: true,
      on_exit: "rebuild", stop_loss_pct: 0, take_profit_pct: 0, max_inventory_base: 0, reopen_delay_s: 0 };
  }
  function dcaDefaults() {
    return { total_budget_quote: 1000, base_order_pct: 20, max_safety_orders: 5, safety_order_quote: 0,
      safety_deviation_pct: 2, safety_step_scale: 1.3, volume_scale: 1.5, take_profit_pct: 1.5,
      trailing_tp_enabled: false, trailing_tp_deviation_pct: 0.4, stop_loss_pct: 0, short_mode: false,
      cooldown_minutes: 0, trend_filter_enabled: false, trend_filter_timeframe: "1h",
      trend_filter_type: "ema", ema_period: 50, ema_side: "above", safety_order_type: "market" };
  }
  function momentumDefaults() {
    return { total_budget_quote: 1000, position_pct: 50, fast_ema: 12, slow_ema: 30,
      volume_filter_enabled: false, volume_ma_period: 20, volume_factor: 1.3,
      rsi_filter_enabled: false, rsi_period: 14, rsi_long_max: 70, rsi_short_min: 30,
      allow_short: false, stop_loss_pct: 3, take_profit_pct: 6,
      trailing_enabled: false, trailing_dev_pct: 1.5, exit_on_cross: true,
      cooldown_minutes: 0, max_hold_hours: 0 };
  }
  function vwapDefaults() {
    return { total_budget_quote: 1000, position_pct: 40, mode: "mean_reversion", vwap_window: 48,
      deviation_pct: 1, confirm_ema_period: 0, volume_factor: 0, volume_ma_period: 20,
      take_profit_pct: 2, stop_loss_pct: 2, allow_short: false, cooldown_minutes: 0, max_trades_per_day: 5 };
  }

  const runBtn = el("button", { class: "btn primary", onclick: run }, "Run Backtest");
  form.append(el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Backtest Configuration")),
    el("div", { class: "card-body" },
      el("div", { class: "form-grid" },
        field("Strategy", selectEl([
          { value: "grid", label: "Grid" }, { value: "dca", label: "DCA" },
          { value: "momentum", label: "Momentum (EMA cross)" }, { value: "vwap", label: "VWAP" }],
          state.type, { onchange: (e) => { state.type = e.target.value; drawParams(); } })),
        field("Exchange (public data)", selectEl(["bybit", "binance", "okx", "kraken", "coinbase", "bitget"], state.exchange, { onchange: (e) => state.exchange = e.target.value })),
        field("Symbol", el("input", { value: state.symbol, onchange: (e) => state.symbol = e.target.value.toUpperCase() })),
        field("Timeframe", selectEl(["5m", "15m", "1h", "4h", "1d"], state.timeframe, { onchange: (e) => state.timeframe = e.target.value })),
        field("Candles", numInput(state.candle_limit, "100", { onchange: (e) => state.candle_limit = parseInt(e.target.value) || 1500 })),
        field("Maker fee %", numInput(state.maker_fee, "0.01", { onchange: (e) => state.maker_fee = parseFloat(e.target.value) || 0 })),
        field("Taker fee %", numInput(state.taker_fee, "0.01", { onchange: (e) => state.taker_fee = parseFloat(e.target.value) || 0 })),
        field("Initial balance", numInput(state.balance, "10", { onchange: (e) => state.balance = parseFloat(e.target.value) || 1000 }))),
      el("div", { style: "height: 8px" }),
      paramsHost,
      el("div", { style: "margin-top: 14px; display:flex; gap:10px; align-items:center" },
        runBtn, el("span", { class: "muted", id: "bt-status" }, ""))));

  const paramsHost = el("div", {});
  form.append();
  drawParams();

  function drawParams() {
    paramsHost.innerHTML = "";
    const g = state.grid, d = state.dca, m = state.momentum, w = state.vwap;
    if (state.type === "grid") {
      paramsHost.append(el("div", { class: "sect-title" }, "Grid parameters"));
      paramsHost.append(el("div", { class: "form-grid" },
        field("Budget", numInput(g.total_budget_quote, "10", { onchange: (e) => g.total_budget_quote = parseFloat(e.target.value) || 1000 })),
        field("Upper (0=auto)", numInput(g.upper_price, "any", { onchange: (e) => g.upper_price = parseFloat(e.target.value) || 0 })),
        field("Lower (0=auto)", numInput(g.lower_price, "any", { onchange: (e) => g.lower_price = parseFloat(e.target.value) || 0 })),
        field("Levels", numInput(g.levels, "1", { onchange: (e) => g.levels = parseInt(e.target.value) || 20 })),
        field("Grid type", selectEl(["arithmetic", "geometric"], g.grid_type, { onchange: (e) => g.grid_type = e.target.value })),
        field("Stop loss %", numInput(g.stop_loss_pct, "0.5", { onchange: (e) => g.stop_loss_pct = parseFloat(e.target.value) || 0 })),
        field("Take profit %", numInput(g.take_profit_pct, "0.5", { onchange: (e) => g.take_profit_pct = parseFloat(e.target.value) || 0 }))));
    } else if (state.type === "dca") {
      paramsHost.append(el("div", { class: "sect-title" }, "DCA parameters"));
      paramsHost.append(el("div", { class: "form-grid" },
        field("Budget", numInput(d.total_budget_quote, "10", { onchange: (e) => d.total_budget_quote = parseFloat(e.target.value) || 1000 })),
        field("Base order %", numInput(d.base_order_pct, "1", { onchange: (e) => d.base_order_pct = parseFloat(e.target.value) || 20 })),
        field("Max safety orders", numInput(d.max_safety_orders, "1", { onchange: (e) => d.max_safety_orders = parseInt(e.target.value) || 5 })),
        field("Deviation %", numInput(d.safety_deviation_pct, "0.1", { onchange: (e) => d.safety_deviation_pct = parseFloat(e.target.value) || 2 })),
        field("Step scale ×", numInput(d.safety_step_scale, "0.05", { onchange: (e) => d.safety_step_scale = parseFloat(e.target.value) || 1.3 })),
        field("Volume scale ×", numInput(d.volume_scale, "0.05", { onchange: (e) => d.volume_scale = parseFloat(e.target.value) || 1.5 })),
        field("Take profit %", numInput(d.take_profit_pct, "0.1", { onchange: (e) => d.take_profit_pct = parseFloat(e.target.value) || 1.5 })),
        field("Stop loss %", numInput(d.stop_loss_pct, "0.5", { onchange: (e) => d.stop_loss_pct = parseFloat(e.target.value) || 0 })),
        field("Short mode", selectEl([{ value: "no", label: "No (long)" }, { value: "yes", label: "Yes (short)" }], d.short_mode ? "yes" : "no", { onchange: (e) => d.short_mode = e.target.value === "yes" })),
        field("Trend filter", selectEl([{ value: "no", label: "Off" }, { value: "yes", label: "EMA trend" }], d.trend_filter_enabled ? "yes" : "no", { onchange: (e) => d.trend_filter_enabled = e.target.value === "yes" }))));
    } else if (state.type === "momentum") {
      paramsHost.append(el("div", { class: "sect-title" }, "Momentum parameters"));
      paramsHost.append(el("div", { class: "form-grid" },
        field("Budget", numInput(m.total_budget_quote, "10", { onchange: (e) => m.total_budget_quote = parseFloat(e.target.value) || 1000 })),
        field("Position %", numInput(m.position_pct, "1", { onchange: (e) => m.position_pct = parseFloat(e.target.value) || 50 })),
        field("Fast EMA", numInput(m.fast_ema, "1", { onchange: (e) => m.fast_ema = parseInt(e.target.value) || 12 })),
        field("Slow EMA", numInput(m.slow_ema, "1", { onchange: (e) => m.slow_ema = parseInt(e.target.value) || 30 })),
        field("Stop loss %", numInput(m.stop_loss_pct, "0.1", { onchange: (e) => m.stop_loss_pct = parseFloat(e.target.value) || 3 })),
        field("Take profit %", numInput(m.take_profit_pct, "0.1", { onchange: (e) => m.take_profit_pct = parseFloat(e.target.value) || 6 })),
        field("Trailing", selectEl([{ value: "no", label: "Off" }, { value: "yes", label: "On" }], m.trailing_enabled ? "yes" : "no", { onchange: (e) => m.trailing_enabled = e.target.value === "yes" })),
        field("Volume filter", selectEl([{ value: "no", label: "Off" }, { value: "yes", label: "On" }], m.volume_filter_enabled ? "yes" : "no", { onchange: (e) => m.volume_filter_enabled = e.target.value === "yes" })),
        field("Short mode", selectEl([{ value: "no", label: "No (long)" }, { value: "yes", label: "Yes (short)" }], m.allow_short ? "yes" : "no", { onchange: (e) => m.allow_short = e.target.value === "yes" })),
        field("Cooldown (min)", numInput(m.cooldown_minutes, "1", { onchange: (e) => m.cooldown_minutes = parseInt(e.target.value) || 0 }))));
    } else {
      paramsHost.append(el("div", { class: "sect-title" }, "VWAP parameters"));
      paramsHost.append(el("div", { class: "form-grid" },
        field("Budget", numInput(w.total_budget_quote, "10", { onchange: (e) => w.total_budget_quote = parseFloat(e.target.value) || 1000 })),
        field("Position %", numInput(w.position_pct, "1", { onchange: (e) => w.position_pct = parseFloat(e.target.value) || 40 })),
        field("Mode", selectEl([
          { value: "mean_reversion", label: "Mean reversion" },
          { value: "reclaim", label: "Reclaim" }], w.mode, { onchange: (e) => w.mode = e.target.value })),
        field("VWAP window (0=anchored)", numInput(w.vwap_window, "1", { onchange: (e) => w.vwap_window = parseInt(e.target.value) || 0 })),
        field("Deviation %", numInput(w.deviation_pct, "0.05", { onchange: (e) => w.deviation_pct = parseFloat(e.target.value) || 1 })),
        field("Take profit %", numInput(w.take_profit_pct, "0.1", { onchange: (e) => w.take_profit_pct = parseFloat(e.target.value) || 2 })),
        field("Stop loss %", numInput(w.stop_loss_pct, "0.1", { onchange: (e) => w.stop_loss_pct = parseFloat(e.target.value) || 2 })),
        field("Short mode", selectEl([{ value: "no", label: "No (long)" }, { value: "yes", label: "Yes (short)" }], w.allow_short ? "yes" : "no", { onchange: (e) => w.allow_short = e.target.value === "yes" }))));
    }
  }

  async function run() {
    runBtn.disabled = true;
    document.getElementById("bt-status").textContent = "fetching candles & simulating…";
    try {
      const body = {
        type: state.type, symbol: state.symbol, exchange: state.exchange,
        timeframe: state.timeframe, candle_limit: state.candle_limit,
        maker_fee_pct: state.maker_fee, taker_fee_pct: state.taker_fee,
        initial_balance_quote: state.balance,
      };
      if (state.type === "grid") body.grid = state.grid;
      else if (state.type === "dca") body.dca = state.dca;
      else if (state.type === "momentum") body.momentum = state.momentum;
      else body.vwap = state.vwap;
      const res = await post("/backtest", body);
      drawResults(res);
      document.getElementById("bt-status").textContent = "";
    } catch (e) {
      toast(e.message, "err");
      document.getElementById("bt-status").textContent = "";
    }
    runBtn.disabled = false;
  }

  function drawResults(res) {
    results.innerHTML = "";
    const s = res.stats;
    const fmtPct = (v) => `${v > 0 ? "+" : ""}${fmtNum(v, 2)}%`;
    results.append(
      el("div", { class: "stat-row", style: "margin-bottom:16px" },
        statCard("Final Equity", `$${fmtNum(s.final_equity, 2)}`, `from $${fmtNum(s.initial_balance, 0)}`),
        statCard("Net PnL", fmtMoney(s.pnl), `${s.days} days`, pnlCls(s.pnl)),
        statCard("ROI", fmtPct(s.roi_pct), `APR ≈ ${fmtPct(s.apr_pct)}`, pnlCls(s.roi_pct)),
        statCard("Max Drawdown", `-${fmtNum(s.max_drawdown_pct, 2)}%`, "from equity peak", s.max_drawdown_pct > 20 ? "neg" : ""),
        statCard("Trades", String(s.trades), s.wins !== undefined ? `${s.wins} profitable rounds` : `${s.buy_trades || 0} buys / ${s.sell_trades || 0} sells`),
        statCard("Fees Paid", fmtNum(s.fees, 2), "maker+taker")),
      el("div", { class: "card", style: "margin-bottom:16px" },
        el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Price & Trades",
          el("span", { class: "tag" }, `${s.symbol} ${s.timeframe}`))),
        el("div", { class: "card-body" }, chartHost(res))),
      el("div", { class: "card" },
        el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Simulated Trades")),
        el("div", { class: "card-body flush scroll-x", style: "max-height:420px; overflow-y:auto" }, tradesTable(res.trades))));

    function chartHost(r) {
      const host = el("div");
      const chart = new CandleChart(host, {});
      chart.setCandles(r.candles.map((c) => ({ t: c[0], o: c[1], h: c[2], l: c[3], c: c[4], v: c[5] })));
      chart.setMarkers(r.trades.map((t) => ({
        ts: t.ts, price: t.price, side: t.side,
        title: `${t.side.toUpperCase()} · ${(t.role || "").replace("_", " ")}`,
        infoRows: [["Price", fmtNum(t.price, 2)], ["Qty", fmtNum(t.qty, 6)],
          ...(t.pnl !== undefined ? [["PnL", fmtMoney(t.pnl)]] : []), ["Time", fmtDate(t.ts)]],
      })));
      if (r.levels && r.levels.length) {
        const step = Math.max(1, Math.floor(r.levels.length / 12));
        chart.setPriceLines(r.levels.filter((_, i) => i % step === 0).map((p) => ({
          price: p, color: "rgba(91,140,255,.35)", label: "", dash: true,
        })));
      }
      return host;
    }

    function tradesTable(trades) {
      if (!trades.length) return el("div", { class: "empty" }, "No trades in this period");
      return el("table", { class: "tbl" },
        el("thead", {}, el("tr", {}, el("th", {}, "Time"), el("th", {}, "Side"), el("th", { class: "num" }, "Price"),
          el("th", { class: "num" }, "Qty"), el("th", { class: "num" }, "Fee"), el("th", { class: "num" }, "PnL"), el("th", {}, "Role"))),
        el("tbody", {}, ...[...trades].reverse().slice(0, 400).map((t) => el("tr", {},
          el("td", { class: "mono muted" }, fmtDate(t.ts)),
          el("td", {}, el("span", { class: t.side === "buy" ? "pos" : "neg", style: "font-weight:700" }, t.side.toUpperCase())),
          el("td", { class: "num" }, fmtNum(t.price, 2)),
          el("td", { class: "num" }, fmtNum(t.qty, 6)),
          el("td", { class: "num muted" }, fmtNum(t.fee, 4)),
          el("td", { class: `num ${pnlCls(t.pnl || 0)}` }, t.pnl ? fmtMoney(t.pnl) : "—"),
          el("td", { class: "muted" }, (t.role || "").replace("_", " "))))));
    }
  }
}

