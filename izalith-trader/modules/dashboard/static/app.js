/* Izalith - lógica del panel. Todo texto que viene de la API entra con textContent. */
(function () {
  const { fmt } = Viz;
  const $ = (sel) => document.querySelector(sel);

  // ── Token (opcional): se pasa una vez como #token=... y se guarda en la sesión ──
  let token = "";
  try {
    const m = location.hash.match(/token=([^&]+)/);
    if (m) { sessionStorage.setItem("iz_token", decodeURIComponent(m[1])); history.replaceState(null, "", location.pathname + "#resumen"); }
    token = sessionStorage.getItem("iz_token") || "";
  } catch (e) { /* sin almacenamiento: se trabaja sin token */ }

  async function api(path) {
    const res = await fetch(path, { headers: token ? { "X-Token": token } : {} });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* sin cuerpo */ }
      const err = new Error(detail);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  const fill = (node, ...kids) => node.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));

  function h(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined) node.append(c instanceof Node ? c : String(c));
    return node;
  }

  const SEM = {
    verde: { label: "Pasa", cls: "good", shape: "circle", color: () => css("--good") },
    amarillo: { label: "Casi", cls: "warn", shape: "triangle", color: () => css("--warning") },
    rojo: { label: "No sirve", cls: "bad", shape: "square", color: () => css("--critical") },
    investigacion: { label: "En investigación", cls: "info", shape: "circle", color: () => css("--accent") },
    error: { label: "Error", cls: "bad", shape: "square", color: () => css("--critical") },
  };
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const statusEl = (sem) => h("span", { class: "status " + (SEM[sem] || SEM.error).cls, text: (SEM[sem] || SEM.error).label });
  const signed = (x, d = 1) => h("span", { class: x > 0 ? "up" : x < 0 ? "down" : "", text: fmt.pct(x, d) });
  const moneySigned = (x) => h("span", { class: x > 0 ? "up" : x < 0 ? "down" : "", text: (x > 0 ? "+" : "") + fmt.money(x) });

  // ── Tema ──
  const themeBtn = $("#theme");
  function applyTheme(t) {
    if (t) document.documentElement.setAttribute("data-theme", t); else document.documentElement.removeAttribute("data-theme");
    themeBtn.textContent = (t || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark" ? "Tema claro" : "Tema oscuro";
  }
  let theme = null;
  try { theme = localStorage.getItem("iz_theme"); } catch (e) { /* sin almacenamiento */ }
  applyTheme(theme);
  themeBtn.addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    theme = cur === "dark" ? "light" : "dark";
    try { localStorage.setItem("iz_theme", theme); } catch (e) { /* sin almacenamiento */ }
    applyTheme(theme);
    rerenderCharts();
  });

  // ── Navegación ──
  const views = ["resumen", "estrategias", "vivo", "mercados", "donde"];
  const loaded = new Set();
  function route() {
    const v = (location.hash.replace("#", "").split("&")[0]) || "resumen";
    const view = views.includes(v) ? v : "resumen";
    for (const id of views) $("#view-" + id).classList.toggle("active", id === view);
    document.querySelectorAll("nav.tabs a").forEach((a) => a.setAttribute("aria-current", a.dataset.view === view ? "page" : "false"));
    load(view);
  }
  window.addEventListener("hashchange", route);

  async function load(view, force = false) {
    try {
      if (view === "resumen") await loadResumen();
      if (view === "estrategias" && (!loaded.has(view) || force)) await loadLab();
      if (view === "vivo") await loadVivo();
      if (view === "mercados" && (!loaded.has(view) || force)) await loadMarkets(false);
      if (view === "donde" && !loaded.has(view)) await loadVenues();
      loaded.add(view);
      $("#updated").textContent = "Actualizado " + new Date().toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch (e) {
      if (e.status === 401) $("#updated").textContent = "Falta el token: abre el panel con #token=…";
      else $("#updated").textContent = "Error: " + e.message;
    }
  }

  const chartRenders = [];
  function rerenderCharts() { chartRenders.forEach((fn) => fn()); }

  // ══ Resumen ══

  let status = null, hours = 24;
  $("#range").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    hours = Number(b.dataset.h);
    $("#range").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
    loadEquity();
  });

  async function loadResumen() {
    status = await api("/api/status");
    const mode = $("#mode");
    if (!status.running) {
      $("#offline").hidden = false; $("#summary").hidden = true; mode.hidden = true;
      return;
    }
    $("#offline").hidden = true; $("#summary").hidden = false;
    mode.hidden = false;
    mode.textContent = status.dry_run ? "Papel" : "Dinero real";
    mode.className = "mode-badge" + (status.dry_run ? "" : " real");

    const p = await api("/api/portfolio");
    $("#hero-value").textContent = fmt.money(p.total_value_usd);
    const change = p.total_value_usd - p.initial_cash_usd;
    $("#hero-caption").textContent = status.dry_run
      ? `Valor del portafolio de papel. Empezó con ${fmt.money(p.initial_cash_usd, 0)}: ${change >= 0 ? "+" : ""}${fmt.money(change)}`
      : "Valor de lo que maneja el bot";
    const tiles = $("#tiles");
    tiles.replaceChildren(
      tile("P&L realizado hoy", moneySigned(p.realized_pnl_today), "día en " + status.timezone),
      tile("P&L realizado total", moneySigned(p.realized_pnl_total)),
      tile("P&L abierto", moneySigned(p.unrealized_pnl)),
      tile("Efectivo", fmt.money(p.cash_usd)),
      tile("Posiciones abiertas", String(p.position_count)),
    );

    // Avisos
    const alerts = [];
    for (const o of status.unrecorded_orders || []) alerts.push(["bad", `La orden ${o.client_order_id} (${o.side} ${o.symbol}) se llenó en el exchange y el bot no la registró. Revísala a mano.`]);
    for (const [name, m] of Object.entries(status.modules || {})) if (m.error) alerts.push(["bad", `El módulo ${name} se detuvo: ${m.error}`]);
    for (const [name, m] of Object.entries(status.markets || {})) {
      const noKeys = (m.last_error || "").startsWith("sin llaves");
      if (m.simulating) alerts.push(["warn", `${m.name || name}: precios simulados (inventados). ${m.last_error || ""}`]);
      else if (!m.connected && !noKeys) alerts.push(["warn", `${m.name || name}: sin conexión. ${m.last_error || ""}`]);
    }
    const ul = $("#alerts");
    ul.replaceChildren(...(alerts.length ? alerts.map(([c, t]) => h("li", { class: c, text: t })) : [h("li", { text: "Todo en orden." })]));

    $("#connections").replaceChildren(...Object.entries(status.markets || {}).map(([name, m]) => {
      const noKeys = (m.last_error || "").startsWith("sin llaves");
      const cls = m.source === "real" ? "good" : m.simulating ? "warn" : noKeys ? "" : "bad";
      const detail = m.source === "real" ? (m.market_open ? "abierto" : "cerrado") : noKeys ? "sin llaves, apagado" : m.source;
      return h("span", { class: "chip", title: m.last_error || "" },
        h("span", { class: "status " + cls, text: m.name || name }), h("span", { class: "muted", text: detail }));
    }));
    $("#modules").replaceChildren(...Object.entries(status.modules || {}).map(([name, m]) =>
      h("span", { class: "chip" }, h("span", { class: "status " + (m.error ? "bad" : "good"), text: name }))));
    await loadEquity();
  }

  function tile(label, value, delta) {
    return h("div", { class: "tile" }, h("div", { class: "label", text: label }),
      h("div", { class: "value" }, value), delta ? h("div", { class: "delta", text: delta }) : null);
  }

  async function loadEquity() {
    if (!status || !status.running) return;
    const { snapshots } = await api("/api/snapshots?hours=" + hours);
    const values = snapshots.map((s) => [Date.parse(s.timestamp + "Z"), s.total_value_usd]);
    const render = () => Viz.line($("#equity-chart"), {
      series: [{ name: "Valor del portafolio", color: css("--series-1"), values, area: true }],
      yFormat: (v) => fmt.money(v, 0), preciseFormat: (v) => fmt.money(v, 2), height: 240, timeFormat: fmt.datetime,
      empty: "Todavía no hay fotos del portafolio: se toma una cada 5 minutos.", label: "Valor del portafolio en el tiempo",
    });
    render();
  }

  // ══ Estrategias ══

  let lab = null, fNivel = "all", fSem = "all", selectedKey = null;
  function segHandler(id, set) {
    $(id).addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      $(id).querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
      set(b.dataset.v); renderLab();
    });
  }
  segHandler("#f-nivel", (v) => (fNivel = v));
  segHandler("#f-sem", (v) => (fSem = v));

  document.querySelectorAll(".toggle-table").forEach((b) => b.addEventListener("click", () => {
    const id = b.dataset.for, tbl = $("#" + id + "-table"), chart = $("#" + id);
    const showTable = tbl.hidden;
    tbl.hidden = !showTable; chart.hidden = showTable;
    b.textContent = showTable ? "Ver como gráfica" : "Ver como tabla";
  }));

  const annual = (e) => (e.tipo === "dca" ? (e.extra || {}).irr : (e.metricas || {}).cagr);

  async function loadLab() {
    lab = await api("/api/lab");
    const tested = lab.estrategias.filter((e) => e.metricas);
    const counts = { verde: 0, amarillo: 0, rojo: 0 };
    tested.forEach((e) => (counts[e.semaforo] = (counts[e.semaforo] || 0) + 1));
    $("#lab-lede").textContent = `${tested.length} estrategias probadas con datos reales, comisiones y deslizamiento, con sus parámetros de libro. ` +
      `${counts.verde} pasan todas las pruebas, ${counts.amarillo} casi y ${counts.rojo} no sirven como están. Reporte del ${lab.generado}.`;
    renderLab();
    renderResearch();
    if (!selectedKey) {
      const best = tested.filter((e) => e.semaforo === "verde").sort((a, b) => (b.metricas.sharpe || 0) - (a.metricas.sharpe || 0))[0];
      if (best) selectStrategy(best.key, false);
    }
  }

  function filtered() {
    return lab.estrategias.filter((e) => e.metricas && (fNivel === "all" || String(e.nivel) === fNivel) && (fSem === "all" || e.semaforo === fSem));
  }

  function renderLab() {
    if (!lab) return;
    const rows = filtered();
    const cashRef = median(lab.estrategias.filter((e) => e.metricas).map((e) => e.metricas.cash_cagr));
    const points = rows.map((e) => ({
      key: e.key, x: Math.abs(e.metricas.max_drawdown), y: annual(e), label: e.nombre,
      shape: (SEM[e.semaforo] || SEM.error).shape, color: (SEM[e.semaforo] || SEM.error).color(),
      selected: e.key === selectedKey, priority: (e.semaforo === "verde" ? 2 : e.semaforo === "amarillo" ? 1 : 0) + (e.key === selectedKey ? 5 : 0),
      tip: [
        { name: "rendimiento anual", value: fmt.pct(annual(e)) },
        { name: "peor caída", value: fmt.pct(e.metricas.max_drawdown, 0) },
        { name: "Sharpe", value: fmt.num(e.metricas.sharpe) },
        { name: e.nivel_nombre.toLowerCase(), value: (SEM[e.semaforo] || SEM.error).label, color: (SEM[e.semaforo] || SEM.error).color(), shape: true },
      ],
    }));
    const renderMap = () => Viz.scatter($("#risk-map"), {
      points, height: 440,
      xFormat: (v) => fmt.pct(v, 0, false), yFormat: (v) => fmt.pct(v, 0),
      xLabel: "Peor caída de su historia: más segura", xLabelRight: "más arriesgada",
      reference: cashRef, referenceLabel: `Efectivo en dólares (T-bill), ~${fmt.pct(cashRef, 1, false)} al año`,
      legend: [
        { name: "Pasa las pruebas", color: SEM.verde.color() },
        { name: "Casi", color: SEM.amarillo.color() },
        { name: "No sirve como está", color: SEM.rojo.color() },
      ],
      onSelect: (p) => selectStrategy(p.key, true),
    });
    renderMap();
    chartRenders[0] = renderMap;

    const columns = [
      { key: "semaforo", label: "Resultado", render: (e) => statusEl(e.semaforo), sortValue: (e) => ({ verde: 3, amarillo: 2, rojo: 1 }[e.semaforo] || 0) },
      { key: "nombre", label: "Estrategia" },
      { key: "nivel", label: "Nivel", render: (e) => e.nivel_nombre },
      { key: "cagr", label: "Rend. anual", num: true, render: (e) => signed(annual(e)), sortValue: annual },
      { key: "dd", label: "Peor caída", num: true, render: (e) => fmt.pct(e.metricas.max_drawdown, 0), sortValue: (e) => e.metricas.max_drawdown },
      { key: "sharpe", label: "Sharpe", num: true, render: (e) => fmt.num(e.metricas.sharpe), sortValue: (e) => e.metricas.sharpe },
      { key: "oos", label: "Últimos años", num: true, render: (e) => signed(e.metricas_oos.cagr), sortValue: (e) => e.metricas_oos.cagr },
      { key: "bh", label: "Sólo comprar", num: true, render: (e) => fmt.pct((e.metricas.benchmark || {}).cagr), sortValue: (e) => (e.metricas.benchmark || {}).cagr },
      { key: "capital_min_usd", label: "Desde", num: true, render: (e) => fmt.money(e.capital_min_usd, 0) },
    ];
    Viz.table($("#lab-table"), columns, rows, {
      sortable: true, sortKey: "semaforo", sortDir: -1, onRow: (e) => selectStrategy(e.key, true),
      selected: (e) => e.key === selectedKey, empty: "Ninguna estrategia con este filtro",
    });
    Viz.table($("#risk-map-table"), columns.slice(0, 6), rows, { sortable: true });
  }

  function median(xs) {
    const s = xs.filter((x) => typeof x === "number").sort((a, b) => a - b);
    return s.length ? s[Math.floor(s.length / 2)] : 0;
  }

  function renderResearch() {
    const list = lab.estrategias.filter((e) => e.semaforo === "investigacion");
    $("#research").replaceChildren(...list.map((e) => h("div", { class: "card" },
      h("h4", { text: e.nombre }), h("p", { text: e.idea }),
      h("p", { class: "small", text: "Riesgo: " + e.riesgos }), h("p", { class: "small muted", text: e.datos }))));
  }

  async function selectStrategy(key, scroll) {
    selectedKey = key;
    renderLab();
    const e = await api("/api/lab/" + encodeURIComponent(key));
    renderDetail(e);
    if (scroll) $("#detail").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }

  function renderDetail(e) {
    const d = $("#detail");
    d.hidden = false;
    const m = e.metricas, isDca = e.tipo === "dca";
    const nombres = {
      gana_al_efectivo: "Le gana al efectivo", gana_fuera_de_muestra: "También en los últimos años",
      aguanta_doble_costo: "Aguanta el doble de comisiones", aguanta_ejecutar_tarde: "Aguanta operar un día tarde",
      parametros_robustos: "Funciona con parámetros vecinos", sin_riesgo_de_ruina: "Nunca cayó más de 85%",
      sin_sesgo_conocido: "Sin sesgo conocido en la prueba", suficientes_operaciones: "Al menos 30 operaciones",
    };
    const checks = Object.entries(e.pruebas || {}).map(([k, v]) =>
      h("li", { class: v ? "ok" : "no" }, h("span", { class: "mark", text: v ? "✓" : "✕", "aria-label": v ? "sí" : "no" }), nombres[k] || k));

    const stats = isDca ? [
      tile("Aportado", fmt.money(e.extra.invested, 0)), tile("Terminó con", fmt.money(e.extra.final_value, 0), fmt.mult(e.extra.final_multiple)),
      tile("Rendimiento anual del dinero (TIR)", signed(e.extra.irr)), tile("Peor caída de la cuenta", fmt.pct(m.max_drawdown, 0)),
    ] : [
      tile("Rendimiento anual", signed(m.cagr), "efectivo: " + fmt.pct(m.cash_cagr)),
      tile("Peor caída", fmt.pct(m.max_drawdown, 0), `${m.longest_drawdown_days} días bajo su máximo`),
      tile("Sharpe", fmt.num(m.sharpe), "más de 1 es muy bueno"),
      tile("Peor año", signed(m.worst_year, 0)),
      tile("Meses positivos", fmt.num(m.pct_positive_months * 100, 0) + "%"),
      tile("Desde " + (e.oos_desde || "").slice(0, 4), signed(e.metricas_oos.cagr), "parte que no se usó para diseñarla"),
    ];

    const growth = h("div", { class: "viz" }), dd = h("div", { class: "viz" }), years = h("div", { class: "viz" });
    let how;
    const perps = (e.extra || {}).perp_cols && e.extra.perp_cols.length;
    if (e.tipo === "weights" && !perps) {
      how = h("div", {}, h("p", { class: "small", text: "Para correrla en papel con el allocator, en el .env:" }),
        h("pre", { text: `ACTIVE_MODULES=allocator,collectors,dashboard\nALLOCATOR_STRATEGIES=${e.key}\nALLOCATOR_CAPITAL_USD=${Math.max(1000, e.capital_min_usd)}\nDRY_RUN=true` }));
    } else if (perps) {
      how = h("p", { class: "note", text: "Necesita perpetuos (cortos o apalancamiento). El allocator todavía sólo opera spot, así que por ahora se queda en el laboratorio." });
    } else if (e.tipo === "signals") {
      how = h("p", { class: "note", text: "Es del módulo scalper. Con estos resultados sólo tiene sentido en papel." });
    } else if (isDca) {
      how = h("p", { class: "note", text: "Es el módulo dca: DCA_ENABLED_ASSETS, DCA_AMOUNT_USD y DCA_FREQUENCY en el .env." });
    }

    const ts = (e.extra || {}).trade_stats;
    const tradeLine = ts ? (ts.trades
      ? h("p", { class: "small", text: `${ts.trades} operaciones, ${fmt.num(ts.win_rate * 100, 0)}% ganadoras. Promedio por operación: ${fmt.num(ts.expectancy_pct, 3)}% neto contra ${fmt.num(ts.gross_expectancy_pct, 3)}% antes de comisiones.` })
      : h("p", { class: "small", text: "No hizo ni una operación en todo el periodo." })) : null;

    fill(d,
      h("div", { class: "detail-head" }, h("h2", { text: e.nombre }), statusEl(e.semaforo), h("span", { class: "muted", text: e.nivel_nombre })),
      h("dl", { class: "facts" },
        h("dt", { text: "Cómo gana" }), h("dd", { text: e.idea }),
        h("dt", { text: "Riesgos" }), h("dd", { text: e.riesgos }),
        h("dt", { text: "Dónde se opera" }), h("dd", { text: e.donde }),
        h("dt", { text: "Desde" }), h("dd", { text: `${fmt.money(e.capital_min_usd, 0)}, revisión ${e.frecuencia.toLowerCase()}` }),
        h("dt", { text: "Probada con" }), h("dd", { text: `${e.datos}, de ${m.start} a ${m.end} (${fmt.num(m.years, 1)} años)` }),
        e.extra && e.extra.aviso ? h("dt", { text: "Ojo" }) : null, e.extra && e.extra.aviso ? h("dd", { text: e.extra.aviso }) : null,
      ),
      h("div", { class: "tiles", style: "margin-top:16px" }, ...stats),
      tradeLine,
      h("div", { class: "panel-head" }, h("h3", { text: isDca ? "Valor de la cuenta por dólar invertido" : "Crecimiento de 1 dólar" }),
        h("span", { class: "muted small", text: "escala logarítmica: la misma distancia es el mismo porcentaje" })),
      growth,
      h("div", { class: "grid-2", style: "margin-top:16px" },
        h("div", {}, h("h3", { text: "Caída desde su máximo" }), dd),
        h("div", {}, h("h3", { text: "Rendimiento por año" }), years)),
      h("h3", { style: "margin-top:16px", text: "Pruebas" }),
      checks.length ? h("ul", { class: "checks" }, ...checks) : h("p", { class: "muted", text: "Sin pruebas de robustez." }),
      robustness(e),
      how ? h("div", { style: "margin-top:16px" }, h("h3", { text: "Cómo probarla" }), how) : null,
    );

    const toPts = (arr) => (arr || []).map(([t, v]) => [Date.parse(t), v]);
    const renderGrowth = () => Viz.line(growth, {
      series: [
        { name: e.nombre, color: css("--series-1"), values: toPts(e.curva) },
        { name: e.benchmark_name || "Comprar y aguantar", color: css("--bench"), values: toPts(e.curva_benchmark) },
      ], log: true, yFormat: (v) => "$" + fmt.num(v, v >= 10 ? 0 : 2), height: 280, label: "Crecimiento de un dólar",
    });
    const renderDD = () => Viz.line(dd, {
      series: [{ name: "Caída desde el máximo", color: css("--neg"), values: toPts(e.drawdown), area: true }],
      yFormat: (v) => fmt.pct(v, 0), zero: true, height: 200, endLabels: false, label: "Caída desde el máximo",
    });
    const renderYears = () => Viz.columns(years, {
      items: Object.entries(m.yearly || {}).map(([y, v]) => ({ label: "’" + y.slice(2), value: v })),
      format: (v) => fmt.pct(v, 0), height: 200, seriesName: "en el año", label: "Rendimiento por año",
    });
    renderGrowth(); renderDD(); renderYears();
    chartRenders[1] = () => { renderGrowth(); renderDD(); renderYears(); };
  }

  function robustness(e) {
    const rows = [];
    if (e.costos_x2) rows.push({ prueba: "Con el doble de comisiones", cagr: e.costos_x2.cagr, sharpe: e.costos_x2.sharpe });
    if (e.ejecucion_tarde) rows.push({ prueba: "Operando un día tarde", cagr: e.ejecucion_tarde.cagr, sharpe: e.ejecucion_tarde.sharpe });
    for (const v of e.vecinos || []) rows.push({ prueba: `${v.param} = ${v.valor}`, cagr: v.cagr, sharpe: v.sharpe, dd: v.max_drawdown });
    if (!rows.length) return null;
    const box = h("div", { style: "margin-top:16px" }, h("h3", { text: "Qué pasa si cambian las condiciones" }));
    const t = h("div");
    Viz.table(t, [
      { key: "prueba", label: "Variante" },
      { key: "cagr", label: "Rend. anual", num: true, render: (r) => signed(r.cagr) },
      { key: "sharpe", label: "Sharpe", num: true, render: (r) => fmt.num(r.sharpe) },
      { key: "dd", label: "Peor caída", num: true, render: (r) => (r.dd === undefined ? "—" : fmt.pct(r.dd, 0)) },
    ], [{ prueba: "Original", cagr: annual(e), sharpe: e.metricas.sharpe, dd: e.metricas.max_drawdown }, ...rows]);
    box.appendChild(t);
    return box;
  }

  // ══ En vivo ══

  async function loadVivo() {
    let st;
    try { st = await api("/api/status"); } catch (e) { st = { running: false }; }
    $("#vivo-offline").hidden = !!st.running;
    $("#vivo-body").hidden = !st.running;
    if (!st.running) return;

    const [p, tr, od, sg] = await Promise.all([api("/api/portfolio"), api("/api/trades?limit=40"), api("/api/orders?limit=30"), api("/api/signals")]);
    let alloc = null;
    try { alloc = await api("/api/modules/allocator"); } catch (e) { /* allocator apagado */ }

    if (alloc && Object.keys(alloc.strategies || {}).length) {
      const rows = Object.entries(alloc.strategies).map(([k, s]) => ({ key: k, ...s }));
      Viz.table($("#sleeves"), [
        { key: "nombre", label: "Estrategia" },
        { key: "status", label: "Estado", render: (r) => h("span", { class: "status " + (r.error ? "bad" : r.status === "rebalanceada" || r.status === "al día" ? "good" : "warn"), text: r.error || r.status }) },
        { key: "capital", label: "Capital", num: true, render: (r) => fmt.money(r.capital, 0) },
        { key: "equity", label: "Vale hoy", num: true, render: (r) => (r.equity === undefined ? "—" : fmt.money(r.equity)) },
        { key: "pnl", label: "Resultado", num: true, render: (r) => (r.equity === undefined ? "—" : signed(r.equity / r.capital - 1, 2)) },
        { key: "target", label: "Quiere tener", render: (r) => Object.entries(r.target || {}).map(([s, w]) => `${s} ${fmt.pct(w, 0, false)}`).join(", ") || "todo en efectivo" },
        { key: "target_date", label: "Vigente desde", render: (r) => (r.target_date || "").slice(0, 10) },
      ], rows);
    } else {
      $("#sleeves").replaceChildren(h("p", { class: "muted", text: "El allocator no está corriendo. Agrégalo a ACTIVE_MODULES y elige estrategias en ALLOCATOR_STRATEGIES." }));
    }

    Viz.table($("#positions"), [
      { key: "module", label: "Módulo" }, { key: "symbol", label: "Activo" },
      { key: "side", label: "Lado", render: (r) => (r.side === "long" ? "Largo" : "Corto") },
      { key: "quantity", label: "Cantidad", num: true, render: (r) => fmt.num(r.quantity, 6) },
      { key: "entry_price", label: "Entrada", num: true, render: (r) => fmt.num(r.entry_price, 4) },
      { key: "current_price", label: "Ahora", num: true, render: (r) => fmt.num(r.current_price, 4) },
      { key: "unrealized_pnl", label: "P&L", num: true, render: (r) => moneySigned(r.unrealized_pnl) },
      { key: "stop_loss", label: "Stop", num: true, render: (r) => (r.stop_loss ? fmt.num(r.stop_loss, 4) : "—") },
    ], p.positions, { empty: "Sin posiciones abiertas" });

    Viz.table($("#trades"), [
      { key: "t", label: "Cuándo", render: (r) => fmt.datetime(Date.parse((r.closed_at || r.timestamp) + "Z")) },
      { key: "module", label: "Módulo" }, { key: "symbol", label: "Activo" },
      { key: "status", label: "Estado", render: (r) => (r.status === "closed" ? "Cerrada" : "Abierta") },
      { key: "pnl", label: "P&L", num: true, render: (r) => (r.pnl === null ? "—" : moneySigned(r.pnl)) },
    ], tr.trades, { empty: "Sin operaciones todavía" });

    Viz.table($("#orders"), [
      { key: "t", label: "Cuándo", render: (r) => fmt.datetime(Date.parse(r.created_at + "Z")) },
      { key: "module", label: "Módulo" }, { key: "side", label: "Lado", render: (r) => (r.side === "buy" ? "Compra" : "Venta") },
      { key: "symbol", label: "Activo" },
      { key: "status", label: "Estado", render: (r) => h("span", { class: "status " + (r.status === "filled" ? "good" : r.status === "failed" ? "bad" : "warn"), text: r.status, title: r.error || "" }) },
    ], od.orders, { empty: "Sin órdenes" });

    Viz.table($("#signals"), [
      { key: "t", label: "Hora", render: (r) => fmt.datetime(Date.parse(r.timestamp + "Z")) },
      { key: "symbol", label: "Activo" }, { key: "signal_type", label: "Señal" },
      { key: "direction", label: "Dirección", render: (r) => (r.direction === "bullish" ? "Alcista" : "Bajista") },
      { key: "price_at_signal", label: "Precio", num: true, render: (r) => fmt.num(r.price_at_signal, 4) },
      { key: "simulated", label: "Datos", render: (r) => (r.simulated ? "simulados" : "reales") },
    ], sg.signals, { empty: "Sin señales hoy" });
  }

  // ══ Mercados ══

  $("#refresh-markets").addEventListener("click", () => loadMarkets(true));

  async function loadMarkets(refresh) {
    const btn = $("#refresh-markets");
    btn.disabled = true; btn.textContent = "Actualizando…";
    try {
      const snap = await api("/api/collectors" + (refresh ? "?refresh=true" : ""));
      renderMarkets(snap);
    } finally {
      btn.disabled = false; btn.textContent = "Actualizar ahora";
    }
  }

  function renderMarkets(snap) {
    const f = snap.funding || {};
    $("#markets-ts").textContent = f.ts ? "Foto de " + fmt.datetime(Date.parse(f.ts)) : "";
    const venues = ["binance", "bybit", "hyperliquid"];
    const majors = Object.entries(f.mayores || {}).map(([coin, q]) => ({ coin, ...q }));
    Viz.table($("#majors"), [{ key: "coin", label: "Moneda" },
      ...venues.map((v) => ({ key: v, label: v[0].toUpperCase() + v.slice(1), num: true, render: (r) => signed(r[v]) })),
      { key: "dif", label: "Diferencia", num: true, render: (r) => { const vs = venues.map((v) => r[v]).filter((x) => typeof x === "number"); return vs.length > 1 ? fmt.pct(Math.max(...vs) - Math.min(...vs), 1, false) : "—"; } },
    ], majors);

    const b = snap.bitso || {};
    fill($("#bitso"),
      ...(b.pares || []).map((p) => tile(p.par.replace("_mxn", "").toUpperCase() + " en pesos", signed(p.prima_pct / 100, 2), "spread en Bitso " + fmt.num(p.spread_bitso_pct, 3) + "%")),
      b.usdt_mxn ? tile("USDT contra dólar (pesos)", signed((b.prima_usdt_vs_usd_pct || 0) / 100, 2), `USDT ${fmt.num(b.usdt_mxn, 3)}, USD ${fmt.num(b.usd_mxn, 3)}`) : null,
      b.error ? tile("Bitso", "sin datos", b.error) : null,
    );

    Viz.table($("#spreads"), [
      { key: "coin", label: "Moneda" },
      { key: "short_en", label: "Corto en" }, { key: "apr_alto", label: "Cobra", num: true, render: (r) => signed(r.apr_alto) },
      { key: "long_en", label: "Largo en" }, { key: "apr_bajo", label: "Paga", num: true, render: (r) => signed(r.apr_bajo) },
      { key: "diferencia_apr", label: "Diferencia anual", num: true, render: (r) => fmt.pct(r.diferencia_apr, 1, false) },
      { key: "volumen_min", label: "Volumen 24 h", num: true, render: (r) => "$" + fmt.compact(r.volumen_min) },
    ], (f.diferencias || []).slice(0, 15), { sortable: true, empty: "Sin datos de funding" });

    Viz.table($("#top-funding"), [
      { key: "coin", label: "Moneda" }, { key: "kind", label: "Tipo", render: (r) => kindName(r.kind) },
      { key: "apr", label: "Funding anual", num: true, render: (r) => signed(r.apr) },
      { key: "interval_h", label: "Cada", num: true, render: (r) => r.interval_h + " h" },
      { key: "volume_24h", label: "Volumen 24 h", num: true, render: (r) => "$" + fmt.compact(r.volume_24h) },
    ], (f.top_funding || []).slice(0, 12), { sortable: true });

    Viz.table($("#stock-perps"), [
      { key: "coin", label: "Activo" }, { key: "kind", label: "Tipo", render: (r) => kindName(r.kind) },
      { key: "mark", label: "Precio", num: true, render: (r) => fmt.num(r.mark, 2) },
      { key: "apr", label: "Funding anual", num: true, render: (r) => signed(r.apr) },
      { key: "basis_pct", label: "Contra índice", num: true, render: (r) => signed((r.basis_pct || 0) / 100, 2) },
      { key: "volume_24h", label: "Volumen 24 h", num: true, render: (r) => "$" + fmt.compact(r.volume_24h) },
    ], (f.perpetuos_no_cripto || []).slice(0, 20), { sortable: true });

    const pm = (snap.polymarket || {}).mercados || [];
    Viz.table($("#polymarket"), [
      { key: "pregunta", label: "Pregunta" },
      { key: "precios", label: "Precios", render: (r) => (r.resultados || []).map((o, i) => `${o} ${fmt.num((r.precios[i] || 0) * 100, 1)}¢`).join(" / ") },
      { key: "suma_precios", label: "Suma", num: true, render: (r) => (r.suma_precios ? fmt.num(r.suma_precios * 100, 1) + "¢" : "—") },
      { key: "volumen_24h", label: "Volumen 24 h", num: true, render: (r) => "$" + fmt.compact(r.volumen_24h) },
      { key: "cierra", label: "Cierra", render: (r) => (r.cierra || "").slice(0, 10) },
    ], pm.slice(0, 15), { empty: (snap.polymarket || {}).error || "Sin datos" });
  }
  const kindName = (k) => ({ COIN: "cripto", EQUITY: "acción EE.UU.", COMMODITY: "materia prima", INDEX: "índice", PREMARKET: "pre-mercado",
    KR_EQUITY: "acción Corea", HK_EQUITY: "acción Hong Kong", CN_EQUITY: "acción China", FX: "divisa" }[k] || (k || "—").toLowerCase());

  // ══ Dónde abrir cuenta ══

  async function loadVenues() {
    const box = $("#venues");
    let v;
    try { v = await api("/api/venues"); } catch (e) {
      box.replaceChildren(h("p", { class: "muted", text: "Todavía no está la comparación de plataformas." }));
      return;
    }
    const parts = [];
    if (v.intro) parts.push(h("p", { class: "lede", text: v.intro }));
    for (const c of v.paises || []) {
      const panel = h("div", { class: "panel" }, h("h3", { text: c.nombre }));
      if (c.recomendacion) panel.appendChild(h("p", { text: c.recomendacion }));
      if (c.pasos && c.pasos.length) panel.appendChild(h("ol", {}, ...c.pasos.map((p) => h("li", { text: p }))));
      const t = h("div");
      Viz.table(t, [
        { key: "plataforma", label: "Plataforma" }, { key: "para", label: "Para qué" },
        { key: "deposito", label: "Cómo se deposita" }, { key: "minimo", label: "Mínimo" },
        { key: "comisiones", label: "Comisiones" }, { key: "api", label: "API para el bot" },
        { key: "notas", label: "Notas" },
      ], c.plataformas || []);
      panel.appendChild(t);
      if (c.impuestos) panel.appendChild(h("p", { class: "note", style: "margin-top:12px", text: c.impuestos }));
      parts.push(panel);
    }
    if (v.tasas) parts.push(h("div", { class: "panel" }, h("h3", { text: "Lo que paga no hacer nada (la vara a superar)" }), h("p", { text: v.tasas })));
    if (v.sin_confirmar && v.sin_confirmar.length) parts.push(h("div", { class: "panel" }, h("h3", { text: "Sin confirmar" }), h("ul", {}, ...v.sin_confirmar.map((s) => h("li", { text: s })))));
    if (v.fuentes && v.fuentes.length) parts.push(h("details", { class: "panel" }, h("summary", { text: `Fuentes (${v.fuentes.length})` }),
      h("ul", { class: "small" }, ...v.fuentes.map((u) => h("li", {}, h("a", { href: u, rel: "noreferrer", target: "_blank", text: u }))))));
    box.replaceChildren(...parts);
  }

  // ── Arranque y refresco ──
  route();
  setInterval(() => {
    if (document.hidden) return;
    const v = location.hash.replace("#", "") || "resumen";
    if (v === "resumen" || v === "vivo") load(v);
  }, 15000);
})();
