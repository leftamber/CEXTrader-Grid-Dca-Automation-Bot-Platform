import { post, put, get } from "../api.js";
import { el, toast, openModal, field, input, selectEl, numInput } from "../ui.js";

export const EXCHANGES = [
  { value: "paper", label: "Paper Trading (simulated)" },
  { value: "binance", label: "Binance" },
  { value: "okx", label: "OKX" },
  { value: "bybit", label: "Bybit" },
  { value: "bitget", label: "Bitget" },
  { value: "coinbase", label: "Coinbase" },
  { value: "kraken", label: "Kraken" },
  { value: "gate", label: "Gate.io" },
  { value: "kucoin", label: "KuCoin" },
  { value: "mexc", label: "MEXC" },
  { value: "htx", label: "HTX (Huobi)" },
  { value: "hyperliquid", label: "Hyperliquid" },
  { value: "dydx", label: "dYdX v4" },
];

export async function openAccountForm(a, onSaved) {
  const isNew = !a;
  let cfg = {
    name: a?.name || "", exchange: a?.exchange || "paper", underlying: a?.underlying || "bybit",
    paper: a?.paper !== false, api_key: a?.api_key || "", secret: a?.secret || "",
    password: a?.password || "", wallet_address: a?.wallet_address || "", mnemonic: a?.mnemonic || "",
    testnet: a?.testnet !== false, maker_fee_pct: a?.maker_fee_pct ?? 0.1,
    taker_fee_pct: a?.taker_fee_pct ?? 0.1, slippage_bps: a?.slippage_bps ?? 2,
    initial_balances: a?.initial_balances || { USDT: 10000 }, leverage: a?.leverage || 1,
  };

  const name = input({ value: cfg.name, placeholder: "My Account", onchange: (e) => cfg.name = e.target.value });
  const exSel = selectEl(EXCHANGES, cfg.exchange, { onchange: (e) => { cfg.exchange = e.target.value; drawFields(); } });
  const undSel = selectEl(["bybit", "binance", "okx", "kraken", "gate", "kucoin", "mexc"], cfg.underlying, { onchange: (e) => cfg.underlying = e.target.value });
  const paperSel = selectEl([{ value: "yes", label: "Paper — simulated trades (recommended first)" }, { value: "no", label: "Live — real money" }], cfg.paper ? "yes" : "no",
    { onchange: (e) => { cfg.paper = e.target.value === "yes"; drawFields(); } });
  const apiKey = input({ value: cfg.api_key, placeholder: "API key", onchange: (e) => cfg.api_key = e.target.value });
  const secret = input({ type: "password", value: cfg.secret, placeholder: "API secret", onchange: (e) => cfg.secret = e.target.value });
  const password = input({ type: "password", value: cfg.password, placeholder: "Passphrase (OKX)", onchange: (e) => cfg.password = e.target.value });
  const wallet = input({ value: cfg.wallet_address, placeholder: "Wallet address", onchange: (e) => cfg.wallet_address = e.target.value });
  const mnemonic = input({ type: "password", value: cfg.mnemonic, placeholder: "Mnemonic", onchange: (e) => cfg.mnemonic = e.target.value });
  const testnet = selectEl([{ value: "yes", label: "Testnet" }, { value: "no", label: "Mainnet" }], cfg.testnet ? "yes" : "no", { onchange: (e) => cfg.testnet = e.target.value === "yes" });
  const maker = numInput(cfg.maker_fee_pct, "0.01", { onchange: (e) => cfg.maker_fee_pct = parseFloat(e.target.value) || 0 });
  const taker = numInput(cfg.taker_fee_pct, "0.01", { onchange: (e) => cfg.taker_fee_pct = parseFloat(e.target.value) || 0 });
  const slippage = numInput(cfg.slippage_bps, "0.5", { onchange: (e) => cfg.slippage_bps = parseFloat(e.target.value) || 0 });
  const usdtBal = numInput((cfg.initial_balances || {}).USDT ?? 10000, "1", { onchange: (e) => { const v = parseFloat(e.target.value); if (v) cfg.initial_balances.USDT = v; } });
  const btcBal = numInput((cfg.initial_balances || {}).BTC ?? 0, "0.0001", { onchange: (e) => { const v = parseFloat(e.target.value); cfg.initial_balances.BTC = v || 0; } });

  const fieldsHost = el("div", {});
  const feedStatus = el("div", { class: "hint", style: "margin-top:2px" }, "");
  let feedTimer = null;
  async function checkFeed(und) {
    feedStatus.textContent = "checking market data source…";
    feedStatus.style.color = "";
    try {
      const res = await get(`/market/ticker?exchange=${und}&symbol=BTC/USDT`);
      if (res.last || res.bid) {
        feedStatus.textContent = `source OK — BTC/USDT live price ${res.last || res.bid}`;
        feedStatus.style.color = "var(--green)";
      } else {
        feedStatus.textContent = "source responded but no price — try another exchange";
        feedStatus.style.color = "var(--red)";
      }
    } catch (e) {
      feedStatus.textContent = `${und} unreachable from this machine — pick bybit, okx or another source`;
      feedStatus.style.color = "var(--red)";
    }
  }
  function scheduleFeedCheck(und) {
    clearTimeout(feedTimer);
    feedTimer = setTimeout(() => checkFeed(und), 400);
  }
  function drawFields() {
    clearTimeout(feedTimer);
    fieldsHost.innerHTML = "";
    fieldsHost.append(el("div", { class: "sect-title" }, "General"));
    fieldsHost.append(el("div", { class: "form-grid" },
      field("Account name", name),
      field("Type", paperSel),
      field("Exchange", exSel),
      cfg.paper ? field("Market data source", undSel, "Paper engine trades against live prices from this exchange") : null));
    if (cfg.paper) {
      const undWrap = fieldsHost.lastChild;
      undWrap.append(feedStatus);
      scheduleFeedCheck(cfg.underlying);
      undSel.addEventListener("change", () => scheduleFeedCheck(cfg.underlying));
      fieldsHost.append(el("div", { class: "sect-title" }, "Paper engine"));
      fieldsHost.append(el("div", { class: "form-grid" },
        field("Maker fee %", maker),
        field("Taker fee %", taker),
        field("Market slippage (bps)", slippage, "Applied to market orders"),
        field("Initial USDT balance", usdtBal),
        field("Initial BTC balance", btcBal)));
    } else {
      fieldsHost.append(el("div", { class: "sect-title" }, `API credentials — ${labelOf(cfg.exchange)}`));
      fieldsHost.append(el("div", { class: "form-grid" },
        field("API key", apiKey),
        field("Secret", secret),
        cfg.exchange === "okx" ? field("Passphrase", password) : null,
        cfg.exchange === "hyperliquid" ? field("Wallet address", wallet, "API Wallet key above + main wallet address") : null,
        cfg.exchange === "dydx" ? field("Wallet address", wallet) : null,
        cfg.exchange === "dydx" ? field("Mnemonic", mnemonic, "dYdX v4 mnemonic") : null,
        field("Network", testnet)));
      fieldsHost.append(el("div", { class: "hint", style: "margin-top:8px" },
        "Keys are stored locally in data/trader.db on your machine. Never share them. Withdrawal permission must be disabled on the exchange."));
    }
  }
  drawFields();

  const modal = openModal(isNew ? "Add Exchange Account" : `Edit — ${a.name}`, [fieldsHost], {
    foot: [
      el("button", { class: "btn", onclick: () => modal.close() }, "Cancel"),
      ...(isNew ? [el("button", {
        class: "btn", onclick: async () => {
          try {
            await post("/accounts", { config: cfg });
            toast("Account saved (connection not tested)", "ok");
            modal.close();
            if (onSaved) onSaved();
          } catch (e) { toast(e.message, "err"); }
        },
      }, "Save only")] : []),
      el("button", {
        class: "btn primary", onclick: async () => {
          try {
            if (isNew) {
              const res = await post("/accounts", { config: cfg });
              toast("Testing connection…");
              const t = await post(`/accounts/${res.id}/test`);
              if (t.ok) {
                const feed = t.feed && t.feed !== "live" ? ` · feed: ${t.feed}` : "";
                toast(`Saved & connected (${t.markets} markets${feed})`, "ok");
              } else {
                toast(`Saved — connection test failed: ${t.error}`, "err");
              }
            } else {
              await put(`/accounts/${a.id}`, { config: cfg });
              toast("Account saved", "ok");
            }
            modal.close();
            if (onSaved) onSaved();
          } catch (e) { toast(e.message, "err"); }
        },
      }, isNew ? "Create & Test" : "Save"),
    ],
  });
  return modal;
}

function labelOf(id) {
  const f = EXCHANGES.find((x) => x.value === id);
  return f ? f.label : id;
}

export async function testAccount(id) {
  const res = await post(`/accounts/${id}/test`);
  if (res.ok) {
    const feed = res.feed && res.feed !== "live" ? ` · feed: ${res.feed}` : "";
    toast(`Connected: ${res.markets} markets${feed}`, "ok");
  } else toast(`Failed: ${res.error}`, "err");
  return res;
}
