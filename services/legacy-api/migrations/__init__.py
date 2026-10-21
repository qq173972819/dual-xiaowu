from __future__ import annotations

from datetime import datetime


async def _column_exists(db, table: str, column: str) -> bool:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return any(row[1] == column for row in rows)


async def _add_column_if_missing(db, table: str, column: str, ddl: str) -> None:
    if await _column_exists(db, table, column):
        return
    await db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


async def _merge_case_duplicate_device_state(db) -> None:
    """把仅大小写不同的 `device_state` 行合并成一行（统一大写）。

    背景：`device_state.mac` 是 TEXT PRIMARY KEY，SQLite 的 TEXT 比较大小写敏感。
    固件 `SystemInfo::GetMacAddress()` 用 `%02x` 生成**小写** MAC，而 `/runtime`
    等路由经 `validate_mac_param` 转成**大写**写入，`/state` 却原样使用调用方传入的
    MAC —— 同一台设备于是裂成两行，固件写大写行、网页读小写行，界面表现为
    「长按切活跃成功，页面永远显示间歇」。

    合并策略：以大写 MAC 为准（不存在则先建），从各副本里挑「有值且 updated_at
    最新」的字段补齐（尤其是 auth_token，不能丢），再删除其余副本行。
    """
    cursor = await db.execute("SELECT mac FROM device_state")
    groups: dict[str, list[str]] = {}
    for (raw_mac,) in await cursor.fetchall():
        groups.setdefault(str(raw_mac).upper(), []).append(str(raw_mac))
    # 除了合并大小写重复行，也必须把「只有一条小写历史行」改成大写。
    # 否则升级后数据访问层统一用大写查询，这条旧数据会变成不可见。
    noncanonical = {
        canonical: variants
        for canonical, variants in groups.items()
        if len(variants) > 1 or variants[0] != canonical
    }
    if not noncanonical:
        return

    now = datetime.now().isoformat()
    info = await db.execute("PRAGMA table_info(device_state)")
    columns = [row[1] for row in await info.fetchall() if row[1] != "mac"]

    merged: list[str] = []
    for canonical, variants in noncanonical.items():
        if len(variants) == 1:
            # 没有冲突时直接改主键，完整保留该行（包括 runtime_mode/token）。
            await db.execute(
                "UPDATE device_state "
                "SET mac = ?, last_state_poll_at = '', updated_at = ? "
                "WHERE mac = ?",
                (canonical, now, variants[0]),
            )
            merged.append(canonical)
            continue
        await db.execute(
            "INSERT OR IGNORE INTO device_state (mac, updated_at) VALUES (?, ?)",
            (canonical, now),
        )
        for column in columns:
            candidates: list[tuple[object, str]] = []
            for variant in variants:
                cur = await db.execute(
                    f"SELECT {column}, updated_at FROM device_state WHERE mac = ?",
                    (variant,),
                )
                row = await cur.fetchone()
                if row is None:
                    continue
                value, updated_at = row[0], row[1] or ""
                # 空串 / NULL / 0 都视为「没值」，不参与覆盖
                if value in (None, "", 0):
                    continue
                candidates.append((value, updated_at))
            if not candidates:
                continue
            best = max(candidates, key=lambda item: item[1])[0]
            cur = await db.execute(
                f"SELECT {column} FROM device_state WHERE mac = ?", (canonical,)
            )
            current = await cur.fetchone()
            if current and current[0] in (None, "", 0):
                await db.execute(
                    f"UPDATE device_state SET {column} = ? WHERE mac = ?",
                    (best, canonical),
                )
        for variant in variants:
            if variant != canonical:
                await db.execute("DELETE FROM device_state WHERE mac = ?", (variant,))
        # `last_state_poll_at` 只是「该固件会不会轮询 /state」的判定依据，
        # 跨行继承会把探针（诊断脚本、误用 device token 的调用）留下的时间带走，
        # 让从不轮询的 S3 被误判成 C3。合并时一律清空，让判定回到干净状态。
        await db.execute(
            "UPDATE device_state SET last_state_poll_at = '', updated_at = ? WHERE mac = ?",
            (now, canonical),
        )
        merged.append(canonical)

    if merged:
        print(f"[MIGRATION] 合并大小写重复的 device_state 行: {', '.join(sorted(merged))}")


