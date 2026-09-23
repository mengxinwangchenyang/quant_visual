"use strict";

const state = { snapshot: null, activeWeek: "", activeStrategy: "all" };
const strategyLabels = { all: "全部", s1: "策略1", s2: "策略2", s3: "策略3", s4: "策略4", s5: "策略5", s6: "策略6", s7: "策略7", s8: "策略8", s9: "策略9", s10: "策略10", s11: "策略11", s12: "策略12", s13: "策略13" };
const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
const integer = (value) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Number(value || 0));
const money = (value) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(Number(value || 0));
const price = (value) => Number(value || 0) > 0 ? Number(value).toFixed(2) : "-";
const displayDate = (value) => /^\d{8}$/.test(value || "") ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}` : value || "-";
const sum = (rows, key) => rows.reduce((total, row) => total + Number(row[key] || 0), 0);

function readUrlView() {
  const params = new URLSearchParams(window.location.search);
  const strategy = params.get("strategy") || "all";
  return { week: params.get("week") || "", strategy: Object.hasOwn(strategyLabels, strategy) ? strategy : "all" };
}

function syncUrl(mode = "replace") {
  if (!state.activeWeek) return;
  const url = new URL(window.location.href);
  url.searchParams.set("week", state.activeWeek);
  url.searchParams.set("strategy", state.activeStrategy);
  const next = `${url.pathname}${url.search}${url.hash}`;
  if (next === `${location.pathname}${location.search}${location.hash}`) return;
  history[mode === "push" ? "pushState" : "replaceState"]({ week: state.activeWeek, strategy: state.activeStrategy }, "", next);
}

function activeWeek() {
  return (state.snapshot?.weeks || []).find((row) => row.week === state.activeWeek) || state.snapshot?.weeks?.[0] || null;
}

function allStrategies(week) {
  const found = new Map();
  (week.buy_days || []).forEach((day) => (day.strategies || []).forEach((row) => {
    if (!found.has(row.id)) found.set(row.id, { id: row.id, name: row.name, signal: row.signal, capital: row.capital });
  }));
  return [...found.values()].sort((a, b) => Number(a.id.slice(1)) - Number(b.id.slice(1)));
}

function buyRows(week, strategyId = "all") {
  const rows = [];
  (week.buy_days || []).forEach((day) => (day.strategies || []).forEach((strategy) => {
    if (strategyId !== "all" && strategy.id !== strategyId) return;
    if (strategy.estimated) {
      (strategy.candidates || []).filter((row) => row.reason === "estimated_buy" || (row.eligible && row.buy_amount)).forEach((row) => rows.push({
        ...row, date: day.date, strategy_id: strategy.id, strategy_name: strategy.name, data_type: "estimated",
        data_label: "历史预估", buy_price: row.price, volume: row.shares, amount: row.buy_amount,
        result: row.reason_label || "预估可以买，未提交委托"
      }));
    } else {
      (strategy.buys || []).forEach((row) => rows.push({
        ...row, date: day.date, strategy_id: strategy.id, strategy_name: strategy.name, data_type: "actual",
        data_label: "真实委托", buy_price: row.buy_price || row.filled_price || row.price,
        volume: row.filled_shares || row.shares || row.order_volume,
        amount: row.buy_amount || row.filled_amount,
        result: row.status_label || row.status || (row.filled_shares ? "已成交" : "已提交委托")
      }));
    }
  }));
  return rows.sort((a, b) => a.date.localeCompare(b.date) || a.strategy_id.localeCompare(b.strategy_id) || String(a.code).localeCompare(String(b.code)));
}

function aggregateStrategy(week, meta) {
  const daily = (week.buy_days || []).map((day) => (day.strategies || []).find((row) => row.id === meta.id)).filter(Boolean);
  const rows = buyRows(week, meta.id);
  return {
    ...meta, candidate_count: sum(daily, "candidate_count"), accepted_count: sum(daily, "accepted_count"),
    estimated_count: rows.filter((row) => row.data_type === "estimated").length,
    submitted_count: sum(daily.filter((row) => !row.estimated), "submitted_count"),
    filled_count: sum(daily.filter((row) => !row.estimated), "filled_count"), amount: sum(rows, "amount")
  };
}

function renderWeekSelect() {
  byId("weekSelect").innerHTML = state.snapshot.weeks.map((week) => `<option value="${week.week}">${displayDate(week.start_date)} 至 ${displayDate(week.end_date)}</option>`).join("");
  byId("weekSelect").value = state.activeWeek;
}

function renderTabs() {
  byId("strategyTabs").innerHTML = Object.entries(strategyLabels).map(([id, label]) => `<button type="button" role="tab" data-strategy="${id}" aria-selected="${state.activeStrategy === id}">${label}</button>`).join("");
}

function render() {
  const week = activeWeek();
  if (!week) throw new Error("周快照中没有可展示的完整周");
  state.activeWeek = week.week;
  const strategyMeta = allStrategies(week);
  const strategyTotals = strategyMeta.map((meta) => aggregateStrategy(week, meta));
  const visibleTotals = state.activeStrategy === "all" ? strategyTotals : strategyTotals.filter((row) => row.id === state.activeStrategy);
  const rows = buyRows(week, state.activeStrategy);
  const buyDays = week.buy_days || [];
  const availableDates = [...new Set(buyDays.map((day) => day.date))];

  renderWeekSelect(); renderTabs(); syncUrl();
  byId("weekRange").textContent = `${displayDate(week.start_date)} 至 ${displayDate(week.end_date)}`;
  byId("coverageState").textContent = availableDates.length === 5 ? "周一至周五数据完整" : `当前快照覆盖 ${availableDates.length} 个交易日`;
  byId("tradingDays").textContent = integer(availableDates.length);
  byId("candidateCount").textContent = integer(sum(visibleTotals, "candidate_count"));
  byId("acceptedCount").textContent = integer(sum(visibleTotals, "accepted_count"));
  byId("estimatedCount").textContent = integer(sum(visibleTotals, "estimated_count"));
  byId("submittedCount").textContent = integer(sum(visibleTotals, "submitted_count"));
  byId("filledCount").textContent = integer(sum(visibleTotals, "filled_count"));
  byId("buyAmount").textContent = money(sum(visibleTotals, "amount"));
  byId("scheduleLabel").textContent = state.snapshot.schedule || "每周五15:30自动生成";
  byId("buyCountLabel").textContent = `${integer(rows.length)} 条`;

  byId("strategyRows").innerHTML = visibleTotals.map((row) => `<tr>
    <td><span class="strategy-cell"><strong>${escapeHtml(row.name)}</strong><span>${escapeHtml(row.capital)}</span></span></td>
    <td class="signal-cell">${escapeHtml((window.StrategyDescriptions && window.StrategyDescriptions[row.id]) || row.signal)}</td><td class="number">${integer(row.candidate_count)}</td><td class="number">${integer(row.accepted_count)}</td>
    <td class="number">${integer(row.estimated_count)}</td><td class="number">${integer(row.submitted_count)}</td><td class="number">${integer(row.filled_count)}</td><td class="number">${money(row.amount)}</td>
  </tr>`).join("");

  byId("emptyState").hidden = rows.length > 0;
  byId("detailTable").hidden = rows.length === 0;
  byId("buyRows").innerHTML = rows.map((row) => {
    const change = Number(row.change_pct);
    const changeText = Number.isFinite(change) ? `${change >= 0 ? "+" : ""}${change.toFixed(2)}%` : "-";
    return `<tr><td>${displayDate(row.date)}</td><td><strong>${escapeHtml(row.strategy_name)}</strong></td>
      <td><span class="data-badge ${row.data_type}">${escapeHtml(row.data_label)}</span></td>
      <td><span class="stock-cell"><strong>${escapeHtml(row.code)}</strong><span>${escapeHtml(row.name || "")}</span></span></td>
      <td class="number">${price(row.buy_price)}</td><td class="number"><span class="change-value ${change > 0 ? "up" : change < 0 ? "down" : ""}">${changeText}</span></td>
      <td class="number">${integer(row.volume)}</td><td class="number">${money(row.amount)}</td><td>${escapeHtml(row.result)}</td>
      <td><a class="detail-link" href="./daily_buys.html?date=${row.date}&strategy=${row.strategy_id}">查看当日行情</a></td></tr>`;
  }).join("");
  byId("syncBadge").textContent = "周快照已生成";
  byId("generatedAt").textContent = `更新 ${state.snapshot.generated_at || "-"}`;
  byId("loadingState").hidden = true; byId("dashboard").hidden = false; byId("errorState").hidden = true;
}

function bindEvents() {
  byId("weekSelect").addEventListener("change", (event) => { state.activeWeek = event.target.value; syncUrl("push"); render(); });
  byId("strategyTabs").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-strategy]"); if (!button) return;
    state.activeStrategy = button.dataset.strategy; syncUrl("push"); render();
  });
  window.addEventListener("popstate", () => { const view = readUrlView(); state.activeWeek = view.week; state.activeStrategy = view.strategy; render(); });
}

async function init() {
  try {
    const response = await fetch(`./weekly_snapshot.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.snapshot = await response.json();
    const view = readUrlView();
    state.activeWeek = state.snapshot.weeks.some((row) => row.week === view.week) ? view.week : state.snapshot.latest_week;
    state.activeStrategy = view.strategy; bindEvents(); render();
  } catch (error) {
    byId("loadingState").hidden = true; byId("errorState").hidden = false; byId("errorMessage").textContent = `读取 weekly_snapshot.json 失败：${error.message}`;
  }
}

init();
