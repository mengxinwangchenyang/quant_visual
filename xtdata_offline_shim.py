# -*- coding: utf-8 -*-
"""离线 xtdata 替身：用 QMT 客户端内导出的日线(qmt_local/qmt_history_data.json)
充当 xtquant.xtdata，供可视化在本机(xtdata 离线)构建个股分钟图、查询名称与中证1000成分。

只实现回放路径用到的方法：
  - get_market_data_ex：日线直接返回；分钟(period="1m")由**当日日线合成**几条帧
    (09:31/10:00/14:53/14:55，high=当日最高、low=当日最低、close=当日收盘)，
    满足 historical_market(14:53 收≈日收) 与 first_target_hit/close_at(日高判止盈、
    日收作强平价)。这是**日级粒度近似**(已向用户披露)。
  - get_instrument_detail/get_instrumentdetail：返回导出的合约详情(名称/上市日/状态)。
  - get_stock_list_in_sector/get_index_weight：中证1000成分(读 qmt_csi1000.json)。
  - download_history_data/download_index_weight/connect/get_data_dir/get_full_tick：桩。
"""

import json
from pathlib import Path

import pandas as pd

from project_paths import PROJECT_ROOT

HISTORY_DATA = PROJECT_ROOT / "qmt_local" / "qmt_history_data.json"
MINUTE_DATA = PROJECT_ROOT / "qmt_local" / "qmt_minute_data.json"
CSI1000_FILE = PROJECT_ROOT / "qmt_local" / "qmt_csi1000.json"

# 由日线合成的当日分钟时间戳(HHMMSS)。close 各条相同=当日收盘，high/low=当日高/低，
# 故 first_target_hit(high>=target) 命中即记在最早的一条；这是刻意的日级近似。
_SYNTH_TIMES = ("093100", "100000", "145300", "145500")


class OfflineXtData(object):
    enable_hello = False

    def __init__(self, data_path=HISTORY_DATA, csi_path=CSI1000_FILE,
                 minute_path=MINUTE_DATA):
        doc = json.loads(Path(data_path).read_text(encoding="utf-8-sig"))
        self._bars = doc.get("bars", {}) or {}
        self._details = doc.get("details", {}) or {}
        # Real 1-minute OHLCV exported from the QMT client (optional). Shape:
        # {code: {stamp14: {open,high,low,close,volume}}}. When present for a
        # code, _frame serves these true intraday bars instead of the 4-point
        # daily synthesis, restoring real per-stock minute K-lines.
        self._minute = {}
        try:
            mdoc = json.loads(Path(minute_path).read_text(encoding="utf-8-sig"))
            self._minute = mdoc.get("minute", {}) or {}
        except Exception:
            self._minute = {}
        self.meta = {
            "start": doc.get("start"), "end": doc.get("end"),
            "n_codes": doc.get("n_codes"), "n_ok": doc.get("n_ok"),
            "codes_with_bars": len(self._bars),
            "codes_with_minute": len(self._minute),
        }
        try:
            csi = json.loads(Path(csi_path).read_text(encoding="utf-8-sig"))
            self._csi1000 = [str(c) for c in (csi.get("codes") or [])]
        except Exception:
            self._csi1000 = []

    # ---- 连接 / 下载：桩 ----
    def connect(self, *a, **k):
        return True

    def download_history_data(self, *a, **k):
        return None

    def download_index_weight(self, *a, **k):
        return None

    def get_data_dir(self):
        return str(HISTORY_DATA.parent)

    def get_full_tick(self, codes, *a, **k):
        return {}

    # ---- 行情 ----
    def get_market_data_ex(self, fields, codes, period="1d", start_time="",
                           end_time="", count=-1, dividend_type="none",
                           fill_data=False, **kw):
        out = {}
        for code in codes:
            out[code] = self._frame(code, period, start_time or "", end_time or "",
                                    fill_data)
        return out

    def _frame(self, code, period, start_time, end_time, fill_data=False):
        # Minute request: prefer real exported 1-minute bars whenever present.
        # Older historical dates still fall back to daily 4-point synthesis.
        if period != "1d":
            real = self._minute_frame(code, start_time, end_time)
            if real is not None:
                return real
        bars = self._bars.get(code) or {}
        if not bars:
            return pd.DataFrame()
        s8, e8 = start_time[:8], end_time[:8]
        index, rows = [], []
        for day_str in sorted(bars.keys()):
            if s8 and day_str < s8:
                continue
            if e8 and day_str > e8:
                continue
            day = bars[day_str]
            if period == "1d":
                index.append(day_str)
                rows.append(day)
            else:  # 分钟：由日线合成。
                for hhmmss in _SYNTH_TIMES:
                    ts = day_str + hhmmss
                    if len(start_time) >= 14 and ts < start_time:
                        continue
                    if len(end_time) >= 14 and ts > end_time:
                        continue
                    index.append(ts)
                    row = dict(day)
                    row["_real_minute"] = False
                    rows.append(row)
        if not index:
            return pd.DataFrame()
        return pd.DataFrame(rows, index=index)

    def _minute_frame(self, code, start_time, end_time):
        # Return real 1-minute bars for code within [start,end], or None when no
        # real minute data exists for it (caller falls back to daily synthesis).
        mins = self._minute.get(code)
        if not mins:
            return None
        all_ts = sorted(str(ts)[:14] for ts in mins.keys())
        if not all_ts:
            return None
        req_start = start_time[:14] if len(start_time) >= 14 else ""
        req_end = end_time[:14] if len(end_time) >= 14 else ""
        if req_end and req_end < all_ts[0]:
            return None
        if req_start and req_start > all_ts[-1]:
            return None
        index, rows = [], []
        for ts in sorted(mins.keys()):
            ts14 = str(ts)[:14]
            if len(start_time) >= 14 and ts14 < start_time[:14]:
                continue
            if len(end_time) >= 14 and ts14 > end_time[:14]:
                continue
            row = mins[ts]
            close = row.get("close")
            if close is None or float(close) <= 0:
                continue
            index.append(ts14)
            out_row = dict(row)
            out_row["_real_minute"] = True
            rows.append(out_row)
        if not index:
            # code has an entry but nothing in range -> still return empty so we
            # do NOT fall back to the misleading 4-point synthesis for it.
            return pd.DataFrame()
        return pd.DataFrame(rows, index=index)

    # ---- 合约详情 ----
    def get_instrument_detail(self, code, *a, **k):
        det = self._details.get(code)
        return dict(det) if isinstance(det, dict) else {}

    def get_instrumentdetail(self, code, *a, **k):
        return self.get_instrument_detail(code)

    # ---- 板块 / 指数成分 ----
    def get_stock_list_in_sector(self, name, *a, **k):
        n = str(name)
        if "852" in n or "1000" in n:
            return list(self._csi1000)
        return []

    def get_index_weight(self, code, *a, **k):
        if "852" in str(code):
            return dict((c, 0.0) for c in self._csi1000)
        return {}
