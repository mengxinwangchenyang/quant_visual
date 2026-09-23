"use strict";

const state = { buys: null, sells: null, activeEnd: "" };
const strategyIds = ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11", "s12", "s13"];
const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
const integer = (value) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Number(value || 0));
const money = (value) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(Number(value || 0));
const displayDate = (value) => /^\d{8}$/.test(value || "") ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}` : value || "-";
const displayMonth = (value) => /^\d{6}$/.test(value || "") ? `${value.slice(0, 4)}-${value.slice(4, 6)}` : value || "-";
const sum = (rows, key) => rows.reduce((total, row) => total + Number(row[key] || 0), 0);
const readUrlEnd = () => new URLSearchParams(location.search).get("end") || "";

function syncUrl(mode = "replace") {
  if (!state.activeEnd) return;
  const url = new URL(location.href); url.searchParams.set("end", state.activeEnd); const next = `${url.pathname}${url.search}${url.hash}`;
  if (next !== `${location.pathname}${location.search}${location.hash}`) history[mode === "push" ? "pushState" : "replaceState"]({ end: state.activeEnd }, "", next);
}

function buyDays() { return (state.buys?.days || []).filter((day) => day.date <= state.activeEnd).sort((a, b) => a.date.localeCompare(b.date)); }
function sellDays() { return (state.sells?.days || []).filter((day) => day.date <= state.activeEnd).sort((a, b) => a.date.localeCompare(b.date)); }

function strategyBuyRows(days, strategyId) {
  const rows = [];
  days.forEach((day) => {
    const strategy = (day.strategies || []).find((row) => row.id === strategyId); if (!strategy) return;
    if (strategy.estimated) (strategy.candidates || []).filter((row) => row.reason === "estimated_buy" || (row.eligible && row.buy_amount)).forEach((row) => rows.push({ ...row, date: day.date, data_type: "estimated", amount: row.buy_amount }));
    else (strategy.buys || []).forEach((row) => rows.push({ ...row, date: day.date, data_type: "actual", amount: row.buy_amount || row.filled_amount }));
  });
  return rows;
}

function metaFor(days, strategyId) {
  for (const day of days) { const row = (day.strategies || []).find((item) => item.id === strategyId); if (row) return row; }
  return { id: strategyId, name: `策略${strategyId.slice(1)}`, signal: "-", capital: "-" };
}

function strategySummary(buys, sells, strategyId) {
  const meta = metaFor(buys, strategyId);
  const daily = buys.map((day) => (day.strategies || []).find((row) => row.id === strategyId)).filter(Boolean);
  const buyRows = strategyBuyRows(buys, strategyId);
  const sellRows = sells.flatMap((day) => day.records || []).filter((row) => row.strategy_id === strategyId);
  return { ...meta, candidate_count: sum(daily, "candidate_count"), accepted_count: sum(daily, "accepted_count"), buy_count: buyRows.length,
    buy_amount: sum(buyRows, "amount"), sell_count: sellRows.length, fees: sum(sellRows, "fees"), pnl: sum(sellRows, "pnl") };
}

function monthlySummary(buys, sells, month) {
  const monthBuys = buys.filter((day) => day.date.startsWith(month)); const monthSells = sells.filter((day) => day.date.startsWith(month));
  const buyRows = strategyIds.flatMap((id) => strategyBuyRows(monthBuys, id)); const sellRows = monthSells.flatMap((day) => day.records || []);
  const dates = new Set([...monthBuys.map((day) => day.date), ...monthSells.map((day) => day.date)]);
  const estimated = buyRows.filter((row) => row.data_type === "estimated").length;
  const submitted = monthBuys.reduce((total, day) => total + Number(day.submitted_count || 0), 0);
  const filled = monthBuys.reduce((total, day) => total + Number(day.filled_count || 0), 0);
  const invested = sum(sellRows, "buy_amount"); const pnl = sum(sellRows, "pnl");
  return { month, trading_days: dates.size, estimated, submitted, filled, buy_amount: sum(buyRows, "amount"), sell_count: sellRows.length, fees: sum(sellRows, "fees"), pnl, return_rate: invested ? pnl / invested : 0 };
}

function pnlClass(value) { return Number(value) > 0 ? "pnl-positive" : Number(value) < 0 ? "pnl-negative" : "pnl-flat"; }

function render() {
  const buys = buyDays(); const sells = sellDays(); if (!buys.length && !sells.length) throw new Error("截止日之前没有每日快照");
  const dates = [...new Set([...buys.map((day) => day.date), ...sells.map((day) => day.date)])].sort(); const start = dates[0];
  const summaries = strategyIds.map((id) => strategySummary(buys, sells, id));
  const allBuys = strategyIds.flatMap((id) => strategyBuyRows(buys, id)); const allSells = sells.flatMap((day) => day.records || []);
  const estimated = allBuys.filter((row) => row.data_type === "estimated").length; const actual = allBuys.length - estimated;
  const totalBuyAmount = sum(allBuys, "amount"); const totalFees = sum(allSells, "fees"); const totalPnl = sum(allSells, "pnl"); const invested = sum(allSells, "buy_amount");
  const returnRate = invested ? totalPnl / invested : 0;
  const months = [...new Set(dates.map((date) => date.slice(0, 6)))].sort().reverse().map((month) => monthlySummary(buys, sells, month));
  const optionDates = [...new Set([...(state.buys.days || []).map((day) => day.date), ...(state.sells.days || []).map((day) => day.date)])].sort().reverse();

  byId("endSelect").innerHTML = optionDates.map((date) => `<option value="${date}">${displayDate(date)}</option>`).join(""); byId("endSelect").value = state.activeEnd;
  syncUrl();
  byId("periodRange").textContent = `${displayDate(start)} 至 ${displayDate(state.activeEnd)}`; byId("coverageState").textContent = `累计 ${integer(dates.length)} 个交易日快照`;
  byId("reportVerdict").textContent = totalPnl > 0 ? `盈利 ${money(totalPnl)}` : totalPnl < 0 ? `亏损 ${money(Math.abs(totalPnl))}` : "累计持平";
  byId("reportVerdict").className = pnlClass(totalPnl); byId("reportVerdictNote").textContent = `已卖出收益率 ${(returnRate * 100).toFixed(2)}%，已包含 ${money(totalFees)} 手续费`;
  byId("tradingDays").textContent = integer(dates.length); byId("buyCount").textContent = integer(allBuys.length); byId("buyTypeNote").textContent = `预估${integer(estimated)} / 真实${integer(actual)}`;
  byId("buyAmount").textContent = money(totalBuyAmount); byId("sellCount").textContent = integer(allSells.length); byId("fees").textContent = money(totalFees);
  byId("pnl").textContent = money(totalPnl); byId("pnl").className = pnlClass(totalPnl); byId("returnRate").textContent = `收益率 ${(returnRate * 100).toFixed(2)}%`;
  byId("snapshotTime").textContent = `买入 ${state.buys.generated_at || "-"} · 卖出 ${state.sells.generated_at || "-"}`;
  byId("strategyRows").innerHTML = summaries.map((row) => `<tr><td><span class="strategy-cell"><strong>${escapeHtml(row.name)}</strong><span>${escapeHtml(row.capital)}</span></span></td>
    <td class="signal-cell">${escapeHtml((window.StrategyDescriptions && window.StrategyDescriptions[row.id]) || row.signal)}</td><td class="number">${integer(row.candidate_count)}</td><td class="number">${integer(row.accepted_count)}</td>
    <td class="number">${integer(row.buy_count)}</td><td class="number">${money(row.buy_amount)}</td><td class="number">${integer(row.sell_count)}</td><td class="number">${money(row.fees)}</td>
    <td class="number ${pnlClass(row.pnl)}">${money(row.pnl)}</td></tr>`).join("");
  byId("monthRows").innerHTML = months.map((row) => `<tr><td><strong>${displayMonth(row.month)}</strong></td><td class="number">${integer(row.trading_days)}</td>
    <td class="number">${integer(row.estimated)}</td><td class="number">${integer(row.submitted)}</td><td class="number">${integer(row.filled)}</td><td class="number">${money(row.buy_amount)}</td>
    <td class="number">${integer(row.sell_count)}</td><td class="number">${money(row.fees)}</td><td class="number ${pnlClass(row.pnl)}">${money(row.pnl)}</td>
    <td class="number ${pnlClass(row.return_rate)}">${(row.return_rate * 100).toFixed(2)}%</td></tr>`).join("");
  byId("reportBadge").textContent = estimated && actual ? "预估与真实混合" : estimated ? "历史预估数据" : "真实交易数据";
  byId("loadingState").hidden = true; byId("report").hidden = false; byId("errorState").hidden = true;
}

function bindEvents() {
  byId("endSelect").addEventListener("change", (event) => { state.activeEnd = event.target.value; syncUrl("push"); render(); });
  byId("printButton").addEventListener("click", () => print()); addEventListener("popstate", () => { state.activeEnd = readUrlEnd(); render(); });
}

async function init() {
  try {
    const stamp = Date.now(); const [buyResponse, sellResponse] = await Promise.all([
      fetch(`./daily_buys_snapshot.json?t=${stamp}`, { cache: "no-store" }), fetch(`./daily_sells_snapshot.json?t=${stamp}`, { cache: "no-store" })
    ]);
    if (!buyResponse.ok || !sellResponse.ok) throw new Error(`HTTP buy=${buyResponse.status} sell=${sellResponse.status}`);
    state.buys = await buyResponse.json(); state.sells = await sellResponse.json();
    const dates = [...new Set([...(state.buys.days || []).map((day) => day.date), ...(state.sells.days || []).map((day) => day.date)])].sort();
    const requested = readUrlEnd(); state.activeEnd = dates.includes(requested) ? requested : dates[dates.length - 1]; bindEvents(); render();
  } catch (error) { byId("loadingState").hidden = true; byId("errorState").hidden = false; byId("errorMessage").textContent = `读取每日买入或卖出快照失败：${error.message}`; }
}

init();
