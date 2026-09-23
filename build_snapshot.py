# -*- coding: utf-8 -*-  # 声明脚本使用 UTF-8 编码，保证中文注释和中文输出正常。
import csv  # 导入 CSV 模块，用于读取历史委托、撤单和拒单流水。
import json  # 导入 JSON 模块，用于读取状态文件和写入前端数据。
import re  # 导入正则模块，用于从委托备注中识别买入日和卖出日。
import subprocess  # 导入子进程模块，用于查询当前交易进程是否仍在运行。
import sys  # 导入系统模块，用于把项目根目录加入模块搜索路径。
from datetime import datetime, timedelta  # 导入日期时间模块，用于记录快照生成时间和截取行情窗口。
from pathlib import Path  # 导入路径模块，用于稳定定位项目目录和数据文件。
from typing import Any, Dict, Iterable, List, Optional, Set  # 导入类型标注，提升脚本可读性。
import pandas as pd  # 导入 pandas，用于整理 miniQMT 历史 tick 行情。
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # 计算项目根目录，方便从 visual 子目录导入项目模块。
if str(PROJECT_ROOT) not in sys.path:  # 判断项目根目录是否已经在模块搜索路径中。
    sys.path.insert(0, str(PROJECT_ROOT))  # 把项目根目录插到搜索路径最前面，确保导入当前项目代码。
from xtquant import xtconstant, xtdata  # 导入 miniQMT 常量和行情模块，用于识别委托状态并读取历史 tick。
import qmt_broker  # 导入项目券商模块，用于只读连接虚拟 miniQMT 账户。
import multi_strategy  # 导入七策略配置和账本辅助函数。
import strategy  # 导入策略模块，用于复用预测响应解析和板块识别逻辑。
import util  # 导入项目工具模块，用于行情连接和对象转换。
FIRST_BATCH_CODES: Set[str] = {"605118.SH", "002364.SZ", "002434.SZ", "001203.SZ", "300666.SZ", "301217.SZ", "300428.SZ", "603236.SH", "688146.SH", "688031.SH", "688167.SH", "688388.SH", "688381.SH", "688195.SH"}  # 定义第一批测试单股票集合。
SECOND_BATCH_CODES: Set[str] = {"600667.SH", "002428.SZ", "603698.SH", "002192.SZ", "600105.SH", "002046.SZ", "002536.SZ", "688403.SH", "688601.SH", "688575.SH", "688116.SH", "688807.SH", "688012.SH"}  # 定义第二批真实预测单股票集合。
THIRD_BATCH_CODES: Set[str] = {"600667.SH", "603678.SH", "600888.SH", "000636.SZ", "002484.SZ", "605376.SH", "002965.SZ", "600330.SH", "688380.SH", "688669.SH", "688381.SH", "688162.SH", "688585.SH", "688316.SH"}  # 定义第三批真实预测单股票集合。
FOURTH_BATCH_NAME = "第四批真实预测单（20260706）"  # 定义昨日买入今日卖出这批预测单的展示名称。
FOURTH_BUY_DATE = "20260706"  # 定义第四批策略买入日期。
FOURTH_SELL_DATE = "20260707"  # 定义第四批策略卖出日期。
FOURTH_BATCH_CODES: Set[str] = {"600246.SH", "002396.SZ", "002815.SZ", "001287.SZ", "301275.SZ", "603866.SH", "600064.SH", "002051.SZ", "688767.SH", "688234.SH", "688469.SH", "688191.SH", "688045.SH", "688055.SH"}  # 定义第四批预测股票集合，用于展示未成交和跳过项。
FIFTH_BATCH_NAME = "第五批真实预测单（20260707）"  # 定义最新一批昨日买入今日卖出的展示名称。
FIFTH_BUY_DATE = "20260707"  # 定义第五批策略买入日期。
FIFTH_SELL_DATE = "20260708"  # 定义第五批策略卖出日期。
FIFTH_BATCH_CODES: Set[str] = {"600120.SH", "600206.SH", "600094.SH", "001227.SZ", "002936.SZ", "300723.SZ", "600812.SH", "000589.SZ", "688008.SH", "688221.SH", "688253.SH", "688079.SH", "688428.SH", "688528.SH"}  # 定义第五批预测股票集合，用于展示未成交和跳过项。
DYNAMIC_BATCH_START = "20260706"  # 从这一交易日起按 T 买、T+1 卖自动生成可视化批次，避免每天改代码。
DYNAMIC_SELL_DATE_CACHE: Dict[str, str] = {}  # 缓存买入日对应的下一交易日，减少重复日历查询。
DYNAMIC_PREV_DATE_CACHE: Dict[str, str] = {}  # 缓存卖出日对应的上一交易日，方便从卖出备注反推买入日。
LEGACY_BATCHES = [("第三批真实预测单", THIRD_BATCH_CODES), ("第二批真实预测单", SECOND_BATCH_CODES), ("第一批测试单", FIRST_BATCH_CODES)]  # 定义老批次保留展示顺序。
FIRST_ORDER_MIN = 1082196991  # 定义第一批测试单买入委托号下界。
FIRST_ORDER_MAX = 1082197004  # 定义第一批测试单买入委托号上界。
SECOND_BUY_ORDER_IDS: Set[int] = {1098961827, 1098961828, 1098961829, 1098961830, 1098961831, 1098961833, 1098961834, 1098961835, 1098961836, 1098961837, 1098961838, 1098961839, 1098961841}  # 定义第二批真实预测买入委托号集合。
THIRD_ORDER_MIN = 1099007650  # 定义第三批真实预测买入委托号下界。
THIRD_ORDER_MAX = 1099007669  # 定义第三批真实预测买入委托号上界。
TIMELINE_LANES: List[str] = ["买入委托", "买入成交", "卖出委托", "卖出成交", "撤单/拒单"]  # 定义前端时间轴的纵向泳道顺序。
MARKET_PRICE_TYPES: Set[int] = {42, 47, 88}  # 定义 miniQMT 市价类卖出相关报价类型集合。
BOARD_LABELS: Dict[str, str] = {"csi1000": "中证1000", "kcb": "科创板"}  # 定义板块归一化名称到中文名称的映射。
MAX_STOCK_CHART_POINTS = 260  # 定义每只股票小图最多保留的行情点数，覆盖 T-1 和 T 日两段行情。
def safe_float(value: Any) -> float:  # 定义安全转换浮点数的函数。
    try:  # 尝试转换传入值。
        return float(value or 0)  # 返回转换后的浮点数。
    except Exception:  # 捕获转换失败异常。
        return 0.0  # 转换失败时返回零。
def safe_int(value: Any) -> int:  # 定义安全转换整数的函数。
    try:  # 尝试转换传入值。
        return int(value or 0)  # 返回转换后的整数。
    except Exception:  # 捕获转换失败异常。
        return 0  # 转换失败时返回零。
def compact_date(value: Any) -> str:  # 定义把日期压缩为 YYYYMMDD 的函数。
    text = str(value or "").strip()  # 转换日期文本并去掉空白。
    return text.replace("-", "")[:8]  # 去掉横线并截取八位日期。
def parse_time_text(date_value: Any, time_value: Any) -> Optional[datetime]:  # 定义把日期和时分秒文本转为时间对象的函数。
    date_text = compact_date(date_value)  # 标准化日期文本。
    time_text = str(time_value or "").strip()  # 标准化时间文本。
    if not date_text or not time_text:  # 判断日期或时间是否缺失。
        return None  # 缺少必要字段时返回空。
    try:  # 尝试解析标准日期时间。
        return datetime.strptime(f"{date_text} {time_text}", "%Y%m%d %H:%M:%S")  # 返回解析后的时间对象。
    except Exception:  # 捕获解析失败异常。
        return None  # 解析失败时返回空。
def parse_qmt_time(value: Any) -> Optional[datetime]:  # 定义把 miniQMT 时间整数转为时间对象的函数。
    timestamp = safe_int(value)  # 转换 miniQMT 时间整数。
    if timestamp <= 0:  # 判断时间整数是否无效。
        return None  # 无效时间返回空。
    try:  # 尝试按系统本地时区解析时间戳。
        return datetime.fromtimestamp(timestamp)  # 返回本地时间对象。
    except Exception:  # 捕获时间戳解析异常。
        return None  # 解析失败时返回空。
def time_label(moment: Optional[datetime]) -> str:  # 定义时间对象的前端显示文本函数。
    return moment.strftime("%Y-%m-%d %H:%M:%S") if moment else ""  # 返回完整日期时间文本。
def time_key(moment: Optional[datetime]) -> str:  # 定义排序用时间键函数。
    return moment.strftime("%Y%m%d%H%M%S") if moment else "99999999999999"  # 返回可排序字符串。
