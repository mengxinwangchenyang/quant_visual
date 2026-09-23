"use strict";

const state = { snapshot: null, activeEnd: "", activeStrategy: "all" };
const strategyLabels = { all: "全部", s1: "策略1", s2: "策略2", s3: "策略3", s4: "策略4", s5: "策略5", s6: "策略6", s7: "策略7", s8: "策略8", s9: "策略9", s10: "策略10", s11: "策略11", s12: "策略12", s13: "策略13" };
const statusLabels = { buy_submitted: "已委托", buy_partial: "部分成交", open: "已成交", buy_unfilled: "未成交", sell_submitted: "已进入卖出", sell_partial: "卖出中", closed: "已卖出" };
const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
const integer = (value) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Number(value || 0));
const money = (value) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(Number(value || 0));
const price = (value) => Number(value || 0) > 0 ? Number(value).toFixed(2) : "-";
const displayDate = (value) => /^\d{8}$/.test(value || "") ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}` : value || "-";
const sum = (rows, key) => rows.reduce((total, row) => total + Number(row[key] || 0), 0);

function readUrlView() {
  const params = new URLSearchParams(location.search);
  const strategy = params.get("strategy") || "all";
  return { end: params.get("end") || "", strategy: Object.hasOwn(strategyLabels, strategy) ? strategy : "all" };
}

function syncUrl(mode = "replace") {
  if (!state.activeEnd) return;
  const url = new URL(location.href); url.searchParams.set("end", state.activeEnd); url.searchParams.set("strategy", state.activeStrategy);
  const next = `${url.pathname}${url.search}${url.hash}`;
  if (next !== `${location.pathname}${location.search}${location.hash}`) history[mode === "push" ? "pushState" : "replaceState"]({ end: state.activeEnd, strategy: state.activeStrategy }, "", next);
}

function periodDays() { return (state.snapshot?.days || []).filter((day) => day.date <= state.activeEnd).sort((a, b) => a.date.localeCompare(b.date)); }

function allStrategies(days) {
  const found = new Map();
  days.forEach((day) => (day.strategies || []).forEach((row) => {
    if (!found.has(row.id)) found.set(row.id, { id: row.id, name: row.name, signal: row.signal, capital: row.capital });
  }));
  return [...found.values()].sort((a, b) => Number(a.id.slice(1)) - Number(b.id.slice(1)));
}

function buyRows(days, strategyId = "all") {
  const rows = [];
  days.forEach((day) => (day.strategies || []).forEach((strategy) => {
    if (strategyId !== "all" && strategy.id !== strategyId) return;
    if (strategy.estimated) {
      (strategy.candidates || []).filter((row) => row.reason === "estimated_buy" || (row.eligible && row.buy_amount)).forEach((row) => rows.push({
        ...row, date: day.date, strategy_id: strategy.id, strategy_name: strategy.name, data_type: "estimated", data_label: "历史预估",
        buy_price: row.price, volume: row.shares, amount: row.buy_amount, result: row.reason_label || "预估可以买，未提交委托"
      }));
    } else {
      (strategy.buys || []).forEach((row) => rows.push({
        ...row, date: day.date, strategy_id: strategy.id, strategy_name: strategy.name, data_type: "actual", data_label: "真实委托",
        buy_price: row.buy_price || row.filled_price || row.price, volume: row.filled_shares || row.shares || row.order_volume,
        amount: row.buy_amount || row.filled_amount, result: row.status_label || statusLabels[row.status] || row.status || (row.filled_shares ? "已成交" : "已提交委托")
      }));
    }
  }));
  return rows.sort((a, b) => b.date.localeCompare(a.date) || a.strategy_id.localeCompare(b.strategy_id) || String(a.code).localeCompare(String(b.code)));
}

function aggregateStrategy(days, meta) {
  const daily = days.map((day) => (day.strategies || []).find((row) => row.id === meta.id)).filter(Boolean);
  const rows = buyRows(days, meta.id);
  return { ...meta, candidate_count: sum(daily, "candidate_count"), accepted_count: sum(daily, "accepted_count"),
    estimated_count: rows.filter((row) => row.data_type === "estimated").length,
    submitted_count: sum(daily.filter((row) => !row.estimated), "submitted_count"), filled_count: sum(daily.filter((row) => !row.estimated), "filled_count"), amount: sum(rows, "amount") };
}

function renderSelectors() {
  const dates = (state.snapshot.days || []).map((day) => day.date).sort().reverse();
  byId("endSelect").innerHTML = dates.map((date) => `<option value="${date}">${displayDate(date)}</option>`).join("");
  byId("endSelect").value = state.activeEnd;
  byId("strategyTabs").innerHTML = Object.entries(strategyLabels).map(([id, label]) => `<button type="button" role="tab" data-strategy="${id}" aria-selected="${state.activeStrategy === id}">${label}</button>`).join("");
}

function render() {
  const days = periodDays(); if (!days.length) throw new Error("截止日之前没有买入快照");
  const start = days[0].date;
  const totals = allStrategies(days).map((meta) => aggregateStrategy(days, meta));
  const visibleTotals = state.activeStrategy === "all" ? totals : totals.filter((row) => row.id === state.activeStrategy);
  const rows = buyRows(days, state.activeStrategy);
  renderSelectors(); syncUrl();
  byId("periodRange").textContent = `${displayDate(start)} 至 ${displayDate(state.activeEnd)}`;
  byId("coverageState").textContent = `累计 ${integer(days.length)} 个交易日快照`;
  byId("tradingDays").textContent = integer(days.length);
  byId("candidateCount").textContent = integer(sum(visibleTotals, "candidate_count"));
  byId("acceptedCount").textContent = integer(sum(visibleTotals, "accepted_count"));
  byId("estimatedCount").textContent = integer(sum(visibleTotals, "estimated_count"));
  byId("submittedCount").textContent = integer(sum(visibleTotals, "submitted_count"));
  byId("filledCount").textContent = integer(sum(visibleTotals, "filled_count"));
  byId("buyAmount").textContent = money(sum(visibleTotals, "amount"));
  byId("scheduleLabel").textContent = "每工作日15:30随日终快照更新";
  byId("buyCountLabel").textContent = `${integer(rows.length)} 条`;
  byId("strategyRows").innerHTML = visibleTotals.map((row) => `<tr>
    <td><span class="strategy-cell"><strong>${escapeHtml(row.name)}</strong><span>${escapeHtml(row.capital)}</span></span></td><td class="signal-cell">${escapeHtml((window.StrategyDescriptions && window.StrategyDescriptions[row.id]) || row.signal)}</td>
    <td class="number">${integer(row.candidate_count)}</td><td class="number">${integer(row.accepted_count)}</td><td class="number">${integer(row.estimated_count)}</td>
    <td class="number">${integer(row.submitted_count)}</td><td class="number">${integer(row.filled_count)}</td><td class="number">${money(row.amount)}</td></tr>`).join("");
  byId("emptyState").hidden = rows.length > 0; byId("detailTable").hidden = rows.length === 0;
  byId("buyRows").innerHTML = rows.map((row) => {
    const change = Number(row.change_pct); const changeText = Number.isFinite(change) ? `${change >= 0 ? "+" : ""}${change.toFixed(2)}%` : "-";
    return `<tr><td>${displayDate(row.date)}</td><td><strong>${escapeHtml(row.strategy_name)}</strong></td><td><span class="data-badge ${row.data_type}">${escapeHtml(row.data_label)}</span></td>
      <td><span class="stock-cell"><strong>${escapeHtml(row.code)}</strong><span>${escapeHtml(row.name || "")}</span></span></td><td class="number">${price(row.buy_price)}</td>
      <td class="number"><span class="change-value ${change > 0 ? "up" : change < 0 ? "down" : ""}">${changeText}</span></td><td class="number">${integer(row.volume)}</td>
      <td class="number">${money(row.amount)}</td><td>${escapeHtml(row.result)}</td><td><a class="detail-link" href="./daily_buys.html?date=${row.date}&strategy=${row.strategy_id}">查看当日行情</a></td></tr>`;
  }).join("");
  byId("syncBadge").textContent = "累计数据已更新"; byId("generatedAt").textContent = `快照 ${state.snapshot.generated_at || "-"}`;
  byId("loadingState").hidden = true; byId("dashboard").hidden = false; byId("errorState").hidden = true;
}

function bindEvents() {
  byId("endSelect").addEventListener("change", (event) => { state.activeEnd = event.target.value; syncUrl("push"); render(); });
  byId("strategyTabs").addEventListener("click", (event) => { const button = event.target.closest("button[data-strategy]"); if (!button) return; state.activeStrategy = button.dataset.strategy; syncUrl("push"); render(); });
  addEventListener("popstate", () => { const view = readUrlView(); state.activeEnd = view.end; state.activeStrategy = view.strategy; render(); });
}

async function init() {
  try {
    const response = await fetch(`./daily_buys_snapshot.json?t=${Date.now()}`, { cache: "no-store" }); if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.snapshot = await response.json(); const view = readUrlView();
    const dates = state.snapshot.days.map((day) => day.date); state.activeEnd = dates.includes(view.end) ? view.end : state.snapshot.latest_date; state.activeStrategy = view.strategy;
    bindEvents(); render();
  } catch (error) { byId("loadingState").hidden = true; byId("errorState").hidden = false; byId("errorMessage").textContent = `读取 daily_buys_snapshot.json 失败：${error.message}`; }
}

init();
