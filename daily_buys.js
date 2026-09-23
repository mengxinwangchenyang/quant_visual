"use strict";

const state = {
  snapshot: null,
  activeDate: "",
  activeStrategy: "all",
  chartSnapshots: new Map(),
  chartInstances: [],
  candidateChartObserver: null
};
const strategyLabels = { all: "全部", s1: "策略1", s2: "策略2", s3: "策略3", s4: "策略4", s5: "策略5", s6: "策略6", s7: "策略7", s8: "策略8", s9: "策略9", s10: "策略10", s11: "策略11", s12: "策略12", s13: "策略13" };
const MIN_REAL_CHART_POINTS = 60;
const statusLabels = {
  buy_submitted: "已委托", buy_partial: "部分成交", open: "已成交", buy_unfilled: "未成交",
  preview: "预览", sell_submitted: "已进入卖出", sell_partial: "卖出中", closed: "已卖出"
};

const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
const integer = (value) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Number(value || 0));
const money = (value) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(Number(value || 0));
const hasValue = (value) => value !== null && value !== undefined && value !== "";
const moneyOrDash = (value) => hasValue(value) ? money(value) : "-";
const price = (value) => Number(value || 0) > 0 ? Number(value).toFixed(2) : "-";
const displayDate = (value) => /^\d{8}$/.test(value || "") ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}` : value || "-";

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

function activeDay() {
  return (state.snapshot?.days || []).find((day) => day.date === state.activeDate) || state.snapshot?.days?.[0] || null;
}

function statusClass(status) {
  if (["open", "closed", "sell_submitted", "sell_partial"].includes(status)) return "filled";
  if (["buy_submitted", "buy_partial"].includes(status)) return "pending";
  if (["buy_unfilled"].includes(status)) return "failed";
  return "";
}

function renderDateSelect() {
  const select = byId("dateSelect");
  select.innerHTML = (state.snapshot.days || []).map((day) => `<option value="${escapeHtml(day.date)}">${displayDate(day.date)}</option>`).join("");
  select.value = state.activeDate;
}

function renderTabs() {
  byId("strategyTabs").innerHTML = Object.entries(strategyLabels).map(([id, label]) =>
    `<button type="button" role="tab" data-strategy="${id}" aria-selected="${state.activeStrategy === id}">${label}</button>`
  ).join("");
}

function renderSummary(day) {
  byId("tradeDate").textContent = displayDate(day.date);
  const strategyCount = day.strategies.length;
  byId("completionState").textContent = day.estimated_strategy_count
    ? `预测故障 · ${day.estimated_strategy_count} 个策略历史估算`
    : day.completed_strategy_count === strategyCount ? `${strategyCount} 个策略已执行` : `${day.completed_strategy_count}/${strategyCount} 个策略已执行`;
  byId("candidateCount").textContent = integer(day.candidate_count);
  byId("acceptedCount").textContent = integer(day.accepted_count);
  byId("submittedCount").textContent = integer(day.submitted_count);
  byId("filledCount").textContent = integer(day.filled_count);
  byId("filledAmount").textContent = money(day.filled_amount);
}

function resultMarkup(row) {
  if (row.estimated) {
    const filters = row.filters.map((item) => `<span class="filter-tag">${escapeHtml(item.label)} ${integer(item.count)}</span>`).join("");
    return `<div class="estimated-result"><span class="result-good">预计买入 ${integer(row.estimated_buy_count)} 只</span>${filters ? `<div class="filter-list">${filters}</div>` : ""}</div>`;
  }
  if (!row.attempted) return `<span class="result-muted">未执行</span>`;
  if (row.submitted_count > 0) return `<span class="result-good">已提交 ${integer(row.submitted_count)} 单</span>`;
  if (row.pending_count > 0) {
    const buyable = row.est_buyable_count || 0;
    if (buyable > 0) return `<span class="result-pending">预估可买入 ${integer(buyable)} 只 · 未成交</span>`;
    return `<span class="result-pending">已生成 ${integer(row.pending_count)} 委托 · 未成交</span>`;
  }
  if (!row.filters.length) return `<span class="result-muted">无可下单股票</span>`;
  return `<div class="filter-list">${row.filters.map((item) => `<span class="filter-tag">${escapeHtml(item.label)} ${integer(item.count)}</span>`).join("")}</div>`;
}

function renderStrategies(day) {
  const rows = day.strategies.filter((row) => state.activeStrategy === "all" || row.id === state.activeStrategy);
  byId("strategyRows").innerHTML = rows.map((row) => `
    <tr>
      <td><div class="strategy-cell"><strong>${escapeHtml(row.name)}</strong><span>${escapeHtml(row.capital)}</span></div></td>
      <td class="signal-cell">${escapeHtml((window.StrategyDescriptions && window.StrategyDescriptions[row.id]) || row.signal)}</td>
      <td class="number">${integer(row.candidate_count)}</td>
      <td class="number">${integer(row.accepted_count)}</td>
      <td class="number">${integer(row.submitted_count)}</td>
      <td class="number">${integer(row.filled_count)}</td>
      <td class="number">${moneyOrDash(row.cash_before_sell)}</td>
      <td class="number">${moneyOrDash(row.cash_after_sell)}</td>
      <td class="number">${moneyOrDash(row.cash_before_buy ?? row.cash_before)}</td>
      <td class="number">${moneyOrDash(row.cash_after_buy)}</td>
      <td class="result-cell">${resultMarkup(row)}</td>
    </tr>`).join("");
}

function renderBuys(day) {
  const rows = day.buys.filter((row) => state.activeStrategy === "all" || row.strategy_id === state.activeStrategy);
  const empty = byId("emptyState");
  const table = byId("detailTable");
  byId("detailCaption").textContent = state.activeStrategy === "all" ? "当日十三策略实际委托与成交" : `${strategyLabels[state.activeStrategy]}实际委托与成交`;
  empty.hidden = rows.length > 0;
  table.hidden = rows.length === 0;
  if (!rows.length) {
    const selected = day.strategies.filter((row) => state.activeStrategy === "all" || row.id === state.activeStrategy);
    const candidateCount = selected.reduce((sum, row) => sum + row.candidate_count, 0);
    const pendingCount = selected.reduce((sum, row) => sum + (row.pending_count || 0), 0);
    const buyableCount = selected.reduce((sum, row) => sum + (row.est_buyable_count || 0), 0);
    byId("emptyReason").textContent = day.estimated_strategy_count
      ? "预测服务当日故障，以下结果为历史行情估算，没有真实委托"
      : buyableCount > 0 ? `预估可买入 ${integer(buyableCount)} 只，但当日无成交（收盘后生成委托，见下方候选清单及未买入原因）`
      : pendingCount > 0 ? `已生成 ${integer(pendingCount)} 笔委托但当日无成交（见下方候选清单）`
      : candidateCount > 0 ? "候选股票均未通过买入风控" : "当日没有产生候选股票";
    byId("buyRows").innerHTML = "";
    return;
  }
  byId("buyRows").innerHTML = rows.map((row) => `
    <tr>
      <td><strong>${escapeHtml(strategyLabels[row.strategy_id] || row.strategy_name)}</strong></td>
      <td>${escapeHtml(row.submit_time || "-")}</td>
      <td><div class="stock-cell"><strong>${escapeHtml(row.code)}</strong><span>${escapeHtml(row.name || "")}${row.near_limit ? ' <span class="reason-badge warning">涨停买入</span>' : ""}</span></div></td>
      <td>${row.order_id ? integer(row.order_id) : "-"}</td>
      <td><span class="order-status ${statusClass(row.status)}">${escapeHtml(statusLabels[row.status] || row.status || "未知")}</span></td>
      <td class="number">${integer(row.shares)}</td>
      <td class="number">${integer(row.filled_shares)}</td>
      <td class="number">${price(row.buy_price)}</td>
      <td class="number">${money(row.buy_amount)}</td>
    </tr>`).join("");
}

function reasonTone(reason) {
  if (["submitted", "estimated_buy", "pending"].includes(reason)) return "good";
  if (["near_limit", "paused", "insufficient_cash", "new_stock"].includes(reason)) return "warning";
  if (["invalid_price", "st", "delisted", "listing_date", "order_failed"].includes(reason)) return "danger";
  return "neutral";
}

function candidateRows(day) {
  return day.strategies.flatMap((strategy) => (strategy.candidates || []).map((candidate) => ({ ...candidate, strategy_id: strategy.id, strategy_name: strategy.name })));
}

function candidateDetails() {
  return Array.from(byId("candidateList").querySelectorAll("details.candidate-item"));
}

function syncCandidateToggle() {
  const items = candidateDetails();
  const allOpen = items.length > 0 && items.every((item) => item.open);
  const button = byId("candidateToggleAll");
  button.hidden = items.length === 0;
  button.textContent = allOpen ? "全部折叠" : "全部展开";
  button.setAttribute("aria-expanded", String(allOpen));
}

function destroyCharts() {
  state.candidateChartObserver?.disconnect();
  state.candidateChartObserver = null;
  state.chartInstances.forEach(({ chart, observer, separators }) => {
    observer?.disconnect();
    separators?.destroy();
    chart?.remove();
  });
  state.chartInstances = [];
}

function renderCandidates(day) {
  destroyCharts();
  const rows = candidateRows(day).filter((row) => state.activeStrategy === "all" || row.strategy_id === state.activeStrategy);
  const empty = byId("candidateEmpty");
  const list = byId("candidateList");
  empty.hidden = rows.length > 0;
  list.hidden = rows.length === 0;
  byId("candidateCountLabel").textContent = `${integer(rows.length)} 条候选记录`;
  byId("candidateCaption").textContent = day.estimated_strategy_count
    ? "历史估算候选 · 最近3个交易日分钟K线与成交量"
    : "最近3个交易日分钟K线与成交量";
  if (!rows.length) {
    list.innerHTML = "";
    syncCandidateToggle();
    return;
  }
  list.innerHTML = rows.map((row, index) => `
    <details class="candidate-item" data-code="${escapeHtml(row.code)}" data-chart-path="${escapeHtml(day.chart_path)}" data-reason="${escapeHtml(row.reason)}" data-up-price="${Number(row.up_price || 0)}" open>
      <summary>
        <span class="expand-symbol" aria-hidden="true">+</span>
        <span class="candidate-rank">#${integer(row.rank || index + 1)}</span>
        <span class="candidate-strategy">${escapeHtml(strategyLabels[row.strategy_id] || row.strategy_name)}</span>
        <span class="candidate-stock"><strong>${escapeHtml(row.code)}</strong><span>${escapeHtml(row.name || "")}</span></span>
        <span class="candidate-reason"><span class="reason-badge ${reasonTone(row.reason)}">${escapeHtml(row.reason_label || row.reason)}</span><span class="reason-source">${escapeHtml(row.reason_source_label || (row.reason_source === "recorded" ? "14:53决策记录" : "日终行情补算"))}</span></span>
        <span class="candidate-price"><strong>${row.estimated ? "14:53价" : "现价"} ${price(row.price)}</strong><small>涨停价 ${price(row.up_price)}</small></span>
      </summary>
      <div class="chart-panel">
        <div class="chart-meta"><span class="chart-days">分钟行情加载中</span><span class="chart-interpretation">红涨绿跌 · 下方柱为成交量</span></div>
        <div class="chart-container"><div class="chart-loading">正在读取分钟行情</div></div>
      </div>
    </details>`).join("");
  syncCandidateToggle();
  list.querySelectorAll("details[open]").forEach(scheduleCandidateChart);
}

function formatShanghaiTime(timestamp, withDate = false) {
  const options = withDate
    ? { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }
    : { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false };
  return new Intl.DateTimeFormat("zh-CN", options).format(new Date(Number(timestamp) * 1000)).replace("/", "-");
}

async function loadChartSnapshot(path) {
  if (!state.chartSnapshots.has(path)) {
    state.chartSnapshots.set(path, fetch(`${path}?t=${Date.now()}`, { cache: "no-store" }).then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    }));
  }
  return state.chartSnapshots.get(path);
}

function sparseChartMessage(chartData) {
  const count = chartData?.points?.length || 0;
  const days = (chartData?.trading_days || []).map(displayDate).join(" / ");
  const suffix = days ? `：${days}` : "";
  return `真实分钟线未导出，仅有${integer(count)}个日线合成点${suffix}`;
}

function describeChart(points, reason, upPrice) {
  if (reason !== "near_limit" || upPrice <= 0 || !points.length) {
    return { isLimit: false, text: "红涨绿跌 · 下方柱为成交量", markerText: "", markerTime: 0, limitSegments: [] };
  }
  const latestDate = points[points.length - 1].date;
  const dayPoints = points.filter((row) => row.date === latestDate);
  const tolerance = Math.max(0.005, upPrice * 0.0001);
  const limitPoints = dayPoints.filter((row) => Math.abs(Number(row.close) - upPrice) <= tolerance && Math.abs(Number(row.high) - Number(row.low)) < 0.000001);
  const limitSegments = [];
  let segment = [];
  dayPoints.forEach((row) => {
    const atLimit = Math.abs(Number(row.close) - upPrice) <= tolerance && Math.abs(Number(row.high) - Number(row.low)) < 0.000001;
    const continuous = !segment.length || Number(row.time) - Number(segment[segment.length - 1].time) <= 90;
    if (atLimit && continuous) {
      segment.push({ time: row.time, value: upPrice });
      return;
    }
    if (segment.length) limitSegments.push(segment);
    segment = atLimit ? [{ time: row.time, value: upPrice }] : [];
  });
  if (segment.length) limitSegments.push(segment);
  const lockedAllDay = dayPoints.length > 0 && dayPoints.every((row) => Math.abs(Number(row.close) - upPrice) <= tolerance);
  const markerText = lockedAllDay ? "全天涨停封板" : limitPoints.length ? `涨停封板${integer(limitPoints.length)}分钟` : "14:53涨停附近";
  return {
    isLimit: true,
    text: limitPoints.length ? `${markerText} · 红线为涨停封板价` : `${markerText} · K线已接近涨停价`,
    markerText,
    markerTime: limitPoints[Math.floor(limitPoints.length / 2)]?.time || dayPoints[Math.floor(dayPoints.length / 2)]?.time || 0,
    limitSegments
  };
}

function createFinancialChart(container, points, description) {
  const library = window.LightweightCharts;
  if (!library) throw new Error("K线组件未加载");
  const chart = library.createChart(container, {
    width: Math.max(320, container.clientWidth),
    height: container.clientHeight,
    layout: {
      background: { type: library.ColorType.Solid, color: "#ffffff" },
      textColor: "#66716d",
      fontFamily: '"Segoe UI", "Microsoft YaHei UI", sans-serif',
      fontSize: 11
    },
    grid: { vertLines: { color: "#edf1ef" }, horzLines: { color: "#edf1ef" } },
    rightPriceScale: { borderColor: "#dce2df" },
    timeScale: {
      borderColor: "#dce2df",
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter: (time) => formatShanghaiTime(time, true)
    },
    localization: { timeFormatter: (time) => formatShanghaiTime(time, true) },
    crosshair: { mode: library.CrosshairMode.Normal }
  });
  const candleSeries = chart.addSeries(library.CandlestickSeries, {
    upColor: "#d84a55", downColor: "#16866a", borderVisible: false,
    wickUpColor: "#d84a55", wickDownColor: "#16866a",
    priceLineVisible: false,
    priceFormat: { type: "price", precision: 2, minMove: 0.01 }
  });
  const volumeSeries = chart.addSeries(library.HistogramSeries, {
    priceScaleId: "volume",
    priceFormat: { type: "volume" },
    priceLineVisible: false,
    lastValueVisible: false
  });
  candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.28 } });
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
  candleSeries.setData(points.map((row) => ({ time: row.time, open: row.open, high: row.high, low: row.low, close: row.close })));
  (description.limitSegments || []).filter((segment) => segment.length > 1).forEach((segment) => {
    const limitSeries = chart.addSeries(library.LineSeries, {
      color: "#d84a55",
      lineWidth: 3,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false
    });
    limitSeries.setData(segment);
  });
  volumeSeries.setData(points.map((row) => ({ time: row.time, value: row.volume, color: row.close >= row.open ? "rgba(216,74,85,.62)" : "rgba(22,134,106,.62)" })));
  if (description.markerText && description.markerTime) {
    library.createSeriesMarkers(candleSeries, [{
      time: description.markerTime,
      position: "aboveBar",
      color: "#d84a55",
      shape: "circle",
      text: description.markerText
    }]);
  }
  chart.timeScale().fitContent();
  const separators = window.ChartDaySeparators?.attach(container, chart, points);
  const observer = new ResizeObserver((entries) => {
    const width = Math.floor(entries[0]?.contentRect.width || 0);
    if (width > 0) chart.applyOptions({ width });
  });
  observer.observe(container);
  state.chartInstances.push({ chart, observer, separators });
}

async function mountCandidateChart(details) {
  if (!details || details.dataset.rendered === "true" || details.dataset.loading === "true") return;
  state.candidateChartObserver?.unobserve(details);
  details.dataset.loading = "true";
  const container = details.querySelector(".chart-container");
  const daysLabel = details.querySelector(".chart-days");
  const interpretation = details.querySelector(".chart-interpretation");
  try {
    const chartSnapshot = await loadChartSnapshot(details.dataset.chartPath);
    const chartData = chartSnapshot.charts?.[details.dataset.code];
    daysLabel.textContent = (chartSnapshot.trading_days || []).map(displayDate).join(" / ");
    if (!chartData?.points?.length) {
      container.innerHTML = `<div class="chart-message">${escapeHtml(chartData?.message || "该交易日没有分钟行情快照")}</div>`;
      details.dataset.rendered = "true";
      return;
    }
    if (chartData.points.length < MIN_REAL_CHART_POINTS) {
      interpretation.textContent = "分钟行情未导出";
      interpretation.classList.remove("limit");
      container.innerHTML = `<div class="chart-message">${escapeHtml(sparseChartMessage(chartData))}</div>`;
      details.dataset.rendered = "true";
      return;
    }
    const description = describeChart(chartData.points, details.dataset.reason, Number(details.dataset.upPrice || 0));
    interpretation.textContent = description.text;
    interpretation.classList.toggle("limit", description.isLimit);
    container.innerHTML = "";
    createFinancialChart(container, chartData.points, description);
    details.dataset.rendered = "true";
  } catch (error) {
    daysLabel.textContent = "分钟行情不可用";
    interpretation.textContent = "行情说明不可用";
    container.innerHTML = `<div class="chart-message">${escapeHtml(error.message)}</div>`;
  } finally {
    delete details.dataset.loading;
  }
}

function scheduleCandidateChart(details) {
  if (!details || details.dataset.rendered === "true" || details.dataset.loading === "true") return;
  if (!("IntersectionObserver" in window)) {
    mountCandidateChart(details);
    return;
  }
  if (!state.candidateChartObserver) {
    state.candidateChartObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting && entry.target.open) mountCandidateChart(entry.target);
      });
    }, { rootMargin: "700px 0px" });
  }
  state.candidateChartObserver.observe(details);
}

function render() {
  const day = activeDay();
  if (!day) throw new Error("快照中没有可展示的交易日");
  renderDateSelect();
  renderTabs();
  renderSummary(day);
  renderStrategies(day);
  renderBuys(day);
  renderCandidates(day);
  byId("generatedAt").textContent = `快照 ${state.snapshot.generated_at}`;
  byId("scheduleLabel").textContent = `自动更新 ${state.snapshot.schedule}`;
  byId("syncBadge").textContent = day.estimated_strategy_count ? "历史估算" : "快照已更新";
  byId("loadingState").hidden = true;
  byId("errorState").hidden = true;
  byId("dashboard").hidden = false;
}

async function loadSnapshot(initial = false) {
  try {
    const response = await fetch(`./daily_buys_snapshot.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const snapshot = await response.json();
    const changed = snapshot.generated_at !== state.snapshot?.generated_at;
    state.snapshot = snapshot;
    if (!state.activeDate || !(snapshot.days || []).some((day) => day.date === state.activeDate)) state.activeDate = snapshot.latest_date;
    if (initial || changed) {
      render();
      syncUrlView("replace");
    }
  } catch (error) {
    if (!state.snapshot) {
      byId("loadingState").hidden = true;
      byId("dashboard").hidden = true;
      byId("errorState").hidden = false;
      byId("errorMessage").textContent = `无法读取 daily_buys_snapshot.json：${error.message}`;
    }
  }
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
byId("candidateList").addEventListener("toggle", (event) => {
  const details = event.target.closest("details.candidate-item");
  if (!details) return;
  syncCandidateToggle();
  if (details.open) scheduleCandidateChart(details);
  else state.candidateChartObserver?.unobserve(details);
}, true);
byId("candidateToggleAll").addEventListener("click", () => {
  const items = candidateDetails();
  const shouldOpen = items.some((item) => !item.open);
  items.forEach((item) => { item.open = shouldOpen; });
  syncCandidateToggle();
  if (shouldOpen) items.forEach(scheduleCandidateChart);
  else state.candidateChartObserver?.disconnect();
});

window.addEventListener("popstate", () => {
  if (!state.snapshot) return;
  const requested = readUrlView();
  state.activeStrategy = requested.strategy;
  state.activeDate = (state.snapshot.days || []).some((day) => day.date === requested.date)
    ? requested.date
    : state.snapshot.latest_date;
  render();
  syncUrlView("replace");
});

loadSnapshot(true);
window.setInterval(() => loadSnapshot(false), 60000);
