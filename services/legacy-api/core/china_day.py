"""
「历史上的今天 · 中国」—— 本地日期事件解析器。

数据来源：core/data/china_day_events.json
- date_type = "solar"      : 公历节日 / 纪念日，date = "MM-DD"
- date_type = "lunar"      : 农历节日，date = "MM-DD"（可带 date_alt 处理腊月廿九/三十）
- date_type = "solar_term" : 二十四节气，date = 节气名（如「清明」「冬至」）

desc 支持模板：
    {{current_year}}          -> 2026
    {{current_year - 1931}}   -> 95
    {{current_year + 278}}    -> 2304

同一天命中多条时，取 weight 最高的一组；组内按日期做确定性轮转，
保证同一天每年看到的内容不完全重复。
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "china_day_events.json")

_CACHE: dict[str, Any] = {"mtime": None, "events": []}

_LUNAR_MONTH_NAMES = ["正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊"]
_LUNAR_DAY_NAMES = [
    "", "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
]

_TEMPLATE_RE = re.compile(r"\{\{\s*current_year\s*(?:(?P<op>[-+])\s*(?P<num>\d+))?\s*\}\}")


def _load_events() -> list[dict]:
    """Load event list, cached by file mtime so edits take effect on next render."""
    try:
        mtime = os.path.getmtime(DATA_PATH)
    except OSError:
        logger.warning("[ChinaDay] event data missing: %s", DATA_PATH)
        return []
    if _CACHE["mtime"] == mtime and _CACHE["events"]:
        return _CACHE["events"]
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("[ChinaDay] failed to load event data: %s", exc)
        return []
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        logger.error("[ChinaDay] event data has no `events` list")
        return []
    clean = [e for e in events if isinstance(e, dict) and e.get("date_type") and e.get("date")]
    _CACHE["mtime"] = mtime
    _CACHE["events"] = clean
    logger.info("[ChinaDay] loaded %d events", len(clean))
    return clean


def _split_md(value: str) -> tuple[int, int] | None:
    text = str(value or "").strip()
    parts = text.replace("/", "-").replace(".", "-").split("-")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None


def _lunar_has_day(lunar_year: int, month: int, day: int) -> bool:
    """Whether a given lunar month actually contains `day` (handles 廿九/三十)."""
    try:
        from zhdate import ZhDate

        ZhDate(lunar_year, month, day)
        return True
    except Exception:
        return False


def _today_lunar(now: datetime) -> tuple[int, int, int]:
    """Return (lunar_year, lunar_month, lunar_day) for a solar datetime."""
    try:
        from zhdate import ZhDate

        zh = ZhDate.from_datetime(now)
        return int(zh.lunar_year), int(abs(zh.lunar_month)), int(zh.lunar_day)
    except Exception as exc:
        logger.warning("[ChinaDay] lunar conversion failed: %s", exc)
        return now.year, now.month, now.day


def _today_solar_term(now: datetime) -> str:
    try:
        from .config import SOLAR_TERMS

        return str(SOLAR_TERMS.get((now.year, now.month, now.day), "") or "")
    except Exception:
        return ""


def _lunar_date_label(lunar_month: int, lunar_day: int) -> str:
    m = _LUNAR_MONTH_NAMES[lunar_month - 1] if 1 <= lunar_month <= 12 else str(lunar_month)
    d = _LUNAR_DAY_NAMES[lunar_day] if 0 <= lunar_day < len(_LUNAR_DAY_NAMES) else str(lunar_day)
    return f"农历{m}月{d}"


def _match_candidates(now: datetime) -> list[dict]:
    solar_md = (now.month, now.day)
    lunar_year, lunar_month, lunar_day = _today_lunar(now)
    term = _today_solar_term(now)

    hits: list[dict] = []
    for ev in _load_events():
        dtype = str(ev.get("date_type", "")).strip().lower()
        date = str(ev.get("date", ""))
        if dtype == "solar":
            md = _split_md(date)
            if md == solar_md:
                hits.append(ev)
        elif dtype == "lunar":
            md = _split_md(date)
            if md == (lunar_month, lunar_day):
                hits.append(ev)
                continue
            # date_alt: 仅在主日期所在的农历月不存在那天时才生效（如腊月廿九/三十）
            alt = _split_md(str(ev.get("date_alt") or ""))
            if alt == (lunar_month, lunar_day) and md is not None:
                if not _lunar_has_day(lunar_year, md[0], md[1]):
                    hits.append(ev)
        elif dtype == "solar_term":
            if term and str(date).strip() == term:
                hits.append(ev)
    return hits


def _resolve_templates(text: str, current_year: int) -> str:
    def _sub(match: re.Match) -> str:
        op = match.group("op")
        num = match.group("num")
        if not op or num is None:
            return str(current_year)
        value = current_year - int(num) if op == "-" else current_year + int(num)
        return str(value)

    return _TEMPLATE_RE.sub(_sub, str(text or ""))


def _years_ago_text(event: dict, current_year: int) -> str:
    since = event.get("since")
    try:
        since_int = int(since)
    except (TypeError, ValueError):
        return str(event.get("tag") or "纪念日")
    if since_int < 0:
        return f"公元前{-since_int}年"
    diff = current_year - since_int
    if diff <= 0:
        return str(event.get("tag") or "纪念日")
    return f"{diff}年前"


def _hero_text(event: dict, current_year: int) -> str:
    hero = str(event.get("hero") or "").strip()
    if hero:
        return hero
    since = event.get("since")
    try:
        since_int = int(since)
    except (TypeError, ValueError):
        return str(event.get("title") or "")
    diff = current_year - since_int
    if since_int > 0 and diff > 0:
        return f"{diff}周年"
    return str(event.get("title") or "")


def pick_event(now: datetime | None = None) -> dict | None:
    """Pick the event to display for a given day (defaults to today)."""
    now = now or datetime.now()
    hits = _match_candidates(now)
    if not hits:
        return None
    hits.sort(key=lambda e: (-int(e.get("weight") or 0), str(e.get("title") or "")))
    top_weight = int(hits[0].get("weight") or 0)
    top_group = [e for e in hits if int(e.get("weight") or 0) == top_weight]
    # 同权重轮转：不同年份的同一天会看到不同的条目（如 7月1日 建党节 / 香港回归）
    idx = (now.toordinal()) % len(top_group)
    return top_group[idx]


def build_content(now: datetime | None = None, *, language: str = "zh", fallback: dict | None = None) -> dict:
    """Build the content payload consumed by the CHINA_DAY layout."""
    now = now or datetime.now()
    current_year = now.year
    solar_md = (now.month, now.day)
    lunar_year, lunar_month, lunar_day = _today_lunar(now)
    term = _today_solar_term(now)

    result = dict(fallback or {})

    event = pick_event(now)
    if event is None:
        result.update(
            {
                "date_label": f"{now.month}月{now.day}日",
                "hero_text": "今日",
                "event_title": "历史上的今天",
                "event_desc": f"{now.month}月{now.day}日，平凡的一天，也值得认真过。",
                "significance": "每一个今天，都是未来的历史",
                "years_ago": "",
                "tag": "日常",
                "date_type": "solar",
                "has_event": False,
            }
        )
        return result

    dtype = str(event.get("date_type", "")).strip().lower()
    if dtype == "lunar":
        date_label = _lunar_date_label(lunar_month, lunar_day)
    elif dtype == "solar_term":
        date_label = f"{term} · {now.month}月{now.day}日" if term else f"{now.month}月{now.day}日"
    else:
        date_label = f"{solar_md[0]}月{solar_md[1]}日"

    result.update(
        {
            "date_label": date_label,
            "hero_text": _hero_text(event, current_year),
            "event_title": str(event.get("title") or ""),
            "event_desc": _resolve_templates(str(event.get("desc") or ""), current_year),
            "significance": str(event.get("note") or event.get("tag") or ""),
            "years_ago": _years_ago_text(event, current_year),
            "tag": str(event.get("tag") or ""),
            "date_type": dtype,
            "lunar_date": _lunar_date_label(lunar_month, lunar_day),
            "solar_term": term,
            "has_event": True,
        }
    )
    return result