def previous_calendar_day(day_text: str) -> str:  # 定义获取前一自然日文本的函数。
    try:  # 尝试解析 YYYYMMDD 日期。
        return (datetime.strptime(day_text, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")  # 返回前一自然日的 YYYYMMDD 文本。
    except Exception:  # 捕获日期解析失败异常。
        return day_text  # 解析失败时保留原日期文本。
def next_calendar_day(day_text: str) -> str:  # 定义获取下一自然日文本的函数。
    try:  # 尝试解析 YYYYMMDD 日期。
        return (datetime.strptime(day_text, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")  # 返回下一自然日的 YYYYMMDD 文本。
    except Exception:  # 捕获日期解析失败异常。
        return day_text  # 解析失败时保留原日期文本。
def is_dynamic_buy_date(day_text: str) -> bool:  # 定义是否进入自动 T 买 T+1 卖批次规则的函数。
    text = compact_date(day_text)  # 标准化日期文本。
    return len(text) == 8 and text >= DYNAMIC_BATCH_START  # 从配置起始日之后的真实预测单自动归组。
def next_trading_day_text(day_text: str) -> str:  # 定义获取下一交易日文本的函数。
    text = compact_date(day_text)  # 标准化日期文本。
    if text in DYNAMIC_SELL_DATE_CACHE:  # 判断缓存是否已有结果。
        return DYNAMIC_SELL_DATE_CACHE[text]  # 返回缓存的下一交易日。
    try:  # 优先复用策略工具里的交易日判断。
        current = datetime.strptime(text, "%Y%m%d").date() + timedelta(days=1)  # 从下一自然日开始查找。
        while not util.is_trading_day(current, use_api=False):  # 只用本地节假日表，避免刷新看板访问外部日历接口。
            current += timedelta(days=1)  # 继续往后找。
        result = current.strftime("%Y%m%d")  # 转换为 YYYYMMDD 文本。
    except Exception:  # 日历接口或解析失败时兜底自然日。
        result = next_calendar_day(text)  # 使用下一自然日兜底。
    DYNAMIC_SELL_DATE_CACHE[text] = result  # 缓存结果。
    return result  # 返回下一交易日。
def previous_trading_day_text(day_text: str) -> str:  # 定义获取上一交易日文本的函数。
    text = compact_date(day_text)  # 标准化日期文本。
    if text in DYNAMIC_PREV_DATE_CACHE:  # 判断缓存是否已有结果。
        return DYNAMIC_PREV_DATE_CACHE[text]  # 返回缓存的上一交易日。
    try:  # 尝试按交易日历向前查找。
        current = datetime.strptime(text, "%Y%m%d").date() - timedelta(days=1)  # 从前一自然日开始。
        while not util.is_trading_day(current, use_api=False):  # 只用本地节假日表，避免刷新看板访问外部日历接口。
            current -= timedelta(days=1)  # 继续往前找。
        result = current.strftime("%Y%m%d")  # 转换回 YYYYMMDD 文本。
    except Exception:  # 日期解析或日历接口失败时兜底自然日。
        result = previous_calendar_day(text)  # 使用上一自然日兜底。
    DYNAMIC_PREV_DATE_CACHE[text] = result  # 缓存结果。
    return result  # 返回上一交易日。
def dynamic_batch_name(buy_date: str) -> str:  # 定义自动批次名称函数。
    text = compact_date(buy_date)  # 标准化买入日。
    return f"真实预测单（{text} 买 / {next_trading_day_text(text)} 卖）"  # 返回前端展示名称。
def dynamic_batch_buy_date(batch: str) -> str:  # 定义从自动批次名称中提取买入日的函数。
    match = re.search(r"（(\d{8}) 买", str(batch or ""))  # 匹配批次名里的买入日期。
    return match.group(1) if match else ""  # 返回买入日或空字符串。
def dynamic_batch_from_buy_date(buy_date: Any) -> str:  # 定义按买入日生成自动批次名的函数。
    text = compact_date(buy_date)  # 标准化买入日。
    return dynamic_batch_name(text) if is_dynamic_buy_date(text) else ""  # 只为自动规则范围内的日期返回批次。
def dynamic_batch_from_sell_date(sell_date: Any) -> str:  # 定义按卖出日反推自动批次名的函数。
    sell_text = compact_date(sell_date)  # 标准化卖出日。
    if len(sell_text) != 8:  # 判断卖出日是否不可用。
        return ""  # 不可用时返回空。
    buy_text = previous_trading_day_text(sell_text)  # 用上一交易日作为 T 买日期。
    return dynamic_batch_name(buy_text) if is_dynamic_buy_date(buy_text) else ""  # 返回自动批次名或空。
def dynamic_batch_from_remark_text(remark: Any) -> str:  # 定义从委托备注中识别自动批次的函数。
    text = str(remark or "")  # 转换备注为文本。
    match = re.search(r"_(buy|sellforce|sell)_(\d{8})_", text)  # 匹配 virtual_qmt_buy/sell/sellforce 日期。
    if not match:  # 判断是否没有自动备注格式。
        return ""  # 没有匹配时返回空。
    action, day_text = match.group(1), match.group(2)  # 读取动作和日期。
    if action == "buy":  # 买入备注里的日期就是买入日。
        return dynamic_batch_from_buy_date(day_text)  # 返回买入日批次。
    return dynamic_batch_from_sell_date(day_text)  # 卖出备注里的日期用上一交易日反推批次。
def valid_sell_time(plan: Dict[str, Any]) -> bool:  # 定义判断卖出时间是否属于当前计划的函数。
    buy_time = safe_int(plan.get("buy_trade_time"))  # 读取当前计划买入成交时间戳。
    sell_time = safe_int(plan.get("sell_trade_time"))  # 读取当前计划卖出成交时间戳。
    return sell_time > 0 and (buy_time <= 0 or sell_time > buy_time)  # 卖出时间必须存在且晚于买入时间才有效。
def public_attrs(value: Any) -> Dict[str, Any]:  # 定义对象转普通字典的函数。
    return util.public_attrs(value) if value is not None else {}  # 使用项目公共转换工具并兼容空对象。
def status_name(status: Any) -> str:  # 定义委托状态码转中文的函数。
    mapping = {48: "未报", 49: "待报", 50: "已报", 51: "已报待撤", 52: "部成已撤", 53: "部撤", 54: "已撤", 55: "部成", 56: "已成", 57: "废单"}  # 定义常见 miniQMT 委托状态映射。
    return mapping.get(safe_int(status), str(status))  # 返回中文状态，未知状态保留原值。
def side_name(order_type: Any) -> str:  # 定义委托方向转中文的函数。
    value = safe_int(order_type)  # 转换委托方向常量。
    if value == xtconstant.STOCK_BUY:  # 判断是否股票买入。
        return "买入"  # 返回买入文字。
    if value == xtconstant.STOCK_SELL:  # 判断是否股票卖出。
        return "卖出"  # 返回卖出文字。
    return str(value)  # 未知方向返回数字字符串。
def batch_name(code: str) -> str:  # 定义按股票代码识别批次的函数。
    if code in FIRST_BATCH_CODES:  # 判断是否第一批测试单。
        return "第一批测试单"  # 返回第一批名称。
    if code in SECOND_BATCH_CODES:  # 判断是否第二批真实预测单。
        return "第二批真实预测单"  # 返回第二批名称。
    if code in THIRD_BATCH_CODES:  # 判断是否第三批真实预测单。
        return "第三批真实预测单"  # 返回第三批名称。
    return "其他"  # 其余股票归入其他。
def batch_from_plan(code: str, plan: Dict[str, Any]) -> str:  # 定义按状态计划识别批次的函数。
    buy_order_id = safe_int(plan.get("buy_order_id"))  # 读取计划里的买入委托号。
    buy_date = compact_date(plan.get("buy_date"))  # 读取计划里的买入日期。
    sell_date = compact_date(plan.get("sell_date"))  # 读取计划里的卖出日期。
    dynamic_batch = dynamic_batch_from_buy_date(buy_date) or dynamic_batch_from_sell_date(sell_date)  # 优先按 T 买 T+1 卖规则动态识别新批次。
    if dynamic_batch:  # 判断是否命中自动批次。
        return dynamic_batch  # 返回自动批次名称。
    if code in FIFTH_BATCH_CODES and (buy_date == FIFTH_BUY_DATE or sell_date == FIFTH_SELL_DATE):  # 判断是否第五批昨日买入今日卖出计划。
        return FIFTH_BATCH_NAME  # 返回第五批名称。
    if code in FOURTH_BATCH_CODES and (buy_date == FOURTH_BUY_DATE or sell_date == FOURTH_SELL_DATE):  # 判断是否第四批昨日买入今日卖出计划。
        return FOURTH_BATCH_NAME  # 返回第四批名称。
    if FIRST_ORDER_MIN <= buy_order_id <= FIRST_ORDER_MAX or (code in FIRST_BATCH_CODES and buy_date == "20260624"):  # 判断是否第一批测试单计划。
        return "第一批测试单"  # 返回第一批名称。
    if buy_order_id in SECOND_BUY_ORDER_IDS or (code in SECOND_BATCH_CODES and buy_date == "20260624"):  # 判断是否第二批真实预测单计划。
        return "第二批真实预测单"  # 返回第二批名称。
    if THIRD_ORDER_MIN <= buy_order_id <= THIRD_ORDER_MAX or (code in THIRD_BATCH_CODES and buy_date == "20260625"):  # 判断是否第三批真实预测单计划。
        return "第三批真实预测单"  # 返回第三批名称。
    return batch_name(code)  # 委托号和日期缺失时回退到股票代码识别。
def batch_from_remark(code: str, remark: Any) -> str:  # 定义按委托备注识别批次的函数。
    text = str(remark or "")  # 转换备注为文本。
    if not text:  # 判断备注是否为空。
        return ""  # 备注为空时返回空批次。
    if qmt_broker.is_cleanup_remark(text):  # 判断是否为人工清理旧持仓委托。
        return ""  # 清理单不归入任何策略批次。
    dynamic_batch = dynamic_batch_from_remark_text(text)  # 优先用备注里的日期自动识别 T 买 T+1 卖批次。
    if dynamic_batch:  # 判断是否命中自动批次。
        return dynamic_batch  # 返回自动批次名称。
    if "_buy_20260707_" in text or "_sell_20260708_" in text:  # 判断是否第五批昨日买入今日卖出备注。
        return FIFTH_BATCH_NAME if code in FIFTH_BATCH_CODES else ""  # 第五批代码返回第五批名称。
    if "_buy_20260706_" in text or "_sell_20260707_" in text:  # 判断是否第四批昨日买入今日卖出备注。
        return FOURTH_BATCH_NAME if code in FOURTH_BATCH_CODES else ""  # 第四批代码返回第四批名称。
    if "_buy_20260625_" in text or "_sell_20260626_" in text or "_repair_duplicate_sell_20260626_" in text:  # 判断是否第三批买入、次日卖出或重复票补卖备注。
        return "第三批真实预测单" if code in THIRD_BATCH_CODES else ""  # 第三批代码返回第三批名称。
    if "_buy_20260624_" in text or "_sell_20260625_" in text or "_repair_sell_20260625_" in text:  # 判断是否前两批买入、次日卖出或旧补丁卖出备注。
        return batch_name(code)  # 前两批按股票代码所属批次兜底识别。
    return ""  # 未识别备注返回空批次。
def batch_from_order(row: Dict[str, Any]) -> str:  # 定义按委托记录识别批次的函数。
    code = str(row.get("stock_code") or "")  # 读取委托股票代码。
    order_id = safe_int(row.get("order_id"))  # 读取委托号。
    remark_batch = batch_from_remark(code, row.get("order_remark"))  # 优先用 miniQMT 委托备注识别批次。
    if remark_batch:  # 判断备注是否已经识别出批次。
        return remark_batch  # 返回备注识别出的批次。
    if FIRST_ORDER_MIN <= order_id <= FIRST_ORDER_MAX:  # 优先判断是否第一批买入委托号。
        return "第一批测试单"  # 返回第一批名称。
    if order_id in SECOND_BUY_ORDER_IDS:  # 优先判断是否第二批买入委托号。
        return "第二批真实预测单"  # 返回第二批名称。
    if THIRD_ORDER_MIN <= order_id <= THIRD_ORDER_MAX:  # 优先判断是否第三批买入委托号。
        return "第三批真实预测单"  # 返回第三批名称。
    if code in FIRST_BATCH_CODES:  # 判断是否第一批相关股票代码。
        return "第一批测试单"  # 返回第一批名称。
    if code in SECOND_BATCH_CODES:  # 判断是否第二批相关股票代码。
        return "第二批真实预测单"  # 返回第二批名称。
    if code in THIRD_BATCH_CODES:  # 判断是否第三批相关股票代码。
        return "第三批真实预测单"  # 返回第三批名称。
    return "其他"  # 其余委托归入其他。
def batch_from_csv(row: Dict[str, Any]) -> str:  # 定义按本地 CSV 流水识别批次的函数。
    code = str(row.get("code") or "").strip()  # 读取 CSV 股票代码。
    order_id = safe_int(row.get("order_id"))  # 读取 CSV 委托号。
    remark_batch = batch_from_remark(code, row.get("remark"))  # 优先用本地流水备注识别批次。
    if remark_batch:  # 判断备注是否已经识别出批次。
        return remark_batch  # 返回备注识别出的批次。
    date_text = compact_date(row.get("date"))  # 读取流水日期。
    side = str(row.get("side") or "").strip().lower()  # 读取流水方向。
    if is_dynamic_buy_date(date_text) and side == "buy":  # 判断是否自动批次买入日流水。
        return dynamic_batch_name(date_text)  # 返回该买入日的自动批次。
    if date_text >= next_trading_day_text(DYNAMIC_BATCH_START) and side == "sell":  # 判断是否自动批次卖出日流水。
        return dynamic_batch_from_sell_date(date_text)  # 返回卖出日前一交易日对应批次。
    if FIRST_ORDER_MIN <= order_id <= FIRST_ORDER_MAX:  # 优先判断是否第一批买入委托号。
        return "第一批测试单"  # 返回第一批名称。
    if order_id in SECOND_BUY_ORDER_IDS:  # 优先判断是否第二批买入委托号。
        return "第二批真实预测单"  # 返回第二批名称。
    if THIRD_ORDER_MIN <= order_id <= THIRD_ORDER_MAX:  # 优先判断是否第三批买入委托号。
        return "第三批真实预测单"  # 返回第三批名称。
    if compact_date(row.get("date")) in {FIFTH_BUY_DATE, FIFTH_SELL_DATE} and code in FIFTH_BATCH_CODES:  # 判断是否第五批本地流水。
        return FIFTH_BATCH_NAME  # 返回第五批名称。
    if compact_date(row.get("date")) == FOURTH_BUY_DATE and code in FOURTH_BATCH_CODES:  # 判断是否第四批本地流水。
        return FOURTH_BATCH_NAME  # 返回第四批名称。
    if code in FIRST_BATCH_CODES:  # 判断是否第一批股票代码。
        return "第一批测试单"  # 返回第一批名称。
    if code in SECOND_BATCH_CODES:  # 判断是否第二批股票代码。
        return "第二批真实预测单"  # 返回第二批名称。
    if code in THIRD_BATCH_CODES:  # 判断是否第三批股票代码。
        return "第三批真实预测单"  # 返回第三批名称。
    return "其他"  # 其余 CSV 流水归入其他。
def visual_plan_key(plan: Dict[str, Any]) -> str:  # 定义可视化计划唯一键函数。
    code = str(plan.get("code") or "")  # 读取计划股票代码。
    batch = batch_from_plan(code, plan)  # 读取计划所属批次。
    buy_date = compact_date(plan.get("buy_date"))  # 读取计划买入日期。
    buy_order_id = safe_int(plan.get("buy_order_id"))  # 读取计划买入委托号。
    return f"{batch}|{buy_date}|{code}|{buy_order_id}"  # 返回批次、日期、代码和委托号组成的唯一键。
def plan_status_rank(status: Any) -> int:  # 定义可视化计划状态优先级，避免旧摘要覆盖今天最新运行态。
    mapping = {"not_bought": 1, "buy_submitted": 2, "buy_partial": 3, "open": 4, "sell_failed": 5, "sell_submitted": 6, "sell_partial": 7, "closed": 8}  # 设置状态从低到高的覆盖顺序。
    return mapping.get(str(status or ""), 0)  # 未知状态按最低优先级处理。
def merge_plan(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:  # 定义合并计划字段的函数。
    for key, value in source.items():  # 遍历来源计划字段。
        if key == "status" and key in target and plan_status_rank(value) < plan_status_rank(target.get(key)):  # 判断来源状态是否比已有状态更旧。
            continue  # 保留更靠后的运行态，避免 summary.json 旧快照回退状态。
        if value not in ("", None, 0, 0.0) or key not in target:  # 判断来源字段是否有效或目标缺少字段。
            target[key] = value  # 写入或覆盖目标计划字段。
    return target  # 返回合并后的目标计划。
def store_visual_plan(plans: Dict[str, Dict[str, Any]], plan: Dict[str, Any]) -> None:  # 定义保存可视化计划的函数。
    if not isinstance(plan, dict):  # 判断计划是否不是字典。
        return  # 非字典计划直接跳过。
    code = str(plan.get("code") or "")  # 读取股票代码。
    if not code:  # 判断股票代码是否缺失。
        return  # 缺少股票代码时跳过。
    key = visual_plan_key(plan)  # 生成可视化计划唯一键。
    if key in plans:  # 判断该计划是否已经存在。
        plans[key] = merge_plan(plans[key], dict(plan))  # 合并新旧计划字段。
    else:  # 处理首次出现的计划。
        plans[key] = dict(plan)  # 保存计划副本。
def find_plan_for_code_batch(plans: Dict[str, Any], code: str, batch: str) -> Dict[str, Any]:  # 定义按代码和批次查找计划的函数。
    matches = [plan for plan in plans.values() if isinstance(plan, dict) and str(plan.get("code") or "") == code and batch_from_plan(code, plan) == batch]  # 筛选同代码同批次计划。
    if not matches:  # 判断是否没有匹配计划。
        return {}  # 没有匹配时返回空计划。
    return sorted(matches, key=lambda plan: safe_int(plan.get("buy_order_id")), reverse=True)[0]  # 返回买入委托号最大的匹配计划。
def trade_amount(row: Dict[str, Any]) -> float:  # 定义成交金额计算函数。
    return safe_float(row.get("traded_price")) * safe_int(row.get("traded_volume"))  # 返回成交价格乘成交数量。
def price_type_name(price_type: Any) -> str:  # 定义报价类型转中文说明的函数。
    value = safe_int(price_type)  # 转换报价类型整数。
    if value in MARKET_PRICE_TYPES:  # 判断是否市价类报价。
        return "市价类"  # 返回市价类说明。
    if value == 50:  # 判断是否固定限价报价。
        return "限价"  # 返回限价说明。
    if value == 5:  # 判断是否最新价报价。
        return "最新价"  # 返回最新价说明。
    if value <= 0:  # 判断是否缺少报价类型。
        return ""  # 缺少报价类型时返回空字符串。
    return str(value)  # 未知报价类型保留数字文本。
def board_label(board: str) -> str:  # 定义板块代码转中文名称的函数。
    return BOARD_LABELS.get(board, board or "未知")  # 返回中文板块名称，未知值保留原文。
def fallback_board_for_code(code: str) -> str:  # 定义按股票代码兜底识别板块的函数。
    return "kcb" if code.startswith("688") and code.endswith(".SH") else "csi1000"  # 688.SH 兜底为科创板，其余按中证1000处理。
def safe_rate(numerator: float, denominator: float) -> float:  # 定义安全计算收益率的函数。
    return numerator / denominator if denominator else 0.0  # 分母有效时返回比率，否则返回零。
def timeline_event(event_id: str, batch: str, code: str, lane: str, moment: Optional[datetime], side: str, volume: int, price: float, amount: float, status: str, order_id: int, price_type: Any, source: str, note: str) -> Dict[str, Any]:  # 定义标准化时间线事件的函数。
    board = fallback_board_for_code(code)  # 用股票代码兜底识别当前事件板块。
    return {"id": event_id, "batch": batch, "code": code, "board": board, "board_label": board_label(board), "lane": lane, "time": time_label(moment), "sort": time_key(moment), "side": side, "volume": volume, "price": price, "amount": amount, "status": status, "order_id": order_id, "price_type": price_type_name(price_type), "source": source, "note": note}  # 返回前端时间轴使用的事件结构。
def build_order_lookup(orders: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:  # 定义按委托号索引委托对象的函数。
    return {safe_int(row.get("order_id")): row for row in orders if safe_int(row.get("order_id")) > 0}  # 返回委托号到委托对象的映射。
def timeline_from_csv(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:  # 定义从本地流水构建时间线事件的函数。
    events: List[Dict[str, Any]] = []  # 初始化时间线事件列表。
    for index, row in enumerate(rows):  # 遍历 CSV 流水行。
        code = str(row.get("code") or "").strip()  # 读取股票代码。
        batch = batch_from_csv(row)  # 根据委托号和股票代码识别批次。
        if batch == "其他":  # 判断是否不是两批交易。
            continue  # 其他股票跳过。
        side = "买入" if str(row.get("side") or "") == "buy" else "卖出"  # 转换买卖方向文字。
        event = str(row.get("event") or "")  # 读取事件类型。
        status = str(row.get("status") or "")  # 读取流水状态。
        moment = parse_time_text(row.get("date"), row.get("time"))  # 解析流水发生时间。
        lane = "买入委托" if side == "买入" else "卖出委托"  # 按买卖方向设置默认泳道。
        if event == "cancel" or status.startswith("already") or status.endswith("failed") or "reject" in event:  # 判断是否撤单或拒单事件。
            lane = "撤单/拒单"  # 设置撤单拒单泳道。
        amount = safe_float(row.get("price")) * safe_int(row.get("order_volume"))  # 计算委托金额估算。
        note = str(row.get("message") or row.get("remark") or "")  # 读取事件说明文字。
        events.append(timeline_event(f"csv-{index}", batch, code, lane, moment, side, safe_int(row.get("order_volume")), safe_float(row.get("price")), amount, status, safe_int(row.get("order_id")), row.get("price_type"), "orders.csv", note))  # 追加 CSV 时间线事件。
    return events  # 返回 CSV 事件列表。
def timeline_from_orders(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:  # 定义从 miniQMT 委托对象构建时间线事件的函数。
    events: List[Dict[str, Any]] = []  # 初始化委托事件列表。
    for row in orders:  # 遍历委托对象。
        code = str(row.get("stock_code") or "")  # 读取股票代码。
        batch = batch_from_order(row)  # 识别委托所属批次。
        if batch == "其他":  # 判断是否不是两批交易。
            continue  # 其他委托跳过。
        side = side_name(row.get("order_type"))  # 转换买卖方向。
        lane = "买入委托" if side == "买入" else "卖出委托"  # 根据方向选择委托泳道。
        moment = parse_qmt_time(row.get("order_time"))  # 解析 miniQMT 委托时间。
        amount = safe_float(row.get("price")) * safe_int(row.get("order_volume"))  # 计算委托金额估算。
        note = str(row.get("order_remark") or row.get("status_msg") or "")  # 读取委托备注或状态消息。
        events.append(timeline_event(f"order-{safe_int(row.get('order_id'))}", batch, code, lane, moment, side, safe_int(row.get("order_volume")), safe_float(row.get("price")), amount, status_name(row.get("order_status")), safe_int(row.get("order_id")), row.get("price_type"), "miniQMT委托", note))  # 追加委托时间线事件。
    return events  # 返回委托事件列表。
def timeline_from_trades(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:  # 定义从 miniQMT 成交对象构建时间线事件的函数。
    events: List[Dict[str, Any]] = []  # 初始化成交事件列表。
    for index, row in enumerate(trades):  # 遍历成交对象。
        code = str(row.get("stock_code") or "")  # 读取股票代码。
        batch = str(row.get("_visual_batch") or batch_from_order(row))  # 优先使用委托映射补齐后的批次，否则按成交对象自身识别。
        if batch == "其他":  # 判断是否不是两批交易。
            continue  # 其他成交跳过。
        side = side_name(row.get("order_type"))  # 转换成交方向。
        lane = "买入成交" if side == "买入" else "卖出成交"  # 根据方向选择成交泳道。
        moment = parse_qmt_time(row.get("traded_time"))  # 解析成交时间。
        amount = trade_amount(row)  # 计算成交金额。
        note = str(row.get("order_remark") or row.get("traded_id") or "")  # 读取成交备注或成交编号。
        events.append(timeline_event(f"trade-{safe_int(row.get('order_id'))}-{index}", batch, code, lane, moment, side, safe_int(row.get("traded_volume")), safe_float(row.get("traded_price")), amount, "已成", safe_int(row.get("order_id")), "", "miniQMT成交", note))  # 追加成交时间线事件。
    return events  # 返回成交事件列表。
def timeline_from_plans(plans: Dict[str, Any], order_lookup: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:  # 定义从状态计划补齐跨日成交事件的函数。
    events: List[Dict[str, Any]] = []  # 初始化计划事件列表。
    for _key, plan in plans.items():  # 遍历状态文件里的持仓计划。
        code = str(plan.get("code") or "")  # 读取当前计划股票代码。
        batch = batch_from_plan(code, plan)  # 根据计划信息识别批次。
        if batch == "其他":  # 判断是否不是两批交易。
            continue  # 其他计划跳过。
        buy_order_id = safe_int(plan.get("buy_order_id"))  # 读取买入委托号。
        buy_volume = safe_int(plan.get("filled_shares"))  # 读取买入成交股数。
        buy_price = safe_float(plan.get("buy_price"))  # 读取买入成交均价。
        buy_moment = parse_qmt_time(plan.get("buy_trade_time"))  # 解析买入成交时间。
        if buy_volume > 0 and buy_price > 0 and buy_moment and buy_order_id not in order_lookup:  # 判断是否需要补充前一日买入成交。
            events.append(timeline_event(f"plan-buy-{code}", batch, str(code), "买入成交", buy_moment, "买入", buy_volume, buy_price, buy_price * buy_volume, "已成", buy_order_id, "", "state.json", "状态文件补齐买入成交"))  # 追加计划买入成交事件。
        sell_order_id = safe_int(plan.get("sell_order_id"))  # 读取卖出委托号。
        sell_volume = safe_int(plan.get("sell_volume")) if valid_sell_time(plan) else 0  # 读取有效卖出成交股数。
        sell_price = safe_float(plan.get("sell_price")) if valid_sell_time(plan) else 0.0  # 读取有效卖出成交均价。
        sell_moment = parse_qmt_time(plan.get("sell_trade_time"))  # 解析卖出成交时间。
        if sell_volume > 0 and sell_price > 0 and sell_moment and sell_order_id not in order_lookup:  # 判断是否需要补充缺失卖出成交。
            events.append(timeline_event(f"plan-sell-{code}", batch, str(code), "卖出成交", sell_moment, "卖出", sell_volume, sell_price, sell_price * sell_volume, "已成", sell_order_id, "", "state.json", "状态文件补齐卖出成交"))  # 追加计划卖出成交事件。
    return events  # 返回计划补齐事件列表。
def dedupe_timeline(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:  # 定义时间线事件去重函数。
    chosen: Dict[str, Dict[str, Any]] = {}  # 初始化去重后的事件字典。
    for event in events:  # 遍历所有候选事件。
        key = "|".join([str(event.get("batch", "")), str(event.get("code", "")), str(event.get("lane", "")), str(event.get("time", "")), str(event.get("order_id", "")), str(event.get("volume", "")), str(round(safe_float(event.get("price")), 4))])  # 构造去重键。
        current = chosen.get(key)  # 读取已有事件。
        if current is None or event.get("source") == "miniQMT成交" or current.get("source") == "orders.csv":  # 优先保留成交和账户对象来源。
            chosen[key] = event  # 保存当前更可信事件。
    return sorted(chosen.values(), key=lambda item: (str(item.get("sort")), TIMELINE_LANES.index(item.get("lane")) if item.get("lane") in TIMELINE_LANES else 99, str(item.get("code"))))  # 按时间、泳道和代码排序。
def enrich_timeline_events(events: List[Dict[str, Any]], plans: Dict[str, Any], board_map: Dict[str, str]) -> List[Dict[str, Any]]:  # 定义增强时间线事件指标的函数。
    for event in events:  # 遍历时间线事件。
        code = str(event.get("code") or "")  # 读取事件股票代码。
        batch = str(event.get("batch") or "")  # 读取事件所属批次。
        plan = find_plan_for_code_batch(plans, str(code), batch) if isinstance(plans, dict) else {}  # 按代码和批次读取当前股票计划。
        board = board_map.get(code, fallback_board_for_code(code))  # 读取当前股票所属板块。
        buy_price = safe_float(plan.get("buy_price"))  # 读取计划买入均价。
        sell_price = safe_float(plan.get("sell_price"))  # 读取计划卖出均价。
        target_price = safe_float(plan.get("target_price"))  # 读取计划止盈目标价。
        filled_shares = safe_int(plan.get("filled_shares"))  # 读取累计买入成交股数。
        sell_volume = safe_int(plan.get("sell_volume"))  # 读取累计卖出成交股数。
        buy_cost = buy_price * filled_shares  # 计算当前股票买入成本。
        sell_amount = sell_price * sell_volume  # 计算当前股票卖出金额。
        total_result = sell_amount - buy_cost if sell_volume > 0 else 0.0  # 计算当前股票已卖部分结果。
        chart_price = safe_float(event.get("price"))  # 读取事件原始价格。
        if chart_price <= 0 and "买入" in str(event.get("lane")):  # 判断买入事件是否缺少绘图价格。
            chart_price = buy_price  # 使用计划买入均价作为绘图价格。
        if chart_price <= 0 and str(event.get("lane")) == "卖出委托":  # 判断卖出委托是否缺少绘图价格。
            chart_price = (sell_price or target_price) if str(event.get("price_type")) == "市价类" else (target_price or sell_price)  # 市价类卖出委托优先使用真实卖出均价，限价卖出委托优先使用止盈价。
        if chart_price <= 0 and "卖出" in str(event.get("lane")):  # 判断卖出事件是否仍缺少绘图价格。
            chart_price = sell_price or target_price  # 使用卖出均价或止盈目标价作为绘图价格。
        event["board"] = board  # 更新事件板块代码。
        event["board_label"] = board_label(board)  # 更新事件板块中文名。
        event["chart_price"] = chart_price  # 写入前端绘图价格。
        event["return_rate"] = safe_rate(total_result, buy_cost)  # 写入单票已实现收益率。
        if safe_float(event.get("amount")) <= 0 and chart_price > 0 and safe_int(event.get("volume")) > 0:  # 判断事件金额是否可用。
            event["amount"] = chart_price * safe_int(event.get("volume"))  # 用绘图价格和数量估算事件金额。
    return events  # 返回增强后的时间线事件列表。
def build_timeline(plans: Dict[str, Any], orders: List[Dict[str, Any]], trades: List[Dict[str, Any]], board_map: Dict[str, str]) -> Dict[str, Any]:  # 定义构建前端时间线数据的函数。
    csv_events = read_order_events_csv()  # 读取本地委托流水。
    order_lookup = build_order_lookup(orders)  # 构建当前委托号索引。
    events = []  # 初始化全部时间线事件。
    events.extend(timeline_from_csv(csv_events))  # 添加历史委托流水事件。
    events.extend(timeline_from_orders(orders))  # 添加 miniQMT 委托事件。
    events.extend(timeline_from_trades(trades))  # 添加 miniQMT 成交事件。
    events.extend(timeline_from_plans(plans, order_lookup))  # 添加状态文件补齐事件。
    events = dedupe_timeline(events)  # 去重并排序事件。
    events = enrich_timeline_events(events, plans, board_map)  # 补充板块、绘图价格和收益率。
    times = [event["time"] for event in events if event.get("time")]  # 提取有效时间文本。
    return {"lanes": TIMELINE_LANES, "events": events, "start": min(times) if times else "", "end": max(times) if times else ""}  # 返回完整时间线结构。
def read_state() -> Dict[str, Any]:  # 定义读取本地状态文件的函数。
    state_path = PROJECT_ROOT / "virtual_qmt_data" / "state.json"  # 拼接状态文件路径。
    if not state_path.exists():  # 判断状态文件是否不存在。
        return {"position_plans": {}}  # 状态文件不存在时返回默认结构。
    return json.loads(state_path.read_text(encoding="utf-8-sig"))  # 读取并解析状态文件。
def read_summary_snapshot() -> Dict[str, Any]:  # 定义读取最近账户摘要文件的函数。
    summary_path = PROJECT_ROOT / "virtual_qmt_data" / "summary.json"  # 拼接账户摘要文件路径。
    if not summary_path.exists():  # 判断摘要文件是否不存在。
        return {"asset": {}, "positions": [], "orders": [], "trades": []}  # 返回空账户快照结构。
    return json.loads(summary_path.read_text(encoding="utf-8-sig"))  # 读取并解析最近账户摘要。


def build_multi_strategy_snapshot(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    root = multi_strategy.ensure_state(state)
    plans = list(multi_strategy.iter_plans(root))
    codes = sorted({str(plan.get("code") or "") for _key, plan in plans if str(plan.get("code") or "")})
    try:
        prices = qmt_broker.get_last_prices(codes)
    except Exception:
        prices = {}
    rows: List[Dict[str, Any]] = []
    for strategy_id, config in multi_strategy.STRATEGY_CONFIGS.items():
        account = root["accounts"][strategy_id]
        strategy_plans = [plan for _key, plan in plans if str(plan.get("strategy_id") or "") == strategy_id]
        market_value = 0.0
        open_count = 0
        pending_count = 0
        for plan in strategy_plans:
            remaining = max(0, safe_int(plan.get("filled_shares")) - safe_int(plan.get("sold_shares")))
            if remaining > 0:
                open_count += 1
                market_value += remaining * safe_float(prices.get(str(plan.get("code") or "")))
            if plan.get("buy_order_active") or plan.get("sell_order_active"):
                pending_count += 1
        cash = safe_float(account.get("cash"))
        initial_cash = safe_float(account.get("initial_cash"))
        equity = cash + market_value
        rows.append({
            "strategy_id": strategy_id,
            "name": config["name"],
            "initial_cash": initial_cash,
            "cash": cash,
            "available_cash": multi_strategy.available_cash(root, strategy_id),
            "market_value": market_value,
            "equity": equity,
            "total_pnl": equity - initial_cash,
            "return_rate": safe_rate(equity - initial_cash, initial_cash),
            "realized_pnl": safe_float(account.get("realized_pnl")),
            "open_count": open_count,
            "pending_count": pending_count,
        })
    return rows
def read_order_events_csv() -> List[Dict[str, Any]]:  # 定义读取本地委托事件流水的函数。
    orders_path = PROJECT_ROOT / "virtual_qmt_data" / "orders.csv"  # 拼接委托流水 CSV 路径。
    if not orders_path.exists():  # 判断委托流水是否不存在。
        return []  # 没有流水时返回空列表。
    with orders_path.open("r", encoding="utf-8-sig", newline="") as handle:  # 用 UTF-8-SIG 打开 CSV 文件。
        return list(csv.DictReader(handle))  # 读取 CSV 为字典列表。
def build_visual_plans(state: Dict[str, Any]) -> Dict[str, Any]:  # 定义构建可视化专用计划字典的函数。
    source_plans = state.get("position_plans", {}) if isinstance(state, dict) else {}  # 读取状态文件里的原始计划。
    plans: Dict[str, Dict[str, Any]] = {}  # 初始化允许同代码多批次共存的计划字典。
    for code, plan in source_plans.items():  # 遍历状态文件里的原始计划。
        if isinstance(plan, dict):  # 判断当前计划是否为字典。
            copy_plan = dict(plan)  # 复制当前计划。
            copy_plan.setdefault("code", str(code))  # 补齐股票代码字段。
            store_visual_plan(plans, copy_plan)  # 保存当前计划到可视化计划集合。
    summary_plans = read_summary_snapshot().get("plans", {})  # 读取摘要文件里保留的计划快照。
    for code, plan in (summary_plans.items() if isinstance(summary_plans, dict) else []):  # 遍历摘要计划快照。
        if isinstance(plan, dict):  # 判断摘要计划是否为字典。
            copy_plan = dict(plan)  # 复制摘要计划。
            copy_plan.setdefault("code", str(code))  # 补齐股票代码字段。
            store_visual_plan(plans, copy_plan)  # 保存摘要计划到可视化计划集合。
    predictions = state.get("predictions", {}) if isinstance(state, dict) else {}  # 读取预测批次记录。
    third_codes = predictions.get("20260625:20260625", []) if isinstance(predictions, dict) else []  # 读取第三批预测股票代码。
    csv_rows = read_order_events_csv()  # 读取本地委托流水。
    for row in csv_rows:  # 遍历本地委托流水。
        code = str(row.get("code") or "").strip()  # 读取流水股票代码。
        order_id = safe_int(row.get("order_id"))  # 读取流水委托号。
        if not (THIRD_ORDER_MIN <= order_id <= THIRD_ORDER_MAX):  # 判断是否不是第三批买入委托。
            continue  # 非第三批买入委托跳过。
        plan = find_plan_for_code_batch(plans, code, "第三批真实预测单") or {"code": code}  # 获取或创建第三批计划。
        plan["code"] = code  # 写入股票代码。
        plan["buy_date"] = compact_date(row.get("date"))  # 写入买入日期。
        plan["sell_date"] = next_calendar_day(compact_date(row.get("date")))  # 写入策略明日卖出日期字段。
        plan["buy_order_id"] = order_id  # 写入第三批买入委托号。
        plan["buy_price"] = safe_float(plan.get("buy_price")) or safe_float(row.get("price"))  # 优先保留状态文件里的真实成交均价，缺失时才使用流水委托价兜底。
        plan["shares"] = safe_int(row.get("order_volume")) or safe_int(plan.get("shares"))  # 写入委托买入股数。
        plan["target_price"] = safe_float(plan.get("target_price")) or round((safe_float(plan.get("buy_price")) or safe_float(row.get("price"))) * 1.09, 4)  # 缺少止盈价时按买入价九个百分点估算。
        plan["status"] = "open" if not valid_sell_time(plan) else str(plan.get("status") or "closed")  # 第三批未到有效卖出时间时按持仓中处理。
        plan["buy_remark"] = str(row.get("remark") or plan.get("buy_remark") or "")  # 写入买入备注。
        store_visual_plan(plans, plan)  # 保存第三批修正后的计划。
    for code in third_codes:  # 遍历第三批预测代码。
        text_code = str(code)  # 转换股票代码文本。
        if not find_plan_for_code_batch(plans, text_code, "第三批真实预测单"):  # 判断第三批预测代码是否还没有计划。
            store_visual_plan(plans, {"code": text_code, "buy_date": "20260625", "sell_date": "20260626", "status": "buy_submitted"})  # 追加未成交或缺失计划的第三批占位。
    dynamic_codes: Dict[str, List[str]] = {}  # 初始化自动批次预测代码字典，键为买入日。
    for key, codes in (predictions.items() if isinstance(predictions, dict) else []):  # 遍历所有预测缓存批次。
        buy_date = compact_date(str(key).split(":", 1)[0])  # 预测缓存键第一段就是本次买入交易日。
        if not is_dynamic_buy_date(buy_date):  # 判断是否不属于自动批次范围。
            continue  # 老批次继续交给历史逻辑。
        dynamic_codes.setdefault(buy_date, [])  # 创建当前买入日列表。
        for code in codes if isinstance(codes, list) else []:  # 遍历预测股票代码。
            text_code = str(code)  # 转换股票代码文本。
            if text_code and text_code not in dynamic_codes[buy_date]:  # 避免重复追加。
                dynamic_codes[buy_date].append(text_code)  # 保存当前买入日预测代码。
    dynamic_status: Dict[str, str] = {}  # 初始化自动批次本地流水状态字典。
    for row in csv_rows:  # 遍历本地委托流水，补齐自动批次未成交和跳过状态。
        code = str(row.get("code") or "").strip()  # 读取流水股票代码。
        batch = batch_from_csv(row)  # 读取流水所属批次。
        buy_date = dynamic_batch_buy_date(batch)  # 从自动批次名提取买入日。
        if not code or not buy_date:  # 判断是否不是自动批次流水。
            continue  # 非自动批次跳过。
        key = f"{buy_date}|{code}"  # 构造买入日和股票代码组合键。
        status = str(row.get("status") or "").strip()  # 读取流水状态。
        event = str(row.get("event") or "").strip()  # 读取流水事件。
        if event == "order" and key not in dynamic_status:  # 判断是否存在买入委托。
            dynamic_status[key] = "buy_submitted"  # 记录买入已提交状态。
    for buy_date, codes in sorted(dynamic_codes.items()):  # 遍历自动批次预测代码。
        batch = dynamic_batch_name(buy_date)  # 生成当前自动批次名。
        for code in codes:  # 遍历当前买入日股票。
            text_code = str(code)  # 转换股票代码文本。
            if find_plan_for_code_batch(plans, text_code, batch):  # 判断该自动批次计划是否已存在。
                continue  # 已有计划时跳过。
            status = dynamic_status.get(f"{buy_date}|{text_code}", "not_bought")  # 读取流水状态，缺失按未买入占位。
            store_visual_plan(plans, {"code": text_code, "buy_date": buy_date, "sell_date": next_trading_day_text(buy_date), "status": status})  # 追加自动批次占位计划。
    return plans  # 返回可视化专用计划字典。
def account_snapshot_from_summary() -> Dict[str, Any]:  # 定义从本地摘要转换账户快照的函数。
    summary = read_summary_snapshot()  # 读取最近保存的账户摘要。
    return {"asset": summary.get("asset", {}) or {}, "positions": summary.get("positions", []) or [], "orders": summary.get("orders", []) or [], "trades": summary.get("trades", []) or []}  # 返回和实时查询一致的快照结构。
def read_predict_json_files() -> List[Dict[str, Any]]:  # 定义读取预测响应 JSON 文件列表的函数。
    data_dir = PROJECT_ROOT / "virtual_qmt_data"  # 拼接虚拟交易数据目录。
    paths = list(data_dir.glob("predict_sync_*_latest.json"))  # 读取最新预测响应文件路径列表。
    paths.extend(sorted(data_dir.glob("predict_api/*/*_predict_sync_response.json")))  # 补充历史预测接口响应文件路径列表。
    payloads: List[Dict[str, Any]] = []  # 初始化预测响应内容列表。
    for path in paths:  # 遍历候选预测响应文件。
        try:  # 尝试读取当前 JSON 文件。
            payloads.append(json.loads(path.read_text(encoding="utf-8-sig")))  # 解析并追加 JSON 内容。
        except Exception:  # 捕获损坏或非 JSON 文件。
            continue  # 当前文件失败时跳过。
    return payloads  # 返回预测响应内容列表。
def extract_prediction_payload(payload: Dict[str, Any]) -> Dict[str, Any]:  # 定义提取预测有效载荷的函数。
    data = payload.get("data") if isinstance(payload, dict) else None  # 读取接口包装层里的 data 字段。
    return data if isinstance(data, dict) else payload  # 有 data 包装时返回 data，否则返回原始 payload。
def build_board_map() -> Dict[str, str]:  # 定义构建股票代码到板块的映射函数。
    mapping: Dict[str, str] = {}  # 初始化板块映射字典。
    for payload in read_predict_json_files():  # 遍历本地保存的预测响应。
        data = extract_prediction_payload(payload)  # 提取真实预测响应载荷。
        for item in strategy.parse_predict_items(data):  # 遍历预测响应解析出的股票项。
            code = str(item.get("stock_code") or "")  # 读取股票代码。
            if code and code not in mapping:  # 判断代码有效且尚未映射。
                mapping[code] = strategy.normalize_predict_board(item)  # 使用策略同款规则识别板块。
    for code in FIRST_BATCH_CODES | SECOND_BATCH_CODES | THIRD_BATCH_CODES:  # 遍历三批交易股票代码。
        mapping.setdefault(code, fallback_board_for_code(code))  # 对预测文件缺失的代码使用代码规则兜底。
    return mapping  # 返回股票代码到板块的映射。
def query_process_ids() -> List[int]:  # 定义查询交易进程编号的函数。
    command = "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*virtual_qmt_trader.py*' } | Select-Object -ExpandProperty ProcessId"  # 定义 PowerShell 查询命令。
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, encoding="utf-8", errors="ignore")  # 执行进程查询命令。
    return [safe_int(item) for item in result.stdout.split() if safe_int(item) > 0]  # 解析并返回有效进程编号列表。
def current_account_snapshot() -> Dict[str, Any]:  # 定义查询当前账户快照的函数。
    util.connect_market_data()  # 连接 miniQMT 行情服务。
    trader = qmt_broker.make_trader(util.DEFAULT_USERDATA_PATH)  # 连接虚拟 miniQMT 交易服务。
    try:  # 使用保护块确保最终断开交易连接。
        account = qmt_broker.make_account(util.DEFAULT_ACCOUNT_ID, util.DEFAULT_ACCOUNT_TYPE)  # 构造股票账户对象。
        qmt_broker.subscribe_account(trader, account)  # 订阅账户推送。
        asset = public_attrs(trader.query_stock_asset(account))  # 查询账户资产并转换为字典。
        positions = [public_attrs(item) for item in (trader.query_stock_positions(account) or [])]  # 查询当前持仓并转换为字典列表。
        orders = [row for row in [public_attrs(item) for item in (trader.query_stock_orders(account, False) or [])] if not qmt_broker.is_cleanup_order_row(row)]  # 查询今日全部委托并过滤人工清理旧持仓委托。
        trades = [row for row in [public_attrs(item) for item in (trader.query_stock_trades(account) or [])] if not qmt_broker.is_cleanup_order_row(row)]  # 查询今日全部成交并过滤人工清理旧持仓成交。
        return {"asset": asset, "positions": positions, "orders": orders, "trades": trades}  # 返回账户快照字典。
    finally:  # 执行交易连接清理。
        trader.stop()  # 停止 miniQMT 交易连接。
def load_account_snapshot() -> Dict[str, Any]:  # 定义加载账户快照的函数。
    try:  # 优先尝试读取 miniQMT 实时账户。
        return current_account_snapshot()  # 返回实时账户快照。
    except Exception as exc:  # 捕获 miniQMT 连接或查询失败。
        print(f"读取 miniQMT 实时账户失败，改用 summary.json：{exc}")  # 输出兜底原因，方便命令行排查。
        return account_snapshot_from_summary()  # 返回最近保存的本地账户摘要。
def enrich_trades_with_order_batches(trades: List[Dict[str, Any]], orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:  # 定义用当日委托批次补齐成交批次的函数。
    order_batches = {safe_int(order.get("order_id")): batch_from_order(order) for order in orders if safe_int(order.get("order_id")) > 0}  # 构建委托号到可视化批次的映射。
    enriched: List[Dict[str, Any]] = []  # 初始化补齐后的成交列表。
    for trade in trades:  # 遍历原始成交记录。
        copy_trade = dict(trade)  # 复制成交记录，避免修改账户快照原始对象。
        copy_trade["_visual_batch"] = order_batches.get(safe_int(trade.get("order_id")), batch_from_order(trade))  # 写入成交所属可视化批次。
        enriched.append(copy_trade)  # 追加补齐后的成交记录。
    return enriched  # 返回带可视化批次的成交列表。
def apply_visual_sell_fills(plans: Dict[str, Any], trades: List[Dict[str, Any]]) -> None:  # 定义把当日成交修正回可视化计划的函数。
    grouped: Dict[str, Dict[str, Any]] = {}  # 初始化按批次和代码聚合的卖出成交字典。
    for trade in trades:  # 遍历当日成交记录。
        if safe_int(trade.get("order_type")) != xtconstant.STOCK_SELL:  # 判断是否不是卖出成交。
            continue  # 非卖出成交跳过。
        batch = str(trade.get("_visual_batch") or batch_from_order(trade))  # 读取成交所属可视化批次。
        code = str(trade.get("stock_code") or "")  # 读取成交股票代码。
        if batch == "其他" or not code:  # 判断批次或代码是否不可用。
            continue  # 无效成交跳过。
        volume = safe_int(trade.get("traded_volume"))  # 读取成交股数。
        price = safe_float(trade.get("traded_price"))  # 读取成交价格。
        if volume <= 0 or price <= 0:  # 判断成交数量和价格是否有效。
            continue  # 无效成交跳过。
        key = f"{batch}|{code}"  # 构造批次加代码聚合键。
        fill = grouped.setdefault(key, {"batch": batch, "code": code, "amount": 0.0, "volume": 0, "last_time": 0, "order_id": 0})  # 获取或创建当前卖出聚合。
        fill["amount"] += price * volume  # 累加成交金额。
        fill["volume"] += volume  # 累加成交数量。
        fill["last_time"] = max(safe_int(fill.get("last_time")), safe_int(trade.get("traded_time")))  # 记录最新成交时间。
        fill["order_id"] = max(safe_int(fill.get("order_id")), safe_int(trade.get("order_id")))  # 记录最新卖出委托号。
    for fill in grouped.values():  # 遍历聚合后的卖出成交。
        plan = find_plan_for_code_batch(plans, str(fill.get("code") or ""), str(fill.get("batch") or ""))  # 查找对应批次和股票的可视化计划。
        if not plan:  # 判断是否没有对应计划。
            continue  # 没有计划时跳过。
        buy_time = safe_int(plan.get("buy_trade_time"))  # 读取计划买入成交时间。
        if buy_time > 0 and safe_int(fill.get("last_time")) <= buy_time:  # 判断卖出成交是否早于或等于当前计划买入。
            continue  # 旧批次成交不能覆盖当前批次计划。
        volume = safe_int(fill.get("volume"))  # 读取聚合卖出股数。
        avg_price = safe_float(fill.get("amount")) / volume if volume > 0 else 0.0  # 计算加权卖出均价。
        plan["sell_order_id"] = safe_int(fill.get("order_id"))  # 写入当前批次真实卖出委托号。
        plan["sell_price"] = avg_price  # 写入当前批次真实卖出成交均价。
        plan["sell_volume"] = volume  # 写入当前批次真实卖出成交数量。
        plan["sell_trade_time"] = safe_int(fill.get("last_time"))  # 写入当前批次最后卖出成交时间。
        plan["status"] = "closed" if volume >= safe_int(plan.get("filled_shares") or plan.get("shares")) else "sell_partial"  # 根据成交数量更新计划状态。
def apply_visual_buy_fills(plans: Dict[str, Any], trades: List[Dict[str, Any]]) -> None:  # 定义把当日买入成交修正回可视化计划的函数。
    grouped: Dict[str, Dict[str, Any]] = {}  # 初始化按批次和代码聚合的买入成交字典。
    for trade in trades:  # 遍历当日成交记录。
        if safe_int(trade.get("order_type")) != xtconstant.STOCK_BUY:  # 判断是否不是买入成交。
            continue  # 非买入成交跳过。
        batch = str(trade.get("_visual_batch") or batch_from_order(trade))  # 读取成交所属可视化批次。
        code = str(trade.get("stock_code") or "")  # 读取成交股票代码。
        buy_date = dynamic_batch_buy_date(batch)  # 读取自动批次买入日。
        if not buy_date or not code:  # 判断是否不是自动批次买入。
            continue  # 老批次仍使用 state/summary 里的历史成交。
        volume = safe_int(trade.get("traded_volume"))  # 读取成交股数。
        price = safe_float(trade.get("traded_price"))  # 读取成交价格。
        if volume <= 0 or price <= 0:  # 判断成交数量和价格是否有效。
            continue  # 无效成交跳过。
        key = f"{batch}|{code}"  # 构造批次加代码聚合键。
        fill = grouped.setdefault(key, {"batch": batch, "code": code, "buy_date": buy_date, "amount": 0.0, "volume": 0, "last_time": 0, "order_id": 0, "remark": ""})  # 获取或创建聚合行。
        fill["amount"] += price * volume  # 累加成交金额。
        fill["volume"] += volume  # 累加成交数量。
        fill["last_time"] = max(safe_int(fill.get("last_time")), safe_int(trade.get("traded_time")))  # 记录最新成交时间。
        fill["order_id"] = max(safe_int(fill.get("order_id")), safe_int(trade.get("order_id")))  # 记录最新买入委托号。
        if trade.get("order_remark"):  # 判断成交对象是否有备注。
            fill["remark"] = str(trade.get("order_remark"))  # 保存备注。
    for fill in grouped.values():  # 遍历聚合后的买入成交。
        code = str(fill.get("code") or "")  # 读取股票代码。
        batch = str(fill.get("batch") or "")  # 读取批次。
        buy_date = str(fill.get("buy_date") or "")  # 读取买入日。
        plan = find_plan_for_code_batch(plans, code, batch) or {"code": code, "buy_date": buy_date, "sell_date": next_trading_day_text(buy_date)}  # 查找或创建自动批次计划。
        volume = safe_int(fill.get("volume"))  # 读取聚合股数。
        avg_price = safe_float(fill.get("amount")) / volume if volume > 0 else 0.0  # 计算加权买入均价。
        plan["code"] = code  # 写入股票代码。
        plan["buy_date"] = buy_date  # 写入买入日。
        plan["sell_date"] = next_trading_day_text(buy_date)  # 写入预计卖出日。
        plan["buy_order_id"] = safe_int(fill.get("order_id")) or safe_int(plan.get("buy_order_id"))  # 写入真实 QMT 买入委托号。
        plan["buy_price"] = avg_price or safe_float(plan.get("buy_price"))  # 写入真实成交均价。
        plan["shares"] = volume or safe_int(plan.get("shares"))  # 写入成交股数。
        plan["filled_shares"] = volume or safe_int(plan.get("filled_shares"))  # 写入已成交股数。
        plan["buy_trade_time"] = safe_int(fill.get("last_time")) or safe_int(plan.get("buy_trade_time"))  # 写入买入成交时间。
        plan["buy_remark"] = str(fill.get("remark") or plan.get("buy_remark") or "")  # 写入买入备注。
        if safe_float(plan.get("target_price")) <= 0 and avg_price > 0:  # 判断是否缺少目标价。
            plan["target_price"] = round(avg_price * 1.09, 4)  # 用 9% 止盈目标兜底展示。
        if str(plan.get("status") or "") in {"", "buy_submitted", "buy_partial", "not_bought"}:  # 判断状态是否仍停留在买入阶段。
            plan["status"] = "open"  # 自动批次买入成交后展示为仍持仓，等待 T+1 卖出。
        store_visual_plan(plans, plan)  # 保存回可视化计划集合。
def collect_dynamic_batch_codes(state: Dict[str, Any], plans: Dict[str, Any], orders: List[Dict[str, Any]], trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:  # 定义收集自动批次代码集合的函数。
    by_date: Dict[str, Set[str]] = {}  # 初始化买入日到股票代码集合的映射。
    predictions = state.get("predictions", {}) if isinstance(state, dict) else {}  # 读取预测缓存。
    for key, codes in (predictions.items() if isinstance(predictions, dict) else []):  # 遍历预测缓存。
        buy_date = compact_date(str(key).split(":", 1)[0])  # 读取预测买入日。
        if not is_dynamic_buy_date(buy_date):  # 判断是否不是自动批次。
            continue  # 非自动批次跳过。
        target = by_date.setdefault(buy_date, set())  # 获取当前买入日代码集合。
        for code in codes if isinstance(codes, list) else []:  # 遍历预测代码。
            if str(code):  # 判断代码是否有效。
                target.add(str(code))  # 添加预测代码。
    for plan in (plans.values() if isinstance(plans, dict) else []):  # 遍历可视化计划。
        if not isinstance(plan, dict):  # 判断计划是否不是字典。
            continue  # 非字典跳过。
        buy_date = compact_date(plan.get("buy_date"))  # 读取买入日。
        code = str(plan.get("code") or "")  # 读取股票代码。
        if is_dynamic_buy_date(buy_date) and code:  # 判断是否自动批次计划。
            by_date.setdefault(buy_date, set()).add(code)  # 添加计划代码。
    for row in orders:  # 遍历今日 QMT 委托。
        batch = batch_from_order(row)  # 读取委托批次。
        buy_date = dynamic_batch_buy_date(batch)  # 提取自动批次买入日。
        code = str(row.get("stock_code") or "")  # 读取股票代码。
        if buy_date and code:  # 判断是否自动批次委托。
            by_date.setdefault(buy_date, set()).add(code)  # 添加委托代码。
    for row in trades:  # 遍历今日 QMT 成交。
        batch = str(row.get("_visual_batch") or batch_from_order(row))  # 读取成交批次。
        buy_date = dynamic_batch_buy_date(batch)  # 提取自动批次买入日。
        code = str(row.get("stock_code") or "")  # 读取股票代码。
        if buy_date and code:  # 判断是否自动批次成交。
            by_date.setdefault(buy_date, set()).add(code)  # 添加成交代码。
    return [{"name": dynamic_batch_name(day), "buy_date": day, "codes": sorted(codes)} for day, codes in sorted(by_date.items(), reverse=True)]  # 返回按买入日倒序排列的批次定义。
def summarize_batch(name: str, codes: Iterable[str], plans: Dict[str, Any], positions: Dict[str, Dict[str, Any]], orders: List[Dict[str, Any]], trades: List[Dict[str, Any]], board_map: Dict[str, str]) -> Dict[str, Any]:  # 定义按批次汇总交易详情的函数。
    plan_items = [(str(plan.get("code") or ""), plan) for _key, plan in plans.items() if isinstance(plan, dict) and batch_from_plan(str(plan.get("code") or ""), plan) == name] if isinstance(plans, dict) else []  # 筛选当前批次对应的计划项。
    code_set = sorted(set(codes) | {code for code, _plan in plan_items})  # 合并批次静态代码和当前批次计划代码。
    plan_by_code = {code: plan for code, plan in plan_items}  # 构建当前批次内代码到计划的映射。
    rows: List[Dict[str, Any]] = []  # 初始化批次明细列表。
    board_summary: Dict[str, Dict[str, Any]] = {}  # 初始化板块维度汇总字典。
    buy_cost = 0.0  # 初始化买入成本。
    sell_amount = 0.0  # 初始化卖出金额。
    realized_pnl = 0.0  # 初始化已实现盈亏。
    remaining_market = 0.0  # 初始化剩余市值。
    remaining_cost = 0.0  # 初始化剩余成本。
    pending_codes: List[str] = []  # 初始化挂单未成代码列表。
    closed_count = 0  # 初始化已关闭计划数量。
    filled_count = 0  # 初始化有买入成交数量。
    buy_unfilled_count = 0  # 初始化买入委托未成交数量。
    open_count = 0  # 初始化仍持仓未卖数量。
    for code in sorted(code_set):  # 遍历当前批次股票代码。
        plan = plan_by_code.get(code, {})  # 读取当前批次内股票计划。
        raw_position = positions.get(code, {})  # 读取当前股票持仓。
        filled_shares = safe_int(plan.get("filled_shares"))  # 读取累计买入成交股数。
        sell_volume = safe_int(plan.get("sell_volume")) if valid_sell_time(plan) else 0  # 读取当前计划有效卖出成交股数。
        has_plan_position = filled_shares > sell_volume  # 判断当前计划是否仍应有持仓。
        position = raw_position if plan.get("status") not in {"closed"} and has_plan_position else {}  # 已关闭、未成交或跳过计划不复用同代码其他持仓。
        board = board_map.get(code, fallback_board_for_code(code))  # 读取当前股票所属板块。
        board_row = board_summary.setdefault(board, {"board": board, "label": board_label(board), "stock_count": 0, "filled_count": 0, "closed_count": 0, "buy_cost": 0.0, "sell_amount": 0.0, "remaining_market": 0.0, "realized_pnl": 0.0, "total_result": 0.0, "return_rate": 0.0})  # 获取或创建当前板块汇总行。
        board_row["stock_count"] += 1  # 累加当前板块股票数量。
        buy_price = safe_float(plan.get("buy_price"))  # 读取买入均价。
        sell_price = safe_float(plan.get("sell_price")) if valid_sell_time(plan) else 0.0  # 读取当前计划有效卖出均价。
        current_volume = safe_int(position.get("volume"))  # 读取当前持仓股数。
        current_market = safe_float(position.get("market_value"))  # 读取当前持仓市值。
        current_cost = buy_price * current_volume if buy_price > 0 else safe_float(position.get("open_price")) * current_volume  # 优先用本批买入均价估算剩余成本。
        code_buy_cost = buy_price * filled_shares  # 计算当前股票买入成本。
        code_sell_amount = sell_price * sell_volume  # 计算当前股票卖出金额。
        code_realized = (sell_price - buy_price) * sell_volume if sell_volume > 0 else 0.0  # 计算当前股票已实现盈亏。
        code_unrealized = current_market - current_cost if current_volume > 0 else 0.0  # 计算当前股票未实现盈亏。
        code_total = code_sell_amount + current_market - code_buy_cost  # 计算当前股票合计结果。
        code_return = safe_rate(code_total, code_buy_cost)  # 计算当前股票收益率。
        if filled_shares > 0:  # 判断是否有买入成交。
            filled_count += 1  # 累加已买入股票数。
            buy_cost += code_buy_cost  # 累加批次买入成本。
            board_row["filled_count"] += 1  # 累加当前板块有买入成交数量。
            board_row["buy_cost"] += code_buy_cost  # 累加当前板块买入成本。
        if sell_volume > 0:  # 判断是否有卖出成交。
            sell_amount += code_sell_amount  # 累加批次卖出金额。
            realized_pnl += code_realized  # 累加批次已实现盈亏。
            board_row["sell_amount"] += code_sell_amount  # 累加当前板块卖出金额。
            board_row["realized_pnl"] += code_realized  # 累加当前板块已实现盈亏。
        if current_volume > 0:  # 判断是否仍有持仓。
            remaining_market += current_market  # 累加批次剩余市值。
            remaining_cost += current_cost  # 累加批次剩余成本。
            board_row["remaining_market"] += current_market  # 累加当前板块剩余市值。
        if plan.get("status") == "closed" and valid_sell_time(plan):  # 判断计划是否已有效关闭。
            closed_count += 1  # 累加已关闭计划数量。
            board_row["closed_count"] += 1  # 累加当前板块已关闭计划数量。
        elif filled_shares > 0 and current_volume > 0:  # 判断是否仍有本批持仓。
            open_count += 1  # 累加仍持仓数量。
        if plan.get("status") in {"buy_submitted", "not_bought"} and filled_shares <= 0:  # 判断是否买入未成交或未买入。
            buy_unfilled_count += 1  # 累加买入未成交数量。
        if plan.get("status") == "sell_submitted":  # 判断计划是否卖单已提交未完全成交。
            pending_codes.append(code)  # 记录当前挂单股票代码。
        board_row["total_result"] += code_total  # 累加当前板块合计结果。
        rows.append({"code": code, "board": board, "board_label": board_label(board), "status": plan.get("status", "无计划"), "buy_date": plan.get("buy_date", ""), "sell_date": plan.get("sell_date", ""), "buy_price": buy_price, "filled_shares": filled_shares, "target_price": safe_float(plan.get("target_price")), "sell_price": sell_price, "sell_volume": sell_volume, "sell_order_id": plan.get("sell_order_id", ""), "current_volume": current_volume, "can_use_volume": safe_int(position.get("can_use_volume")), "market_value": current_market, "realized_pnl": code_realized, "unrealized_pnl": code_unrealized, "total_pnl": code_total, "return_rate": code_return})  # 追加当前股票交易明细。
    for board_row in board_summary.values():  # 遍历板块汇总行。
        board_row["return_rate"] = safe_rate(board_row["total_result"], board_row["buy_cost"])  # 计算当前板块收益率。
    batch_orders = [normalize_order(row) for row in orders if batch_from_order(row) == name]  # 筛选并标准化当前批次今日委托。
    batch_trades = [row for row in trades if batch_from_order(row) == name]  # 筛选当前批次今日成交。
    buy_trades = [row for row in batch_trades if safe_int(row.get("order_type")) == xtconstant.STOCK_BUY]  # 筛选当前批次今日买入成交。
    sell_trades = [row for row in batch_trades if safe_int(row.get("order_type")) == xtconstant.STOCK_SELL]  # 筛选当前批次今日卖出成交。
    total_result = sell_amount + remaining_market - buy_cost  # 计算当前批次合计结果。
    return {"name": name, "summary": {"stock_count": len(code_set), "filled_count": filled_count, "closed_count": closed_count, "open_count": open_count, "buy_unfilled_count": buy_unfilled_count, "skipped_count": 0, "pending_count": len(pending_codes), "pending_codes": pending_codes, "buy_cost": buy_cost, "sell_amount": sell_amount, "remaining_market": remaining_market, "realized_pnl": realized_pnl, "unrealized_pnl": remaining_market - remaining_cost, "total_result": total_result, "return_rate": safe_rate(total_result, buy_cost), "today_orders": len(batch_orders), "today_buy_trades": len(buy_trades), "today_sell_trades": len(sell_trades), "today_buy_amount": sum(trade_amount(row) for row in buy_trades), "today_sell_amount": sum(trade_amount(row) for row in sell_trades)}, "board_summary": sorted(board_summary.values(), key=lambda row: row["label"]), "positions": rows, "orders": batch_orders}  # 返回批次汇总、板块汇总、明细和委托。
def normalize_order(row: Dict[str, Any]) -> Dict[str, Any]:  # 定义标准化委托记录的函数。
    moment = parse_qmt_time(row.get("order_time"))  # 解析委托时间。
    return {"batch": batch_from_order(row), "code": str(row.get("stock_code") or ""), "side": side_name(row.get("order_type")), "order_id": safe_int(row.get("order_id")), "order_time": time_label(moment), "order_sort": time_key(moment), "order_volume": safe_int(row.get("order_volume")), "traded_volume": safe_int(row.get("traded_volume")), "status": status_name(row.get("order_status")), "price_type": safe_int(row.get("price_type")), "price_type_name": price_type_name(row.get("price_type")), "price": safe_float(row.get("price")), "remark": str(row.get("order_remark") or ""), "message": str(row.get("status_msg") or "")}  # 返回前端展示需要的委托字段。
def summarize_boards(batches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:  # 定义跨批次板块汇总函数。
    summary: Dict[str, Dict[str, Any]] = {}  # 初始化跨批次板块汇总字典。
    for batch in batches:  # 遍历批次数据。
        for row in batch.get("board_summary", []):  # 遍历当前批次板块汇总。
            board = str(row.get("board") or "")  # 读取板块代码。
            target = summary.setdefault(board, {"board": board, "label": board_label(board), "stock_count": 0, "filled_count": 0, "closed_count": 0, "buy_cost": 0.0, "sell_amount": 0.0, "remaining_market": 0.0, "realized_pnl": 0.0, "total_result": 0.0, "return_rate": 0.0})  # 获取或创建跨批次板块汇总行。
            target["stock_count"] += safe_int(row.get("stock_count"))  # 累加板块股票数量。
            target["filled_count"] += safe_int(row.get("filled_count"))  # 累加有买入成交数量。
            target["closed_count"] += safe_int(row.get("closed_count"))  # 累加已关闭数量。
            target["buy_cost"] += safe_float(row.get("buy_cost"))  # 累加买入成本。
            target["sell_amount"] += safe_float(row.get("sell_amount"))  # 累加卖出金额。
            target["remaining_market"] += safe_float(row.get("remaining_market"))  # 累加剩余市值。
            target["realized_pnl"] += safe_float(row.get("realized_pnl"))  # 累加已实现盈亏。
            target["total_result"] += safe_float(row.get("total_result"))  # 累加合计结果。
    for row in summary.values():  # 遍历跨批次板块汇总行。
        row["return_rate"] = safe_rate(row["total_result"], row["buy_cost"])  # 计算跨批次板块收益率。
    return sorted(summary.values(), key=lambda row: row["label"])  # 返回按中文板块名排序的汇总列表。
def market_time_text(moment: datetime) -> str:  # 定义 miniQMT 历史行情时间格式化函数。
    return moment.strftime("%Y%m%d%H%M%S")  # 返回 miniQMT 接口需要的 YYYYMMDDHHMMSS 文本。
def normalize_tick_frame(raw: Any) -> pd.DataFrame:  # 定义标准化 miniQMT tick 行情表的函数。
    if raw is None or getattr(raw, "empty", True):  # 判断原始行情是否为空。
        return pd.DataFrame()  # 原始行情为空时返回空表。
    df = raw.copy()  # 复制原始行情表，避免修改接口返回对象。
    df["stamp"] = pd.to_datetime(df.index.astype(str), format="%Y%m%d%H%M%S", errors="coerce")  # 优先用索引解析 tick 时间。
    if df["stamp"].isna().all() and "time" in df.columns:  # 判断索引时间是否全部解析失败且存在毫秒时间列。
        df["stamp"] = pd.to_datetime(df["time"], unit="ms", errors="coerce")  # 用毫秒时间列解析 tick 时间。
    df = df.dropna(subset=["stamp"]).sort_values("stamp").copy()  # 删除无效时间并按时间排序。
    if "lastPrice" not in df.columns:  # 判断是否缺少最新价字段。
        return pd.DataFrame()  # 缺少价格字段时返回空表。
    df["price"] = pd.to_numeric(df["lastPrice"], errors="coerce").fillna(0)  # 把最新价转换为数值列。
    df = df[df["price"] > 0].copy()  # 只保留有效价格 tick。
    if df.empty:  # 判断过滤后是否没有有效行情。
        return pd.DataFrame()  # 没有有效行情时返回空表。
    df["last_close"] = pd.to_numeric(df["lastClose"], errors="coerce").ffill().fillna(0) if "lastClose" in df.columns else 0.0  # 提取当前交易日昨收价用于计算涨幅。
    volume = pd.to_numeric(df["volume"], errors="coerce").ffill().fillna(0) if "volume" in df.columns else pd.Series([0] * len(df), index=df.index)  # 读取累计成交量并兼容缺失列。
    amount = pd.to_numeric(df["amount"], errors="coerce").ffill().fillna(0) if "amount" in df.columns else pd.Series([0] * len(df), index=df.index)  # 读取累计成交额并兼容缺失列。
    df["volume_delta"] = volume.diff().fillna(0).clip(lower=0)  # 计算相邻 tick 的成交量增量。
    df["amount_delta"] = amount.diff().fillna(0).clip(lower=0)  # 计算相邻 tick 的成交额增量。
    df["time"] = df["stamp"].dt.strftime("%Y-%m-%d %H:%M:%S")  # 生成前端展示使用的时间文本。
    df["trade_date"] = df["stamp"].dt.strftime("%Y%m%d")  # 生成当前 tick 所属交易日期。
    df["rise_rate"] = df.apply(lambda row: safe_rate(safe_float(row.get("price")) - safe_float(row.get("last_close")), safe_float(row.get("last_close"))), axis=1)  # 计算相对当日昨收涨幅。
    return df[["stamp", "time", "trade_date", "price", "last_close", "rise_rate", "volume_delta", "amount_delta"]].copy()  # 返回前端图表需要的精简字段。
def sample_tick_rows(df: pd.DataFrame, max_points: int) -> pd.DataFrame:  # 定义按最大点数抽样 tick 行情的函数。
    if df.empty or len(df) <= max_points:  # 判断是否无需抽样。
        return df.copy()  # 点数较少时直接返回副本。
    indexes = sorted(set(round(index * (len(df) - 1) / max(1, max_points - 1)) for index in range(max_points)))  # 计算等距抽样行号。
    return df.iloc[indexes].copy()  # 按抽样行号返回压缩后的行情表。
def tick_points_from_frame(df: pd.DataFrame) -> List[Dict[str, Any]]:  # 定义把 tick 表转为前端点数组的函数。
    points: List[Dict[str, Any]] = []  # 初始化前端点数组。
    for row in df.to_dict("records"):  # 遍历抽样后的行情记录。
        points.append({"time": str(row.get("time") or ""), "trade_date": str(row.get("trade_date") or ""), "price": safe_float(row.get("price")), "last_close": safe_float(row.get("last_close")), "rise_rate": safe_float(row.get("rise_rate")), "volume": safe_float(row.get("volume_delta")), "amount": safe_float(row.get("amount_delta"))})  # 追加单个行情点。
    return points  # 返回前端可直接绘制的点数组。
def fetch_stock_tick_frame(code: str, start: datetime, end: datetime) -> pd.DataFrame:  # 定义读取单只股票指定区间 tick 表的函数。
    if end <= start:  # 判断结束时间是否不晚于开始时间。
        return pd.DataFrame()  # 时间范围无效时返回空表。
    start_text = market_time_text(start)  # 格式化行情开始时间。
    end_text = market_time_text(end)  # 格式化行情结束时间。
    try:  # 捕获单只股票行情读取异常，避免一个股票失败拖垮整个看板。
        xtdata.download_history_data(code, "tick", start_text, end_text)  # 下载指定区间历史 tick 行情。
        raw_map = xtdata.get_market_data_ex([], [code], period="tick", start_time=start_text, end_time=end_text, count=-1, dividend_type="none", fill_data=True)  # 读取指定区间历史 tick 行情。
        raw_df = raw_map.get(code) if isinstance(raw_map, dict) else None  # 从 miniQMT 返回字典中取出目标股票表。
        df = normalize_tick_frame(raw_df)  # 标准化原始 tick 表。
        if df.empty:  # 判断是否没有标准化后的行情点。
            return pd.DataFrame()  # 无行情时返回空表。
        window = df[(df["stamp"] >= pd.Timestamp(start)) & (df["stamp"] <= pd.Timestamp(end))].copy()  # 再按真实买卖窗口过滤一次。
        return window  # 返回过滤后的原始密度行情表。
    except Exception as exc:  # 捕获 miniQMT 下载或读取异常。
        print(f"读取 {code} 指定区间 tick 行情失败：{exc}")  # 输出单只股票失败原因，方便命令行排查。
        return pd.DataFrame()  # 当前股票行情失败时返回空表。
def fetch_stock_tick_points(code: str, start: datetime, end: datetime, max_points: int = MAX_STOCK_CHART_POINTS) -> List[Dict[str, Any]]:  # 定义读取单只股票指定区间 tick 点的函数。
    frame = fetch_stock_tick_frame(code, start, end)  # 读取指定区间原始 tick 表。
    sampled = sample_tick_rows(frame, max_points)  # 把行情点抽样到前端可流畅展示的数量。
    return tick_points_from_frame(sampled)  # 转换并返回前端行情点。
def trading_day_window(day_text: str, today_text: str) -> Dict[str, datetime]:  # 定义单个交易日的行情窗口函数。
    start = datetime.strptime(f"{day_text} 09:30:00", "%Y%m%d %H:%M:%S")  # 生成当前交易日开盘起点。
    full_end = datetime.strptime(f"{day_text} 15:00:00", "%Y%m%d %H:%M:%S")  # 生成当前交易日收盘终点。
    now_moment = datetime.now()  # 读取当前本地时间，用于今日尚未收盘时截断。
    end = min(full_end, now_moment) if day_text == today_text else full_end  # 今日取到当前时间或收盘，历史日取全天。
    end = max(end, start)  # 保证结束时间不早于开盘时间。
    return {"start": start, "end": end}  # 返回当前交易日行情窗口。
def fetch_two_day_tick_points(code: str, previous_day: str, today_day: str, max_points: int = MAX_STOCK_CHART_POINTS) -> List[Dict[str, Any]]:  # 定义读取 T-1 和 T 日两段行情点的函数。
    day_points: List[Dict[str, Any]] = []  # 初始化两日行情点数组。
    per_day_points = max(40, max_points // 2)  # 计算每个交易日最多保留的点数。
    for day_text in [previous_day, today_day]:  # 遍历 T-1 和 T 日。
        if not day_text:  # 判断交易日文本是否缺失。
            continue  # 交易日缺失时跳过。
        window = trading_day_window(day_text, today_day)  # 计算当前交易日行情窗口。
        frame = fetch_stock_tick_frame(code, window["start"], window["end"])  # 读取当前交易日历史 tick 表。
        sampled = sample_tick_rows(frame, per_day_points)  # 抽样当前交易日行情点。
        day_points.extend(tick_points_from_frame(sampled))  # 追加当前交易日点到总数组。
    return day_points  # 返回 T-1 和 T 日拼接后的行情点。
def plan_window(plan: Dict[str, Any]) -> Dict[str, Optional[datetime]]:  # 定义从交易计划推导图表时间窗口的函数。
    buy_moment = parse_qmt_time(plan.get("buy_trade_time"))  # 读取买入成交时间。
    sell_moment = parse_qmt_time(plan.get("sell_trade_time")) if valid_sell_time(plan) else None  # 读取当前计划有效卖出成交时间。
    if buy_moment is None:  # 判断是否缺少买入成交时间。
        return {"buy": None, "sell": sell_moment, "start": None, "end": None}  # 无买入时间时返回空窗口。
    buy_day = compact_date(plan.get("buy_date")) or buy_moment.strftime("%Y%m%d")  # 读取买入日期作为基础交易日。
    today_day = (sell_moment.strftime("%Y%m%d") if sell_moment else buy_day)  # 有有效卖出时 T 为卖出日，否则 T 为买入日。
    previous_day = previous_calendar_day(today_day) if today_day == buy_day and sell_moment is None else buy_day  # 未卖出持仓取买入日前一日作为 T-1，已卖出计划取买入日作为 T-1。
    start = trading_day_window(previous_day, today_day)["start"]  # 生成 T-1 日开盘起点。
    end = trading_day_window(today_day, today_day)["end"]  # 生成 T 日当前或收盘终点。
    return {"buy": buy_moment, "sell": sell_moment, "start": start, "end": end, "previous_day": previous_day, "today_day": today_day}  # 返回两日全天行情窗口。
def empty_stock_chart(batch_name_value: str, row: Dict[str, Any], message: str) -> Dict[str, Any]:  # 定义生成无行情股票卡片数据的函数。
    return {"batch": batch_name_value, "code": row.get("code", ""), "board": row.get("board", ""), "board_label": row.get("board_label", ""), "status": row.get("status", ""), "buy_time": "", "sell_time": "", "range_start": "", "range_end": "", "previous_day": "", "today_day": "", "buy_price": safe_float(row.get("buy_price")), "sell_price": safe_float(row.get("sell_price")), "target_price": safe_float(row.get("target_price")), "shares": safe_int(row.get("filled_shares")), "sell_volume": safe_int(row.get("sell_volume")), "total_pnl": safe_float(row.get("total_pnl")), "return_rate": safe_float(row.get("return_rate")), "points": [], "message": message}  # 返回前端展示空状态所需字段。
def build_stock_chart(batch_name_value: str, row: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:  # 定义构建单只股票复盘图数据的函数。
    window = plan_window(plan)  # 计算当前股票买入到卖出的行情窗口。
    if window.get("start") is None or window.get("end") is None:  # 判断行情窗口是否不可用。
        return empty_stock_chart(batch_name_value, row, "没有买入成交时间，无法截取买卖区间行情")  # 返回缺少时间的空状态。
    points = fetch_two_day_tick_points(str(row.get("code") or ""), str(window.get("previous_day") or ""), str(window.get("today_day") or ""))  # 读取 T-1 全天和 T 日截至当前的行情点。
    message = "" if points else "miniQMT 暂未返回该区间 tick 行情"  # 生成无行情说明。
    return {"batch": batch_name_value, "code": row.get("code", ""), "board": row.get("board", ""), "board_label": row.get("board_label", ""), "status": row.get("status", ""), "buy_time": time_label(window.get("buy")), "sell_time": time_label(window.get("sell")), "range_start": time_label(window.get("start")), "range_end": time_label(window.get("end")), "previous_day": str(window.get("previous_day") or ""), "today_day": str(window.get("today_day") or ""), "buy_price": safe_float(row.get("buy_price")), "sell_price": safe_float(row.get("sell_price")), "target_price": safe_float(row.get("target_price")), "shares": safe_int(row.get("filled_shares")), "sell_volume": safe_int(row.get("sell_volume")), "total_pnl": safe_float(row.get("total_pnl")), "return_rate": safe_float(row.get("return_rate")), "points": points, "message": message}  # 返回单只股票复盘图完整数据。
def attach_stock_charts(batch: Dict[str, Any], plans: Dict[str, Any]) -> None:  # 定义给批次附加股票行情小图数据的函数。
    charts: List[Dict[str, Any]] = []  # 初始化当前批次小图列表。
    for row in batch.get("positions", []):  # 遍历当前批次所有股票明细。
        code = str(row.get("code") or "")  # 读取股票代码。
        plan = find_plan_for_code_batch(plans, code, str(batch.get("name") or "")) if isinstance(plans, dict) else {}  # 按当前批次读取股票交易计划。
        if safe_int(row.get("filled_shares")) <= 0:  # 判断当前股票是否没有买入成交。
            charts.append(empty_stock_chart(str(batch.get("name") or ""), row, "未买入成交"))  # 追加未买入股票的空状态卡片。
            continue  # 未买入股票不读取行情。
        charts.append(build_stock_chart(str(batch.get("name") or ""), row, plan))  # 追加已成交股票的行情复盘卡片。
    batch["stock_charts"] = charts  # 把小图数据挂到批次结构上供前端渲染。
def build_snapshot() -> Dict[str, Any]:  # 定义构建完整前端快照的函数。
    state = read_state()  # 读取本地策略状态。
    account_snapshot = load_account_snapshot()  # 查询当前 miniQMT 账户快照，失败时使用本地摘要。
    positions = {str(item.get("stock_code") or ""): item for item in account_snapshot["positions"]}  # 按股票代码索引当前持仓。
    orders = [row for row in account_snapshot["orders"] if not qmt_broker.is_cleanup_order_row(row)]  # 读取今日委托列表，过滤人工清理旧持仓委托。
    trades = enrich_trades_with_order_batches([row for row in account_snapshot["trades"] if not qmt_broker.is_cleanup_order_row(row)], orders)  # 用当日委托备注补齐成交所属批次，并过滤人工清理旧持仓成交。
    plans = build_visual_plans(state)  # 构建可视化专用持仓计划，支持重复代码分批展示。
    apply_visual_buy_fills(plans, trades)  # 用当日真实买入成交修正自动批次计划，避免 state.json 状态滞后。
    apply_visual_sell_fills(plans, trades)  # 用当日真实卖出成交修正可视化计划，避免重复股票被旧批次成交污染。
    board_map = build_board_map()  # 构建股票代码到中证1000或科创板的映射。
    dynamic_batches = [summarize_batch(item["name"], item["codes"], plans, positions, orders, trades, board_map) for item in collect_dynamic_batch_codes(state, plans, orders, trades)]  # 构建按买入日自动生成的最新批次汇总。
    legacy_batches = [summarize_batch(name, codes, plans, positions, orders, trades, board_map) for name, codes in LEGACY_BATCHES]  # 构建老批次汇总。
    batches = dynamic_batches + legacy_batches  # 汇总各批交易数据，自动批次按日期倒序优先展示。
    for batch in batches:  # 遍历三批交易数据。
        attach_stock_charts(batch, plans)  # 为当前批次附加每只股票的买卖区间行情小图数据。
    boards = summarize_boards(batches)  # 构建跨批次板块收益归因。
    relevant_orders = [normalize_order(row) for row in orders if batch_from_order(row) != "其他"]  # 筛选前端需要展示的两批委托。
    active_positions = [item for item in account_snapshot["positions"] if safe_int(item.get("volume")) > 0]  # 筛选当前仍有股数的持仓。
    timeline = build_timeline(plans, orders, trades, board_map)  # 构建细粒度交易时间线。
    return {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "process_ids": query_process_ids(), "account": {"total_asset": safe_float(account_snapshot["asset"].get("total_asset")), "cash": safe_float(account_snapshot["asset"].get("cash")), "market_value": safe_float(account_snapshot["asset"].get("market_value")), "frozen_cash": safe_float(account_snapshot["asset"].get("frozen_cash")), "position_count": len(active_positions), "order_count": len(orders), "trade_count": len(trades)}, "strategies": build_multi_strategy_snapshot(state), "boards": boards, "batches": batches, "timeline": timeline, "orders": sorted(relevant_orders, key=lambda item: (item["order_sort"], item["order_id"]))}  # 返回完整看板快照。
def write_snapshot(snapshot: Dict[str, Any]) -> Path:  # 定义写入前端数据文件的函数。
    output_path = Path(__file__).resolve().parent / "trade_snapshot.js"  # 拼接前端快照脚本路径。
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))  # 把快照压缩成一行 JSON 文本。
    output_path.write_text(f"window.tradeDashboardData = {payload}; // 设置交易看板使用的最新账户快照数据。\\n", encoding="utf-8")  # 写入带中文注释的前端数据脚本。
    return output_path  # 返回写入的数据文件路径。
if __name__ == "__main__":  # 判断是否直接运行当前脚本。
    target_path = write_snapshot(build_snapshot())  # 构建并写入交易看板快照。
    print(f"已生成交易看板数据：{target_path}")  # 输出生成完成提示。
