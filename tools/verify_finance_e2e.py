# -*- coding: utf-8 -*-
"""端到端验证：理财场景 A/B 屏渲染（走本机 8105 HTTP 接口）。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8105"
TOKEN = "demo-user-token"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, body: dict | None = None, headers: dict | None = None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with opener.open(req, timeout=40) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


# 1) 写入理财场景双屏配置
st, _, body = call("POST", "/api/device/FIN-TEST-01/dual-config", {
    "version": 1, "sceneKey": "finance",
    "config": {"finance": {"a": {"mode": "finance-watch", "title": "实时行情"},
                            "b": {"mode": "finance-trend", "title": "行情趋势"}}},
    "content": {},
}, {"X-User-Token": TOKEN})
print("dual-config:", st, body[:120])

# 2) A 屏渲染（1-bit BMP，固件同路径）
st, hdrs, data = call("GET", "/api/render?mac=FIN-TEST-01&w=400&h=300&persona=DUAL_A",
                      headers={"X-Device-Token": "CGWvVB2pxYeyo6NNC0V7zuCO5ZCoGADBNT64ivT9mUw"})
print("A render:", st, hdrs.get("X-Render-Time-Ms"), hdrs.get("X-Pending-Refresh"), len(data), "bytes")
if st == 200:
    open(r"data\verify_a.bmp", "wb").write(data)
    from PIL import Image
    import io
    Image.open(io.BytesIO(data)).save(r"data\verify_a.png")

# 3) B 屏预览（与设备渲染同一路径 render_dual_screen，PNG 便于查看）
st, hdrs, data = call("GET", "/api/preview?mac=FIN-TEST-01&persona=DUAL_B&colors=4&bpp=2")
print("B preview:", st, hdrs.get("X-Render-Time-Ms"), len(data), "bytes")
if st == 200:
    open(r"data\verify_b.png", "wb").write(data)
print("done")
