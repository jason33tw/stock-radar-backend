from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, Date
from sqlalchemy.sql import func
from datetime import datetime
from config import settings

# Convert sqlite:/// to sqlite+aiosqlite:///
db_url = settings.DATABASE_URL.replace("sqlite:///", "sqlite+aiosqlite:///")
engine = create_async_engine(db_url, echo=settings.DEBUG)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(String(10), unique=True, index=True, nullable=False)
    stock_name = Column(String(50), nullable=False)
    industry = Column(String(50))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StockDailyData(Base):
    __tablename__ = "stock_daily_data"

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(String(10), index=True, nullable=False)
    date = Column(Date, index=True, nullable=False)

    # 價格資料
    open_price = Column(Float)
    close_price = Column(Float)
    high_price = Column(Float)
    low_price = Column(Float)
    change = Column(Float)
    change_percent = Column(Float)

    # 成交量
    volume = Column(Float)
    turnover = Column(Float)

    # 技術指標
    ma5 = Column(Float)
    ma20 = Column(Float)
    ma60 = Column(Float)
    is_60d_high = Column(Boolean, default=False)
    volume_ma20 = Column(Float)
    volume_ratio = Column(Float)  # 今日量 / 20日均量

    # 法人籌碼
    foreign_buy = Column(Float, default=0)    # 外資買超
    foreign_sell = Column(Float, default=0)   # 外資賣超
    foreign_net = Column(Float, default=0)    # 外資買賣超
    foreign_consecutive_buy = Column(Integer, default=0)  # 外資連買天數

    trust_buy = Column(Float, default=0)      # 投信買超
    trust_sell = Column(Float, default=0)     # 投信賣超
    trust_net = Column(Float, default=0)      # 投信買賣超
    trust_consecutive_buy = Column(Integer, default=0)  # 投信連買天數

    dealer_buy = Column(Float, default=0)     # 自營商買超
    dealer_sell = Column(Float, default=0)    # 自營商賣超
    dealer_net = Column(Float, default=0)     # 自營商買賣超

    # 融資融券
    margin_purchase = Column(Float, default=0)   # 融資餘額
    margin_change = Column(Float, default=0)     # 融資增減

    # 評分
    score = Column(Integer, default=0)
    score_breakdown = Column(Text)  # JSON string

    created_at = Column(DateTime, default=datetime.utcnow)


class UpdateLog(Base):
    __tablename__ = "update_logs"

    id = Column(Integer, primary_key=True, index=True)
    update_date = Column(Date, index=True)
    status = Column(String(20))  # success, failed, running
    stocks_updated = Column(Integer, default=0)
    message = Column(Text)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
