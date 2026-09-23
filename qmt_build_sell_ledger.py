# -*- coding: utf-8 -*-
"""【卖出大脑】从持久化的真实成交归档生成 QMT 卖出台账。

===================== 这个脚本在整条链路里的位置 =====================

    qmt_auto_buy.py   →  qmt_orders.json    →  执行器下买单
                                                    ↓ 产生真实成交
                                            qmt_deal_archive.json（持久归档）
                                                    ↓
    本脚本            →  qmt_sell_ledger.json  →  执行器按台账止盈/强平
                      →  qmt_sell_state.json

【为什么卖出台账必须收盘后才能生成】
目标价 = 买入成交均价 × (1 + 止盈率)。而买入成交均价只有【成交之后】才知道
（本机 xtdata 离线，下单前拿不到成交价）。所以 qmt_auto_buy.py 在下单前
只写一份只读参考 qmt_sell_plan_dryrun.json，真正的台账由本脚本事后回填。

【核心概念：lot（建仓批）】
一个 lot = (策略, 买入日期, 股票代码) 三元组唯一确定的一笔建仓。
同一只票被同一个策略在不同日期买入 → 是两个独立的 lot，各有各的成本价、
各有各的止盈线和强平日。这就是 batch_id 的构成方式。

【卖出成交如何归属到 lot：两种备注格式】
  新格式 s2-260825-688383t → 备注里自带买入日期和代码，【精确命中】唯一的 lot
  旧格式 s2t / s2e         → 只有策略名，无法区分是哪天买的，按 FIFO 先进先出
                             依次冲减该策略该代码下的各个 lot
两种格式并存且都必须支持，因为历史成交里有大量旧格式记录。

【数据源的权威性】
持久归档 qmt_deal_archive.json 是唯一事实来源。它是累积的，不像 qmt_fills.json
只有当天快照——台账要覆盖所有未平仓的历史批次，必须用归档重放。
"""

import argparse
import datetime as dt
import json
import os
import re
import tempfile
from collections import defaultdict

import multi_strategy      # 复用策略配置（止盈率、持有模式）和交易日历

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QMT_LOCAL = os.path.join(BASE_DIR, "qmt_local")
# 【输入】当日执行器快照，只用于校验（对账持仓，防止台账超过实际持仓）
DEFAULT_FILLS_PATH = os.path.join(QMT_LOCAL, "qmt_fills.json")
# 【输入】持久成交归档 —— 本脚本的权威数据源，累积所有历史成交
DEFAULT_ARCHIVE_PATH = os.path.join(QMT_LOCAL, "qmt_deal_archive.json")
# 【输出】卖出台账，执行器每根 bar 读它做止盈/强平判断
DEFAULT_LEDGER_PATH = os.path.join(QMT_LOCAL, "qmt_sell_ledger.json")
# 【输出】卖出状态（最高价棘轮 + 已卖股数），执行器读写
DEFAULT_STATE_PATH = os.path.join(QMT_LOCAL, "qmt_sell_state.json")
DEFAULT_ACCOUNT = "90007892"        # 模拟账户（假钱），绝不对实盘账户操作

# ★ 托管起始日 ★ 只接管这个日期【之后】买入的 lot。
# 在此之前的历史持仓由旧流程处理，本脚本不碰，避免两套逻辑打架。
MANAGED_BUY_START = "20260820"

# 强平时刻，按持有模式区分：
#   隔日策略(next_day)  → 次个交易日 10:00 强平（开盘后不久就走，不恋战）
#   多日持有策略        → 到期日 14:55 强平（尾盘走，给行情留足时间）
NEXT_DAY_FORCE_TIME = "10:00:00"
EXPIRY_FORCE_TIME = "14:55:00"

