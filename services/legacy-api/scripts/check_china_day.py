"""CHINA_DAY 模式自检：事件库命中 + 内容字段 + 图片渲染。"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.china_day import build_content, pick_event  # noqa: E402
from core.mode_catalog import BUILTIN_CATALOG  # noqa: E402

PROBE_DATES = [
    "2026-01-01",  # 元旦
    "2026-02-17",  # 春节（农历正月初一）
    "2026-02-16",  # 除夕
    "2026-03-03",  # 元宵（农历正月十五）
    "2026-04-05",  # 清明（节气）
    "2026-05-01",  # 劳动节
    "2026-06-19",  # 端午（农历五月初五）
    "2026-07-01",  # 建党节 / 香港回归（轮转）
    "2026-07-07",  # 七七事变
    "2026-09-03",  # 抗战胜利
    "2026-09-10",  # 教师节
    "2026-09-18",  # 九一八
    "2026-09-25",  # 中秋（农历八月十五）
    "2026-10-01",  # 国庆
    "2026-10-18",  # 重阳（农历九月初九）
    "2026-12-13",  # 国家公祭日
    "2026-12-20",  # 澳门回归
    "2026-08-02",  # 无事件日
]


def main() -> int:
    print("=== catalog core order ===")
    core = [c.mode_id for c in BUILTIN_CATALOG if c.category == "core"]
    print(core[:5], "...")
    assert core[0] == "CHINA_DAY", f"CHINA_DAY 不是核心模式第一个: {core[0]}"

    print("\n=== event hits ===")
    for ds in PROBE_DATES:
        now = datetime.strptime(ds, "%Y-%m-%d")
        ev = pick_event(now)
        c = build_content(now)
        title = ev.get("title") if ev else "-"
        print(
            f"{ds}  {c['date_label']:<16} hero={c['hero_text']:<8} "
            f"title={c['event_title']:<18} tag={c['tag']:<6} has={c['has_event']} | lib={title}"
        )
        print(f"           desc: {c['event_desc']}")
        print(f"           note: {c['significance']}  ({c['years_ago']})")

    print("\n=== render checks ===")
    import asyncio

    from core.mode_registry import get_registry
    from core.json_content import generate_json_mode_content
    from core.json_renderer import render_json_mode

    reg = get_registry()
    jm = reg.get_json_mode("CHINA_DAY")
    if jm is None:
        print("FAIL: CHINA_DAY 未加载进 registry")
        return 1
    print("registry ok:", jm.info.mode_id, jm.info.display_name, jm.info.source, jm.file_path)

    now = datetime(2026, 9, 18)
    content = asyncio.run(
        generate_json_mode_content(
            jm.definition,
            config={},
            date_ctx={"year": now.year, "month_cn": "九月", "day": now.day},
            language="zh",
        )
    )
    print("content:", {k: content.get(k) for k in ("date_label", "hero_text", "event_title", "significance")})

    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    os.makedirs(outdir, exist_ok=True)
    for (w, h, colors) in ((400, 300, 2), (648, 480, 4), (800, 480, 6), (296, 128, 2)):
        img = render_json_mode(
            jm.definition,
            content,
            date_str="9月18日 周五",
            weather_str="",
            battery_pct=88,
            screen_w=w,
            screen_h=h,
            colors=colors,
            language="zh",
        )
        path = os.path.join(outdir, f"china_day_{w}x{h}_{colors}c.png")
        img.save(path)
        print(f"saved {path} {img.size} {img.mode}")

    print("\nEXIT OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
