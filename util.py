# -*- coding: utf-8 -*-  # 声明源码使用 UTF-8 编码，保证中文注释和中文路径可以被 Python 正确读取。
import csv  # 导入 CSV 模块，用于追加委托记录和账户统计。
import datetime as dt  # 导入日期时间模块，用于日志时间戳和交易日判断。
import json  # 导入 JSON 模块，用于读写状态文件和接口记录。
import os  # 导入系统模块，用于路径拼接、目录创建和环境变量读取。
import subprocess  # 导入子进程模块，用于读取 Windows 本地监听端口状态。
import time  # 导入时间模块，用于 Windows 文件占用时短暂重试。
import urllib.error  # 导入 URL 错误模块，用于捕获日历接口异常。
import urllib.request  # 导入 URL 请求模块，用于调用外部日历接口。
from typing import Any, Dict, List, Optional, Sequence, Set  # 导入类型标注，提升代码可读性。

from xtquant import xtdata  # 导入 miniQMT 行情接口，用于连接行情、读取板块和查询股票名称。

xtdata.enable_hello = False  # 关闭 xtdata 问候输出，避免计划任务日志被无关内容刷屏。

ROOT = os.path.dirname(os.path.abspath(__file__))  # 记录当前项目根目录，保证计划任务下相对路径稳定。
DATA_DIR = os.path.join(ROOT, "virtual_qmt_data")  # 设置策略运行数据目录。
LOG_PATH = os.path.join(DATA_DIR, "virtual_qmt_trader.log")  # 设置主日志文件路径。
LOCK_PATH = os.path.join(DATA_DIR, "virtual_qmt_trader.lock")  # 设置单实例运行锁文件路径。
STATE_PATH = os.path.join(DATA_DIR, "state.json")  # 设置策略状态 JSON 文件路径。
TRADES_PATH = os.path.join(DATA_DIR, "orders.csv")  # 设置委托和成交记录 CSV 文件路径。
SUMMARY_PATH = os.path.join(DATA_DIR, "summary.json")  # 设置最新账户摘要 JSON 文件路径。
DAILY_PATH = os.path.join(DATA_DIR, "daily_stats.csv")  # 设置每日账户权益 CSV 文件路径。
DEFAULT_USERDATA_PATH = r"D:\国金QMT交易端模拟\userdata_mini"  # 设置默认国金虚拟 miniQMT 用户数据目录。
DEFAULT_ACCOUNT_ID = "90007892"  # 设置默认国金虚拟 miniQMT 资金账号。
DEFAULT_ACCOUNT_TYPE = "STOCK"  # 设置默认账号类型为普通股票账户。
PORTS = (58610, 58600)  # 设置 xtdata 常见端口，用于自动连接失败后的兜底尝试。
DEFAULT_HOLIDAYS_2026 = {"20260101", "20260102", "20260216", "20260217", "20260218", "20260219", "20260220", "20260223", "20260406", "20260501", "20260504", "20260505", "20260619", "20260925", "20261001", "20261002", "20261005", "20261006", "20261007"}  # 设置 2026 年 A 股节假日兜底表。
_TRADING_DAY_CACHE: Dict[str, bool] = {}  # 创建交易日判断缓存，减少重复访问日历接口。


class SingleInstanceLock:
    def __init__(self, path: str = LOCK_PATH) -> None:
        self.path = path
        self.file_obj = None

    def acquire(self) -> bool:
        ensure_data_dir()
        self.file_obj = open(self.path, "a+b")
        try:
            self.file_obj.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file_obj.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file_obj.close()
            self.file_obj = None
            return False
        self.file_obj.seek(0)
        self.file_obj.truncate()
        self.file_obj.write(f"pid={os.getpid()} time={dt.datetime.now():%Y-%m-%d %H:%M:%S}\n".encode("utf-8"))
        self.file_obj.flush()
        return True

    def release(self) -> None:
        if self.file_obj is None:
            return
        try:
            self.file_obj.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file_obj.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file_obj.fileno(), fcntl.LOCK_UN)
        finally:
            self.file_obj.close()
            self.file_obj = None

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.release()


