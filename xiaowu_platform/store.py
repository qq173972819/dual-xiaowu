import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
              device_id TEXT PRIMARY KEY, model TEXT NOT NULL, firmware_version TEXT NOT NULL,
              token_hash TEXT NOT NULL, capabilities TEXT NOT NULL, created_at TEXT NOT NULL,
              last_seen_at TEXT, battery_voltage REAL, rssi INTEGER, runtime_mode TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, device_id TEXT NOT NULL, payload TEXT NOT NULL,
              status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
              acknowledged_at TEXT, detail TEXT, source TEXT NOT NULL DEFAULT 'openapi',
              created_by TEXT,
              FOREIGN KEY(device_id) REFERENCES devices(device_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_device_status ON tasks(device_id, status, created_at);
            CREATE TABLE IF NOT EXISTS device_bindings (
              user_id TEXT NOT NULL, device_id TEXT NOT NULL, nickname TEXT NOT NULL,
              PRIMARY KEY(user_id, device_id), FOREIGN KEY(device_id) REFERENCES devices(device_id)
            );
            CREATE TABLE IF NOT EXISTS templates (
              template_id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
              schema_definition TEXT NOT NULL, enabled INTEGER NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL,
              target TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
              user_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
              password_hash TEXT NOT NULL, password_salt TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_sessions (
              token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            CREATE TABLE IF NOT EXISTS content_configs (
              device_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL,
              FOREIGN KEY(device_id) REFERENCES devices(device_id)
            );
            CREATE TABLE IF NOT EXISTS device_claims (
              code TEXT PRIMARY KEY, device_id TEXT NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(device_id) REFERENCES devices(device_id)
            );
            CREATE TABLE IF NOT EXISTS teacher_auth (
              device_id TEXT PRIMARY KEY, frozen INTEGER NOT NULL DEFAULT 0,
              frozen_by TEXT, frozen_at TEXT, FOREIGN KEY(device_id) REFERENCES devices(device_id)
            );
            CREATE TABLE IF NOT EXISTS user_profiles (
              user_id TEXT NOT NULL, category TEXT NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY(user_id, category), FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            CREATE TABLE IF NOT EXISTS service_configs (
              key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS teacher_codes (
              id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, auth_code TEXT NOT NULL,
              auth_name TEXT NOT NULL, permission TEXT NOT NULL, created_by TEXT NOT NULL,
              created_at TEXT NOT NULL, expire_at TEXT, frozen INTEGER NOT NULL DEFAULT 0,
              FOREIGN KEY(device_id) REFERENCES devices(device_id)
            );
            CREATE TABLE IF NOT EXISTS wechat_openid_map (
              openid TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            CREATE TABLE IF NOT EXISTS push_record (
              id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, push_type TEXT NOT NULL,
              sender_type TEXT, sender_name TEXT, content TEXT, is_append INTEGER DEFAULT 1,
              item_count INTEGER DEFAULT 0, create_time TEXT NOT NULL,
              FOREIGN KEY(device_id) REFERENCES devices(device_id)
            );
            CREATE TABLE IF NOT EXISTS device_tokens (
              device_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(device_id) REFERENCES devices(device_id)
            );
            CREATE TABLE IF NOT EXISTS developer_apps (
              app_id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
              scopes TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS webhooks (
              id TEXT PRIMARY KEY, app_id TEXT NOT NULL, url TEXT NOT NULL, events TEXT NOT NULL,
              signing_secret TEXT NOT NULL, enabled INTEGER NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(app_id) REFERENCES developer_apps(app_id)
            );
            CREATE TABLE IF NOT EXISTS device_events (
              id TEXT PRIMARY KEY, device_id TEXT NOT NULL, event_type TEXT NOT NULL,
              data TEXT NOT NULL, occurred_at TEXT NOT NULL, received_at TEXT NOT NULL,
              FOREIGN KEY(device_id) REFERENCES devices(device_id)
            );
            CREATE TABLE IF NOT EXISTS webhook_outbox (
              id TEXT PRIMARY KEY, webhook_id TEXT NOT NULL, event_type TEXT NOT NULL,
              payload TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL,
              created_at TEXT NOT NULL, delivered_at TEXT,
              FOREIGN KEY(webhook_id) REFERENCES webhooks(id)
            );
            CREATE TABLE IF NOT EXISTS user_connectors (
              user_id TEXT NOT NULL, connector_id TEXT NOT NULL,
              credential_encrypted TEXT NOT NULL, external_account TEXT NOT NULL DEFAULT '',
              scopes TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'active',
              expires_at TEXT, updated_at TEXT NOT NULL,
              PRIMARY KEY(user_id, connector_id),
              FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            CREATE TABLE IF NOT EXISTS connector_auth_states (
              state TEXT PRIMARY KEY, user_id TEXT NOT NULL, connector_id TEXT NOT NULL,
              created_at TEXT NOT NULL, expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY, device_id TEXT NOT NULL, sender_name TEXT NOT NULL,
              sender_type TEXT NOT NULL DEFAULT 'member', sender_id TEXT,
              kind TEXT NOT NULL DEFAULT 'text', text TEXT, audio_id TEXT, audio_mime TEXT,
              duration REAL, played_at TEXT, created_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'console',
              FOREIGN KEY(device_id) REFERENCES devices(device_id)
            );
            CREATE INDEX IF NOT EXISTS idx_messages_device_created ON messages(device_id, created_at DESC);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(devices)")}
            if "pending_refresh" not in columns:
                db.execute("ALTER TABLE devices ADD COLUMN pending_refresh INTEGER NOT NULL DEFAULT 0")
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
            if "source" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN source TEXT NOT NULL DEFAULT 'openapi'")
            if "created_by" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN created_by TEXT")
            for item in DEFAULT_TEMPLATES:
                db.execute(
                    "INSERT OR IGNORE INTO templates VALUES(?,?,?,?,?,?)",
                    (item["template_id"], item["name"], item["category"],
                     json.dumps(item["schema_definition"], ensure_ascii=False), 1, now_iso()),
                )
            self._ensure_demo_user(db)
            self._ensure_demo_app(db)

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def register(self, device_id: str, model: str, firmware_version: str, capabilities: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute(
                "INSERT INTO devices VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 0)",
                (device_id, model, firmware_version, self.hash_token(token), json.dumps(capabilities), now_iso()),
            )
            # 生成设备配对码（6 位大写字母数字），供用户在内容后台扫码/输入绑定
            code = secrets.token_hex(3).upper()
            db.execute("INSERT OR REPLACE INTO device_claims(code,device_id,created_at) VALUES(?,?,?)",
                       (code, device_id, now_iso()))
        return token

    def authenticate(self, device_id: str, token: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT token_hash FROM devices WHERE device_id=?", (device_id,)).fetchone()
        return bool(row and secrets.compare_digest(row["token_hash"], self.hash_token(token)))

    def heartbeat(self, device_id: str, data: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE devices SET firmware_version=?, last_seen_at=?, battery_voltage=?, rssi=?, runtime_mode=? WHERE device_id=?",
                (data["firmware_version"], now_iso(), data.get("battery_voltage"), data.get("rssi"), data["runtime_mode"], device_id),
            )

    def capabilities(self, device_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT capabilities FROM devices WHERE device_id=?", (device_id,)).fetchone()
        return json.loads(row["capabilities"]) if row else None

    def create_task(self, device_id: str, payload: dict[str, Any], expires: int,
                    source: str = "openapi", created_by: str | None = None) -> str | None:
        task_id = secrets.token_hex(12)
        with self.connect() as db:
            exists = db.execute("SELECT 1 FROM devices WHERE device_id=?", (device_id,)).fetchone()
            if not exists:
                return None
            expires_at = (datetime.now(UTC) + timedelta(seconds=expires)).isoformat()
            db.execute("INSERT INTO tasks(id,device_id,payload,status,created_at,expires_at,source,created_by) VALUES(?,?,?,?,?,?,?,?)",
                       (task_id, device_id, json.dumps(payload, ensure_ascii=False), "queued", now_iso(), expires_at, source, created_by))
        return task_id

    def next_task(self, device_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE device_id=? AND status='queued' AND expires_at>? ORDER BY created_at LIMIT 1",
                (device_id, now_iso()),
            ).fetchone()
            if not row:
                return None
        return {"id": row["id"], "payload": json.loads(row["payload"]), "created_at": row["created_at"], "expires_at": row["expires_at"]}

    def ack(self, device_id: str, task_id: str, status: str, detail: str | None) -> bool:
        with self.connect() as db:
            cur = db.execute("UPDATE tasks SET status=?, acknowledged_at=?, detail=? WHERE id=? AND device_id=?",
                             (status, now_iso(), detail, task_id, device_id))
        return cur.rowcount == 1

    def bind_demo_device(self, user_id: str, device_id: str, nickname: str = "我的小悟") -> None:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO device_bindings VALUES(?,?,?)", (user_id, device_id, nickname))

    def user_devices(self, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT d.device_id,d.model,d.firmware_version,d.last_seen_at,d.battery_voltage,d.rssi,b.nickname "
                "FROM device_bindings b JOIN devices d ON d.device_id=b.device_id WHERE b.user_id=? ORDER BY b.nickname",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def device_members(self, device_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT b.user_id,u.username,b.nickname FROM device_bindings b JOIN users u ON u.user_id=b.user_id "
                "WHERE b.device_id=? ORDER BY b.nickname",
                (device_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def remove_member(self, device_id: str, user_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM device_bindings WHERE user_id=? AND device_id=?", (user_id, device_id))

    def user_owns(self, user_id: str, device_id: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM device_bindings WHERE user_id=? AND device_id=?", (user_id, device_id)).fetchone() is not None

    def device_owner(self, device_id: str) -> str | None:
        """返回设备当前绑定的账号（未绑定为 None）。"""
        with self.connect() as db:
            row = db.execute("SELECT user_id FROM device_bindings WHERE device_id=?", (device_id,)).fetchone()
        return row["user_id"] if row else None

    def list_templates(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM templates" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY category,name"
        with self.connect() as db:
            rows = db.execute(sql).fetchall()
        return [{**dict(row), "enabled": bool(row["enabled"]), "schema_definition": json.loads(row["schema_definition"])} for row in rows]

    def upsert_template(self, item: dict[str, Any], actor: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO templates VALUES(?,?,?,?,?,?) ON CONFLICT(template_id) DO UPDATE SET "
                "name=excluded.name,category=excluded.category,schema_definition=excluded.schema_definition,enabled=excluded.enabled,updated_at=excluded.updated_at",
                (item["template_id"], item["name"], item["category"], json.dumps(item["schema_definition"], ensure_ascii=False), int(item["enabled"]), now_iso()),
            )
        self.audit(actor, "template.upsert", item["template_id"], {"enabled": item["enabled"]})

    def task_history(self, device_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM tasks", []
        if device_id:
            sql, params = sql + " WHERE device_id=?", [device_id]
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def all_devices(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT device_id,model,firmware_version,created_at,last_seen_at,battery_voltage,rssi,runtime_mode FROM devices ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def overview(self) -> dict[str, int]:
        with self.connect() as db:
            devices = db.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
            online = db.execute("SELECT COUNT(*) FROM devices WHERE last_seen_at IS NOT NULL").fetchone()[0]
            queued = db.execute("SELECT COUNT(*) FROM tasks WHERE status='queued'").fetchone()[0]
            displayed = db.execute("SELECT COUNT(*) FROM tasks WHERE status='displayed'").fetchone()[0]
        return {"devices": devices, "seen_devices": online, "queued_tasks": queued, "displayed_tasks": displayed}

    def audit(self, actor: str, action: str, target: str, detail: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO audit_logs(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)",
                       (actor, action, target, json.dumps(detail, ensure_ascii=False), now_iso()))

    def audits(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]

    @staticmethod
    def _password_hash(password: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000).hex()

    def _ensure_demo_user(self, db: sqlite3.Connection) -> None:
        salt = "7869616f77752d73332d64656d6f2d31"
        password_hash = self._password_hash("demo1234", salt)
        db.execute("INSERT OR IGNORE INTO users VALUES(?,?,?,?,?)", ("demo-user", "demo", password_hash, salt, now_iso()))
        db.execute("INSERT OR IGNORE INTO user_sessions VALUES(?,?,?)",
                   (self.hash_token("demo-user-token"), "demo-user", "2099-01-01T00:00:00+00:00"))

    def _ensure_demo_app(self, db: sqlite3.Connection) -> None:
        db.execute(
            "INSERT OR IGNORE INTO developer_apps VALUES(?,?,?,?,?,?)",
            ("app-dev", "Local development", self.hash_token("dev-app-key"),
             json.dumps(["device.content.write", "device.read", "device.event.read", "webhook.manage"]),
             "active", now_iso()),
        )

    def login(self, username: str, password: str) -> tuple[str, str] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not row or not secrets.compare_digest(row["password_hash"], self._password_hash(password, row["password_salt"])):
                return None
            token = secrets.token_urlsafe(32)
            expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
            db.execute("INSERT INTO user_sessions VALUES(?,?,?)", (self.hash_token(token), row["user_id"], expires))
        return row["user_id"], token

    def session_user(self, token: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT user_id FROM user_sessions WHERE token_hash=? AND expires_at>?",
                             (self.hash_token(token), now_iso())).fetchone()
        return row["user_id"] if row else None

    def create_session_token(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        with self.connect() as db:
            db.execute("INSERT INTO user_sessions VALUES(?,?,?)", (self.hash_token(token), user_id, expires))
        return token

    def device_exists(self, device_id: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM devices WHERE device_id=?", (device_id,)).fetchone() is not None

    def get_content_config(self, device_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT payload, updated_at FROM content_configs WHERE device_id=?", (device_id,)).fetchone()
        if not row:
            return None
        return {"config": json.loads(row["payload"]), "updated_at": row["updated_at"]}

    def get_device_owner(self, device_id: str) -> str | None:
        """返回设备绑定关系的 owner（首个绑定用户），未绑定返回 None。"""
        with self.connect() as db:
            row = db.execute(
                "SELECT user_id FROM device_bindings WHERE device_id=? ORDER BY rowid ASC LIMIT 1",
                (device_id,),
            ).fetchone()
        return row["user_id"] if row else None

    def get_claim_code(self, device_id: str) -> str | None:
        """返回设备配对码（用于未绑定引导图）。"""
        with self.connect() as db:
            row = db.execute("SELECT code FROM device_claims WHERE device_id=?", (device_id,)).fetchone()
        return row["code"] if row else None

    def ensure_claim_code(self, device_id: str) -> str:
        """保证设备一定有一个配对码：没有就现生成一个。

        老设备（或早期注册路径）可能只有 devices 行没有 device_claims 行，
        屏幕会显示「------」，用户永远绑不上。这里兜底补发。
        """
        with self.connect() as db:
            row = db.execute("SELECT code FROM device_claims WHERE device_id=?", (device_id,)).fetchone()
            if row:
                return row["code"]
            code = secrets.token_hex(3).upper()
            db.execute("INSERT OR REPLACE INTO device_claims(code,device_id,created_at) VALUES(?,?,?)",
                       (code, device_id, now_iso()))
        return code

    def consume_pending_refresh(self, device_id: str) -> bool:
        """若设备有待刷新标记则清除并返回 True（设备已拉取）。"""
        with self.connect() as db:
            row = db.execute("SELECT pending_refresh FROM devices WHERE device_id=?", (device_id,)).fetchone()
            was = bool(row and int(row["pending_refresh"]))
            if was:
                db.execute("UPDATE devices SET pending_refresh=0 WHERE device_id=?", (device_id,))
        return was

    def save_content_config(self, device_id: str, payload: dict[str, Any]) -> str:
        ts = now_iso()
        with self.connect() as db:
            db.execute(
                "INSERT INTO content_configs(device_id,payload,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(device_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                (device_id, json.dumps(payload, ensure_ascii=False), ts),
            )
        return ts

    # —— 用户内容后台（dual 控制台）所需 ——
    def register_user(self, username: str, password: str) -> tuple[str, str]:
        if not username or not password:
            raise ValueError("用户名和密码不能为空")
        if len(password) < 6:
            raise ValueError("密码至少 6 位")
        salt = secrets.token_hex(16)
        with self.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ValueError("用户名已被注册")
            user_id = "u-" + secrets.token_hex(8)
            db.execute("INSERT INTO users VALUES(?,?,?,?,?)",
                       (user_id, username, self._password_hash(password, salt), salt, now_iso()))
            token = secrets.token_urlsafe(32)
            expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
            db.execute("INSERT INTO user_sessions VALUES(?,?,?)", (self.hash_token(token), user_id, expires))
        return user_id, token

    def user_id_by_username(self, username: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT user_id FROM users WHERE username=?", (username,)).fetchone()
        return row["user_id"] if row else None

    def bind_device(self, user_id: str, device_id: str, nickname: str) -> None:
        if not self.device_exists(device_id):
            raise ValueError("设备不存在，请先开机配网注册")
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO device_bindings VALUES(?,?,?)", (user_id, device_id, nickname))

    def unbind_device(self, user_id: str, device_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM device_bindings WHERE user_id=? AND device_id=?", (user_id, device_id))

    def share_device(self, owner_id: str, device_id: str, target_username: str, role: str) -> None:
        target = self.user_id_by_username(target_username)
        if not target:
            raise ValueError("对方账号不存在")
        if target == owner_id:
            raise ValueError("不能分享给自己")
        with self.connect() as db:
            existing = db.execute("SELECT nickname FROM device_bindings WHERE user_id=? AND device_id=?",
                                 (owner_id, device_id)).fetchone()
            nickname = existing["nickname"] if existing else "共享设备"
            db.execute("INSERT OR REPLACE INTO device_bindings VALUES(?,?,?)", (target, device_id, nickname))

    def consume_claim(self, code: str, user_id: str, nickname: str) -> str:
        with self.connect() as db:
            row = db.execute("SELECT device_id FROM device_claims WHERE code=?", (code.strip().upper(),)).fetchone()
            if not row:
                raise ValueError("配对码无效或已失效")
            device_id = row["device_id"]
            db.execute("INSERT OR REPLACE INTO device_bindings VALUES(?,?,?)", (user_id, device_id, nickname or "我的小悟双屏"))
        return device_id

    def set_teacher_auth(self, device_id: str, frozen: bool, actor: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO teacher_auth(device_id,frozen,frozen_by,frozen_at) VALUES(?,?,?,?) "
                "ON CONFLICT(device_id) DO UPDATE SET frozen=excluded.frozen, frozen_by=excluded.frozen_by, frozen_at=excluded.frozen_at",
                (device_id, int(bool(frozen)), actor, now_iso()),
            )

    def get_teacher_auth(self, device_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM teacher_auth WHERE device_id=?", (device_id,)).fetchone()
        return dict(row) if row else None

    def get_user_profile(self, user_id: str, category: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM user_profiles WHERE user_id=? AND category=?", (user_id, category)).fetchone()
        return json.loads(row["data"]) if row else None

    def set_user_profile(self, user_id: str, category: str, data: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO user_profiles(user_id,category,data,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id,category) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (user_id, category, json.dumps(data, ensure_ascii=False), now_iso()),
            )

    def get_service_config(self) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT value FROM service_configs WHERE key='service_config'").fetchone()
        return json.loads(row["value"]) if row else {}

    def set_service_config(self, data: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO service_configs(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                ("service_config", json.dumps(data, ensure_ascii=False), now_iso()),
            )

    def create_teacher_code(self, device_id: str, auth_name: str, permission: str, created_by: str, validity_days: int) -> dict[str, Any]:
        import secrets as _secrets
        code = _secrets.token_hex(3).upper()
        expire_at = None
        if validity_days and validity_days > 0:
            expire_at = (datetime.now(UTC) + timedelta(days=validity_days)).isoformat()
        with self.connect() as db:
            cur = db.execute(
                "INSERT INTO teacher_codes(device_id,auth_code,auth_name,permission,created_by,created_at,expire_at,frozen) VALUES(?,?,?,?,?,?,?,0)",
                (device_id, code, auth_name, permission, created_by, now_iso(), expire_at),
            )
            rid = cur.lastrowid
            row = db.execute("SELECT * FROM teacher_codes WHERE id=?", (rid,)).fetchone()
        return self._teacher_row(dict(row))

    def list_teacher_codes(self, device_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM teacher_codes WHERE device_id=? ORDER BY id DESC", (device_id,)).fetchall()
        return [self._teacher_row(dict(r)) for r in rows]

    def freeze_teacher_code(self, device_id: str, auth_code: str, frozen: bool) -> None:
        with self.connect() as db:
            db.execute("UPDATE teacher_codes SET frozen=? WHERE device_id=? AND auth_code=?",
                       (int(bool(frozen)), device_id, auth_code))

    @staticmethod
    def _teacher_row(r: dict[str, Any]) -> dict[str, Any]:
        expired = bool(r["expire_at"]) and r["expire_at"] < now_iso()
        return {
            "id": r["id"], "auth_code": r["auth_code"], "auth_name": r["auth_name"],
            "permission": r["permission"], "created_at": r["created_at"], "expire_time": r["expire_at"],
            "is_frozen": bool(r["frozen"]), "expired": expired,
        }

    # ——— 微信用户（小程序一键登录，openid↔账号映射）———
    def get_or_create_wechat_user(self, openid: str) -> tuple[str, str, bool]:
        with self.connect() as db:
            row = db.execute("SELECT user_id FROM wechat_openid_map WHERE openid=?", (openid,)).fetchone()
            if row:
                uid = row["user_id"]
                u = db.execute("SELECT username FROM users WHERE user_id=?", (uid,)).fetchone()
                return uid, (u["username"] if u else ("wx_" + openid[-12:])), False
            username = "wx_" + openid[-12:]
            uid = secrets.token_hex(8)
            pwd = secrets.token_urlsafe(16)
            db.execute("INSERT OR REPLACE INTO users VALUES(?,?,?,?,?)",
                       (uid, username, self._password_hash(pwd, secrets.token_hex(8)), secrets.token_hex(8), now_iso()))
            db.execute("INSERT OR REPLACE INTO wechat_openid_map(openid,user_id,created_at) VALUES(?,?,?)",
                       (openid, uid, now_iso()))
            return uid, username, True

    # ——— 推送记录（老师快捷推送作业/便签）———
    def save_push(self, device_id: str, ptype: str, sender_type: str, sender_name: str,
                  content: str, is_append: bool, count: int) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO push_record(device_id,push_type,sender_type,sender_name,content,is_append,item_count,create_time) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (device_id, ptype, sender_type, sender_name, content[:2000], int(bool(is_append)), count, now_iso()),
            )

    def get_push_history(self, device_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,push_type,sender_type,sender_name,content,is_append,item_count,create_time "
                "FROM push_record WHERE device_id=? ORDER BY id DESC LIMIT ?", (device_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_push(self, device_id: str, push_id: int) -> bool:
        with self.connect() as db:
            cur = db.execute("DELETE FROM push_record WHERE device_id=? AND id=?", (device_id, push_id))
            return cur.rowcount > 0

    # ——— pending_refresh（A 屏立即同步信号）———
    def set_pending_refresh(self, device_id: str, value: bool) -> None:
        with self.connect() as db:
            db.execute("UPDATE devices SET pending_refresh=? WHERE device_id=?", (int(bool(value)), device_id))

    def get_pending_refresh(self, device_id: str) -> int:
        with self.connect() as db:
            row = db.execute("SELECT pending_refresh FROM devices WHERE device_id=?", (device_id,)).fetchone()
        return int(row["pending_refresh"]) if row else 0

    def template(self, template_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM templates WHERE template_id=? AND enabled=1", (template_id,)).fetchone()
        if not row:
            return None
        return {**dict(row), "schema_definition": json.loads(row["schema_definition"])}

    def create_developer_app(self, name: str, scopes: list[str]) -> tuple[str, str]:
        app_id, token = "app-" + secrets.token_hex(6), secrets.token_urlsafe(36)
        with self.connect() as db:
            db.execute("INSERT INTO developer_apps VALUES(?,?,?,?,?,?)",
                       (app_id, name, self.hash_token(token), json.dumps(scopes), "active", now_iso()))
        return app_id, token

    def authenticate_app(self, token: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT app_id,name,scopes,status FROM developer_apps WHERE token_hash=?",
                             (self.hash_token(token),)).fetchone()
        if not row or row["status"] != "active":
            return None
        return {**dict(row), "scopes": json.loads(row["scopes"])}

    def list_developer_apps(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT app_id,name,scopes,status,created_at FROM developer_apps ORDER BY created_at DESC").fetchall()
        return [{**dict(row), "scopes": json.loads(row["scopes"])} for row in rows]

    def create_webhook(self, app_id: str, url: str, events: list[str]) -> tuple[str, str]:
        webhook_id, secret = "wh-" + secrets.token_hex(6), secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("INSERT INTO webhooks VALUES(?,?,?,?,?,?,?)",
                       (webhook_id, app_id, url, json.dumps(events), secret, 1, now_iso()))
        return webhook_id, secret

    def list_webhooks(self, app_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT id,url,events,enabled,created_at FROM webhooks WHERE app_id=? ORDER BY created_at DESC",
                              (app_id,)).fetchall()
        return [{**dict(row), "events": json.loads(row["events"]), "enabled": bool(row["enabled"])} for row in rows]

    def record_device_event(self, device_id: str, event_type: str, data: dict[str, Any], occurred_at: str | None) -> str:
        event_id, received = "evt-" + secrets.token_hex(8), now_iso()
        payload = {"id": event_id, "device_id": device_id, "type": event_type,
                   "data": data, "occurred_at": occurred_at or received, "received_at": received}
        with self.connect() as db:
            db.execute("INSERT INTO device_events VALUES(?,?,?,?,?,?)",
                       (event_id, device_id, event_type, json.dumps(data, ensure_ascii=False), payload["occurred_at"], received))
            rows = db.execute("SELECT id,events FROM webhooks WHERE enabled=1").fetchall()
            for row in rows:
                if "device.event" in json.loads(row["events"]):
                    db.execute("INSERT INTO webhook_outbox VALUES(?,?,?,?,?,?,?,NULL)",
                               ("out-" + secrets.token_hex(8), row["id"], "device.event",
                                json.dumps(payload, ensure_ascii=False), "pending", 0, received))
        return event_id

    def list_device_events(self, device_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM device_events WHERE device_id=? ORDER BY received_at DESC LIMIT ?",
                              (device_id, limit)).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def webhook_outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT id,webhook_id,event_type,status,attempts,created_at,delivered_at FROM webhook_outbox ORDER BY created_at DESC LIMIT ?",
                              (limit,)).fetchall()
        return [dict(row) for row in rows]

    # ——— 设备令牌（固件领令牌 + 校验）———
    def mint_device_token(self, device_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO device_tokens(device_id,token_hash,created_at) VALUES(?,?,?)",
                       (device_id, self.hash_token(token), now_iso()))
        return token

    def verify_device_token(self, device_id: str, token: str) -> bool:
        if not token:
            return False
        with self.connect() as db:
            row = db.execute("SELECT token_hash FROM device_tokens WHERE device_id=?", (device_id,)).fetchone()
        if not row:
            return False
        return self.hash_token(token) == row["token_hash"]

    def unbind_device_self(self, device_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM device_bindings WHERE device_id=?", (device_id,))
            db.execute("DELETE FROM device_tokens WHERE device_id=?", (device_id,))

    def report_runtime(self, device_id: str, mode: str, awake: bool) -> None:
        with self.connect() as db:
            db.execute("UPDATE devices SET runtime_mode=?, last_seen_at=? WHERE device_id=?",
                       (mode, now_iso(), device_id))

    def get_teacher_code(self, device_id: str, auth_code: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM teacher_codes WHERE device_id=? AND auth_code=?",
                             (device_id, auth_code.strip().upper())).fetchone()
        if not row:
            return None
        return self._teacher_row(dict(row))

    # ——— 留言（文本 / 语音）———
    @staticmethod
    def _message_row(row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        # 已播放（=已读）状态：语音听过、文本读过都算
        item["played"] = bool(item.get("played_at"))
        item["duration"] = round(float(item["duration"] or 0), 1)
        if item["audio_id"]:
            item["audio_url"] = f"/api/messages/{item['id']}/audio"
        else:
            item["audio_url"] = ""
        return item

    def create_message(self, device_id: str, sender_name: str, kind: str, text: str = "",
                       audio_id: str = "", audio_mime: str = "", duration: float = 0,
                       sender_type: str = "member", sender_id: str | None = None,
                       source: str = "console") -> dict[str, Any]:
        msg_id = secrets.token_hex(10)
        created = now_iso()
        with self.connect() as db:
            db.execute(
                "INSERT INTO messages(id,device_id,sender_name,sender_type,sender_id,kind,text,"
                "audio_id,audio_mime,duration,played_at,created_at,source) VALUES(?,?,?,?,?,?,?,?,?,?,NULL,?,?)",
                (msg_id, device_id, (sender_name or "匿名").strip()[:24], sender_type, sender_id,
                 "voice" if kind == "voice" else "text", (text or "").strip()[:500],
                 audio_id, audio_mime, float(duration or 0), created, source),
            )
            row = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        return self._message_row(dict(row))

    def list_messages(self, device_id: str, limit: int = 50, unread_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM messages WHERE device_id=?"
        if unread_only:
            sql += " AND played_at IS NULL"
        sql += " ORDER BY created_at DESC LIMIT ?"
        with self.connect() as db:
            rows = db.execute(sql, (device_id, limit)).fetchall()
        return [self._message_row(dict(r)) for r in rows]

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        return self._message_row(dict(row)) if row else None

    def count_unread_messages(self, device_id: str) -> int:
        with self.connect() as db:
            row = db.execute("SELECT COUNT(*) AS c FROM messages WHERE device_id=? AND played_at IS NULL",
                             (device_id,)).fetchone()
        return int(row["c"] or 0)

    def mark_message_played(self, device_id: str, message_id: str) -> bool:
        with self.connect() as db:
            cur = db.execute("UPDATE messages SET played_at=? WHERE id=? AND device_id=? AND played_at IS NULL",
                             (now_iso(), message_id, device_id))
        return cur.rowcount > 0

    def mark_all_messages_played(self, device_id: str) -> int:
        with self.connect() as db:
            cur = db.execute("UPDATE messages SET played_at=? WHERE device_id=? AND played_at IS NULL",
                             (now_iso(), device_id))
        return cur.rowcount

    def delete_message(self, device_id: str, message_id: str) -> bool:
        with self.connect() as db:
            cur = db.execute("DELETE FROM messages WHERE id=? AND device_id=?", (message_id, device_id))
        return cur.rowcount > 0


    # ——— 用户级连接器绑定（OAuth 凭据入保险库）———
    def save_connector_binding(self, user_id: str, connector_id: str, credential_encrypted: str,
                               external_account: str, scopes: list[str],
                               expires_at: str | None = None) -> str:
        ts = now_iso()
        with self.connect() as db:
            db.execute(
                "INSERT INTO user_connectors(user_id,connector_id,credential_encrypted,external_account,"
                "scopes,status,expires_at,updated_at) VALUES(?,?,?,?,?,'active',?,?) "
                "ON CONFLICT(user_id,connector_id) DO UPDATE SET credential_encrypted=excluded.credential_encrypted,"
                "external_account=excluded.external_account, scopes=excluded.scopes, status='active',"
                "expires_at=excluded.expires_at, updated_at=excluded.updated_at",
                (user_id, connector_id, credential_encrypted, external_account,
                 json.dumps(scopes, ensure_ascii=False), expires_at, ts),
            )
        return ts

    def get_connector_binding(self, user_id: str, connector_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM user_connectors WHERE user_id=? AND connector_id=?",
                             (user_id, connector_id)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["scopes"] = json.loads(item["scopes"])
        return item

    def list_connector_bindings(self, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM user_connectors WHERE user_id=? ORDER BY updated_at DESC",
                              (user_id,)).fetchall()
        return [{**dict(r), "scopes": json.loads(r["scopes"])} for r in rows]

    def delete_connector_binding(self, user_id: str, connector_id: str) -> bool:
        with self.connect() as db:
            cur = db.execute("DELETE FROM user_connectors WHERE user_id=? AND connector_id=?",
                             (user_id, connector_id))
        return cur.rowcount > 0

    def create_connector_state(self, user_id: str, connector_id: str, ttl_seconds: int = 600) -> str:
        state = secrets.token_urlsafe(24)
        expires = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
        with self.connect() as db:
            db.execute("INSERT INTO connector_auth_states VALUES(?,?,?,?,?)",
                       (state, user_id, connector_id, now_iso(), expires))
        return state

    def consume_connector_state(self, state: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM connector_auth_states WHERE state=?", (state,)).fetchone()
            if row:
                db.execute("DELETE FROM connector_auth_states WHERE state=?", (state,))
        if not row or row["expires_at"] < now_iso():
            return None
        return {"user_id": row["user_id"], "connector_id": row["connector_id"]}


DEFAULT_TEMPLATES = [
    {"template_id": "notice-card", "name": "通用通知", "category": "general", "schema_definition": {"required": ["title", "content"]}},
    {"template_id": "homework-list", "name": "今日作业", "category": "education", "schema_definition": {"required": ["title", "items"]}},
    {"template_id": "vocabulary-card", "name": "单词学习", "category": "education", "schema_definition": {"required": ["word", "meaning"]}},
    {"template_id": "care-reminder", "name": "照护提醒", "category": "elder-care", "schema_definition": {"required": ["title", "time", "content"], "notice": "仅作提醒，不构成医疗建议"}},
    {"template_id": "daily-schedule", "name": "今日安排", "category": "elder-care", "schema_definition": {"required": ["title", "items"]}},
]
