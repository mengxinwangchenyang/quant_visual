# -*- coding: utf-8 -*-  # 声明源码使用 UTF-8 编码，保证中文注释和中文路径可以被 Python 正确读取。
import argparse  # 导入命令行参数类型，用于标注策略运行参数。
import datetime as dt  # 导入日期时间模块，用于记录委托和摘要时间。
import math  # 导入数学模块，用于判断行情价格是否有效。
import os  # 导入系统模块，用于检查 miniQMT 用户数据目录。
import time  # 导入时间模块，用于生成交易会话编号。
from decimal import Decimal, ROUND_HALF_UP  # 导入十进制定点数模块，用于按交易最小价位四舍五入。
from typing import Any, Dict, List, Sequence, Tuple  # 导入类型标注，提升代码可读性。

from xtquant import xtconstant, xtdata  # 导入 miniQMT 常量和行情接口，用于直接委托和读取最新价。
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback  # 导入 miniQMT 交易客户端和回调基类。
from xtquant.xttype import StockAccount  # 导入股票账户对象，用于传给 xttrader 查询和委托接口。

import util  # 导入公共工具模块，用于日志、状态、CSV、交易日和通用查询。

DEFAULT_BUY_AMOUNT = 100000.0  # 设置默认单只股票目标买入金额。
DEFAULT_LOT_SIZE = 100  # 设置 A 股默认最小交易单位为 100 股。
DEFAULT_TAKE_PROFIT_RATE = 0.09  # 设置默认止盈比例为买入成本上浮 9%。
DEFAULT_SELL_MARKET_TIME = "10:00:00"  # 设置默认次日未止盈时的强制卖出时间。
STRATEGY_NAME = "virtual_qmt_trader"  # 设置提交给 miniQMT 的策略名称。
ORDER_REMARK_PREFIX = "virtual_qmt"  # 设置委托备注前缀，便于从当日委托和成交里识别本脚本订单。
CLEANUP_ORDER_PREFIX = "cleanup_non0708"  # 设置人工清理旧持仓委托备注前缀，这类委托不计入每日策略统计。
ACTIVE_ORDER_STATUSES = {xtconstant.ORDER_UNREPORTED, xtconstant.ORDER_WAIT_REPORTING, xtconstant.ORDER_REPORTED, xtconstant.ORDER_REPORTED_CANCEL, xtconstant.ORDER_PART_SUCC}  # 定义仍可能占用持仓或资金的活跃委托状态集合。
CANCEL_PENDING_ORDER_STATUSES = {xtconstant.ORDER_REPORTED_CANCEL}  # 定义已经发出撤单但柜台尚未释放股份的状态集合。
INACTIVE_RETRY_ORDER_STATUSES = {xtconstant.ORDER_CANCELED, xtconstant.ORDER_PARTSUCC_CANCEL, xtconstant.ORDER_PART_CANCEL, xtconstant.ORDER_JUNK}  # 定义可重新尝试卖出的非活跃委托状态集合。
MARKET_PRICE_TYPES = {xtconstant.MARKET_PEER_PRICE_FIRST, xtconstant.MARKET_SZ_CONVERT_5_CANCEL, xtconstant.MARKET_SH_CONVERT_5_CANCEL}  # 定义市价类卖单报价类型，避免强制卖出时重复撤单。
SELL_DUE_STATUSES = {"open", "buy_submitted", "buy_partial", "sell_failed", "sell_submitted", "sell_partial"}  # 定义到期卖出逻辑需要检查的计划状态集合。
SELL_RETRY_WAIT_SECONDS = 60.0  # 设置异步卖单提交后未查到委托时的最短重试等待时间。
CANCEL_RETRY_WAIT_SECONDS = 300.0  # 设置强制卖出撤单请求的最短重试等待时间，避免午休期间反复刷请求。
CANCEL_ACK_WAIT_SECONDS = 1800.0  # 设置委托号和合同号撤单均已受理后的等待时间，避免柜台返回重复撤单。


class TraderCallback(XtQuantTraderCallback):  # 定义空交易回调，供异步委托接口消费响应。
    def on_order_stock_async_response(self, response: Any) -> None:
        util.log(f"[order_async] seq={getattr(response, 'seq', '')} order_id={getattr(response, 'order_id', '')} remark={getattr(response, 'order_remark', '')} error={getattr(response, 'error_msg', '')}")

    def on_cancel_order_stock_async_response(self, response: Any) -> None:
        util.log(f"[cancel_async] {util.public_attrs(response)}")

    def on_cancel_error(self, cancel_error: Any) -> None:
        util.log(f"[cancel_error] {util.public_attrs(cancel_error)}")

    def on_order_error(self, order_error: Any) -> None:
        util.log(f"[order_error] {util.public_attrs(order_error)}")


def make_trader(path: str) -> XtQuantTrader:  # 定义创建并连接 miniQMT 交易客户端的函数。
    if not os.path.isdir(path):  # 检查用户数据目录是否存在。
        raise FileNotFoundError(f"miniQMT 用户数据目录不存在：{path}")  # 目录不存在时抛出明确异常。
    trader = XtQuantTrader(path, int(time.time()), TraderCallback())  # 用当前时间戳创建唯一交易会话并注册空回调。
    trader.set_relaxed_response_order_enabled(True)  # 放宽异步委托回调处理，避免响应阻塞主交易线程。
    trader.start()  # 启动 xttrader 内部工作线程。
    connect_code = trader.connect()  # 连接已登录的 miniQMT 客户端。
    if connect_code != 0:  # 判断交易连接返回码是否成功。
        trader.stop()  # 连接失败时停止内部线程。
        raise RuntimeError(f"连接 miniQMT 交易服务失败：{connect_code}")  # 抛出交易连接错误。
    return trader  # 返回已连接的交易客户端。


def make_account(account_id: str, account_type: str) -> StockAccount:  # 定义创建股票账户对象的函数。
    return StockAccount(account_id, account_type)  # 返回 xtquant 股票账户对象。


def subscribe_account(trader: XtQuantTrader, account: StockAccount) -> None:  # 定义订阅账户推送的函数。
    result = trader.subscribe(account)  # 订阅账户资产、委托和成交推送。
    util.log(f"[connect] subscribe account={account.account_id} result={result}")  # 记录订阅结果。


def valid_price(value: Any) -> bool:  # 定义判断价格是否可用的函数。
    try:  # 尝试转换为浮点数。
        price = float(value)  # 转换当前价格值。
    except Exception:  # 捕获非数字价格。
        return False  # 非数字价格不可用。
    return price > 0 and math.isfinite(price)  # 返回价格是否为正且有限。


def round_stock_price(value: Any) -> float:  # 定义股票价格按分位四舍五入的函数。
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))  # 返回按一分钱价格单位处理后的价格。


def code_limit_rate(code: str) -> float:  # 定义按股票代码推断普通涨停幅度的函数。
    if code.endswith(".BJ") or code.startswith("4") or code.startswith("8"):  # 判断是否北交所股票。
        return 0.30  # 北交所普通股票按百分之三十涨跌幅处理。
    if code.startswith("300") or code.startswith("301") or code.startswith("688"):  # 判断是否创业板或科创板股票。
        return 0.20  # 创业板和科创板普通股票按百分之二十涨跌幅处理。
    return 0.10  # 其他普通 A 股按百分之十涨跌幅处理。