# 新格式卖出备注：策略 - 买入日期(6位YYMMDD) - 代码(6位) - 原因(t止盈/e强平)
# 例：s2-260825-688383t
_TAG_RE = re.compile(r"^(s\d+)-(\d{6})-(\d{6})([te])$")
_DATE_RE = re.compile(r"^\d{8}$")   # 8 位日期 YYYYMMDD


def _add_trading_days(buy_date, n):
    """从买入日往后推 n 个【交易日】（跳过周末和节假日），返回日期字符串。

    用于计算多日持有策略的强平日。必须走交易日历而不是简单加天数，
    否则强平日可能落在周末，那天根本没法下单。
    """
    cursor = buy_date
    for _ in range(int(n)):
        cursor = multi_strategy.next_trading_day(cursor)
    return cursor


def _deal_rows(payload):
    """从归档/快照里取出成交（DEAL）行列表，兼容两种存储结构。

    执行器写出的格式是 {"orders_deals": {"DEAL": {"rows": [...]}}}，
    但历史归档里也可能直接是列表，所以两种都要认。取不到一律返回空列表。
    """
    od = (payload or {}).get("orders_deals") or {}
    deal = od.get("DEAL")
    if isinstance(deal, dict):
        return list(deal.get("rows") or [])
    if isinstance(deal, list):
        return list(deal)
    return []


def _code_with_suffix(row):
    """从成交行拼出带交易所后缀的完整代码，如 "600000.SH"。

    柜台的成交行里代码和交易所是分开两个字段的（m_strInstrumentID +
    m_strExchangeID），而台账里统一用带后缀的完整格式，所以要在这里拼。
    如果代码本身已经带点号就直接用，避免拼成 "600000.SH.SH"。
    """
    inst = str(row.get("m_strInstrumentID") or "").strip()
    exch = str(row.get("m_strExchangeID") or "").strip().upper()
    if not inst:
        return ""
    if "." in inst:
        return inst.upper()
    return ("%s.%s" % (inst, exch)).upper() if exch else inst.upper()


def _code6(code):
    """取 6 位纯代码，去掉交易所后缀。"600000.SH" → "600000"。"""
    return str(code).split(".", 1)[0].strip()


def make_sell_tag(strategy, buy_date, code):
    """生成卖出备注的前缀（调用方再追加 t 或 e）。

    格式：策略-YYMMDD-代码6位，例如 "s2-260825-688383"。
    买入日期取后 6 位（去掉世纪的 "20"），是为了让备注尽量短——
    柜台的备注字段长度有限，而这个标签必须完整传到成交记录里，
    否则事后就无法把卖出成交归属回具体是哪一批买入。
    """
    return "%s-%s-%s" % (strategy, str(buy_date)[2:], _code6(code))


def parse_sell_remark(remark):
    """解析卖出备注，返回 (策略, 买入日期|None, 代码6位|None, 原因) 或 None。

    ★ 两种格式都要支持 ★
      新格式 "s2-260825-688383t" → 返回 ("s2", "20260825", "688383", "t")
                                   信息完整，可以精确命中唯一的 lot
      旧格式 "s2t"               → 返回 ("s2", None, None, "t")
                                   只有策略名，买入日期和代码都是 None，
                                   调用方只能按 FIFO 冲减

    返回 None 表示这不是一条我们认识的卖出备注（可能是手工单或其他来源）。
    策略名必须在 STRATEGY_CONFIGS 里登记过才认，防止误解析无关备注。
    """
    text = str(remark or "").strip()
    known = set(multi_strategy.STRATEGY_CONFIGS)
    match = _TAG_RE.match(text)
    if match and match.group(1) in known:
        # "260825" → "20260825"，补回世纪
        return match.group(1), "20" + match.group(2), match.group(3), match.group(4)
    # 旧格式兜底：末位是 t/e，去掉末位后剩下的部分是已知策略名
    if text[-1:] in ("t", "e") and text[:-1] in known:
        return text[:-1], None, None, text[-1]
    return None


