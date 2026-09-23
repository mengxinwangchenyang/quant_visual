"use strict";

window.SellPeriodCharts = (() => {
  const snapshots = new Map();
  let chartInstances = [];
  let visibilityObserver = null;

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
  const money = (value) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(Number(value || 0));
  const feeMoney = (value) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", minimumFractionDigits: 4, maximumFractionDigits: 4 }).format(Number(value || 0));
  const price = (value) => Number(value || 0) > 0 ? Number(value).toFixed(2) : "-";
  const percent = (value) => `${Number(value || 0) >= 0 ? "+" : ""}${(Number(value || 0) * 100).toFixed(2)}%`;
  const displayDate = (value) => /^\d{8}$/.test(value || "") ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}` : value || "-";
  const shortDisplayDate = (value) => /^\d{8}$/.test(value || "") ? `${Number(value.slice(4, 6))}月${Number(value.slice(6, 8))}日` : value || "-";
  const pnlClass = (value) => Number(value) > 0 ? "pnl-positive" : Number(value) < 0 ? "pnl-negative" : "pnl-flat";
  const boardBadge = (code) => { const board = window.SellStatistics?.boardOf(code) || ""; return board ? `<span class="board-tag ${board}">${window.SellStatistics.boardLabel(code)}</span>` : ""; };

  function destroy() {
    visibilityObserver?.disconnect();
    visibilityObserver = null;
    chartInstances.forEach(({ chart, observer, separators }) => { observer?.disconnect(); separators?.destroy(); chart?.remove(); });
    chartInstances = [];
  }

  function formatShanghaiTime(timestamp) {
    return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(Number(timestamp) * 1000)).replace("/", "-");
  }

  async function loadSnapshot(path) {
    if (!snapshots.has(path)) snapshots.set(path, fetch(`${path}?t=${Date.now()}`, { cache: "no-store" }).then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    }));
    return snapshots.get(path);
  }

  function addPriceLine(series, value, title, color) {
    if (Number(value || 0) <= 0) return;
    series.createPriceLine({ price: Number(value), color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title });
  }

  function minuteOfDay(value) {
    const [hour = 0, minute = 0] = String(value || "").split(":").map(Number);
    return hour * 60 + minute;
  }

  function findEventPoint(points, date, time) {
    const dayPoints = points.filter((row) => row.date === String(date || ""));
    if (!dayPoints.length) return null;
    const targetMinute = minuteOfDay(time);
    return dayPoints.reduce((nearest, row) => {
      const local = new Date(Number(row.time) * 1000);
      const rowMinute = Number(new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false }).format(local).replace(":", ""));
      const normalizedMinute = Math.floor(rowMinute / 100) * 60 + rowMinute % 100;
      return !nearest || Math.abs(normalizedMinute - targetMinute) < nearest.distance ? { row, distance: Math.abs(normalizedMinute - targetMinute) } : nearest;
    }, null)?.row || null;
  }

  function addEventSegment(chart, library, points, event, title, color, shape, position) {
    const value = Number(event.price || 0);
    const eventPoint = findEventPoint(points, event.date, event.time);
    if (value <= 0 || !eventPoint) return null;
    const dayStart = points.find((row) => row.date === eventPoint.date) || eventPoint;
    const segment = chart.addSeries(library.LineSeries, { color, lineWidth: 2, lineStyle: 2, pointMarkersVisible: false, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
    segment.setData([{ time: dayStart.time, value }, { time: eventPoint.time, value }]);
    return { time: eventPoint.time, position, shape, color, price: value, text: `${title} ${String(event.time || "").slice(0, 5)} ${price(value)}` };
  }

  function createChart(container, points, levels) {
    const library = window.LightweightCharts;
    if (!library) throw new Error("图表组件未载入");
    const chart = library.createChart(container, {
      width: Math.max(320, container.clientWidth), height: container.clientHeight,
      layout: { background: { type: library.ColorType.Solid, color: "#fff" }, textColor: "#66716d", fontFamily: '"Segoe UI", "Microsoft YaHei UI", sans-serif', fontSize: 11 },
      grid: { vertLines: { color: "#edf1ef" }, horzLines: { color: "#edf1ef" } }, rightPriceScale: { borderColor: "#dce2df" },
      timeScale: { borderColor: "#dce2df", timeVisible: true, secondsVisible: false, tickMarkFormatter: formatShanghaiTime }, localization: { timeFormatter: formatShanghaiTime }
    });
    const candles = chart.addSeries(library.CandlestickSeries, { upColor: "#d84a55", downColor: "#16866a", borderVisible: false, wickUpColor: "#d84a55", wickDownColor: "#16866a", priceLineVisible: false });
    const volume = chart.addSeries(library.HistogramSeries, { priceScaleId: "volume", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
    candles.priceScale().applyOptions({ scaleMargins: { top: .07, bottom: .28 } });
    volume.priceScale().applyOptions({ scaleMargins: { top: .8, bottom: 0 } });
    candles.setData(points.map((row) => ({ time: row.time, open: row.open, high: row.high, low: row.low, close: row.close })));
    volume.setData(points.map((row) => ({ time: row.time, value: row.volume, color: row.close >= row.open ? "rgba(216,74,85,.58)" : "rgba(22,134,106,.58)" })));
    const markers = [
      addEventSegment(chart, library, points, levels.buy, "买入点", "#3b6ea8", "arrowUp", "atPriceTop"),
      addEventSegment(chart, library, points, levels.sell, "卖出点", "#087a5b", "arrowDown", "atPriceBottom")
    ].filter(Boolean);
    if (markers.length) library.createSeriesMarkers(candles, markers);
    addPriceLine(candles, levels.target, "+9%目标", "#9a5b05");
    chart.timeScale().fitContent();
    const separators = window.ChartDaySeparators?.attach(container, chart, points);
    const observer = new ResizeObserver((entries) => { const width = Math.floor(entries[0]?.contentRect.width || 0); if (width > 0) chart.applyOptions({ width }); });
    observer.observe(container);
    chartInstances.push({ chart, observer, separators });
  }

  async function mount(details) {
    if (!details || details.dataset.rendered === "true" || details.dataset.loading === "true" || !details.open) return;
    details.dataset.loading = "true";
    const container = details.querySelector(".chart-container");
    const days = details.querySelector(".chart-days");
    try {
      const snapshot = await loadSnapshot(details.dataset.chartPath);
      const data = snapshot.charts?.[details.dataset.code];
      days.textContent = (data?.trading_days || snapshot.trading_days || []).map(displayDate).join(" / ");
      if (!data?.points?.length) { container.innerHTML = `<div class="chart-message">${escapeHtml(data?.message || "没有分钟行情")}</div>`; return; }
      container.innerHTML = "";
      createChart(container, data.points, {
        buy: { price: details.dataset.buy, date: details.dataset.buyDate, time: details.dataset.buyTime },
        target: details.dataset.target,
        sell: { price: details.dataset.sell, date: details.dataset.sellDate, time: details.dataset.sellTime }
      });
      details.dataset.rendered = "true";
    } catch (error) {
      days.textContent = "分钟行情不可用";
      container.innerHTML = `<div class="chart-message">${escapeHtml(error.message)}</div>`;
    } finally {
      delete details.dataset.loading;
    }
  }

  function schedule(details) {
    if (!details?.open || details.dataset.rendered === "true") return;
    if (!("IntersectionObserver" in window)) { mount(details); return; }
    if (!visibilityObserver) {
      visibilityObserver = new IntersectionObserver((entries) => entries.forEach((entry) => {
        if (entry.isIntersecting && entry.target.open) { visibilityObserver.unobserve(entry.target); mount(entry.target); }
      }), { rootMargin: "700px 0px" });
    }
    visibilityObserver.observe(details);
  }

  function recordMarkup(row, open, options) {
    const recordAction = options.showBuyDayLink === false
      ? `<span class="chart-expand-label">${open ? "折叠图表" : "展开图表"}</span>`
      : `<a class="buy-day-link" href="./daily_buys.html?date=${encodeURIComponent(row.buy_date || "")}&strategy=${encodeURIComponent(row.strategy_id || "all")}">查看${shortDisplayDate(row.buy_date)}买入</a>`;
    return `<details class="candidate-item sell-record period-chart-record" data-code="${escapeHtml(row.code)}" data-chart-path="${escapeHtml(row.chart_path)}" data-buy="${Number(row.buy_price || 0)}" data-buy-date="${escapeHtml(row.buy_date || "")}" data-buy-time="${escapeHtml(row.buy_time || "14:53:00")}" data-target="${Number(row.target_price || 0)}" data-sell="${Number(row.sell_price || 0)}" data-sell-date="${escapeHtml(row.sell_date || row.date || "")}" data-sell-time="${escapeHtml(row.sell_time || "")}" ${open ? "open" : ""}>
      <summary><span class="expand-symbol" aria-hidden="true">+</span><span class="sell-type ${escapeHtml(row.data_type)}">${escapeHtml(row.data_type_label || (row.data_type === "estimated" ? "预估卖出" : "真实卖出"))}</span>
        <span class="candidate-strategy">${escapeHtml(row.strategy_name || row.strategy_id)}</span><span class="candidate-stock"><strong>${escapeHtml(row.code)}</strong><span>${escapeHtml(row.name || "")}</span>${boardBadge(row.code)}</span>
        <span class="sell-status"><span class="reason-badge ${row.data_type === "estimated" ? "warning" : "good"}">${escapeHtml(row.reason_label || "卖出")}</span><span class="reason-source">${displayDate(row.buy_date)} 买入 · ${displayDate(row.sell_date || row.date)} 卖出</span></span>
        <span class="sell-quote"><strong>卖出价 ${price(row.sell_price)}</strong><small>买入 ${price(row.buy_price)} · 目标 ${price(row.target_price)}</small></span>
        <span class="sell-pnl ${pnlClass(row.pnl)}">${money(row.pnl)}<br><small>${percent(row.return_rate)} · 手续费 ${money(row.fees)}</small></span>
        ${recordAction}</summary>
      <div class="chart-panel"><div class="chart-meta"><span class="chart-days">分钟行情加载中</span>${boardBadge(row.code)}<span>${displayDate(row.buy_date)} 14:53买入 · ${displayDate(row.sell_date || row.date)} ${escapeHtml(row.sell_time || "")}卖出</span></div>
        <div class="chart-container"><div class="chart-loading">展开后加载分钟行情</div></div>
        <div class="sell-chart-legend"><span class="legend-line" style="--legend-color:#3b6ea8">买入点（14:53）</span><span class="legend-line" style="--legend-color:#9a5b05">+9%目标价</span><span class="legend-line" style="--legend-color:#087a5b">卖出点（卖出时刻）</span></div>
        <div class="fee-breakdown"><div class="fee-breakdown-heading"><strong>手续费明细</strong><span>${escapeHtml(row.fee_source_label || "")}</span></div><dl>${(row.fee_details || []).map((item) => `<div><dt>${escapeHtml(item.label)}</dt><dd>${feeMoney(item.amount)}</dd></div>`).join("")}</dl><div class="fee-total"><span>总手续费</span><strong>${money(row.fees)}</strong></div></div>
      </div></details>`;
  }

  function render(container, rows, toggleButton, options = {}) {
    destroy();
    const openAll = Boolean(options.openAll);
    const openFirst = options.openFirst !== false;
    container.innerHTML = rows.map((row, index) => recordMarkup(row, openAll || (openFirst && index === 0), options)).join("");
    toggleButton.hidden = rows.length === 0;
    const updateToggle = () => {
      const items = [...container.querySelectorAll("details.period-chart-record")];
      const allOpen = items.length > 0 && items.every((item) => item.open);
      toggleButton.textContent = allOpen ? "全部折叠" : "全部展开";
      toggleButton.setAttribute("aria-expanded", String(allOpen));
    };
    container.querySelectorAll("details.period-chart-record").forEach((details) => {
      details.addEventListener("toggle", () => {
        const label = details.querySelector(".chart-expand-label");
        if (label) label.textContent = details.open ? "折叠图表" : "展开图表";
        if (details.open) schedule(details); else visibilityObserver?.unobserve(details);
        updateToggle();
      });
      if (details.open) schedule(details);
    });
    toggleButton.onclick = () => {
      const items = [...container.querySelectorAll("details.period-chart-record")];
      const shouldOpen = items.some((item) => !item.open);
      items.forEach((item) => { item.open = shouldOpen; if (shouldOpen) schedule(item); });
      updateToggle();
    };
    updateToggle();
  }

  return { render, destroy };
})();
