import { get, onWs } from "../api.js";
import { el, fmtNum, fmtMoney, pnlCls, fmtTime, statusPill, pageHeader, statCard } from "../ui.js";
import { openAccountForm } from "./accountform.js";

let unsub = null;

export async function render(container) {
  const data = await get("/dashboard");

  const totalEquity = data.accounts.reduce((a, x) => a + (x.equity || 0), 0);

  const stats = el("div", { class: "stat-row", style: "margin-bottom:16px" },
    statCard("Account Equity", `$${fmtNum(totalEquity, 2)}`, `${data.accounts.length} accounts · paper & live`),
    statCard("Total Realized PnL", fmtMoney(data.total_realized_pnl), "all bots, all time", pnlCls(data.total_realized_pnl)),
    statCard("Active Bots", `${data.active_bots} / ${data.bots.length}`, "running / total"),
    statCard("Open Orders", `${data.bots.reduce((a, b) => a + ((b.open_orders || []).length), 0)}`, "across all bots"));

  container.append(pageHeader("Dashboard",
    el("button", { class: "btn primary", onclick: () => { location.hash = "#/bots"; } }, "+ New Bot"),
    el("button", { class: "btn", onclick: () => { location.hash = "#/accounts"; } }, "Accounts")),
    stats, configCard(data), equityCard(data), bottom(data));

  function configCard(d) {
    const chips = (d.accounts || []).map((a) => el("span", {
      class: "chip", style: "cursor:pointer",
      onclick: () => { location.hash = "#/accounts"; },
    }, `${a.name} · ${a.exchange}${a.paper ? "" : " LIVE"}`));
    return el("div", { class: "card", style: "margin-bottom:16px" },
      el("div", { class: "card-head" },
        el("div", { class: "card-title" }, "Exchange Config — API Keys & Balances",
          el("span", { class: "tag" }, "LOCAL ONLY")),
        el("div", { class: "btn-row" },
          el("button", { class: "btn sm primary", onclick: () => openAccountForm(null, () => location.reload()) }, "+ Add exchange / API key"),
          el("button", { class: "btn sm", onclick: () => { location.hash = "#/accounts"; } }, "Manage all"))),
      el("div", { class: "card-body", style: "display:flex; flex-wrap:wrap; gap:8px; align-items:center" },
        chips.length ? chips : el("span", { class: "muted", style: "font-size:12.5px" },
          "No accounts configured yet — add a paper account to start safely, or connect an exchange with API keys."),
        el("span", { class: "muted", style: "font-size:11.5px; margin-left:auto" },
          "keys stored locally in data/trader.db")));
  }

  function equityCard(d) {
    return el("div", { class: "card", style: "margin-bottom:16px" },
      el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Account Equity", el("span", { class: "tag" }, "SAMPLED 1M"))),
      el("div", { class: "card-body" }, equityChartHost(d)));
  }

  function equityChartHost(d) {
    const host = el("div", { class: "chart-wrap small" });
    if (d.equity_history.length > 1) {
      import("../chart.js").then(({ LineChart }) => {
        const chart = new LineChart(host, { color: "#ff6a1a" });
        chart.setData(d.equity_history.map((x) => ({ t: x.ts, v: x.value })));
      });
    } else {
      host.append(el("div", { class: "empty" }, el("div", { class: "big" }, "equity curve"),
        "Samples appear here while bots are running"));
    }
    return host;
  }

  function bottom(d) {
    const wrap = el("div", { class: "grid", style: "grid-template-columns: 1.6fr 1fr" },
      botsTable(d.bots), eventsFeed(d.recent_events));
    return wrap;
  }

  function botsTable(bots) {
    const rows = bots.map((b) => {
      const st = b.strategy_state || {};
      const pnl = st.realized_pnl || 0;
      return el("tr", { style: "cursor:pointer", onclick: () => { location.hash = `#/bot/${b.id}`; } },
        el("td", {}, el("div", { class: "bot-name" }, b.config.name,
          el("span", { class: `type-badge ${b.config.type}` }, b.config.type.toUpperCase())),
          el("div", { class: "bot-sub" }, `${b.config.symbol} · ${b.paper ? "PAPER" : "LIVE"}`)),
        el("td", {}, statusPill(b.status)),
        el("td", { class: "num" }, fmtNum(b.price || 0, 2)),
        el("td", { class: "num " + pnlCls(pnl) }, fmtMoney(pnl)),
        el("td", { class: "num muted" }, String((b.open_orders || []).length)),
        el("td", { class: "num muted" }, st.roundtrips !== undefined ? st.roundtrips : (st.cycles_done ?? "—")));
    });
    return el("div", { class: "card" },
      el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Bots")),
      el("div", { class: "card-body flush scroll-x" },
        bots.length ? el("table", { class: "tbl" },
          el("thead", {}, el("tr", {}, el("th", {}, "Bot"), el("th", {}, "Status"), el("th", { class: "num" }, "Price"),
            el("th", { class: "num" }, "PnL"), el("th", { class: "num" }, "Orders"), el("th", { class: "num" }, "Cycles"))),
          el("tbody", {}, ...rows))
          : el("div", { class: "empty" }, el("div", { class: "big" }, "no bots"), "Create a bot in the Bots section")));
  }

  function eventsFeed(events) {
    const list = el("div", { class: "card-body", style: "padding: 6px 0; max-height: 420px; overflow-y: auto" },
      events.length ? events.map((e) => logLine(e)) : el("div", { class: "empty" }, "No events yet"));
    return el("div", { class: "card" },
      el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Recent Events")),
      list);
  }

  function logLine(e) {
    return el("div", { class: "log-line" },
      el("span", { class: "ts" }, fmtTime(e.ts)),
      el("span", { class: `lv ${e.level}` }, e.level),
      el("span", { class: "msg" }, e.message));
  }

  unsub = onWs((topic, p) => {
    if (topic === "log") {
      const feed = document.querySelectorAll(".grid .card-body")[1];
      if (feed) {
        feed.prepend(logLine(p));
        while (feed.children.length > 60) feed.lastChild.remove();
      }
    }
    if (topic === "equity") {
      const sv = stats.children[0]?.querySelector(".stat-value");
      if (sv) sv.textContent = `$${fmtNum(p.equity, 2)}`;
    }
  });
  return () => { if (unsub) unsub(); unsub = null; };
}


