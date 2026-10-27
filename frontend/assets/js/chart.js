const COLORS = {
  up: "#2fbf8f", down: "#ff4d5e", upFill: "rgba(47,191,143,.85)", downFill: "rgba(255,77,94,.85)",
  grid: "rgba(255,255,255,.045)", text: "#63636b", cross: "rgba(255,255,255,.28)",
  volUp: "rgba(47,191,143,.22)", volDown: "rgba(255,77,94,.22)",
};

function niceStep(range, target) {
  const raw = range / Math.max(target, 1);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = norm > 5 ? 10 : norm > 2 ? 5 : norm > 1 ? 2 : 1;
  return step * mag;
}

function fmtAxis(v, step) {
  if (v === undefined || v === null || isNaN(v)) return "";
  const d = step >= 1 ? 0 : step >= 0.1 ? 1 : step >= 0.01 ? 2 : step >= 0.001 ? 3 : 5;
  return v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

function fmtTimeLabel(ts, spanMs) {
  const d = new Date(ts);
  if (spanMs > 86400000 * 3) return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
  if (spanMs > 86400000) return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) + " " +
    d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

export class CandleChart {
  constructor(container, opts = {}) {
    this.container = container;
    this.opts = opts;
    this.wrap = document.createElement("div");
    this.wrap.className = "chart-wrap" + (opts.small ? " small" : "");
    this.canvas = document.createElement("canvas");
    this.legend = document.createElement("div");
    this.legend.className = "chart-legend";
    this.tip = document.createElement("div");
    this.tip.className = "chart-tip";
    this.wrap.append(this.canvas, this.legend, this.tip);
    container.append(this.wrap);

    this.candles = [];
    this.priceLines = [];
    this.markers = [];
    this.view = { start: 0, end: 1 };
    this.hover = null;
    this.lastPrice = null;
    this.onClickMarker = opts.onClickMarker || null;
    this.padR = 66; this.padB = 24; this.padT = 8; this.padL = 6;

    this.canvas.addEventListener("mousemove", (e) => this._onMove(e));
    this.canvas.addEventListener("mouseleave", () => { this.hover = null; this.tip.style.display = "none"; this._renderLegend(null); this.draw(); });
    this.canvas.addEventListener("wheel", (e) => this._onWheel(e), { passive: false });
    this.canvas.addEventListener("mousedown", (e) => this._onDown(e));
    window.addEventListener("mouseup", this._upHandler = () => { this.drag = null; });
    this.canvas.addEventListener("click", (e) => this._onClick(e));
    this._ro = new ResizeObserver(() => this.draw());
    this._ro.observe(this.wrap);
  }

  destroy() {
    this._ro.disconnect();
    window.removeEventListener("mouseup", this._upHandler);
  }

  setCandles(candles) {
    this.candles = candles || [];
    this.view.start = Math.max(0, this.candles.length - Math.min(120, this.candles.length));
    this.view.end = this.candles.length;
    this.draw();
  }

  setPriceLines(lines) { this.priceLines = lines || []; this.draw(); }
  setMarkers(markers) { this.markers = markers || []; this.draw(); }
  setLastPrice(p) { this.lastPrice = p; this.draw(); }

  x2idx(x, rect) {
    const w = rect.width - this.padR - this.padL;
    const n = this.view.end - this.view.start;
    return this.view.start + (x - this.padL) / w * n;
  }
  idx2x(i, rect) {
    const w = rect.width - this.padR - this.padL;
    return this.padL + (i - this.view.start + 0.5) / (this.view.end - this.view.start) * w;
  }
  p2y(p, rect, yMin, yMax) {
    const h = rect.height - this.padB - this.padT;
    return this.padT + (yMax - p) / (yMax - yMin) * h;
  }
  y2p(y, rect, yMin, yMax) {
    const h = rect.height - this.padB - this.padT;
    return yMax - (y - this.padT) / h * (yMax - yMin);
  }

  _yRange() {
    const s = Math.max(0, Math.floor(this.view.start));
    const e = Math.min(this.candles.length, Math.ceil(this.view.end));
    let lo = Infinity, hi = -Infinity;
    for (let i = s; i < e; i++) {
      const c = this.candles[i];
      if (!c) continue;
      if (c.l < lo) lo = c.l;
      if (c.h > hi) hi = c.h;
    }
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    const pad = (hi - lo) * 0.07 || hi * 0.01 || 1;
    lo -= pad; hi += pad;
    return [lo, hi];
  }

  _onWheel(e) {
    e.preventDefault();
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const anchor = this.x2idx(x, rect);
    const factor = e.deltaY > 0 ? 1.15 : 0.87;
    const span = Math.max(3, (this.view.end - this.view.start) * factor);
    const ratio = (anchor - this.view.start) / (this.view.end - this.view.start);
    let start = anchor - ratio * span;
    let end = start + span;
    const max = this.candles.length;
    if (end > max) { end = max; start = end - span; }
    if (start < 0) { start = 0; end = Math.min(span, max); }
    this.view = { start, end };
    this.draw();
  }

  _onDown(e) {
    this.drag = { x: e.clientX, start: this.view.start, end: this.view.end };
  }
  _onMove(e) {
    const rect = this.canvas.getBoundingClientRect();
    if (this.drag) {
      const dx = e.clientX - this.drag.x;
      const w = rect.width - this.padR - this.padL;
      const shift = -dx / w * (this.drag.end - this.drag.start);
      let s = this.drag.start + shift, en = this.drag.end + shift;
      if (s < 0) { s = 0; en = this.drag.end - this.drag.start; }
      if (en > this.candles.length) { en = this.candles.length; s = en - (this.drag.end - this.drag.start); }
      this.view = { start: s, end: en };
      this.hover = null;
      this.draw();
      return;
    }
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    if (x > rect.width - this.padR || y > rect.height - this.padB) { this.hover = null; this.tip.style.display = "none"; this.draw(); return; }
    const idx = Math.round(this.x2idx(x, rect));
    const candle = this.candles[Math.max(0, Math.min(this.candles.length - 1, idx))];
    const [yMin, yMax] = this._yRange();
    const price = this.y2p(y, rect, yMin, yMax);
    this.hover = { x, y, idx, candle, price };
    this._renderLegend(candle);
    this._hoverMarker(x, y, rect);
    this.draw();
  }

  _renderLegend(c) {
    this.legend.innerHTML = "";
    if (!c) { this.legend.style.display = "none"; return; }
    this.legend.style.display = "flex";
    const items = [
      ["O", c.o, ""], ["H", c.h, ""], ["L", c.l, ""], ["C", c.c, c.c >= c.o ? COLORS.up : COLORS.down],
    ];
    for (const [k, v, col] of items) {
      const s = document.createElement("span");
      s.innerHTML = `${k} <b style="color:${col || "var(--text)"}">${fmtAxis(v, 0.01)}</b>`;
      this.legend.append(s);
    }
  }

  _markerAt(x, y, rect) {
    let best = null, bd = 16;
    for (const m of this.markers) {
      const i = this._tsIndex(m.ts);
      if (i === null) continue;
      if (i < this.view.start - 1 || i > this.view.end + 1) continue;
      const [yMin, yMax] = this._yRange();
      const mx = this.idx2x(i, rect);
      const my = this.p2y(m.price, rect, yMin, yMax);
      const d = Math.hypot(mx - x, my - y);
      if (d < bd) { bd = d; best = m; }
    }
    return best;
  }

  _tsIndex(ts) {
    if (!this.candles.length) return null;
    if (ts < this.candles[0].t - 3600000 || ts > this.candles[this.candles.length - 1].t + 3600000) return null;
    let lo = 0, hi = this.candles.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (this.candles[mid].t < ts) lo = mid + 1; else hi = mid;
    }
    return lo;
  }

  _hoverMarker(x, y, rect) {
    const m = this._markerAt(x, y, rect);
    if (!m) { this.tip.style.display = "none"; return; }
    const rows = m.infoRows || [];
    this.tip.innerHTML = "";
    const title = document.createElement("div");
    title.style.cssText = "font-weight:700;margin-bottom:5px";
    title.style.color = m.side === "buy" ? COLORS.up : COLORS.down;
    title.textContent = m.title || (m.side === "buy" ? "BUY" : "SELL");
    this.tip.append(title);
    for (const [k, v] of rows) {
      const r = document.createElement("div");
      r.className = "row";
      r.innerHTML = `<span class="k">${k}</span><span class="v">${v}</span>`;
      this.tip.append(r);
    }
    const [yMin, yMax] = this._yRange();
    const i = this._tsIndex(m.ts);
    const mx = this.idx2x(i, rect);
    const my = this.p2y(m.price, rect, yMin, yMax);
    this.tip.style.display = "block";
    let tx = mx + 16, ty = my - 14;
    if (tx + 220 > rect.width) tx = mx - 230;
    if (ty + 150 > rect.height) ty = rect.height - 150;
    if (ty < 4) ty = 4;
    this.tip.style.left = tx + "px";
    this.tip.style.top = ty + "px";
  }

  _onClick(e) {
    if (!this.onClickMarker) return;
    const rect = this.canvas.getBoundingClientRect();
    const m = this._markerAt(e.clientX - rect.left, e.clientY - rect.top, rect);
    if (m) this.onClickMarker(m);
  }

  draw() {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width) return;
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = rect.width * dpr;
    this.canvas.height = rect.height * dpr;
    const ctx = this.canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);

    const W = rect.width, H = rect.height;
    const plotW = W - this.padR, plotH = H - this.padB;
    if (!this.candles.length) {
      ctx.fillStyle = COLORS.text;
      ctx.font = "12px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("no candle data", plotW / 2, plotH / 2);
      return;
    }
    const [yMin, yMax] = this._yRange();
    const vis = Math.max(1, this.view.end - this.view.start);
    const cw = plotW / vis;

    ctx.font = "10.5px 'JetBrains Mono', monospace";
    ctx.fillStyle = COLORS.text;

    const step = niceStep(yMax - yMin, plotH / 55);
    const startP = Math.ceil(yMin / step) * step;
    ctx.strokeStyle = COLORS.grid;
    ctx.textAlign = "left";
    for (let p = startP; p <= yMax; p += step) {
      const y = this.p2y(p, rect, yMin, yMax);
      ctx.beginPath();
      ctx.moveTo(this.padL, y); ctx.lineTo(plotW, y);
      ctx.stroke();
      ctx.fillText(fmtAxis(p, step), plotW + 6, y + 3);
    }

    const spanMs = (this.candles[Math.min(this.candles.length - 1, Math.ceil(this.view.end))] || this.candles[this.candles.length - 1]).t -
      (this.candles[Math.max(0, Math.floor(this.view.start))] || this.candles[0]).t;
    const tickN = Math.max(2, Math.floor(plotW / 95));
    const tickStep = Math.max(1, Math.round(vis / tickN));
    ctx.textAlign = "center";
    const s0 = Math.max(0, Math.floor(this.view.start));
    const e0 = Math.min(this.candles.length, Math.ceil(this.view.end));
    for (let i = s0; i < e0; i += tickStep) {
      const x = this.idx2x(i, rect);
      ctx.strokeStyle = "rgba(43,56,73,.25)";
      ctx.beginPath();
      ctx.moveTo(x, this.padT); ctx.lineTo(x, plotH);
      ctx.stroke();
      ctx.fillStyle = COLORS.text;
      ctx.fillText(fmtTimeLabel(this.candles[i].t, spanMs), x, plotH + 15);
    }

    let maxVol = 0;
    for (let i = s0; i < e0; i++) maxVol = Math.max(maxVol, this.candles[i].v || 0);
    const volH = plotH * 0.14;
    for (let i = s0; i < e0; i++) {
      const c = this.candles[i];
      const x = this.idx2x(i, rect);
      const up = c.c >= c.o;
      const bw = Math.max(1, Math.min(cw * 0.62, 14));
      if (maxVol > 0 && c.v) {
        ctx.fillStyle = up ? COLORS.volUp : COLORS.volDown;
        const vh = (c.v / maxVol) * (volH - 2);
        ctx.fillRect(x - bw / 2, plotH - vh, bw, vh);
      }
      const yO = this.p2y(c.o, rect, yMin, yMax);
      const yC = this.p2y(c.c, rect, yMin, yMax);
      const yH = this.p2y(c.h, rect, yMin, yMax);
      const yL = this.p2y(c.l, rect, yMin, yMax);
      ctx.strokeStyle = up ? COLORS.up : COLORS.down;
      ctx.beginPath();
      ctx.moveTo(x, yH); ctx.lineTo(x, yL);
      ctx.stroke();
      ctx.fillStyle = up ? COLORS.upFill : COLORS.downFill;
      const top = Math.min(yO, yC);
      const hgt = Math.max(1, Math.abs(yC - yO));
      ctx.fillRect(x - bw / 2, top, bw, hgt);
    }

    for (const pl of this.priceLines) {
      if (pl.price < yMin || pl.price > yMax) continue;
      const y = this.p2y(pl.price, rect, yMin, yMax);
      ctx.save();
      ctx.strokeStyle = pl.color;
      if (pl.dash !== false) ctx.setLineDash([5, 4]);
      ctx.lineWidth = pl.width || 1;
      ctx.beginPath();
      ctx.moveTo(this.padL, y); ctx.lineTo(plotW, y);
      ctx.stroke();
      ctx.restore();
      if (pl.label) {
        const tw = ctx.measureText(pl.label).width + 10;
        ctx.fillStyle = pl.color;
        ctx.globalAlpha = 0.9;
        ctx.fillRect(plotW - tw - 4, y - 8, tw, 15);
        ctx.globalAlpha = 1;
        ctx.fillStyle = "#0a0d12";
        ctx.textAlign = "left";
        ctx.font = "600 10px 'JetBrains Mono', monospace";
        ctx.fillText(pl.label, plotW - tw + 1, y + 3);
        ctx.font = "10.5px 'JetBrains Mono', monospace";
      }
    }

    if (this.lastPrice) {
      const y = this.p2y(this.lastPrice, rect, yMin, yMax);
      if (y > this.padT && y < plotH) {
        ctx.save();
        ctx.strokeStyle = "rgba(232,237,244,.55)";
        ctx.setLineDash([2, 3]);
        ctx.beginPath();
        ctx.moveTo(this.padL, y); ctx.lineTo(plotW, y);
        ctx.stroke();
        ctx.restore();
        const label = fmtAxis(this.lastPrice, 0.01);
        ctx.fillStyle = "rgba(232,237,244,.9)";
        ctx.fillRect(plotW + 2, y - 8, this.padR - 4, 15);
        ctx.fillStyle = "#0a0d12";
        ctx.textAlign = "left";
        ctx.fillText(label, plotW + 6, y + 3);
      }
    }

    for (const m of this.markers) {
      const i = this._tsIndex(m.ts);
      if (i === null || i < s0 - 1 || i > e0) continue;
      const x = this.idx2x(i, rect);
      const y = this.p2y(m.price, rect, yMin, yMax);
      const up = m.side === "buy";
      const col = up ? COLORS.up : COLORS.down;
      const dir = up ? 1 : -1;
      const yy = up ? y + 9 : y - 9;
      ctx.save();
      ctx.fillStyle = col;
      ctx.beginPath();
      ctx.moveTo(x, yy + dir * 7);
      ctx.lineTo(x - 5.5, yy - dir * 2);
      ctx.lineTo(x + 5.5, yy - dir * 2);
      ctx.closePath();
      ctx.fill();
      ctx.strokeStyle = "#0a0d12";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.restore();
    }

    if (this.hover && !this.drag) {
      const hv = this.hover;
      const cx = Math.round(this.idx2x(hv.idx, rect));
      ctx.save();
      ctx.strokeStyle = COLORS.cross;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(cx, this.padT); ctx.lineTo(cx, plotH);
      ctx.moveTo(this.padL, hv.y); ctx.lineTo(plotW, hv.y);
      ctx.stroke();
      ctx.setLineDash([]);
      const pl = fmtAxis(hv.price, niceStep(yMax - yMin, 20));
      ctx.fillStyle = "rgba(26,35,48,.95)";
      ctx.fillRect(plotW + 2, hv.y - 8, this.padR - 4, 15);
      ctx.fillStyle = "#e8edf4";
      ctx.textAlign = "left";
      ctx.fillText(pl, plotW + 6, hv.y + 3);
      ctx.restore();
    }
  }
}

