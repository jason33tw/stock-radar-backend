from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from collections import defaultdict
from datetime import date, timedelta
from finmind_service import finmind_service
from database import get_db, StockDailyData
from config import settings

router = APIRouter()


@router.get("/parse-test/{stock_id}")
async def parse_test(stock_id: str = "2330"):
    """
    直接抓 API 資料並模擬解析流程，看 inst_map 是否正確建立
    http://localhost:8000/api/debug/parse-test/2330
    """
    end   = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")

    price_data         = await finmind_service.get_price_data(stock_id, start, end)
    institutional_data = await finmind_service.get_institutional_investors(stock_id, start, end)

    # 價格 key 統一截前10碼
    price_map    = {str(p["date"])[:10]: p for p in price_data}
    sorted_dates = sorted(price_map.keys())

    # 解析法人
    inst_map = defaultdict(lambda: {
        "foreign_net": 0, "foreign_buy": 0, "foreign_sell": 0,
        "trust_net":   0, "trust_buy":   0, "trust_sell":   0,
        "dealer_net":  0, "dealer_buy":  0, "dealer_sell":  0,
    })
    for inst in institutional_data:
        raw_d    = inst.get("date")
        d        = str(raw_d)[:10] if raw_d else None
        inv_type = inst.get("name", "")
        buy      = float(inst.get("buy",  0) or 0)
        sell     = float(inst.get("sell", 0) or 0)
        net      = buy - sell

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

    latest_date = sorted_dates[-1] if sorted_dates else None
    inst_row    = dict(inst_map[latest_date]) if latest_date else {}

    return {
        "latest_price_date":   latest_date,
        "price_map_keys":      sorted_dates[-5:],
        "inst_map_keys":       sorted(inst_map.keys()),
        "inst_raw_dates":      sorted({str(r.get("date"))[:10] for r in institutional_data}),
        "keys_overlap":        [d for d in sorted_dates if d in inst_map],
        "inst_row_for_latest": inst_row,
        "raw_inst_first5":     institutional_data[:5],
    }


@router.get("/db-check/{stock_id}")
async def db_check(stock_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StockDailyData)
        .where(StockDailyData.stock_id == stock_id)
        .order_by(desc(StockDailyData.date))
        .limit(5)
    )
    rows = result.scalars().all()
    return [
        {
            "date":           r.date.isoformat(),
            "close":          r.close_price,
            "foreign_net":    r.foreign_net,
            "foreign_buy":    r.foreign_buy,
            "foreign_sell":   r.foreign_sell,
            "foreign_consec": r.foreign_consecutive_buy,
            "trust_net":      r.trust_net,
            "trust_buy":      r.trust_buy,
            "dealer_net":     r.dealer_net,
            "score":          r.score,
        }
        for r in rows
    ]


@router.get("/raw-inst/{stock_id}")
async def raw_institutional(stock_id: str):
    end   = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
    inst  = await finmind_service.get_institutional_investors(stock_id, start, end)
    return {
        "total_rows":   len(inst),
        "unique_names": list({r.get("name") for r in inst}),
        "all_rows":     inst,
    }


@router.get("/raw-margin/{stock_id}")
async def raw_margin(stock_id: str):
    end    = date.today().strftime("%Y-%m-%d")
    start  = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
    margin = await finmind_service.get_margin_trading(stock_id, start, end)
    return {
        "total_rows": len(margin),
        "columns":    list(margin[0].keys()) if margin else [],
        "all_rows":   margin,
    }


@router.get("/test-finmind")
async def test_finmind_api():
    end   = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
    stock = "2330"
    price  = await finmind_service.get_price_data(stock, start, end)
    inst   = await finmind_service.get_institutional_investors(stock, start, end)
    margin = await finmind_service.get_margin_trading(stock, start, end)
    return {
        "token_configured": bool(settings.FINMIND_API_TOKEN),
        "price_rows":  len(price),
        "inst_rows":   len(inst),
        "margin_rows": len(margin),
        "inst_names":  list({r.get("name") for r in inst}),
        "inst_sample": inst[:3] if inst else [],
    }


@router.get("/raw-http")
async def raw_http_test():
    """
    最底層測試：直接發 HTTP 請求，印出原始回應
    http://localhost:8000/api/debug/raw-http
    """
    import httpx
    token = settings.FINMIND_API_TOKEN
    url   = "https://api.finmindtrade.com/api/v4/data"

    # 試1：有 token header
    params = {
        "dataset":    "TaiwanStockPrice",
        "data_id":    "2330",
        "start_date": "2026-06-11",
        "end_date":   "2026-06-18",
    }

    results = {}

    async with httpx.AsyncClient(timeout=20) as client:
        # 帶 Bearer header
        if token:
            r1 = await client.get(url, params=params,
                                  headers={"Authorization": f"Bearer {token}"})
            results["with_bearer"] = {
                "status_code": r1.status_code,
                "body": r1.json(),
            }
        else:
            results["with_bearer"] = "TOKEN IS EMPTY"

        # 不帶 token（測試是否能抓到部分資料）
        r2 = await client.get(url, params=params)
        body2 = r2.json()
        results["no_token"] = {
            "status_code": r2.status_code,
            "api_status":  body2.get("status"),
            "msg":         body2.get("msg"),
            "row_count":   len(body2.get("data", [])),
        }

    return {
        "token_value":  token[:10] + "..." if token and len(token) > 10 else f"[{repr(token)}]",
        "token_length": len(token) if token else 0,
        "results":      results,
    }


@router.get("/env-check")
async def env_check():
    """
    確認 .env 檔案是否存在、路徑是否正確
    http://localhost:8000/api/debug/env-check
    """
    import os
    from pathlib import Path

    here     = Path(__file__).parent.parent.resolve()  # backend/
    env_path = here / ".env"

    env_exists   = env_path.exists()
    env_contents = ""
    if env_exists:
        raw = env_path.read_text(encoding="utf-8")
        # 只印 key 名稱，不印 value（保護 token 安全）
        lines = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key = line.split("=")[0].strip()
            val = line.split("=", 1)[1].strip() if "=" in line else ""
            has_val = "✅ 有值" if val else "❌ 空白"
            lines.append(f"{key} → {has_val}")
        env_contents = lines

    return {
        "backend_dir":        str(here),
        "env_path":           str(env_path),
        "env_file_exists":    env_exists,
        "env_keys_status":    env_contents,
        "cwd":                os.getcwd(),
        "token_in_settings":  bool(settings.FINMIND_API_TOKEN),
        "token_from_environ": bool(os.environ.get("FINMIND_API_TOKEN")),
    }
