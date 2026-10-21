"""ctypes bridge for native e-ink dithering."""
from __future__ import annotations

import ctypes
import logging
from pathlib import Path

from PIL import Image

from .config import EINK_4COLOR_PALETTE

import platform

logger = logging.getLogger(__name__)

_EXT = ".dll" if platform.system() == "Windows" else ".so"
_LIB_PATH = Path(__file__).resolve().parent / "native" / f"libeink_dither{_EXT}"
_LIB: ctypes.CDLL | None = None
_BUILD_HINT = "run `python3 backend/scripts/build_native_dither.py` from the repository root"

# Atkinson error-diffusion offsets (dx, dy); each carries 1/8 of the error.
_ATKINSON_STEPS = ((1, 0), (2, 0), (-1, 1), (0, 1), (1, 1), (0, 2))
_fallback_warned = False


def _clamp_byte(v: int) -> int:
    return 0 if v < 0 else (255 if v > 255 else v)


def _trunc_div8(v: int) -> int:
    """Integer v/8 truncated toward zero (mirrors the C++ double diffusion)."""
    return v // 8 if v >= 0 else -((-v) // 8)


def _py_atkinson_bw(gray: Image.Image) -> Image.Image:
    """Pure-Python Atkinson black/white dither; used only when the native lib is absent.

    没有原生库时这就是所有 1bpp 渲染的主路径（800×480 = 38 万像素），所以内层
    循环刻意把 clamp / 截断除法内联展开，不做任何函数调用：实测比朴素写法快
    约 40%。生产环境请务必构建原生库（``scripts/build_native_dither.py``）。
    """
    g = gray.convert("L")
    w, h = g.size
    data = bytearray(g.tobytes())
    out = bytearray(w * h)
    wm1 = w - 1
    hm1 = h - 1
    for y in range(h):
        row = y * w
        row1 = row + w
        row2 = row + 2 * w
        has_row1 = y < hm1
        has_row2 = y < hm1 - 1
        for x in range(w):
            pos = row + x
            old = data[pos]
            if old >= 128:
                out[pos] = 255
                err = old - 255
            else:
                out[pos] = 0
                err = old
            # 截断除法 v//8（向零取整）
            q = err >> 3 if err >= 0 else -((-err) >> 3)
            if not q:
                continue
            if x < wm1:
                p = pos + 1
                v = data[p] + q
                data[p] = 0 if v < 0 else (255 if v > 255 else v)
                if x < wm1 - 1:
                    p = pos + 2
                    v = data[p] + q
                    data[p] = 0 if v < 0 else (255 if v > 255 else v)
            if has_row1:
                if x > 0:
                    p = row1 + x - 1
                    v = data[p] + q
                    data[p] = 0 if v < 0 else (255 if v > 255 else v)
                p = row1 + x
                v = data[p] + q
                data[p] = 0 if v < 0 else (255 if v > 255 else v)
                if x < wm1:
                    p = row1 + x + 1
                    v = data[p] + q
                    data[p] = 0 if v < 0 else (255 if v > 255 else v)
            if has_row2:
                p = row2 + x
                v = data[p] + q
                data[p] = 0 if v < 0 else (255 if v > 255 else v)
    return Image.frombytes("L", (w, h), bytes(out)).convert("1", dither=Image.Dither.NONE)


