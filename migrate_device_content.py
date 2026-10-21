#!/usr/bin/env python3
"""把旧生产库 services/legacy-api/inksight.db 的设备双屏内容迁移到新后台 data/xiaowu.db。

策略（按用户决定：只迁设备内容，不迁账号）：
- 旧库 device_state.mac  -> 新库 devices.device_id (MAC 即设备ID)
- 旧库 device_state.dual_config -> 新库 content_configs.payload
- 全部设备注册进 devices 表（使固件将来能识别 device_exists）
- 有 dual_config 的设备：写入 content_configs
- 所有设备绑定到 demo-user（demo 账号登录即可在后台看到）
"""
import sqlite3, json, shutil, os, hashlib, secrets
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
OLD = os.path.join(ROOT, "services", "legacy-api", "inksight.db")
NEW = os.path.join(ROOT, "data", "xiaowu.db")
BAK = NEW + ".bak-before-migrate-" + datetime.now().strftime("%Y%m%d-%H%M%S")

DEFAULT_CAPS = {
    "model": "xiaowu-s3-dual",
    "screens": [
        {"id": "A", "width": 400, "height": 300, "colors": ["black", "white"]},
        {"id": "B", "width": 400, "height": 300, "colors": ["black", "white", "yellow"]},
    ],
    "buttons": ["left", "right", "confirm"],
    "audio_input": True, "audio_output": True, "storage": True, "wifi": True,
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def main():
    assert os.path.exists(OLD), f"旧库不存在: {OLD}"
    # 1) 备份新库
    if os.path.exists(NEW):
        shutil.copy2(NEW, BAK)
        print(f"已备份新库 -> {BAK}")
    else:
        print("新库不存在，将新建（理论上不会发生）")

    old = sqlite3.connect(OLD)
    new = sqlite3.connect(NEW)
    oc, nc = old.cursor(), new.cursor()

    # 2) 取全部设备 MAC（去重），并取每台最新一条非空 dual_config
    rows = oc.execute(
        "SELECT mac, dual_config, dual_config_updated_at FROM device_state"
    ).fetchall()
    devices = {}  # mac -> (dual_config or None, updated_at)
    for mac, dc, upd in rows:
        if not mac:
            continue
        mac = mac.strip().upper()
        if not dc:
            devices.setdefault(mac, (None, None))
        else:
            # 保留最新非空
            if mac not in devices or (devices[mac][0] is None):
                devices[mac] = (dc, upd)

    print(f"旧库去重设备数: {len(devices)}")

    DEMO = "demo-user"
    cnt_devices = cnt_content = cnt_bind = 0
    for mac, (dc, upd) in sorted(devices.items()):
        # 设备表（若不存在）
        nc.execute(
            "INSERT OR IGNORE INTO devices "
            "(device_id, model, firmware_version, token_hash, capabilities, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (mac, "xiaowu-s3-dual", "1.0.0",
             hashlib.sha256(secrets.token_hex(16).encode()).hexdigest(),
             json.dumps(DEFAULT_CAPS, ensure_ascii=False),
             now_iso()),
        )
        if nc.rowcount:
            cnt_devices += 1

        # 内容表
        if dc:
            updated = upd or now_iso()
            nc.execute(
                "INSERT OR REPLACE INTO content_configs (device_id, payload, updated_at) VALUES (?,?,?)",
                (mac, dc, updated),
            )
            cnt_content += 1

        # 绑定到 demo-user
        nc.execute(
            "INSERT OR IGNORE INTO device_bindings (user_id, device_id, nickname) VALUES (?,?,?)",
            (DEMO, mac, mac),
        )
        if nc.rowcount:
            cnt_bind += 1

    new.commit()
    old.close(); new.close()
    print(f"迁移完成：新建设备 {cnt_devices} 台，写入双屏内容 {cnt_content} 台，绑定 demo 账号 {cnt_bind} 台")
    print(f"备份文件: {BAK}")

if __name__ == "__main__":
    main()
