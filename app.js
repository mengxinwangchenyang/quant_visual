const data = window.tradeDashboardData || {}; // 读取交易快照数据，缺失时使用空对象。
const yuan = (value) => `¥${Number(value || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; // 把数字格式化为人民币金额。
const num = (value) => Number(value || 0).toLocaleString("zh-CN"); // 把数字格式化为中文千分位。
const pct = (value) => `${(Number(value || 0) * 100).toFixed(2)}%`; // 把小数收益率格式化为百分比。
const signed = (value) => `${Number(value || 0) >= 0 ? "+" : ""}${yuan(value)}`; // 把盈亏金额格式化为带正负号的文本。
const signedPct = (value) => `${Number(value || 0) >= 0 ? "+" : ""}${pct(value)}`; // 把收益率格式化为带正负号的百分比。
const pctWidth = (value, max) => `${Math.max(4, Math.min(100, Math.round(Math.abs(Number(value || 0)) / Math.max(1, max) * 100)))}%`; // 按最大值计算条形宽度。
const query = (selector) => document.querySelector(selector); // 封装单元素查询函数。
let activeTimelineBatch = "全部"; // 记录当前时间轴筛选批次。
let activeReviewBatch = (((data.batches || [])[0] || {}).name) || ""; // 记录当前复盘小图展示的批次，默认显示最新买入日批次。
const toneClass = (value) => Number(value || 0) >= 0 ? "text-emerald-700" : "text-rose-700"; // 根据盈亏正负返回文字颜色。
const barClass = (value) => Number(value || 0) >= 0 ? "bg-emerald-500" : "bg-rose-500"; // 根据盈亏正负返回条形颜色。
const statusClass = (status) => ({ closed: "bg-emerald-50 text-emerald-700 border-emerald-200", sell_submitted: "bg-amber-50 text-amber-700 border-amber-200", sell_partial: "bg-amber-50 text-amber-700 border-amber-200", open: "bg-sky-50 text-sky-700 border-sky-200", buy_submitted: "bg-rose-50 text-rose-700 border-rose-200", not_bought: "bg-slate-50 text-slate-600 border-slate-200" }[status] || "bg-slate-50 text-slate-600 border-slate-200"); // 根据计划状态返回标签颜色。
const statusText = (status) => ({ closed: "已卖出", sell_submitted: "卖出挂单", sell_partial: "部分卖出", open: "仍持仓", buy_submitted: "买入未成", not_bought: "未买入" }[status] || status || "-"); // 把计划状态转换为中文展示。
const orderStatusClass = (status) => status === "已成" ? "bg-emerald-50 text-emerald-700 border-emerald-200" : status === "已报" ? "bg-amber-50 text-amber-700 border-amber-200" : status === "已撤" ? "bg-slate-50 text-slate-600 border-slate-200" : "bg-rose-50 text-rose-700 border-rose-200"; // 根据委托状态返回标签颜色。
const codePalette = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#be123c", "#4f46e5", "#65a30d", "#c026d3", "#0f766e", "#d97706", "#475569", "#0284c7", "#7c3aed", "#db2777", "#059669", "#b45309", "#1d4ed8", "#b91c1c", "#15803d", "#6d28d9", "#0e7490", "#a16207"]; // 定义股票代码稳定配色板。
const eventStatusClass = (status) => status === "已成" || status === "submitted" ? "bg-emerald-50 text-emerald-700 border-emerald-200" : status === "已撤" || status === "cancel_sent" ? "bg-slate-50 text-slate-600 border-slate-200" : String(status || "").includes("reject") || String(status || "").includes("failed") ? "bg-rose-50 text-rose-700 border-rose-200" : "bg-amber-50 text-amber-700 border-amber-200"; // 根据事件状态返回标签颜色。
function colorForCode(code) { // 定义按股票代码生成稳定颜色的函数。
  const text = String(code || ""); // 把股票代码转成文本。
  const hash = Array.from(text).reduce((sum, char, index) => sum + char.charCodeAt(0) * (index + 3), 0); // 计算一个简单稳定的字符哈希。
  return codePalette[Math.abs(hash) % codePalette.length]; // 用哈希结果从配色板中取颜色。
} // 结束股票代码稳定配色函数。
function escapeHtml(value) { // 定义 HTML 转义函数。
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])); // 返回安全的 HTML 文本。
} // 结束 HTML 转义函数。
function metricCard(label, value, subtext, tone) { // 定义生成顶部指标卡片的函数。
  const colors = { neutral: "border-slate-200 bg-white", green: "border-emerald-200 bg-emerald-50", amber: "border-amber-200 bg-amber-50", rose: "border-rose-200 bg-rose-50", sky: "border-sky-200 bg-sky-50" }; // 定义指标卡片色彩映射。
  return [`<section class="rounded-lg border ${colors[tone] || colors.neutral} p-4 shadow-sm">`, `<p class="text-xs font-medium uppercase tracking-wide text-slate-500">${label}</p>`, `<p class="mt-2 text-2xl font-semibold text-slate-950">${value}</p>`, `<p class="mt-1 text-sm text-slate-500">${subtext}</p>`, `</section>`].join(""); // 返回指标卡片 HTML。
} // 结束指标卡片函数。
function renderSummary() { // 定义渲染账户总览的函数。
  const account = data.account || {}; // 读取账户总览数据。
  query("#generatedAt").textContent = `数据时间：${data.generated_at || "未知"}`; // 渲染数据生成时间。
  query("#processState").textContent = (data.process_ids || []).length ? `交易进程运行中：PID ${(data.process_ids || []).join(", ")}` : "交易进程未运行"; // 渲染交易进程状态。
  query("#summaryCards").innerHTML = [metricCard("总资产", yuan(account.total_asset), "账户权益总览", "green"), metricCard("现金", yuan(account.cash), "可用资金口径", "neutral"), metricCard("持仓市值", yuan(account.market_value), `${num(account.position_count)} 只非零持仓`, "sky"), metricCard("冻结资金", yuan(account.frozen_cash), `${num(account.order_count)} 笔今日委托`, Number(account.frozen_cash || 0) > 0 ? "amber" : "neutral")].join(""); // 渲染顶部指标卡片。
} // 结束账户总览渲染函数。
function renderStrategies() { // 渲染五套内部策略资金账本。
  query("#strategyCards").innerHTML = (data.strategies || []).map((row) => [`<article class="rounded border border-slate-200 bg-slate-50 p-4">`, `<div class="flex items-start justify-between gap-2"><h3 class="text-sm font-semibold text-slate-950">${escapeHtml(row.name)}</h3><span class="text-sm font-semibold ${toneClass(row.total_pnl)}">${signedPct(row.return_rate)}</span></div>`, `<p class="mt-3 text-xl font-semibold ${toneClass(row.total_pnl)}">${signed(row.total_pnl)}</p>`, `<dl class="mt-3 grid grid-cols-2 gap-2 text-xs">`, `<div><dt class="text-slate-500">策略权益</dt><dd class="font-medium text-slate-900">${yuan(row.equity)}</dd></div>`, `<div><dt class="text-slate-500">可用现金</dt><dd class="font-medium text-slate-900">${yuan(row.available_cash)}</dd></div>`, `<div><dt class="text-slate-500">持仓市值</dt><dd class="font-medium text-slate-900">${yuan(row.market_value)}</dd></div>`, `<div><dt class="text-slate-500">持仓/挂单</dt><dd class="font-medium text-slate-900">${num(row.open_count)} / ${num(row.pending_count)}</dd></div>`, `</dl>`, `</article>`].join("")).join("");
} // 结束七策略资金账本渲染。
function batchCard(batch, maxAbs) { // 定义生成批次汇总卡片的函数。
  const summary = batch.summary || {}; // 读取批次汇总数据。
  const pending = (summary.pending_codes || []).length ? summary.pending_codes.join("、") : "无"; // 生成挂单代码文本。
  return [`<section class="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">`, `<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">`, `<div>`, `<p class="text-sm font-medium text-slate-500">${batch.name}</p>`, `<p class="mt-1 text-3xl font-semibold ${toneClass(summary.total_result)}">${signed(summary.total_result)}</p>`, `<p class="mt-1 text-sm font-medium ${toneClass(summary.return_rate)}">${signedPct(summary.return_rate)}</p>`, `</div>`, `<div class="grid grid-cols-3 gap-2 text-center text-xs text-slate-500">`, `<div><p class="text-lg font-semibold text-slate-900">${num(summary.filled_count)}</p><p>买入成交</p></div>`, `<div><p class="text-lg font-semibold text-slate-900">${num(summary.closed_count)}</p><p>今日已卖</p></div>`, `<div><p class="text-lg font-semibold text-slate-900">${num(summary.open_count)}</p><p>仍持仓</p></div>`, `</div>`, `</div>`, `<div class="mt-5 h-3 overflow-hidden rounded bg-slate-100">`, `<div class="h-full ${barClass(summary.total_result)}" style="width:${pctWidth(summary.total_result, maxAbs)}"></div>`, `</div>`, `<dl class="mt-5 grid grid-cols-2 gap-3 text-sm md:grid-cols-3">`, `<div><dt class="text-slate-500">预测选中</dt><dd class="font-medium text-slate-950">${num(summary.stock_count)}</dd></div>`, `<div><dt class="text-slate-500">买入未成</dt><dd class="font-medium text-slate-950">${num(summary.buy_unfilled_count)}</dd></div>`, `<div><dt class="text-slate-500">买入成本</dt><dd class="font-medium text-slate-950">${yuan(summary.buy_cost)}</dd></div>`, `<div><dt class="text-slate-500">卖出金额</dt><dd class="font-medium text-slate-950">${yuan(summary.sell_amount)}</dd></div>`, `<div><dt class="text-slate-500">剩余市值</dt><dd class="font-medium text-slate-950">${yuan(summary.remaining_market)}</dd></div>`, `<div><dt class="text-slate-500">已实现</dt><dd class="font-medium ${toneClass(summary.realized_pnl)}">${signed(summary.realized_pnl)}</dd></div>`, `<div><dt class="text-slate-500">浮动盈亏</dt><dd class="font-medium ${toneClass(summary.unrealized_pnl)}">${signed(summary.unrealized_pnl)}</dd></div>`, `<div><dt class="text-slate-500">挂单代码</dt><dd class="font-medium text-slate-950">${pending}</dd></div>`, `</dl>`, `</section>`].join(""); // 返回批次汇总卡片 HTML。
} // 结束批次汇总卡片函数。
function renderBatches() { // 定义渲染批次汇总的函数。
  const batches = data.batches || []; // 读取批次数组。
  const maxAbs = Math.max(1, ...batches.map((batch) => Math.abs(Number((batch.summary || {}).total_result || 0)))); // 计算最大绝对盈亏用于条形图比例。
  query("#batchCards").innerHTML = batches.map((batch) => batchCard(batch, maxAbs)).join(""); // 渲染两批汇总卡片。
} // 结束批次汇总渲染函数。
function reviewBatch() { // 定义读取当前复盘批次的函数。
  const batches = data.batches || []; // 读取批次数组。
  return batches.find((batch) => batch.name === activeReviewBatch) || batches[0] || { name: "", summary: {}, stock_charts: [] }; // 返回当前选中批次，缺失时兜底第一批。
} // 结束当前复盘批次读取函数。
function reviewButton(label) { // 定义生成批次复盘切换按钮的函数。
  const active = activeReviewBatch === label; // 判断当前按钮是否激活。
  const classes = active ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 bg-white text-slate-700 hover:border-slate-400"; // 根据激活状态选择按钮样式。
  return `<button type="button" data-review-batch="${escapeHtml(label)}" class="rounded border px-3 py-1.5 text-sm font-medium ${classes}">${escapeHtml(label)}</button>`; // 返回复盘切换按钮 HTML。
} // 结束复盘切换按钮函数。
function renderReviewFilters() { // 定义渲染批次复盘切换按钮的函数。
  const labels = (data.batches || []).map((batch) => batch.name); // 读取所有批次名称。
  const container = query("#batchReviewFilters"); // 获取批次复盘切换按钮容器。
  container.innerHTML = labels.map(reviewButton).join(""); // 写入批次切换按钮。
  container.querySelectorAll("button").forEach((button) => button.addEventListener("click", () => { activeReviewBatch = button.dataset.reviewBatch; renderBatchReviewContent(); updateReviewFilterState(); })); // 绑定点击切换批次并只重绘复盘内容和按钮状态。
} // 结束批次复盘切换按钮渲染函数。
function updateReviewFilterState() { // 定义更新批次复盘按钮激活状态的函数。
  query("#batchReviewFilters").querySelectorAll("button").forEach((button) => { const active = activeReviewBatch === button.dataset.reviewBatch; button.className = `rounded border px-3 py-1.5 text-sm font-medium ${active ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 bg-white text-slate-700 hover:border-slate-400"}`; }); // 按当前复盘批次更新按钮样式。
} // 结束批次复盘按钮状态更新函数。
function timeMs(value) { // 定义把前端时间文本转成毫秒时间戳的函数。
  const date = eventDate(value); // 复用事件时间解析函数。
  return Number.isNaN(date.getTime()) ? 0 : date.getTime(); // 返回有效毫秒值，无效时返回零。
} // 结束时间戳转换函数。
function compactDayLabel(value) { // 定义把 YYYYMMDD 日期转成短日期标签的函数。
  const text = String(value || ""); // 把日期值转成文本。
  return text.length === 8 ? `${text.slice(4, 6)}/${text.slice(6, 8)}` : text; // 返回 MM/DD 文本，格式不符时保留原文。
} // 结束短日期标签函数。
function pointTitle(chart, point) { // 定义生成行情点悬停提示的函数。
  return escapeHtml(`${chart.code} ${point.time} 价格${Number(point.price || 0).toFixed(2)} 涨幅${signedPct(point.rise_rate)} 昨收${Number(point.last_close || 0).toFixed(2)} 成交量${num(point.volume)}`); // 返回包含相对昨收涨幅的提示文本。
} // 结束行情点悬停提示函数。
function markerTitle(chart, label, timeText, price, nearby) { // 定义生成买卖标记悬停提示的函数。
  return escapeHtml(`${chart.code} ${label} ${timeText} 委托/成交价${Number(price || 0).toFixed(2)} 最近点${nearby.time} 点价${Number(nearby.price || 0).toFixed(2)} 相对昨收涨幅${signedPct(nearby.rise_rate)} 昨收${Number(nearby.last_close || 0).toFixed(2)} 成交量${num(nearby.volume)}`); // 返回包含涨幅和成交量的买卖标记提示。
} // 结束买卖标记提示函数。
function nearestPoint(points, targetTime) { // 定义查找最接近目标时间行情点的函数。
  if (!points.length || !targetTime) { // 判断行情点或目标时间是否缺失。
    return null; // 缺少必要数据时返回空。
  } // 结束空数据判断。
  return points.reduce((best, point) => Math.abs(timeMs(point.time) - targetTime) < Math.abs(timeMs(best.time) - targetTime) ? point : best, points[0]); // 返回距离目标时间最近的行情点。
} // 结束最近行情点查找函数。
function chartMarker(kind, chart, xFor, yFor, points, color) { // 定义生成买卖标记的函数。
  const timeText = kind === "buy" ? chart.buy_time : chart.sell_time; // 按类型读取买入或卖出时间。
  const price = Number(kind === "buy" ? chart.buy_price : chart.sell_price || chart.target_price); // 按类型读取买入或卖出价格。
  const target = timeMs(timeText); // 把目标时间转换为毫秒。
  const nearby = nearestPoint(points, target); // 查找离买卖时间最近的行情点。
  if (!target || !price || !nearby) { // 判断标记数据是否完整。
    return ""; // 数据不完整时不绘制标记。
  } // 结束标记数据判断。
  const x = xFor(timeMs(nearby.time)); // 用最近行情点时间计算横坐标。
  const y = yFor(price); // 用买卖价格计算纵坐标。
  const label = kind === "buy" ? "买入" : "卖出"; // 生成买卖标签文字。
  const title = markerTitle(chart, label, timeText, price, nearby); // 生成带相对昨收涨幅和成交量的标记提示。
  const shape = kind === "buy" ? `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="5.5" fill="${color}" stroke="#ffffff" stroke-width="2"><title>${title}</title></circle>` : `<rect x="${(x - 5.5).toFixed(1)}" y="${(y - 5.5).toFixed(1)}" width="11" height="11" rx="1.5" fill="${color}" stroke="#ffffff" stroke-width="2"><title>${title}</title></rect>`; // 按买入圆点和卖出方块生成 SVG。
  return `<g><line x1="${x.toFixed(1)}" y1="24" x2="${x.toFixed(1)}" y2="246" stroke="${color}" stroke-opacity="0.22" stroke-dasharray="4 4" />${shape}<text x="${x.toFixed(1)}" y="${Math.max(14, y - 10).toFixed(1)}" text-anchor="middle" fill="${color}" font-size="11" font-weight="600">${label}</text></g>`; // 返回带竖线和文字的标记。
} // 结束买卖标记函数。
function stockChartSvg(chart) { // 定义生成单只股票价格成交量 SVG 的函数。
  const points = chart.points || []; // 读取当前股票行情点。
  const color = colorForCode(chart.code); // 读取当前股票稳定颜色。
  if (!points.length) { // 判断是否没有行情点。
    return `<div class="flex h-64 items-center justify-center rounded border border-dashed border-slate-300 bg-slate-50 text-sm text-slate-500">${escapeHtml(chart.message || "暂无行情点")}</div>`; // 返回无行情空状态。
  } // 结束空行情判断。
  const width = 640; // 设置 SVG 逻辑宽度。
  const height = 300; // 设置 SVG 逻辑高度。
  const left = 58; // 设置左侧价格轴留白。
  const right = 18; // 设置右侧留白。
  const top = 24; // 设置顶部留白。
  const priceBottom = 186; // 设置价格区域底部。
  const volumeTop = 214; // 设置成交量区域顶部。
  const volumeBottom = 260; // 设置成交量区域底部。
  const bottom = 282; // 设置时间轴文字基线。
  const times = points.map((point) => timeMs(point.time)).filter(Boolean); // 提取有效行情时间戳。
  const minTime = Math.min(...times); // 计算行情最早时间。
  const maxTime = Math.max(...times); // 计算行情最晚时间。
  const span = Math.max(1, maxTime - minTime); // 计算行情时间跨度。
  const priceInputs = points.map((point) => Number(point.price || 0)).concat([Number(chart.buy_price || 0), Number(chart.sell_price || 0), Number(chart.target_price || 0)]).filter((value) => value > 0); // 汇总价格线和买卖目标价格。
  const minPriceRaw = Math.min(...priceInputs); // 计算原始最低价。
  const maxPriceRaw = Math.max(...priceInputs); // 计算原始最高价。
  const pricePad = Math.max(0.01, (maxPriceRaw - minPriceRaw) * 0.12); // 计算价格上下留白。
  const minPrice = Math.max(0, minPriceRaw - pricePad); // 计算绘图最低价。
  const maxPrice = maxPriceRaw + pricePad; // 计算绘图最高价。
  const priceSpan = Math.max(0.01, maxPrice - minPrice); // 计算价格轴跨度。
  const xFor = (value) => left + ((value - minTime) / span) * (width - left - right); // 定义时间到横坐标的映射。
  const yFor = (value) => top + (1 - ((value - minPrice) / priceSpan)) * (priceBottom - top); // 定义价格到纵坐标的映射。
  const maxVolume = Math.max(1, ...points.map((point) => Number(point.volume || 0))); // 计算成交量最大值。
  const barWidth = Math.max(1.2, ((width - left - right) / Math.max(1, points.length)) * 0.7); // 计算成交量柱宽。
  const linePath = points.map((point, index) => `${index === 0 ? "M" : "L"}${xFor(timeMs(point.time)).toFixed(1)},${yFor(Number(point.price || 0)).toFixed(1)}`).join(" "); // 生成价格折线路径。
  const volumeBars = points.map((point) => { const x = xFor(timeMs(point.time)) - barWidth / 2; const h = Math.max(1, (Number(point.volume || 0) / maxVolume) * (volumeBottom - volumeTop)); return `<rect x="${x.toFixed(1)}" y="${(volumeBottom - h).toFixed(1)}" width="${barWidth.toFixed(1)}" height="${h.toFixed(1)}" fill="${color}" fill-opacity="0.22"><title>${pointTitle(chart, point)}</title></rect>`; }).join(""); // 生成可悬停查看涨幅和成交量的成交量柱。
  const pointDots = points.map((point) => `<circle cx="${xFor(timeMs(point.time)).toFixed(1)}" cy="${yFor(Number(point.price || 0)).toFixed(1)}" r="2.2" fill="${color}" fill-opacity="0.58" stroke="#ffffff" stroke-width="0.8"><title>${pointTitle(chart, point)}</title></circle>`).join(""); // 为每个行情点生成可悬停查看涨幅的小圆点。
  const priceTicks = [minPrice, (minPrice + maxPrice) / 2, maxPrice]; // 生成价格轴刻度。
  const grid = priceTicks.map((price) => `<g><line x1="${left}" y1="${yFor(price).toFixed(1)}" x2="${width - right}" y2="${yFor(price).toFixed(1)}" stroke="#e2e8f0" /><text x="${left - 8}" y="${(yFor(price) + 4).toFixed(1)}" text-anchor="end" fill="#64748b" font-size="11">${price.toFixed(2)}</text></g>`).join(""); // 生成价格网格线。
  const dayStartTimes = Array.from(new Set(points.map((point) => point.trade_date).filter(Boolean))).map((day) => ({ day, time: timeMs(`${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6, 8)} 09:30:00`) })).filter((item) => item.time >= minTime && item.time <= maxTime); // 生成每个交易日开盘分界时间。
  const dayLines = dayStartTimes.map((item) => `<g><line x1="${xFor(item.time).toFixed(1)}" y1="${top}" x2="${xFor(item.time).toFixed(1)}" y2="${volumeBottom}" stroke="#94a3b8" stroke-opacity="0.45" stroke-dasharray="5 5" /><text x="${(xFor(item.time) + 4).toFixed(1)}" y="${top + 12}" fill="#475569" font-size="11">${compactDayLabel(item.day)} 开盘</text></g>`).join(""); // 生成 T-1 和 T 日开盘分界线。
  const timeTicks = [new Date(minTime), new Date(Math.min(maxTime, minTime + span / 3)), new Date(Math.min(maxTime, minTime + span * 2 / 3)), new Date(maxTime)]; // 生成更完整的横轴刻度。
  const timeGrid = timeTicks.map((tick) => `<text x="${xFor(tick.getTime()).toFixed(1)}" y="${bottom}" text-anchor="middle" fill="#64748b" font-size="11">${tick.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</text>`).join(""); // 生成时间轴文字。
  const buyMarker = chartMarker("buy", chart, xFor, yFor, points, color); // 生成买入标记。
  const sellMarker = chartMarker("sell", chart, xFor, yFor, points, color); // 生成卖出标记。
  const volumeLabel = `<text x="${left - 8}" y="${volumeTop + 12}" text-anchor="end" fill="#64748b" font-size="11">量</text>`; // 生成成交量轴标签。
  return `<svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(chart.code)} T-1 与 T 日价格成交量图" class="block">${grid}${dayLines}<line x1="${left}" y1="${top}" x2="${left}" y2="${priceBottom}" stroke="#94a3b8" /><line x1="${left}" y1="${priceBottom}" x2="${width - right}" y2="${priceBottom}" stroke="#cbd5e1" /><path d="${linePath}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" />${pointDots}${buyMarker}${sellMarker}<line x1="${left}" y1="${volumeBottom}" x2="${width - right}" y2="${volumeBottom}" stroke="#cbd5e1" />${volumeBars}${volumeLabel}${timeGrid}</svg>`; // 返回两日价格线、点位涨幅和成交量柱组合 SVG。
} // 结束单只股票 SVG 生成函数。
function stockChartCard(chart) { // 定义生成单只股票复盘卡片的函数。
  const color = colorForCode(chart.code); // 读取当前股票稳定颜色。
  const lastPoint = (chart.points || []).length ? chart.points[chart.points.length - 1] : {}; // 读取当前图表最后一个行情点。
  const buyPoint = nearestPoint(chart.points || [], timeMs(chart.buy_time)) || {}; // 读取最接近买入时刻的行情点。
  const rangeText = `${compactDayLabel(chart.previous_day)} 全天 + ${compactDayLabel(chart.today_day)} ${chart.range_end ? "至当前/收盘" : ""}`; // 生成 T-1 和 T 日范围文字。
  return [`<article class="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">`, `<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">`, `<div>`, `<div class="flex items-center gap-2">`, `<span class="h-3 w-3 rounded-full" style="background:${color}"></span>`, `<h3 class="text-base font-semibold text-slate-950">${escapeHtml(chart.code)}</h3>`, `<span class="rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-600">${escapeHtml(chart.board_label || "-")}</span>`, `</div>`, `<p class="mt-1 text-xs text-slate-500">${escapeHtml(rangeText)}</p>`, `<p class="mt-1 text-xs text-slate-500">买入 ${escapeHtml(chart.buy_time || "-")}，卖出 ${escapeHtml(chart.sell_time || "-")}</p>`, `</div>`, `<div class="text-left sm:text-right">`, `<p class="text-lg font-semibold ${toneClass(chart.total_pnl)}">${signed(chart.total_pnl)}</p>`, `<p class="text-sm font-medium ${toneClass(chart.return_rate)}">${chart.shares ? signedPct(chart.return_rate) : "-"}</p>`, `<p class="mt-1 text-xs font-medium ${toneClass(lastPoint.rise_rate)}">末点涨幅 ${lastPoint.time ? signedPct(lastPoint.rise_rate) : "-"}</p>`, `</div>`, `</div>`, `<dl class="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-600 sm:grid-cols-7">`, `<div><dt>买入价</dt><dd class="font-semibold text-slate-950">${chart.buy_price ? yuan(chart.buy_price) : "-"}</dd></div>`, `<div><dt>买入涨幅</dt><dd class="font-semibold ${toneClass(buyPoint.rise_rate)}">${buyPoint.time ? signedPct(buyPoint.rise_rate) : "-"}</dd></div>`, `<div><dt>买入点量</dt><dd class="font-semibold text-slate-950">${buyPoint.time ? num(buyPoint.volume) : "-"}</dd></div>`, `<div><dt>卖出价</dt><dd class="font-semibold text-slate-950">${chart.sell_price ? yuan(chart.sell_price) : "-"}</dd></div>`, `<div><dt>买入量</dt><dd class="font-semibold text-slate-950">${num(chart.shares)}</dd></div>`, `<div><dt>卖出量</dt><dd class="font-semibold text-slate-950">${num(chart.sell_volume)}</dd></div>`, `<div><dt>末点价</dt><dd class="font-semibold text-slate-950">${lastPoint.price ? yuan(lastPoint.price) : "-"}</dd></div>`, `</dl>`, `<div class="mt-3">${stockChartSvg(chart)}</div>`, `</article>`].join(""); // 返回单只股票复盘卡片 HTML。
} // 结束单只股票复盘卡片函数。
function renderBatchReviewContent() { // 定义渲染批次全股票复盘内容区域的函数。
  const batch = reviewBatch(); // 读取当前选中的复盘批次。
  const summary = batch.summary || {}; // 读取当前批次汇总数据。
  const charts = batch.stock_charts || []; // 读取当前批次股票小图数据。
  const chartCount = charts.filter((chart) => (chart.points || []).length).length; // 统计有行情点的小图数量。
  query("#batchReviewSummary").textContent = `${batch.name || "未知批次"}：选中 ${num(summary.stock_count)} 只，买入成交 ${num(summary.filled_count)} 只，今日已卖 ${num(summary.closed_count)} 只，仍持仓 ${num(summary.open_count)} 只，买入未成 ${num(summary.buy_unfilled_count)} 只，行情图 ${num(chartCount)} 只，收益率 ${signedPct(summary.return_rate)}`; // 渲染当前批次复盘摘要。
  query("#batchStockGrid").innerHTML = charts.map(stockChartCard).join(""); // 渲染当前批次所有股票复盘小图。
} // 结束批次全股票复盘内容渲染函数。
function renderBatchReview() { // 定义渲染批次全股票复盘区域的函数。
  renderReviewFilters(); // 渲染批次复盘切换按钮。
  renderBatchReviewContent(); // 渲染当前批次复盘内容。
} // 结束批次全股票复盘渲染函数。
function boardMiniRow(row, maxAbs) { // 定义生成单个板块收益行的函数。
  return [`<div class="rounded border border-slate-200 bg-slate-50 p-3">`, `<div class="flex items-start justify-between gap-3">`, `<div>`, `<p class="text-sm font-medium text-slate-500">${escapeHtml(row.label)}</p>`, `<p class="mt-1 text-xl font-semibold ${toneClass(row.total_result)}">${signed(row.total_result)}</p>`, `<p class="mt-1 text-sm font-medium ${toneClass(row.return_rate)}">${signedPct(row.return_rate)}</p>`, `</div>`, `<div class="text-right text-xs text-slate-500">`, `<p>${num(row.filled_count)} 只成交</p>`, `<p class="mt-1">${num(row.closed_count)} 只已卖</p>`, `</div>`, `</div>`, `<div class="mt-3 h-2 overflow-hidden rounded bg-white">`, `<div class="h-full ${barClass(row.total_result)}" style="width:${pctWidth(row.total_result, maxAbs)}"></div>`, `</div>`, `<dl class="mt-3 grid grid-cols-3 gap-2 text-xs">`, `<div><dt class="text-slate-500">成本</dt><dd class="font-medium text-slate-950">${yuan(row.buy_cost)}</dd></div>`, `<div><dt class="text-slate-500">卖出</dt><dd class="font-medium text-slate-950">${yuan(row.sell_amount)}</dd></div>`, `<div><dt class="text-slate-500">股票</dt><dd class="font-medium text-slate-950">${num(row.stock_count)}</dd></div>`, `</dl>`, `</div>`].join(""); // 返回板块收益行 HTML。
} // 结束单个板块收益行函数。
function boardBatchCard(batch, maxAbs) { // 定义生成按批次拆分的板块收益卡片函数。
  const rows = batch.board_summary || []; // 读取当前批次板块汇总。
  return [`<section class="rounded-lg border border-slate-200 bg-white p-4">`, `<div class="mb-3 flex items-center justify-between gap-3">`, `<h3 class="text-sm font-semibold text-slate-950">${escapeHtml(batch.name)}</h3>`, `<span class="text-sm font-medium ${toneClass((batch.summary || {}).return_rate)}">${signedPct((batch.summary || {}).return_rate)}</span>`, `</div>`, `<div class="grid gap-3">${rows.map((row) => boardMiniRow(row, maxAbs)).join("")}</div>`, `</section>`].join(""); // 返回批次板块卡片 HTML。
} // 结束批次板块卡片函数。
function renderBoards() { // 定义渲染板块收益归因的函数。
  const batches = data.batches || []; // 读取批次数组。
  const rows = batches.flatMap((batch) => batch.board_summary || []); // 展开所有批次板块汇总行。
  const maxAbs = Math.max(1, ...rows.map((row) => Math.abs(Number(row.total_result || 0)))); // 计算板块最大盈亏绝对值。
  query("#boardCards").innerHTML = batches.map((batch) => boardBatchCard(batch, maxAbs)).join(""); // 按批次写入板块收益卡片。
} // 结束板块收益归因渲染函数。
function timelineEvents() { // 定义读取当前时间轴事件的函数。
  const allEvents = ((data.timeline || {}).events || []).filter((event) => event.time); // 读取带时间的事件列表。
  if (activeTimelineBatch === "全部") { // 判断是否显示全部批次。
    return allEvents; // 返回全部事件。
  } // 结束全部批次判断。
  return allEvents.filter((event) => event.batch === activeTimelineBatch); // 返回当前批次事件。
} // 结束时间轴事件读取函数。
function eventDate(value) { // 定义把事件时间文本转为日期对象的函数。
  return new Date(String(value || "").replace(" ", "T")); // 返回浏览器可解析的本地日期对象。
} // 结束事件日期转换函数。
function codeColorMap(events) { // 定义生成股票代码颜色映射的函数。
  const codes = Array.from(new Set(events.map((event) => event.code).filter(Boolean))).sort(); // 提取并排序当前图表股票代码。
  return Object.fromEntries(codes.map((code) => [code, colorForCode(code)])); // 按股票代码哈希稳定分配颜色。
} // 结束股票代码颜色映射函数。
function eventShape(point, color) { // 定义按买卖方向生成 SVG 点形状的函数。
  const { event, x, y, r, price } = point; // 解构点位数据。
  const opacity = event.status === "已成" ? "0.95" : "0.58"; // 根据成交状态设置透明度。
  const title = escapeHtml(`${event.time} ${event.batch} ${event.code} ${event.lane} ${event.volume}股 价格${price.toFixed(2)} 收益率${signedPct(event.return_rate)} ${event.status}`); // 生成悬停提示文本。
  if (event.side === "卖出") { // 判断当前事件是否卖出。
    const size = r * 1.7; // 计算卖出方块尺寸。
    return `<rect x="${(x - size / 2).toFixed(1)}" y="${(y - size / 2).toFixed(1)}" width="${size.toFixed(1)}" height="${size.toFixed(1)}" rx="1.5" fill="${color}" fill-opacity="${opacity}" stroke="#ffffff" stroke-width="1.5"><title>${title}</title></rect>`; // 返回卖出方块。
  } // 结束卖出判断。
  if (event.lane === "撤单/拒单") { // 判断当前事件是否撤单或拒单。
    const topPoint = `${x.toFixed(1)},${(y - r).toFixed(1)}`; // 计算三角形顶部点。
    const leftPoint = `${(x - r).toFixed(1)},${(y + r).toFixed(1)}`; // 计算三角形左下点。
    const rightPoint = `${(x + r).toFixed(1)},${(y + r).toFixed(1)}`; // 计算三角形右下点。
    return `<polygon points="${topPoint} ${leftPoint} ${rightPoint}" fill="${color}" fill-opacity="${opacity}" stroke="#ffffff" stroke-width="1.5"><title>${title}</title></polygon>`; // 返回撤单拒单三角形。
  } // 结束撤单拒单判断。
  return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r.toFixed(1)}" fill="${color}" fill-opacity="${opacity}" stroke="#ffffff" stroke-width="1.5"><title>${title}</title></circle>`; // 返回买入圆点。
} // 结束事件形状函数。
function timelineButton(label) { // 定义生成时间轴筛选按钮的函数。
  const active = activeTimelineBatch === label; // 判断当前按钮是否激活。
  const classes = active ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 bg-white text-slate-700 hover:border-slate-400"; // 根据激活状态选择样式。
  return `<button type="button" data-batch="${escapeHtml(label)}" class="rounded border px-3 py-1.5 text-sm font-medium ${classes}">${escapeHtml(label)}</button>`; // 返回筛选按钮 HTML。
} // 结束筛选按钮函数。
function renderTimelineFilters() { // 定义渲染时间轴筛选器的函数。
  const labels = ["全部", ...(data.batches || []).map((batch) => batch.name)]; // 构造筛选项列表。
  const container = query("#timelineFilters"); // 获取筛选按钮容器。
  container.innerHTML = labels.map(timelineButton).join(""); // 写入筛选按钮。
  container.querySelectorAll("button").forEach((button) => button.addEventListener("click", () => { activeTimelineBatch = button.dataset.batch; renderTimelineContent(); updateTimelineFilterState(); })); // 给按钮绑定筛选重绘事件，只更新图表内容和按钮状态。
} // 结束筛选器渲染函数。
function updateTimelineFilterState() { // 定义更新时间轴筛选按钮激活状态的函数。
  query("#timelineFilters").querySelectorAll("button").forEach((button) => { const active = activeTimelineBatch === button.dataset.batch; button.className = `rounded border px-3 py-1.5 text-sm font-medium ${active ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 bg-white text-slate-700 hover:border-slate-400"}`; }); // 按当前时间轴批次更新按钮样式。
} // 结束时间轴筛选按钮状态更新函数。
function renderTimelineLegend(events) { // 定义渲染时间轴图例的函数。
  const colors = codeColorMap(events); // 生成当前筛选事件的股票颜色映射。
  const codeItems = Object.entries(colors).map(([code, color]) => `<span class="inline-flex items-center gap-1.5"><span class="h-2.5 w-2.5 rounded-full" style="background:${color}"></span>${escapeHtml(code)}</span>`).join(""); // 生成股票代码颜色图例。
  const shapeItems = [`<span class="inline-flex items-center gap-1.5"><span class="h-2.5 w-2.5 rounded-full bg-slate-500"></span>买入圆点</span>`, `<span class="inline-flex items-center gap-1.5"><span class="h-2.5 w-2.5 rounded-sm bg-slate-500"></span>卖出方块</span>`, `<span class="inline-flex items-center gap-1.5"><span class="h-0 w-0 border-x-[5px] border-b-[9px] border-x-transparent border-b-slate-500"></span>撤单三角</span>`].join(""); // 生成形状说明图例。
  query("#timelineLegend").innerHTML = `<div class="flex flex-wrap gap-3">${shapeItems}</div><div class="mt-2 flex flex-wrap gap-3">${codeItems}</div>`; // 写入代码颜色和形状图例。
} // 结束图例渲染函数。
function renderTimelineChart(events) { // 定义渲染 SVG 时间价格图表的函数。
  const chart = query("#timelineChart"); // 获取图表容器。
  const pricedEvents = events.filter((event) => Number(event.chart_price || event.price || 0) > 0); // 筛选有价格可绘制的事件。
  if (!pricedEvents.length) { // 判断价格事件是否为空。
    chart.innerHTML = `<div class="rounded border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">暂无可绘制的时间轴事件</div>`; // 渲染空状态。
    return; // 空事件时结束渲染。
  } // 结束空事件判断。
  const dates = pricedEvents.map((event) => eventDate(event.time)).filter((date) => !Number.isNaN(date.getTime())); // 转换事件时间为日期数组。
  const minTime = Math.min(...dates.map((date) => date.getTime())); // 计算最早时间戳。
  const maxTime = Math.max(...dates.map((date) => date.getTime())); // 计算最晚时间戳。
  const prices = pricedEvents.map((event) => Number(event.chart_price || event.price || 0)); // 提取绘图价格数组。
  const minPriceRaw = Math.min(...prices); // 计算最低价格。
  const maxPriceRaw = Math.max(...prices); // 计算最高价格。
  const pricePad = Math.max(0.01, (maxPriceRaw - minPriceRaw) * 0.08); // 计算价格轴上下留白。
  const minPrice = Math.max(0, minPriceRaw - pricePad); // 计算价格轴最低值。
  const maxPrice = maxPriceRaw + pricePad; // 计算价格轴最高值。
  const width = 920; // 设置 SVG 逻辑宽度，实际按容器缩放。
  const height = 360; // 设置 SVG 图表高度。
  const left = 70; // 设置左侧价格轴宽度。
  const right = 24; // 设置右侧留白。
  const top = 28; // 设置顶部留白。
  const bottom = 54; // 设置底部时间轴留白。
  const span = Math.max(1, maxTime - minTime); // 计算横轴时间跨度。
  const xFor = (value) => left + ((value - minTime) / span) * (width - left - right); // 定义时间到 x 坐标的映射函数。
  const priceSpan = Math.max(0.01, maxPrice - minPrice); // 计算价格轴跨度。
  const yFor = (value) => top + (1 - ((value - minPrice) / priceSpan)) * (height - top - bottom); // 定义价格到 y 坐标的映射函数。
  const colors = codeColorMap(pricedEvents); // 按股票代码生成颜色映射。
  const countBySlot = {}; // 初始化同秒同价点位计数。
  const points = pricedEvents.map((event) => { // 把事件转换为 SVG 点位。
    const date = eventDate(event.time); // 转换当前事件时间。
    const price = Number(event.chart_price || event.price || 0); // 读取当前事件绘图价格。
    const slotKey = `${event.code}-${event.time}-${price}`; // 构造同秒同价分组键。
    const offsetIndex = countBySlot[slotKey] || 0; // 读取当前分组已经放置的点数量。
    countBySlot[slotKey] = offsetIndex + 1; // 累加当前分组点数量。
    const xOffset = ((offsetIndex % 5) - 2) * 3; // 计算点位横向错位。
    const radius = Math.max(4, Math.min(10, Math.sqrt(Number(event.amount || 0) / 12000) + 3)); // 根据金额计算点大小。
    return { event, x: xFor(date.getTime()) + xOffset, y: yFor(price), r: radius, price }; // 返回点位对象。
  }); // 结束点位映射。
  const axisTicks = [0, 0.25, 0.5, 0.75, 1].map((ratio) => new Date(minTime + span * ratio)); // 构造横轴刻度时间。
  const priceTicks = [0, 0.25, 0.5, 0.75, 1].map((ratio) => minPrice + priceSpan * ratio); // 构造价格轴刻度。
  const horizontalGrid = priceTicks.map((price) => `<g><line x1="${left}" y1="${yFor(price)}" x2="${width - right}" y2="${yFor(price)}" stroke="#e2e8f0" stroke-width="1" /><text x="${left - 10}" y="${yFor(price) + 4}" text-anchor="end" fill="#64748b" font-size="12">${price.toFixed(2)}</text></g>`).join(""); // 生成价格轴网格。
  const verticalGrid = axisTicks.map((tick) => `<g><line x1="${xFor(tick.getTime())}" y1="${top}" x2="${xFor(tick.getTime())}" y2="${height - bottom}" stroke="#f1f5f9" stroke-width="1" /><text x="${xFor(tick.getTime())}" y="${height - 20}" text-anchor="middle" fill="#64748b" font-size="12">${tick.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</text></g>`).join(""); // 生成时间轴网格。
  const shapes = points.map((point) => eventShape(point, colors[point.event.code] || "#64748b")).join(""); // 按股票代码颜色和买卖形状生成散点。
  chart.innerHTML = `<svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="交易价格时间轴图表" class="block">${verticalGrid}${horizontalGrid}<line x1="${left}" y1="${top}" x2="${left}" y2="${height - bottom}" stroke="#94a3b8" /><line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}" stroke="#94a3b8" /><text x="14" y="18" fill="#475569" font-size="12">价格</text>${shapes}</svg>`; // 写入响应式 SVG 图表。
} // 结束 SVG 图表渲染函数。
function timelineRow(event) { // 定义生成时间轴事件表格行的函数。
  const shownPrice = Number(event.chart_price || event.price || 0); // 读取事件展示价格。
  const shownRate = Number(event.return_rate); // 读取事件收益率。
  return [`<tr class="border-b border-slate-100 hover:bg-slate-50">`, `<td class="px-3 py-3 text-sm text-slate-700">${escapeHtml(event.time)}</td>`, `<td class="px-3 py-3 text-sm text-slate-700">${escapeHtml(event.batch)}</td>`, `<td class="px-3 py-3 text-sm font-semibold text-slate-950">${escapeHtml(event.code)}</td>`, `<td class="px-3 py-3 text-sm text-slate-700">${escapeHtml(event.lane)}${event.price_type ? ` · ${escapeHtml(event.price_type)}` : ""}</td>`, `<td class="px-3 py-3 text-right text-sm text-slate-700">${num(event.volume)}</td>`, `<td class="px-3 py-3 text-right text-sm text-slate-700">${shownPrice ? yuan(shownPrice) : "-"}</td>`, `<td class="px-3 py-3 text-right text-sm text-slate-700">${event.amount ? yuan(event.amount) : "-"}</td>`, `<td class="px-3 py-3 text-right text-sm font-medium ${toneClass(shownRate)}">${Number.isFinite(shownRate) ? signedPct(shownRate) : "-"}</td>`, `<td class="px-3 py-3"><span class="inline-flex rounded border px-2 py-1 text-xs ${eventStatusClass(event.status)}">${escapeHtml(event.status || "-")}</span></td>`, `<td class="px-3 py-3 text-sm text-slate-500">${escapeHtml(event.source)}</td>`, `</tr>`].join(""); // 返回事件明细表格行 HTML。
} // 结束事件明细行函数。
function renderTimelineContent() { // 定义渲染时间轴内容区域的函数。
  const timeline = data.timeline || {}; // 读取时间轴数据。
  const events = timelineEvents(); // 读取当前筛选后的事件。
  query("#timelineRange").textContent = `时间范围：${timeline.start || "未知"} 至 ${timeline.end || "未知"}，当前显示 ${num(events.length)} 个事件`; // 渲染时间范围说明。
  renderTimelineChart(events); // 渲染 SVG 价格时间轴图。
  renderTimelineLegend(events); // 渲染股票代码颜色和买卖形状图例。
  query("#timelineRows").innerHTML = events.map(timelineRow).join(""); // 渲染事件明细表格。
} // 结束时间轴内容区域渲染函数。
function renderTimeline() { // 定义渲染完整时间轴区域的函数。
  renderTimelineFilters(); // 重新渲染筛选按钮状态。
  renderTimelineContent(); // 渲染当前时间轴图表和事件表格。
} // 结束完整时间轴渲染函数。
function positionRow(batch, row) { // 定义生成持仓明细行的函数。
  return [`<tr class="border-b border-slate-100 hover:bg-slate-50">`, `<td class="px-3 py-3 text-sm font-medium text-slate-900">${batch.name}</td>`, `<td class="px-3 py-3 text-sm text-slate-700">${row.board_label || "-"}</td>`, `<td class="px-3 py-3 text-sm font-semibold text-slate-950">${row.code}</td>`, `<td class="px-3 py-3"><span class="inline-flex rounded border px-2 py-1 text-xs ${statusClass(row.status)}">${escapeHtml(statusText(row.status))}</span></td>`, `<td class="px-3 py-3 text-right text-sm text-slate-700">${num(row.filled_shares)}</td>`, `<td class="px-3 py-3 text-right text-sm text-slate-700">${row.buy_price ? yuan(row.buy_price) : "-"}</td>`, `<td class="px-3 py-3 text-right text-sm text-slate-700">${row.target_price ? yuan(row.target_price) : "-"}</td>`, `<td class="px-3 py-3 text-right text-sm text-slate-700">${num(row.sell_volume)}</td>`, `<td class="px-3 py-3 text-right text-sm text-slate-700">${row.sell_price ? yuan(row.sell_price) : "-"}</td>`, `<td class="px-3 py-3 text-right text-sm text-slate-700">${num(row.current_volume)}</td>`, `<td class="px-3 py-3 text-right text-sm text-slate-700">${yuan(row.market_value)}</td>`, `<td class="px-3 py-3 text-right text-sm font-medium ${toneClass(row.total_pnl)}">${signed(row.total_pnl)}</td>`, `<td class="px-3 py-3 text-right text-sm font-medium ${toneClass(row.return_rate)}">${row.filled_shares ? signedPct(row.return_rate) : "-"}</td>`, `</tr>`].join(""); // 返回持仓明细表格行 HTML。
} // 结束持仓明细行函数。
function renderPositions() { // 定义渲染持仓和计划明细的函数。
  const rows = (data.batches || []).flatMap((batch) => (batch.positions || []).filter((row) => row.filled_shares || row.current_volume || row.sell_volume || ["buy_submitted", "not_bought"].includes(row.status)).map((row) => positionRow(batch, row))); // 生成所有有效持仓计划行。
  query("#positionRows").innerHTML = rows.join(""); // 写入持仓计划表格。
} // 结束持仓明细渲染函数。
function orderRow(order) { // 定义生成今日委托行的函数。
  return [`<tr class="border-b border-slate-100 hover:bg-slate-50">`, `<td class="px-3 py-3 text-sm text-slate-700">${order.batch}</td>`, `<td class="px-3 py-3 text-sm font-semibold text-slate-950">${order.code}</td>`, `<td class="px-3 py-3 text-sm text-slate-700">${order.side}</td>`, `<td class="px-3 py-3 text-right text-sm text-slate-700">${order.order_id}</td>`, `<td class="px-3 py-3 text-right text-sm text-slate-700">${num(order.order_volume)}</td>`, `<td class="px-3 py-3 text-right text-sm text-slate-700">${num(order.traded_volume)}</td>`, `<td class="px-3 py-3"><span class="inline-flex rounded border px-2 py-1 text-xs ${orderStatusClass(order.status)}">${order.status}</span></td>`, `<td class="px-3 py-3 text-right text-sm text-slate-700">${yuan(order.price)}</td>`, `</tr>`].join(""); // 返回今日委托表格行 HTML。
} // 结束今日委托行函数。
function renderOrders() { // 定义渲染今日委托表格的函数。
  query("#orderRows").innerHTML = (data.orders || []).map(orderRow).join(""); // 写入今日委托表格。
} // 结束今日委托渲染函数。
function render() { // 定义页面总渲染函数。
  renderSummary(); // 渲染账户总览区域。
  renderStrategies(); // 渲染七策略分账总览。
  renderBatches(); // 渲染两批交易汇总区域。
  renderBatchReview(); // 渲染当前批次全股票买卖复盘区域。
  renderBoards(); // 渲染板块收益归因区域。
  renderTimeline(); // 渲染交易时间轴区域。
  renderPositions(); // 渲染持仓计划明细区域。
  renderOrders(); // 渲染今日委托明细区域。
} // 结束页面总渲染函数。
render(); // 执行页面渲染。
