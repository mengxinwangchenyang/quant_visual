"use strict";

const state = { snapshot: null, activeEnd: "", activeStrategy: "all" };
const strategyLabels = { all: "全部", s1: "策略1", s2: "策略2", s3: "策略3", s4: "策略4", s5: "策略5", s6: "策略6", s7: "策略7", s8: "策略8", s9: "策略9", s10: "策略10", s11: "策略11", s12: "策略12", s13: "策略13" };
const strategyDescriptions = window.StrategyDescriptions || {};
const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
const integer = (value) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Number(value || 0));
const money = (value) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(Number(value || 0));
const price = (value) => Number(value || 0) > 0 ? Number(value).toFixed(2) : "-";
const percent = (value) => `${Number(value || 0) >= 0 ? "+" : ""}${(Number(value || 0) * 100).toFixed(2)}%`;
const displayDate = (value) => /^\d{8}$/.test(value || "") ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}` : value || "-";
const sum = (rows, key) => rows.reduce((total, row) => total + Number(row[key] || 0), 0);

function readUrlView() { const params = new URLSearchParams(location.search); const strategy = params.get("strategy") || "all"; return { end: params.get("end") || "", strategy: Object.hasOwn(strategyLabels, strategy) ? strategy : "all" }; }
function syncUrl(mode = "replace") {
  if (!state.activeEnd) return; const url = new URL(location.href); url.searchParams.set("end", state.activeEnd); url.searchParams.set("strategy", state.activeStrategy); const next = `${url.pathname}${url.search}${url.hash}`;
  if (next !== `${location.pathname}${location.search}${location.hash}`) history[mode === "push" ? "pushState" : "replaceState"]({ end: state.activeEnd, strategy: state.activeStrategy }, "", next);
}
function periodDays() { return (state.snapshot?.days || []).filter((day) => day.date <= state.activeEnd).sort((a, b) => a.date.localeCompare(b.date)); }
function periodRows(days, strategy = "all") { return days.flatMap((day) => (day.records || []).map((row) => ({ ...row, date: day.date, chart_path: day.chart_path }))).filter((row) => strategy === "all" || row.strategy_id === strategy).sort((a, b) => b.date.localeCompare(a.date) || a.strategy_id.localeCompare(b.strategy_id) || String(a.code).localeCompare(String(b.code))); }
function strategySummary(rows, id) {
  const selected = id === "all" ? rows : rows.filter((row) => row.strategy_id === id); const buyBase = sum(selected, "buy_amount") + sum(selected, "buy_fee"); const pnl = sum(selected, "pnl");
  const capital = id === "all" ? SellStatistics.capitalOf(Object.keys(strategyDescriptions)) : SellStatistics.capitalOf(id);
  const mainRows = selected.filter((row) => SellStatistics.boardOf(row.code) === ""); const mainBuyBase = sum(mainRows, "buy_amount") + sum(mainRows, "buy_fee"); const mainPnl = sum(mainRows, "pnl");
  return { id, name: id === "all" ? "综合" : strategyLabels[id], description: id === "all" ? "全部策略" : strategyDescriptions[id] || "-", record_count: selected.length, actual_orders: selected.filter((row) => row.data_type === "actual" && Number(row.order_id || 0) > 0).length,
    actual_filled: selected.filter((row) => row.data_type === "actual" && Number(row.sell_volume || 0) > 0).length, estimated: selected.filter((row) => row.data_type === "estimated").length,
    sell_amount: sum(selected, "sell_amount"), fees: sum(selected, "fees"), pnl, cumulative_return: capital ? pnl / capital : 0, return_rate: buyBase ? pnl / buyBase : 0,
    main_cumulative_return: capital ? mainPnl / capital : 0, main_return_rate: mainBuyBase ? mainPnl / mainBuyBase : 0, performance: SellStatistics.summarize(selected) };
}
function pnlClass(value) { return Number(value) > 0 ? "pnl-positive" : Number(value) < 0 ? "pnl-negative" : "pnl-flat"; }
function renderSelectors() {
  const dates = (state.snapshot.days || []).map((day) => day.date).sort().reverse(); byId("endSelect").innerHTML = dates.map((date) => `<option value="${date}">${displayDate(date)}</option>`).join(""); byId("endSelect").value = state.activeEnd;
  byId("strategyTabs").innerHTML = Object.entries(strategyLabels).map(([id, label]) => `<button type="button" role="tab" data-strategy="${id}" aria-selected="${state.activeStrategy === id}">${label}</button>`).join("");
}
function render() {
  const days = periodDays(); if (!days.length) throw new Error("截止日之前没有卖出快照"); const allRows = periodRows(days); const rows = periodRows(days, state.activeStrategy);
  const ids = state.activeStrategy === "all" ? ["all", ...Object.keys(strategyDescriptions)] : [state.activeStrategy]; const summaries = ids.map((id) => strategySummary(allRows, id));
  const buyBase = sum(rows, "buy_amount") + sum(rows, "buy_fee"); const pnl = sum(rows, "pnl"); const start = days[0].date;
  renderSelectors(); syncUrl();
  byId("periodRange").textContent = `${displayDate(start)} 至 ${displayDate(state.activeEnd)}`; byId("coverageState").textContent = `累计 ${integer(days.length)} 个有卖出记录的交易日`;
  byId("tradingDays").textContent = integer(days.length); byId("recordCount").textContent = integer(rows.length); byId("uniqueStockCount").textContent = integer(new Set(rows.map((row) => row.code)).size);
  byId("actualOrderCount").textContent = integer(rows.filter((row) => row.data_type === "actual" && Number(row.order_id || 0) > 0).length); byId("actualFilledCount").textContent = integer(rows.filter((row) => row.data_type === "actual" && Number(row.sell_volume || 0) > 0).length);
  byId("estimatedCount").textContent = integer(rows.filter((row) => row.data_type === "estimated").length); byId("fees").textContent = money(sum(rows, "fees")); byId("pnl").textContent = `${money(pnl)} · ${percent(buyBase ? pnl / buyBase : 0)}`; byId("pnl").className = pnlClass(pnl);
  byId("scheduleLabel").textContent = "每工作日15:30随日终快照更新"; byId("recordCountLabel").textContent = `${integer(rows.length)} 条`;
  byId("strategyRows").innerHTML = summaries.map((row) => `<tr class="${row.id === "all" ? "comprehensive" : ""}"><td><strong>${escapeHtml(row.name)}</strong></td><td class="signal-cell">${escapeHtml(row.description)}</td><td class="number">${integer(row.record_count)}</td><td class="number">${integer(row.actual_orders)}</td>
    <td class="number">${integer(row.actual_filled)}</td><td class="number">${integer(row.estimated)}</td><td class="number">${money(row.sell_amount)}</td><td class="number">${money(row.fees)}</td>
    <td class="number">${money(row.pnl)}</td><td class="number ${pnlClass(row.cumulative_return)}">${percent(row.cumulative_return)}</td><td class="number">${percent(row.return_rate)}</td><td class="number ${pnlClass(row.main_cumulative_return)}">${percent(row.main_cumulative_return)}</td><td class="number">${percent(row.main_return_rate)}</td>${SellStatistics.cells(row.performance)}</tr>`).join("");
  byId("emptyState").hidden = rows.length > 0;
  SellPeriodCharts.render(byId("sellChartList"), rows, byId("chartToggleAll"), { openAll: false, openFirst: false, showBuyDayLink: false });
  byId("syncBadge").textContent = "累计卖出已更新"; byId("generatedAt").textContent = `快照 ${state.snapshot.generated_at || "-"}`; byId("loadingState").hidden = true; byId("dashboard").hidden = false; byId("errorState").hidden = true;
  SellPnlTrend.render(byId("sellTrendChart"), rows, { strategy: state.activeStrategy });
}
function bindEvents() {
  byId("endSelect").addEventListener("change", (event) => { state.activeEnd = event.target.value; syncUrl("push"); render(); });
  byId("strategyTabs").addEventListener("click", (event) => { const button = event.target.closest("button[data-strategy]"); if (!button) return; state.activeStrategy = button.dataset.strategy; syncUrl("push"); render(); });
  addEventListener("popstate", () => { const view = readUrlView(); state.activeEnd = view.end; state.activeStrategy = view.strategy; render(); });
}
async function init() {
  try { const response = await fetch(`./daily_sells_snapshot.json?t=${Date.now()}`, { cache: "no-store" }); if (!response.ok) throw new Error(`HTTP ${response.status}`); state.snapshot = await response.json();
    const view = readUrlView(); const dates = state.snapshot.days.map((day) => day.date); state.activeEnd = dates.includes(view.end) ? view.end : state.snapshot.latest_date; state.activeStrategy = view.strategy; bindEvents(); render();
  } catch (error) { byId("loadingState").hidden = true; byId("errorState").hidden = false; byId("errorMessage").textContent = `读取 daily_sells_snapshot.json 失败：${error.message}`; }
}
init();
