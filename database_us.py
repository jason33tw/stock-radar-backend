"""
美股資料庫 Model
與台股共用同一個 engine，額外新增兩張 table：
  - us_stocks       : 股票基本資料
  - us_stock_daily  : 每日行情 + 技術指標 + 評分
"""
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, Date
from sqlalchemy.sql import func
from datetime import datetime

from database import Base, engine


class UsStock(Base):
    __tablename__ = "us_stocks"

    id         = Column(Integer, primary_key=True, index=True)
    symbol     = Column(String(20), unique=True, index=True, nullable=False)
    name       = Column(String(100), nullable=False)
    sector     = Column(String(50))
    is_active  = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UsStockDaily(Base):
    __tablename__ = "us_stock_daily"

    id     = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), index=True, nullable=False)
    date   = Column(Date, index=True, nullable=False)

    # 價格
    open_price    = Column(Float)
    close_price   = Column(Float)
    high_price    = Column(Float)
    low_price     = Column(Float)
    change_pct    = Column(Float)   # 漲跌幅 %

    # 成交量
    volume        = Column(Float)
    avg_volume20  = Column(Float)
    volume_ratio  = Column(Float)   # 今日量 / 20日均量

    # 技術指標
    ma5           = Column(Float)
    ma20          = Column(Float)
    ma60          = Column(Float)
    is_52w_high   = Column(Boolean, default=False)   # 創52週新高
    is_60d_high   = Column(Boolean, default=False)   # 創60日新高
    rsi14         = Column(Float)                    # RSI(14)

    # 法人/機構面（Yahoo Finance 可取得的欄位）
    inst_own_pct      = Column(Float)   # 機構持股比例 %
    short_ratio       = Column(Float)   # 空頭比例（Short Ratio）
    short_pct_float   = Column(Float)   # 融券佔流通股 %

    # 評分
    score           = Column(Integer, default=0)
    score_breakdown = Column(Text)   # JSON string

    created_at = Column(DateTime, default=datetime.utcnow)


async def init_us_db():
    """建立美股相關 table（如果不存在）"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
