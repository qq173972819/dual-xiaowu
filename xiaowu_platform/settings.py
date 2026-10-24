import os
from pathlib import Path

class Settings:
    def __init__(self) -> None:
        self.dev_mode = os.getenv("XIAOWU_DEV_MODE", "true").lower() in {"1", "true", "yes"}
        self.database_path = Path(os.getenv("XIAOWU_DATABASE_PATH", "data/xiaowu.db"))
        self.admin_key = os.getenv("XIAOWU_ADMIN_KEY", "")
        self.admin_username = os.getenv("XIAOWU_ADMIN_USERNAME", "").strip()
        self.admin_password = os.getenv("XIAOWU_ADMIN_PASSWORD", "")
        self.default_app_key = os.getenv("XIAOWU_DEFAULT_APP_KEY", "dev-app-key")
        self.demo_user_token = os.getenv("XIAOWU_DEMO_USER_TOKEN", "demo-user-token")
        # 连接器凭据保险库主密钥：生产必须显式设置；开发模式从 admin_key 派生，保证本地可跑
        self.connector_master_key = os.getenv("XIAOWU_CONNECTOR_MASTER_KEY") or f"derived:{self.admin_key}"
        # 对外可达的基础地址（OAuth 回调 redirect_uri 用），生产需改为公网域名
        self.public_base_url = os.getenv("XIAOWU_PUBLIC_BASE_URL", "http://127.0.0.1:8104")


settings = Settings()
