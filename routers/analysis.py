from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, func
from datetime import date, datetime, timedelta
from typing import Optional
import json

from database import get_db, StockDailyData, Stock, UpdateLog

router = APIRouter()


@router.get("/summary")
async def get_analysis_summary(
    trade_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """取得分析摘要統計"""
    if trade_date:
        try:
            target_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
        except:
            target_date = date.today()
    else:
        result = await db.execute(select(func.max(StockDailyData.date)))
        target_date = result.scalar_one_or_none() or date.today()

    result = await db.execute(
        select(StockDailyData).where(StockDailyData.date == target_date)
    )
    all_data = result.scalars().all()

    if not all_data:
        return {"date": target_date.isoformat(), "total": 0}

    score_dist = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
    high_score = sum(1 for d in all_data if d.score >= 60)
    foreign_buy = sum(1 for d in all_data if (d.foreign_net or 0) > 0)
    trust_buy = sum(1 for d in all_data if (d.trust_net or 0) > 0)
    new_high_60d = sum(1 for d in all_data if d.is_60d_high)

    for d in all_data:
        s = d.score or 0
        if s <= 20: score_dist["0-20"] += 1
        elif s <= 40: score_dist["21-40"] += 1
        elif s <= 60: score_dist["41-60"] += 1
        elif s <= 80: score_dist["61-80"] += 1
        else: score_dist["81-100"] += 1

    return {
        "date": target_date.isoformat(),
        "total_stocks": len(all_data),
        "high_score_count": high_score,
        "foreign_buy_count": foreign_buy,
        "trust_buy_count": trust_buy,
        "new_high_60d_count": new_high_60d,
        "score_distribution": score_dist,
        "avg_score": round(sum(d.score or 0 for d in all_data) / len(all_data), 1),
    }


@router.get("/market-trend")
async def get_market_trend(
    days: int = Query(default=20, le=60),
    db: AsyncSession = Depends(get_db)
):
    """取得市場趨勢 (每日法人買賣超統計)"""
    result = await db.execute(
        select(
            StockDailyData.date,
            func.sum(StockDailyData.foreign_net).label("total_foreign_net"),
            func.sum(StockDailyData.trust_net).label("total_trust_net"),
            func.sum(StockDailyData.dealer_net).label("total_dealer_net"),
            func.count(StockDailyData.stock_id).label("stock_count"),
        )
        .group_by(StockDailyData.date)
        .order_by(desc(StockDailyData.date))
        .limit(days)
    )
    rows = result.all()

    trend = []
    for row in reversed(rows):
        trend.append({
            "date": row.date.isoformat(),
            "foreign_net": round(float(row.total_foreign_net or 0), 0),
            "trust_net": round(float(row.total_trust_net or 0), 0),
            "dealer_net": round(float(row.total_dealer_net or 0), 0),
        })
    return trend


@router.get("/update-logs")
async def get_update_logs(
    limit: int = Query(default=10, le=30),
    db: AsyncSession = Depends(get_db)
):
    """取得更新記錄"""
    result = await db.execute(
        select(UpdateLog).order_by(desc(UpdateLog.started_at)).limit(limit)
    )
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "date": log.update_date.isoformat() if log.update_date else None,
            "status": log.status,
            "stocks_updated": log.stocks_updated,
            "message": log.message,
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "finished_at": log.finished_at.isoformat() if log.finished_at else None,
        }
        for log in logs
    ]
