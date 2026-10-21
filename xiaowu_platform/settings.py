import os
from pathlib import Path

class Settings:
    def __init__(self) -> None:
        self.dev_mode = os.getenv("XIAOWU_DEV_MODE", "true").lower() in {"1", "true", "yes"}
        self.database_path = Path(os.getenv("XIAOWU_DATABASE_PATH", "data/xiaowu.db"))
        self.admin_key = os.getenv("XIAOWU_ADMIN_KEY", "dev-admin-key")
        self.default_app_key = os.getenv("XIAOWU_DEFAULT_APP_KEY", "dev-app-key")
        self.demo_user_token = os.getenv("XIAOWU_DEMO_USER_TOKEN", "demo-user-token")


settings = Settings()