def single_instance_lock(path: str = LOCK_PATH) -> SingleInstanceLock:
    return SingleInstanceLock(path)


def ensure_data_dir() -> None:  # 定义确保运行数据目录存在的函数。
    os.makedirs(DATA_DIR, exist_ok=True)  # 创建运行数据目录，目录已存在时保持安静。


def load_env(path: str = os.path.join(ROOT, ".env")) -> Dict[str, str]:  # 定义读取本地 .env 配置的函数。
    values: Dict[str, str] = {}  # 初始化返回给调用方的配置字典。
    if not os.path.exists(path):  # 判断 .env 文件是否存在。
        return values  # 文件不存在时返回空配置。
    with open(path, "r", encoding="utf-8") as file_obj:  # 用 UTF-8 打开 .env 文件。
        for raw_line in file_obj:  # 逐行读取 .env 文件。
            line = raw_line.strip()  # 去掉当前行首尾空白字符。
            if not line or line.startswith("#") or "=" not in line:  # 跳过空行、注释行和非法行。
                continue  # 继续处理下一行配置。
            key, value = line.split("=", 1)  # 只按第一个等号拆分键和值。
            clean_key = key.strip()  # 清理配置键名两侧空白。
            clean_value = value.strip().strip('"').strip("'")  # 清理配置值两侧空白和简单引号。
            values[clean_key] = clean_value  # 把配置保存到返回字典。
            os.environ.setdefault(clean_key, clean_value)  # 写入环境变量但不覆盖外部已经设置的值。
    return values  # 返回从 .env 读取到的配置。


def env(name: str, default: str = "") -> str:  # 定义读取字符串环境变量的函数。
    return os.environ.get(name, default)  # 返回环境变量值或默认值。


def env_float(name: str, default: float) -> float:  # 定义读取浮点数环境变量的函数。
    try:  # 尝试把环境变量转换为浮点数。
        return float(env(name, str(default)))  # 返回转换成功后的浮点数。
    except ValueError:  # 捕获环境变量不是浮点数的情况。
        return default  # 返回默认浮点数。


def env_int(name: str, default: int) -> int:  # 定义读取整数环境变量的函数。
    try:  # 尝试把环境变量转换为整数。
        return int(env(name, str(default)))  # 返回转换成功后的整数。
    except ValueError:  # 捕获环境变量不是整数的情况。
        return default  # 返回默认整数。


def env_csv(name: str, default: Sequence[str]) -> List[str]:  # 定义读取逗号分隔环境变量的函数。
    raw = env(name, "")  # 读取原始环境变量字符串。
    if not raw:  # 判断环境变量是否为空。
        return list(default)  # 返回默认列表副本。
    return [item.strip() for item in raw.split(",") if item.strip()]  # 拆分逗号、清理空白并过滤空项。


def log(message: str) -> None:  # 定义统一日志输出函数。
    ensure_data_dir()  # 确保日志目录存在。
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {message}"  # 拼接带本地时间戳的日志行。
    try:  # 尝试向控制台输出日志。
        print(line, flush=True)  # 输出日志并立即刷新。
    except OSError:  # 捕获计划任务无控制台时的输出异常。
        pass  # 忽略控制台输出异常。
    with open(LOG_PATH, "a", encoding="utf-8") as file_obj:  # 以追加模式打开主日志文件。
        file_obj.write(line + "\n")  # 写入日志行和换行符。


