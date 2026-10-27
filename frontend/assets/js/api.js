export async function api(path, opts = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort("timeout"), opts.timeoutMs || 45000);
  let res;
  try {
    res = await fetch(`/api${path}`, {
      headers: { "Content-Type": "application/json" },
      signal: ctrl.signal,
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    clearTimeout(timer);
    if (String(e).includes("timeout") || e.name === "AbortError") {
      throw new Error("request timed out — backend not responding");
    }
    throw new Error("backend unreachable — is the server running?");
  }
  clearTimeout(timer);
  let data = null;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok) throw new Error(errText(data, res.status));
  return data;
}

function errText(data, status) {
  let d = data && (data.detail !== undefined ? data.detail : data.error);
  if (Array.isArray(d)) {
    return d.map((x) => {
      const loc = (x.loc || []).filter((p) => p !== "body" && p !== "config").join(".");
      return `${loc ? loc + ": " : ""}${x.msg || ""}`;
    }).join("; ") || `HTTP ${status}`;
  }
  if (d && typeof d === "object") d = JSON.stringify(d);
  return d || `HTTP ${status}`;
}

export const get = (p) => api(p);
export const post = (p, body = {}) => api(p, { method: "POST", body });
export const put = (p, body = {}) => api(p, { method: "PUT", body });
export const del = (p) => api(p, { method: "DELETE" });

let ws = null;
let handlers = [];
let backoff = 1000;
const statusCbs = [];

export function onWsStatus(cb) { statusCbs.push(cb); }
export function onWs(fn) { handlers.push(fn); return () => { handlers = handlers.filter((f) => f !== fn); }; }

function setWsStatus(ok) {
  statusCbs.forEach((cb) => cb(ok));
}

export function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { backoff = 1000; setWsStatus(true); };
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.topic === "ping") return;
      handlers.forEach((fn) => { try { fn(msg.topic, msg.payload); } catch (e) { console.error(e); } });
    } catch { }
  };
  ws.onclose = () => {
    setWsStatus(false);
    setTimeout(connectWs, backoff);
    backoff = Math.min(backoff * 1.7, 15000);
  };
  ws.onerror = () => { try { ws.close(); } catch { } };
}

window.addEventListener("unhandledrejection", (e) => {
  console.error("Unhandled:", e.reason);
});