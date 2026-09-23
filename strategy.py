# -*- coding: utf-8 -*-  # 声明源码使用 UTF-8 编码，保证中文注释和中文路径可以被 Python 正确读取。
import argparse  # 导入命令行参数类型，用于标注策略参数。
import datetime as dt  # 导入日期时间模块，用于预测记录文件名和触发时间判断。
import json  # 导入 JSON 模块，用于编码预测请求和解析接口响应。
import math  # 导入数学模块，用于校验预测分数是否为有限数值。
import os  # 导入系统模块，用于拼接预测记录目录。
import re  # 导入正则模块，用于从预测接口响应中提取股票代码。
import threading  # 导入线程模块，让耗时预测请求不阻塞盘中卖出检查。
import time  # 导入时间模块，用于预测接口失败后的重试等待。
import urllib.error  # 导入 URL 错误模块，用于捕获预测接口 HTTP 异常。
import urllib.request  # 导入 URL 请求模块，用标准库发送 HTTP POST 请求。
from typing import Any, Dict, List, Optional, Sequence  # 导入类型标注，提升代码可读性。

import qmt_broker  # 导入虚拟 miniQMT 券商模块，负责真实账户查询和直接委托。
import multi_strategy  # 导入多策略分账与执行模块。
import util  # 导入公共工具模块，负责日志、环境变量、QMT 行情和持久化。

DEFAULT_STOCKS = "all"  # 请求全市场预测，最终买入仍只保留中证1000和科创板。
DEFAULT_NUM_SAMPLES = 64  # 设置默认预测采样次数，与参考项目保持一致。
DEFAULT_SCORES = "2"  # 设置默认预测 score 编号，与参考项目保持一致。
DEFAULT_PREDICT_API_URL = "http://100.123.62.35:8008/api/live/predict_sync"  # 设置默认预测接口地址。
DEFAULT_PREDICT_API_TIMEOUT = 1200.0  # 设置默认预测接口超时时间为二十分钟。
DEFAULT_PREDICT_API_RETRIES = 3  # 设置默认预测接口最大重试次数。
DEFAULT_PREDICT_API_RETRY_SECONDS = 10.0  # 设置默认预测接口失败后的重试等待秒数。
CSI1000_BUY_RATIO = 0.008  # 设置中证1000按预测排序截取前 0.8%。
KCB_BUY_RATIO = 0.01  # 设置科创板按预测排序截取前 1%。
CSI1000_SCORE_LAMBDA = 0.0  # 中证1000的风险惩罚系数。
KCB_SCORE_LAMBDA = 0.4  # 科创板的风险惩罚系数。


def initialize(context: Any) -> None:  # 定义聚宽风格的策略初始化函数。
    multi_strategy.ensure_state(context.state)
    context.predict_thread = None
    context.predict_result = None
    context.predict_error = ""
    context.predict_retry_after = 0.0
    util.log("[strategy] initialize")  # 记录策略初始化事件。


def before_trading_start(context: Any) -> None:  # 定义聚宽风格的盘前函数。
    util.log(f"[strategy] before_trading_start trade_date={context.trade_date}")  # 记录当前交易日。


def handle_data(context: Any, data: Any) -> None:  # 定义聚宽风格的盘中函数。
    # 旧版未完成持仓继续按原规则退出；所有新订单进入多策略独立账本。
    qmt_broker.sync_trade_fills(context.trader, context.account, context.state, context.args)
    qmt_broker.sell_due_positions(context.trader, context.account, context.state, context.trade_date, context.args)
    multi_strategy.sync_trade_fills(context.trader, context.account, context.state)
    multi_strategy.manage_sells(context.trader, context.account, context.state, context.trade_date, context.args)

    root = multi_strategy.ensure_state(context.state)
    cached_items = root["predictions"].get(context.cache_key)
    if context.predict_thread is not None and not context.predict_thread.is_alive():
        context.predict_thread = None
        if context.predict_result:
            cached_items = multi_strategy.compact_prediction_items(context.predict_result, numeric_score_field)
            root["predictions"][context.cache_key] = cached_items
            multi_strategy.prune_prediction_cache(root)
            util.save_state(context.state)
            util.log(f"[multi][predict] 全市场评分已缓存 count={len(cached_items)} key={context.cache_key}")
        else:
            context.predict_retry_after = time.time() + float(context.args.predict_api_retry_seconds)
            util.log(f"[multi][predict] 本次预测无有效结果 error={context.predict_error}")
        context.predict_result = None

    now = dt.datetime.now().time()
    if (
        not cached_items
        and context.predict_thread is None
        and now >= util.parse_hms(context.args.predict_time)
        and now < util.parse_hms(multi_strategy.BUY_CUTOFF_TIME)
        and time.time() >= float(context.predict_retry_after or 0.0)
    ):
        start_prediction_thread(context)

    all_buys_attempted = all(
        f"{context.trade_date}:{strategy_id}" in root["buy_attempts"]
        for strategy_id in multi_strategy.STRATEGY_CONFIGS
    )
    if cached_items and not all_buys_attempted and now >= util.parse_hms(context.args.buy_time) and now < util.parse_hms(multi_strategy.BUY_CUTOFF_TIME):
        selections = select_strategies(cached_items)
        multi_strategy.buy_all_strategies(
            context.trader,
            context.account,
            context.state,
            context.trade_date,
            selections,
            context.args,
        )
        multi_strategy.sync_trade_fills(context.trader, context.account, context.state)


