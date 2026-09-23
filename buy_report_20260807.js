"use strict";

const REPORT_DATE = "20260807";
const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
const integer = (value) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Number(value || 0));
const price = (value) => Number(value || 0) > 0 ? Number(value).toFixed(2) : "-";
const money = (value) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(Number(value || 0));
const changePct = (row) => {
  const stored = Number(row.change_pct);
  if (Number.isFinite(stored)) return stored;
  const current = Number(row.price || 0);
  const previous = Number(row.previous_close || 0);
  return current > 0 && previous > 0 ? (current / previous - 1) * 100 : 0;
};

function changeMarkup(row) {
  const value = changePct(row);
  const tone = value > 0.005 ? "up" : value < -0.005 ? "down" : "flat";
  const sign = value > 0 ? "+" : "";
  return `<span class="change-tag ${tone}">买入点较昨收 ${sign}${value.toFixed(2)}%</span>`;
}

function candidateRows(day) {
  return day.strategies.flatMap((strategy) => (strategy.candidates || []).map((candidate) => ({ ...candidate, strategy_id: strategy.id })));
}

function estimatedBuys(strategy) {
  return (strategy.candidates || []).filter((candidate) => candidate.reason === "estimated_buy");
}

function filterSummary(strategy) {
  if (!(strategy.filters || []).length) return "无";
  return strategy.filters.map((item) => `${escapeHtml(item.label)} ${integer(item.count)}`).join("、");
}

function buyChips(strategy) {
  const rows = estimatedBuys(strategy);
  if (!rows.length) return '<span class="buy-none">无可买股票</span>';
  return `<div class="buy-list">${rows.map((row) => `<span class="buy-chip">${escapeHtml(row.name || row.code)}</span>`).join("")}</div>`;
}

function renderStrategyTable(day) {
  byId("strategyRows").innerHTML = day.strategies.map((strategy) => `
    <tr>
      <td><div class="strategy-cell"><strong>${escapeHtml(strategy.name)}</strong><span>${escapeHtml(strategy.id.toUpperCase())}</span></div></td>
      <td class="signal-cell">${escapeHtml(strategy.signal)}</td>
      <td>${escapeHtml(strategy.capital)}</td>
      <td class="number">${integer(strategy.candidate_count)}</td>
      <td class="number">${integer(strategy.accepted_count)}</td>
      <td>${buyChips(strategy)}</td>
      <td><span class="filter-tag">${filterSummary(strategy)}</span></td>
    </tr>`).join("");
}

function renderBuyGroups(day) {
  const allBuys = day.strategies.flatMap(estimatedBuys);
  byId("buySummary").textContent = `${integer(allBuys.length)}条 · 去重${integer(new Set(allBuys.map((row) => row.code)).size)}只`;
  byId("buyGroups").innerHTML = day.strategies.map((strategy) => {
    const rows = estimatedBuys(strategy);
    return `<article class="buy-group">
      <div class="buy-group-head"><strong>${escapeHtml(strategy.name)}</strong><span>${integer(rows.length)}只</span></div>
      ${rows.length ? rows.map((row) => `
        <div class="buy-stock">
          <strong>${escapeHtml(row.code)}</strong>
          <span class="stock-name">${escapeHtml(row.name || "")}</span>
          <span class="stock-quote">
            <span class="stock-price">预计买入点 ¥${price(row.price)}</span>
            <span class="previous-close">昨收 ¥${price(row.previous_close)}</span>
            ${changeMarkup(row)}
          </span>
        </div>`).join("") : '<p class="group-empty">候选均在涨停附近，不买入</p>'}
    </article>`;
  }).join("");
}

function render(day) {
  const candidates = candidateRows(day);
  const buys = candidates.filter((row) => row.reason === "estimated_buy");
  const nearLimit = candidates.filter((row) => row.reason === "near_limit");
  const uniqueBuyCount = new Set(buys.map((row) => row.code)).size;
  byId("sourceLabel").textContent = day.estimate_source_label || "按14:53历史行情估算";
  byId("reportVerdict").textContent = `预计可买 ${integer(buys.length)} 条，去重后 ${integer(uniqueBuyCount)} 只`;
  byId("candidateCount").textContent = integer(day.candidate_count);
  byId("uniqueCandidateCount").textContent = `去重${integer(new Set(candidates.map((row) => row.code)).size)}只`;
  byId("acceptedCount").textContent = integer(day.accepted_count);
  byId("estimatedBuyCount").textContent = integer(buys.length);
  byId("uniqueBuyCount").textContent = `去重${integer(uniqueBuyCount)}只`;
  byId("nearLimitCount").textContent = integer(nearLimit.length);
  byId("submittedCount").textContent = integer(day.submitted_count);
  renderStrategyTable(day);
  renderBuyGroups(day);
  byId("loadingState").hidden = true;
  byId("report").hidden = false;
}

