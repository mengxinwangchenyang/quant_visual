# quant_visual

[quant_fresh](https://github.com/mengxinwangchenyang/quant_fresh) 虚拟盘的可视化：每日/每周/累计的买入、卖出与报告页面，附个股分钟 K 线。

**只读**：只读取 quant_fresh 的交易数据，只写本仓库下的快照与图表，绝不下单、绝不改交易文件。

## 目录位置

本仓库与 quant_fresh 克隆到同一个父目录下：

```
quant/
├── quant_fresh/     ← 交易仓库（config.py、util.py、auto_buy/predict_client.py 被本仓库复用）
└── quant_visual/    ← 本仓库
```

`project_paths.py` 默认把同级的 `../quant_fresh` 作为 `PROJECT_ROOT` 挂到 `sys.path`，复用其 `config`（`START_DATE`、`STRATEGY_CONFIGS`）、`util`（交易日历）和 `predict_client`（历史候选回放）。放在别处时设置环境变量 `QUANT_FRESH_ROOT`。

quant_fresh 的 `auto_buy/export_history_request.py`、`export_minute_request.py` 反过来读取本仓库的 `daily_buys_snapshot.json` 与分钟图目录，以决定要导出哪些股票的行情，因此目录名须为 `quant_visual`。

## 数据来源（只读）

| 数据 | 路径（相对 quant_fresh） |
|---|---|
| 大脑委托 / 买入候选 | `auto_buy/qmt_orders.json`、`auto_buy/qmt_buy_candidates.json` |
| 成交归档 | `daily_refresh/qmt_deal_archive.json` |
| 资金账本 | `cash_ledger/strategy_cash_ledger.json` |
| 实时成交、日线、分钟线、中证1000、名称 | `qmt_local/qmt_fills.json`、`qmt_history_data.json`、`qmt_minute_data.json`、`qmt_csi1000.json`、`qmt_order_names.json` |
| 预测结果 | `virtual_qmt_data/predict_sync_*_latest.json` |

只统计 `config.START_DATE`（虚拟盘重置日）及之后的交易日。

## 使用

| 命令 | 作用 |
|---|---|
| `refresh.bat`（或 `python refresh.py`） | 一次性重建全部快照与图表，日志 `refresh.log` |
| `live_refresh.bat` | 盘中每 60 秒检查成交/分钟数据，变化即重建，15:10 自动退出，日志 `live_refresh.log` |
| `serve.bat` | 启动网页 <http://127.0.0.1:5510/daily_buys.html>（其他机器用 `http://<本机IP>:5510/daily_buys.html`） |

默认解释器 `C:\Python\Python38\python.exe`，可用环境变量 `PYTHON_EXE` 覆盖。

## 文件说明

| 文件 | 作用 |
|---|---|
| `project_paths.py` | 定位 quant_fresh 并挂到 `sys.path` |
| `build_qmt_fills.py` | 从委托/成交/候选/预测中整理真实买入、卖出 |
| `build_daily_buys.py` | `daily_buys_snapshot.json` + 最新日买入分钟图 |
| `build_daily_sells.py` | `daily_sells_snapshot.json` + 最新日卖出分钟图 |
| `build_weekly_snapshot.py` | `weekly_snapshot.json`（周/累计，仅读前两份快照） |
| `rebuild_missing_charts.py` | 只补建历史日中缺失/稀疏的分钟图，不覆盖已正常的图 |
| `xtdata_offline_shim.py` | 离线 xtdata 替身，从 qmt_local 导出数据构建分钟图 |
| `refresh.py` / `live_refresh.py` | 收盘后一次性刷新 / 盘中调度 |
| `serve_daily_buys.py` | 无缓存静态服务器（端口 5510） |
| `*.html` / `*.js` / `*.css` | 页面：daily / weekly / cumulative 的 buys、sells、report |

`*_snapshot.json`、`daily_buy_charts/`、`daily_sell_charts/` 是生成数据，不纳入版本控制，运行 `refresh.bat` 即可重建。

## 部署到新机器

前提：同一台机器上已按 [quant_fresh 的 README](https://github.com/mengxinwangchenyang/quant_fresh#部署到新机器) 部署好交易仓库，且 QMT 执行器在运行（数据由它导出）。

```powershell
cd <quant_fresh 所在的父目录>
git clone https://github.com/mengxinwangchenyang/quant_visual.git
cd quant_visual
.\refresh.bat          # 首次生成快照与图表，查看 refresh.log 以 "visual refresh done" 结束
.\serve.bat            # 浏览器打开 http://127.0.0.1:5510/daily_buys.html
```

依赖与 quant_fresh 相同（pandas 等，见其 `requirements.txt`），不需要额外安装。

### 需要修改/确认的地方

| 项 | 默认 | 何时要改 |
|---|---|---|
| 目录位置 | `../quant_fresh` | 不是同级时设置环境变量 `QUANT_FRESH_ROOT=<quant_fresh 路径>`；本仓库目录名必须是 `quant_visual` |
| Python 路径 | `C:\Python\Python38\python.exe` | 不同时设置环境变量 `PYTHON_EXE` |
| 端口 | `5510`，监听 `0.0.0.0` | 改 `serve_daily_buys.py` 的 `PORT` |
| 起始日 | quant_fresh `config.START_DATE` | 本仓库不单独配置 |

### 其他机器通过 IP 访问

服务已监听所有网卡，另需：

1. Windows 防火墙放行：`New-NetFirewallRule -DisplayName "QMT Visual Web 5510" -Direction Inbound -Protocol TCP -LocalPort 5510 -Action Allow`
2. 云服务器（如阿里云 ECS）还要在**安全组**加入方向规则 TCP 5510，来源尽量只填自己的 IP。
3. 或者用 Tailscale：`http://<本机 Tailscale IP>:5510/daily_buys.html`，无需开放公网。

页面**没有登录验证**，能访问端口即可看到全部持仓与成交，不要对 `0.0.0.0/0` 开放。

### 计划任务

以 `cmd.exe /d /c "<quant_visual>\xxx.bat"` 创建，起始目录设为 quant_visual：

| 任务名（参考） | 时间 | 动作 |
|---|---|---|
| `QMT_Daily_Viz_Refresh`（quant_fresh） | 工作日 15:35 | 成交归档 / 卖出账本 / 资金账本 |
| `QMT_Visual_Refresh` | 工作日 15:45（须在上一项之后） | `refresh.bat` |
| `QMT_Visual_Live_Refresh` | 工作日 09:25，限时 7 小时 | `live_refresh.bat` |
| `QMT_Visual_Web` | 登录时 | `serve.bat` |
