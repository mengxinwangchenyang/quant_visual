"use strict";

window.SellPnlTrend = (() => {
  let resizeObserver = null;
  let resizeFrame = 0;

  const wan = (value, signed = true) => {
    const amount = Number(value || 0) / 10000;
    const normalized = Math.abs(amount) < .005 ? 0 : amount;
    return `${signed && normalized > 0 ? "+" : ""}${normalized.toFixed(2)}w`;
  };
  const percent = (value) => `${Number(value || 0) >= 0 ? "+" : ""}${(Number(value || 0) * 100).toFixed(2)}%`;
  const displayDate = (value) => /^\d{8}$/.test(value || "") ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}` : value || "-";

  function destroy() {
    resizeObserver?.disconnect();
    resizeObserver = null;
    cancelAnimationFrame(resizeFrame);
    resizeFrame = 0;
  }

  function aggregate(rows) {
    const grouped = new Map();
    rows.forEach((row) => {
      const date = String(row.sell_date || row.date || "");
      if (!/^\d{8}$/.test(date)) return;
      const current = grouped.get(date) || { date, pnl: 0, buyBase: 0, records: 0 };
      current.pnl += Number(row.pnl || 0);
      current.buyBase += Number(row.buy_amount || 0) + Number(row.buy_fee || 0);
      current.records += 1;
      grouped.set(date, current);
    });
    return [...grouped.values()].sort((a, b) => a.date.localeCompare(b.date)).map((row) => ({ ...row, rate: row.buyBase ? row.pnl / row.buyBase : 0 }));
  }

  function paddedRange(values) {
    let min = Math.min(0, ...values);
    let max = Math.max(0, ...values);
    if (min === max) { min -= 1; max += 1; }
    const padding = (max - min) * .08;
    return { min: min - padding, max: max + padding };
  }

  function render(container, rows, options = {}) {
    destroy();
    const daily = aggregate(rows);
    if (!daily.length) {
      container.innerHTML = '<div class="sell-trend-empty">当前策略在该区间没有卖出记录</div>';
      return;
    }

    let lastWidth = 0;
    const draw = () => {
      const viewportWidth = Math.max(320, Math.floor(container.clientWidth || 320));
      if (viewportWidth === lastWidth && container.querySelector("svg")) return;
      lastWidth = viewportWidth;
      const compact = viewportWidth <= 640;
      const height = compact ? 300 : 340;
      const margin = { top: 18, right: compact ? 54 : 68, bottom: 38, left: compact ? 58 : 72 };
      const width = compact ? Math.max(viewportWidth, margin.left + margin.right + daily.length * 66) : viewportWidth;
      const plotWidth = width - margin.left - margin.right;
      const plotHeight = height - margin.top - margin.bottom;
      const profitRange = paddedRange(daily.map((row) => row.pnl));
      const rateRange = paddedRange(daily.map((row) => row.rate));
      const x = (index) => margin.left + plotWidth * (index + .5) / daily.length;
      const y = (value, range) => margin.top + (range.max - value) / (range.max - range.min) * plotHeight;
      const zeroY = y(0, profitRange);
      const barWidth = Math.max(2, Math.min(46, plotWidth / daily.length * .58));
      const labelStep = Math.max(1, Math.ceil(daily.length / Math.max(2, Math.floor(plotWidth / 64))));
      const labelIndexes = new Set([0, daily.length - 1]);
      for (let index = labelStep; index < daily.length - 1 - labelStep / 2; index += labelStep) labelIndexes.add(index);
      const strategy = encodeURIComponent(options.strategy || "all");

      const grid = Array.from({ length: 5 }, (_, index) => {
        const ratio = index / 4;
        const gridY = margin.top + ratio * plotHeight;
        const profitTick = profitRange.max - ratio * (profitRange.max - profitRange.min);
        const rateTick = rateRange.max - ratio * (rateRange.max - rateRange.min);
        return `<line class="sell-trend-grid" x1="${margin.left}" y1="${gridY}" x2="${width - margin.right}" y2="${gridY}"/><text class="sell-trend-axis left" x="${margin.left - 8}" y="${gridY + 4}">${wan(profitTick, false)}</text><text class="sell-trend-axis right" x="${width - margin.right + 8}" y="${gridY + 4}">${percent(rateTick)}</text>`;
      }).join("");
      const dates = daily.map((row, index) => labelIndexes.has(index) ? `<text class="sell-trend-date" x="${x(index)}" y="${height - 12}">${row.date.slice(4, 6)}-${row.date.slice(6, 8)}</text>` : "").join("");
      const bars = daily.map((row, index) => {
        const valueY = y(row.pnl, profitRange);
        const top = Math.min(valueY, zeroY);
        const barHeight = Math.max(1, Math.abs(valueY - zeroY));
        const href = `./daily_sells.html?date=${row.date}&strategy=${strategy}`;
        return `<a href="${href}" data-trend-index="${index}"><rect class="sell-trend-bar ${row.pnl >= 0 ? "up" : "down"}" x="${x(index) - barWidth / 2}" y="${top}" width="${barWidth}" height="${barHeight}" rx="2"><title>${displayDate(row.date)} 利润金额 ${wan(row.pnl)}</title></rect></a>`;
      }).join("");
      const linePath = daily.map((row, index) => `${index ? "L" : "M"}${x(index)} ${y(row.rate, rateRange)}`).join(" ");
      const points = daily.map((row, index) => {
        const href = `./daily_sells.html?date=${row.date}&strategy=${strategy}`;
        const pointY = y(row.rate, rateRange);
        const hitRadius = Math.max(6, Math.min(11, plotWidth / daily.length / 2));
        return `<a href="${href}" data-trend-index="${index}"><circle class="sell-trend-point-hit" cx="${x(index)}" cy="${pointY}" r="${hitRadius}"/><circle class="sell-trend-point" cx="${x(index)}" cy="${pointY}" r="4"><title>${displayDate(row.date)} 利润率 ${percent(row.rate)}</title></circle></a>`;
      }).join("");
      const valueLabels = daily.map((row, index) => {
        const valueY = y(row.pnl, profitRange);
        const labelTop = row.pnl >= 0
          ? Math.max(2, valueY - 32)
          : Math.min(height - margin.bottom - 29, valueY + 5);
        const href = `./daily_sells.html?date=${row.date}&strategy=${strategy}`;
        return `<a href="${href}" data-trend-index="${index}" aria-label="${displayDate(row.date)}，利润金额 ${wan(row.pnl)}，利润率 ${percent(row.rate)}"><g class="sell-trend-value-label ${row.pnl >= 0 ? "up" : "down"}"><rect x="${x(index) - 28}" y="${labelTop}" width="56" height="27" rx="3"/><text class="amount" x="${x(index)}" y="${labelTop + 10}">${wan(row.pnl)}</text><text class="rate" x="${x(index)}" y="${labelTop + 22}">${percent(row.rate)}</text></g></a>`;
      }).join("");

      container.innerHTML = `<svg class="sell-trend-svg" viewBox="0 0 ${width} ${height}" style="width:${width}px" role="img" aria-label="每日利润金额与利润率趋势图">
        ${grid}<line class="sell-trend-zero" x1="${margin.left}" y1="${zeroY}" x2="${width - margin.right}" y2="${zeroY}"/>
        ${bars}<path class="sell-trend-rate-line" d="${linePath}"/>${points}${valueLabels}${dates}
      </svg><div class="sell-trend-tooltip" hidden></div>`;

      const tooltip = container.querySelector(".sell-trend-tooltip");
      container.querySelectorAll("a[data-trend-index]").forEach((anchor) => {
        const row = daily[Number(anchor.dataset.trendIndex)];
        const show = (event) => {
          tooltip.innerHTML = `<strong>${displayDate(row.date)}</strong><span class="${row.pnl >= 0 ? "up" : "down"}">利润金额 ${wan(row.pnl)}</span><span>利润率 ${percent(row.rate)}</span><small>${row.records} 条卖出记录</small>`;
          tooltip.hidden = false;
          const bounds = container.getBoundingClientRect();
          const pointerX = container.scrollLeft + event.clientX - bounds.left;
          const minLeft = container.scrollLeft + 8;
          const maxLeft = container.scrollLeft + container.clientWidth - tooltip.offsetWidth - 8;
          const left = Math.max(minLeft, Math.min(pointerX + 12, maxLeft));
          const top = Math.max(8, Math.min(event.clientY - bounds.top - tooltip.offsetHeight / 2, height - tooltip.offsetHeight - 8));
          tooltip.style.transform = `translate(${left}px, ${top}px)`;
        };
        anchor.addEventListener("pointerenter", show);
        anchor.addEventListener("pointermove", show);
        anchor.addEventListener("pointerleave", () => { tooltip.hidden = true; });
      });
    };

    draw();
    resizeObserver = new ResizeObserver(() => {
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(draw);
    });
    resizeObserver.observe(container);
  }

  return { render, destroy };
})();