def load_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:  # 定义读取 JSON 文件的函数。
    if not os.path.exists(path):  # 判断目标 JSON 文件是否存在。
        return default  # 文件不存在时返回默认对象。
    try:  # 尝试解析 JSON 文件。
        with open(path, "r", encoding="utf-8-sig") as file_obj:  # 兼容 UTF-8 BOM 打开 JSON 文件。
            data = json.load(file_obj)  # 解析 JSON 内容。
    except json.JSONDecodeError as exc:  # 捕获 JSON 损坏或空文件异常。
        log(f"[state] 状态文件解析失败 path={path} error={exc}")  # 记录状态文件解析失败原因。
        return default  # 状态损坏时回退默认对象。
    return data if isinstance(data, dict) else default  # 只接受字典状态，否则回退默认对象。


def save_json(path: str, data: Dict[str, Any]) -> None:  # 定义原子写入 JSON 文件的函数。
    os.makedirs(os.path.dirname(path), exist_ok=True)  # 确保目标文件父目录存在。
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S%f")
    temp_path = f"{path}.{os.getpid()}.{stamp}.tmp"  # 生成进程独立的临时文件路径。
    with open(temp_path, "w", encoding="utf-8") as file_obj:  # 用 UTF-8 打开临时文件。
        json.dump(data, file_obj, ensure_ascii=False, indent=2)  # 写入可读 JSON 内容。
    for attempt in range(5):
        try:
            os.replace(temp_path, path)  # 用原子替换方式更新目标文件。
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.2)


def append_csv(path: str, fieldnames: Sequence[str], row: Dict[str, Any]) -> None:  # 定义追加 CSV 行的函数。
    os.makedirs(os.path.dirname(path), exist_ok=True)  # 确保 CSV 父目录存在。
    exists = os.path.exists(path)  # 判断 CSV 文件是否已经存在。
    with open(path, "a", newline="", encoding="utf-8-sig") as file_obj:  # 用 utf-8-sig 追加打开 CSV，方便 Excel 识别中文。
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)  # 创建字典 CSV 写入器。
        if not exists:  # 判断是否需要写入表头。
            writer.writeheader()  # 新文件先写入 CSV 表头。
        writer.writerow(row)  # 写入当前记录行。


def public_attrs(obj: Any) -> Dict[str, Any]:  # 定义把 xtquant 对象转换为普通字典的函数。
    data: Dict[str, Any] = {}  # 初始化属性字典。
    if obj is None:  # 判断对象是否为空。
        return data  # 空对象返回空字典。
    for name in dir(obj):  # 遍历对象所有属性名。
        if name.startswith("_"):  # 跳过内部属性。
            continue  # 继续处理下一个属性。
        value = getattr(obj, name, None)  # 安全读取当前属性值。
        if callable(value):  # 跳过方法和其他可调用对象。
            continue  # 继续处理下一个属性。
        data[name] = value  # 保存普通属性值。
    return data  # 返回普通属性字典。


def default_state() -> Dict[str, Any]:  # 定义初始策略状态结构。
    return {"predictions": {}, "position_plans": {}, "orders": [], "daily_equity": {}, "last_summary": {}}  # 返回默认状态字典。


def load_state() -> Dict[str, Any]:  # 定义读取策略状态的函数。
    return load_json(STATE_PATH, default_state())  # 从状态文件读取，失败时使用默认状态。


def save_state(state: Dict[str, Any]) -> None:  # 定义保存策略状态的函数。
    save_json(STATE_PATH, state)  # 把状态写入固定 JSON 路径。


def today_yyyymmdd() -> str:  # 定义获取当天日期字符串的函数。
    return dt.date.today().strftime("%Y%m%d")  # 返回本地日期的 YYYYMMDD 字符串。


def parse_hms(value: str) -> dt.time:  # 定义解析 HH:MM:SS 时间字符串的函数。
    return dt.datetime.strptime(value, "%H:%M:%S").time()  # 把时间字符串转换为 time 对象。


def configured_holidays() -> Set[str]:  # 定义读取本地节假日配置的函数。
    override = set(env_csv("VIRTUAL_QMT_HOLIDAYS", []))  # 读取完全覆盖节假日列表。
    extra = set(env_csv("VIRTUAL_QMT_EXTRA_HOLIDAYS", []))  # 读取追加节假日列表。
    if override:  # 判断是否配置了覆盖列表。
        return override  # 有覆盖列表时只使用覆盖列表。
    return set(DEFAULT_HOLIDAYS_2026) | extra  # 返回内置节假日和追加节假日的并集。