def _py_atkinson_palette(rgb: Image.Image, colors: int) -> Image.Image:
    """Pure-Python Atkinson dither onto the 3/4-color e-ink palette; dev fallback only."""
    if colors not in (3, 4):
        raise ValueError("palette Atkinson dithering supports only 3 or 4 colors")
    src = rgb.convert("RGB")
    w, h = src.size
    data = bytearray(src.tobytes())
    out = bytearray(w * h)
    allowed = (0, 1, 3) if colors == 3 else (0, 1, 2, 3)
    pal = EINK_4COLOR_PALETTE
    for y in range(h):
        row = y * w
        for x in range(w):
            pos = row + x
            base = pos * 3
            r, g, b = data[base], data[base + 1], data[base + 2]
            best = allowed[0]
            best_dist = 1 << 30
            for idx in allowed:
                pb = idx * 3
                dr = r - pal[pb]
                dg = g - pal[pb + 1]
                db = b - pal[pb + 2]
                dist = dr * dr + dg * dg + db * db
                if dist < best_dist:
                    best_dist = dist
                    best = idx
            out[pos] = best
            pb = best * 3
            qr = _trunc_div8(r - pal[pb])
            qg = _trunc_div8(g - pal[pb + 1])
            qb = _trunc_div8(b - pal[pb + 2])
            if qr or qg or qb:
                for dx, dy in _ATKINSON_STEPS:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        nb = (ny * w + nx) * 3
                        data[nb] = _clamp_byte(data[nb] + qr)
                        data[nb + 1] = _clamp_byte(data[nb + 1] + qg)
                        data[nb + 2] = _clamp_byte(data[nb + 2] + qb)
    im = Image.frombytes("P", (w, h), bytes(out))
    im.putpalette(EINK_4COLOR_PALETTE + [0] * (768 - len(EINK_4COLOR_PALETTE)))
    return im


def _native_lib_or_none() -> ctypes.CDLL | None:
    """Return the loaded native library, or None when it has not been built."""
    global _LIB
    if _LIB is not None:
        return _LIB
    if not _LIB_PATH.exists():
        global _fallback_warned
        if not _fallback_warned:
            _fallback_warned = True
            logger.warning(
                "native dithering library missing at %s (%s); "
                "using the slower pure-Python fallback. Build the native lib for production.",
                _LIB_PATH, _BUILD_HINT,
            )
        return None
    return _load_lib()



def _load_lib() -> ctypes.CDLL:
    global _LIB
    if _LIB is not None:
        return _LIB
    if not _LIB_PATH.exists():
        raise RuntimeError(f"native dithering library not found at {_LIB_PATH}; {_BUILD_HINT}")
    try:
        lib = ctypes.CDLL(str(_LIB_PATH))
        lib.inksight_atkinson_bw.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        lib.inksight_atkinson_bw.restype = ctypes.c_int
        lib.inksight_atkinson_palette.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        lib.inksight_atkinson_palette.restype = ctypes.c_int
        _LIB = lib
        return lib
    except OSError as exc:
        raise RuntimeError(f"failed to load native dithering library at {_LIB_PATH}: {exc}") from exc

def atkinson_bw(gray: Image.Image) -> Image.Image:
    lib = _native_lib_or_none()
    if lib is None:
        return _py_atkinson_bw(gray)
    src = gray.convert("L")
    w, h = src.size
    in_buf = src.tobytes()
    out_buf = ctypes.create_string_buffer(w * h)
    err_buf = ctypes.create_string_buffer(256)
    status = lib.inksight_atkinson_bw(
        in_buf,
        w,
        h,
        out_buf,
        err_buf,
        len(err_buf),
    )
    if status != 0:
        raise RuntimeError(f"native black/white Atkinson dithering failed: {err_buf.value.decode('utf-8', errors='replace')}")
    return Image.frombytes("L", (w, h), out_buf.raw).convert("1", dither=Image.Dither.NONE)


def atkinson_palette(rgb: Image.Image, colors: int) -> Image.Image:
    lib = _native_lib_or_none()
    if lib is None:
        return _py_atkinson_palette(rgb, colors)
    if colors not in (3, 4):
        raise ValueError("native palette Atkinson dithering supports only 3 or 4 colors")
    src = rgb.convert("RGB")
    w, h = src.size
    in_buf = src.tobytes()
    out_buf = ctypes.create_string_buffer(w * h)
    err_buf = ctypes.create_string_buffer(256)
    status = lib.inksight_atkinson_palette(
        in_buf,
        w,
        h,
        int(colors),
        out_buf,
        err_buf,
        len(err_buf),
    )
    if status != 0:
        raise RuntimeError(f"native palette Atkinson dithering failed: {err_buf.value.decode('utf-8', errors='replace')}")
    out = Image.frombytes("P", (w, h), out_buf.raw)
    out.putpalette(EINK_4COLOR_PALETTE + [0] * (768 - len(EINK_4COLOR_PALETTE)))
    return out
