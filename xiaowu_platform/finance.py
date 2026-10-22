"""理财场景：自选行情 + K 线渲染（原型实现）。

数据源：腾讯公开行情接口（主源，qt.gtimg.cn / web.ifzq.gtimg.cn），
东方财富公开接口（备源，主源不可用时自动切换）。
均为非官方授权接口，仅供原型/内测。商用化阶段须替换为持牌行情数据商
（同花顺 iFinD B 端 / 融聚汇 / Tushare Pro 等），并完成交易所行情信息分发授权。

能力：
- resolve_secid：证券代码 → 市场 secid（沪 6/5/9→1.，深 0/3 与北交 8/4→0.）
- fetch_quotes：批量实时快照（名称 / 现价 / 涨跌幅 / 涨跌额），60s 内存缓存
- fetch_kline：日 K（前复权，不足时回落不复权）
- render_finance_list：A 屏 1-bit 自选列表 / 市场摘要
- render_kline_image：B 屏日 K 蜡烛图（四色调色板：红涨、黑跌；黑白屏空心/实心）
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Optional

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

_QQ_QUOTE_URL = "https://qt.gtimg.cn/q="
_QQ_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_EM_QUOTE_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://gu.qq.com/",
    "Accept": "application/json, text/plain, */*",
}
_TIMEOUT = 4.0
_RETRIES = 2
_CACHE_TTL = 60.0
_MAX_CACHE = 256
_cache: dict[str, tuple[float, Any]] = {}

# 直连 opener：绕过 http_proxy/https_proxy 等环境代理，
# 避免本机代理（Clash 等）劫持或断连公开行情接口。
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 市场摘要模式默认展示的三大指数
INDEX_SUMMARY_CODES = ["sh000001", "sz399001", "sz399006"]


def resolve_secid(code: str) -> str:
    """600519 / sh600519 / 1.600519 → 东财 secid。"""
    raw = str(code).strip().lower()
    if raw[:2] in ("1.", "0."):
        raw = raw[2:]
    m = re.match(r"^(sh|sz|bj)?(\d{6})$", raw)
    if not m:
        raise ValueError(f"无效的证券代码：{code}")
    prefix, num = m.group(1), m.group(2)
    if prefix == "sh":
        market = 1
    elif prefix in ("sz", "bj"):
        market = 0
    else:
        market = 1 if num[0] in ("6", "5", "9") else 0
    return f"{market}.{num}"


def _get_json(url: str) -> dict[str, Any]:
    now = time.time()
    hit = _cache.get(url)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    req = urllib.request.Request(url, headers=_HEADERS)
    data: dict[str, Any] | None = None
    last_exc: Exception | None = None
    for attempt in range(_RETRIES + 1):
        try:
            with _opener.open(req, timeout=_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < _RETRIES:
                time.sleep(0.4 * (attempt + 1))
    if data is None:
        raise last_exc if last_exc else RuntimeError("request failed")
    if len(_cache) >= _MAX_CACHE:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
    _cache[url] = (now, data)
    return data


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) else None


def _symbol(code: str) -> str:
    """600519 → sh600519（腾讯符号）。北交所代码腾讯接口不支持，走东财备源。"""
    secid = resolve_secid(code)
    return ("sh" if secid[0] == "1" else "sz") + secid[2:]


def fetch_quotes(codes: list[str]) -> list[dict[str, Any]]:
    """批量实时快照。停牌/无效代码的数值字段为 None。"""
    if not codes:
        return []
    try:
        return _quotes_tencent(codes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[FINANCE] tencent quote failed, fallback to eastmoney: %s", exc)
    try:
        return _quotes_eastmoney(codes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[FINANCE] eastmoney quote failed: %s", exc)
        return []


def _quotes_tencent(codes: list[str]) -> list[dict[str, Any]]:
    url = _QQ_QUOTE_URL + ",".join(_symbol(c) for c in codes)
    req = urllib.request.Request(url, headers=_HEADERS)
    with _opener.open(req, timeout=_TIMEOUT) as resp:
        text = resp.read().decode("gbk", errors="replace")
    by_symbol: dict[str, dict[str, Any]] = {}
    for m in re.finditer(r'v_(sh|sz)(\d{6})="([^"]*)"', text):
        parts = m.group(3).split("~")
        if len(parts) < 33:
            continue
        by_symbol[m.group(1) + m.group(2)] = {
            "code": parts[2],
            "name": parts[1],
            "price": _num(float(parts[3])) if parts[3] else None,
            "chg": _num(float(parts[31])) if parts[31] else None,
            "pct": _num(float(parts[32])) if parts[32] else None,
            "market": 1 if m.group(1) == "sh" else 0,
        }
    return [by_symbol.get(_symbol(c), {"code": c[-6:], "name": "", "price": None, "pct": None, "chg": None, "market": 0})
            for c in codes]


def _quotes_eastmoney(codes: list[str]) -> list[dict[str, Any]]:
    secids = [resolve_secid(c) for c in codes]
    url = f"{_EM_QUOTE_URL}?fltt=2&invt=2&fields=f2,f3,f4,f12,f13,f14&secids={','.join(secids)}"
    data = _get_json(url)
    diff = ((data or {}).get("data") or {}).get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    return [
        {
            "code": str(row.get("f12") or ""),
            "name": str(row.get("f14") or ""),
            "price": _num(row.get("f2")),
            "pct": _num(row.get("f3")),
            "chg": _num(row.get("f4")),
            "market": int(row.get("f13") or 0),
        }
        for row in diff
    ]


def fetch_kline(code: str, days: int = 60) -> dict[str, Any]:
    """日 K（前复权）。返回 {code, name, items: [{date, open, close, high, low}]}。"""
    try:
        return _kline_tencent(code, days)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[FINANCE] tencent kline failed, fallback to eastmoney: %s", exc)
    return _kline_eastmoney(code, days)


def _kline_tencent(code: str, days: int) -> dict[str, Any]:
    sym = _symbol(code)
    days = max(10, min(int(days), 250))
    url = f"{_QQ_KLINE_URL}?param={sym},day,,,{days},qfq"
    data = _get_json(url).get("data") or {}
    node = data.get(sym) or {}
    rows = node.get("qfqday") or node.get("day") or []
    items = []
    for row in rows:
        if len(row) < 5:
            continue
        try:
            items.append({
                "date": str(row[0]),
                "open": float(row[1]),
                "close": float(row[2]),
                "high": float(row[3]),
                "low": float(row[4]),
            })
        except (TypeError, ValueError):
            continue
    qt = ((node.get("qt") or {}).get(sym) or [])
    name = qt[1] if len(qt) > 1 else ""
    return {"code": code[-6:], "name": str(name), "items": items}


def _kline_eastmoney(code: str, days: int) -> dict[str, Any]:
    secid = resolve_secid(code)
    days = max(10, min(int(days), 250))
    url = (f"{_EM_KLINE_URL}?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
           f"&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=1&end=20500101&lmt={days}")
    try:
        data = (_get_json(url) or {}).get("data") or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[FINANCE] eastmoney kline fetch failed: %s", exc)
        data = {}
    items: list[dict[str, Any]] = []
    for line in data.get("klines") or []:
        parts = str(line).split(",")
        if len(parts) < 5:
            continue
        try:
            items.append({
                "date": parts[0],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
            })
        except ValueError:
            continue
    return {"code": str(data.get("code") or code), "name": str(data.get("name") or ""), "items": items}


def _cjk(size: int):
    from .render import _load_cjk
    return _load_cjk(size)


def _load_cjk_bold(size: int):
    from .render import _load_cjk
    return _load_cjk(size, bold=True)


def _palette(w: int, h: int, colors: int) -> Image.Image:
    from .render import _palette_p_image
    return _palette_p_image(w, h, colors)


def _fmt(value: Optional[float], digits: int = 2, na: str = "--") -> str:
    return f"{value:.{digits}f}" if isinstance(value, float) else na


def _pct_text(pct: Optional[float]) -> str:
    if not isinstance(pct, float):
        return "--"
    arrow = "▲" if pct >= 0 else "▼"
    return f"{arrow}{abs(pct):.2f}%"


# ── A 屏：自选列表 / 市场摘要（1-bit 黑白） ─────────────────────
def render_finance_list(quotes: list[dict[str, Any]], w: int, h: int,
                        title: str = "自选行情", footer: str = "") -> Image.Image:
    img = Image.new("1", (w, h), 1)
    draw = ImageDraw.Draw(img)
    s = w / 400.0
    pad = int(12 * s)
    f_title = _cjk(max(14, int(17 * s)))
    f_meta = _cjk(max(10, int(11 * s)))
    f_name = _cjk(max(11, int(13 * s)))
    f_price = _cjk(max(11, int(13 * s)))

    today = datetime.now().strftime("%m-%d %H:%M")
    draw.text((pad, int(8 * s)), title, fill=0, font=f_title)
    tw = draw.textlength(today, font=f_meta)
    draw.text((w - pad - tw, int(12 * s)), today, fill=0, font=f_meta)
    draw.line([(pad, int(32 * s)), (w - pad, int(32 * s))], fill=0, width=max(1, int(s)))

    rows = quotes[:8]
    top, bottom = int(38 * s), h - int(18 * s)
    if not rows:
        draw.text((pad, (top + bottom) // 2 - int(8 * s)), "暂无自选，请在后台「理财自选」添加",
                  fill=0, font=f_name)
        return img
    row_h = (bottom - top) // len(rows)
    # 行数少时不许行高无限撑满（如三大指数只有 3 行），保持紧凑列表观感
    row_h = min(row_h, int(34 * s))
    for i, q in enumerate(rows):
        y = top + i * row_h
        name = str(q.get("name") or q.get("code") or "--")
        draw.text((pad, y + row_h // 2 - int(9 * s)), name, fill=0, font=f_name)
        price = _fmt(q.get("price"))
        pw = draw.textlength(price, font=f_price)
        draw.text((int(w * 0.62) - pw, y + row_h // 2 - int(9 * s)), price, fill=0, font=f_price)
        pct = _pct_text(q.get("pct"))
        cw = draw.textlength(pct, font=f_price)
        draw.text((w - pad - cw, y + row_h // 2 - int(9 * s)), pct, fill=0, font=f_price)
        if i:
            draw.line([(pad, y), (w - pad, y)], fill=0, width=1)
    foot = footer or "行情快照 · 腾讯公开接口（原型）"
    fw = draw.textlength(foot, font=f_meta)
    draw.text((w - pad - fw, h - int(14 * s)), foot, fill=0, font=f_meta)
    return img


# ── B 屏：日 K 蜡烛图（调色板 / 黑白自适应） ───────────────────
def render_kline_image(kline: dict[str, Any], w: int, h: int, colors: int) -> Image.Image:
    items = kline.get("items") or []
    name = kline.get("name") or kline.get("code") or "--"
    colored = colors >= 3
    img = _palette(w, h, colors) if colored else Image.new("1", (w, h), 1)
    draw = ImageDraw.Draw(img)
    s = w / 400.0
    pad = int(10 * s)
    f_title = _load_cjk_bold(max(13, int(15 * s)))
    f_meta = _cjk(max(9, int(10 * s)))

    if len(items) < 2:
        draw.text((pad, int(40 * s)), "K 线数据获取失败，稍后自动重试", fill=0, font=f_title)
        return img

    # 标题：名称 + 最新价 + 最新涨跌幅
    last, prev = items[-1], items[-2]
    pct = (last["close"] - prev["close"]) / prev["close"] * 100 if prev["close"] else 0.0
    draw.text((pad, int(8 * s)), f"{name}  {last['close']:.2f}  {_pct_text(pct)}", fill=0, font=f_title)
    days_label = f"日K · {items[0]['date'][5:]} ~ {last['date'][5:]}"
    dw = draw.textlength(days_label, font=f_meta)
    draw.text((w - pad - dw, int(12 * s)), days_label, fill=0, font=f_meta)

    left = int(44 * s)          # 左侧留给价格刻度
    right, top, bottom = w - pad, int(34 * s), h - int(18 * s)
    lo = min(x["low"] for x in items)
    hi = max(x["high"] for x in items)
    if hi <= lo:
        hi = lo + 1.0
    span = hi - lo
    lo -= span * 0.05
    hi += span * 0.05

    def py(price: float) -> int:
        return int(bottom - (price - lo) / (hi - lo) * (bottom - top))

    # 横向网格 + 价格刻度（4 档）
    f_axis = _cjk(max(8, int(9 * s)))
    for i in range(4):
        price = lo + (hi - lo) * (i + 0.5) / 4
        y = py(price)
        draw.line([(left, y), (right, y)], fill=0, width=1)
        label = f"{price:.2f}"
        draw.text((pad, y - int(5 * s)), label, fill=0, font=f_axis)

    # 蜡烛
    chart_w = right - left
    slot = chart_w / len(items)
    body_w = max(1, int(slot * 0.6))
    red = 3  # 调色板索引：3=红
    for i, k in enumerate(items):
        cx = left + int(i * slot + slot / 2)
        up = k["close"] >= k["open"]
        wick = red if (colored and up) else 0
        draw.line([(cx, py(k["high"])), (cx, py(k["low"]))], fill=wick, width=1)
        y_top, y_bot = py(max(k["open"], k["close"])), py(min(k["open"], k["close"]))
        if y_bot - y_top < 1:
            y_bot = y_top + 1
        bx = cx - body_w // 2
        if up and colored:
            draw.rectangle([bx, y_top, bx + body_w, y_bot], fill=red)  # 红涨（A股惯例）
        elif up:
            draw.rectangle([bx, y_top, bx + body_w, y_bot], outline=0, width=1)  # 黑白屏：空心
        else:
            draw.rectangle([bx, y_top, bx + body_w, y_bot], fill=0)  # 跌：实心黑

    return img


# ── 渲染入口（由 render_dual_screen 的理财分支调用） ───────────
def render_a_screen(a_cfg: dict[str, Any], dual_config: Optional[dict], w: int, h: int,
                    mac: str = "") -> Image.Image:
    """A 屏理财模式：finance-watch 自选行情 / finance-summary 市场摘要。"""
    mode = str(a_cfg.get("mode") or "finance-watch")
    watchlist = _effective_watchlist(mac, dual_config)
    if mode == "finance-summary":
        quotes = fetch_quotes(INDEX_SUMMARY_CODES)
        return render_finance_list(quotes, w, h, title="市场摘要", footer="指数快照 · 腾讯公开接口（原型）")
    quotes = fetch_quotes(watchlist) if watchlist else []
    return render_finance_list(quotes, w, h, title="自选行情")


def render_b_screen(b_cfg: dict[str, Any], dual_config: Optional[dict], w: int, h: int, colors: int,
                    mac: str = "") -> Image.Image:
    """B 屏理财模式：finance-trend / finance-kline → A 屏自选首只的日 K。"""
    watchlist = _effective_watchlist(mac, dual_config)
    code = str(b_cfg.get("klineCode") or "").strip()
    if not code:
        code = watchlist[0] if watchlist else "sh000001"
    kline = fetch_kline(code, days=60)
    if not kline["name"]:
        kline["name"] = kline["code"]
    return render_kline_image(kline, w, h, colors)


def _effective_watchlist(mac: str, dual_config: Optional[dict]) -> list[str]:
    """设备配置的 content.financeWatchlist 优先；为空时回落到绑定账号的理财档案自选。

    保证「A 屏选的股票 → B 屏 K 线」不需要手动「应用到设备」也成立。
    """
    codes = _device_watchlist(dual_config)
    if codes:
        return codes
    if not mac:
        return []
    try:
        from .store import Store
        from .settings import settings
        store = Store(settings.database_path)
        owner = store.device_owner(mac.strip().upper())
        if not owner:
            return []
        profile = store.get_user_profile(owner, "finance") or {}
        out = []
        for item in profile.get("watchlist", []):
            code = item.get("code") if isinstance(item, dict) else str(item)
            if code:
                out.append(str(code))
        return out[:8]
    except Exception:  # noqa: BLE001 — 渲染路径不允许因自选回落失败而出错
        return []


def _device_watchlist(dual_config: Optional[dict]) -> list[str]:
    """从设备 dual_config 读取自选代码（content.financeWatchlist）。"""
    if not dual_config:
        return []
    raw = (dual_config.get("content") or {}).get("financeWatchlist") or []
    out = []
    for item in raw:
        code = item.get("code") if isinstance(item, dict) else str(item)
        if code:
            out.append(str(code))
    return out[:8]


# ——— 推荐股（动量筛选原型）与资金管理（持仓视图合成）———
# 纯计算函数，行情由调用方注入，便于单测；两端点见 console_api.py。
# 合规边界：仅基于公开行情的规则化排序/统计，输出必须携带免责声明。

# 推荐候选池：流动性较好的宽基/行业代表（原型，商用需换持牌数据源+策略引擎）
RECO_POOL = [
    "600519", "601318", "600036", "000858", "600900",
    "000333", "600276", "601899", "300750", "002594",
    "601012", "600030",
]


def rank_recommendations(quotes: list[dict[str, Any]], top: int = 5) -> list[dict[str, Any]]:
    """按当日涨跌幅（动量）排序取前 N，附规则化理由标签。"""
    valid = [q for q in quotes if q.get("pct") is not None and q.get("name")]
    ranked = sorted(valid, key=lambda q: q["pct"], reverse=True)[: max(1, top)]
    items = []
    for q in ranked:
        pct = q["pct"]
        if pct >= 5:
            reason = "今日强势领涨"
        elif pct >= 2:
            reason = "日内动量走强"
        elif pct >= 0:
            reason = "温和上行"
        elif pct > -2:
            reason = "小幅回调"
        else:
            reason = "明显回调"
        items.append({
            "code": q.get("code"),
            "name": q.get("name"),
            "price": q.get("price"),
            "pct": pct,
            "reason": reason,
        })
    return items


RECO_DISCLAIMER = "推荐结果为公开行情的规则化排序，仅供参考，不构成投资建议"


def build_portfolio_view(positions: list[dict[str, Any]], quotes: list[dict[str, Any]],
                         cash: float = 0.0) -> dict[str, Any]:
    """持仓 × 实时行情 → 逐只盈亏/权重/调仓参考 + 汇总。

    positions: [{code, name?, shares>0, cost>0}]；quotes 与 fetch_quotes 输出同构。
    中国市场惯例：涨为正（红）、跌为负（绿），前端按正负着色。
    """
    by_code = {q.get("code"): q for q in quotes}
    rows = []
    total_value = 0.0
    total_cost = 0.0
    for p in positions:
        try:
            shares = float(p.get("shares") or 0)
            cost = float(p.get("cost") or 0)
        except (TypeError, ValueError):
            shares, cost = 0.0, 0.0
        if shares <= 0 or cost <= 0:
            continue
        q = by_code.get(str(p.get("code"))) or {}
        price = q.get("price")
        name = q.get("name") or p.get("name") or str(p.get("code"))
        value = round(shares * price, 2) if price is not None else None
        pnl = round((price - cost) * shares, 2) if price is not None else None
        pnl_pct = round((price / cost - 1) * 100, 2) if price is not None else None
        rows.append({
            "code": p.get("code"), "name": name, "shares": shares, "cost": cost,
            "price": price, "value": value, "pnl": pnl, "pnl_pct": pnl_pct,
            "weight": None,  # 第二遍填充
            "advice": "停牌或无行情，维持观察" if price is None else "",
        })
        if value is not None:
            total_value += value
        total_cost += cost * shares

    n = len(rows)
    for r in rows:
        weight = round(r["value"] / total_value * 100, 1) if (r["value"] is not None and total_value > 0) else None
        r["weight"] = weight
        if r["advice"]:
            continue
        pnl_pct, dev = r["pnl_pct"], (weight - 100.0 / n) if (weight is not None and n) else 0.0
        if pnl_pct >= 15:
            r["advice"] = "累计涨幅较大，可关注止盈节奏"
        elif pnl_pct <= -15:
            r["advice"] = "回撤较大，关注仓位与风险"
        elif dev > 10:
            r["advice"] = f"仓位高于平均 {dev:.0f} 个百分点，可考虑减持均衡"
        elif dev < -10:
            r["advice"] = "仓位低于平均，可考虑逢低增持"
        else:
            r["advice"] = "持有观察"

    total_pnl = round(total_value - total_cost, 2) if total_value else 0.0
    hot = max(rows, key=lambda r: r["weight"] or 0) if rows else None
    summary = {
        "market_value": round(total_value, 2),
        "cost_value": round(total_cost, 2),
        "pnl": total_pnl,
        "pnl_pct": round(total_pnl / total_cost * 100, 2) if total_cost else None,
        "cash": round(cash, 2),
        "count": n,
    }
    notes = []
    if hot and hot["weight"] is not None and hot["weight"] > 40:
        notes.append(f"{hot['name']} 占比 {hot['weight']:.0f}%，仓位较集中")
    if cash > 0 and total_value > 0 and cash / (cash + total_value) > 0.5:
        notes.append("现金占比过半，资金利用率偏低")
    return {"positions": rows, "summary": summary, "notes": notes,
            "disclaimer": "调仓参考为规则化提示，不构成投资建议"}


def search_security(keyword: str, count: int = 5) -> list[dict[str, Any]]:
    """名称/拼音/代码 → 候选证券（东方财富公开 suggest 接口，仅 A 股）。

    返回 [{code(6位), name, market(0深1沪)}]，供持仓录入时自动反查代码。
    """
    kw = (keyword or "").strip()
    if not kw:
        return []
    if re.fullmatch(r"\d{6}", kw):
        return [{"code": kw, "name": "", "market": 1 if kw.startswith(("6", "9", "5")) else 0}]
    url = (
        "https://searchapi.eastmoney.com/api/suggest/get"
        f"?input={urllib.parse.quote(kw)}&type=14&token=D43BF722C8E33BDC906FB84D85E326E8"
        f"&count={max(1, min(count, 10))}"
    )
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.eastmoney.com/"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with _opener.open(req, timeout=8) as resp:
            data = json.load(resp)
    except Exception:
        logger.warning("security search failed: %s", kw, exc_info=True)
        return []
    items = ((data.get("QuotationCodeTable") or {}).get("Data")) or []
    out = []
    for it in items:
        code = str(it.get("Code") or "")
        name = str(it.get("Name") or "")
        quote_id = str(it.get("QuoteID") or "")  # 形如 1.688018（1=沪 0=深）
        stype_name = str(it.get("SecurityTypeName") or "")
        if not re.fullmatch(r"\d{6}", code) or "." not in quote_id:
            continue
        if stype_name and ("债" in stype_name or "基金" in stype_name or "退" in stype_name):
            continue
        market = 1 if quote_id.startswith("1.") else 0
        out.append({"code": code, "name": name, "market": market})
        if len(out) >= count:
            break
    return out


# ——— 黄金/期货（贵金属与商品期货主连）———
# 东财 secid 已实测可用：113=上期所 101=COMEX 118=上金所 114=大商所
GOLD_FUTURES_SECIDS: list[tuple[str, str]] = [
    ("113.aum", "沪金主连"),
    ("113.agm", "沪银主连"),
    ("101.GC00Y", "COMEX黄金"),
    ("118.AUTD", "黄金T+D"),
    ("114.jm", "焦炭主连"),
]


def fetch_gold_futures() -> list[dict[str, Any]]:
    """贵金属/期货实时快照（固定标的池，secid 直查）。"""
    url = (f"{_EM_QUOTE_URL}?fltt=2&invt=2&fields=f2,f3,f4,f12,f13,f14"
           f"&secids={','.join(s for s, _ in GOLD_FUTURES_SECIDS)}")
    try:
        data = _get_json(url)
    except Exception:
        logger.warning("[FINANCE] gold/futures quote failed", exc_info=True)
        return []
    diff = ((data or {}).get("data") or {}).get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    rows = {
        str(row.get("f12") or ""): {
            "code": str(row.get("f12") or ""),
            "name": str(row.get("f14") or ""),
            "price": _num(row.get("f2")),
            "pct": _num(row.get("f3")),
            "chg": _num(row.get("f4")),
            "market": int(row.get("f13") or 0),
        }
        for row in diff
    }
    # 按定义顺序输出，缺行情的也保留占位
    return [rows.get(secid.split(".")[-1]) or {"code": secid.split(".")[-1], "name": name,
                                               "price": None, "pct": None, "chg": None, "market": 0}
            for secid, name in GOLD_FUTURES_SECIDS]
