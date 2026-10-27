import { get, onWs } from "../api.js";
import { el, fmtDate, pageHeader, selectEl } from "../ui.js";

let unsub = null;

export async function render(container) {
  const filterBot = selectEl([], "", { onchange: () => load() });
  const filterLevel = selectEl(["", "info", "warning", "error"], "", { onchange: () => load() });
  const list = el("div", { style: "max-height: calc(100vh - 240px); overflow-y: auto" });

  container.append(pageHeader("Logs & Events"),
    el("div", { class: "card", style: "margin-bottom:16px" },
      el("div", { class: "card-body", style: "display:flex; gap:16px; align-items:center" },
        el("div", { style: "width:280px" }, filterBot),
        el("div", { style: "width:160px" }, filterLevel))),
    el("div", { class: "card" }, list));

  const bots = (await get("/bots")).bots;
  filterBot.append(el("option", { value: "" }, "All bots"));
  bots.forEach((b) => filterBot.append(el("option", { value: b.id }, b.config.name)));

  async function load() {
    const bid = filterBot.value ? `&bot_id=${filterBot.value}` : "";
    const lvl = filterLevel.value ? `&level=${filterLevel.value}` : "";
    const res = await get(`/logs?limit=300${bid}${lvl}`);
    list.innerHTML = "";
    if (!res.events.length) { list.append(el("div", { class: "empty" }, "No events")); return; }
    res.events.forEach((e) => list.append(row(e)));
  }

  function row(e) {
    return el("div", { class: "log-line" },
      el("span", { class: "ts" }, fmtDate(e.ts)),
      el("span", { class: "lv" }, e.bot_id ? e.bot_id : "sys"),
      el("span", { class: `lv ${e.level}` }, e.level),
      el("span", { class: "msg" }, e.message));
  }

  await load();
  unsub = onWs((topic, p) => {
    if (topic === "log") {
      if (filterBot.value && p.bot_id !== filterBot.value) return;
      list.prepend(row({ ts: p.ts, level: p.level, bot_id: p.bot_id, message: p.message }));
      while (list.children.length > 400) list.lastChild.remove();
    }
  });
  return () => { if (unsub) unsub(); unsub = null; };
}
