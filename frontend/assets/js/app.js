import { connectWs, onWsStatus } from "./api.js";
import { el } from "./ui.js";
import { render as renderDashboard } from "./pages/dashboard.js";
import { render as renderBots } from "./pages/bots.js";
import { render as renderBotDetail } from "./pages/botdetail.js";
import { render as renderBacktest } from "./pages/backtest.js";
import { render as renderAccounts } from "./pages/accounts.js";
import { render as renderLogs } from "./pages/logs.js";
import { render as renderSettings } from "./pages/settings.js";

const TITLES = {
  dashboard: "Dashboard", bots: "Bots", backtest: "Backtest",
  accounts: "Exchange Accounts", logs: "Logs & Events", settings: "Settings",
};

const routes = {
  dashboard: { title: "Dashboard", render: renderDashboard },
  bots: { title: "Bots", render: renderBots },
  backtest: { title: "Backtest", render: renderBacktest },
  accounts: { title: "Exchange Accounts", render: renderAccounts },
  logs: { title: "Logs & Events", render: renderLogs },
  settings: { title: "Settings", render: renderSettings },
};

let cleanupFns = [];

function parseHash() {
  const h = location.hash.replace(/^#\/?/, "") || "dashboard";
  const parts = h.split("/");
  return { page: parts[0] || "dashboard", arg: parts[1] || null };
}

function cleanup() {
  cleanupFns.forEach((f) => { try { f(); } catch { } });
  cleanupFns = [];
}

async function navigate() {
  const { page, arg } = parseHash();
  const pageEl = document.getElementById("page");
  cleanup();
  document.querySelectorAll("#nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.route === page);
  });
  document.getElementById("page-title").textContent =
    page === "bot" ? "Bot Details" : (TITLES[page] || "Dashboard");
  pageEl.innerHTML = "";
  try {
    if (page === "bot" && arg) {
      cleanupFns.push(await renderBotDetail(pageEl, arg));
    } else if (routes[page]) {
      cleanupFns.push((await routes[page].render(pageEl)) || (() => { }));
    } else {
      location.hash = "#/dashboard";
    }
  } catch (e) {
    pageEl.append(el("div", { class: "card" },
      el("div", { class: "card-body" },
        el("div", { class: "empty" },
          el("div", { class: "big" }, "⚠"),
          `Failed to load page: ${e.message}`))));
    console.error(e);
  }
}

window.addEventListener("hashchange", navigate);

function startClock() {
  const c = document.getElementById("clock");
  setInterval(() => {
    c.textContent = new Date().toLocaleTimeString("en-GB") + " UTC" +
      (new Date().getTimezoneOffset() > 0 ? "-" : "+") +
      Math.abs(new Date().getTimezoneOffset() / 60);
  }, 1000);
}

function initWsStatus() {
  const dot = document.getElementById("ws-dot");
  const label = document.getElementById("ws-label");
  onWsStatus((ok) => {
    dot.className = `dot ${ok ? "on" : "off"}`;
    label.textContent = ok ? "live feed" : "disconnected";
  });
}

initWsStatus();
connectWs();
startClock();
navigate();
