"""
美股資料更新服務
資料來源：yfinance (Yahoo Finance)
評分邏輯：技術面 + 機構面並重
"""
import asyncio
import json
import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
import concurrent.futures

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from database_us import UsStock, UsStockDaily

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
#  預設追蹤清單（美國主流大型股 + 成長股）
# ──────────────────────────────────────────
DEFAULT_US_WATCHLIST = [
    # 科技
    ("AAPL",  "蘋果",        "Technology"),
    ("MSFT",  "微軟",        "Technology"),
    ("NVDA",  "輝達",        "Technology"),
    ("GOOGL", "Alphabet",    "Technology"),
    ("META",  "Meta",        "Technology"),
    ("AMZN",  "亞馬遜",      "Consumer"),
    ("TSLA",  "特斯拉",      "Automotive"),
    ("AMD",   "超微",        "Technology"),
    ("AVGO",  "博通",        "Technology"),
    ("QCOM",  "高通",        "Technology"),
    ("INTC",  "英特爾",      "Technology"),
    ("CRM",   "Salesforce",  "Technology"),
    ("ORCL",  "甲骨文",      "Technology"),
    ("ADBE",  "Adobe",       "Technology"),
    ("NOW",   "ServiceNow",  "Technology"),
    # 金融
    ("JPM",   "摩根大通",    "Finance"),
    ("BAC",   "美國銀行",    "Finance"),
    ("GS",    "高盛",        "Finance"),
    ("V",     "Visa",        "Finance"),
    ("MA",    "萬事達",      "Finance"),
    # 醫療 / 消費 / 其他
    ("LLY",   "禮來",        "Healthcare"),
    ("UNH",   "聯合健康",    "Healthcare"),
    ("JNJ",   "嬌生",        "Healthcare"),
    ("WMT",   "沃爾瑪",      "Consumer"),
    ("COST",  "好市多",      "Consumer"),
    ("NFLX",  "Netflix",     "Communication"),
    ("DIS",   "迪士尼",      "Communication"),
    ("XOM",   "埃克森美孚",  "Energy"),
    ("CVX",   "雪佛龍",      "Energy"),
    ("SPY",   "標普500 ETF", "ETF"),
    ("QQQ",   "那斯達克 ETF","ETF"),
    ("SOXX",  "半導體 ETF",  "ETF"),
]

# ──────────────────────────────────────────
#  評分規則
# ──────────────────────────────────────────
US_SCORE_RULES = {
    "above_ma20":          10,   # 站上月線
    "above_ma60":          10,   # 站上季線
    "is_60d_high":         15,   # 創60日新高
    "is_52w_high":         10,   # 創52週新高
    "volume_ratio_200":    20,   # 量增2倍以上
    "volume_ratio_150":    10,   # 量增1.5倍以上
    "rsi_bullish":         10,   # RSI 50-70 多頭區
    "inst_own_high":       10,   # 機構持股 > 60%
    "short_ratio_low":     10,   # 空頭比例 < 3 (不易被軋空)
    "price_momentum_3":    10,   # 3日漲幅 > 3%
}


def calculate_us_score(data: dict) -> tuple:
    breakdown = {}
    reasons = []
    total = 0

    close  = data.get("close_price", 0) or 0
    ma20   = data.get("ma20", 0) or 0
    ma60   = data.get("ma60", 0) or 0

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

    inst = data.get("inst_own_pct", 0) or 0
    if inst >= 60:
        pts = US_SCORE_RULES["inst_own_high"]
        breakdown["inst_own_high"] = pts
        reasons.append(f"機構持股{inst:.0f}% (+{pts})")
        total += pts

    sr = data.get("short_ratio", 99) or 99
    if sr < 3:
        pts = US_SCORE_RULES["short_ratio_low"]
        breakdown["short_ratio_low"] = pts
        reasons.append(f"空頭比低({sr:.1f}) (+{pts})")
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