def is_civil_workday_from_api(day: dt.date) -> Optional[bool]:  # 定义通过外部日历接口判断工作日的函数。
    if env("HOLIDAY_API_ENABLED", "1") not in ("1", "true", "TRUE", "yes", "YES"):  # 判断是否启用日历接口。
        return None  # 禁用接口时交给本地兜底逻辑。
    date_text = day.strftime("%Y-%m-%d")  # 生成接口常用日期格式。
    url_template = env("HOLIDAY_API_URL", "https://timor.tech/api/holiday/info/{date}")  # 读取日历接口 URL 模板。
    url = url_template.format(date=date_text, yyyymmdd=day.strftime("%Y%m%d"))  # 把日期变量填入 URL 模板。
    timeout = env_float("HOLIDAY_API_TIMEOUT", 3.0)  # 读取日历接口超时时间。
    try:  # 尝试调用日历接口。
        request = urllib.request.Request(url, headers={"User-Agent": "virtual-qmt-trader/1.0"})  # 构造 HTTP 请求对象。
        with urllib.request.urlopen(request, timeout=timeout) as response:  # 发起 HTTP 请求并等待响应。
            payload = json.loads(response.read().decode("utf-8"))  # 读取并解析 JSON 响应。
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:  # 捕获网络、URL 和 JSON 异常。
        log(f"[calendar] 日历接口失败 error={exc}")  # 记录日历接口失败原因。
        return None  # 接口失败时交给本地兜底逻辑。
    holiday = payload.get("holiday")  # 读取常见 holiday 字段。
    if isinstance(holiday, bool):  # 判断 holiday 是否为布尔值。
        return not holiday  # holiday 为真表示休息日，因此返回其反义。
    if isinstance(holiday, dict) and "holiday" in holiday:  # 兼容嵌套 holiday 对象。
        return not bool(holiday["holiday"])  # 返回嵌套 holiday 的反义。
    data_node = payload.get("data")  # 读取常见 data 节点。
    if isinstance(data_node, dict) and "holiday" in data_node:  # 判断 data 节点是否包含 holiday。
        return not bool(data_node["holiday"])  # 返回 data.holiday 的反义。
    type_node = payload.get("type")  # 读取 timor 接口常见 type 节点。
    if isinstance(type_node, dict) and "type" in type_node:  # 判断 type 节点是否包含类型编号。
        return int(type_node["type"]) == 0  # type 为 0 通常表示工作日。
    log(f"[calendar] 日历接口返回结构未知 payload={str(payload)[:200]}")  # 记录未知接口结构摘要。
    return None  # 未知结构交给本地兜底逻辑。


def is_trading_day(day: dt.date, use_api: bool = True) -> bool:  # 定义判断 A 股交易日的函数。
    cache_key = f"{day:%Y%m%d}:{int(use_api)}"  # 生成交易日缓存键。
    if cache_key in _TRADING_DAY_CACHE:  # 判断缓存是否已有结果。
        return _TRADING_DAY_CACHE[cache_key]  # 返回缓存结果。
    if day.weekday() >= 5:  # 判断是否周六或周日。
        _TRADING_DAY_CACHE[cache_key] = False  # 缓存周末非交易日结果。
        return False  # 周末直接视为非交易日。
    if use_api:  # 判断是否允许调用日历接口。
        civil_workday = is_civil_workday_from_api(day)  # 调用外部接口判断民用工作日。
        if civil_workday is not None:  # 判断接口是否给出明确结果。
            _TRADING_DAY_CACHE[cache_key] = civil_workday  # 缓存接口结果。
            return civil_workday  # 返回接口结果。
    result = day.strftime("%Y%m%d") not in configured_holidays()  # 用本地节假日表兜底判断。
    _TRADING_DAY_CACHE[cache_key] = result  # 缓存兜底判断结果。
    return result  # 返回最终交易日判断。


