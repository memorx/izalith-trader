/*
 * Izalith - gráficas en SVG sin librerías.
 *
 * Especificación (skill de dataviz): líneas de 2px, rejilla en línea fina sólida, barras
 * de máx. 24px con punta redondeada de 4px, marcadores >= 8px con anillo del color de
 * la superficie, tooltip en todo gráfico, leyenda cuando hay 2+ series, texto siempre
 * con colores de texto (nunca con el color de la serie), una sola escala vertical.
 */
(function () {
  const NS = "http://www.w3.org/2000/svg";

  function el(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const k in attrs || {}) node.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(node);
    return node;
  }

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  const fmt = {
    pct(x, d = 1, signed = true) {
      if (x === null || x === undefined || Number.isNaN(x)) return "—";
      const v = (x * 100).toFixed(d);
      return (signed && x > 0 ? "+" : "") + v + "%";
    },
    money(x, d = 2) {
      if (x === null || x === undefined || Number.isNaN(x)) return "—";
      return (x < 0 ? "-$" : "$") + Math.abs(x).toLocaleString("es-MX", { minimumFractionDigits: d, maximumFractionDigits: d });
    },
    compact(x) {
      if (x === null || x === undefined) return "—";
      const a = Math.abs(x);
      if (a >= 1e9) return (x / 1e9).toFixed(1) + " mil M";
      if (a >= 1e6) return (x / 1e6).toFixed(1) + " M";
      if (a >= 1e3) return (x / 1e3).toFixed(1) + " K";
      return x.toFixed(0);
    },
    num(x, d = 2) {
      if (x === null || x === undefined || Number.isNaN(x)) return "—";
      return Number(x).toLocaleString("es-MX", { minimumFractionDigits: d, maximumFractionDigits: d });
    },
    mult(x) { return x === null || x === undefined ? "—" : "×" + fmt.num(x, x >= 10 ? 0 : 2); },
    date(t) {
      return new Date(t).toLocaleDateString("es-MX", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
    },
    datetime(t) {
      return new Date(t).toLocaleString("es-MX", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    },
  };

  // ── Escalas ──

  function niceTicks(min, max, count = 5) {
    if (min === max) { min -= 1; max += 1; }
    const span = max - min;
    const step0 = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const norm = step0 / mag;
    const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
    const start = Math.ceil(min / step) * step;
    const ticks = [];
    for (let v = start; v <= max + step * 1e-9; v += step) ticks.push(Math.round(v / step) * step);
    return ticks;
  }

  function niceDomain(min, max, count = 5) {
    if (min === max) { min -= Math.abs(min) * 0.1 || 1; max += Math.abs(max) * 0.1 || 1; }
    const step0 = (max - min) / count;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const norm = step0 / mag;
    const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
    const lo = Math.floor(min / step + 1e-9) * step, hi = Math.ceil(max / step - 1e-9) * step;
    const ticks = [];
    for (let v = lo; v <= hi + step * 1e-9; v += step) ticks.push(Math.round(v / step) * step);
    return { ticks, lo, hi, step };
  }

  function labelsFor(ticks, format, precise) {
    const labels = ticks.map(format);
    if (new Set(labels).size === labels.length || !precise) return labels;
    return ticks.map(precise);
  }

  function logTicks(min, max) {
    const ticks = [];
    const lo = Math.floor(Math.log10(min)), hi = Math.ceil(Math.log10(max));
    for (let e = lo; e <= hi; e++) {
      for (const m of [1, 2, 5]) {
        const v = m * Math.pow(10, e);
        if (v >= min * 0.999 && v <= max * 1.001) ticks.push(v);
      }
    }
    return ticks.length >= 2 ? ticks : niceTicks(min, max);
  }

  function timeTicks(t0, t1, width) {
    const span = t1 - t0, day = 864e5;
    const maxTicks = Math.max(2, Math.floor(width / 90));
    const out = [];
    if (span > 3 * 365 * day) {
      const y0 = new Date(t0).getUTCFullYear(), y1 = new Date(t1).getUTCFullYear();
      const every = Math.max(1, Math.ceil((y1 - y0 + 1) / maxTicks));
      for (let y = y0 + 1; y <= y1; y += every) out.push({ t: Date.UTC(y, 0, 1), label: String(y) });
    } else if (span > 60 * day) {
      const d = new Date(t0);
      let m = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1));
      const months = span / (30 * day), every = Math.max(1, Math.ceil(months / maxTicks));
      while (m.getTime() <= t1) {
        out.push({ t: m.getTime(), label: m.toLocaleDateString("es-MX", { month: "short", year: "2-digit", timeZone: "UTC" }) });
        m = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + every, 1));
      }
    } else {
      const n = Math.min(maxTicks, 6);
      for (let i = 1; i < n; i++) {
        const t = t0 + (span * i) / n;
        out.push({ t, label: span > 2 * day ? fmt.date(t) : new Date(t).toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" }) });
      }
    }
    return out;
  }

  // ── Tooltip compartido ──

  let tip;
  function tooltip() {
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "viz-tooltip";
      tip.setAttribute("role", "status");
      document.body.appendChild(tip);
    }
    return tip;
  }

  function showTip(x, y, title, rows) {
    const t = tooltip();
    t.replaceChildren();
    const h = document.createElement("div");
    h.className = "viz-tooltip-title";
    h.textContent = title;
    t.appendChild(h);
    for (const r of rows) {
      const row = document.createElement("div");
      row.className = "viz-tooltip-row";
      if (r.color) {
        const key = document.createElement("span");
        key.className = r.shape ? "viz-key-dot" : "viz-key-line";
        key.style.background = r.color;
        row.appendChild(key);
      }
      const v = document.createElement("strong");
      v.textContent = r.value;
      row.appendChild(v);
      const n = document.createElement("span");
      n.className = "viz-tooltip-name";
      n.textContent = r.name;
      row.appendChild(n);
      t.appendChild(row);
    }
    t.style.display = "block";
    const pad = 14, w = t.offsetWidth, hgt = t.offsetHeight;
    let left = x + pad, top = y + pad;
    if (left + w > window.innerWidth - 8) left = x - w - pad;
    if (top + hgt > window.innerHeight - 8) top = y - hgt - pad;
    t.style.left = Math.max(8, left) + "px";
    t.style.top = Math.max(8, top) + "px";
  }

  function hideTip() { if (tip) tip.style.display = "none"; }

  function legend(container, items) {
    const box = document.createElement("div");
    box.className = "viz-legend";
    for (const it of items) {
      const span = document.createElement("span");
      span.className = "viz-legend-item";
      const key = document.createElement("span");
      key.className = it.kind === "bar" ? "viz-key-rect" : it.kind === "shape" ? "viz-key-dot" : "viz-key-line";
      key.style.background = it.color;
      span.appendChild(key);
      span.appendChild(document.createTextNode(it.name));
      box.appendChild(span);
    }
    container.appendChild(box);
  }

  function observe(container, render) {
    let last = 0;
    const ro = new ResizeObserver(() => {
      const w = container.clientWidth;
      if (Math.abs(w - last) > 4) { last = w; render(); }
    });
    ro.observe(container);
    container._ro && container._ro.disconnect();
    container._ro = ro;
  }

  // ── Línea ──

  function line(container, opts) {
    const render = () => {
      container.replaceChildren();
      const series = opts.series.filter((s) => s.values && s.values.length);
      if (!series.length) { empty(container, opts.empty || "Sin datos todavía"); return; }
      if (series.length >= 2) legend(container, series.map((s) => ({ name: s.name, color: s.color })));

      const W = Math.max(280, container.clientWidth), H = opts.height || 260;
      const m = { l: 58, r: opts.endLabels === false ? 16 : 78, t: 10, b: 28 };
      const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "viz-svg", role: "img",
        "aria-label": opts.label || "Gráfica de línea" }, container);

      let t0 = Infinity, t1 = -Infinity, v0 = Infinity, v1 = -Infinity;
      for (const s of series) for (const [t, v] of s.values) {
        if (t < t0) t0 = t; if (t > t1) t1 = t;
        if (v < v0) v0 = v; if (v > v1) v1 = v;
      }
      if (opts.zero) { v0 = Math.min(v0, 0); v1 = Math.max(v1, 0); }
      const log = opts.log && v0 > 0;
      let ticks;
      if (log) ticks = logTicks(v0, v1);
      else { const d = niceDomain(v0, v1, opts.ticks || 4); ticks = d.ticks; v0 = d.lo; v1 = d.hi; }
      const x = (t) => m.l + ((t - t0) / (t1 - t0 || 1)) * (W - m.l - m.r);
      const y = log
        ? (v) => m.t + (1 - (Math.log(v) - Math.log(v0)) / (Math.log(v1) - Math.log(v0) || 1)) * (H - m.t - m.b)
        : (v) => m.t + (1 - (v - v0) / (v1 - v0 || 1)) * (H - m.t - m.b);
      const yf = opts.yFormat || ((v) => fmt.num(v, 2));

      const grid = el("g", { class: "viz-grid" }, svg);
      const tickLabels = labelsFor(ticks, opts.tickFormat || yf, opts.preciseFormat);
      ticks.forEach((tv, i) => {
        el("line", { x1: m.l, x2: W - m.r, y1: y(tv), y2: y(tv) }, grid);
        const lbl = el("text", { x: m.l - 8, y: y(tv) + 4, "text-anchor": "end", class: "viz-axis-text" }, grid);
        lbl.textContent = tickLabels[i];
      });
      if (opts.reference !== undefined && opts.reference >= v0 && opts.reference <= v1) {
        el("line", { x1: m.l, x2: W - m.r, y1: y(opts.reference), y2: y(opts.reference), class: "viz-baseline" }, svg);
      }
      for (const tk of timeTicks(t0, t1, W - m.l - m.r)) {
        const lbl = el("text", { x: x(tk.t), y: H - 8, "text-anchor": "middle", class: "viz-axis-text" }, svg);
        lbl.textContent = tk.label;
      }

      series.forEach((s, i) => {
        const d = s.values.map(([t, v], j) => (j ? "L" : "M") + x(t).toFixed(1) + "," + y(v).toFixed(1)).join("");
        if (s.area) {
          const base = opts.zero && !log ? y(0) : H - m.b;
          const area = d + `L${x(s.values[s.values.length - 1][0]).toFixed(1)},${base}L${x(s.values[0][0]).toFixed(1)},${base}Z`;
          el("path", { d: area, fill: s.color, opacity: 0.1 }, svg);
        }
        el("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
      });

      // Etiquetas al final de la línea, sólo si no chocan
      if (opts.endLabels !== false) {
        const ends = series.map((s) => ({ s, v: s.values[s.values.length - 1][1], yy: y(s.values[s.values.length - 1][1]) }))
          .sort((a, b) => a.yy - b.yy);
        const collide = ends.some((e, i) => i && e.yy - ends[i - 1].yy < 14);
        for (const e of ends) {
          el("circle", { cx: x(t1), cy: e.yy, r: 4, fill: e.s.color, stroke: css("--surface-1"), "stroke-width": 2 }, svg);
          if (!collide || series.length === 1) {
            const lbl = el("text", { x: x(t1) + 8, y: e.yy + 4, class: "viz-label" }, svg);
            lbl.textContent = (opts.preciseFormat && series.length === 1 ? opts.preciseFormat : yf)(e.v);
          }
        }
      }

      // Cruz que sigue al puntero
      const cross = el("line", { y1: m.t, y2: H - m.b, class: "viz-crosshair", visibility: "hidden" }, svg);
      const dots = series.map((s) => el("circle", { r: 4, fill: s.color, stroke: css("--surface-1"), "stroke-width": 2, visibility: "hidden" }, svg));
      const hit = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent", tabindex: 0,
        class: "viz-hit", "aria-label": "Mueve el cursor o usa las flechas para leer valores" }, svg);
      const main = series[0].values;
      let idx = main.length - 1;

      function nearest(vals, t) {
        let lo = 0, hi = vals.length - 1;
        while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (vals[mid][0] < t) lo = mid; else hi = mid; }
        return Math.abs(vals[lo][0] - t) <= Math.abs(vals[hi][0] - t) ? lo : hi;
      }
      function show(t, cx, cy) {
        const rows = [];
        cross.setAttribute("x1", x(t)); cross.setAttribute("x2", x(t)); cross.setAttribute("visibility", "visible");
        series.forEach((s, i) => {
          const j = nearest(s.values, t), [tt, v] = s.values[j];
          dots[i].setAttribute("cx", x(tt)); dots[i].setAttribute("cy", y(v)); dots[i].setAttribute("visibility", "visible");
          rows.push({ name: s.name, value: yf(v), color: s.color });
        });
        showTip(cx, cy, opts.timeFormat ? opts.timeFormat(t) : fmt.date(t), rows);
      }
      function hide() {
        cross.setAttribute("visibility", "hidden");
        dots.forEach((d) => d.setAttribute("visibility", "hidden"));
        hideTip();
      }
      hit.addEventListener("pointermove", (ev) => {
        const r = svg.getBoundingClientRect();
        const t = t0 + ((ev.clientX - r.left - m.l) / (W - m.l - m.r)) * (t1 - t0);
        idx = nearest(main, t);
        show(main[idx][0], ev.clientX, ev.clientY);
      });
      hit.addEventListener("pointerleave", hide);
      hit.addEventListener("blur", hide);
      hit.addEventListener("keydown", (ev) => {
        if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
        ev.preventDefault();
        idx = Math.max(0, Math.min(main.length - 1, idx + (ev.key === "ArrowRight" ? 1 : -1)));
        const r = svg.getBoundingClientRect();
        show(main[idx][0], r.left + x(main[idx][0]), r.top + y(main[idx][1]));
      });
    };
    observe(container, render);
    render();
  }

  // ── Columnas (con signo) ──

  function columns(container, opts) {
    const render = () => {
      container.replaceChildren();
      const items = opts.items || [];
      if (!items.length) { empty(container, "Sin datos"); return; }
      const W = Math.max(260, container.clientWidth), H = opts.height || 200;
      const m = { l: 52, r: 12, t: 18, b: 26 };
      const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "viz-svg", role: "img",
        "aria-label": opts.label || "Gráfica de columnas" }, container);
      const vals = items.map((d) => d.value);
      const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals), 4);
      const v0 = Math.min(ticks[0], ...vals), v1 = Math.max(ticks[ticks.length - 1], ...vals);
      const y = (v) => m.t + (1 - (v - v0) / (v1 - v0 || 1)) * (H - m.t - m.b);
      const yf = opts.format || ((v) => fmt.num(v));
      const g = el("g", { class: "viz-grid" }, svg);
      for (const tv of ticks) {
        el("line", { x1: m.l, x2: W - m.r, y1: y(tv), y2: y(tv) }, g);
        el("text", { x: m.l - 8, y: y(tv) + 4, "text-anchor": "end", class: "viz-axis-text" }, g).textContent = yf(tv);
      }
      el("line", { x1: m.l, x2: W - m.r, y1: y(0), y2: y(0), class: "viz-baseline" }, svg);
      const band = (W - m.l - m.r) / items.length;
      const bw = Math.min(24, Math.max(4, band - 2));
      const showLabels = items.length <= 14 && band >= 34;
      items.forEach((d, i) => {
        const cx = m.l + band * i + band / 2;
        const top = y(Math.max(0, d.value)), bot = y(Math.min(0, d.value));
        const h = Math.max(1, bot - top), x0 = cx - bw / 2, r = Math.min(4, h, bw / 2);
        const pos = d.value >= 0;
        const color = pos ? css("--pos") : css("--neg");
        // Punta redondeada en el extremo del dato, recta en la base
        const path = pos
          ? `M${x0},${bot}V${top + r}Q${x0},${top} ${x0 + r},${top}H${x0 + bw - r}Q${x0 + bw},${top} ${x0 + bw},${top + r}V${bot}Z`
          : `M${x0},${top}V${bot - r}Q${x0},${bot} ${x0 + r},${bot}H${x0 + bw - r}Q${x0 + bw},${bot} ${x0 + bw},${bot - r}V${top}Z`;
        el("path", { d: path, fill: color, class: "viz-bar" }, svg);
        const hit = el("rect", { x: cx - band / 2, y: m.t, width: band, height: H - m.t - m.b, fill: "transparent", tabindex: 0 }, svg);
        const label = el("text", { x: cx, y: H - 8, "text-anchor": "middle", class: "viz-axis-text" }, svg);
        label.textContent = d.label;
        if (showLabels) {
          const below = bot + 12 > H - m.b - 2 ? y(0) - 5 : bot + 12;
          const vt = el("text", { x: cx, y: pos ? top - 5 : below, "text-anchor": "middle", class: "viz-label-sm" }, svg);
          vt.textContent = yf(d.value);
        }
        const on = (ev) => {
          const r = hit.getBoundingClientRect();
          showTip(ev.clientX || r.left + r.width / 2, ev.clientY || r.top, d.label, [{ name: opts.seriesName || "", value: yf(d.value), color }]);
        };
        hit.addEventListener("pointermove", on);
        hit.addEventListener("focus", on);
        hit.addEventListener("pointerleave", hideTip);
        hit.addEventListener("blur", hideTip);
      });
    };
    observe(container, render);
    render();
  }

  // ── Mapa de riesgo (dispersión) ──

  const SHAPES = {
    circle: (g, cx, cy, s, attrs) => el("circle", { cx, cy, r: s, ...attrs }, g),
    triangle: (g, cx, cy, s, attrs) => el("path", { d: `M${cx},${cy - s * 1.25}L${cx + s * 1.15},${cy + s * 0.85}L${cx - s * 1.15},${cy + s * 0.85}Z`, ...attrs }, g),
    square: (g, cx, cy, s, attrs) => el("rect", { x: cx - s * 0.9, y: cy - s * 0.9, width: s * 1.8, height: s * 1.8, rx: 1.5, ...attrs }, g),
  };

  function scatter(container, opts) {
    const render = () => {
      container.replaceChildren();
      const pts = opts.points || [];
      if (!pts.length) { empty(container, "Sin estrategias con este filtro"); return; }
      if (opts.legend) legend(container, opts.legend.map((l) => ({ ...l, kind: "shape" })));
      const W = Math.max(300, container.clientWidth), H = opts.height || 420;
      const m = { l: 62, r: 24, t: 16, b: 46 };
      const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "viz-svg", role: "img",
        "aria-label": opts.label || "Mapa de riesgo y rendimiento" }, container);
      const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
      const dx = niceDomain(0, Math.max(...xs, 0.1), 5), dy = niceDomain(Math.min(0, ...ys, opts.reference ?? 0), Math.max(...ys), 5);
      const xt = dx.ticks, yt = dy.ticks;
      const x0 = 0, x1 = dx.hi, y0 = dy.lo, y1 = dy.hi;
      const X = (v) => m.l + ((v - x0) / (x1 - x0 || 1)) * (W - m.l - m.r);
      const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0 || 1)) * (H - m.t - m.b);
      const g = el("g", { class: "viz-grid" }, svg);
      for (const v of yt) {
        el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }, g);
        el("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end", class: "viz-axis-text" }, g).textContent = opts.yFormat(v);
      }
      for (const v of xt) {
        el("text", { x: X(v), y: H - 26, "text-anchor": "middle", class: "viz-axis-text" }, g).textContent = opts.xFormat(v);
      }
      el("text", { x: m.l, y: H - 6, class: "viz-axis-title" }, svg).textContent = opts.xLabel || "";
      el("text", { x: W - m.r, y: H - 6, "text-anchor": "end", class: "viz-axis-title" }, svg).textContent = opts.xLabelRight || "";
      if (opts.reference !== undefined) {
        el("line", { x1: m.l, x2: W - m.r, y1: Y(opts.reference), y2: Y(opts.reference), class: "viz-baseline" }, svg);
        el("text", { x: W - m.r, y: Y(opts.reference) - 6, "text-anchor": "end", class: "viz-axis-text" }, svg).textContent = opts.referenceLabel || "";
      }

      // Etiquetas directas sólo donde caben (la etiqueta de la línea de referencia ya ocupa su lugar)
      const placed = [];
      if (opts.reference !== undefined && opts.referenceLabel) {
        const rw = opts.referenceLabel.length * 6.2;
        placed.push({ x: W - m.r - rw, y: Y(opts.reference) - 18, w: rw, h: 14 });
      }
      const order = [...pts].sort((a, b) => (b.priority || 0) - (a.priority || 0));
      const labelOf = new Map();
      for (const p of order) {
        const lx = X(p.x) + 10, ly = Y(p.y) + 4, w = p.label.length * 6.4;
        const box = { x: lx, y: ly - 11, w, h: 14 };
        const fits = lx + w < W - 4 && !placed.some((b) => !(box.x > b.x + b.w || box.x + box.w < b.x || box.y > b.y + b.h || box.y + box.h < b.y))
          && !pts.some((q) => q !== p && X(q.x) > box.x - 6 && X(q.x) < box.x + box.w + 6 && Math.abs(Y(q.y) - (ly - 4)) < 9);
        if (fits) { placed.push(box); labelOf.set(p, box); }
      }
      const layer = el("g", {}, svg);
      for (const p of pts) {
        const cx = X(p.x), cy = Y(p.y);
        const grp = el("g", { class: "viz-point" + (p.selected ? " is-selected" : ""), tabindex: 0, role: "button", "aria-label": p.label }, layer);
        el("circle", { cx, cy, r: 13, fill: "transparent" }, grp);   // área para el cursor (26px)
        SHAPES[p.shape || "circle"](grp, cx, cy, p.selected ? 6.5 : 5, { fill: p.color, stroke: css("--surface-1"), "stroke-width": 2 });
        if (labelOf.has(p)) {
          const b = labelOf.get(p);
          el("text", { x: b.x, y: b.y + 11, class: "viz-label-sm" }, layer).textContent = p.label;
        }
        const on = (ev) => {
          const r = grp.getBoundingClientRect();
          showTip(ev.clientX || r.right, ev.clientY || r.top, p.label, p.tip);
        };
        grp.addEventListener("pointermove", on);
        grp.addEventListener("focus", on);
        grp.addEventListener("pointerleave", hideTip);
        grp.addEventListener("blur", hideTip);
        if (opts.onSelect) {
          grp.addEventListener("click", () => { hideTip(); opts.onSelect(p); });
          grp.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); opts.onSelect(p); } });
        }
      }
    };
    observe(container, render);
    render();
  }

  function empty(container, text) {
    const d = document.createElement("p");
    d.className = "viz-empty";
    d.textContent = text;
    container.appendChild(d);
  }

  // ── Tabla (gemela accesible de cada gráfica, y tablas normales) ──

  function table(container, columns, rows, opts = {}) {
    container.replaceChildren();
    if (!rows.length) { empty(container, opts.empty || "Nada que mostrar"); return; }
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    const t = document.createElement("table");
    const thead = t.createTHead().insertRow();
    let sortKey = opts.sortKey, sortDir = opts.sortDir || -1;
    columns.forEach((c) => {
      const th = document.createElement("th");
      th.textContent = c.label;
      if (c.num) th.className = "num";
      if (c.sort !== false && opts.sortable) {
        th.tabIndex = 0;
        th.classList.add("sortable");
        th.setAttribute("aria-sort", c.key === sortKey ? (sortDir > 0 ? "ascending" : "descending") : "none");
        const sort = () => {
          sortDir = sortKey === c.key ? -sortDir : -1;
          sortKey = c.key;
          table(container, columns, rows, { ...opts, sortKey, sortDir });
        };
        th.addEventListener("click", sort);
        th.addEventListener("keydown", (e) => { if (e.key === "Enter") sort(); });
      }
      thead.appendChild(th);
    });
    let data = rows;
    if (sortKey) {
      const col = columns.find((c) => c.key === sortKey);
      const val = col && col.sortValue ? col.sortValue : (r) => r[sortKey];
      data = [...rows].sort((a, b) => {
        const va = val(a), vb = val(b);
        if (va === vb) return 0;
        if (va === null || va === undefined) return 1;
        if (vb === null || vb === undefined) return -1;
        return (va > vb ? 1 : -1) * sortDir;
      });
    }
    const tbody = t.createTBody();
    for (const r of data) {
      const tr = tbody.insertRow();
      if (opts.onRow) {
        tr.tabIndex = 0;
        tr.classList.add("clickable");
        tr.addEventListener("click", () => opts.onRow(r));
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter") opts.onRow(r); });
        if (opts.selected && opts.selected(r)) tr.classList.add("is-selected");
      }
      for (const c of columns) {
        const td = tr.insertCell();
        if (c.num) td.className = "num";
        const v = c.render ? c.render(r) : r[c.key];
        if (v instanceof Node) td.appendChild(v);
        else td.textContent = v === null || v === undefined ? "—" : String(v);
      }
    }
    wrap.appendChild(t);
    container.appendChild(wrap);
  }

  window.Viz = { line, columns, scatter, table, fmt, hideTip };
})();