def _fetch_yfinance_sync(symbol: str) -> Optional[dict]:
    """同步版 yfinance 抓取（在 executor 裡執行）"""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)

        # 抓近 120 天日線資料
        hist = ticker.history(period="6mo", interval="1d", auto_adjust=True)
        if hist.empty or len(hist) < 10:
            return None

        closes  = hist["Close"].tolist()
        volumes = hist["Volume"].tolist()
        opens   = hist["Open"].tolist()
        highs   = hist["High"].tolist()
        lows    = hist["Low"].tolist()
        dates   = [str(d.date()) for d in hist.index]

        # 技術指標
        def ma(lst, n):
            if len(lst) < n:
                return None
            return round(sum(lst[-n:]) / n, 4)

        close_today  = closes[-1]
        ma5          = ma(closes, 5)
        ma20         = ma(closes, 20)
        ma60         = ma(closes, 60)
        avg_vol20    = ma(volumes, 20)
        vol_today    = volumes[-1]
        vol_ratio    = round(vol_today / avg_vol20, 2) if avg_vol20 else 0
        is_60d_high  = close_today >= max(closes[-60:]) if len(closes) >= 60 else False
        is_52w_high  = close_today >= max(closes[-252:]) if len(closes) >= 252 else close_today >= max(closes)
        rsi14        = _calculate_rsi(closes)
        change_pct   = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2) if len(closes) >= 2 else 0
        mom3         = round((closes[-1] - closes[-4]) / closes[-4] * 100, 2) if len(closes) >= 4 else 0

        # 機構資料
        info = ticker.info
        inst_own_pct    = round((info.get("institutionPercentHeld") or 0) * 100, 1)
        short_ratio     = info.get("shortRatio") or 99
        short_pct_float = round((info.get("shortPercentOfFloat") or 0) * 100, 1)
        name            = info.get("shortName") or info.get("longName") or symbol
        sector          = info.get("sector") or ""

        return {
            "symbol":         symbol,
            "name":           name,
            "sector":         sector,
            "date":           dates[-1],
            "open_price":     round(opens[-1], 4),
            "close_price":    round(close_today, 4),
            "high_price":     round(highs[-1], 4),
            "low_price":      round(lows[-1], 4),
            "change_pct":     change_pct,
            "volume":         vol_today,
            "avg_volume20":   avg_vol20,
            "volume_ratio":   vol_ratio,
            "ma5":            ma5,
            "ma20":           ma20,
            "ma60":           ma60,
            "is_60d_high":    is_60d_high,
            "is_52w_high":    is_52w_high,
            "rsi14":          rsi14,
            "inst_own_pct":   inst_own_pct,
            "short_ratio":    short_ratio,
            "short_pct_float":short_pct_float,
            "price_momentum_3": mom3,
        }
    except Exception as e:
        logger.error(f"yfinance error [{symbol}]: {e}")
        return None


class UsUpdateService:
    def __init__(self):
        self.is_running = False
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

    async def _fetch(self, symbol: str) -> Optional[dict]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, _fetch_yfinance_sync, symbol)

    async def sync_us_stock_list(self, db: AsyncSession) -> int:
        count = 0
        for symbol, name, sector in DEFAULT_US_WATCHLIST:
            result = await db.execute(select(UsStock).where(UsStock.symbol == symbol))
            if not result.scalar_one_or_none():
                db.add(UsStock(symbol=symbol, name=name, sector=sector))
                count += 1
        await db.commit()
        return count

    async def update_one(self, symbol: str) -> bool:
        data = await self._fetch(symbol)
        if not data:
            return False

        score, score_bd = calculate_us_score(data)

        async with AsyncSessionLocal() as db:
            try:
                target_date = datetime.strptime(data["date"], "%Y-%m-%d").date()
                result = await db.execute(
                    select(UsStockDaily).where(
                        and_(UsStockDaily.symbol == symbol,
                             UsStockDaily.date   == target_date)
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

                # 確保股票基本資料存在
                sr = await db.execute(select(UsStock).where(UsStock.symbol == symbol))
                if not sr.scalar_one_or_none():
                    db.add(UsStock(
                        symbol=symbol,
                        name=data["name"],
                        sector=data["sector"],
                    ))

                await db.commit()
                return True
            except Exception as e:
                logger.error(f"DB error [{symbol}]: {e}")
                await db.rollback()
                return False

    async def run_daily_update(self) -> dict:
        if self.is_running:
            return {"status": "already_running"}

        self.is_running = True
        success, error = 0, 0

        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(UsStock).where(UsStock.is_active == True))
                stocks = result.scalars().all()
                if not stocks:
                    await self.sync_us_stock_list(db)
                    result = await db.execute(select(UsStock).where(UsStock.is_active == True))
                    stocks = result.scalars().all()

            symbols = [s.symbol for s in stocks]
            # 每批 5 支，避免 Yahoo Finance rate limit
            batch_size = 5
            for i in range(0, len(symbols), batch_size):
                batch = symbols[i:i + batch_size]
                results = await asyncio.gather(*[self.update_one(s) for s in batch], return_exceptions=True)
                for r in results:
                    if r is True:
                        success += 1
                    else:
                        error += 1
                await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"US daily update failed: {e}")
        finally:
            self.is_running = False

        logger.info(f"US update done. Success={success} Error={error}")
        return {"status": "success", "success": success, "error": error}


us_update_service = UsUpdateService()
