"""
美股資料更新服務 v4
資料來源：Alpha Vantage API（穩定、不受地區 IP 限制）
免費方案：每分鐘 25 次請求，每天 500 次
評分邏輯：技術面（MA、量比、RSI、動能、新高）
"""
import asyncio
import json
import logging
import time
from datetime import datetime, date
from typing import Optional

import httpx
from sqlalchemy import select, and_

from database import AsyncSessionLocal
from database_us import UsStock, UsStockDaily

logger = logging.getLogger(__name__)

ALPHA_VANTAGE_KEYS = [ "9D50FDF0FBGY7R1H"]
_key_index = 0

def _get_next_key():
    global _key_index
    key = ALPHA_VANTAGE_KEYS[_key_index % len(ALPHA_VANTAGE_KEYS)]
    _key_index += 1
    return key
AV_BASE = "https://www.alphavantage.co/query"

DEFAULT_US_WATCHLIST = [
    ("AAPL",  "蘋果",         "Technology"),
    ("MSFT",  "微軟",         "Technology"),
    ("NVDA",  "輝達",         "Technology"),
    ("GOOGL", "Alphabet",     "Technology"),
    ("META",  "Meta",         "Technology"),
    ("AMZN",  "亞馬遜",       "Consumer"),
    ("TSLA",  "特斯拉",       "Automotive"),
    ("AMD",   "超微",         "Technology"),
    ("AVGO",  "博通",         "Technology"),
    ("QCOM",  "高通",         "Technology"),
    ("INTC",  "英特爾",       "Technology"),
    ("CRM",   "Salesforce",   "Technology"),
    ("ORCL",  "甲骨文",       "Technology"),
    ("ADBE",  "Adobe",        "Technology"),
    ("NOW",   "ServiceNow",   "Technology"),
    ("JPM",   "摩根大通",     "Finance"),
    ("BAC",   "美國銀行",     "Finance"),
    ("GS",    "高盛",         "Finance"),
    ("V",     "Visa",         "Finance"),
    ("MA",    "萬事達",       "Finance"),
    ("LLY",   "禮來",         "Healthcare"),
    ("UNH",   "聯合健康",     "Healthcare"),
    ("JNJ",   "嬌生",         "Healthcare"),
    ("WMT",   "沃爾瑪",       "Consumer"),
    ("COST",  "好市多",       "Consumer"),
    ("NFLX",  "Netflix",      "Communication"),
    ("DIS",   "迪士尼",       "Communication"),
    ("XOM",   "埃克森美孚",   "Energy"),
    ("CVX",   "雪佛龍",       "Energy"),
    ("SPY",   "標普500 ETF",  "ETF"),
    ("QQQ",   "那斯達克 ETF", "ETF"),
    ("SOXX",  "半導體 ETF",   "ETF"),
]

US_SCORE_RULES = {
    "above_ma20":       10,
    "above_ma60":       10,
    "is_60d_high":      15,
    "is_52w_high":      15,
    "volume_ratio_200": 25,
    "volume_ratio_150": 12,
    "rsi_bullish":      15,
    "price_momentum_3": 15,
}