def fallback_up_stop_price(code: str, pre_close: Any) -> float:  # 定义用昨收价估算最高买入挂单价的函数。
    if not valid_price(pre_close):  # 判断昨收价是否可用。
        return 0.0  # 昨收价不可用时返回零。
    return round_stock_price(float(pre_close) * (1.0 + code_limit_rate(code)))  # 按代码对应涨停幅度估算最高买入挂单价。


def fallback_down_stop_price(code: str, pre_close: Any) -> float:  # 定义用昨收价估算最低卖出挂单价的函数。
    if not valid_price(pre_close):  # 判断昨收价是否可用。
        return 0.0  # 昨收价不可用时返回零。
    return round_stock_price(float(pre_close) * (1.0 - code_limit_rate(code)))  # 按代码对应跌停幅度估算最低卖出挂单价。


def get_buy_limit_prices(codes: Sequence[str], fallback_prices: Dict[str, float]) -> Dict[str, float]:  # 定义批量读取买入最高可挂价的函数。
    prices: Dict[str, float] = {}  # 初始化股票代码到最高可挂买入价的映射。
    for code in codes:  # 遍历候选股票代码。
        detail: Dict[str, Any] = {}  # 初始化当前股票的合约详情。
        try:  # 尝试读取 miniQMT 合约详情。
            raw_detail = xtdata.get_instrument_detail(code) or {}  # 读取合约详情，优先使用其中的涨停价。
        except Exception as exc:  # 捕获合约详情读取异常。
            util.log(f"[price] 读取最高买入价失败 code={code} error={exc}")  # 记录合约详情读取失败原因。
            raw_detail = {}  # 失败时使用空详情继续兜底。
        if isinstance(raw_detail, dict):  # 判断合约详情是否为字典。
            detail = raw_detail  # 保存有效的合约详情字典。
        if valid_price(detail.get("UpStopPrice")):  # 优先判断 miniQMT 是否直接给出涨停价。
            prices[code] = round_stock_price(detail["UpStopPrice"])  # 保存 miniQMT 返回的最高可挂买入价。
            continue  # 已拿到最高价时处理下一只股票。
        if valid_price(detail.get("PreClose")):  # 判断是否可以用合约昨收价估算涨停价。
            prices[code] = fallback_up_stop_price(code, detail["PreClose"])  # 保存用昨收价估算出的最高买入挂单价。
            continue  # 已估算最高价时处理下一只股票。
        if valid_price(fallback_prices.get(code)):  # 判断是否可以用实时最新价兜底。
            prices[code] = round_stock_price(fallback_prices[code])  # 没有涨停价时使用最新价作为保守兜底委托价。
    return prices  # 返回股票代码到最高可挂买入价的映射。


def get_force_sell_limit_prices(codes: Sequence[str], fallback_prices: Dict[str, float]) -> Dict[str, float]:  # 定义批量读取强制卖出最低可挂价的函数。
    prices: Dict[str, float] = {}  # 初始化股票代码到强制卖出限价的映射。
    for code in codes:  # 遍历股票代码。
        detail: Dict[str, Any] = {}  # 初始化当前股票的合约详情。
        try:  # 尝试读取 miniQMT 合约详情。
            raw_detail = xtdata.get_instrument_detail(code) or {}  # 读取合约详情，优先使用其中的跌停价。
        except Exception as exc:  # 捕获合约详情读取异常。
            util.log(f"[price] 读取最低卖出价失败 code={code} error={exc}")  # 记录合约详情读取失败原因。
            raw_detail = {}  # 失败时使用空详情继续兜底。
        if isinstance(raw_detail, dict):  # 判断合约详情是否为字典。
            detail = raw_detail  # 保存有效的合约详情字典。
        if valid_price(detail.get("DownStopPrice")):  # 优先判断 miniQMT 是否直接给出跌停价。
            prices[code] = round_stock_price(detail["DownStopPrice"])  # 保存 miniQMT 返回的最低可挂卖出价。
            continue  # 已拿到最低价时处理下一只股票。
        if valid_price(detail.get("PreClose")):  # 判断是否可以用合约昨收价估算跌停价。
            prices[code] = fallback_down_stop_price(code, detail["PreClose"])  # 保存用昨收价估算出的最低卖出挂单价。
            continue  # 已估算最低价时处理下一只股票。
        if valid_price(fallback_prices.get(code)):  # 判断是否可以用实时最新价兜底。
            prices[code] = fallback_down_stop_price(code, fallback_prices[code])  # 没有跌停价时用最新价按跌停幅度估算激进卖出价。
    return prices  # 返回股票代码到强制卖出限价的映射。


def get_last_prices(codes: Sequence[str]) -> Dict[str, float]:  # 定义批量读取最新价的函数。
    prices: Dict[str, float] = {}  # 初始化股票代码到价格的映射。
    if not codes:  # 判断代码列表是否为空。
        return prices  # 空列表直接返回空价格映射。
    data = xtdata.get_full_tick(list(codes)) or {}  # 读取实时 tick 快照。
    for code, tick in data.items():  # 遍历 tick 返回结果。
        if not isinstance(tick, dict):  # 判断 tick 是否为字典。
            continue  # 非字典 tick 跳过。
        if valid_price(tick.get("lastPrice")):  # 优先判断最新价是否可用。
            prices[code] = float(tick["lastPrice"])  # 保存最新价。
        elif isinstance(tick.get("askPrice"), list) and tick["askPrice"] and valid_price(tick["askPrice"][0]):  # 最新价不可用时用卖一价兜底估算股数。
            prices[code] = float(tick["askPrice"][0])  # 保存卖一价作为估算价格。
    return prices  # 返回股票代码到价格的映射。


def price_type_value(name: str) -> Tuple[int, float]:  # 定义把价格类型文本转换为 QMT 参数的函数。
    if name.lower() == "latest":  # 判断是否使用最新价委托。
        return xtconstant.LATEST_PRICE, 0.0  # 最新价委托价格参数传 0。
    if name.lower() == "market":  # 判断是否使用对手方最优价委托。
        return xtconstant.MARKET_PEER_PRICE_FIRST, 0.0  # 市价类委托价格参数传 0。
    return xtconstant.LATEST_PRICE, 0.0  # 未知类型默认使用最新价委托。


def market_sell_price_type_value(code: str) -> Tuple[int, float]:  # 定义按交易所选择卖出市价报价类型的函数。
    if code.endswith(".SZ"):  # 判断是否深交所股票。
        return xtconstant.MARKET_SZ_CONVERT_5_CANCEL, 0.0  # 深交所卖出使用最优五档即时成交剩余撤销。
    if code.endswith(".SH") or code.endswith(".BJ"):  # 判断是否上交所或北交所股票。
        return xtconstant.MARKET_SH_CONVERT_5_CANCEL, 0.0  # 上交所和北交所卖出使用最优五档即时成交剩余撤销。
    return xtconstant.MARKET_PEER_PRICE_FIRST, 0.0  # 无法识别交易所时退回对手方最优价格委托。


