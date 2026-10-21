"""后台服务配置（全局）。

当前暴露：快递100 实时查询所需的 KUAIDI100_KEY / KUAIDI100_CUSTOMER。
这两项原本需写在后端 `.env` 并经重启生效；现在可在后台「系统配置」页直接
填写，DB 中的值优先于 `.env`。

鉴权：复用双屏控制台的登录会话（require_user）。对自托管单操作员部署而言，
登录即可配置，符合控制台现有鉴权习惯。
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.auth import require_user
from core.service_config import get_service_config, set_service_config, mask_secret

logger = logging.getLogger(__name__)

router = APIRouter()

# 后台可配置项白名单。新增"启动先决条件"类密钥时在此登记即可。
KNOWN = {
    "KUAIDI100_KEY": {
        "label": "快递100 授权 KEY",
        "secret": True,
        "placeholder": "在 kuaidi100.com 申请后获得",
        "hint": "快递100 实时查询的核心密钥。申请地址：https://www.kuaidi100.com（免费）",
    },
    "KUAIDI100_CUSTOMER": {
        "label": "快递100 客户号 CUSTOMER",
        "secret": False,
        "placeholder": "快递100 客户号（customer）",
        "hint": "与 KEY 配套的客户号，二者缺一不可。",
    },
}


class SetServiceConfigIn(BaseModel):
    key: str
    value: str


@router.get("/settings/service")
async def get_service_settings(user_id: int = Depends(require_user)):
    items = []
    for key, meta in KNOWN.items():
        db_val = (await get_service_config(key, "")).strip()
        env_val = (os.getenv(key) or "").strip()
        effective = db_val or env_val
        source = "db" if db_val else ("env" if env_val else "none")
        items.append({
            "key": key,
            "label": meta["label"],
            "secret": meta["secret"],
            "hint": meta["hint"],
            "placeholder": meta["placeholder"],
            "masked": mask_secret(effective) if meta["secret"] else effective,
            "configured": bool(effective),
            "source": source,
        })
    express_key = (await get_service_config("KUAIDI100_KEY", "")).strip() or (os.getenv("KUAIDI100_KEY") or "").strip()
    express_customer = (await get_service_config("KUAIDI100_CUSTOMER", "")).strip() or (os.getenv("KUAIDI100_CUSTOMER") or "").strip()
    return {
        "items": items,
        "express_configured": bool(express_key and express_customer),
    }


@router.post("/settings/service")
async def set_service_setting(payload: SetServiceConfigIn, user_id: int = Depends(require_user)):
    key = (payload.key or "").strip()
    if key not in KNOWN:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"不支持的配置项：{key}")
    value = payload.value or ""
    await set_service_config(key, value)
    meta = KNOWN[key]
    raw = (await get_service_config(key, "")).strip()
    return {
        "key": key,
        "configured": bool(raw),
        "masked": mask_secret(raw) if meta["secret"] else raw,
    }
