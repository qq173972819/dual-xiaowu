"""彩度闸门（Lab）与两段式抖动的回归测试。

背景：墨水屏的彩色端点（黄/红/蓝/绿）在 RGB 空间里离中灰比黑白还近，
只要把灰度像素混进彩色调色板做误差扩散，整屏就会被撒满彩点。
这些用例锁住"中性内容不上色、真彩色才上色"这条底线。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from core.image_processing import (  # noqa: E402
    _chroma_gate,
    _endpoint_luts,
    _panel_endpoints,
    _panel_lab_chroma,
    convert_image_block,
)


def _endpoint_of(rgb: tuple[int, int, int], colors: int = 6) -> int:
    """按固件同款路径取一个 RGB 像素被吸附到的端点索引。"""
    idx_lut, _ = _endpoint_luts(colors)
    h = Image.new("RGB", (1, 1), rgb).convert("HSV").getpixel((0, 0))[0]
    return idx_lut[h]


def _counts(img: Image.Image) -> dict[int, float]:
    px = list(img.getdata())
    total = len(px) or 1
    out: dict[int, float] = {}
    for p in px:
        idx = p & 0x0F
        out[idx] = out.get(idx, 0) + 1
    return {k: v * 100.0 / total for k, v in out.items()}


def _gray_gradient(size=(200, 150), warm: bool = False) -> Image.Image:
    w, h = size
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        v = int(50 + 200 * y / h)
        if warm:
            draw.line([(0, y), (w, y)], fill=(min(255, v + 8), v, max(0, v - 8)))
        else:
            draw.line([(0, y), (w, y)], fill=(v, v, v))
    return img


def test_panel_lab_chroma_ordering():
    """端点彩度必须按 红 > 黄 > 蓝 > 绿 排序（闸门阈值由它反算）。"""
    chroma = {i: _panel_lab_chroma(i) for i in (2, 3, 4, 5)}
    assert chroma[3] > chroma[2] > chroma[4] > chroma[5] > 0


def test_panel_endpoints_by_colors():
    assert _panel_endpoints(3) == (3,)
    assert _panel_endpoints(4) == (2, 3)
    assert _panel_endpoints(6) == (2, 3, 4, 5)


def test_neutral_gradient_has_no_color_on_4color():
    """纯灰阶（含轻度暖偏色）在四色屏上必须完全不上色。"""
    img = _gray_gradient(warm=True)
    out = convert_image_block(img, 200, 150, 4, enhance="auto", dither="atkinson")
    counts = _counts(out)
    colored = sum(v for k, v in counts.items() if k in (2, 3, 4, 5))
    assert colored < 0.5, counts


def test_neutral_gradient_has_no_color_on_6color():
    img = _gray_gradient(warm=True)
    out = convert_image_block(img, 200, 150, 6, enhance="auto", dither="atkinson")
    counts = _counts(out)
    colored = sum(v for k, v in counts.items() if k in (2, 3, 4, 5))
    assert colored < 0.5, counts


def test_photo_gate_keeps_subdued_sky_color_without_graphic_color_speckle():
    """六色照片放宽柔和蓝天的彩度门槛；普通图形仍走严格闸门。"""
    # Muted blue-grey sky: colorful enough to be real sky, but below the strict
    # graphic threshold. Keep it flat to detect Bayer pinholes reliably.
    sky = Image.new("RGB", (160, 100), (130, 160, 190))

    regular_mask, _ = _chroma_gate(sky, 6)
    photo_mask, _ = _chroma_gate(sky, 6, photo=True)
    regular_coverage = sum(regular_mask.convert("L").getdata())
    photo_coverage = sum(photo_mask.convert("L").getdata())
    assert photo_coverage > regular_coverage

    # The widened photo gate is contiguous; Bayer stipple must not punch holes
    # through a broad, smooth sky region.
    assert photo_mask.getbbox() == (0, 0, 160, 100)


def test_ink_wash_painting_stays_neutral_on_6color():
    """水墨画（灰阶为主，带一点墨色偏色）在六色屏上不能出现大片蓝/绿点。"""
    art = BACKEND_ROOT / "static" / "art" / "moyun.png"
    if not art.exists():  # 素材不入库时跳过
        return
    with Image.open(art) as src:
        out = convert_image_block(src, 400, 234, 6, fit="cover", enhance="auto", dither="atkinson")
    counts = _counts(out)
    blue_green = counts.get(4, 0) + counts.get(5, 0)
    assert blue_green < 1.0, counts


def test_saturated_subject_keeps_its_color():
    """真正鲜艳的内容必须保留颜色：纯红块走红、纯绿块（六色）走绿。"""
    red = Image.new("RGB", (80, 60), (200, 30, 20))
    out4 = convert_image_block(red, 80, 60, 4, enhance="auto", dither="atkinson")
    assert _counts(out4).get(3, 0) > 20.0

    green = Image.new("RGB", (80, 60), (20, 150, 60))
    out6 = convert_image_block(green, 80, 60, 6, enhance="auto", dither="atkinson")
    assert _counts(out6).get(5, 0) > 20.0


def test_4color_panel_drops_cold_colors():
    """四色面板没有冷色端点：纯蓝内容必须整片归灰，而不是被吸附成黄/红。"""
    blue = Image.new("RGB", (80, 60), (40, 90, 190))
    out = convert_image_block(blue, 80, 60, 4, enhance="auto", dither="atkinson")
    counts = _counts(out)
    colored = sum(v for k, v in counts.items() if k in (2, 3, 4, 5))
    assert colored < 0.5, counts


def test_gate_reports_endpoint_index_map():
    """闸门返回的端点索引图只含面板实际拥有的索引。"""
    img = Image.new("RGB", (32, 32), (200, 30, 20))
    mask, endpoints = _chroma_gate(img, 4)
    used = {p for p in endpoints.getdata()}
    assert used <= {2, 3}
    assert mask.getbbox() is not None


def test_6color_colored_pixels_never_borrow_other_hues():
    """六色：被吸附到绿色的像素，其所在区域不能出现蓝点（跨色相串扰）。"""
    img = Image.new("RGB", (96, 96), (30, 140, 70))
    out = convert_image_block(img, 96, 96, 6, enhance="auto", dither="atkinson")
    counts = _counts(out)
    assert counts.get(4, 0) < 1.0, counts
    assert counts.get(5, 0) > 20.0, counts


def test_6color_panel_keeps_muted_cool_colors():
    """佳显六色屏上的柔和蓝色不能因通用彩度下限而被整片压成黑白。"""
    muted_blue = Image.new("RGB", (100, 100), (130, 170, 205))
    out = convert_image_block(muted_blue, 100, 100, 6, enhance="auto", dither="atkinson")
    counts = _counts(out)
    assert counts.get(4, 0) > 5.0, counts
    assert counts.get(4, 0) > counts.get(5, 0), counts


def test_6color_panel_preserves_dark_saturated_blue_but_not_brown():
    """暗部蓝光按蓝墨表现，暗棕/中性仍留在黑白，避免整片被量成黑色。"""
    dark_blue = Image.new("RGB", (80, 80), (15, 70, 110))
    blue_out = convert_image_block(dark_blue, 80, 80, 6, enhance="auto", dither="atkinson")
    blue_counts = _counts(blue_out)
    assert blue_counts.get(4, 0) > 20.0, blue_counts

    dark_brown = Image.new("RGB", (80, 80), (70, 40, 30))
    brown_out = convert_image_block(dark_brown, 80, 80, 6, enhance="auto", dither="atkinson")
    brown_counts = _counts(brown_out)
    assert brown_counts.get(4, 0) + brown_counts.get(5, 0) < 1.0, brown_counts


def test_endpoint_assignment_follows_lab_not_rgb_hue():
    """端点归属按 Lab 彩度平面夹角判定，不按 RGB 色相。

    面板绿是偏青的深绿（H≈163°），草地/树叶却多在 H≈90~110°。纯按色相划分会把
    整片草地判进黄色端点（满屏黄草），所以这条用例把"草地归绿、新芽归黄"钉住。
    """
    green = 5  # 绿
    yellow = 2  # 黄
    blue = 4  # 蓝
    red = 3  # 红

    cases = {
        (110, 170, 225): blue,    # 天空
        (40, 70, 140): blue,      # 深蓝
        (60, 120, 45): green,     # 树叶
        (110, 180, 65): green,    # 草地（纯色相法会误判成黄）
        (150, 190, 60): yellow,   # 嫩芽黄绿
        (240, 200, 40): yellow,   # 向日葵
        (235, 140, 45): yellow,   # 橙
        (215, 45, 40): red,       # 红花
        (150, 75, 60): red,       # 砖红
        (35, 60, 40): green,      # 墨绿
    }
    for rgb, expect in cases.items():
        assert _endpoint_of(rgb) == expect, (rgb, _endpoint_of(rgb), expect)


def test_grass_field_renders_green_not_yellow():
    """整片草绿在六色屏上必须以绿为主，不能整片变黄。

    ⚠️ 断言上限受面板色域限制，不是"越纯越好"。草绿 (110,180,65) 的 G=180
    比面板黄 (176) 还亮、R=110 又只有面板黄能给出，而面板绿 R=0、G=124。
    解线性方程得到唯一可行配比 ≈ 绿 53% + 黄 40% + 白 7%（此时 R/G/B 为
    110/154/64，与原色最接近）；强行"纯绿"会把 R 拉到 0、画面偏青且整体压暗。
    所以这里只要求**绿占多数**，而不是要求绿是黄的 4 倍。
    """
    img = Image.new("RGB", (120, 90), (110, 180, 65))
    out = convert_image_block(img, 120, 90, 6, enhance="auto", dither="atkinson")
    counts = _counts(out)
    assert counts.get(5, 0) > counts.get(2, 0), counts
    assert counts.get(5, 0) > 25.0, counts
