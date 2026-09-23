"use strict";

const state = { snapshot: null, activeDate: "", activeStrategy: "all", chartSnapshots: new Map(), chartInstances: [] };
const strategyLabels = { all: "全部", s1: "策略1", s2: "策略2", s3: "策略3", s4: "策略4", s5: "策略5", s6: "策略6", s7: "策略7", s8: "策略8", s9: "策略9", s10: "策略10", s11: "策略11", s12: "策略12", s13: "策略13" };
const strategyDescriptions = window.StrategyDescriptions || {};
const boardBadge = (code) => { const board = window.SellStatistics?.boardOf(code) || ""; return board ? `<span class="board-tag ${board}">${window.SellStatistics.boardLabel(code)}</span>` : ""; };
const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
const integer = (value) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Number(value || 0));
const money = (value) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(Number(value || 0));
const feeMoney = (value) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(Number(value || 0));
const price = (value) => Number(value || 0) > 0 ? Number(value).toFixed(2) : "-";
const percent = (value) => `${Number(value || 0) > 0 ? "+" : ""}${(Number(value || 0) * 100).toFixed(2)}%`;
const displayDate = (value) => /^\d{8}$/.test(value || "") ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}` : value || "-";
const shortDisplayDate = (value) => /^\d{8}$/.test(value || "") ? `${Number(value.slice(4, 6))}月${Number(value.slice(6, 8))}日` : value || "买入日";

function readUrlView() {
  const params = new URLSearchParams(window.location.search);
  const strategy = params.get("strategy") || "all";
  return {
    date: params.get("date") || "",
    strategy: Object.hasOwn(strategyLabels, strategy) ? strategy : "all"
  };
}

function syncUrlView(mode = "replace") {
  if (!state.activeDate) return;
  const url = new URL(window.location.href);
  url.searchParams.set("date", state.activeDate);
  url.searchParams.set("strategy", state.activeStrategy);
  const next = `${url.pathname}${url.search}${url.hash}`;
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next === current) return;
  const method = mode === "push" ? "pushState" : "replaceState";
  window.history[method]({ date: state.activeDate, strategy: state.activeStrategy }, "", next);
}

const initialUrlView = readUrlView();
state.activeDate = initialUrlView.date;
state.activeStrategy = initialUrlView.strategy;

function pnlMarkup(value) {
  const number = Number(value || 0);
  const tone = number > 0.005 ? "pnl-positive" : number < -0.005 ? "pnl-negative" : "pnl-flat";
  return `<span class="${tone}">${money(number)}</span>`;
}

function pnlWithRate(value, rate, fees, colored = true) {
  const feeText = Number(fees || 0) > 0 ? ` · 手续费 ${money(fees)}` : "";
  const amount = colored ? pnlMarkup(value) : money(value);
  return `<span class="pnl-stack">${amount}<small>${percent(rate)}${feeText}</small></span>`;
}

function activeDay() {
  return (state.snapshot?.days || []).find((day) => day.date === state.activeDate) || state.snapshot?.days?.[0];
}

function renderDateSelect() {
  byId("dateSelect").innerHTML = state.snapshot.days.map((day) => `<option value="${day.date}">${displayDate(day.date)}</option>`).join("");
  byId("dateSelect").value = state.activeDate;
}

function renderTabs() {
  byId("strategyTabs").innerHTML = Object.entries(strategyLabels).map(([id, label]) => `<button type="button" role="tab" data-strategy="${id}" aria-selected="${state.activeStrategy === id}">${label}</button>`).join("");
}

function renderSummary(day) {
  byId("tradeDate").textContent = displayDate(day.date);
  byId("completionState").textContent = day.estimated_sold_count && !day.actual_order_count ? "仅含预估卖出，未提交真实委托" : "真实卖出与预估卖出分开统计";
  byId("recordCount").textContent = integer(day.record_count);
  byId("uniqueStockCount").textContent = integer(day.unique_stock_count);
  byId("actualOrderCount").textContent = integer(day.actual_order_count);
  byId("actualFilledCount").textContent = integer(day.actual_filled_count);
  byId("estimatedSoldCount").textContent = integer(day.estimated_sold_count);
  byId("sellAmount").textContent = money(day.sell_amount);
  byId("totalPnl").innerHTML = pnlWithRate(day.pnl, day.return_rate, day.fees);
  const feeModel = state.snapshot.fee_model || {};
  byId("feeModelLabel").innerHTML = `<span>${escapeHtml(feeModel.label || "手续费已计入")}</span><span>收益率=含费盈亏÷买入成本</span>`;
}

function renderStrategies(day) {
  const combined = { id: "all", name: "综合", record_count: day.record_count, actual_order_count: day.actual_order_count, actual_filled_count: day.actual_filled_count,
    estimated_sold_count: day.estimated_sold_count, sell_amount: day.sell_amount, fees: day.fees, pnl: day.pnl, return_rate: day.return_rate };
  const rows = state.activeStrategy === "all" ? [combined, ...day.strategies] : day.strategies.filter((row) => row.id === state.activeStrategy);
  byId("strategyRows").innerHTML = rows.map((row) => {
    const result = row.record_count ? `${row.actual_filled_count ? `真实成交 ${row.actual_filled_count}` : ""}${row.actual_filled_count && row.estimated_sold_count ? " · " : ""}${row.estimated_sold_count ? `预估卖出 ${row.estimated_sold_count}` : ""}` : "无卖出记录";
    const performanceRows = (day.records || []).filter((record) => row.id === "all" || record.strategy_id === row.id);
    return `<tr class="${row.id === "all" ? "comprehensive" : ""}">
      <td><div class="strategy-cell"><strong>${escapeHtml(row.name)}</strong><span>${row.id === "all" ? "全部策略" : row.id.toUpperCase()}</span></div></td>
      <td class="signal-cell">${escapeHtml(row.id === "all" ? "全部策略" : strategyDescriptions[row.id] || "-")}</td>
      <td class="number">${integer(row.record_count)}</td><td class="number">${integer(row.actual_order_count)}</td><td class="number">${integer(row.actual_filled_count)}</td><td class="number">${integer(row.estimated_sold_count)}</td>
      <td class="number">${money(row.sell_amount)}</td><td class="number">${pnlWithRate(row.pnl, row.return_rate, row.fees, false)}</td>${SellStatistics.cells(SellStatistics.summarize(performanceRows))}<td><span class="result-muted">${escapeHtml(result)}</span></td>
    </tr>`;
  }).join("");
}

function destroyCharts() {
  state.chartInstances.forEach(({ chart, observer, separators }) => { observer?.disconnect(); separators?.destroy(); chart?.remove(); });
  state.chartInstances = [];
}

function detailItems() {
  return Array.from(byId("sellRecordList").querySelectorAll("details.sell-record"));
}

function syncToggle() {
  const items = detailItems();
  const allOpen = items.length > 0 && items.every((item) => item.open);
  byId("toggleAll").hidden = !items.length;
  byId("toggleAll").textContent = allOpen ? "全部折叠" : "全部展开";
  byId("toggleAll").setAttribute("aria-expanded", String(allOpen));
}

function renderDetails(day) {
  destroyCharts();
  const rows = day.records.filter((row) => state.activeStrategy === "all" || row.strategy_id === state.activeStrategy);
  byId("recordCountLabel").textContent = `${integer(rows.length)} 条卖出记录`;
  byId("emptyState").hidden = rows.length > 0;
  byId("sellRecordList").hidden = rows.length === 0;
  byId("detailCaption").textContent = day.estimated_sold_count ? "真实与预估记录明确区分 · 行情从买入日前3个交易日展示到卖出点" : "行情从买入日前3个交易日展示到卖出点";
  byId("sellRecordList").innerHTML = rows.map((row, index) => `
    <details class="candidate-item sell-record" data-code="${escapeHtml(row.code)}" data-chart-path="${escapeHtml(day.chart_path)}" data-buy="${Number(row.buy_price || 0)}" data-buy-date="${escapeHtml(row.buy_date || "")}" data-buy-time="${escapeHtml(row.buy_time || "14:53:00")}" data-target="${Number(row.target_price || 0)}" data-sell="${Number(row.sell_price || 0)}" data-sell-date="${escapeHtml(row.sell_date || "")}" data-sell-time="${escapeHtml(row.sell_time || "")}" ${index === 0 ? "open" : ""}>
      <summary>
        <span class="expand-symbol" aria-hidden="true">+</span>
        <span class="sell-type ${row.data_type}">${escapeHtml(row.data_type_label)}</span>
        <span class="candidate-strategy">${escapeHtml(strategyLabels[row.strategy_id] || row.strategy_name)}</span>
        <span class="candidate-stock"><strong>${escapeHtml(row.code)}</strong><span>${escapeHtml(row.name || "")}</span>${boardBadge(row.code)}</span>
        <span class="sell-status"><span class="reason-badge ${row.data_type === "estimated" ? "warning" : "good"}">${escapeHtml(row.reason_label)}</span><span class="reason-source">${escapeHtml(row.status_label)}</span></span>
        <span class="sell-quote"><strong>${row.data_type === "estimated" ? "预估卖出价" : "成交/委托价"} ${price(row.sell_price)}</strong><small>买入点 ${price(row.buy_price)} · 目标 ${price(row.target_price)}</small></span>
        <span class="sell-pnl">${pnlMarkup(row.pnl)}<br><small>${percent(row.return_rate)} · ${escapeHtml(row.fee_source_label || "含手续费")} ${money(row.fees)}</small></span>
        ${row.buy_date ? `<a class="buy-day-link" href="./daily_buys.html?date=${encodeURIComponent(row.buy_date)}&strategy=${encodeURIComponent(row.strategy_id)}" title="查看${escapeHtml(strategyLabels[row.strategy_id] || row.strategy_name)}在${displayDate(row.buy_date)}的每日买入页面">查看${shortDisplayDate(row.buy_date)}买入</a>` : ""}
      </summary>
      <div class="chart-panel">
        <div class="chart-meta"><span class="chart-days">分钟行情加载中</span>${boardBadge(row.code)}<span>${displayDate(row.buy_date)}买入 · ${displayDate(row.sell_date)} ${escapeHtml(row.sell_time)}</span></div>
        <div class="chart-container"><div class="chart-loading">正在读取分钟行情</div></div>
        <div class="sell-chart-legend"><span class="legend-line" style="--legend-color:#3b6ea8">买入点（14:53）</span><span class="legend-line" style="--legend-color:#9a5b05">目标价</span><span class="legend-line" style="--legend-color:#087a5b">卖出点（卖出时刻）</span></div>
        <div class="fee-breakdown">
          <div class="fee-breakdown-heading"><strong>手续费明细</strong><span>${escapeHtml(row.fee_source_label || "")}</span></div>
          <dl>${(row.fee_details || []).map((item) => `<div><dt>${escapeHtml(item.label)}</dt><dd>${feeMoney(item.amount)}</dd></div>`).join("")}</dl>
          <div class="fee-total"><span>总手续费</span><strong>${money(row.fees)}</strong></div>
        </div>
      </div>
    </details>`).join("");
  syncToggle();
  const first = byId("sellRecordList").querySelector("details[open]");
  if (first) mountChart(first);
}

function formatShanghaiTime(timestamp) {
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(Number(timestamp) * 1000)).replace("/", "-");
}

async function loadChartSnapshot(path) {
  if (!state.chartSnapshots.has(path)) state.chartSnapshots.set(path, fetch(`${path}?t=${Date.now()}`, { cache: "no-store" }).then((response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }));
  return state.chartSnapshots.get(path);
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
    return !nearest || Math.abs(normalizedMinute - targetMinute) < nearest.distance
      ? { row, distance: Math.abs(normalizedMinute - targetMinute) }
      : nearest;
  }, null)?.row || null;
}

function addEventSegment(chart, library, points, event, title, color, shape, position) {
  const value = Number(event.price || 0);
  const eventPoint = findEventPoint(points, event.date, event.time);
  if (value <= 0 || !eventPoint) return null;
  const dayStart = points.find((row) => row.date === eventPoint.date) || eventPoint;
  const segment = chart.addSeries(library.LineSeries, {
    color, lineWidth: 2, lineStyle: 2, pointMarkersVisible: false,
    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false
  });
  segment.setData([
    { time: dayStart.time, value },
    { time: eventPoint.time, value }
  ]);
  return { time: eventPoint.time, position, shape, color, price: value, text: `${title} ${String(event.time || "").slice(0, 5)} ${price(value)}` };
}

function createChart(container, points, levels) {
  const library = window.LightweightCharts;
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
  addPriceLine(candles, levels.target, "目标价", "#9a5b05");
  chart.timeScale().fitContent();
  const separators = window.ChartDaySeparators?.attach(container, chart, points);
  const observer = new ResizeObserver((entries) => { const width = Math.floor(entries[0]?.contentRect.width || 0); if (width > 0) chart.applyOptions({ width }); });
  observer.observe(container); state.chartInstances.push({ chart, observer, separators });
}

async function mountChart(details) {
  if (!details || details.dataset.rendered === "true" || details.dataset.loading === "true") return;
  details.dataset.loading = "true";
  const container = details.querySelector(".chart-container");
  const days = details.querySelector(".chart-days");
  try {
    const snapshot = await loadChartSnapshot(details.dataset.chartPath);
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
  } catch (error) { days.textContent = "分钟行情不可用"; container.innerHTML = `<div class="chart-message">${escapeHtml(error.message)}</div>`; }
  finally { delete details.dataset.loading; }
}

function render() {
  const day = activeDay();
  renderDateSelect(); renderTabs(); renderSummary(day); renderStrategies(day); renderDetails(day);
  const warning = state.snapshot.warning || "";
  byId("stateWarning").hidden = !warning; byId("stateWarning").textContent = warning;
  byId("generatedAt").textContent = `快照 ${state.snapshot.generated_at}`;
  byId("scheduleLabel").textContent = `自动更新 ${state.snapshot.schedule}`;
  byId("syncBadge").textContent = day.estimated_sold_count && !day.actual_order_count ? "预估卖出" : "卖出快照";
  byId("loadingState").hidden = true; byId("errorState").hidden = true; byId("dashboard").hidden = false;
}

async function loadSnapshot(initial = false) {
  try {
    const response = await fetch(`./daily_sells_snapshot.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const snapshot = await response.json(); const changed = snapshot.generated_at !== state.snapshot?.generated_at; state.snapshot = snapshot;
    if (!state.activeDate || !snapshot.days.some((day) => day.date === state.activeDate)) state.activeDate = snapshot.latest_date;
    if (initial || changed) {
      render();
      syncUrlView("replace");
    }
  } catch (error) { if (!state.snapshot) { byId("loadingState").hidden = true; byId("errorState").hidden = false; byId("errorMessage").textContent = `无法读取 daily_sells_snapshot.json：${error.message}`; } }
}

byId("dateSelect").addEventListener("change", (event) => {
  state.activeDate = event.target.value;
  render();
  syncUrlView("push");
});
byId("strategyTabs").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-strategy]");
  if (!button) return;
  state.activeStrategy = button.dataset.strategy;
  render();
  syncUrlView("push");
});
byId("sellRecordList").addEventListener("toggle", (event) => { const details = event.target.closest("details.sell-record"); if (details?.open) mountChart(details); syncToggle(); }, true);
byId("sellRecordList").addEventListener("click", (event) => {
  if (event.target.closest("a.buy-day-link")) event.stopPropagation();
});
byId("toggleAll").addEventListener("click", () => { const items = detailItems(); const open = items.some((item) => !item.open); items.forEach((item) => { item.open = open; if (open) mountChart(item); }); syncToggle(); });
window.addEventListener("popstate", () => {
  if (!state.snapshot) return;
  const requested = readUrlView();
  state.activeStrategy = requested.strategy;
  state.activeDate = state.snapshot.days.some((day) => day.date === requested.date)
    ? requested.date
    : state.snapshot.latest_date;
  render();
  syncUrlView("replace");
});
loadSnapshot(true); window.setInterval(() => loadSnapshot(false), 60000);