def _row_sort_key(row, index):
    """成交行的排序键：日期 → 时间 → 成交编号 → 原始下标。

    ★ 排序顺序直接决定 FIFO 的正确性 ★
    必须严格按成交发生的先后重放，否则旧格式备注的先进先出冲减会算错批次。
    最后加原始下标是为了让排序【稳定】：当前三项完全相同时（同一秒的多笔成交），
    保持它们在归档里的原始顺序，保证每次运行结果可复现。
    """
    return (str(row.get("m_strTradeDate") or ""),
            str(row.get("m_strTradeTime") or ""),
            str(row.get("m_strTradeID") or row.get("m_strOrderSysID") or ""),
            index)


def _is_buy(row, known):
    """判断是不是我们的买入成交。

    两个条件：备注【正好等于】某个已知策略名（买单备注是裸的 "s2"，不带后缀），
    且操作名称里含「买」字。操作名称为空时不拦（有些成交行不带这个字段）。
    """
    remark = str(row.get("m_strRemark") or "").strip()
    opt = str(row.get("m_strOptName") or "")
    return remark in known and (not opt or "买" in opt)


def _is_sell(row):
    """判断是不是我们的卖出成交：备注能被解析成卖出标签，且操作名称含「卖」字。"""
    opt = str(row.get("m_strOptName") or "")
    return parse_sell_remark(row.get("m_strRemark")) is not None and (not opt or "卖" in opt)


