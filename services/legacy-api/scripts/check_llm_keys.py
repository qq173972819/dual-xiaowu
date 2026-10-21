"""一键自检：设备刷出「API key 无效 / 未配置 Key」时，先看这个脚本。

用法（仓库根目录或 backend/ 下均可）：
    backend/.venv/Scripts/python.exe backend/scripts/check_llm_keys.py

它回答三个问题：
1. 平台免费额度这条路有没有钥匙（backend/.env 里的三个 Key）
2. 账号里有没有 BYOK（user_llm_config），是不是掩码残留
3. 设备绑定到哪个账号了（device_memberships），该账号走哪条路

只打印 Key 的长度和首尾几位，不打印完整密钥。
"""
import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def mask(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return "（空）"
    if len(v) <= 8:
        return f"{v[0]}***{v[-1]} (len={len(v)})"
    return f"{v[:4]}...{v[-4:]} (len={len(v)})"


def load_dotenv(path: str) -> dict:
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


async def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = load_dotenv(os.path.join(root, ".env"))

    print("=" * 68)
    print("① 平台免费额度用的钥匙（backend/.env）")
    print("=" * 68)
    platform_ok = False
    for var in ("DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY"):
        raw = os.getenv(var) or env.get(var, "")
        ok = bool(raw.strip())
        platform_ok = platform_ok or ok
        print(f"  {var:22s} {mask(raw)}   {'✓' if ok else '✗ 未配置'}")
    if not platform_ok:
        print("\n  ⚠ 三个全是空的：选了「平台免费额度」的设备必然出")
        print("     「未配置 AI 接口 Key」或「API key 无效」——账号有多少额度都没用，")
        print("     额度只是计数器，钥匙才是真正能调模型的东西。")
        print("     要么在 .env 里填一个 Key 并重启后端，要么让用户在个人中心填自己的 Key。")

    print()
    print("=" * 68)
    print("② 账号 BYOK（user_llm_config）")
    print("=" * 68)
    from core.db import get_main_db

    db = await get_main_db()
    cursor = await db.execute(
        "SELECT user_id, llm_access_mode, provider, api_key FROM user_llm_config"
    )
    rows = await cursor.fetchall()
    if not rows:
        print("  （空表）没有任何账号配置过自己的 Key，所有设备都只能走平台额度。")
    for user_id, mode, provider, key in rows:
        key_s = (key or "").strip()
        usable = bool(key_s) and "****" not in key_s
        print(
            f"  user_id={user_id}  mode={mode or 'preset':14s} provider={provider or '-':10s} "
            f"key={mask(key_s)}  {'✓ 可用' if usable else '✗ 不可用（空或掩码残留）'}"
        )

    print()
    print("=" * 68)
    print("③ 设备绑定（device_memberships）")
    print("=" * 68)
    try:
        cur = await db.execute(
            "SELECT mac, user_id, role, status FROM device_memberships"
        )
        mems = await cur.fetchall()
    except Exception as exc:  # 表不存在等
        print(f"  读取失败：{exc}")
        mems = []
    if not mems:
        print("  （空表）没有设备被绑定 —— 设备拉图应该拿到配对码图。")
        print("  如果屏幕上不是配对码，说明请求没带 mac（见后端日志 [RENDER] 未携带 mac）。")
    byok_users = {r[0]: r for r in rows}
    for mac, user_id, role, status in mems:
        cfg = byok_users.get(user_id)
        key_s = (cfg[3] or "").strip() if cfg else ""
        usable = bool(key_s) and "****" not in key_s
        mode = (cfg[1] or "preset") if cfg else "（账号未配置）"
        if usable and mode != "platform":
            path = "账号自己的 Key（BYOK）"
        elif platform_ok:
            path = "平台 .env 的 Key + 扣账号额度"
        elif usable:
            path = "平台 .env 没配 → 回落账号 Key"
        else:
            path = "★ 没有任何可用 Key，设备会出「未配置 AI 接口 Key」★"
        print(f"  {mac}  user_id={user_id}  {role}/{status}  mode={mode}  → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