def order_fields() -> List[str]:  # 定义委托记录 CSV 字段顺序。
    return ["event", "code", "date", "time", "side", "order_id", "order_volume", "price_type", "price", "status", "message", "sell_date", "target_price", "remark"]  # 返回委托记录字段列表。


def append_order_event(event: str, code: str, side: str, order_id: Any, volume: Any, price_type: Any, price: Any, status: str, message: str, sell_date: str = "", target_price: Any = "", remark: str = "") -> None:  # 定义追加委托事件的函数。
    row = {"event": event, "code": code, "date": util.today_yyyymmdd(), "time": dt.datetime.now().strftime("%H:%M:%S"), "side": side, "order_id": order_id, "order_volume": volume, "price_type": price_type, "price": price, "status": status, "message": message, "sell_date": sell_date, "target_price": target_price, "remark": remark}  # 构造 CSV 行。
    util.append_csv(util.TRADES_PATH, order_fields(), row)  # 写入委托事件 CSV。


def query_positions_map(trader: XtQuantTrader, account: StockAccount) -> Dict[str, Any]:  # 定义查询持仓映射的函数。
    positions = trader.query_stock_positions(account) or []  # 查询当前账户持仓。
    return {str(position.stock_code): position for position in positions}  # 按股票代码构造持仓映射。


def query_asset_dict(trader: XtQuantTrader, account: StockAccount) -> Dict[str, Any]:  # 定义查询资产字典的函数。
    asset = trader.query_stock_asset(account)  # 查询当前账户资产。
    return util.public_attrs(asset) if asset else {}  # 返回资产对象的普通字典。


def query_orders_list(trader: XtQuantTrader, account: StockAccount) -> List[Any]:  # 定义查询当日委托列表的函数。
    return trader.query_stock_orders(account, False) or []  # 查询当前账户当日全部委托，失败或为空时返回空列表。


def is_cleanup_remark(remark: Any) -> bool:  # 定义判断是否为人工清理旧持仓委托备注的函数。
    return str(remark or "").startswith(CLEANUP_ORDER_PREFIX)  # cleanup_non0708 不进入策略统计和批次状态。


def is_cleanup_order_row(row: Dict[str, Any]) -> bool:  # 定义判断普通委托/成交字典是否为清理单的函数。
    return is_cleanup_remark(row.get("order_remark") or row.get("remark"))  # 兼容 miniQMT 对象字典和本地 CSV 字段。


def order_status(order: Any) -> int:  # 定义读取委托状态的函数。
    return int(getattr(order, "order_status", 0) or 0)  # 返回 miniQMT 委托状态码。


def order_id_value(order: Any) -> int:  # 定义读取委托编号的函数。
    return int(getattr(order, "order_id", 0) or 0)  # 返回 miniQMT 委托编号。


def order_market_value(order: Any) -> int:  # 定义读取委托市场编号的函数。
    code = str(getattr(order, "stock_code", "") or "")  # 读取委托股票代码。
    if code.endswith(".SZ"):  # 判断是否为深市股票。
        return xtconstant.SZ_MARKET  # 返回深市编号。
    if code.endswith(".SH") or code.endswith(".BJ"):  # 判断是否为沪市或北交所股票。
        return xtconstant.SH_MARKET  # 返回沪市编号。
    return int(getattr(order, "market", 0) or getattr(order, "m_nMarket", 0) or 0)  # 无法从代码判断时使用委托对象字段兜底。


def order_sysid_value(order: Any) -> str:  # 定义读取柜台合同号的函数。
    return str(getattr(order, "order_sysid", "") or getattr(order, "m_strOrderSysID", "") or "")  # 返回 miniQMT 柜台合同号。


def order_is_active(order: Any) -> bool:  # 定义判断委托是否仍然活跃的函数。
    return order_status(order) in ACTIVE_ORDER_STATUSES  # 活跃状态表示委托可能仍占用股份或等待成交。


def order_type_value(order: Any) -> int:  # 定义读取委托买卖类型的函数。
    return int(getattr(order, "order_type", 0) or 0)  # 返回 miniQMT 委托买卖类型码。


def active_order_for_remark(orders: Sequence[Any], remark: str, order_type: int) -> Any:  # 定义按备注查找活跃委托的函数。
    for order in orders:  # 遍历当日委托列表。
        if str(getattr(order, "order_remark", "") or "") == remark and order_type_value(order) == order_type and order_is_active(order):  # 判断备注、方向和活跃状态是否匹配。
            return order  # 返回已存在的活跃委托。
    return None  # 没有找到时返回空。


def submitted_order_for_remark(orders: Sequence[Any], remark: str, order_type: int) -> Any:  # 定义按备注查找已提交且不需要重试的委托函数。
    for order in orders:  # 遍历当日委托列表。
        if str(getattr(order, "order_remark", "") or "") != remark or order_type_value(order) != order_type:  # 判断备注和方向是否不匹配。
            continue  # 不匹配时处理下一笔委托。
        if order_status(order) not in INACTIVE_RETRY_ORDER_STATUSES:  # 判断该委托不是撤单、废单等可重试终态。
            return order  # 返回已存在的有效同备注委托，避免同一批次重启后重复提交。
    return None  # 没有找到时返回空。


def active_sell_order_for_plan(orders: Sequence[Any], plan: Dict[str, Any]) -> Any:  # 定义查找计划对应活跃卖单的函数。
    sell_remark = str(plan.get("sell_remark") or "")  # 读取计划记录的卖出委托备注。
    if sell_remark:  # 判断计划是否有卖出委托备注。
        active_order = active_order_for_remark(orders, sell_remark, xtconstant.STOCK_SELL)  # 优先按备注查找活跃卖单。
        if active_order:  # 判断是否找到同备注活跃卖单。
            return active_order  # 返回同备注活跃卖单。
    sell_order_id = int(plan.get("sell_order_id") or 0)  # 读取计划记录的卖出委托编号。
    if sell_order_id <= 0:  # 判断计划是否没有卖出委托编号。
        return None  # 没有委托编号时返回空。
    for order in orders:  # 遍历当日委托列表。
        if order_id_value(order) == sell_order_id and order_is_active(order):  # 判断委托号匹配且仍然活跃。
            return order  # 返回找到的活跃卖单。
    return None  # 没有找到活跃卖单时返回空。


def latest_sell_order_for_plan(orders: Sequence[Any], plan: Dict[str, Any]) -> Any:  # 定义查找计划对应最新卖单的函数。
    sell_remark = str(plan.get("sell_remark") or "")  # 读取计划记录的卖出委托备注。
    sell_order_id = int(plan.get("sell_order_id") or 0)  # 读取计划记录的卖出委托编号。
    matches = []  # 初始化匹配委托列表。
    for order in orders:  # 遍历当日委托列表。
        if order_type_value(order) != xtconstant.STOCK_SELL:  # 判断是否不是卖出委托。
            continue  # 非卖出委托跳过。
        if sell_remark and str(getattr(order, "order_remark", "") or "") == sell_remark:  # 判断备注是否匹配。
            matches.append(order)  # 追加备注匹配的委托。
            continue  # 当前委托已匹配。
        if sell_order_id > 0 and order_id_value(order) == sell_order_id:  # 判断委托号是否匹配。
            matches.append(order)  # 追加委托号匹配的委托。
    if not matches:  # 判断是否没有匹配委托。
        return None  # 没有匹配时返回空。
    return sorted(matches, key=lambda item: int(getattr(item, "order_time", 0) or 0), reverse=True)[0]  # 返回最新委托。