export class LineChart {
  constructor(container, opts = {}) {
    this.container = container;
    this.opts = opts;
    this.wrap = document.createElement("div");
    this.wrap.className = "chart-wrap" + (opts.small ? " small" : "");
    this.canvas = document.createElement("canvas");
    this.wrap.append(this.canvas);
    container.append(this.wrap);
    this.data = [];
    this._ro = new ResizeObserver(() => this.draw());
    this._ro.observe(this.wrap);
  }

  setData(data) {
    this.data = data || [];
    this.draw();
  }

  destroy() { this._ro.disconnect(); }

  draw() {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width) return;
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = rect.width * dpr;
    this.canvas.height = rect.height * dpr;
    const ctx = this.canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    const d = this.data;
    if (d.length < 2) return;
    const padR = 60, padB = 20, padT = 10;
    const W = rect.width, H = rect.height;
    const vals = d.map((x) => x.v);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.1 || hi * 0.02 || 1;
    lo -= pad; hi += pad;
    const t0 = d[0].t, t1 = d[d.length - 1].t;
    const x = (t) => 6 + (t - t0) / Math.max(1, t1 - t0) * (W - padR - 6);
    const y = (v) => padT + (hi - v) / (hi - lo) * (H - padB - padT);
    ctx.font = "10.5px 'JetBrains Mono', monospace";
    const step = niceStep(hi - lo, (H - padB - padT) / 40);
    ctx.fillStyle = COLORS.text;
    ctx.strokeStyle = COLORS.grid;
    ctx.textAlign = "left";
    for (let p = Math.ceil(lo / step) * step; p <= hi; p += step) {
      const yy = y(p);
      ctx.beginPath();
      ctx.moveTo(6, yy); ctx.lineTo(W - padR, yy);
      ctx.stroke();
      ctx.fillText(fmtAxis(p, step), W - padR + 4, yy + 3);
    }
    const grad = ctx.createLinearGradient(0, padT, 0, H - padB);
    const col = this.opts.color || "#ff6a1a";
    grad.addColorStop(0, col + "55");
    grad.addColorStop(1, col + "00");
    ctx.beginPath();
    ctx.moveTo(x(t0), y(d[0].v));
    for (const p of d) ctx.lineTo(x(p.t), y(p.v));
    ctx.strokeStyle = col;
    ctx.lineWidth = 1.6;
    ctx.stroke();
    ctx.lineTo(x(t1), H - padB);
    ctx.lineTo(x(t0), H - padB);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();
  }
}