def _calculate_rsi(closes: list, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calculate_us_score(data: dict) -> tuple:
    breakdown = {}
    reasons = []
    total = 0

    close = data.get("close_price", 0) or 0
    ma20  = data.get("ma20", 0) or 0
    ma60  = data.get("ma60", 0) or 0

    if close > 0 and ma20 > 0 and close > ma20:
        pts = US_SCORE_RULES["above_ma20"]
        breakdown["above_ma20"] = pts
        reasons.append(f"站上月線(20MA) (+{pts})")
        total += pts

    if close > 0 and ma60 > 0 and close > ma60:
        pts = US_SCORE_RULES["above_ma60"]
        breakdown["above_ma60"] = pts
        reasons.append(f"站上季線(60MA) (+{pts})")
        total += pts

    if data.get("is_60d_high"):
        pts = US_SCORE_RULES["is_60d_high"]
        breakdown["is_60d_high"] = pts
        reasons.append(f"創60日新高 (+{pts})")
        total += pts

    if data.get("is_52w_high"):
        pts = US_SCORE_RULES["is_52w_high"]
        breakdown["is_52w_high"] = pts
        reasons.append(f"創52週新高 (+{pts})")
        total += pts

    vr = data.get("volume_ratio", 0) or 0
    if vr >= 2.0:
        pts = US_SCORE_RULES["volume_ratio_200"]
        breakdown["volume_ratio_200"] = pts
        reasons.append(f"量增{vr:.1f}x (+{pts})")
        total += pts
    elif vr >= 1.5:
        pts = US_SCORE_RULES["volume_ratio_150"]
        breakdown["volume_ratio_150"] = pts
        reasons.append(f"量增{vr:.1f}x (+{pts})")
        total += pts

    rsi = data.get("rsi14", 0) or 0
    if 50 <= rsi <= 70:
        pts = US_SCORE_RULES["rsi_bullish"]
        breakdown["rsi_bullish"] = pts
        reasons.append(f"RSI多頭區({rsi:.0f}) (+{pts})")
        total += pts

    mom3 = data.get("price_momentum_3", 0) or 0
    if mom3 > 3:
        pts = US_SCORE_RULES["price_momentum_3"]
        breakdown["price_momentum_3"] = pts
        reasons.append(f"3日動能+{mom3:.1f}% (+{pts})")
        total += pts

    total = min(total, 100)
    breakdown["total"] = total
    breakdown["reasons"] = reasons
    return total, json.dumps(breakdown, ensure_ascii=False)


async def _fetch_av(symbol: str, client: httpx.AsyncClient) -> Optional[dict]:
    """用 Alpha Vantage TIME_SERIES_DAILY 抓取日線資料"""
    try:
        resp = await client.get(AV_BASE, params={
            "function":   "TIME_SERIES_DAILY",
            "symbol":     symbol,
            "outputsize": "compact",
            "apikey": _get_next_key(),
        }, timeout=30)

        data = resp.json()

        if "Note" in data:
            logger.warning(f"[{symbol}] AV rate limit: {data['Note']}")
            return None

        if "Information" in data:
            logger.warning(f"[{symbol}] AV info: {data['Information']}")
            return None

        ts = data.get("Time Series (Daily)")
        if not ts:
            logger.error(f"[{symbol}] no time series in response")
            return None

        # 排序日期（最新在前）
        sorted_dates = sorted(ts.keys(), reverse=True)
        if len(sorted_dates) < 20:
            logger.warning(f"[{symbol}] too few data points: {len(sorted_dates)}")
            return None

        # 取最近 252 個交易日
        sorted_dates = sorted_dates[:252]
        # 反轉成由舊到新，方便計算 MA
        sorted_dates.reverse()

        closes  = [float(ts[d]["4. close"])  for d in sorted_dates]
        opens   = [float(ts[d]["1. open"])   for d in sorted_dates]
        highs   = [float(ts[d]["2. high"])   for d in sorted_dates]
        lows    = [float(ts[d]["3. low"])    for d in sorted_dates]
        volumes = [float(ts[d]["5. volume"]) for d in sorted_dates]

        def ma(lst, n):
            if len(lst) < n:
                return None
            return round(sum(lst[-n:]) / n, 4)

        close_today = closes[-1]
        ma5         = ma(closes, 5)
        ma20        = ma(closes, 20)
        ma60        = ma(closes, 60)
        avg_vol20   = ma(volumes, 20)
        vol_today   = volumes[-1]
        vol_ratio   = round(vol_today / avg_vol20, 2) if avg_vol20 else 0
        is_60d_high = close_today >= max(closes[-60:]) if len(closes) >= 60 else False
        is_52w_high = close_today >= max(closes) if len(closes) >= 80 else False
        rsi14       = _calculate_rsi(closes)
        change_pct  = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2) if len(closes) >= 2 else 0
        mom3        = round((closes[-1] - closes[-4]) / closes[-4] * 100, 2) if len(closes) >= 4 else 0

        logger.info(f"[{symbol}] OK close={close_today} vr={vol_ratio} rsi={rsi14}")

        return {
            "symbol":           symbol,
            "date":             sorted_dates[-1],
            "open_price":       round(opens[-1], 4),
            "close_price":      round(close_today, 4),
            "high_price":       round(highs[-1], 4),
            "low_price":        round(lows[-1], 4),
            "change_pct":       change_pct,
            "volume":           vol_today,
            "avg_volume20":     avg_vol20,
            "volume_ratio":     vol_ratio,
            "ma5":              ma5,
            "ma20":             ma20,
            "ma60":             ma60,
            "is_60d_high":      is_60d_high,
            "is_52w_high":      is_52w_high,
            "rsi14":            rsi14,
            "inst_own_pct":     None,
            "short_ratio":      None,
            "short_pct_float":  None,
            "price_momentum_3": mom3,
        }

    except Exception as e:
        logger.error(f"[{symbol}] fetch error: {e}")
        return None