def after_trading_end(context: Any) -> None:  # 定义聚宽风格的盘后函数。
    qmt_broker.sync_trade_fills(context.trader, context.account, context.state, context.args)  # 盘后汇总前补同步成交，避免状态滞留在已提交。
    multi_strategy.sync_trade_fills(context.trader, context.account, context.state)
    qmt_broker.write_summary(context.trader, context.account, context.state, util.today_yyyymmdd())  # 写入账户摘要和每日权益。


def start_prediction_thread(context: Any) -> None:
    def worker() -> None:
        try:
            context.predict_result = call_predict_api(context.predict_date, context.args)
            context.predict_error = ""
        except Exception as exc:
            context.predict_result = []
            context.predict_error = repr(exc)

    context.predict_result = None
    context.predict_error = ""
    context.predict_thread = threading.Thread(target=worker, name="tick-llm-predict", daemon=True)
    context.predict_thread.start()
    util.log(f"[multi][predict] 后台预测已启动 key={context.cache_key}")


def resolve_candidate_pool() -> List[str]:  # 定义生成预测兜底股票池的函数。
    csi1000 = util.get_sector_codes("中证1000") or util.get_sector_codes("csi1000")  # 优先读取中证1000成分。
    kcb = [code for code in util.get_sector_codes("科创板") if code.endswith(".SH")]  # 读取科创板股票代码。
    pool = sorted(set(csi1000 + kcb))  # 合并并去重排序。
    if pool:  # 判断目标池是否为空。
        return pool  # 返回目标候选股票池。
    return sorted(set(util.get_sector_codes("沪深A股")))  # 目标池为空时回退到沪深 A 股。


def resolve_tradeable_pool() -> List[str]:  # 定义生成可交易股票池的函数。
    return sorted(set(util.get_sector_codes("沪深A股")))  # 使用沪深 A 股宽池做预测结果校验。


def normalize_stocks_arg(stocks: str) -> object:  # 定义把股票池别名转换为预测接口参数的函数。
    alias = {"csi300": "csi300", "csi1000": "中证1000", "csi2000": "csi2000", "kcb": "科创板", "star": "科创板", "kechuang": "科创板", "all": "all"}  # 设置常见股票池别名映射。
    items = [item.strip() for item in str(stocks).split(",") if item.strip()]  # 按逗号拆分并清理股票池参数。
    if not items:  # 判断用户是否传入空股票池。
        items = DEFAULT_STOCKS.split(",")  # 空股票池时使用默认股票池。
    mapped = [alias.get(item, item) for item in items]  # 把别名映射为预测接口识别的名称。
    if len(mapped) == 1 and mapped[0] in ("all", "csi300", "csi2000"):  # 判断是否为接口支持的单字符串股票池。
        return mapped[0]  # 返回单字符串股票池参数。
    return mapped  # 返回组合股票池列表。


def parse_scores(value: str) -> List[int]:  # 定义解析 score 参数的函数。
    return [int(item.strip()) for item in value.split(",") if item.strip()]  # 把逗号分隔文本转换为整数列表。


def build_payload(date: str, stocks: str, num_samples: int, scores: Sequence[int]) -> Dict[str, Any]:  # 定义生成预测请求体的函数。
    return {"date": date, "stocks": normalize_stocks_arg(stocks), "num_samples": num_samples, "scores": list(scores)}  # 返回预测接口 JSON 请求体，不向服务端传入 lambda。