def sell_order_may_retry(order: Any) -> bool:  # 定义判断卖单状态是否允许重试的函数。
    return order_status(order) in INACTIVE_RETRY_ORDER_STATUSES  # 废单、撤单或部成撤单可以重新尝试卖出。


def cancel_stock_order(trader: XtQuantTrader, account: StockAccount, code: str, order_id: int, reason: str, market: int = 0, order_sysid: str = "", prefer_sysid: bool = False) -> bool:  # 定义撤销股票委托的函数。
    if order_id <= 0:  # 判断委托编号是否有效。
        return False  # 无效委托编号不能撤单。
    if prefer_sysid and order_sysid:  # 判断是否优先使用柜台合同号撤单。
        result = trader.cancel_order_stock_sysid_async(account, int(market), str(order_sysid))  # 使用柜台合同号发起异步撤单，作为委托号撤单不释放时的备选路径。
        cancel_key = f"sysid={order_sysid} market={market}"  # 生成日志里的撤单键。
    else:  # 默认使用 miniQMT 委托编号撤单。
        result = trader.cancel_order_stock_async(account, int(order_id))  # 调用 miniQMT 异步撤单接口，避免同步撤单长时间阻塞卖出修复。
        cancel_key = f"order_id={order_id}"  # 生成日志里的撤单键。
    success = int(result) > 0  # 判断撤单请求序号是否有效。
    status = "cancel_sent" if success else "cancel_failed"  # 生成撤单记录状态。
    message = f"异步撤单请求已发出：{reason}" if success else f"撤单失败：{reason}"  # 生成撤单记录消息。
    util.log(f"[cancel] code={code} {cancel_key} result={result} reason={reason}")  # 写入撤单日志。
    append_order_event("cancel", code, "sell", order_id, "", "", "", status, message, remark=reason)  # 写入撤单 CSV 记录。
    return success  # 返回撤单指令是否成功发出。


def calc_order_volume(buy_amount: float, price: float, lot_size: int) -> int:  # 定义按目标金额计算买入股数的函数。
    if price <= 0:  # 判断价格是否非法。
        return 0  # 非法价格无法计算股数。
    return int(buy_amount / price / lot_size) * lot_size  # 按整手向下取整计算委托股数。


def order_remark(side: str, trade_date: str, code: str) -> str:  # 定义生成委托备注的函数。
    safe_code = code.replace(".", "")  # 去掉代码里的点号，便于备注简洁。
    return f"{ORDER_REMARK_PREFIX}_{side}_{trade_date}_{safe_code}"  # 返回可追踪的委托备注。


def parse_order_remark(remark: str) -> Dict[str, str]:  # 定义解析本策略委托备注的函数。
    parts = str(remark or "").split("_")  # 按下划线拆分备注。
    if len(parts) >= 5 and parts[0] == "virtual" and parts[1] == "qmt":  # 判断是否符合 virtual_qmt_side_date_code 格式。
        return {"side": parts[2], "date": parts[3], "code_key": parts[4]}  # 返回可恢复的备注字段。
    return {}  # 非本策略标准备注返回空。


def plan_storage_key(buy_date: Any, code: str) -> str:  # 定义持仓计划存储键，支持同一股票不同买入日共存。
    date_text = str(buy_date or "").replace("-", "")[:8]  # 标准化买入日期。
    return f"{date_text}:{code}" if date_text else code  # 有买入日时用日期加代码，否则回退旧代码键。


def plan_code_from_key(key: str, plan: Dict[str, Any]) -> str:  # 定义从计划键或计划内容恢复股票代码的函数。
    code = str(plan.get("code") or "")  # 优先读取计划内部代码。
    if code:  # 判断计划内部是否已有代码。
        return code  # 返回计划内部代码。
    text_key = str(key or "")  # 转换计划键为文本。
    return text_key.split(":", 1)[1] if ":" in text_key else text_key  # 新键取冒号后代码，旧键直接就是代码。


def iter_position_plans(plans: Dict[str, Dict[str, Any]]) -> List[Tuple[str, str, Dict[str, Any]]]:  # 定义遍历持仓计划的兼容函数。
    rows: List[Tuple[str, str, Dict[str, Any]]] = []  # 初始化计划列表。
    for key, plan in list(plans.items()):  # 遍历计划字典副本，允许过程中补字段。
        if not isinstance(plan, dict):  # 判断计划是否不是字典。
            continue  # 非字典计划跳过。
        code = plan_code_from_key(str(key), plan)  # 恢复股票代码。
        if code and not plan.get("code"):  # 判断计划内部是否缺少代码。
            plan["code"] = code  # 补齐代码字段。
        rows.append((str(key), code, plan))  # 追加兼容后的计划行。
    return rows  # 返回计划行列表。


def buy_plan_key(plans: Dict[str, Dict[str, Any]], buy_date: str, code: str) -> str:  # 定义获取买入计划键的函数。
    key = plan_storage_key(buy_date, code)  # 生成新格式计划键。
    if key in plans:  # 判断新格式计划是否已经存在。
        return key  # 返回新格式键。
    legacy_plan = plans.get(code)  # 读取旧格式代码键计划。
    if isinstance(legacy_plan, dict) and str(legacy_plan.get("buy_date") or "") == str(buy_date):  # 判断旧计划是否属于同一买入日。
        return code  # 保留旧键，兼容历史状态文件。
    return key  # 默认使用新格式键。


def ensure_buy_plan(plans: Dict[str, Dict[str, Any]], buy_date: str, code: str) -> Dict[str, Any]:  # 定义获取或创建买入计划的函数。
    key = buy_plan_key(plans, buy_date, code)  # 读取应使用的计划键。
    plan = plans.setdefault(key, {"code": code, "buy_date": buy_date})  # 获取或创建计划。
    plan.setdefault("code", code)  # 补齐股票代码。
    plan.setdefault("buy_date", buy_date)  # 补齐买入日期。
    return plan  # 返回计划对象。


def find_sell_plan_key(plans: Dict[str, Dict[str, Any]], code: str, sell_date: str, remark: str = "", order_id: int = 0) -> str:  # 定义按卖出信息查找计划键的函数。
    for key, plan_code, plan in iter_position_plans(plans):  # 遍历所有计划。
        if plan_code != code:  # 判断股票代码是否不匹配。
            continue  # 不匹配时处理下一条。
        if remark and str(plan.get("sell_remark") or "") == remark:  # 优先按卖出备注匹配。
            return key  # 返回匹配计划键。
        if order_id > 0 and int(plan.get("sell_order_id") or 0) == order_id:  # 其次按卖出委托号匹配。
            return key  # 返回匹配计划键。
    for key, plan_code, plan in iter_position_plans(plans):  # 再按卖出日期匹配。
        if plan_code == code and str(plan.get("sell_date") or "") == str(sell_date or ""):  # 判断代码和卖出日期是否匹配。
            return key  # 返回匹配计划键。
    return code  # 找不到时回退旧代码键。