async def _save_one(symbol: str, name: str, sector: str, data: dict) -> bool:
    score, score_bd = calculate_us_score(data)

    async with AsyncSessionLocal() as db:
        try:
            target_date = datetime.strptime(data["date"], "%Y-%m-%d").date()
            result = await db.execute(
                select(UsStockDaily).where(
                    and_(
                        UsStockDaily.symbol == symbol,
                        UsStockDaily.date   == target_date,
                    )
                )
            )
            existing = result.scalar_one_or_none()
            row_data = dict(
                symbol          = symbol,
                date            = target_date,
                open_price      = data["open_price"],
                close_price     = data["close_price"],
                high_price      = data["high_price"],
                low_price       = data["low_price"],
                change_pct      = data["change_pct"],
                volume          = data["volume"],
                avg_volume20    = data["avg_volume20"],
                volume_ratio    = data["volume_ratio"],
                ma5             = data["ma5"],
                ma20            = data["ma20"],
                ma60            = data["ma60"],
                is_60d_high     = data["is_60d_high"],
                is_52w_high     = data["is_52w_high"],
                rsi14           = data["rsi14"],
                inst_own_pct    = data["inst_own_pct"],
                short_ratio     = data["short_ratio"],
                short_pct_float = data["short_pct_float"],
                score           = score,
                score_breakdown = score_bd,
            )
            if existing:
                for k, v in row_data.items():
                    setattr(existing, k, v)
            else:
                db.add(UsStockDaily(**row_data))

            # 確保 us_stocks 基本資料存在
            sr = await db.execute(select(UsStock).where(UsStock.symbol == symbol))
            if not sr.scalar_one_or_none():
                db.add(UsStock(symbol=symbol, name=name, sector=sector))

            await db.commit()
            return True
        except Exception as e:
            logger.error(f"DB save error [{symbol}]: {e}")
            await db.rollback()
            return False


class UsUpdateService:
    def __init__(self):
        self.is_running = False

    async def sync_us_stock_list(self, db) -> int:
        count = 0
        for symbol, name, sector in DEFAULT_US_WATCHLIST:
            result = await db.execute(select(UsStock).where(UsStock.symbol == symbol))
            if not result.scalar_one_or_none():
                db.add(UsStock(symbol=symbol, name=name, sector=sector))
                count += 1
        await db.commit()
        return count

    async def run_daily_update(self) -> dict:
        if self.is_running:
            return {"status": "already_running"}

        self.is_running = True
        success, error = 0, 0

        try:
            # 確保股票清單存在
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(UsStock).where(UsStock.is_active == True))
                stocks = result.scalars().all()
                if not stocks:
                    await self.sync_us_stock_list(db)
                    result = await db.execute(select(UsStock).where(UsStock.is_active == True))
                    stocks = result.scalars().all()

            name_map   = {s.symbol: s.name   for s in stocks}
            sector_map = {s.symbol: s.sector  for s in stocks}
            symbols    = [s.symbol for s in stocks]

            # Alpha Vantage 免費版：每分鐘 25 次
            # 每支請求之間間隔 2.5 秒，確保不超限
            async with httpx.AsyncClient() as client:
                for i, symbol in enumerate(symbols):
                    data = await _fetch_av(symbol, client)
                    if data:
                        ok = await _save_one(
                            symbol,
                            name_map.get(symbol, symbol),
                            sector_map.get(symbol, ""),
                            data,
                        )
                        if ok:
                            success += 1
                        else:
                            error += 1
                    else:
                        error += 1

                    # 每 25 支暫停 65 秒，確保不超過每分鐘 25 次限制
                    if (i + 1) % 25 == 0 and i + 1 < len(symbols):
                        logger.info("AV rate limit pause: 65s")
                        await asyncio.sleep(65)
                    else:
                        await asyncio.sleep(2.5)

        except Exception as e:
            logger.error(f"US daily update failed: {e}")
        finally:
            self.is_running = False

        logger.info(f"US update done. Success={success} Error={error}")
        return {"status": "success", "success": success, "error": error}


us_update_service = UsUpdateService()
