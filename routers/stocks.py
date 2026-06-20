from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, func
from typing import List, Optional
from datetime import date, datetime
import json

from database import get_db, Stock, StockDailyData
from config import settings

router = APIRouter()


def fmt(v, decimals=2):
    if v is None:
        return None
    try:
        return round(float(v), decimals)
    except:
        return None


@router.get("/list")
async def get_stock_list(db: AsyncSession = Depends(get_db)):
    """取得所有股票清單"""
    result = await db.execute(
        select(Stock).where(Stock.is_active == True).order_by(Stock.stock_id)
    )
    stocks = result.scalars().all()
    return [{"stock_id": s.stock_id, "stock_name": s.stock_name, "industry": s.industry}
            for s in stocks]


@router.get("/top")
async def get_top_stocks(
    limit: int = Query(default=20, le=50),
    trade_date: Optional[str] = None,
    min_score: int = Query(default=0),
    db: AsyncSession = Depends(get_db)
):
    """取得評分最高的股票 TOP N"""
    # 取得最新有資料的日期
    if trade_date:
        try:
            target_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
        except:
            raise HTTPException(status_code=400, detail="日期格式錯誤")
    else:
        result = await db.execute(
            select(func.max(StockDailyData.date))
        )
        target_date = result.scalar_one_or_none()
        if not target_date:
            return {"date": None, "stocks": []}

    result = await db.execute(
        select(StockDailyData, Stock.stock_name)
        .join(Stock, Stock.stock_id == StockDailyData.stock_id)
        .where(
            and_(
                StockDailyData.date == target_date,
                StockDailyData.score >= min_score,
            )
        )
        .order_by(desc(StockDailyData.score))
        .limit(limit)
    )
    rows = result.all()

    stocks = []
    for row, stock_name in rows:
        breakdown = {}
        if row.score_breakdown:
            try:
                breakdown = json.loads(row.score_breakdown)
            except:
                pass

        stocks.append({
            "stock_id": row.stock_id,
            "stock_name": stock_name,
            "date": row.date.isoformat(),
            "close_price": fmt(row.close_price),
            "volume": fmt(row.volume, 0),
            "volume_ratio": fmt(row.volume_ratio),
            "ma5": fmt(row.ma5),
            "ma20": fmt(row.ma20),
            "ma60": fmt(row.ma60),
            "is_60d_high": row.is_60d_high,
            "foreign_net": fmt(row.foreign_net, 0),
            "foreign_consecutive_buy": row.foreign_consecutive_buy,
            "trust_net": fmt(row.trust_net, 0),
            "trust_consecutive_buy": row.trust_consecutive_buy,
            "dealer_net": fmt(row.dealer_net, 0),
            "margin_change": fmt(row.margin_change, 0),
            "score": row.score,
            "score_breakdown": breakdown,
        })

    return {"date": target_date.isoformat(), "stocks": stocks}


@router.get("/{stock_id}")
async def get_stock_detail(
    stock_id: str,
    days: int = Query(default=60, le=250),
    db: AsyncSession = Depends(get_db)
):
    """取得股票詳細資料"""
    result = await db.execute(select(Stock).where(Stock.stock_id == stock_id))
    stock = result.scalar_one_or_none()
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")

    result = await db.execute(
        select(StockDailyData)
        .where(StockDailyData.stock_id == stock_id)
        .order_by(desc(StockDailyData.date))
        .limit(days)
    )
    daily_data = result.scalars().all()
    daily_data = list(reversed(daily_data))

    history = []
    for row in daily_data:
        breakdown = {}
        if row.score_breakdown:
            try:
                breakdown = json.loads(row.score_breakdown)
            except:
                pass
        history.append({
            "date": row.date.isoformat(),
            "open": fmt(row.open_price),
            "close": fmt(row.close_price),
            "high": fmt(row.high_price),
            "low": fmt(row.low_price),
            "volume": fmt(row.volume, 0),
            "volume_ratio": fmt(row.volume_ratio),
            "ma5": fmt(row.ma5),
            "ma20": fmt(row.ma20),
            "ma60": fmt(row.ma60),
            "is_60d_high": row.is_60d_high,
            "foreign_net": fmt(row.foreign_net, 0),
            "foreign_consecutive_buy": row.foreign_consecutive_buy,
            "trust_net": fmt(row.trust_net, 0),
            "trust_consecutive_buy": row.trust_consecutive_buy,
            "dealer_net": fmt(row.dealer_net, 0),
            "margin_purchase": fmt(row.margin_purchase, 0),
            "margin_change": fmt(row.margin_change, 0),
            "score": row.score,
            "score_breakdown": breakdown,
        })

    latest = history[-1] if history else {}

    return {
        "stock_id": stock.stock_id,
        "stock_name": stock.stock_name,
        "industry": stock.industry,
        "latest": latest,
        "history": history,
    }


@router.get("/search/query")
async def search_stocks(
    q: str = Query(min_length=1),
    db: AsyncSession = Depends(get_db)
):
    """搜尋股票"""
    result = await db.execute(
        select(Stock)
        .where(
            and_(
                Stock.is_active == True,
                (Stock.stock_id.contains(q) | Stock.stock_name.contains(q))
            )
        )
        .limit(20)
    )
    stocks = result.scalars().all()
    return [{"stock_id": s.stock_id, "stock_name": s.stock_name} for s in stocks]