function pnlMarkup(value) {
  const amount = Number(value || 0);
  const tone = amount > 0.005 ? "pnl-positive" : amount < -0.005 ? "pnl-negative" : "pnl-flat";
  return `<span class="${tone}">${money(amount)}</span>`;
}

function renderEstimatedSells(snapshot) {
  const summary = snapshot.summary || {};
  const positions = Object.values(snapshot.positions || {});
  byId("sellPositionCount").textContent = integer(summary.position_count);
  byId("sellTrackingCount").textContent = integer(summary.tracking_count);
  byId("estimatedSoldCount").textContent = integer(summary.estimated_sold_count);
  byId("estimatedRealizedPnl").innerHTML = pnlMarkup(summary.estimated_realized_pnl);
  byId("realSellOrderCount").textContent = integer(summary.orders_submitted);
  byId("sellEstimateBadge").textContent = `预估卖出 ${integer(summary.estimated_sold_count)}/${integer(summary.position_count)} · 真实委托0`;
  byId("sellUpdatedAt").textContent = `预估卖出状态更新 ${escapeHtml(snapshot.updated_at || "-")}`;
  const strategies = summary.strategies || {};
  byId("sellStrategyRows").innerHTML = ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11", "s12", "s13"].map((id) => {
    const row = strategies[id] || {};
    return `<tr>
      <td><strong>${escapeHtml(row.strategy_name || id.toUpperCase())}</strong></td>
      <td class="number">${integer(row.position_count)}</td>
      <td class="number">${integer(row.tracking_count)}</td>
      <td class="number">${integer(row.estimated_sold_count)}</td>
      <td class="number">${pnlMarkup(row.estimated_floating_pnl)}</td>
      <td class="number">${pnlMarkup(row.estimated_realized_pnl)}</td>
      <td><span class="estimated-sell-label ${Number(row.tracking_count || 0) ? "" : "done"}">${escapeHtml(row.status_label || "无预计买入持仓")}</span></td>
    </tr>`;
  }).join("");
  byId("sellPositionRows").innerHTML = positions.length ? positions.map((row) => {
    const sold = row.status === "estimated_sold";
    const quote = sold ? row.estimated_sell_price : row.latest_price;
    const time = sold ? `${row.estimated_sell_date || ""} ${row.estimated_sell_time || ""}` : "仍在预估跟踪";
    const reason = sold ? row.estimated_sell_reason_label : "尚未触发预估卖出";
    const pnl = sold ? row.estimated_pnl : (Number(row.latest_price || 0) - Number(row.buy_price || 0)) * Number(row.shares || 0);
    return `<tr>
      <td><strong>${escapeHtml(row.strategy_name)}</strong></td>
      <td><div class="stock-cell"><strong>${escapeHtml(row.code)}</strong><span>${escapeHtml(row.name || "")}</span></div></td>
      <td class="number">${price(row.buy_price)}</td>
      <td class="number">${price(row.target_price)}</td>
      <td class="number"><strong>${price(quote)}</strong><br><span class="estimated-sell-label ${sold ? "done" : ""}">${sold ? "预估卖出价" : "最新行情"}</span></td>
      <td>${escapeHtml(time)}</td>
      <td><span class="estimated-sell-label ${sold ? "done" : ""}">${escapeHtml(reason)}</span></td>
      <td class="number">${pnlMarkup(pnl)}</td>
    </tr>`;
  }).join("") : '<tr><td colspan="8" class="sell-loading">没有预计买入形成的虚拟持仓</td></tr>';
}

async function loadEstimatedSells() {
  try {
    const response = await fetch(`./estimated_sell_snapshot.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderEstimatedSells(await response.json());
  } catch (error) {
    byId("sellEstimateBadge").textContent = "预估卖出进程尚未发布状态";
    byId("sellPositionRows").innerHTML = `<tr><td colspan="8" class="sell-loading">${escapeHtml(error.message || String(error))}</td></tr>`;
  }
}

async function loadReport() {
  try {
    const response = await fetch(`./daily_buys_snapshot.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const snapshot = await response.json();
    const day = (snapshot.days || []).find((item) => item.date === REPORT_DATE);
    if (!day) throw new Error("快照中没有2026-08-07数据");
    render(day);
    await loadEstimatedSells();
  } catch (error) {
    byId("loadingState").hidden = true;
    byId("errorMessage").textContent = error.message || String(error);
    byId("errorState").hidden = false;
  }
}

loadReport();
setInterval(loadEstimatedSells, 15000);
