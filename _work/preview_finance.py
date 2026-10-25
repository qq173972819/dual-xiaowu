"""预览「大盘全景」(A屏黑白) 与「板块动向」(B屏四色) 两个新理财模式。
用代表性样例数据渲染，生成 PNG 供视觉核对。"""
import os
import sys
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from xiaowu_platform import finance  # noqa: E402

# ── 样例数据（覆盖涨/跌/缺失，验证墨水屏适配） ──
SAMPLE_MARKET = [
    ("A股", [
        {"name": "上证指数", "price": 3690.48, "pct": 0.89, "chg": 32.5},
        {"name": "深证成指", "price": 11673.83, "pct": 2.79, "chg": 316.7},
        {"name": "创业板指", "price": 2412.06, "pct": -1.12, "chg": -27.3},
        {"name": "沪深300", "price": 4321.55, "pct": 0.42, "chg": 18.1},
        {"name": "上证50", "price": 2912.34, "pct": -0.21, "chg": -6.1},
    ]),
    ("港股", [
        {"name": "恒生指数", "price": 20512.7, "pct": 1.35, "chg": 273.2},
        {"name": "恒生科技", "price": 4567.8, "pct": -0.88, "chg": -40.5},
    ]),
    ("美股", [
        {"name": "道琼斯", "price": 42156.3, "pct": 0.55, "chg": 230.1},
        {"name": "标普500", "price": 5631.2, "pct": -0.32, "chg": -18.0},
        {"name": "纳斯达克", "price": 17891.4, "pct": 0.78, "chg": 138.0},
    ]),
    ("亚太", [
        {"name": "日经225", "price": 38901.2, "pct": -1.45, "chg": -572.0},
    ]),
]

SAMPLE_SECTORS = {
    "industry": [
        {"name": "半导体", "pct": 3.42},
        {"name": "贵金属", "pct": 2.18},
        {"name": "证券", "pct": -1.05},
        {"name": "房地产", "pct": -2.36},
    ],
    "concept": [
        {"name": "AI芯片", "pct": 4.11},
        {"name": "新能源车", "pct": 1.27},
        {"name": "白酒", "pct": -0.94},
    ],
}

SAMPLE_COMMODITIES = [
    ("贵金属", [
        {"name": "沪金", "price": 589.32, "pct": 0.87, "chg": 5.1},
        {"name": "沪银", "price": 7654.0, "pct": -0.43, "chg": -33.0},
        {"name": "COMEX金", "price": 2634.5, "pct": 0.21, "chg": 5.5},
        {"name": "COMEX银", "price": 30.12, "pct": -0.66, "chg": -0.2},
    ]),
    ("石油", [
        {"name": "WTI原油", "price": 71.34, "pct": -1.12, "chg": -0.81},
        {"name": "布伦特原油", "price": 75.2, "pct": 0.45, "chg": 0.34},
    ]),
]


def main():
    W, H = 400, 300
    # A 屏：1-bit 黑白
    a = finance.render_market_board(SAMPLE_MARKET, W, H, title="大盘全景")
    a_path = os.path.join(ROOT, "_work", "preview_finance_a.png")
    a.save(a_path)
    print("A saved:", a_path, a.size, a.mode)

    # B 屏：BWRY 四色 (colors=4)
    b = finance.render_sectors(SAMPLE_SECTORS, SAMPLE_COMMODITIES, W, H, 4)
    b_path = os.path.join(ROOT, "_work", "preview_finance_b.png")
    if b.mode != "RGB" and b.mode != "P":
        b = b.convert("RGB")
    b.save(b_path)
    print("B saved:", b_path, b.size, b.mode)


if __name__ == "__main__":
    main()