async def run_main_db_migrations(db, *, defaults: dict[str, str]) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_versions (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    cursor = await db.execute("SELECT version FROM schema_versions")
    applied_versions = {row[0] for row in await cursor.fetchall()}

    migrations = [
        (1, "configs.countdown_events", lambda: _add_column_if_missing(db, "configs", "countdown_events", "countdown_events TEXT DEFAULT '[]'")),
        (2, "configs.time_slot_rules", lambda: _add_column_if_missing(db, "configs", "time_slot_rules", "time_slot_rules TEXT DEFAULT '[]'")),
        (3, "configs.memo_text", lambda: _add_column_if_missing(db, "configs", "memo_text", "memo_text TEXT DEFAULT ''")),
        (4, "configs.llm_api_key", lambda: _add_column_if_missing(db, "configs", "llm_api_key", "llm_api_key TEXT DEFAULT ''")),
        (5, "configs.image_provider", lambda: _add_column_if_missing(db, "configs", "image_provider", f"image_provider TEXT DEFAULT '{defaults['image_provider']}'")),
        (6, "configs.image_model", lambda: _add_column_if_missing(db, "configs", "image_model", f"image_model TEXT DEFAULT '{defaults['image_model']}'")),
        (7, "configs.image_api_key", lambda: _add_column_if_missing(db, "configs", "image_api_key", "image_api_key TEXT DEFAULT ''")),
        (8, "configs.mode_overrides", lambda: _add_column_if_missing(db, "configs", "mode_overrides", "mode_overrides TEXT DEFAULT '{}'")),
        (9, "device_state.pending_mode", lambda: _add_column_if_missing(db, "device_state", "pending_mode", "pending_mode TEXT DEFAULT ''")),
        (10, "device_state.last_state_poll_at", lambda: _add_column_if_missing(db, "device_state", "last_state_poll_at", "last_state_poll_at TEXT DEFAULT ''")),
        (11, "device_state.auth_token", lambda: _add_column_if_missing(db, "device_state", "auth_token", "auth_token TEXT DEFAULT ''")),
        (12, "device_state.runtime_mode", lambda: _add_column_if_missing(db, "device_state", "runtime_mode", "runtime_mode TEXT DEFAULT 'interval'")),
        (13, "device_state.expected_refresh_min", lambda: _add_column_if_missing(db, "device_state", "expected_refresh_min", "expected_refresh_min INTEGER DEFAULT 0")),
        (14, "device_state.last_reconnect_regen_at", lambda: _add_column_if_missing(db, "device_state", "last_reconnect_regen_at", "last_reconnect_regen_at TEXT DEFAULT ''")),
        (15, "device_claim_tokens.pair_code", lambda: _add_column_if_missing(db, "device_claim_tokens", "pair_code", "pair_code TEXT DEFAULT ''")),
        (
            16,
            "user_preferences.create",
            lambda: db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id INTEGER PRIMARY KEY,
                    push_enabled INTEGER DEFAULT 0,
                    push_time TEXT DEFAULT '08:00',
                    push_modes TEXT DEFAULT '[]',
                    widget_mode TEXT DEFAULT 'STOIC',
                    locale TEXT DEFAULT 'zh',
                    timezone TEXT DEFAULT 'Asia/Shanghai',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            ),
        ),
        (
            17,
            "push_tokens.create",
            lambda: db.execute(
                """
                CREATE TABLE IF NOT EXISTS push_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    push_token TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    push_time TEXT DEFAULT '08:00',
                    timezone TEXT DEFAULT 'Asia/Shanghai',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, push_token),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            ),
        ),
        (18, "configs.latitude", lambda: _add_column_if_missing(db, "configs", "latitude", "latitude REAL")),
        (19, "configs.longitude", lambda: _add_column_if_missing(db, "configs", "longitude", "longitude REAL")),
        (20, "configs.timezone", lambda: _add_column_if_missing(db, "configs", "timezone", "timezone TEXT DEFAULT ''")),
        (21, "configs.admin1", lambda: _add_column_if_missing(db, "configs", "admin1", "admin1 TEXT DEFAULT ''")),
        (22, "configs.country", lambda: _add_column_if_missing(db, "configs", "country", "country TEXT DEFAULT ''")),
        (23, "device_state.ota_original_url", lambda: _add_column_if_missing(db, "device_state", "ota_original_url", "ota_original_url TEXT DEFAULT ''")),
        (
            24,
            "user_activity_events.create",
            lambda: db.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_activity_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    event_name TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'web',
                    path TEXT DEFAULT '',
                    method TEXT DEFAULT '',
                    ip_hash TEXT DEFAULT '',
                    user_agent TEXT DEFAULT '',
                    metadata_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );
                CREATE INDEX IF NOT EXISTS idx_user_activity_user_time
                    ON user_activity_events(user_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_user_activity_event_time
                    ON user_activity_events(event_name, created_at);
                CREATE INDEX IF NOT EXISTS idx_user_activity_time
                    ON user_activity_events(created_at);
                """
            ),
        ),
        (
            25,
            "vocab_review.create",
            lambda: db.executescript(
                """
                CREATE TABLE IF NOT EXISTS vocab_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    deck_id TEXT NOT NULL,
                    word TEXT NOT NULL,
                    phonetic TEXT DEFAULT '',
                    definition TEXT NOT NULL,
                    example TEXT DEFAULT '',
                    difficulty INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    UNIQUE(deck_id, word)
                );
                CREATE INDEX IF NOT EXISTS idx_vocab_items_deck
                    ON vocab_items(deck_id, difficulty, id);

                CREATE TABLE IF NOT EXISTS vocab_progress (
                    mac TEXT NOT NULL,
                    vocab_item_id INTEGER NOT NULL,
                    due_at TEXT NOT NULL,
                    interval_days INTEGER DEFAULT 0,
                    ease_factor REAL DEFAULT 2.5,
                    repetitions INTEGER DEFAULT 0,
                    lapses INTEGER DEFAULT 0,
                    last_grade TEXT DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(mac, vocab_item_id),
                    FOREIGN KEY(vocab_item_id) REFERENCES vocab_items(id)
                );
                CREATE INDEX IF NOT EXISTS idx_vocab_progress_due
                    ON vocab_progress(mac, due_at);
                CREATE TABLE IF NOT EXISTS vocab_session_state (
                    mac TEXT PRIMARY KEY,
                    deck_id TEXT DEFAULT 'core_en',
                    current_item_id INTEGER,
                    side TEXT DEFAULT 'front',
                    rating_cursor INTEGER DEFAULT 0,
                    review_date TEXT DEFAULT '',
                    reviewed_count INTEGER DEFAULT 0,
                    new_count INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(current_item_id) REFERENCES vocab_items(id)
                );
                """
            ),
        ),
        (26, "device_state.merge_case_duplicates", lambda: _merge_case_duplicate_device_state(db)),
    ]

    now = datetime.now().isoformat()
    for version, name, apply in migrations:
        if version in applied_versions:
            continue
        await apply()
        await db.execute(
            "INSERT INTO schema_versions (version, name, applied_at) VALUES (?, ?, ?)",
            (version, name, now),
        )

    await db.commit()
