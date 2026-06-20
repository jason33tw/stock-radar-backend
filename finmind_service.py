import httpx
import logging
from datetime import datetime, timedelta, date
from typing import Optional, List, Dict
from config import settings

logger = logging.getLogger(__name__)


class FinMindService:
    def __init__(self):
        self.base_url = settings.FINMIND_API_URL
        self.timeout = 30.0

    def _headers(self) -> dict:
        """FinMind 使用 Authorization: Bearer {token} Header 驗證"""
        token = settings.FINMIND_API_TOKEN
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}

    def _get_params(self, dataset: str, stock_id: str = None,
                    start_date: str = None, end_date: str = None) -> dict:
        """只放查詢參數，token 改到 Header"""
        params = {"dataset": dataset}
        if stock_id:
            params["data_id"] = stock_id
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return params

    async def fetch(self, params: dict) -> Optional[List[Dict]]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(
                    self.base_url,
                    params=params,
                    headers=self._headers(),   # ← Bearer token 在 Header
                )
                data = resp.json()
                status = data.get("status")

                if status == 200:
                    rows = data.get("data", [])
                    logger.debug(
                        f"FinMind [{params.get('dataset')}] "
                        f"stock={params.get('data_id','-')} "
                        f"rows={len(rows)}"
                    )
                    return rows
                else:
                    # 402 = 超過用量, 401 = token 錯誤
                    logger.warning(
                        f"FinMind API error status={status} "
                        f"msg={data.get('msg')} "
                        f"dataset={params.get('dataset')} "
                        f"stock={params.get('data_id','-')}"
                    )
                    return []
            except Exception as e:
                logger.error(f"FinMind fetch error: {e} params={params}")
                return []

    # ── 個股資料 ──────────────────────────────────────────

    async def get_stock_list(self) -> List[Dict]:
        """取得上市上櫃股票清單"""
        data = await self.fetch(self._get_params("TaiwanStockInfo"))
        return [s for s in (data or []) if s.get("type") == "股票"]

    async def get_price_data(self, stock_id: str,
                              start_date: str, end_date: str) -> List[Dict]:
        """取得股價 (欄位: date, open, close, max, min, Trading_Volume)"""
        return await self.fetch(
            self._get_params("TaiwanStockPrice", stock_id, start_date, end_date)
        ) or []

    async def get_institutional_investors(self, stock_id: str,
                                           start_date: str, end_date: str) -> List[Dict]:
        """三大法人買賣超 (欄位: date, stock_id, name, buy, sell)
        name 可能值: 外資及陸資 / 外資及陸資(不含外資自營商) / 投信 / 自營商 /
                     自營商(自行買賣) / 自營商(避險)
        """
        return await self.fetch(
            self._get_params("TaiwanStockInstitutionalInvestorsBuySell",
                             stock_id, start_date, end_date)
        ) or []

    async def get_margin_trading(self, stock_id: str,
                                  start_date: str, end_date: str) -> List[Dict]:
        """融資融券 (欄位: MarginPurchaseTodayBalance, MarginPurchaseYesterdayBalance ...)"""
        return await self.fetch(
            self._get_params("TaiwanStockMarginPurchaseShortSale",
                             stock_id, start_date, end_date)
        ) or []

    # ── 全市場（不帶 data_id）─────────────────────────────

    async def get_all_institutional_by_date(self, trade_date: str) -> List[Dict]:
        """指定日期所有股票的三大法人"""
        return await self.fetch({
            "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
            "start_date": trade_date,
            "end_date":   trade_date,
        }) or []

    async def get_all_prices_by_date(self, trade_date: str) -> List[Dict]:
        """指定日期所有股票的價格"""
        return await self.fetch({
            "dataset":    "TaiwanStockPrice",
            "start_date": trade_date,
            "end_date":   trade_date,
        }) or []


finmind_service = FinMindService()