def next_trading_day(day: str) -> str:  # 定义查询下一交易日的函数。
    current = dt.datetime.strptime(day, "%Y%m%d").date() + dt.timedelta(days=1)  # 从传入日期的下一自然日开始。
    while not is_trading_day(current):  # 循环跳过非交易日。
        current += dt.timedelta(days=1)  # 向后移动一天。
    return current.strftime("%Y%m%d")  # 返回下一交易日字符串。


def local_listening_ports() -> Optional[Set[int]]:  # 定义读取本机 TCP 监听端口的函数。
    if os.name != "nt":  # 目前只在 Windows 上用 netstat 做无连接探测。
        return None
    try:
        output = subprocess.check_output(["netstat", "-ano", "-p", "tcp"], text=True, encoding="utf-8", errors="ignore", timeout=5)
    except Exception as exc:
        log(f"[connect] 读取本机监听端口失败 error={exc}")
        return None
    ports: Set[int] = set()
    for line in output.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        local_addr = parts[1]
        if local_addr.startswith("["):
            closing = local_addr.rfind("]:")
            host = local_addr[1:closing] if closing >= 0 else ""
            port_text = local_addr[closing + 2:] if closing >= 0 else ""
        else:
            host, _, port_text = local_addr.rpartition(":")
        if host not in ("0.0.0.0", "127.0.0.1", "::", "::1", "localhost"):
            continue
        try:
            ports.add(int(port_text))
        except ValueError:
            continue
    return ports


def xtdata_server_candidates() -> List[tuple]:
    candidates: List[tuple] = []
    try:
        from xtquant import xtconn

        for addr in xtconn.scan_available_server_addr():
            host, _, port_text = str(addr).rpartition(":")
            try:
                port = int(port_text)
            except ValueError:
                continue
            if port == 8086:
                continue
            candidates.append((host or "127.0.0.1", port))
    except Exception:
        pass
    for port in PORTS:
        if port == 8086:
            continue
        candidates.append(("127.0.0.1", int(port)))
    deduped: List[tuple] = []
    seen = set()
    for host, port in candidates:
        clean_host = "127.0.0.1" if host in ("", "0.0.0.0", "::", "::1", "localhost") else host
        key = (clean_host, int(port))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    listening = local_listening_ports()
    if listening is None:
        return deduped
    return [(host, port) for host, port in deduped if port in listening and port != 8086]


def connect_market_data() -> object:
    client = None
    candidates = xtdata_server_candidates()
    if not candidates:
        raise RuntimeError("cannot connect miniQMT data service: no local xtdata listener found; ensure miniQMT is fully started and logged in")
    errors: List[str] = []
    for host, port in candidates:
        if port == 8086:
            errors.append(f"{host}:{port} blocked")
            continue
        try:
            client = xtdata.connect(ip=host, port=port)
            break
        except Exception as exc:
            errors.append(f"{host}:{port} {exc}")
            client = None
    if client is None:
        detail = "; ".join(errors[-3:])
        raise RuntimeError(f"cannot connect miniQMT data service: {detail}")
    log(f"[connect] xtdata data_dir={xtdata.get_data_dir()}")
    return client


def get_stock_name(code: str) -> str:
    try:
        detail = xtdata.get_instrument_detail(code) or {}
        return str(detail.get("InstrumentName") or detail.get("ProductName") or "")
    except Exception:
        return ""


def is_st_stock(code: str) -> bool:
    name = get_stock_name(code).upper()
    return "ST" in name or "*ST" in name or "閫€" in name


def get_sector_codes(name: str) -> List[str]:
    try:
        return [code for code in xtdata.get_stock_list_in_sector(name) if code.endswith((".SH", ".SZ"))]
    except Exception:
        return []