def submit_stock_order(trader: XtQuantTrader, account: StockAccount, code: str, side: str, volume: int, price_type_name: str, remark: str, execute: bool, limit_price: float = 0.0) -> int:  # 定义提交股票委托的函数。
    if valid_price(limit_price):  # 判断委托是否指定了固定限价。
        price_type, price = xtconstant.FIX_PRICE, round_stock_price(limit_price)  # 买入时使用固定限价和最高可挂价提交。
        actual_price_type_name = "limit_up" if side == "buy" else ("force_limit" if price_type_name.lower() == "force_limit" else "target_limit")  # 买入记录涨停限价，卖出记录止盈或强制限价。
    elif side == "sell" and price_type_name.lower() == "market":  # 判断卖出委托是否要求市价类报价。
        price_type, price = market_sell_price_type_value(code)  # 按交易所选择卖出市价类报价类型。
        actual_price_type_name = "market_sell"  # 记录卖出委托实际使用市价卖出。
    else:  # 没有指定买入限价时。
        price_type, price = price_type_value(price_type_name)  # 解析原有价格类型和价格参数。
        actual_price_type_name = price_type_name  # 记录实际使用的原始价格类型。
    order_type = xtconstant.STOCK_BUY if side == "buy" else xtconstant.STOCK_SELL  # 根据买卖方向选择委托类型。
    if not execute:  # 判断是否为预演模式。
        util.log(f"[order] 预演 side={side} code={code} volume={volume} price_type={actual_price_type_name} price={price} remark={remark}")  # 记录预演委托。
        append_order_event("preview", code, side, "", volume, price_type, price, "preview", "未开启 --execute，不提交 miniQMT", remark=remark)  # 写入预演记录。
        return 0  # 预演模式返回 0。
    use_async = True  # 买卖都使用异步接口，避免同步下单在已送单后卡住主进程。
    util.log(f"[order] submit_start side={side} code={code} volume={volume} price_type={actual_price_type_name} price={price} async={use_async} remark={remark}")  # 先记录即将提交的委托，便于定位卡点。
    if use_async:  # 判断是否使用异步委托接口。
        order_id = trader.order_stock_async(account, code, order_type, int(volume), price_type, price, STRATEGY_NAME, remark)  # 调用 miniQMT 异步委托接口下单。
    else:  # 保留同步接口分支，便于后续需要时快速切换。
        order_id = trader.order_stock(account, code, order_type, int(volume), price_type, price, STRATEGY_NAME, remark)  # 调用 miniQMT 同步委托接口直接下单。
    status = "submitted" if int(order_id) > 0 else "rejected"  # 根据返回委托号判断提交状态。
    message = "委托请求已提交" if int(order_id) > 0 else "委托提交失败"  # 生成记录消息。
    util.log(f"[order] side={side} code={code} volume={volume} order_id={order_id} price_type={actual_price_type_name} price={price} status={status}")  # 记录委托提交结果。
    append_order_event("order", code, side, order_id, volume, price_type, price, status, message, remark=remark)  # 写入委托记录。
    return int(order_id)  # 返回 miniQMT 委托编号。


def buy_candidates(trader: XtQuantTrader, account: StockAccount, state: Dict[str, Any], trade_date: str, codes: Sequence[str], args: argparse.Namespace) -> None:  # 定义买入候选股票的函数。
    if not codes:  # 判断候选股票是否为空。
        util.log("[buy] 候选为空，跳过买入")  # 记录空候选信息。
        return  # 空候选直接返回。
    prices = get_last_prices(codes)  # 读取最新价用于计算委托股数。
    buy_limit_prices = get_buy_limit_prices(codes, prices)  # 读取买入委托最高可挂价。
    plans: Dict[str, Dict[str, Any]] = state.setdefault("position_plans", {})  # 获取或创建持仓计划字典。
    orders = query_orders_list(trader, account)  # 查询当日委托，用于避免重启后重复提交已存在买单。
    sell_date = util.next_trading_day(trade_date)  # 计算本批买入对应的计划卖出日期。
    bought = 0  # 初始化提交成功计数。
    rejected = 0  # 初始化拒绝计数。
    for code in codes:  # 遍历最终候选股票。
        price = prices.get(code, 0.0)  # 读取用于估算股数的最新价。
        if price <= 0:  # 判断是否拿到有效估算价格。
            append_order_event("buy_reject", code, "buy", "", "", "", "", "no_last_price", "没有可用最新价计算股数", sell_date=sell_date)  # 记录无价格拒单。
            rejected += 1  # 累加拒单数量。
            continue  # 跳过当前股票。
        volume = calc_order_volume(float(args.buy_amount), price, int(args.lot_size))  # 按最新价估算目标金额对应的整手委托股数。
        if volume <= 0:  # 判断目标金额是否足够买一手。
            append_order_event("buy_reject", code, "buy", "", volume, "", price, "lot_too_small", "目标金额不足一手", sell_date=sell_date)  # 记录股数不足拒单。
            rejected += 1  # 累加拒单数量。
            continue  # 跳过当前股票。
        limit_price = buy_limit_prices.get(code, 0.0)  # 读取当前股票买入委托最高可挂价。
        if limit_price <= 0:  # 判断最高可挂价是否可用。
            append_order_event("buy_reject", code, "buy", "", "", "", price, "no_limit_price", "没有可用最高买入挂单价", sell_date=sell_date)  # 记录无最高价拒单。
            rejected += 1  # 累加拒单数量。
            continue  # 跳过当前股票。
        remark = order_remark("buy", trade_date, code)  # 生成买入委托备注。
        existing_order = submitted_order_for_remark(orders, remark, xtconstant.STOCK_BUY)  # 查找 miniQMT 中是否已经存在同批买单。
        if existing_order:  # 判断是否已有活跃买单。
            order_id = order_id_value(existing_order)  # 读取已有委托编号。
            existing_volume = int(getattr(existing_order, "order_volume", 0) or volume)  # 读取已有委托数量。
            existing_price = float(getattr(existing_order, "price", 0.0) or limit_price)  # 读取已有委托价格。
            target_price = round(price * (1.0 + float(args.take_profit_rate)), 4)  # 计算计划止盈价。
            append_order_event("order", code, "buy", order_id, existing_volume, getattr(existing_order, "price_type", ""), existing_price, "submitted", "miniQMT已有同批买入委托，复用该委托", sell_date=sell_date, target_price=target_price, remark=remark)  # 写入已有委托记录。
            plan = ensure_buy_plan(plans, trade_date, code)  # 获取当前批次买入计划。
            plan.update({"code": code, "buy_date": trade_date, "sell_date": sell_date, "buy_order_id": order_id, "buy_price": price, "shares": existing_volume, "target_price": target_price, "status": "buy_submitted", "buy_remark": remark})  # 保存已有买入计划。
            bought += 1  # 已有活跃买单视为本轮已提交。
            continue  # 避免重复下单。
        order_id = submit_stock_order(trader, account, code, "buy", volume, args.order_price_type, remark, bool(args.execute), limit_price)  # 直接按最高可挂价向虚拟 miniQMT 提交买入委托。
        if order_id > 0 or not args.execute:  # 判断是否需要写入持仓计划。
            plan = ensure_buy_plan(plans, trade_date, code)  # 获取当前批次买入计划。
            plan.update({"code": code, "buy_date": trade_date, "sell_date": sell_date, "buy_order_id": order_id, "buy_price": price, "shares": volume, "target_price": round(price * (1.0 + float(args.take_profit_rate)), 4), "status": "buy_submitted" if args.execute else "preview", "buy_remark": remark})  # 保存买入计划和止盈价。
            bought += 1  # 累加买入提交数量。
        else:  # 委托提交失败时。
            rejected += 1  # 累加拒单数量。
    util.save_state(state)  # 保存更新后的状态。
    util.log(f"[buy] candidates={len(codes)} submitted={bought} rejected={rejected}")  # 记录本轮买入汇总。