def extract_codes(value: object) -> List[str]:  # 定义从任意响应结构提取股票代码的函数。
    codes: List[str] = []  # 初始化代码结果列表。
    if isinstance(value, str):  # 判断当前值是否为字符串。
        if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", value):  # 判断整个字符串是否就是股票代码。
            return [value]  # 返回单个股票代码。
        return re.findall(r"\b\d{6}\.(?:SH|SZ|BJ)\b", value)  # 从字符串中提取全部股票代码。
    if isinstance(value, list):  # 判断当前值是否为列表。
        for item in value:  # 遍历列表元素。
            codes.extend(extract_codes(item))  # 递归提取元素里的股票代码。
    elif isinstance(value, dict):  # 判断当前值是否为字典。
        for key in ("stock_code", "code", "symbol", "股票代码"):  # 优先检查常见股票代码字段。
            if key in value:  # 判断字段是否存在。
                codes.extend(extract_codes(value[key]))  # 从该字段递归提取股票代码。
        if not codes:  # 判断当前字典是否还没有直接提取出代码。
            for item in value.values():  # 遍历字典所有值。
                codes.extend(extract_codes(item))  # 递归提取内部值里的股票代码。
    return list(dict.fromkeys(codes))  # 去重并保留原始顺序。


def extract_predict_items(value: object) -> List[Dict[str, Any]]:  # 定义从预测响应提取预测项的函数。
    items: List[Dict[str, Any]] = []  # 初始化预测项列表。
    if isinstance(value, list):  # 判断当前值是否为列表。
        for item in value:  # 遍历列表元素。
            items.extend(extract_predict_items(item))  # 递归提取预测项。
    elif isinstance(value, dict):  # 判断当前值是否为字典。
        direct_codes: List[str] = []  # 初始化当前字典直接包含的代码列表。
        for key in ("stock_code", "code", "symbol", "股票代码"):  # 遍历常见代码字段。
            if key in value:  # 判断字段是否存在。
                direct_codes.extend(extract_codes(value[key]))  # 从字段中提取股票代码。
        if direct_codes:  # 判断当前字典是否代表预测记录。
            for code in direct_codes:  # 遍历当前记录包含的股票代码。
                items.append({"stock_code": code, **value})  # 保留整条记录并补齐标准字段。
        else:  # 当前字典不是直接预测记录时。
            for item in value.values():  # 遍历字典内部字段。
                items.extend(extract_predict_items(item))  # 递归提取内部预测项。
    elif isinstance(value, str):  # 兼容直接返回字符串代码的情况。
        for code in extract_codes(value):  # 从字符串中提取股票代码。
            items.append({"stock_code": code})  # 构造最小预测项。
    return items  # 返回预测项列表。


def parse_predict_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:  # 定义解析预测响应的函数。
    result = data.get("result", {})  # 读取响应 result 节点。
    candidates: List[Dict[str, Any]] = []  # 初始化候选预测项列表。
    if isinstance(result, dict):  # 判断 result 是否为字典。
        for key in ("scores", "top", "selected", "stocks", "stock_codes"):  # 优先从常见字段提取。
            candidates.extend(extract_predict_items(result.get(key)))  # 提取当前字段里的预测项。
    for key in ("scores", "top", "selected", "stocks", "stock_codes"):  # 兼容顶层常见字段。
        candidates.extend(extract_predict_items(data.get(key)))  # 提取顶层字段里的预测项。
    unique: Dict[str, Dict[str, Any]] = {}  # 初始化按股票代码去重的字典。
    for item in candidates:  # 遍历提取到的预测项。
        code = str(item.get("stock_code") or "")  # 读取标准股票代码字段。
        if code and code not in unique:  # 只保留第一次出现的代码。
            unique[code] = item  # 保存预测项。
    return list(unique.values())  # 返回去重后的预测项列表。


