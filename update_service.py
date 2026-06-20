import asyncio
import json
import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional
from collections import defaultdict

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal, Stock, StockDailyData, UpdateLog, get_db
from finmind_service import finmind_service
from scoring import (
    calculate_score, calculate_moving_average,
    calculate_consecutive_buy, is_60day_high
)

logger = logging.getLogger(__name__)

# 主要追蹤的大型股票清單 (可擴充)
DEFAULT_WATCHLIST = [
    "2330", "2317", "2454", "2382", "2308",  # 台積電、鴻海、聯發科、廣達、台達電
    "2881", "2882", "2884", "2886", "2891",  # 富邦金、國泰金、玉山金、兆豐金、中信金
    "2303", "2357", "3711", "2395", "2379",  # 聯電、華碩、日月光、研華、瑞昱
    "2412", "2002", "1301", "1303", "1326",  # 中華電、中鋼、台塑、南亞、台化
    "2207", "2408", "2409", "2376", "3034",  # 和泰車、南科、友達、技嘉、聯詠
    "6505", "5880", "2327", "3008", "2337",  # 台塑石化、合庫金、國巨、大立光、旺宏
    "2474", "4938", "3045", "2345", "2385",  # 可成、和碩、台灣大、智邦、群光
    "2371", "3231", "2392", "2448", "6415",  # 大同、緯創、正新、晶電、矽力-KY
]