def build_lots(deal_rows, managed_start=MANAGED_BUY_START):
    """★ 本脚本的核心 ★ 按时间顺序重放全部成交，还原出每一笔建仓（lot）的现状。

    返回 (lots, unallocated)：
      lots        - {batch_id: {策略, 代码, 买入日期, 总股数, 总金额, 已卖股数}}
      unallocated - 无法归属到任何 lot 的卖出成交（诊断用，见下方说明）

    【算法】就是一个「先进先出的仓位账本重放」：
      1. 把所有成交按 日期→时间→成交号 严格排序（顺序决定 FIFO 正确性）；
      2. 遇到买入成交 → 累加到对应 lot 的股数和金额上（同一 lot 可能多笔成交），
         同时把这个 lot 追加到该 (策略,代码) 的 FIFO 队列尾部；
      3. 遇到卖出成交 → 按备注格式归属：
           新格式（带买入日期）→ 直接定位那一个 lot，精确冲减
           旧格式（只有策略名）→ 沿 FIFO 队列从最早的 lot 开始依次冲减

    【关于金额累加】lot["notional"] 累加的是 价格 × 股数，
    最后除以总股数得到【加权平均成本价】，而不是简单算术平均——
    分多笔成交建仓时，只有加权平均才是真实成本。

    【unallocated 的意义】
    卖出股数超过了所有候选 lot 的可用股数，说明账本对不上。
    这不一定是 bug：托管起始日之前的历史持仓不在 lots 里，卖它们必然对不上，
    这类属于可忽略的历史噪音。但如果对不上的是【托管范围内】的批次，
    那就是真的错了，调用方会直接抛异常拒绝生成台账（见 build_ledger）。
    """
    known = set(multi_strategy.STRATEGY_CONFIGS)
    lots = {}                       # batch_id → lot 明细
    queues = defaultdict(list)      # (策略,代码) → 按建仓先后排列的 batch_id 队列，供 FIFO 使用
    unallocated = []                # 无法归属的卖出成交
    # ★ 严格按成交时间排序后重放 ★ 带上原始下标保证排序稳定、结果可复现
    indexed = sorted(enumerate(deal_rows), key=lambda pair: _row_sort_key(pair[1], pair[0]))
    for _index, row in indexed:
        code = _code_with_suffix(row)
        # 股数/价格解析失败的行直接跳过，不能让一行脏数据毁掉整个账本
        try:
            volume = int(row.get("m_nVolume") or 0)
            price = float(row.get("m_dPrice") or 0.0)
        except (TypeError, ValueError):
            continue
        if not code or volume <= 0:
            continue

        # ---------- 买入成交：建仓或加仓 ----------
        if _is_buy(row, known):
            strategy = str(row.get("m_strRemark")).strip()   # 买单备注就是裸策略名
            buy_date = str(row.get("m_strTradeDate") or "").strip()
            if not _DATE_RE.match(buy_date) or price <= 0:
                continue
            # batch_id = 策略:买入日期:代码 —— 三元组唯一确定一个 lot
            bid = "%s:%s:%s" % (strategy, buy_date, code)
            lot = lots.setdefault(bid, {
                "batch_id": bid, "strategy": strategy, "code": code,
                "buy_date": buy_date, "shares": 0, "notional": 0.0,
                "sold_shares": 0,
            })
            lot["shares"] += volume                 # 累加股数
            lot["notional"] += price * volume       # 累加金额，用于算加权平均成本
            # 新 lot 入 FIFO 队尾（已在队列里就不重复追加）
            if bid not in queues[(strategy, code)]:
                queues[(strategy, code)].append(bid)
            continue

        # ---------- 卖出成交：冲减持仓 ----------
        if not _is_sell(row):
            continue        # 既不是我们的买单也不是我们的卖单，忽略
        parsed = parse_sell_remark(row.get("m_strRemark"))
        strategy, buy_date, tagged_code6, _reason = parsed
        # 安全校验：备注里写的代码和成交行的代码对不上 → 数据严重异常，
        # 绝不能瞎归属，记为危险项让上层拒绝发布
        if tagged_code6 and tagged_code6 != _code6(code):
            unallocated.append({"remark": row.get("m_strRemark"), "code": code,
                                "volume": volume, "reason": "tag_code_mismatch"})
            continue
        if buy_date:
            # 新格式：备注自带买入日期 → 候选只有唯一那个 lot，精确冲减
            candidates = ["%s:%s:%s" % (strategy, buy_date, code)]
        else:
            # 旧格式：只知道策略和代码 → 沿 FIFO 队列从最早的 lot 开始冲减
            candidates = list(queues.get((strategy, code), []))
        left = volume       # 这笔卖出还剩多少股没有归属
        for bid in candidates:
            lot = lots.get(bid)
            if not lot:
                continue
            available = int(lot["shares"]) - int(lot["sold_shares"])   # 该 lot 还剩多少可卖
            take = min(left, max(0, available))     # 本 lot 能吃下多少
            lot["sold_shares"] += take
            left -= take
            if left <= 0:
                break       # 全部归属完毕
        # 还有剩余没归属掉：判断严重程度
        if left > 0:
            # 该策略+代码下是否存在【托管范围内】的 lot？
            # 有 → 说明是托管批次的账对不上，危险；
            # 没有 → 多半是在卖托管起始日之前的历史持仓，属于可忽略的噪音。
            managed_candidates = [lot for lot in lots.values()
                                  if lot["strategy"] == strategy
                                  and lot["code"] == code
                                  and lot["buy_date"] >= managed_start]
            unallocated.append({"remark": row.get("m_strRemark"), "code": code,
                                "volume": volume, "unallocated": left,
                                "managed_key": bool(managed_candidates)})
    return lots, unallocated


def group_buys(deal_rows, managed_start=MANAGED_BUY_START):
    """兼容性入口：老调用方按 (策略,买入日期,代码) 元组取分组，现已统一为 lot。

    保留这个函数只是为了不破坏既有调用（主要是测试），新代码直接用 build_lots。
    """
    lots, _unallocated = build_lots(deal_rows, managed_start)
    return {(v["strategy"], v["buy_date"], v["code"]): dict(v)
            for v in lots.values() if v["buy_date"] >= managed_start}


