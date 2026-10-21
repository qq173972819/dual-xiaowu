"""Tests for device stats functions."""
from unittest.mock import patch

import pytest
from core.stats_store import init_stats_db, log_heartbeat, get_device_stats, get_latest_heartbeat


@pytest.fixture(autouse=True)
async def isolate_stats_db(tmp_path):
    """Use an isolated DB file per test and reset shared connections."""
    from core import db as db_mod

    test_db = str(tmp_path / "stats_test.db")
    await db_mod.close_all()

    with patch.object(db_mod, "_MAIN_DB_PATH", test_db), \
         patch("core.stats_store.DB_PATH", test_db), \
         patch("core.config_store.DB_PATH", test_db):
        yield

    await db_mod.close_all()


@pytest.mark.asyncio
async def test_get_device_stats_without_heartbeat():
    """When no heartbeat exists, last_battery_voltage and last_rssi are None."""
    await init_stats_db()

    stats = await get_device_stats("AA:BB:CC:DD:EE:FF")
    assert stats["last_battery_voltage"] is None
    assert stats["last_rssi"] is None


@pytest.mark.asyncio
async def test_get_device_stats_with_heartbeat():
    """Heartbeat data is correctly reflected in the stats response."""
    await init_stats_db()
    mac = "AA:BB:CC:DD:EE:FF"

    await log_heartbeat(mac, battery_voltage=3.91, wifi_rssi=-42)
    stats = await get_device_stats(mac)

    assert stats["last_battery_voltage"] == 3.91
    assert stats["last_rssi"] == -42


@pytest.mark.asyncio
async def test_get_device_stats_uses_latest_heartbeat():
    """Only the most recent heartbeat is used for last_battery_voltage/last_rssi."""
    await init_stats_db()
    mac = "AA:BB:CC:DD:EE:FF"

    await log_heartbeat(mac, battery_voltage=3.50, wifi_rssi=-60)
    await log_heartbeat(mac, battery_voltage=3.91, wifi_rssi=-42)
    await log_heartbeat(mac, battery_voltage=4.00, wifi_rssi=-55)

    stats = await get_device_stats(mac)

    assert stats["last_battery_voltage"] == 4.00
    assert stats["last_rssi"] == -55


@pytest.mark.asyncio
async def test_heartbeat_mac_is_normalized_on_write():
    await init_stats_db()
    await log_heartbeat("7c:4f:ad:24:66:9c", battery_voltage=3.88, wifi_rssi=-48)

    from core.db import get_main_db

    db = await get_main_db()
    cursor = await db.execute("SELECT mac FROM device_heartbeats LIMIT 1")
    assert (await cursor.fetchone())[0] == "7C:4F:AD:24:66:9C"


@pytest.mark.asyncio
async def test_latest_heartbeat_reads_legacy_lowercase_mac():
    await init_stats_db()
    from core.db import get_main_db

    db = await get_main_db()
    await db.execute(
        "INSERT INTO device_heartbeats "
        "(mac, battery_voltage, wifi_rssi, created_at) VALUES (?, ?, ?, ?)",
        ("7c:4f:ad:24:66:9c", 3.92, -41, "2026-10-13T09:30:00"),
    )
    await db.commit()

    heartbeat = await get_latest_heartbeat("7C:4F:AD:24:66:9C")
    assert heartbeat is not None
    assert heartbeat["battery_voltage"] == 3.92
    assert heartbeat["wifi_rssi"] == -41
