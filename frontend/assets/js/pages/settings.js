import { get, put, post } from "../api.js";
import { el, toast, pageHeader, field, numInput, selectEl, checkbox } from "../ui.js";

export async function render(container) {
  const s = (await get("/settings")) || {};

  const defaults = {
    default_tick_interval_s: 1,
    default_timeframe: "5m",
    default_maker_fee_pct: 0.1,
    default_taker_fee_pct: 0.1,
    default_levels: 20,
    default_budget: 1000,
    global_daily_loss_quote: 0,
    require_paper_first: true,
  };
  const cfg = { ...defaults, ...s };

  container.append(pageHeader("Settings"));
  const card = el("div", { class: "card" });
  container.append(card);

  const build = () => {
    card.innerHTML = "";
    card.append(el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Global Defaults"),
      el("div", { class: "btn-row" },
        el("button", {
          class: "btn primary", onclick: async () => {
            await put("/settings", { values: cfg });
            toast("Settings saved", "ok");
          },
        }, "Save"))));
    card.append(el("div", { class: "card-body" },
      el("div", { class: "sect-title" }, "New bot defaults"),
      el("div", { class: "form-grid" },
        field("Tick interval (s)", numInput(cfg.default_tick_interval_s, "0.1", { onchange: (e) => cfg.default_tick_interval_s = parseFloat(e.target.value) || 1 })),
        field("Default timeframe", selectEl(["1m", "5m", "15m", "1h"], cfg.default_timeframe, { onchange: (e) => cfg.default_timeframe = e.target.value })),
        field("Default maker fee %", numInput(cfg.default_maker_fee_pct, "0.01", { onchange: (e) => cfg.default_maker_fee_pct = parseFloat(e.target.value) || 0 })),
        field("Default taker fee %", numInput(cfg.default_taker_fee_pct, "0.01", { onchange: (e) => cfg.default_taker_fee_pct = parseFloat(e.target.value) || 0 })),
        field("Default levels (grid)", numInput(cfg.default_levels, "1", { onchange: (e) => cfg.default_levels = parseInt(e.target.value) || 20 })),
        field("Default budget (quote)", numInput(cfg.default_budget, "10", { onchange: (e) => cfg.default_budget = parseFloat(e.target.value) || 1000 }))),
      el("div", { class: "sect-title" }, "Global risk"),
      el("div", { class: "form-grid" },
        field("Global daily loss cap (quote, 0=off)", numInput(cfg.global_daily_loss_quote, "10", { onchange: (e) => cfg.global_daily_loss_quote = parseFloat(e.target.value) || 0 }),
          "Informational — applied per-bot in risk manager"))));
  };
  build();

  const tools = el("div", { class: "card", style: "margin-top:16px" },
    el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Maintenance")),
    el("div", { class: "card-body btn-row" },
      el("button", {
        class: "btn", onclick: async () => {
          const res = await get("/exchanges");
          openModal("Supported exchanges", [el("div", { class: "card-body" },
            el("table", { class: "tbl" },
              el("thead", {}, el("tr", {}, el("th", {}, "ID"), el("th", {}, "Name"), el("th", {}, "Kind"), el("th", {}, "Order Book"))),
              el("tbody", {}, ...res.exchanges.map((x) => el("tr", {},
                el("td", { class: "mono" }, x.id), el("td", {}, x.name), el("td", { class: "muted" }, x.kind), el("td", { class: "muted" }, x.orderbook)))),
            ))]);
        },
      }, "Supported exchanges"),
      el("button", {
        class: "btn danger", onclick: async () => {
          if (!confirm("Stop ALL running bots?")) return;
          const bots = (await get("/bots")).bots;
          for (const b of bots) {
            if (b.status === "running") await post(`/bots/${b.id}/stop`).catch(() => { });
          }
          toast("All bots stopped", "ok");
        },
      }, "Stop all bots")));
  container.append(tools);
}
