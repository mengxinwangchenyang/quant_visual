"use strict";

window.SellStatistics = (() => {
  const strategyIds = ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11", "s12", "s13"];
  const takeProfitReasons = new Set(["t", "take_profit"]);
  const forcedReasons = new Set(["f", "e", "next_day_force", "expiry"]);
  const integer = (value) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(Number(value || 0));
  const percent = (value, count) => count ? `${(Number(value || 0) * 100).toFixed(2)}%` : "-";
  const boardLabels = { star: "科创板", gem: "创业板" };
  const strategyCapital = { s1: 500000, s2: 500000, s3: 500000, s4: 200000, s5: 500000, s6: 200000, s7: 200000 };
  function capitalOf(ids) {
    if (Array.isArray(ids)) return ids.reduce((total, id) => total + (strategyCapital[id] || 0), 0);
    return strategyCapital[ids] || 0;
  }
  function boardOf(code) {
    const value = String(code || "").trim().toUpperCase();
    if (value.startsWith("688") || value.startsWith("689")) return "star";
    if (value.startsWith("300") || value.startsWith("301")) return "gem";
    return "";
  }
  function boardLabel(code) { return boardLabels[boardOf(code)] || ""; }

  function completedRows(rows) {
    return rows.filter((row) => row.data_type === "estimated" || Number(row.sell_amount || 0) > 0);
  }

  function fallbackHoldingDays(row) {
    if (!/^\d{8}$/.test(row.buy_date || "") || !/^\d{8}$/.test(row.sell_date || row.date || "")) return 0;
    const start = new Date(`${row.buy_date.slice(0, 4)}-${row.buy_date.slice(4, 6)}-${row.buy_date.slice(6, 8)}T12:00:00+08:00`);
    const value = String(row.sell_date || row.date);
    const end = new Date(`${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}T12:00:00+08:00`);
    let count = 0;
    for (const cursor = new Date(start.getTime() + 86400000); cursor <= end; cursor.setDate(cursor.getDate() + 1)) {
      const weekday = cursor.getDay();
      if (weekday !== 0 && weekday !== 6) count += 1;
    }
    return count;
  }

  function holdingDays(row) {
    const stored = Number(row.holding_trading_days);
    return Number.isFinite(stored) && stored >= 0 ? stored : fallbackHoldingDays(row);
  }

  function summarize(rows) {
    const sold = completedRows(rows);
    const count = sold.length;
    const wins = sold.filter((row) => Number(row.pnl || 0) > 0).length;
    const forced = sold.filter((row) => forcedReasons.has(String(row.reason || ""))).length;
    const takeProfit = sold.filter((row) => takeProfitReasons.has(String(row.reason || ""))).length;
    const holdingTotal = sold.reduce((total, row) => total + holdingDays(row), 0);
    const starCount = sold.filter((row) => boardOf(row.code) === "star").length;
    const gemCount = sold.filter((row) => boardOf(row.code) === "gem").length;
    const mainCount = count - starCount - gemCount;
    const mainWins = sold.filter((row) => boardOf(row.code) === "" && Number(row.pnl || 0) > 0).length;
    return { count, winRate: count ? wins / count : 0, averageHolding: count ? holdingTotal / count : 0, forcedRate: count ? forced / count : 0, takeProfitRate: count ? takeProfit / count : 0,
      starRate: count ? starCount / count : 0, gemRate: count ? gemCount / count : 0, mainRate: count ? mainCount / count : 0, mainCount, mainWinRate: mainCount ? mainWins / mainCount : 0 };
  }

  function render(tbody, rows, options = {}) {
    const labels = options.strategyLabels || {};
    const active = options.activeStrategy || "all";
    const summaries = [
      { id: "all", label: "综合", ...summarize(rows) },
      ...strategyIds.map((id) => ({ id, label: labels[id] || `策略${id.slice(1)}`, ...summarize(rows.filter((row) => row.strategy_id === id)) }))
    ];
    tbody.innerHTML = summaries.map((row) => `<tr class="${row.id === "all" ? "comprehensive" : ""}${row.id === active ? " active-stat" : ""}">
      <td><strong>${row.label}</strong>${row.id === "all" ? "<span>全部策略</span>" : ""}</td>
      <td class="number">${integer(row.count)}</td>
      <td class="number ${row.winRate >= .5 && row.count ? "pnl-positive" : row.count ? "pnl-negative" : "pnl-flat"}">${percent(row.winRate, row.count)}</td>
      <td class="number">${row.count ? `${row.averageHolding.toFixed(2)}天` : "-"}</td>
      <td class="number">${percent(row.forcedRate, row.count)}</td>
      <td class="number">${percent(row.takeProfitRate, row.count)}</td>
    </tr>`).join("");
  }

  function cells(summary) {
    return `<td class="number ${summary.winRate >= .5 && summary.count ? "pnl-positive" : summary.count ? "pnl-negative" : "pnl-flat"}">${percent(summary.winRate, summary.count)}</td>
      <td class="number">${percent(summary.mainWinRate, summary.mainCount)}</td>
      <td class="number">${summary.count ? `${summary.averageHolding.toFixed(2)}天` : "-"}</td>
      <td class="number">${percent(summary.forcedRate, summary.count)}</td>
      <td class="number">${percent(summary.takeProfitRate, summary.count)}</td>
      <td class="number">${percent(summary.starRate, summary.count)}</td>
      <td class="number">${percent(summary.gemRate, summary.count)}</td>
      <td class="number">${percent(summary.mainRate, summary.count)}</td>`;
  }

  return { render, summarize, cells, boardOf, boardLabel, capitalOf };
})();