def build_batches(groups, default_buy_date=None, force_overrides=None):
    """把 lot 换算成台账条目：计算成本价、止盈目标价、强平日期时刻。

    ★ 这是「大脑把复杂度全部吃掉」的地方 ★
    执行器不需要任何交易日历、不需要知道策略配置、不需要算持有天数——
    它只做「今天 >= force_sell_date 吗」「最高价 >= target_price 吗」这种纯比较。
    所有需要日历和配置的推算都在这个函数里一次性算成绝对值。

    只输出【还有剩余股数】的 lot：已经全部卖光的批次不进台账。
    """
    force_overrides = force_overrides or {}
    batches = []
    values = groups.values() if isinstance(groups, dict) else groups
    # 按 batch_id 排序，保证每次生成的台账顺序稳定，便于 diff 对比
    for agg in sorted(values, key=lambda x: x["batch_id"]):
        shares = int(agg["shares"])
        sold = int(agg.get("sold_shares") or 0)
        if shares <= 0 or sold >= shares:
            continue        # 已清仓的批次不进台账
        sid = agg["strategy"]
        code = agg["code"]
        buy_date = agg.get("buy_date") or default_buy_date
        # ★ 加权平均成本价 ★ 总金额 ÷ 总股数，多笔成交建仓时这才是真实成本
        buy_price = round(float(agg["notional"]) / shares, 4)
        cfg = multi_strategy.STRATEGY_CONFIGS.get(sid) or {}
        tp = float(cfg.get("take_profit_rate", multi_strategy.DEFAULT_TAKE_PROFIT_RATE))
        mode = str(cfg.get("holding_mode") or "next_day")
        # 按持有模式推算强平日期和时刻
        if mode == "next_day":
            # 隔日策略：下一个交易日 10:00 就走
            force_date = multi_strategy.next_trading_day(buy_date)
            force_time = NEXT_DAY_FORCE_TIME
        else:
            # 多日持有：往后推 holding_days 个交易日，到期日 14:55 尾盘走
            force_date = _add_trading_days(buy_date, int(cfg.get("holding_days") or 3))
            force_time = EXPIRY_FORCE_TIME
        # 人工覆盖：允许命令行 --force-override 指定某个批次的强平时点，
        # 用于特殊情况下手动干预（如提前清仓）
        override = force_overrides.get(agg["batch_id"])
        if override:
            force_date, force_time = override
        batches.append({
            "batch_id": agg["batch_id"],
            # sell_tag：执行器下卖单时会用它 + t/e 作为备注，
            # 这样成交回来后本脚本才能精确归属到这一个 lot
            "sell_tag": make_sell_tag(sid, buy_date, code),
            "strategy": sid, "code": code, "buy_date": buy_date,
            "buy_price": buy_price,             # 加权平均成本
            "shares": shares,                   # 建仓总股数
            "sold_shares": sold,                # 已卖股数（真实成交）
            "remaining_shares": shares - sold,  # 待卖股数
            "take_profit_rate": tp,             # 止盈率（来自策略配置）
            # ★ 执行器只比这个绝对价格 ★
            "target_price": round(buy_price * (1.0 + tp), 4),
            "force_sell_date": force_date,      # ★ 执行器只比这个绝对日期 ★
            "force_sell_time": force_time,
        })
    return batches