def sync_trade_fills(trader: XtQuantTrader, account: StockAccount, state: Dict[str, Any], args: argparse.Namespace) -> None:  # 定义同步当日成交到状态文件的函数。
    trades = trader.query_stock_trades(account) or []  # 查询当日成交列表。
    plans: Dict[str, Dict[str, Any]] = state.setdefault("position_plans", {})  # 获取持仓计划字典。
    buy_fills: Dict[str, Dict[str, Any]] = {}  # 初始化买入成交聚合表，避免同一委托多笔成交时互相覆盖。
    sell_fills: Dict[str, Dict[str, Any]] = {}  # 初始化卖出成交聚合表，避免同一委托多笔成交时互相覆盖。
    for trade in trades:  # 遍历当日成交。
        remark = str(getattr(trade, "order_remark", "") or "")  # 读取成交对应委托备注。
        if not remark.startswith(ORDER_REMARK_PREFIX):  # 只处理本脚本提交的委托。
            continue  # 跳过非本脚本成交。
        code = str(getattr(trade, "stock_code", "") or "")  # 读取成交股票代码。
        if not code:  # 判断成交是否缺少股票代码。
            continue  # 缺少代码时跳过。
        parsed = parse_order_remark(remark)  # 从备注中恢复交易方向和日期。
        order_type = int(getattr(trade, "order_type", 0) or 0)  # 读取成交委托类型。
        order_id = int(getattr(trade, "order_id", 0) or 0)  # 读取成交对应委托编号。
        traded_price = float(getattr(trade, "traded_price", 0.0) or 0.0)  # 读取成交价格。
        traded_volume = int(getattr(trade, "traded_volume", 0) or 0)  # 读取成交数量。
        if order_type == xtconstant.STOCK_BUY and traded_volume > 0 and traded_price > 0:  # 判断是否为买入成交。
            buy_date = str(parsed.get("date") or "")  # 读取买入日期。
            plan_key = buy_plan_key(plans, buy_date, code) if buy_date else code  # 获取买入成交对应的计划键。
            plan = ensure_buy_plan(plans, buy_date, code) if buy_date else plans.setdefault(plan_key, {"code": code})  # 获取或创建该批次计划。
            plan.setdefault("code", code)  # 补齐股票代码。
            if parsed.get("date") and not plan.get("buy_date"):  # 如果状态里缺失买入日期则自动补齐。
                plan["buy_date"] = parsed["date"]  # 补齐买入日期。
            if plan.get("buy_date") and not plan.get("sell_date"):  # 如果状态里缺失计划卖出日则自动补齐。
                plan["sell_date"] = util.next_trading_day(str(plan["buy_date"]))  # 用买入日计算下一交易日卖出。
            if not plan.get("buy_order_id"):  # 如果状态里缺失买入委托号则自动补齐。
                plan["buy_order_id"] = order_id  # 从成交记录补齐委托号。
            if not plan.get("buy_remark"):  # 如果状态里缺失买入备注则自动补齐。
                plan["buy_remark"] = remark  # 补齐买入备注。
            fill = buy_fills.setdefault(plan_key, {"code": code, "buy_date": buy_date, "order_id": order_id, "remark": remark, "amount": 0.0, "volume": 0.0, "last_time": 0.0})  # 获取或创建当前批次买入聚合记录。
            fill["amount"] += traded_price * traded_volume  # 累加买入成交金额。
            fill["volume"] += traded_volume  # 累加买入成交数量。
            fill["last_time"] = max(fill["last_time"], float(getattr(trade, "traded_time", 0) or 0))  # 记录最后一笔买入成交时间。
            fill["order_id"] = order_id or int(fill.get("order_id") or 0)  # 记录买入委托号。
            fill["remark"] = remark or str(fill.get("remark") or "")  # 记录买入备注。
        if order_type == xtconstant.STOCK_SELL and traded_volume > 0 and traded_price > 0:  # 判断是否为卖出成交。
            sell_date = str(parsed.get("date") or "")  # 读取卖出日期。
            plan_key = find_sell_plan_key(plans, code, sell_date, remark, order_id)  # 查找卖出成交对应的计划键。
            plan = plans.setdefault(plan_key, {"code": code})  # 获取或创建该计划。
            plan.setdefault("code", code)  # 补齐股票代码。
            fill = sell_fills.setdefault(plan_key, {"code": code, "sell_date": sell_date, "order_id": order_id, "remark": remark, "amount": 0.0, "volume": 0.0, "last_time": 0.0})  # 获取或创建当前批次卖出聚合记录。
            fill["amount"] += traded_price * traded_volume  # 累加卖出成交金额。
            fill["volume"] += traded_volume  # 累加卖出成交数量。
            fill["last_time"] = max(fill["last_time"], float(getattr(trade, "traded_time", 0) or 0))  # 记录最后一笔卖出成交时间。
            fill["order_id"] = order_id or int(fill.get("order_id") or 0)  # 记录卖出委托号。
            fill["remark"] = remark or str(fill.get("remark") or "")  # 记录卖出备注，便于重启后去重。
    for plan_key, fill in buy_fills.items():  # 遍历聚合后的买入成交。
        code = str(fill.get("code") or plan_key)  # 读取股票代码。
        plan = plans.setdefault(plan_key, {"code": code})  # 获取或创建该批次计划。
        plan.setdefault("code", code)  # 补齐股票代码。
        if fill.get("buy_date") and not plan.get("buy_date"):  # 判断是否需要补齐买入日期。
            plan["buy_date"] = str(fill["buy_date"])  # 补齐买入日期。
        if plan.get("buy_date") and not plan.get("sell_date"):  # 判断是否需要补齐卖出日期。
            plan["sell_date"] = util.next_trading_day(str(plan["buy_date"]))  # 自动计算下一交易日。
        if fill.get("order_id") and not plan.get("buy_order_id"):  # 判断是否需要补齐买入委托号。
            plan["buy_order_id"] = int(fill["order_id"])  # 补齐买入委托号。
        if fill.get("remark") and not plan.get("buy_remark"):  # 判断是否需要补齐买入备注。
            plan["buy_remark"] = str(fill["remark"])  # 补齐买入备注。
        volume = int(fill["volume"])  # 读取聚合买入成交数量。
        avg_price = fill["amount"] / volume if volume > 0 else 0.0  # 计算加权平均买入成交价。
        if volume > 0 and avg_price > 0:  # 判断聚合买入成交是否有效。
            plan["buy_price"] = avg_price  # 用加权成交价更新买入价。
            plan["filled_shares"] = volume  # 记录真实累计成交数量。
            if not plan.get("shares"):  # 如果状态里缺失计划数量则用成交数量补齐。
                plan["shares"] = volume  # 补齐计划股数。
            plan["target_price"] = round(avg_price * (1.0 + float(args.take_profit_rate)), 4)  # 用真实成交价更新止盈价。
            plan["status"] = "open" if volume >= int(plan.get("shares") or 0) else "buy_partial"  # 根据成交量判断全成或部成。
            plan["buy_trade_time"] = int(fill["last_time"])  # 记录最后买入成交时间。
    for plan_key, fill in sell_fills.items():  # 遍历聚合后的卖出成交。
        code = str(fill.get("code") or plan_key)  # 读取股票代码。
        plan = plans.setdefault(plan_key, {"code": code})  # 获取或创建该批次计划。
        plan.setdefault("code", code)  # 补齐股票代码。
        volume = int(fill["volume"])  # 读取聚合卖出成交数量。
        avg_price = fill["amount"] / volume if volume > 0 else 0.0  # 计算加权平均卖出成交价。
        if volume > 0 and avg_price > 0:  # 判断聚合卖出成交是否有效。
            plan["sell_price"] = avg_price  # 记录加权卖出成交价。
            plan["sell_volume"] = volume  # 记录累计卖出成交量。
            if fill.get("order_id"):  # 判断聚合成交里是否有卖出委托号。
                plan["sell_order_id"] = int(fill["order_id"])  # 补齐卖出委托号。
            if fill.get("remark"):  # 判断聚合成交里是否有卖出备注。
                plan["sell_remark"] = str(fill["remark"])  # 补齐卖出备注。
            plan["status"] = "closed" if volume >= int(plan.get("filled_shares") or plan.get("shares") or 0) else "sell_partial"  # 根据卖出量判断全平或部成。
            plan["sell_trade_time"] = int(fill["last_time"])  # 记录最后卖出成交时间。
    util.save_state(state)  # 保存成交同步后的状态。


