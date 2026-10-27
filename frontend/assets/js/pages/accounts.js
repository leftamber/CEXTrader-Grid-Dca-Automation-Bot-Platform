import { get, post, del } from "../api.js";
import { el, fmtNum, toast, openModal, pageHeader, statCard } from "../ui.js";
import { openAccountForm, testAccount } from "./accountform.js";

export async function render(container) {
  const list = el("div", { class: "bot-list" });
  const stats = el("div", { class: "stat-row", style: "margin-bottom:16px" });
  container.append(pageHeader("Exchange Accounts",
    el("button", { class: "btn primary", onclick: () => openAccountForm(null, refresh) }, "+ Add Account"),
    el("button", { class: "btn", onclick: refresh }, "Refresh")));
  container.append(stats, list);
  await refresh();

  async function refresh() {
    const data = await get("/accounts");
    const dash = await get("/dashboard").catch(() => ({ accounts: [] }));
    const eq = data.accounts.reduce((s, a) => {
      const m = dash.accounts.find((x) => x.id === a.id);
      return s + (m?.equity || 0);
    }, 0);
    stats.innerHTML = "";
    stats.append(
      statCard("Accounts", String(data.accounts.length), `${data.accounts.filter((a) => a.paper).length} paper · ${data.accounts.filter((a) => !a.paper).length} live`),
      statCard("Tracked Equity", `$${fmtNum(eq, 2)}`, "across all accounts"),
      statCard("Live Accounts", String(data.accounts.filter((a) => !a.paper).length), "trading real funds"));
    list.innerHTML = "";
    if (!data.accounts.length) {
      list.append(el("div", { class: "card" }, el("div", { class: "card-body" },
        el("div", { class: "empty" }, el("div", { class: "big" }, "no accounts"),
          "No accounts yet", el("br"),
          el("button", { class: "btn primary", style: "margin-top:14px", onclick: () => openAccountForm(null, refresh) }, "Add your first account")))));
      return;
    }
    const cards = [];
    for (const a of data.accounts) {
      const card = accountCard(a);
      list.append(card);
      cards.push([a, card]);
    }
    for (const [a] of cards) loadBalances(a);
  }

  function accountCard(a) {
    const card = el("div", { class: "card" });
    card.append(
      el("div", { class: "card-head" },
        el("div", { class: "card-title" },
          el("span", { class: `pill ${a.paper ? "paper" : "live"}` }, a.paper ? "PAPER" : "LIVE"),
          a.name, el("span", { class: "tag" }, a.exchange),
          a.paper ? el("span", { class: "muted", style: "font-size:11px" }, `via ${a.underlying || "bybit"}`) : null),
        el("div", { class: "btn-row" },
          el("button", { class: "btn sm", onclick: async () => { toast("Testing…"); await testAccount(a.id); } }, "Test"),
          el("button", { class: "btn sm", onclick: () => resetBalances(a) }, "Balances"),
          el("button", { class: "btn sm", onclick: () => openAccountForm(a, refresh) }, "Edit"),
          el("button", { class: "btn sm ghost", onclick: () => removeAcc(a) }, "Delete"))),
      el("div", { class: "card-body", id: `bal-${a.id}` },
        el("div", { class: "muted", style: "font-size:12px" }, "Loading balances…")));
    return card;
  }

async function loadBalances(a, retried = false) {
    const host = document.getElementById(`bal-${a.id}`);
    if (!host) return;
    try {
      const res = await get(`/accounts/${a.id}/balances`);
      host.innerHTML = "";
      const entries = Object.entries(res.balances || {}).filter(([, v]) => v);
      if (res.paper && res.feed) {
        const feedCls = res.feed === "live" ? "pos" : res.feed === "synthetic" ? "neg" : "";
        host.append(el("div", { class: "kv", style: "margin-bottom:6px" },
          el("span", { class: "k" }, "Feed"),
          el("span", { class: `v ${feedCls}` },
            res.feed === "live" ? `live via ${a.underlying || "bybit"}` :
            res.feed === "synthetic" ? "SIMULATED PRICES (source unreachable)" :
            res.feed === "cached" ? "last known prices (source unreachable)" : res.feed)));
      }
      if (!entries.length) { host.append(el("div", { class: "muted", style: "font-size:12px" }, "No balances")); return; }
      host.append(el("div", { style: "display:flex; flex-wrap:wrap; gap:10px" },
        ...entries.map(([c, v]) => el("div", { class: "chip", style: "font-size:12px" }, `${c}: ${fmtNum(v, v > 100 ? 2 : 6)}`))));
      if (res.equity) host.append(el("div", { class: "kv", style: "margin-top:8px" },
        el("span", { class: "k" }, "Equity"), el("span", { class: "v" }, `$${fmtNum(res.equity, 2)}`)));
      if (res.cached) host.append(el("div", { class: "muted", style: "font-size:11px; margin-top:4px" },
        "cached — will refresh when the bot or sampler updates"));
    } catch (e) {
      if (!retried) {
        setTimeout(() => loadBalances(a, true), 3000);
        return;
      }
      host.innerHTML = "";
      host.append(el("div", { class: "muted", style: "font-size:12px" }, e.message));
    }
  }

  function resetBalances(a) {
    const fields = {};
    const mk = (cur) => el("input", {
      type: "number", step: "any", placeholder: "keep current",
      oninput: (e) => { const v = parseFloat(e.target.value); if (!isNaN(v)) fields[cur] = v; },
    });
    const modal = openModal(`Paper balances — ${a.name}`, [
      el("div", { class: "hint", style: "margin-bottom:12px" }, "Leave empty to keep the current value."),
      el("div", { class: "form-grid" },
        field("USDT", mk("USDT")),
        field("BTC", mk("BTC")),
        field("ETH", mk("ETH")),
        field("SOL", mk("SOL"))),
    ], {
      foot: [
        el("button", { class: "btn", onclick: () => modal.close() }, "Cancel"),
        el("button", {
          class: "btn primary", onclick: async () => {
            await post(`/accounts/${a.id}/reset`, { config: { initial_balances: fields } });
            toast("Balances updated", "ok");
            modal.close();
            await refresh();
          },
        }, "Apply"),
      ],
    });
  }

  async function removeAcc(a) {
    if (!confirm(`Delete account "${a.name}"?`)) return;
    try {
      await del(`/accounts/${a.id}`);
      toast("Account deleted", "ok");
      await refresh();
    } catch (e) { toast(e.message, "err"); }
  }
}