class DataUpdateService:
    def __init__(self):
        self.is_running = False

    async def sync_stock_list(self, db: AsyncSession) -> int:
        """同步股票清單"""
        logger.info("Syncing stock list from FinMind...")
        stocks = await finmind_service.get_stock_list()
        if not stocks:
            # 若 API 無資料，使用預設清單 (含股名)
            DEFAULT_NAMES = {
                "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2382": "廣達",
                "2308": "台達電", "2881": "富邦金", "2882": "國泰金", "2884": "玉山金",
                "2886": "兆豐金", "2891": "中信金", "2303": "聯電", "2357": "華碩",
                "3711": "日月光投控", "2395": "研華", "2379": "瑞昱", "2412": "中華電",
                "2002": "中鋼", "1301": "台塑", "1303": "南亞", "1326": "台化",
                "2207": "和泰車", "2408": "南科", "2409": "友達", "2376": "技嘉",
                "3034": "聯詠", "6505": "台塑石化", "5880": "合庫金", "2327": "國巨",
                "3008": "大立光", "2337": "旺宏", "2474": "可成", "4938": "和碩",
                "3045": "台灣大", "2345": "智邦", "2385": "群光", "2371": "大同",
                "3231": "緯創", "2392": "正新", "2448": "晶電", "6415": "矽力-KY",
            }
            stocks = [
                {"stock_id": s, "stock_name": DEFAULT_NAMES.get(s, s), "industry_category": ""}
                for s in DEFAULT_WATCHLIST
            ]

        count = 0
        for s in stocks:
            stock_id = s.get("stock_id", "")
            if not stock_id or len(stock_id) != 4:
                continue
            result = await db.execute(select(Stock).where(Stock.stock_id == stock_id))
            existing = result.scalar_one_or_none()
            if not existing:
                db.add(Stock(
                    stock_id=stock_id,
                    stock_name=s.get("stock_name", stock_id),
                    industry=s.get("industry_category", ""),
                ))
                count += 1

        await db.commit()
        logger.info(f"Added {count} new stocks")
        return count

    async def update_stock_data(self, stock_id: str, db: AsyncSession, target_date: date = None) -> bool:
        """更新單一股票資料"""
        if target_date is None:
            target_date = date.today()

        # 取得歷史資料 (需要計算MA60需要至少60天)
        end_date = target_date.strftime("%Y-%m-%d")
        start_date = (target_date - timedelta(days=120)).strftime("%Y-%m-%d")

        try:
            # 並行取得所有資料
            price_data, institutional_data, margin_data = await asyncio.gather(
                finmind_service.get_price_data(stock_id, start_date, end_date),
                finmind_service.get_institutional_investors(stock_id, start_date, end_date),
                finmind_service.get_margin_trading(stock_id, start_date, end_date),
            )

            if not price_data:
                return False

            # Log 資料筆數，方便排查
            logger.info(
                f"[{stock_id}] fetched: price={len(price_data)} "
                f"inst={len(institutional_data)} margin={len(margin_data)}"
            )
            if not institutional_data:
                logger.warning(f"[{stock_id}] ⚠️  法人資料為空！請確認 FinMind Token 或 API 用量限制")

            # 整理價格資料 — key 統一截前10碼 "YYYY-MM-DD"
            price_map    = {str(p["date"])[:10]: p for p in price_data}
            sorted_dates = sorted(price_map.keys())

            # 整理法人資料
            # FinMind 實際回傳的 name 英文值:
            #   外資: "Foreign_Investor" / "Foreign_Dealer_Self"
            #   投信: "Investment_Trust"
            #   自營: "Dealer_self" / "Dealer_Hedging"
            inst_map = defaultdict(lambda: {
                "foreign_net": 0, "foreign_buy": 0, "foreign_sell": 0,
                "trust_net":   0, "trust_buy":   0, "trust_sell":   0,
                "dealer_net":  0, "dealer_buy":  0, "dealer_sell":  0,
            })

            seen_names = set()
            for inst in institutional_data:
                raw_d    = inst.get("date")
                # 統一取前10碼 "YYYY-MM-DD"，避免帶時間戳的格式不一致
                d        = str(raw_d)[:10] if raw_d else None
                inv_type = inst.get("name", "")
                buy      = float(inst.get("buy",  0) or 0)
                sell     = float(inst.get("sell", 0) or 0)
                net      = buy - sell
                seen_names.add(inv_type)

                if inv_type == "Foreign_Investor":
                    inst_map[d]["foreign_net"]  += net
                    inst_map[d]["foreign_buy"]  += buy
                    inst_map[d]["foreign_sell"] += sell
                elif inv_type == "Investment_Trust":
                    inst_map[d]["trust_net"]  += net
                    inst_map[d]["trust_buy"]  += buy
                    inst_map[d]["trust_sell"] += sell
                elif inv_type in ("Dealer_self", "Dealer_Hedging"):
                    inst_map[d]["dealer_net"]  += net
                    inst_map[d]["dealer_buy"]  += buy
                    inst_map[d]["dealer_sell"] += sell

            # DEBUG: 印出 inst_map 內容確認日期有對上
            logger.info(f"[{stock_id}] inst_map keys: {sorted(inst_map.keys())}")
            logger.info(f"[{stock_id}] price sorted_dates tail: {sorted_dates[-3:]}")
            if inst_map:
                sample_d = sorted(inst_map.keys())[-1]
                logger.info(f"[{stock_id}] inst_map[{sample_d}] = {inst_map[sample_d]}")

            # 整理融資資料
            # FinMind 欄位: MarginPurchaseTodayBalance, MarginPurchaseYesterdayBalance
            margin_map = {}
            for m in margin_data:
                d         = m.get("date")
                today_bal = float(m.get("MarginPurchaseTodayBalance",     0) or 0)
                yes_bal   = float(m.get("MarginPurchaseYesterdayBalance", 0) or 0)
                margin_map[d] = {
                    "margin_purchase": today_bal,
                    "margin_change":   today_bal - yes_bal,
                }

            # 計算移動平均
            # FinMind 欄位: close, open, max(最高), min(最低), Trading_Volume(成交量)
            closes  = [float(price_map[d].get("close", 0) or 0) for d in sorted_dates]
            volumes = [float(price_map[d].get("Trading_Volume", 0) or 0) for d in sorted_dates]

            # 直接用 inst_map[d] 存取（defaultdict），讓沒有資料的日期自動補 0
            foreign_nets = [inst_map[d]["foreign_net"] for d in sorted_dates]
            trust_nets   = [inst_map[d]["trust_net"]   for d in sorted_dates]

            # 只更新目標日期
            target_date_str = target_date.strftime("%Y-%m-%d")
            if target_date_str not in price_map:
                # Try last available date
                if sorted_dates:
                    target_date_str = sorted_dates[-1]
                else:
                    return False

            target_idx = sorted_dates.index(target_date_str)
            closes_up_to = closes[:target_idx + 1]
            volumes_up_to = volumes[:target_idx + 1]

            ma5 = calculate_moving_average(closes_up_to, 5)
            ma20 = calculate_moving_average(closes_up_to, 20)
            ma60 = calculate_moving_average(closes_up_to, 60)
            vol_ma20 = calculate_moving_average(volumes_up_to, 20)
            close_today = closes_up_to[-1]
            vol_today = volumes_up_to[-1]

            # 連買天數
            foreign_nets_up_to = foreign_nets[:target_idx + 1]
            trust_nets_up_to = trust_nets[:target_idx + 1]
            foreign_consecutive = calculate_consecutive_buy(foreign_nets_up_to)
            trust_consecutive = calculate_consecutive_buy(trust_nets_up_to)

            # 60日新高
            _is_60d_high = is_60day_high(close_today, closes_up_to[:-1])

            # 量比
            volume_ratio = (vol_today / vol_ma20) if vol_ma20 and vol_ma20 > 0 else 0

            price_row = price_map[target_date_str]
            inst_row = inst_map.get(target_date_str, {})
            margin_row = margin_map.get(target_date_str, {})

            # 組合資料計算評分
            score_data = {
                "close_price": close_today,
                "ma60": ma60,
                "ma20": ma20,
                "is_60d_high": _is_60d_high,
                "volume_ratio": volume_ratio,
                "foreign_consecutive_buy": foreign_consecutive,
                "trust_consecutive_buy": trust_consecutive,
                "dealer_net": inst_row.get("dealer_net", 0),
                "margin_change": margin_row.get("margin_change", 0),
            }
            score, score_breakdown = calculate_score(score_data)

            # 儲存或更新資料
            target_date_obj = datetime.strptime(target_date_str, "%Y-%m-%d").date()
            result = await db.execute(
                select(StockDailyData).where(
                    and_(StockDailyData.stock_id == stock_id,
                         StockDailyData.date == target_date_obj)
                )
            )
            existing = result.scalar_one_or_none()

            row_data = dict(
                stock_id=stock_id,
                date=target_date_obj,
                open_price=float(price_row.get("open", 0) or 0),
                close_price=close_today,
                high_price=float(price_row.get("max", 0) or price_row.get("high", 0) or 0),
                low_price=float(price_row.get("min", 0) or price_row.get("low", 0) or 0),
                volume=vol_today,
                ma5=ma5,
                ma20=ma20,
                ma60=ma60,
                is_60d_high=_is_60d_high,
                volume_ma20=vol_ma20,
                volume_ratio=volume_ratio,
                foreign_net=inst_row.get("foreign_net", 0),
                foreign_buy=inst_row.get("foreign_buy", 0),
                foreign_sell=inst_row.get("foreign_sell", 0),
                foreign_consecutive_buy=foreign_consecutive,
                trust_net=inst_row.get("trust_net", 0),
                trust_buy=inst_row.get("trust_buy", 0),
                trust_sell=inst_row.get("trust_sell", 0),
                trust_consecutive_buy=trust_consecutive,
                dealer_net=inst_row.get("dealer_net", 0),
                dealer_buy=inst_row.get("dealer_buy", 0),
                dealer_sell=inst_row.get("dealer_sell", 0),
                margin_purchase=margin_row.get("margin_purchase", 0),
                margin_change=margin_row.get("margin_change", 0),
                score=score,
                score_breakdown=score_breakdown,
            )

            if existing:
                for k, v in row_data.items():
                    setattr(existing, k, v)
            else:
                db.add(StockDailyData(**row_data))

            await db.commit()
            return True

        except Exception as e:
            logger.error(f"Error updating {stock_id}: {e}")
            await db.rollback()
            return False

    async def run_daily_update(self, target_date: date = None) -> Dict:
        """執行每日更新任務"""
        if self.is_running:
            return {"status": "already_running"}

        self.is_running = True
        start_time = datetime.utcnow()
        if target_date is None:
            target_date = date.today()

        logger.info(f"Starting daily update for {target_date}")

        async with AsyncSessionLocal() as db:
            # 記錄開始
            log = UpdateLog(
                update_date=target_date,
                status="running",
                started_at=start_time,
            )
            db.add(log)
            await db.commit()
            await db.refresh(log)
            log_id = log.id

        success_count = 0
        error_count = 0

        try:
            async with AsyncSessionLocal() as db:
                # 取得所有股票
                result = await db.execute(select(Stock).where(Stock.is_active == True))
                stocks = result.scalars().all()

                if not stocks:
                    # 初始化預設股票清單
                    await self.sync_stock_list(db)
                    result = await db.execute(select(Stock).where(Stock.is_active == True))
                    stocks = result.scalars().all()

                logger.info(f"Updating {len(stocks)} stocks")

                # 批次更新 (避免 API rate limit)
                batch_size = 5
                for i in range(0, len(stocks), batch_size):
                    batch = stocks[i:i + batch_size]
                    tasks = []
                    for stock in batch:
                        tasks.append(self._update_with_session(stock.stock_id, target_date))

                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for r in results:
                        if r is True:
                            success_count += 1
                        else:
                            error_count += 1

                    await asyncio.sleep(1)  # Rate limit buffer

        except Exception as e:
            logger.error(f"Daily update failed: {e}")
        finally:
            self.is_running = False

        # 更新 log
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(UpdateLog).where(UpdateLog.id == log_id))
            log = result.scalar_one_or_none()
            if log:
                log.status = "success" if error_count == 0 else "partial"
                log.stocks_updated = success_count
                log.message = f"成功: {success_count}, 失敗: {error_count}"
                log.finished_at = datetime.utcnow()
                await db.commit()

        logger.info(f"Daily update complete. Success: {success_count}, Error: {error_count}")
        return {
            "status": "success",
            "success": success_count,
            "error": error_count,
            "date": target_date.isoformat(),
        }

    async def _update_with_session(self, stock_id: str, target_date: date) -> bool:
        async with AsyncSessionLocal() as db:
            return await self.update_stock_data(stock_id, db, target_date)


update_service = DataUpdateService()
