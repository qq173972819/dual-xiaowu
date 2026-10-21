"""本地离线渲染指定模式，用于配色/字体/版式评审（不依赖数据库与网络）。

用法::

    .venv/Scripts/python.exe scripts/preview_mode.py daily out/daily 800 480 4

参数：<mode_id 或 json 路径> <输出前缀> [宽] [高] [colors]
可选第 6 个参数是 JSON，用来覆盖模式里的 fallback 内容字段。

环境变量：
  PREVIEW_DEBUG_LAYOUT=1  叠加布局调试框（红=节点盒，黄=文字墨迹盒）
  PREVIEW_DUMP_TREE=1     打印每个节点的布局盒与测量尺寸
  PREVIEW_TODAY=2026-09-29 覆盖状态栏日期
"""
from __future__ import annotations

import json
import os
import sys

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from PIL import Image  # noqa: E402

from core.config import eink_palette  # noqa: E402
from core.json_renderer import render_json_mode  # noqa: E402
from core.renderer import image_to_raw_2bpp, image_to_raw_4bpp  # noqa: E402

MODE_DIR = os.path.join(BACKEND_ROOT, "core", "modes", "builtin")
DEFAULT_DATE = os.getenv("PREVIEW_TODAY", "2026-09-29 周二")


def collect_fallback(mode: dict) -> dict:
    """从模式定义里拼一份离线内容（computed fallback + llm fallback + 池子首项）。"""
    data: dict = {}
    content = mode.get("content", {}) or {}
    # 顶层就是单个 computed/llm（没有 steps 数组）的模式（如 lifebar），
    # 它的 fallback 挂在 content 自身上，漏掉这一步会让预览数值全变 0。
    if isinstance(content.get("fallback"), dict):
        data.update(content["fallback"])
    for step in content.get("steps", []) or []:
        if isinstance(step.get("fallback"), dict):
            data.update(step["fallback"])
        for key, spec in (step.get("output_schema") or {}).items():
            if isinstance(spec, dict) and "default" in spec:
                data.setdefault(key, spec["default"])
    pool = content.get("fallback_pool")
    if isinstance(pool, list) and pool and isinstance(pool[0], dict):
        for key, val in pool[0].items():
            data.setdefault(key, val)
    return data


def main(argv: list[str]) -> int:
    mode_name = argv[1] if len(argv) > 1 else "daily"
    out_prefix = argv[2] if len(argv) > 2 else "preview"
    width = int(argv[3]) if len(argv) > 3 else 800
    height = int(argv[4]) if len(argv) > 4 else 480
    colors = int(argv[5]) if len(argv) > 5 else 4

    path = mode_name if os.path.isfile(mode_name) else os.path.join(MODE_DIR, f"{mode_name}.json")
    with open(path, encoding="utf-8") as handle:
        mode = json.load(handle)

    data = collect_fallback(mode)
    if len(argv) > 6 and argv[6].strip():
        data.update(json.loads(argv[6]))

    if os.getenv("PREVIEW_DEBUG_LAYOUT"):
        mode.setdefault("layout", {}).setdefault("component_theme", {})["debug_overlay"] = True

    recorded: list = []
    if os.getenv("PREVIEW_DUMP_TREE"):
        import core.json_renderer as jr

        original_layout = jr._layout_component_node

        def spy_layout(node, x, y, w, h, theme, scale):  # noqa: ANN001
            original_layout(node, x, y, w, h, theme, scale)
            label = (
                node.props.get("field")
                or node.props.get("template")
                or node.props.get("fill")
                or ""
            )
            recorded.append(
                (node.kind, str(label)[:26], node.box, node.measured_width, node.measured_height)
            )

        jr._layout_component_node = spy_layout

    img = render_json_mode(
        mode,
        data,
        date_str=DEFAULT_DATE,
        weather_str="26\u00b0C",
        battery_pct=87,
        weather_code=0,
        time_str="\u4e0b\u5348 14:29",
        screen_w=width,
        screen_h=height,
        colors=colors,
    )

    if recorded:
        print("--- \u5e03\u5c40\u76d2 (x,y,w,h | measured w\u00d7h) ---")
        for kind, label, box, mw, mh in recorded:
            if box is not None:
                print(f"  {kind:12s} {label:28s} box=({box.x},{box.y},{box.width},{box.height})  m={mw}x{mh}")

    palette = eink_palette(colors)
    palette = palette + [0] * (768 - len(palette))
    preview = img.copy()
    preview.putpalette(palette)
    preview.save(f"{out_prefix}.png")

    counts = img.getcolors(4096) or []
    total = width * height
    print(f"image {width}x{height} mode={img.mode} colors={colors}")
    for n, idx in sorted(counts, reverse=True):
        if idx < len(palette) // 3:
            r, g, b = palette[idx * 3: idx * 3 + 3]
            print(f"  #{r:02X}{g:02X}{b:02X} idx={idx} {100.0 * n / total:6.2f}%")

    # 六色（colors>4）在设备上走 4bpp（每字节 2 像素），必须和真实下发路径一致，
    # 否则本地预览看不出「六色被 2bpp 截断成黑白」这类问题。
    bpp = 4 if colors > 4 else 2
    packed = image_to_raw_4bpp(img) if bpp == 4 else image_to_raw_2bpp(img)
    with open(f"{out_prefix}.{bpp}bpp", "wb") as handle:
        handle.write(packed)
    print(f"{bpp}bpp bytes: {len(packed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
