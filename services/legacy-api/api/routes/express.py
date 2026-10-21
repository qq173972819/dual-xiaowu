"""快递实时查询代理（可选）。

- 在后端 .env 配置 KUAIDI100_KEY 与 KUAIDI100_CUSTOMER 后，「查询物流」按钮可拉取真实轨迹。
- 未配置密钥时返回 configured=false，前端自动降级为手动维护状态，不影响使用。

快递100 实时查询接口：https://poll.kuaidi100.com/poll/query.do
签名 sign = MD5(param + customer + key).upper()，param 为 {"com": 公司, "num": 单号} 的 JSON。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.parse
import urllib.request
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from core.service_config import get_service_config

logger = logging.getLogger("inksight.express")

router = APIRouter()

KUAIDI100_URL = "https://poll.kuaidi100.com/poll/query.do"

# 快递100 返回的 state（字符串数字）→ 本系统状态
_STATE_MAP = {
    0: "ordered",
    1: "transit",
    2: "transit",
    3: "delivering",
    4: "signed",
    5: "exception",
}


@router.get("/express/track")
async def express_track(no: str = Query(..., description="运单号"), company: str = Query(..., description="快递公司名")):
    # 取值优先级：后台配置（DB）> 环境变量。这样无需改 .env + 重启即可在后台启用。
    key = ((await get_service_config("KUAIDI100_KEY", "")) or os.getenv("KUAIDI100_KEY") or "").strip()
    customer = ((await get_service_config("KUAIDI100_CUSTOMER", "")) or os.getenv("KUAIDI100_CUSTOMER") or "").strip()
    if not key or not customer:
        return JSONResponse({"configured": False, "message": "未配置快递100密钥（可在后台「系统配置」中填写 KUAIDI100_KEY / KUAIDI100_CUSTOMER）"})

    param = json.dumps({"com": company, "num": no}, ensure_ascii=False)
    sign = hashlib.md5((param + customer + key).encode("utf-8")).hexdigest().upper()
    body = urllib.parse.urlencode({"customer": customer, "param": param, "sign": sign}).encode("utf-8")
    req = urllib.request.Request(
        KUAIDI100_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8")
        obj = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 - 网络/解析异常统一降级
        logger.warning("[EXPRESS] query failed: %s", exc)
        return JSONResponse({"configured": True, "ok": False, "message": f"查询失败：{exc}"}, status_code=502)

    if obj.get("message") != "ok":
        return JSONResponse({"configured": True, "ok": False, "message": obj.get("message", "查询失败")})

    state = int(obj.get("state", 0))
    status = _STATE_MAP.get(state, "transit")
    trail = [
        {"time": d.get("time", ""), "context": d.get("context", "")}
        for d in obj.get("data", [])
        if d.get("context")
    ]
    return JSONResponse({"configured": True, "ok": True, "state": state, "status": status, "data": trail})
