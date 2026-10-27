export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(9)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(c));
  }
  return node;
}

export function fmtNum(v, d = 2) {
  if (v === undefined || v === null || isNaN(v)) return "—";
  const n = Number(v);
  const abs = Math.abs(n);
  let digits = d;
  if (abs > 0 && abs < 0.01) digits = Math.max(d, 6);
  else if (abs < 1) digits = Math.max(d, 4);
  return n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtPrice(v) { return fmtNum(v, 2); }
export function fmtQty(v) { return fmtNum(v, 6); }
export function fmtMoney(v) {
  if (v === undefined || v === null || isNaN(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${fmtNum(v, 2)}`;
}
export function pnlCls(v) { return v > 0 ? "pos" : v < 0 ? "neg" : "muted"; }

export function fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
export function fmtDate(ts) {
  if (!ts) return "—";
  return new Date(ts).toLocaleString("en-GB", { day: "2-digit", month: "short", year: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function toast(msg, type = "") {
  const t = el("div", { class: `toast ${type}` }, msg);
  document.getElementById("toasts").append(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(() => t.remove(), 320); }, 3600);
}

export function openModal(title, bodyNodes, opts = {}) {
  const root = document.getElementById("modal-root");
  const foot = el("div", { class: "modal-foot" }, ...(opts.foot || []));
  const modal = el("div", { class: `modal ${opts.wide ? "wide" : ""}` },
    el("div", { class: "modal-head" }, el("h3", {}, title),
      el("button", { class: "modal-x", onclick: close }, "×")),
    el("div", { class: "modal-body" }, ...bodyNodes), foot);
  const overlay = el("div", { class: "overlay", onclick: (e) => { if (e.target === overlay) close(); } }, modal);
  overlay.close = close;
  function close() { overlay.remove(); if (opts.onClose) opts.onClose(); }
  root.append(overlay);
  return { close, root: modal, foot, buttons: [...foot.querySelectorAll("button")] };
}

export function tabs(container, defs, initial = 0) {
  const bar = el("div", { class: "tabs" });
  const panes = [];
  let active = initial;
  defs.forEach((d, i) => {
    const btn = el("button", { class: `tab-btn ${i === active ? "active" : ""}`, onclick: () => select(i) }, d.label);
    const pane = el("div", { class: `tab-pane ${i === active ? "active" : ""}` });
    pane.append(d.render());
    bar.append(btn);
    panes.push({ btn, pane });
  });
  function select(i) {
    active = i;
    panes.forEach((p, j) => {
      p.btn.classList.toggle("active", i === j);
      p.pane.classList.toggle("active", i === j);
    });
    rerender();
  }
  function rerender() {
    const p = panes[active];
    if (!p) return;
    p.pane.innerHTML = "";
    p.pane.append(defs[active].render());
  }
  container.append(bar, ...panes.map((p) => p.pane));
  return { select, rerender };
}

export function field(label, input, hint) {
  const f = el("div", { class: "field" }, el("label", {}, label));
  f.append(input);
  if (hint) f.append(el("div", { class: "hint" }, hint));
  return f;
}

export function input(attrs = {}) {
  return el("input", { ...attrs });
}

export function numInput(value, step = "any", attrs = {}) {
  const i = el("input", { type: "number", step, value: value ?? "", ...attrs });
  return i;
}

export function selectEl(options, value, attrs = {}) {
  const s = el("select", attrs);
  for (const o of options) {
    const label = typeof o === "string" ? o : o.label;
    const val = typeof o === "string" ? o : o.value;
    s.append(el("option", { value: val, ...(val === value ? { selected: true } : {}) }, label));
  }
  return s;
}

export function checkbox(label, checked, attrs = {}) {
  const wrap = el("label", { class: "check" });
  const cb = el("input", { type: "checkbox", ...(checked ? { checked: true } : {}), ...attrs });
  wrap.append(cb, el("span", {}, label));
  wrap.input = cb;
  return wrap;
}

export function statusPill(status) {
  return el("span", { class: `pill ${status}` },
    el("span", { class: "dot" }), status);
}

export function pageHeader(title, ...right) {
  return el("div", { class: "detail-top", style: "margin-bottom:18px" },
    el("div", { class: "detail-title" }, el("h2", {}, title)),
    right.length ? el("div", { class: "btn-row" }, ...right) : null);
}

export function statCard(label, value, sub = "", cls = "") {
  return el("div", { class: "stat-card" },
    el("div", { class: "stat-label" }, label),
    el("div", { class: `stat-value ${cls}` }, value),
    sub ? el("div", { class: "stat-sub" }, sub) : null);
}