def build_ledger(payload, trade_date, account, live, managed_start=MANAGED_BUY_START,
                 force_overrides=None):
    """组装完整台账字典。发现危险的账目不平时【直接抛异常拒绝生成】。

    ★ 什么算「危险」★ 两种情况：
      1. managed_key=True —— 托管范围内的批次有卖出成交对不上账；
      2. tag_code_mismatch —— 备注里的代码和成交代码不符。
    这两种都意味着账本状态不可信。此时宁可不生成台账（让执行器继续用旧台账），
    也绝不能发布一份错的——错的台账会导致漏卖或超卖。

    托管起始日之前的历史持仓卖出对不上账属于正常噪音，只计入诊断信息不报错。
    """
    lots, unallocated = build_lots(_deal_rows(payload), managed_start)
    dangerous = [item for item in unallocated
                 if item.get("managed_key") or item.get("reason") == "tag_code_mismatch"]
    if dangerous:
        raise ValueError("unallocated managed sell fills: %r" % dangerous)
    # 只接管起始日之后建仓的 lot
    managed = [lot for lot in lots.values() if lot["buy_date"] >= managed_start]
    batches = build_batches(managed, trade_date, force_overrides)
    return {
        "trade_date": trade_date, "account": account, "account_type": "STOCK",
        # ★ 卖出侧总开关 ★ 执行器读的就是这个字段，与买入侧的 live 相互独立
        "live": bool(live),
        "managed_buy_start": managed_start,
        "note": "Durable-archive FIFO sell ledger; executor compares absolute targets only.",
        "generated_at": None,           # 由 main() 填入生成时刻
        "source": "qmt_build_sell_ledger",
        "batches": batches,
        # 诊断信息：处理了多少成交行，忽略了多少条历史噪音
        "diagnostics": {"deal_rows": len(_deal_rows(payload)),
                        "legacy_unallocated_ignored": len(unallocated) - len(dangerous)},
    }


def build_state(ledger, old_state=None):
    """生成配套的卖出状态文件内容。

    ★ 关键：必须【继承】旧状态里的 max_price_seen ★
    最高价棘轮是执行器在盘中一格一格推上去的，记录着这个批次持有期见过的最高价。
    如果每次重建台账都把它清零，那么「盘中曾经触及目标价」这个止盈条件就废了——
    重建之后棘轮从 0 开始，之前冲高的记录全部丢失。

    而 sold_shares 则相反，直接用台账里刚算出来的真实成交量【覆盖】，
    因为那才是权威值（本脚本刚从归档重放算出来的）。
    """
    state = dict(old_state or {})
    for batch in ledger.get("batches") or []:
        bid = batch["batch_id"]
        previous = state.get(bid) if isinstance(state.get(bid), dict) else {}
        state[bid] = {"max_price_seen": float(previous.get("max_price_seen") or 0.0),  # 继承
                      "sold_shares": int(batch.get("sold_shares") or 0)}               # 覆盖
    return state


def _position_rows(fills):
    """从执行器快照里取持仓行，供对账校验用。兼容 dict 和 list 两种结构。"""
    section = ((fills or {}).get("account_snapshot") or {}).get("POSITION") or {}
    return section.get("rows") or [] if isinstance(section, dict) else section or []


