"""连接器生态核心：连接器注册表 + 用户凭据保险库。

设计要点（对应 docs/07-connector-development/README.md）：
- 连接器分两类：platform（平台级，平台持有密钥，用户零绑定直接用）
  与 user（用户级，OAuth 授权后凭据入保险库）。
- 用户凭据使用 Fernet 对称加密（信封加密的单层简化版）：主密钥来自
  环境变量 XIAOWU_CONNECTOR_MASTER_KEY；开发模式缺省时从 admin_key
  派生，保证本地可跑、生产必须显式配置。
- 凭据只在服务端内存中解密使用，接口永远不回传明文。
"""
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class ConnectorRegistry:
    """加载 connectors/<id>/connector.yaml（JSON 语法，与 plugins 同约定）。"""

    def __init__(self, directory: Path):
        self.directory = directory
        self._cache: dict[str, dict[str, Any]] | None = None

    def load(self) -> dict[str, dict[str, Any]]:
        if self._cache is None:
            items: dict[str, dict[str, Any]] = {}
            if self.directory.exists():
                for manifest in sorted(self.directory.glob("*/connector.yaml")):
                    try:
                        data = json.loads(manifest.read_text(encoding="utf-8"))
                    except (ValueError, OSError):
                        continue
                    if isinstance(data, dict) and data.get("id"):
                        items[data["id"]] = data
            self._cache = items
        return self._cache

    def get(self, connector_id: str) -> dict[str, Any] | None:
        return self.load().get(connector_id)

    def public_dict(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """对外展示字段（不含 client_secret 等敏感项）。"""
        auth = manifest.get("auth") or {}
        return {
            "id": manifest["id"],
            "name": manifest.get("name", manifest["id"]),
            "version": manifest.get("version", "0.1.0"),
            "type": manifest.get("type", "user"),
            "category": manifest.get("category", "general"),
            "description": manifest.get("description", ""),
            "auth_kind": auth.get("kind", "oauth2") if manifest.get("type") == "user" else "none",
            "scopes": auth.get("scopes", []),
            "guide": manifest.get("guide", []),
            "permissions": manifest.get("permissions", []),
            "risk": manifest.get("risk", "low"),
        }


class CredentialVault:
    """Fernet 凭据保险库：encrypt(dict) → 密文字符串，decrypt 回 dict。"""

    def __init__(self, master_key: str):
        digest = hashlib.sha256(master_key.encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, credential: dict[str, Any]) -> str:
        payload = json.dumps(credential, ensure_ascii=False).encode()
        return self._fernet.encrypt(payload).decode()

    def decrypt(self, token: str) -> dict[str, Any]:
        try:
            return json.loads(self._fernet.decrypt(token.encode()))
        except (InvalidToken, ValueError) as exc:
            raise ValueError("凭据解密失败：主密钥与写入时不一致或数据已损坏") from exc
