from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, func
from typing import Optional
import json

from database import get_db
from database_us import UsStock, UsStockDaily
from us_update_service import us_update_service

router = APIRouter()


def fmt(v, d=2):
    if v is None:
        return None
    try:
        return round(float(v), d)
    except:
        return None


@router.get("/top")
async def get_us_top_stocks(
    limit: int = Query(default=20, le=50),
    min_score: int = Query(default=0),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(func.max(UsStockDaily.date)))
    target_date = result.scalar_one_or_none()
    if not target_date:
        return {"date": None, "stocks": []}

    rows = await db.execute(
        select(UsStockDaily, UsStock.name, UsStock.sector)
        .join(UsStock, UsStock.symbol == UsStockDaily.symbol)
        .where(and_(
            UsStockDaily.date == target_date,
            UsStockDaily.score >= min_score,
        ))
        .order_by(desc(UsStockDaily.score))
        .limit(limit)
    )
    rows = rows.all()

    stocks = []
    for row, name, sector in rows:
        bd = {}
        try:
            bd = json.loads(row.score_breakdown or "{}")
        except:
            pass
        stocks.append({
            "symbol":         row.symbol,
            "name":           name,
            "sector":         sector,
            "date":           row.date.isoformat(),
            "close_price":    fmt(row.close_price),
            "change_pct":     fmt(row.change_pct),
            "volume_ratio":   fmt(row.volume_ratio),
            "ma5":            fmt(row.ma5),
            "ma20":           fmt(row.ma20),
            "ma60":           fmt(row.ma60),
            "is_60d_high":    row.is_60d_high,
            "is_52w_high":    row.is_52w_high,
            "rsi14":          fmt(row.rsi14),
            "inst_own_pct":   fmt(row.inst_own_pct),
            "short_ratio":    fmt(row.short_ratio),
            "score":          row.score,
            "score_breakdown": bd,
        })

    return {"date": target_date.isoformat(), "stocks": stocks}


@router.get("/update/status")
async def get_us_update_status():
    return {"is_running": us_update_service.is_running}


@router.post("/update")
async def trigger_us_update(background_tasks: BackgroundTasks):
    if us_update_service.is_running:
        return {"status": "already_running", "message": "美股更新已在進行中"}
    background_tasks.add_task(us_update_service.run_daily_update)
    return {"status": "started", "message": "美股資料更新已開始，約需 1-2 分鐘"}


@router.get("/{symbol}")
async def get_us_stock_detail(
    symbol: str,
    db: AsyncSession = Depends(get_db),
):
    symbol = symbol.upper()
    result = await db.execute(select(UsStock).where(UsStock.symbol == symbol))
    stock = result.scalar_one_or_none()
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")

    result = await db.execute(
        select(UsStockDaily)
        .where(UsStockDaily.symbol == symbol)
        .order_by(desc(UsStockDaily.date))
        .limit(120)
    )
    daily = list(reversed(result.scalars().all()))

    history = []
    for row in daily:
        bd = {}
        try:
            bd = json.loads(row.score_breakdown or "{}")
        except:
            pass
        history.append({
            "date":         row.date.isoformat(),
            "open":         fmt(row.open_price),
            "close":        fmt(row.close_price),
            "high":         fmt(row.high_price),
            "low":          fmt(row.low_price),
            "change_pct":   fmt(row.change_pct),
            "volume":       fmt(row.volume, 0),
            "volume_ratio": fmt(row.volume_ratio),
            "ma5":          fmt(row.ma5),
            "ma20":         fmt(row.ma20),
            "ma60":         fmt(row.ma60),
            "is_60d_high":  row.is_60d_high,
            "is_52w_high":  row.is_52w_high,
            "rsi14":        fmt(row.rsi14),
            "inst_own_pct": fmt(row.inst_own_pct),
            "short_ratio":  fmt(row.short_ratio),
            "score":        row.score,
            "score_breakdown": bd,
        })

    latest = history[-1] if history else {}
    return {
        "symbol":  stock.symbol,
        "name":    stock.name,
        "sector":  stock.sector,
        "latest":  latest,
        "history": history,
    }


@router.get("/search/query")
async def search_us_stocks(
    q: str = Query(min_length=1),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UsStock)
        .where(
            and_(
                UsStock.is_active == True,
                (UsStock.symbol.contains(q.upper()) | UsStock.name.contains(q))
            )
        )
        .limit(20)
    )
    return [{"symbol": s.symbol, "name": s.name} for s in result.scalars().all()]


@router.get("/debug/av-test")
async def test_av_connection():
    """測試 Alpha Vantage 連線（診斷用）"""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "TIME_SERIES_DAILY",
                    "symbol": "AAPL",
                    "outputsize": "compact",
                    "apikey": "ZBNL9JL5RJ6ZJSRD",
                },
                timeout=30,
            )
            data = resp.json()
            keys = list(data.keys())
            has_data = "Time Series (Daily)" in data
            dates_count = len(data.get("Time Series (Daily)", {}))
            return {
                "status": "ok" if has_data else "error",
                "response_keys": keys,
                "dates_count": dates_count,
                "raw_preview": str(data)[:500],
            }
    except Exception as e:
        return {"status": "exception", "error": str(e)}