def validate_ledger(ledger, fills=None, expected_account=DEFAULT_ACCOUNT):
    """发布前的全面自检。★ 任何一条不通过就抛异常，绝不发布残缺台账 ★

    这是台账进入实盘路径前的最后一道关卡，检查六件事：
      1. 账号必须是预期的模拟账户（防止误对实盘账户生成台账）；
      2. batch_id 和 sell_tag 都不能重复（重复会导致卖出成交归属错乱）；
      3. 股数自洽：总数 > 0、已卖不为负、已卖不超总数、剩余 == 总数 - 已卖；
      4. 买入日期格式合法；
      5. sell_tag 能被反向解析回原始三元组（保证执行器下单时带的备注可回溯）；
      6. ★ 与实际持仓对账：台账里某只票的待卖股数不能超过账户实际持仓 ★

    第 6 条最关键——它防的是「台账让执行器去卖根本没有的股票」，
    那种情况下执行器会反复重试永远卖不掉，直到耗尽追价次数。
    """
    errors = []
    # 1. 账号校验
    if str(ledger.get("account")) != str(expected_account):
        errors.append("unexpected account")
    seen_ids, seen_tags = set(), set()
    remaining_by_code = defaultdict(int)     # 按代码汇总待卖股数，供第 6 条对账
    for batch in ledger.get("batches") or []:
        bid, tag = batch.get("batch_id"), batch.get("sell_tag")
        # 2. 唯一性
        if bid in seen_ids or tag in seen_tags:
            errors.append("duplicate batch/tag %s" % bid)
        seen_ids.add(bid); seen_tags.add(tag)
        # 3. 股数自洽
        shares = int(batch.get("shares") or 0)
        sold = int(batch.get("sold_shares") or 0)
        remaining = int(batch.get("remaining_shares") or 0)
        if shares <= 0 or sold < 0 or sold > shares or remaining != shares - sold:
            errors.append("invalid shares %s" % bid)
        # 4. 日期格式
        if not _DATE_RE.match(str(batch.get("buy_date") or "")):
            errors.append("invalid buy date %s" % bid)
        # 5. 标签可逆：拼上 "e" 后应能解析回 (策略, 买入日期, 代码6位)
        parsed = parse_sell_remark(str(tag) + "e")
        if not parsed or parsed[:3] != (batch.get("strategy"), batch.get("buy_date"),
                                       _code6(batch.get("code"))):
            errors.append("invalid sell tag %s" % bid)
        remaining_by_code[batch.get("code")] += remaining
    # 6. 与账户实际持仓对账（同一只票可能有多个批次，所以要先按代码汇总）
    if fills:
        positions = defaultdict(int)
        for row in _position_rows(fills):
            positions[_code_with_suffix(row)] += int(row.get("m_nVolume") or 0)
        for code, remaining in remaining_by_code.items():
            # 只在该票确实出现在持仓表里时才比对：
            # 取不到持仓数据时不报错，避免因快照缺失而误拒
            if code in positions and remaining > positions[code]:
                errors.append("ledger exceeds position %s %d>%d" %
                              (code, remaining, positions[code]))
    if errors:
        raise ValueError("; ".join(errors))
    return True


