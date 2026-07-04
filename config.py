from pydantic_settings import BaseSettings
from pathlib import Path
import os

# 找到 .env 的絕對路徑 — 不管從哪個目錄啟動都能找到
_HERE = Path(__file__).parent.resolve()
_ENV_FILE = _HERE / ".env"


class Settings(BaseSettings):
    # FinMind API
    FINMIND_API_TOKEN: str = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiamFzb24zM3R3QGdtYWlsLmNvbSIsImVtYWlsIjoiamFzb24zM3R3QGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjF9.oKTBWYhhRRLFpU5EL_JP2AOFQ3PG8wY9LfD4NGoiilw"
    FINMIND_API_URL: str = "https://api.finmindtrade.com/api/v4/data"

    # Database — 預設放在 backend/ 同層
    DATABASE_URL: str = f"sqlite:///{_HERE / 'taiwan_stock_radar.db'}"

    # LINE Messaging API
    LINE_CHANNEL_ACCESS_TOKEN: str = "LgjH/BkjMV///wifI6ZuewRfKPs3BMROU2TtSyUSE86aiePSwFHjWqQttFy9Ub4PTjLNeTDC6psy1NcZgC8RS61KYnOxMyzlQFO59fN7WRS07sn5IeeXDc2/0xqLrrj/dvPZIraCNmLsEFFfgWyDtAdB04t89/1O/w1cDnyilFU="
    LINE_TARGET_ID: str = ""

    # Alpha Vantage API (美股資料)
    ALPHA_VANTAGE_KEY: str = "ZBNL9JL5RJ6ZJSRD"

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
