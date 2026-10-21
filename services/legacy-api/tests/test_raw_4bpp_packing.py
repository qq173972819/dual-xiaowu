"""六色（4bpp）打包与调色板回归测试。

背景：7.3" Spectra 6 面板是 4bpp / 16 色索引、每字节 2 像素（高半字节在左），
而后端历史上只输出 2bpp（黑/白/黄/红四色），蓝(4) 与绿(5) 永远用不到。
这里锁定 4bpp 通道的字节布局，避免以后被改动破坏（固件侧
Epd7in3fPanel::PackPixelPair 依赖同一布局：高半字节 = 左像素）。
"""
from __future__ import annotations

from PIL import Image

from core.config import (
    EINK_4COLOR_PALETTE,
    EINK_6COLOR_PALETTE,
    eink_palette,
)
from core.renderer import image_to_raw_4bpp


def _palette_image(indices: list[int], size: tuple[int, int]) -> Image.Image:
    img = Image.new("P", size)
    pal = EINK_6COLOR_PALETTE + [0] * (768 - len(EINK_6COLOR_PALETTE))
    img.putpalette(pal)
    img.putdata(indices)
    return img


def test_six_color_palette_extends_four_color_prefix():
    """六色表的前 4 项必须与四色表逐字节一致，否则 2bpp 流的颜色含义会漂移。"""
    assert EINK_6COLOR_PALETTE[:12] == EINK_4COLOR_PALETTE[:12]


def test_eink_palette_selects_by_color_count():
    assert eink_palette(6) == EINK_6COLOR_PALETTE
    assert eink_palette(4) == EINK_4COLOR_PALETTE
    assert eink_palette(2) == EINK_4COLOR_PALETTE


def test_raw_4bpp_packs_two_pixels_per_byte_high_nibble_left():
    """0x2 0x3 -> 0x23；0x4 0x5 -> 0x45；0x0 0x1 -> 0x01。"""
    img = _palette_image([2, 3, 4, 5, 0, 1, 1, 0], (4, 2))
    packed = image_to_raw_4bpp(img)
    assert len(packed) == 4 * 2 // 2
    assert packed[0] == 0x23  # row 0: 黄, 红
    assert packed[1] == 0x45  # row 0: 蓝, 绿
    assert packed[2] == 0x01  # row 1: 黑, 白
    assert packed[3] == 0x10  # row 1: 白, 黑


def test_raw_4bpp_keeps_full_nibble_range():
    """蓝/绿索引 4/5 必须原样保留 —— 这正是 2bpp 表达不了的部分。"""
    img = _palette_image([4, 5], (2, 1))
    packed = image_to_raw_4bpp(img)
    assert packed == bytes([0x45])


def test_raw_4bpp_size_matches_panel_frame():
    """800x480 的 4bpp 帧必须正好 192000 字节（面板 kFrameBytes）。"""
    img = Image.new("P", (800, 480), 1)
    pal = EINK_6COLOR_PALETTE + [0] * (768 - len(EINK_6COLOR_PALETTE))
    img.putpalette(pal)
    assert len(image_to_raw_4bpp(img)) == 192000


def test_raw_4bpp_handles_odd_width():
    img = _palette_image([3, 3, 3], (3, 1))
    packed = image_to_raw_4bpp(img)
    assert packed[0] == 0x33
    assert packed[1] == 0x31  # 越界像素补白(1)