def _read_json(path, default=None):
    """读 JSON，文件不存在或内容损坏时返回默认值，不抛异常。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else default


def _atomic_json(path, value):
    """★ 原子写 JSON ★ 保证执行器永远读不到写了一半的文件。

    这一点至关重要：执行器每 3 秒就读一次台账，如果用普通的 open(w) 直接覆盖，
    它完全可能在写入过程中读到半截内容——轻则解析失败，重则读到一份
    缺了若干批次的台账，导致该卖的票没卖。

    四步保证安全：
      1. 先写到【同目录下】的临时文件（必须同目录，跨盘符无法原子替换）；
      2. flush + fsync，强制刷到磁盘，不停留在系统缓存里；
      3. 回读一遍验证 JSON 完整可解析（写坏了就不会替换正式文件）；
      4. os.replace 原子替换——这个操作在同一文件系统内是原子的，
         读取方要么看到完整的旧文件，要么看到完整的新文件，不存在中间态。
    finally 确保临时文件在任何情况下都被清理，不留垃圾。
    """
    folder = os.path.dirname(os.path.abspath(path))
    fd, temporary = tempfile.mkstemp(prefix=".qmt-ledger-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())       # 强制落盘
        with open(temporary, "r", encoding="utf-8") as handle:
            json.load(handle)               # 回读验证完整性
        os.replace(temporary, path)         # 原子替换
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def publish(ledger, ledger_path=DEFAULT_LEDGER_PATH, state_path=DEFAULT_STATE_PATH,
            fills=None):
    """正式发布台账和状态文件（先校验，再原子写）。

    ★ 写入顺序：先写 state，再写 ledger ★
    执行器是靠 ledger 驱动的，只有 ledger 落地后新批次才会被处理。
    先把 state 准备好，能保证执行器读到新 ledger 的那一刻，
    配套的棘轮和已卖股数已经就位，不会出现「有批次但没状态」的空窗。
    """
    validate_ledger(ledger, fills)
    old_state = _read_json(state_path, {})
    state = build_state(ledger, old_state)      # 继承旧棘轮
    _atomic_json(state_path, state)             # 先 state
    _atomic_json(ledger_path, ledger)           # 后 ledger
    return state


def _resolve_trade_date(cli, payload):
    """确定台账的交易日期。

    优先用命令行显式指定的；否则取归档里【最晚的一条成交日期】；
    归档为空时退回今天。用最晚成交日而不是今天，是为了让补跑历史日期时
    生成的台账带上正确的日期标记。
    """
    if cli.trade_date:
        return cli.trade_date
    dates = [str(row.get("m_strTradeDate") or "").strip() for row in _deal_rows(payload)]
    return max([date for date in dates if date] or [dt.datetime.now().strftime("%Y%m%d")])


def _parse_overrides(values):
    """解析 --force-override 参数，格式 "batch_id=YYYYMMDD@HH:MM:SS"。

    返回 {batch_id: (日期, 时刻)}，用于人工覆盖某个批次的强平时点。
    """
    result = {}
    for value in values or []:
        bid, when = value.split("=", 1)
        date, hms = when.split("@", 1)
        result[bid] = (date, hms)
    return result


def parse_cli():
    """命令行参数定义。

    ★ 默认【不发布】★ 不加 --publish 时只写一份 .preview 文件供人工检查，
    正式的 qmt_sell_ledger.json 不会被动。这是刻意的安全默认值：
    误跑一次不会影响实盘，必须显式加 --publish 才会真正发布。
    """
    parser = argparse.ArgumentParser(description="从持久真实成交归档生成 QMT 卖出台账")
    parser.add_argument("--archive-path", default=DEFAULT_ARCHIVE_PATH)   # 成交归档（数据源）
    parser.add_argument("--fills-path", default=DEFAULT_FILLS_PATH)       # 执行器快照（对账用）
    parser.add_argument("--ledger-path", default=DEFAULT_LEDGER_PATH)     # 台账输出路径
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH)       # 状态输出路径
    parser.add_argument("--account", default=DEFAULT_ACCOUNT)             # 账号（会被校验）
    parser.add_argument("--trade-date", default="")                       # 留空则自动推断
    parser.add_argument("--managed-start", default=MANAGED_BUY_START)     # 托管起始日
    parser.add_argument("--force-override", action="append", default=[],
                        help="batch_id=YYYYMMDD@HH:MM:SS")                # 人工覆盖强平时点
    parser.add_argument("--dry", action="store_true", help="生成 live=false 预览")
    parser.add_argument("--publish", action="store_true", help="校验后原子发布 ledger/state")
    return parser.parse_args()


def main():
    """入口：读归档 → 重放建账 → 校验 → 发布或预览。

    注意 --dry 和 --publish 是两个独立维度：
      --dry      控制台账里的 live 字段（false = 执行器只演算不下单）
      --publish  控制写到正式路径还是 .preview 文件
    两者组合可以实现「发布一份关着开关的台账」这种过渡状态。
    """
    cli = parse_cli()
    archive = _read_json(cli.archive_path, {})
    fills = _read_json(cli.fills_path, {})
    trade_date = _resolve_trade_date(cli, archive)
    ledger = build_ledger(archive, trade_date, cli.account, live=(not cli.dry),
                          managed_start=cli.managed_start,
                          force_overrides=_parse_overrides(cli.force_override))
    ledger["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    validate_ledger(ledger, fills)      # 发布前再校验一次（publish 内部还会再验）
    if cli.publish:
        publish(ledger, cli.ledger_path, cli.state_path, fills)
        destination = cli.ledger_path
    else:
        # 安全默认：只写预览文件，不碰正式台账
        destination = cli.ledger_path + ".preview"
        _atomic_json(destination, ledger)
    print("[sell_ledger] %s live=%s batches=%d remaining=%d date=%s" %
          (destination, ledger["live"], len(ledger["batches"]),
           sum(b["remaining_shares"] for b in ledger["batches"]), trade_date))
    return ledger


if __name__ == "__main__":
    main()
