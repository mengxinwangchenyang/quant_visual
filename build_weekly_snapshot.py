# -*- coding: utf-8 -*-
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


VISUAL_ROOT = Path(__file__).resolve().parent
BUY_PATH = VISUAL_ROOT / "daily_buys_snapshot.json"
SELL_PATH = VISUAL_ROOT / "daily_sells_snapshot.json"
OUTPUT_PATH = VISUAL_ROOT / "weekly_snapshot.json"


def load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def week_bounds(value: str) -> Tuple[str, str]:
    day = dt.datetime.strptime(value, "%Y%m%d").date()
    monday = day - dt.timedelta(days=day.weekday())
    friday = monday + dt.timedelta(days=4)
    return monday.strftime("%Y%m%d"), friday.strftime("%Y%m%d")


def completed_week_keys(dates: Iterable[str], as_of: dt.date) -> List[str]:
    keys = set()
    for value in dates:
        if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
            continue
        _monday, friday = week_bounds(value)
        if dt.datetime.strptime(friday, "%Y%m%d").date() <= as_of:
            keys.add(friday)
    return sorted(keys, reverse=True)


def build_week(friday: str, buy_days: List[Dict[str, Any]], sell_days: List[Dict[str, Any]]) -> Dict[str, Any]:
    monday, _ = week_bounds(friday)
    selected_buys = sorted(
        [day for day in buy_days if monday <= str(day.get("date") or "") <= friday],
        key=lambda row: str(row.get("date") or ""),
    )
    selected_sells = sorted(
        [day for day in sell_days if monday <= str(day.get("date") or "") <= friday],
        key=lambda row: str(row.get("date") or ""),
    )
    available_dates = sorted({str(day.get("date") or "") for day in selected_buys + selected_sells})
    return {
        "week": friday,
        "start_date": monday,
        "end_date": friday,
        "available_start_date": available_dates[0] if available_dates else monday,
        "available_end_date": available_dates[-1] if available_dates else friday,
        "buy_days": selected_buys,
        "sell_days": selected_sells,
    }


def build_snapshot(
    buy_snapshot: Dict[str, Any],
    sell_snapshot: Dict[str, Any],
    existing: Dict[str, Any],
    as_of: dt.date,
) -> Dict[str, Any]:
    buy_days = [row for row in buy_snapshot.get("days", []) if isinstance(row, dict)]
    sell_days = [row for row in sell_snapshot.get("days", []) if isinstance(row, dict)]
    dates = [str(row.get("date") or "") for row in buy_days + sell_days]
    completed = completed_week_keys(dates, as_of)
    weeks = {
        str(row.get("week") or ""): row
        for row in existing.get("weeks", [])
        if isinstance(row, dict) and len(str(row.get("week") or "")) == 8
    }
    for key in completed:
        weeks[key] = build_week(key, buy_days, sell_days)
    ordered = [weeks[key] for key in sorted(weeks, reverse=True)]
    return {
        "schema_version": 1,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "schedule": "每周五15:30随日终快照自动生成",
        "latest_week": ordered[0]["week"] if ordered else "",
        "source_generated_at": {
            "buys": str(buy_snapshot.get("generated_at") or ""),
            "sells": str(sell_snapshot.get("generated_at") or ""),
        },
        "weeks": ordered,
    }


def write_json(payload: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(str(temporary), str(path))


def main() -> int:
    parser = argparse.ArgumentParser(description="生成并保留每周五七策略周快照")
    parser.add_argument("--buys", default=str(BUY_PATH))
    parser.add_argument("--sells", default=str(SELL_PATH))
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    parser.add_argument("--as-of", default=dt.date.today().strftime("%Y%m%d"))
    args = parser.parse_args()
    buy_snapshot = load_json(Path(args.buys).resolve())
    sell_snapshot = load_json(Path(args.sells).resolve())
    if not buy_snapshot or not sell_snapshot:
        raise RuntimeError("每日买入或卖出快照不存在")
    output = Path(args.output).resolve()
    snapshot = build_snapshot(
        buy_snapshot,
        sell_snapshot,
        load_json(output),
        dt.datetime.strptime(args.as_of, "%Y%m%d").date(),
    )
    write_json(snapshot, output)
    print(f"weekly snapshot updated: {output} weeks={len(snapshot['weeks'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