def normalize_predict_board(item: Dict[str, Any]) -> str:  # 定义识别预测项所属板块的函数。
    for key in ("board", "sector", "stock_pool", "pool", "source", "universe", "group", "index", "category", "板块"):  # 遍历常见板块字段。
        value = item.get(key)  # 读取当前板块字段值。
        if value is None:  # 判断字段是否不存在。
            continue  # 继续检查下一个字段。
        text = str(value).lower()  # 转成小写文本便于匹配。
        if "kcb" in text or "star" in text or "科创" in text or "688" in text:  # 判断是否科创板。
            return "kcb"  # 返回科创板归一化名称。
        if "csi1000" in text or "中证1000" in text or "zz1000" in text:  # 判断是否中证1000。
            return "csi1000"  # 返回中证1000归一化名称。
    code = str(item.get("stock_code") or "")  # 读取股票代码。
    if code.startswith("688") and code.endswith(".SH"):  # 用 688.SH 前缀兜底判断科创板。
        return "kcb"  # 返回科创板。
    return "csi1000"  # 默认按中证1000处理。


def numeric_score_field(item: Dict[str, Any], kind: str) -> Optional[float]:
    aliases = {
        "mean": ("score_mean", "mean", "primary_score_mean", "primary_score", "score_avg", "avg"),
        "std": ("score_std", "std", "primary_score_std", "score_stddev", "stddev", "standard_deviation"),
    }
    nested_key = "score_stds" if kind == "std" else "scores"
    nested = item.get(nested_key)
    if isinstance(nested, dict):
        for value in nested.values():
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                return float(value)
    for key in aliases[kind]:
        value = item.get(key)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
    tokens = ("std", "stddev", "standard_deviation") if kind == "std" else ("mean", "avg")
    for key, value in item.items():
        lowered = str(key).lower()
        if isinstance(value, (int, float)) and any(token in lowered for token in tokens):
            if math.isfinite(float(value)):
                return float(value)
    return None

def score_predict_item(item: Dict[str, Any], lambda_value: float) -> Optional[float]:
    mean = numeric_score_field(item, "mean")
    std = numeric_score_field(item, "std")
    if mean is None or std is None:
        return None
    return mean - lambda_value * std

def resolve_target_pools() -> Dict[str, set]:
    csi1000 = set(util.xtdata.get_index_weight("000852.SH"))
    if not csi1000:
        try:
            util.xtdata.download_index_weight()
            csi1000 = set(util.xtdata.get_index_weight("000852.SH"))
        except Exception as exc:
            util.log(f"[predict] 中证1000指数成分下载失败 error={exc}")
    if not csi1000:
        csi1000 = set(util.get_sector_codes("中证1000") or util.get_sector_codes("csi1000"))
    kcb = {code for code in util.get_sector_codes("科创板") if code.startswith("688") and code.endswith(".SH")}
    return {"csi1000": csi1000, "kcb": kcb}

