"use strict";

const state = { buySnapshot: null, sellSnapshot: null, activeDate: "" };
const strategyIds = ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11", "s12", "s13"];
const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
const integer = (value) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Number(value || 0));
const money = (value) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(Number(value || 0));
const price = (value) => Number(value || 0) > 0 ? Number(value).toFixed(2) : "-";
const displayDate = (value) => /^\d{8}$/.test(value || "") ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}` : value || "-";
const titleDate = (value) => /^\d{8}$/.test(value || "") ? `${Number(value.slice(4, 6))}月${Number(value.slice(6, 8))}日` : value || "每日";

function readUrlDate() {
  return new URLSearchParams(window.location.search).get("date") || "";
}

function syncUrl(mode = "replace") {
  if (!state.activeDate) return;
  const url = new URL(window.location.href);
  url.searchParams.set("date", state.activeDate);
  const next = `${url.pathname}${url.search}${url.hash}`;
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next === current) return;
  window.history[mode === "push" ? "pushState" : "replaceState"]({ date: state.activeDate }, "", next);
}

function activeBuyDay() {
  return (state.buySnapshot?.days || []).find((day) => day.date === state.activeDate) || null;
}

function candidateRows(day) {
  return day.strategies.flatMap((strategy) => (strategy.candidates || []).map((row) => ({ ...row, strategy_id: strategy.id })));
}

function strategyBuyRows(strategy, day) {
  const reason = strategy.estimated ? "estimated_buy" : "submitted";
  const decisions = (strategy.candidates || []).filter((row) => row.reason === reason);
  if (decisions.length || strategy.estimated) return decisions;
  return (strategy.buys || day.buys || [])
    .filter((row) => !row.strategy_id || row.strategy_id === strategy.id)
    .map((row) => ({ ...row, code: row.code, name: row.name, price: row.buy_price, reason: "submitted" }));
}

function allBuyRows(day) {
  return day.strategies.flatMap((strategy) => strategyBuyRows(strategy, day).map((row) => ({ ...row, strategy_id: strategy.id })));
}

function filterSummary(strategy) {
  if (!(strategy.filters || []).length) return "无";
  return strategy.filters.map((item) => `${escapeHtml(item.label)} ${integer(item.count)}`).join("、");
}

function changeMarkup(row) {
  const stored = Number(row.change_pct);
  const current = Number(row.price || row.buy_price || 0);
  const previous = Number(row.previous_close || 0);
  const value = Number.isFinite(stored) ? stored : (current > 0 && previous > 0 ? (current / previous - 1) * 100 : null);
  if (value === null) return "";
  const tone = value > 0.005 ? "up" : value < -0.005 ? "down" : "flat";
  return `<span class="change-tag ${tone}">买入点较昨收 ${value > 0 ? "+" : ""}${value.toFixed(2)}%</span>`;
}

function buyChips(strategy, day) {
  const rows = strategyBuyRows(strategy, day);
  if (!rows.length) return '<span class="buy-none">无买入股票</span>';
  return `<div class="buy-list">${rows.map((row) => `<span class="buy-chip">${escapeHtml(row.name || row.code)}</span>`).join("")}</div>`;
}

function renderStrategyTable(day) {
  byId("strategyRows").innerHTML = day.strategies.map((strategy) => `
    <tr>
      <td><div class="strategy-cell"><strong>${escapeHtml(strategy.name)}</strong><span>${escapeHtml(strategy.id.toUpperCase())}</span></div></td>
      <td class="signal-cell">${escapeHtml((window.StrategyDescriptions && window.StrategyDescriptions[strategy.id]) || strategy.signal)}</td>
      <td>${escapeHtml(strategy.capital)}</td>
      <td class="number">${integer(strategy.candidate_count)}</td>
      <td class="number">${integer(strategy.accepted_count)}</td>
      <td>${buyChips(strategy, day)}</td>
      <td><span class="filter-tag">${filterSummary(strategy)}</span></td>
    </tr>`).join("");
}

function renderBuyGroups(day) {
  const estimated = day.estimated_strategy_count > 0;
  const buys = allBuyRows(day);
  byId("buySummary").textContent = `${integer(buys.length)}条 · 去重${integer(new Set(buys.map((row) => row.code)).size)}只`;
  byId("buyGroups").innerHTML = day.strategies.map((strategy) => {
    const rows = strategyBuyRows(strategy, day);
    return `<article class="buy-group">
      <div class="buy-group-head"><strong>${escapeHtml(strategy.name)}</strong><span>${integer(rows.length)}只</span></div>
      ${rows.length ? rows.map((row) => `
        <div class="buy-stock">
          <strong>${escapeHtml(row.code)}</strong>
          <span class="stock-name">${escapeHtml(row.name || "")}</span>
          <span class="stock-quote">
            <span class="stock-price">${estimated ? "预计买入点" : "买入价"} ¥${price(row.price || row.buy_price)}</span>
            ${Number(row.previous_close || 0) > 0 ? `<span class="previous-close">昨收 ¥${price(row.previous_close)}</span>` : ""}
            ${changeMarkup(row)}
          </span>
        </div>`).join("") : '<p class="group-empty">当日没有符合条件的买入</p>'}
    </article>`;
  }).join("");
}

function pnlMarkup(value) {
  const amount = Number(value || 0);
  const tone = amount > 0.005 ? "pnl-positive" : amount < -0.005 ? "pnl-negative" : "pnl-flat";
  return `<span class="${tone}">${money(amount)}</span>`;
}

function cohortSellRecords() {
  const records = (state.sellSnapshot?.days || []).flatMap((day) => day.records || []).filter((row) => row.buy_date === state.activeDate);
  return Array.from(new Map(records.map((row) => [row.record_id || `${row.strategy_id}:${row.buy_date}:${row.code}`, row])).values());
}

function strategyMeta() {
  const meta = {};
  (state.buySnapshot?.days || []).forEach((day) => (day.strategies || []).forEach((row) => { meta[row.id] = { name: row.name, signal: row.signal }; }));
  (state.sellSnapshot?.days || []).forEach((day) => (day.strategies || []).forEach((row) => { if (!meta[row.id]) meta[row.id] = { name: row.name, signal: window.StrategyDescriptions?.[row.id] }; }));
  return meta;
}

function renderSells() {
  const rows = cohortSellRecords();
  const actual = rows.filter((row) => row.data_type === "actual" && Number(row.sell_amount || 0) > 0);
  const estimated = rows.filter((row) => row.data_type === "estimated");
  const amount = rows.reduce((sum, row) => sum + Number(row.sell_amount || 0), 0);
  const fees = rows.reduce((sum, row) => sum + Number(row.fees || 0), 0);
  const pnl = rows.reduce((sum, row) => sum + Number(row.pnl || 0), 0);
  const strategies = strategyMeta();
  byId("sellRecordCount").textContent = integer(rows.length);
  byId("actualSoldCount").textContent = integer(actual.length);
  byId("estimatedSoldCount").textContent = integer(estimated.length);
  byId("sellAmount").textContent = money(amount);
  byId("sellPnl").innerHTML = pnlMarkup(pnl);
  byId("sellFeeNote").textContent = `手续费 ${money(fees)}`;
  byId("sellBadge").textContent = rows.length ? `${integer(rows.length)}条 · ${estimated.length ? `预估${integer(estimated.length)}条` : `真实${integer(actual.length)}条`}` : "暂无关联卖出";
  byId("sellUpdatedAt").textContent = `卖出快照 ${state.sellSnapshot?.generated_at || "-"}`;
  byId("sellStrategyRows").innerHTML = strategyIds.map((id) => {
    const selected = rows.filter((row) => row.strategy_id === id);
    const actualCount = selected.filter((row) => row.data_type === "actual" && Number(row.sell_amount || 0) > 0).length;
    const estimatedCount = selected.filter((row) => row.data_type === "estimated").length;
    const strategyFees = selected.reduce((sum, row) => sum + Number(row.fees || 0), 0);
    const strategyPnl = selected.reduce((sum, row) => sum + Number(row.pnl || 0), 0);
    return `<tr>
      <td><strong>${escapeHtml(strategies[id]?.name || `策略${id.slice(1)}`)}</strong></td>
      <td class="signal-cell">${escapeHtml(strategies[id]?.signal || window.StrategyDescriptions?.[id] || "-")}</td>
      <td class="number">${integer(selected.length)}</td>
      <td class="number">${integer(actualCount)}</td>
      <td class="number">${integer(estimatedCount)}</td>
      <td class="number">${money(strategyFees)}</td>
      <td class="number">${pnlMarkup(strategyPnl)}</td>
    </tr>`;
  }).join("");
  byId("sellPositionRows").innerHTML = rows.length ? rows.map((row) => `
    <tr>
      <td><strong>${escapeHtml(row.strategy_name || row.strategy_id)}</strong></td>
      <td><div class="stock-cell"><strong>${escapeHtml(row.code)}</strong><span>${escapeHtml(row.name || "")}</span></div></td>
      <td class="number">${price(row.buy_price)}</td>
      <td class="number">${price(row.target_price)}</td>
      <td class="number"><strong>${price(row.sell_price)}</strong><br><span class="estimated-sell-label ${row.data_type === "actual" ? "done" : ""}">${escapeHtml(row.data_type_label || "卖出")}</span></td>
      <td>${escapeHtml(`${displayDate(row.sell_date)} ${row.sell_time || ""}`)}</td>
      <td><span class="estimated-sell-label ${row.data_type === "actual" ? "done" : ""}">${escapeHtml(row.reason_label || row.status_label || "-")}</span></td>
      <td class="number">${pnlMarkup(row.pnl)}</td>
    </tr>`).join("") : '<tr><td colspan="8" class="sell-loading">该买入日尚无关联卖出记录</td></tr>';
}

function renderDateSelect() {
  const select = byId("dateSelect");
  select.innerHTML = (state.buySnapshot.days || []).map((day) => `<option value="${escapeHtml(day.date)}">${displayDate(day.date)}</option>`).join("");
  select.value = state.activeDate;
}

function render() {
  const day = activeBuyDay();
  if (!day) throw new Error(`快照中没有${displayDate(state.activeDate)}数据`);
  const estimated = day.estimated_strategy_count > 0;
  const candidates = candidateRows(day);
  const buys = allBuyRows(day);
  const uniqueBuys = new Set(buys.map((row) => row.code)).size;
  const nearLimit = candidates.filter((row) => row.reason === "near_limit").length;
  const label = titleDate(day.date);
  document.title = `${displayDate(day.date)} 十三策略每日简报`;
  byId("reportTitle").textContent = `${label}十三策略简报`;
  byId("reportContext").textContent = estimated ? "虚拟 miniQMT · 历史估算" : "虚拟 miniQMT · 自动日报";
  byId("reportBadge").textContent = estimated ? "预测故障补算" : "交易快照";
  byId("reportDate").textContent = displayDate(day.date);
  byId("sourceLabel").textContent = day.estimate_source_label || "15:30自动交易快照";
  byId("reportVerdict").textContent = estimated
    ? `预计可买 ${integer(buys.length)} 条，去重后 ${integer(uniqueBuys)} 只`
    : `买入委托 ${integer(day.submitted_count)} 笔，成交 ${integer(day.filled_count)} 笔`;
  byId("reportVerdictNote").textContent = estimated ? "历史行情估算，没有真实委托或成交" : `成交金额 ${money(day.filled_amount)}`;
  byId("candidateCount").textContent = integer(day.candidate_count);
  byId("uniqueCandidateCount").textContent = `去重${integer(new Set(candidates.map((row) => row.code)).size)}只`;
  byId("acceptedCount").textContent = integer(day.accepted_count);
  byId("buyMetricLabel").textContent = estimated ? "预计买入" : "买入股票";
  byId("buyCount").textContent = integer(buys.length);
  byId("uniqueBuyCount").textContent = `去重${integer(uniqueBuys)}只`;
  byId("nearLimitCount").textContent = integer(nearLimit);
  byId("submittedCount").textContent = integer(day.submitted_count);
  byId("submittedNote").textContent = estimated ? "仅为历史估算" : `成交${integer(day.filled_count)}笔`;
  byId("strategyBuyLabel").textContent = estimated ? "预计买入" : "买入结果";
  byId("buyTitle").textContent = estimated ? "预计买入股票" : "买入股票";
  byId("buyDescription").textContent = estimated ? "同一股票被不同策略选中时分别计入 · 涨幅按14:53价格相对昨收计算" : "同一股票被不同策略买入时分别计入";
  byId("snapshotTime").textContent = `快照 ${state.buySnapshot.generated_at}`;
  byId("reportNote").textContent = estimated
    ? `买入使用Score2排名和${displayDate(day.date)} 14:53历史行情；关联卖出按买入日汇总，所有估算均不会提交真实委托。`
    : `买入来自${displayDate(day.date)}虚拟盘委托与成交；关联卖出按买入日汇总，盈亏包含快照记录的交易手续费。`;
  renderDateSelect();
  renderStrategyTable(day);
  renderBuyGroups(day);
  renderSells();
  byId("loadingState").hidden = true;
  byId("errorState").hidden = true;
  byId("report").hidden = false;
}

async function loadReport() {
  try {
    const stamp = Date.now();
    const [buyResponse, sellResponse] = await Promise.all([
      fetch(`./daily_buys_snapshot.json?t=${stamp}`, { cache: "no-store" }),
      fetch(`./daily_sells_snapshot.json?t=${stamp}`, { cache: "no-store" })
    ]);
    if (!buyResponse.ok) throw new Error(`买入快照 HTTP ${buyResponse.status}`);
    if (!sellResponse.ok) throw new Error(`卖出快照 HTTP ${sellResponse.status}`);
    state.buySnapshot = await buyResponse.json();
    state.sellSnapshot = await sellResponse.json();
    const requested = readUrlDate();
    state.activeDate = (state.buySnapshot.days || []).some((day) => day.date === requested) ? requested : state.buySnapshot.latest_date;
    render();
    syncUrl("replace");
  } catch (error) {
    byId("loadingState").hidden = true;
    byId("report").hidden = true;
    byId("errorMessage").textContent = error.message || String(error);
    byId("errorState").hidden = false;
  }
}

byId("dateSelect").addEventListener("change", (event) => {
  state.activeDate = event.target.value;
  render();
  syncUrl("push");
});
byId("printButton").addEventListener("click", () => window.print());
window.addEventListener("popstate", () => {
  if (!state.buySnapshot) return;
  const requested = readUrlDate();
  state.activeDate = state.buySnapshot.days.some((day) => day.date === requested) ? requested : state.buySnapshot.latest_date;
  render();
  syncUrl("replace");
});

loadReport();