def sell_due_positions(trader: XtQuantTrader, account: StockAccount, state: Dict[str, Any], trade_date: str, args: argparse.Namespace) -> None:  # 定义卖出到期持仓的函数。
    plans: Dict[str, Dict[str, Any]] = state.setdefault("position_plans", {})  # 获取持仓计划字典。
    due_items = [(key, code, plan) for key, code, plan in iter_position_plans(plans) if plan.get("sell_date") == trade_date and plan.get("status") in SELL_DUE_STATUSES]  # 找出今天到期且仍需处理的计划。
    if not due_items:  # 判断是否存在到期持仓。
        return  # 无到期持仓时直接返回。
    due_codes = sorted({code for _key, code, _plan in due_items if code})  # 提取真实股票代码，用于查询持仓和行情。
    positions = query_positions_map(trader, account)  # 查询当前真实持仓。
    orders = query_orders_list(trader, account)  # 查询当前当日委托，用于识别已有止盈卖单。
    force_sell = dt.datetime.now().time() >= util.parse_hms(args.sell_market_time)  # 判断是否已经到强制卖出时间。
    force_sell_prices = get_force_sell_limit_prices(due_codes, get_last_prices(due_codes)) if force_sell else {}  # 十点后用跌停价限价单强制卖出，避开柜台不支持的市价类型。
    sold = 0  # 初始化卖出提交计数。
    canceled = 0  # 初始化撤单提交计数。
    for _plan_key, code, plan in due_items:  # 遍历到期计划。
        normal_remark = order_remark("sell", trade_date, code)  # 生成普通止盈卖出委托备注。
        force_remark = order_remark("sellforce", trade_date, code)  # 生成强制卖出委托备注，避免和早盘止盈单混淆。
        submit_remark = force_remark if force_sell else normal_remark  # 十点后新提交的卖单使用强制卖出备注。
        if not plan.get("sell_remark"):  # 判断状态里是否缺失卖出备注。
            plan["sell_remark"] = normal_remark  # 补齐卖出备注，方便重启后按备注去重。
        latest_order = latest_sell_order_for_plan(orders, plan)  # 查找当前计划的最新卖单，无论是否活跃。
        active_order = active_sell_order_for_plan(orders, plan)  # 查找当前计划是否已有活跃卖单。
        if active_order and force_sell:  # 判断十点后是否需要撤销未成止盈卖单。
            active_order_id = order_id_value(active_order)  # 读取活跃卖单委托编号。
            active_status = order_status(active_order)  # 读取活跃卖单状态。
            active_market = order_market_value(active_order)  # 读取委托所属市场，用于按柜台合同号撤单。
            active_sysid = order_sysid_value(active_order)  # 读取柜台合同号，用于委托号撤单无回报时兜底。
            active_price_type = int(getattr(active_order, "price_type", 0) or 0)  # 读取活跃卖单报价类型。
            active_price = float(getattr(active_order, "price", 0.0) or 0.0)  # 读取活跃卖单价格。
            force_price = float(force_sell_prices.get(code, 0.0) or 0.0)  # 读取当前股票强制卖出价格。
            if active_status in CANCEL_PENDING_ORDER_STATUSES:  # 判断是否已经处于待撤状态。
                continue  # 等待柜台释放股份后再提交强制卖出。
            already_force_order = active_order_id == int(plan.get("force_sell_order_id") or 0)  # 判断是否已经是本策略记录的强制卖单。
            already_force_price = force_price > 0 and active_price > 0 and active_price <= force_price + 0.000001  # 用价格兜底识别重启后仍活跃的强制限价卖单。
            if active_price_type in MARKET_PRICE_TYPES or already_force_order or already_force_price:  # 判断是否已经是强制卖出委托。
                plan["force_sell_order_id"] = active_order_id  # 补齐强制卖出委托号，避免后续重复撤单。
                plan["force_sell_price"] = active_price  # 记录强制卖出价格。
                continue  # 已有强制卖单时不再撤单或重复提交。
            already_cancel_ack = active_sysid and plan.get("force_cancel_order_id") == active_order_id and plan.get("force_cancel_sysid") == active_sysid  # 判断委托号和柜台合同号是否都已发过撤单。
            if already_cancel_ack and time.time() - float(plan.get("force_cancel_time") or 0) < CANCEL_ACK_WAIT_SECONDS:  # 判断柜台是否已经受理撤单且仍在等待释放。
                continue  # 已收到撤单受理后不再重复撤，等待柜台释放股份。
            prefer_sysid = bool(active_sysid and plan.get("force_cancel_order_id") == active_order_id and plan.get("force_cancel_sysid") != active_sysid)  # 如果同一委托号撤过仍未释放，则切换柜台合同号撤单。
            if not prefer_sysid and time.time() - float(plan.get("force_cancel_time") or 0) < CANCEL_RETRY_WAIT_SECONDS:  # 判断最近是否已经发过撤单请求。
                continue  # 等待柜台处理撤单回报和股份释放，避免重复刷撤单。
            if cancel_stock_order(trader, account, code, active_order_id, "十点强制卖出前撤销止盈限价卖单", active_market, active_sysid, prefer_sysid):  # 发出撤单指令。
                plan["status"] = "sell_failed"  # 临时标记为待重新卖出，等待股份释放后用强制限价重提。
                plan["force_cancel_order_id"] = active_order_id  # 记录本次尝试撤销的委托号。
                plan["force_cancel_sysid"] = active_sysid if prefer_sysid else ""  # 记录是否已经尝试过柜台合同号撤单。
                plan["force_cancel_time"] = time.time()  # 记录撤单时间，便于排查股份释放等待。
                canceled += 1  # 累加撤单数量。
            continue  # 本轮先等待撤单释放股份，下轮再提交强制卖出。
        if plan.get("status") == "sell_submitted" and latest_order is not None and not sell_order_may_retry(latest_order):  # 判断卖单已提交且不是可重试终态。
            continue  # 等待成交同步，不重复提交。
        if plan.get("status") == "sell_submitted" and latest_order is None and time.time() - float(plan.get("sell_submit_time") or 0) < SELL_RETRY_WAIT_SECONDS:  # 判断异步提交后等待回报时间不足。
            continue  # 给 miniQMT 回报留出时间，避免立刻重复提交。
        if plan.get("status") == "sell_submitted" and (latest_order is None or sell_order_may_retry(latest_order)):  # 判断已提交卖单需要重新尝试。
            plan["status"] = "sell_failed"  # 标记为可重试卖出。
        if active_order and not force_sell:  # 判断十点前是否已有止盈限价卖单。
            continue  # 已有卖出委托时不重复提交。
        position = positions.get(code)  # 读取真实持仓对象。
        can_use = int(getattr(position, "can_use_volume", 0) or 0) if position else 0  # 读取可卖数量。
        if can_use <= 0:  # 判断是否没有可卖数量。
            continue  # 不可卖时跳过当前股票。
        target_price = float(plan.get("target_price") or 0.0)  # 读取计划止盈价。
        if not force_sell and target_price <= 0:  # 判断十点前是否缺少止盈价格。
            continue  # 未触发时继续等待下一轮。
        sell_price_type = "force_limit" if force_sell else args.order_price_type  # 十点后用强制限价标记，十点前使用普通委托配置。
        limit_price = force_sell_prices.get(code, 0.0) if force_sell else target_price  # 十点后用最低可挂价，十点前用止盈价。
        if limit_price <= 0:  # 判断卖出限价是否可用。
            append_order_event("sell_reject", code, "sell", "", can_use, "", "", "no_sell_price", "没有可用卖出限价", remark=submit_remark)  # 写入无价格拒单。
            continue  # 无有效卖价时跳过。
        order_id = submit_stock_order(trader, account, code, "sell", can_use, sell_price_type, submit_remark, bool(args.execute), limit_price)  # 直接向虚拟 miniQMT 提交卖出委托。
        if order_id > 0 or not args.execute:  # 判断卖出委托是否已提交或处于预演模式。
            plan["sell_order_id"] = order_id  # 记录卖出委托编号。
            plan["sell_remark"] = submit_remark  # 记录卖出委托备注。
            plan["sell_submit_time"] = time.time()  # 记录卖出提交本地时间，避免异步回报未刷新时重复提交。
            plan["status"] = "sell_submitted" if args.execute else "preview_sell"  # 更新计划状态。
            if force_sell:  # 判断是否为强制卖出委托。
                plan["force_sell_order_id"] = order_id  # 记录强制卖出委托号。
                plan["force_sell_price"] = limit_price  # 记录强制卖出限价。
                plan["force_sell_submit_time"] = plan["sell_submit_time"]  # 记录强制卖出提交时间。
            sold += 1  # 累加卖出提交数量。
    util.save_state(state)  # 保存卖出计划更新。
    if sold or canceled:  # 判断本轮是否提交过卖出或撤单。
        util.log(f"[sell] submitted={sold} canceled={canceled} force={force_sell}")  # 记录卖出提交和撤单摘要。


