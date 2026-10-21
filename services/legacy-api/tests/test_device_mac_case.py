"""
MAC 大小写一致性回归测试。

固件 `SystemInfo::GetMacAddress()` 用 `%02x` 生成**小写** MAC，而 `/runtime` 等路由
经 `validate_mac_param` 转成**大写**写入。`device_state.mac` 是 TEXT PRIMARY KEY 且
SQLite 的 TEXT 比较大小写敏感，一旦读写两侧大小写不一致，同一台设备就会裂成两行：

    84:C7:BB:59:A1:10  runtime_mode = active     ← 固件写进来的
    84:c7:bb:59:a1:10  runtime_mode = interval   ← 网页读的（建行默认值）

界面表现：长按 + 3 秒切活跃明明成功了，页面永远显示间歇。
"""
import pytest
from unittest.mock import patch

from core.config_store import (
    generate_device_token,
    get_device_state,
    init_db,
    normalize_mac,
    set_pending_refresh,
    update_device_state,
    validate_device_token,
)
from migrations import _merge_case_duplicate_device_state


@pytest.fixture(autouse=True)
async def use_temp_db(tmp_path):
    from core import db as db_mod

    db_path = str(tmp_path / "test.db")
    await db_mod.close_all()
    with patch.object(db_mod, "_MAIN_DB_PATH", db_path), \
         patch("core.config_store.DB_PATH", db_path), \
         patch("core.stats_store.DB_PATH", db_path):
        await init_db()
        yield


def test_normalize_mac_is_uppercase():
    assert normalize_mac("84:c7:bb:59:a1:10") == "84:C7:BB:59:A1:10"
    assert normalize_mac("84:C7:BB:59:A1:10") == "84:C7:BB:59:A1:10"
    assert normalize_mac("  84:c7:bb:59:a1:10  ") == "84:C7:BB:59:A1:10"
    assert normalize_mac(None) == ""


async def test_runtime_mode_written_lowercase_is_readable_uppercase():
    """固件（小写）写、网页（大写）读，必须命中同一行。"""
    await update_device_state("84:c7:bb:59:a1:10", runtime_mode="active")

    upper = await get_device_state("84:C7:BB:59:A1:10")
    lower = await get_device_state("84:c7:bb:59:a1:10")

    assert upper is not None and upper["runtime_mode"] == "active"
    assert lower is not None and lower["runtime_mode"] == "active"


async def test_write_read_mixed_case_never_creates_two_rows():
    """反复混用大小写写入，device_state 里只能有一行。"""
    mac_variants = ("aa:bb:cc:dd:ee:01", "AA:BB:CC:DD:EE:01", "Aa:Bb:Cc:Dd:Ee:01")
    for variant in mac_variants:
        await update_device_state(variant, runtime_mode="active", last_persona="STOIC")

    from core.db import get_main_db

    db = await get_main_db()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM device_state WHERE UPPER(mac) = 'AA:BB:CC:DD:EE:01'"
    )
    count = (await cursor.fetchone())[0]
    assert count == 1, f"大小写混用产生了 {count} 行，应为 1 行"

    state = await get_device_state("aa:bb:cc:dd:ee:01")
    assert state["runtime_mode"] == "active"
    assert state["last_persona"] == "STOIC"


async def test_device_token_survives_case_mismatch():
    """固件用小写拿 token，后端用大写校验也必须通过。"""
    token = await generate_device_token("11:22:33:44:55:66")
    assert await validate_device_token("11:22:33:44:55:66", token) is True
    assert await validate_device_token("11:22:33:44:55:66".upper(), token) is True


async def test_pending_refresh_case_insensitive():
    await set_pending_refresh("aa:bb:cc:dd:ee:02", True)
    from core.config_store import consume_pending_refresh

    assert await consume_pending_refresh("AA:BB:CC:DD:EE:02") is True


async def test_migration_merges_case_duplicate_rows():
    """迁移必须把历史遗留的大小写重复行合并，且不能丢 auth_token。"""
    from core.db import get_main_db

    db = await get_main_db()
    # 手工造出「固件写大写行、网页读小写行」的现场
    await db.execute(
        "INSERT INTO device_state (mac, runtime_mode, updated_at) VALUES (?, ?, ?)",
        ("84:C7:BB:59:A1:10", "active", "2026-10-12T18:40:00"),
    )
    await db.execute(
        "INSERT INTO device_state (mac, runtime_mode, auth_token, updated_at) "
        "VALUES (?, ?, ?, ?)",
        ("84:c7:bb:59:a1:10", "interval", "tok-lower", "2026-10-12T18:50:00"),
    )
    await db.commit()

    await _merge_case_duplicate_device_state(db)
    await db.commit()

    cursor = await db.execute(
        "SELECT mac, runtime_mode, auth_token FROM device_state WHERE UPPER(mac) = ?",
        ("84:C7:BB:59:A1:10",),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1, "合并后应只剩一行"
    mac, runtime_mode, auth_token = rows[0]
    assert mac == "84:C7:BB:59:A1:10", "规范行必须是大写 MAC"
    # 大写行的 runtime_mode 非空，不能被小写行的默认值覆盖
    assert runtime_mode == "active"
    # 小写行独有的 token 必须补进规范行，不能丢
    assert auth_token == "tok-lower"


async def test_migration_normalizes_single_lowercase_row():
    """只有一条小写历史行时也必须改名，否则规范化后的查询会丢失它。"""
    from core.db import get_main_db

    db = await get_main_db()
    await db.execute(
        "INSERT INTO device_state (mac, runtime_mode, auth_token, updated_at) "
        "VALUES (?, ?, ?, ?)",
        ("84:c7:bb:59:a1:11", "active", "tok-single", "2026-10-12T18:50:00"),
    )
    await db.commit()

    await _merge_case_duplicate_device_state(db)
    await db.commit()

    cursor = await db.execute(
        "SELECT mac, runtime_mode, auth_token FROM device_state WHERE UPPER(mac) = ?",
        ("84:C7:BB:59:A1:11",),
    )
    rows = await cursor.fetchall()
    assert rows == [("84:C7:BB:59:A1:11", "active", "tok-single")]


async def test_migration_is_idempotent_and_noop_when_clean():
    from core.db import get_main_db

    db = await get_main_db()
    await update_device_state("aa:bb:cc:dd:ee:03", runtime_mode="active")

    await _merge_case_duplicate_device_state(db)
    await _merge_case_duplicate_device_state(db)
    await db.commit()

    cursor = await db.execute("SELECT COUNT(*) FROM device_state")
    assert (await cursor.fetchone())[0] == 1
