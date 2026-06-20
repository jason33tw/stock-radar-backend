from pydantic_settings import BaseSettings
from pathlib import Path
import os

# 找到 .env 的絕對路徑 — 不管從哪個目錄啟動都能找到
_HERE = Path(__file__).parent.resolve()
_ENV_FILE = _HERE / ".env"


class Settings(BaseSettings):
    # FinMind API
    FINMIND_API_TOKEN: str = ""
    FINMIND_API_URL: str = "https://api.finmindtrade.com/api/v4/data"

    # Database — 預設放在 backend/ 同層 (本機開發用，正式環境會由 DATABASE_URL 環境變數覆蓋)
    DATABASE_URL: str = f"sqlite:///{_HERE / 'taiwan_stock_radar.db'}"

    # LINE Messaging API
    LINE_CHANNEL_ACCESS_TOKEN: str = ""
    LINE_TARGET_ID: str = ""

    # App
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    DEBUG: bool = False

    UPDATE_HOUR: int = 15
    UPDATE_MINUTE: int = 30
    MIN_SCORE_FOR_NOTIFY: int = 50
    TOP_N_STOCKS: int = 20

    class Config:
        env_file = str(_ENV_FILE)          # 絕對路徑，不受 cwd 影響
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