def select_top_by_board(items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    pools = resolve_target_pools()

    def select(lambda_value: float, ratio: float, pool: set) -> Dict[str, Any]:
        scored = []
        for item in items:
            score = score_predict_item(item, lambda_value)
            code = str(item.get("stock_code") or "")
            if code and score is not None:
                scored.append((score, code))
        scored.sort(key=lambda row: (-row[0], row[1]))
        limit = int(len(scored) * ratio) if scored else 0
        top_codes = [code for _, code in scored[:limit]]
        selected = [code for code in top_codes if code in pool]
        return {"codes": selected, "market_total": len(scored), "top_count": limit}

    csi = select(CSI1000_SCORE_LAMBDA, CSI1000_BUY_RATIO, pools["csi1000"])
    kcb = select(KCB_SCORE_LAMBDA, KCB_BUY_RATIO, pools["kcb"])
    selected = list(dict.fromkeys(csi["codes"] + kcb["codes"]))
    return {"codes": selected, "market_total": max(csi["market_total"], kcb["market_total"]), "csi1000_total": len(pools["csi1000"]), "csi1000_top": csi["top_count"], "csi1000_selected": len(csi["codes"]), "kcb_total": len(pools["kcb"]), "kcb_top": kcb["top_count"], "kcb_selected": len(kcb["codes"])}


def rank_global_codes(items: Sequence[Dict[str, Any]], ratio: float, lambda_value: float) -> List[str]:
    scored = []
    for item in items:
        code = str(item.get("stock_code") or "")
        if not code.endswith((".SH", ".SZ")):
            continue
        mean = numeric_score_field(item, "mean")
        std = numeric_score_field(item, "std")
        if mean is None or (lambda_value != 0.0 and std is None):
            continue
        score = float(mean) - lambda_value * float(std or 0.0)
        scored.append((score, code))
    scored.sort(key=lambda row: (-row[0], row[1]))
    limit = int(len(scored) * ratio) if scored else 0
    return [code for _score, code in scored[:limit]]


def select_strategies(items: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
    pools = resolve_target_pools()
    csi1000 = pools["csi1000"]
    s1_csi = [code for code in rank_global_codes(items, 0.008, 0.0) if code in csi1000]
    selections = {
        "s1": s1_csi,
        "s2": [code for code in rank_global_codes(items, 0.0117, 0.0) if code in csi1000],
        "s3": [code for code in rank_global_codes(items, 0.0108, 0.0) if code in csi1000],
        "s4": rank_global_codes(items, 0.005, 0.0),
        "s5": rank_global_codes(items, 0.008, 0.0),
        "s6": rank_global_codes(items, 0.005, 0.0),
        "s7": rank_global_codes(items, 0.004, 0.0),
        "s8": rank_global_codes(items, 0.008, 0.0),
        "s9": rank_global_codes(items, 0.008, 0.0),
        "s10": rank_global_codes(items, 0.008, 0.0),
        "s11": rank_global_codes(items, 0.008, 0.0),
        "s12": rank_global_codes(items, 0.008, 0.0),
        "s13": rank_global_codes(items, 0.008, 0.0),
    }
    util.log(
        "[multi][select] "
        + " ".join(f"{strategy_id}={len(codes)}" for strategy_id, codes in selections.items())
    )
    return selections


def select_five_strategies(items: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
    """保留旧调用入口；返回当前完整策略集合。"""
    return select_strategies(items)


def filter_candidate_codes(predicted: Sequence[str], allowed_pool: Sequence[str]) -> Dict[str, Any]:  # 定义过滤预测候选的函数。
    allowed_set = set(allowed_pool)  # 把本地可交易股票池转换为集合。
    scoped = [code for code in predicted if not allowed_set or code in allowed_set]  # 过滤本地股票池外的代码。
    codes: List[str] = []  # 初始化最终候选列表。
    st_filtered = 0  # 初始化 ST 过滤计数。
    for code in scoped:  # 按预测排序遍历候选股票。
        if util.is_st_stock(code):  # 判断是否 ST 或退市风险股票。
            st_filtered += 1  # 累加 ST 过滤数量。
            continue  # 跳过当前股票。
        codes.append(code)  # 保留可交易候选股票。
    return {"codes": codes, "out_of_pool": len(predicted) - len(scoped), "st_filtered": st_filtered}  # 返回过滤结果和统计。


def predict_record_dir(date: str) -> str:  # 定义预测调用记录目录函数。
    path = os.path.join(util.DATA_DIR, "predict_api", date)  # 按预测日期拼接记录目录。
    os.makedirs(path, exist_ok=True)  # 确保记录目录存在。
    return path  # 返回记录目录路径。


def predict_record_prefix(date: str) -> str:  # 定义预测调用记录文件名前缀函数。
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")  # 使用当前时间生成唯一前缀。
    return os.path.join(predict_record_dir(date), f"{stamp}_predict_sync")  # 返回完整记录文件名前缀。


def load_predict_response_file(date: str, args: argparse.Namespace) -> List[Dict[str, Any]]:  # 定义读取本地预测响应文件的函数。
    path = str(getattr(args, "predict_response_file", "") or "")  # 读取命令行传入的本地响应文件路径。
    if not path:  # 判断是否没有配置本地响应文件。
        return []  # 未配置时返回空列表，让调用方走真实接口。
    prefix = predict_record_prefix(date)  # 生成本次本地响应调用记录前缀。
    payload = build_payload("", DEFAULT_STOCKS, args.num_samples, args.scores)  # 实盘固定请求全市场，date 留空。
    util.save_json(f"{prefix}_request.json", {**payload, "_mock_response_file": path})  # 保存本地响应模式的请求记录。
    with open(path, "r", encoding="utf-8-sig") as file_obj:  # 用兼容 BOM 的 UTF-8 打开本地响应文件。
        data = json.load(file_obj)  # 解析本地响应 JSON。
    util.save_json(f"{prefix}_response.json", {"ok": True, "kind": "local_file", "path": path, "data": data})  # 保存本地响应记录。
    util.save_json(os.path.join(util.DATA_DIR, f"predict_sync_{date}_latest.json"), data)  # 保存最新预测响应，保持和真实接口路径一致。
    items = parse_predict_items(data)  # 从本地响应中解析预测项。
    util.log(f"[predict] 使用本地响应 path={path} items={len(items)}")  # 记录本地响应解析结果。
    return items  # 返回解析后的预测项。


def call_predict_api(date: str, args: argparse.Namespace) -> List[Dict[str, Any]]:  # 定义调用预测接口的函数。
    local_items = load_predict_response_file(date, args)  # 优先读取本地预测响应文件，用于联调下单链路。
    if local_items:  # 判断本地响应文件是否返回了预测项。
        return local_items  # 本地响应有效时不再请求远程预测接口。
    url = util.env("PREDICT_API_URL", args.predict_api_url)  # 读取预测接口地址。
    timeout = util.env_float("PREDICT_API_TIMEOUT", float(args.predict_api_timeout))  # 读取预测接口超时时间。
    attempts = max(1, util.env_int("PREDICT_API_RETRIES", int(args.predict_api_retries)))  # 读取预测接口最大尝试次数。
    retry_seconds = max(0.0, util.env_float("PREDICT_API_RETRY_SECONDS", float(args.predict_api_retry_seconds)))  # 读取失败重试等待秒数。
    payload = build_payload("", DEFAULT_STOCKS, args.num_samples, args.scores)  # 实盘固定请求全市场，date 留空。
    body = json.dumps(payload, ensure_ascii=True).encode("ascii")  # 用 ASCII 转义编码请求体，降低 Windows 中文编码风险。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 预测接口走直连，避免 urllib 自动使用系统代理。
    util.log(f"[predict] 请求 payload={json.dumps(payload, ensure_ascii=False)}")  # 记录可读请求体。
    for attempt in range(1, attempts + 1):  # 按配置次数尝试预测接口。
        stop_time_text = str(getattr(args, "stop_time", "") or "")  # 读取当天停止时间，用于避免预测请求拖过停盘。
        if stop_time_text:  # 配置了停止时间时才限制本次请求。
            stop_at = dt.datetime.combine(dt.date.today(), util.parse_hms(stop_time_text))  # 拼出今天的停止时刻。
            remaining_seconds = (stop_at - dt.datetime.now()).total_seconds()  # 计算距离停止时间的剩余秒数。
            if remaining_seconds <= 0:  # 已经到停止时间则不再发起新请求。
                util.log("[predict] 已到停止时间，停止本轮预测请求")
                break
            request_timeout = max(1.0, min(timeout, remaining_seconds))  # 将单次请求超时限制在停止时间之前。
        else:
            request_timeout = timeout  # 未配置停止时间时使用原始超时。
        prefix = predict_record_prefix(date)  # 生成本次调用记录文件名前缀。
        request_record = {**payload, "_attempt": attempt, "_max_attempts": attempts, "_proxy": "disabled"}  # 构造带尝试次数和直连标记的请求记录。
        util.save_json(f"{prefix}_request.json", request_record)  # 保存本次预测请求记录。
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")  # 构造 HTTP POST 请求。
        try:  # 尝试发起 HTTP 请求。
            with opener.open(request, timeout=request_timeout) as response:  # 发送直连请求并等待响应。
                raw = response.read().decode("utf-8")  # 读取并按 UTF-8 解码响应体。
                status_code = getattr(response, "status", None)  # 读取 HTTP 状态码。
        except urllib.error.HTTPError as exc:  # 捕获 HTTP 状态码异常。
            raw_error = exc.read().decode("utf-8", errors="replace")  # 读取 HTTP 错误响应体。
            util.save_json(f"{prefix}_response.json", {"ok": False, "kind": "http_error", "attempt": attempt, "status_code": exc.code, "raw": raw_error})  # 保存 HTTP 错误记录。
            util.log(f"[predict] HTTP 错误 attempt={attempt}/{attempts} code={exc.code} body={raw_error[:300]}")  # 记录 HTTP 错误摘要。
        except OSError as exc:  # 捕获连接、超时等传输异常。
            util.save_json(f"{prefix}_response.json", {"ok": False, "kind": "transport_error", "attempt": attempt, "error": repr(exc)})  # 保存传输错误记录。
            util.log(f"[predict] 传输错误 attempt={attempt}/{attempts} error={exc}")  # 记录传输错误摘要。
        else:  # 请求成功返回时。
            try:  # 尝试解析 JSON 响应。
                data = json.loads(raw)  # 解析响应 JSON。
            except json.JSONDecodeError:  # 捕获非 JSON 响应。
                util.save_json(f"{prefix}_response.json", {"ok": False, "kind": "non_json", "attempt": attempt, "status_code": status_code, "raw": raw})  # 保存非 JSON 响应。
                util.log(f"[predict] 非 JSON 响应 attempt={attempt}/{attempts} body={raw[:300]}")  # 记录非 JSON 摘要。
            else:  # JSON 解析成功时。
                util.save_json(f"{prefix}_response.json", {"ok": True, "kind": "json", "attempt": attempt, "status_code": status_code, "data": data})  # 保存成功响应记录。
                util.save_json(os.path.join(util.DATA_DIR, f"predict_sync_{date}_latest.json"), data)  # 保存最新预测响应。
                items = parse_predict_items(data)  # 从响应中解析预测项。
                util.log(f"[predict] 响应成功 items={len(items)} status={data.get('status')} progress={data.get('progress')}")  # 记录预测结果摘要。
                if items:  # 有有效预测项时才结束本轮请求。
                    return items  # 返回解析后的预测项。
        if attempt < attempts and retry_seconds > 0:  # 判断是否还需要等待后重试。
            time.sleep(retry_seconds)  # 等待指定秒数后继续重试。
    return []  # 所有尝试失败时返回空列表。


def fallback_predict(date: str, pool: Sequence[str], args: argparse.Namespace) -> List[str]:  # 定义预测失败后的兜底函数。
    selected = list(pool)[:max(1, min(50, len(pool)))]  # 选取本地股票池前 50 只作为兜底候选。
    payload = build_payload("", DEFAULT_STOCKS, args.num_samples, args.scores)  # 构造与实盘接口一致的全市场无 lambda 请求体。
    prefix = predict_record_prefix(date)  # 生成兜底记录前缀。
    util.save_json(f"{prefix}_request.json", payload)  # 保存兜底请求记录。
    util.save_json(f"{prefix}_response.json", {"ok": True, "kind": "fallback", "selected": selected})  # 保存兜底响应记录。
    util.log(f"[predict] 使用兜底股票池 count={len(selected)}")  # 记录兜底启用信息。
    return selected  # 返回兜底候选代码。


def select_stocks(predict_date: str, args: argparse.Namespace) -> Optional[List[str]]:  # 定义获取最终买入候选的函数。
    predict_items = call_predict_api(predict_date, args)  # 调用预测接口获取预测项。
    source = "api"  # 初始化候选来源为接口。
    board_stats: Dict[str, Any] = {}  # 初始化板块截取统计。
    if predict_items:  # 判断接口是否返回预测项。
        board_stats = select_top_by_board(predict_items)  # 按板块比例截取预测结果。
        predicted = board_stats["codes"]  # 读取截取后的股票代码。
    else:  # 接口失败或无结果时。
        # fallback_pool = resolve_candidate_pool()  # 已停用：不再读取本地目标股票池做兜底。
        # predicted = fallback_predict(predict_date, fallback_pool, args)  # 已停用：接口失败后不再使用本地股票池兜底。
        # tradeable_pool = fallback_pool  # 已停用：兜底时用目标池做校验。
        # source = "fallback"  # 已停用：标记候选来源为兜底。
        util.log("[predict] 本轮未取得有效预测结果，不使用兜底股票池，等待下一轮继续请求")  # 记录本轮失败后继续重试。
        return None
    tradeable_pool = resolve_tradeable_pool()  # 读取本地可交易宽池用于校验接口结果。
    filtered = filter_candidate_codes(predicted, tradeable_pool)  # 过滤本地不可识别和 ST 股票。
    codes = filtered["codes"]  # 读取最终候选代码。
    util.log(f"[predict] date={predict_date} source={source} market={board_stats.get('market_total', 0)} predicted={len(predicted)} selected={len(codes)} csi1000={board_stats.get('csi1000_selected', 0)}/top{board_stats.get('csi1000_top', 0)} kcb={board_stats.get('kcb_selected', 0)}/top{board_stats.get('kcb_top', 0)} out_of_pool={filtered['out_of_pool']} st_filtered={filtered['st_filtered']}")  # 记录全市场截取和板块交集摘要。
    return codes  # 返回最终候选股票代码。
