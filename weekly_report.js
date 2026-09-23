"use strict";

const state = { snapshot: null, activeWeek: "" };
const strategyIds = ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11", "s12", "s13"];
const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
const integer = (value) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Number(value || 0));
const money = (value) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(Number(value || 0));
const displayDate = (value) => /^\d{8}$/.test(value || "") ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}` : value || "-";
const sum = (rows, key) => rows.reduce((total, row) => total + Number(row[key] || 0), 0);

function readUrlWeek() { return new URLSearchParams(location.search).get("week") || ""; }
function syncUrl(mode = "replace") {
  if (!state.activeWeek) return;
  const url = new URL(location.href); url.searchParams.set("week", state.activeWeek);
  const next = `${url.pathname}${url.search}${url.hash}`;
  if (next !== `${location.pathname}${location.search}${location.hash}`) history[mode === "push" ? "pushState" : "replaceState"]({ week: state.activeWeek }, "", next);
}
function activeWeek() { return (state.snapshot?.weeks || []).find((row) => row.week === state.activeWeek) || state.snapshot?.weeks?.[0] || null; }

function strategyBuyRows(week, strategyId) {
  const rows = [];
  (week.buy_days || []).forEach((day) => {
    const strategy = (day.strategies || []).find((row) => row.id === strategyId);
    if (!strategy) return;
    if (strategy.estimated) {
      (strategy.candidates || []).filter((row) => row.reason === "estimated_buy" || (row.eligible && row.buy_amount)).forEach((row) => rows.push({ ...row, data_type: "estimated", amount: row.buy_amount }));
    } else {
      (strategy.buys || []).forEach((row) => rows.push({ ...row, data_type: "actual", amount: row.buy_amount || row.filled_amount }));
    }
  });
  return rows;
}

function metaFor(week, strategyId) {
  for (const day of week.buy_days || []) {
    const row = (day.strategies || []).find((item) => item.id === strategyId);
    if (row) return row;
  }
  return { id: strategyId, name: `策略${strategyId.slice(1)}`, signal: "-", capital: "-" };
}

function strategySummary(week, strategyId) {
  const meta = metaFor(week, strategyId);
  const daily = (week.buy_days || []).map((day) => (day.strategies || []).find((row) => row.id === strategyId)).filter(Boolean);
  const buys = strategyBuyRows(week, strategyId);
  const sells = (week.sell_days || []).flatMap((day) => day.records || []).filter((row) => row.strategy_id === strategyId);
  return { ...meta, candidate_count: sum(daily, "candidate_count"), accepted_count: sum(daily, "accepted_count"), buy_count: buys.length, buy_amount: sum(buys, "amount"), sell_count: sells.length, fees: sum(sells, "fees"), pnl: sum(sells, "pnl") };
}

function daySummary(week, date) {
  const buy = (week.buy_days || []).find((row) => row.date === date) || {};
  const sell = (week.sell_days || []).find((row) => row.date === date) || {};
  const estimated = (buy.strategies || []).reduce((total, row) => total + Number(row.estimated_buy_count || 0), 0);
  return { date, candidate_count: buy.candidate_count || 0, accepted_count: buy.accepted_count || 0, estimated_count: estimated, submitted_count: buy.submitted_count || 0, filled_count: buy.filled_count || 0, sell_count: sell.record_count || 0, fees: sell.fees || 0, pnl: sell.pnl || 0 };
}

function pnlClass(value) { return Number(value) > 0 ? "pnl-positive" : Number(value) < 0 ? "pnl-negative" : "pnl-flat"; }

function render() {
  const week = activeWeek(); if (!week) throw new Error("周快照中没有可展示的完整周");
  state.activeWeek = week.week; syncUrl();
  byId("weekSelect").innerHTML = state.snapshot.weeks.map((row) => `<option value="${row.week}">${displayDate(row.start_date)} 至 ${displayDate(row.end_date)}</option>`).join("");
  byId("weekSelect").value = week.week;

  const summaries = strategyIds.map((id) => strategySummary(week, id));
  const allBuys = strategyIds.flatMap((id) => strategyBuyRows(week, id));
  const allSells = (week.sell_days || []).flatMap((day) => day.records || []);
  const estimatedCount = allBuys.filter((row) => row.data_type === "estimated").length;
  const actualCount = allBuys.length - estimatedCount;
  const totalBuyAmount = sum(summaries, "buy_amount");
  const totalFees = sum(allSells, "fees");
  const totalPnl = sum(allSells, "pnl");
  const invested = sum(allSells, "buy_amount");
  const returnRate = invested ? totalPnl / invested : 0;
  const dates = [...new Set([...(week.buy_days || []).map((row) => row.date), ...(week.sell_days || []).map((row) => row.date)])].sort();

  byId("weekRange").textContent = `${displayDate(week.start_date)} 至 ${displayDate(week.end_date)}`;
  byId("coverageState").textContent = dates.length === 5 ? "周一至周五快照完整" : `现有数据覆盖 ${dates.length} 个交易日`;
  byId("reportVerdict").textContent = totalPnl > 0 ? `盈利 ${money(totalPnl)}` : totalPnl < 0 ? `亏损 ${money(Math.abs(totalPnl))}` : "当周持平";
  byId("reportVerdict").className = pnlClass(totalPnl);
  byId("reportVerdictNote").textContent = `当周卖出收益率 ${(returnRate * 100).toFixed(2)}%，已包含 ${money(totalFees)} 手续费`;
  byId("tradingDays").textContent = integer(dates.length);
  byId("buyCount").textContent = integer(allBuys.length);
  byId("buyTypeNote").textContent = `预估${integer(estimatedCount)} / 真实${integer(actualCount)}`;
  byId("buyAmount").textContent = money(totalBuyAmount);
  byId("sellCount").textContent = integer(allSells.length);
  byId("fees").textContent = money(totalFees);
  byId("pnl").textContent = money(totalPnl); byId("pnl").className = pnlClass(totalPnl);
  byId("returnRate").textContent = `收益率 ${(returnRate * 100).toFixed(2)}%`;
  byId("snapshotTime").textContent = `周快照 ${state.snapshot.generated_at || "-"}`;

  byId("strategyRows").innerHTML = summaries.map((row) => `<tr>
    <td><span class="strategy-cell"><strong>${escapeHtml(row.name)}</strong><span>${escapeHtml(row.capital)}</span></span></td><td class="signal-cell">${escapeHtml((window.StrategyDescriptions && window.StrategyDescriptions[row.id]) || row.signal)}</td>
    <td class="number">${integer(row.candidate_count)}</td><td class="number">${integer(row.accepted_count)}</td><td class="number">${integer(row.buy_count)}</td><td class="number">${money(row.buy_amount)}</td>
    <td class="number">${integer(row.sell_count)}</td><td class="number">${money(row.fees)}</td><td class="number ${pnlClass(row.pnl)}">${money(row.pnl)}</td></tr>`).join("");

  byId("dailyRows").innerHTML = dates.map((date) => {
    const row = daySummary(week, date);
    return `<tr><td><strong>${displayDate(date)}</strong></td><td class="number">${integer(row.candidate_count)}</td><td class="number">${integer(row.accepted_count)}</td>
      <td class="number">${integer(row.estimated_count)}</td><td class="number">${integer(row.submitted_count)}</td><td class="number">${integer(row.filled_count)}</td>
      <td class="number">${integer(row.sell_count)}</td><td class="number">${money(row.fees)}</td><td class="number ${pnlClass(row.pnl)}">${money(row.pnl)}</td>
      <td><span class="daily-links"><a class="detail-link" href="./daily_buys.html?date=${date}&strategy=all">买入</a><a class="detail-link" href="./daily_sells.html?date=${date}&strategy=all">卖出</a><a class="detail-link" href="./daily_report.html?date=${date}">简报</a></span></td></tr>`;
  }).join("");
  byId("reportBadge").textContent = estimatedCount && actualCount ? "预估与真实混合" : estimatedCount ? "历史预估周" : "真实交易周";
  byId("loadingState").hidden = true; byId("report").hidden = false; byId("errorState").hidden = true;
}

function bindEvents() {
  byId("weekSelect").addEventListener("change", (event) => { state.activeWeek = event.target.value; syncUrl("push"); render(); });
  byId("printButton").addEventListener("click", () => window.print());
  window.addEventListener("popstate", () => { state.activeWeek = readUrlWeek(); render(); });
}

async function init() {
  try {
    const response = await fetch(`./weekly_snapshot.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.snapshot = await response.json();
    const requested = readUrlWeek(); state.activeWeek = state.snapshot.weeks.some((row) => row.week === requested) ? requested : state.snapshot.latest_week;
    bindEvents(); render();
  } catch (error) {
    byId("loadingState").hidden = true; byId("errorState").hidden = false; byId("errorMessage").textContent = `读取 weekly_snapshot.json 失败：${error.message}`;
  }
}

init();