def write_summary(trader: XtQuantTrader, account: StockAccount, state: Dict[str, Any], trade_date: str) -> None:  # 定义写入账户摘要的函数。
    asset = query_asset_dict(trader, account)  # 查询当前资产字典。
    positions = [util.public_attrs(position) for position in (trader.query_stock_positions(account) or [])]  # 查询并转换当前持仓列表。
    orders = [row for row in [util.public_attrs(order) for order in (trader.query_stock_orders(account, False) or [])] if not is_cleanup_order_row(row)]  # 查询并转换当日委托列表，过滤人工清理旧持仓委托。
    trades = [row for row in [util.public_attrs(trade) for trade in (trader.query_stock_trades(account) or [])] if not is_cleanup_order_row(row)]  # 查询并转换当日成交列表，过滤人工清理旧持仓成交。
    summary = {"date": trade_date, "time": dt.datetime.now().strftime("%H:%M:%S"), "asset": asset, "positions": positions, "orders": orders, "trades": trades, "plans": state.get("position_plans", {}), "multi_strategy": state.get("multi_strategy", {})}  # 构造完整账户摘要并保留七策略分账快照。
    util.save_json(util.SUMMARY_PATH, summary)  # 保存最新账户摘要 JSON。
    total_asset = float(asset.get("total_asset", 0.0) or asset.get("m_dTotalAsset", 0.0) or 0.0)  # 读取总资产字段。
    cash = float(asset.get("cash", 0.0) or asset.get("m_dCash", 0.0) or 0.0)  # 读取可用现金字段。
    market_value = float(asset.get("market_value", 0.0) or asset.get("m_dMarketValue", 0.0) or 0.0)  # 读取持仓市值字段。
    state.setdefault("daily_equity", {})[trade_date] = total_asset  # 把日终权益写入状态。
    state["last_summary"] = {"date": trade_date, "total_asset": total_asset, "cash": cash, "market_value": market_value, "position_count": len(positions), "order_count": len(orders), "trade_count": len(trades)}  # 保存简要摘要到状态。
    daily_row = dict(state["last_summary"])  # 复制摘要字典，避免为了写 CSV 修改状态里的摘要对象。
    daily_row["time"] = dt.datetime.now().strftime("%H:%M:%S")  # 补充 CSV 需要的当前写入时间字段。
    util.append_csv(util.DAILY_PATH, ["date", "time", "total_asset", "cash", "market_value", "position_count", "order_count", "trade_count"], daily_row)  # 追加每日统计 CSV。
    util.save_state(state)  # 保存更新后的状态文件。
    util.log(f"[summary] total_asset={total_asset:.2f} cash={cash:.2f} market_value={market_value:.2f} positions={len(positions)} orders={len(orders)} trades={len(trades)}")  # 记录账户摘要。
