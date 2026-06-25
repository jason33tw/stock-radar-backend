from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, and_
import httpx
import logging
import json

from database import get_db, StockDailyData, Stock
from config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  LINE Messaging API 發送函式（支援 Flex Message）
# ─────────────────────────────────────────────
async def send_line_message(messages: list, broadcast: bool = True) -> dict:
    """
    支援任意 LINE message 格式（text / flex）
    broadcast=True  → 廣播給所有好友
    broadcast=False → 單點推送至 LINE_TARGET_ID
    """
    token = settings.LINE_CHANNEL_ACCESS_TOKEN

    if not token:
        return {"success": False, "error": "LINE_CHANNEL_ACCESS_TOKEN 未設定"}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token.strip()}",
    }

    if broadcast:
        url = "https://api.line.me/v2/bot/message/broadcast"
        payload = {"messages": messages}
    else:
        to = settings.LINE_TARGET_ID
        if not to:
            return {"success": False, "error": "LINE_TARGET_ID 未設定"}
        url = "https://api.line.me/v2/bot/message/push"
        payload = {"to": to.strip(), "messages": messages}

    try:
        logger.info(f"🎯 發送 {'廣播' if broadcast else '單點'} 訊息...")
        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.post(url, headers=headers, json=payload, timeout=10.0)
            if response.status_code == 200:
                return {"success": True}
            else:
                logger.error(f"LINE API 錯誤: {response.status_code} - {response.text}")
                return {"success": False, "error": f"LINE 拒絕 ({response.status_code}): {response.text}"}
    except Exception as e:
        logger.exception("發送異常")
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────
#  Flex Message 格式化（股票可點擊連結）
# ─────────────────────────────────────────────
def build_stock_bubble(i: int, s: dict) -> dict:
    """產生單一股票的 Flex Bubble"""
    score     = s.get("score", 0)
    name      = s.get("stock_name", "")
    sid       = s.get("stock_id", "")
    close     = s.get("close_price", 0)
    bd        = s.get("score_breakdown", {})
    fdays     = s.get("foreign_consecutive_buy", 0)
    tdays     = s.get("trust_consecutive_buy", 0)
    vr        = s.get("volume_ratio", 0)
    goodinfo_url = f"https://goodinfo.tw/tw/StockDetail.php?STOCK_ID={sid}"

    # 分數顏色
    score_color = "#E53935" if score >= 80 else "#FB8C00" if score >= 60 else "#43A047"

    # 原因標籤
    reasons = []
    if bd.get("foreign_consecutive_buy_5") or bd.get("foreign_consecutive_buy_3"):
        reasons.append(f"外資連買{fdays}天")
    if bd.get("trust_consecutive_buy_5") or bd.get("trust_consecutive_buy_3"):
        reasons.append(f"投信連買{tdays}天")
    if bd.get("is_60d_high"):
        reasons.append("創60日新高")
    if bd.get("volume_ratio_200") and vr:
        reasons.append(f"量增{vr:.1f}x")
    if bd.get("margin_decrease"):
        reasons.append("融資減少")

    reason_components = [
        {
            "type": "text",
            "text": r,
            "size": "xs",
            "color": "#ffffff",
            "wrap": False,
            "adjustMode": "shrink-to-fit",
            "flex": 0,
            "margin": "sm",
            "decoration": "none",
            "style": "normal",
            # 小標籤背景用 box 包裝，見下方
        }
        for r in reasons
    ]

    # 標籤 box 列表
    tag_boxes = [
        {
            "type": "box",
            "layout": "vertical",
            "contents": [{"type": "text", "text": r, "size": "xs", "color": "#ffffff"}],
            "backgroundColor": "#5C6BC0",
            "cornerRadius": "8px",
            "paddingAll": "4px",
            "paddingStart": "8px",
            "paddingEnd": "8px",
            "margin": "sm",
        }
        for r in reasons
    ]

    return {
        "type": "bubble",
        "size": "kilo",
        "header": {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {
                    "type": "text",
                    "text": f"#{i}",
                    "size": "sm",
                    "color": "#888888",
                    "flex": 0,
                },
                {
                    "type": "text",
                    "text": f"{score}分",
                    "size": "sm",
                    "color": score_color,
                    "align": "end",
                    "weight": "bold",
                },
            ],
            "paddingAll": "12px",
            "paddingBottom": "4px",
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                # 股票名稱（可點擊）
                {
                    "type": "button",
                    "action": {
                        "type": "uri",
                        "label": f"{sid} {name}",
                        "uri": goodinfo_url,
                    },
                    "style": "primary",
                    "color": "#1565C0",
                    "height": "sm",
                    "margin": "none",
                },
                # 收盤價
                {
                    "type": "text",
                    "text": f"收盤價　${close:.1f}",
                    "size": "sm",
                    "color": "#555555",
                    "margin": "md",
                },
                # 原因標籤
                *(
                    [{
                        "type": "box",
                        "layout": "horizontal",
                        "contents": tag_boxes,
                        "margin": "md",
                        "wrap": True,
                    }]
                    if tag_boxes else []
                ),
            ],
            "paddingAll": "12px",
        },
    }


def format_flex_message(stocks: list, target_date: str) -> list:
    """回傳完整 LINE messages list（header carousel + stock carousel）"""

    # ── 標題泡泡 ──
    header_bubble = {
        "type": "bubble",
        "size": "kilo",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "📡 台股法人飆股雷達", "weight": "bold", "size": "lg"},
                {"type": "text", "text": f"📅 {target_date}", "size": "sm", "color": "#888888", "margin": "sm"},
                {"type": "separator", "margin": "md"},
                {"type": "text", "text": f"🏆 今日 TOP{len(stocks)} 潛力飆股", "size": "sm", "margin": "md", "color": "#333333"},
            ],
        },
    }

    # ── 股票泡泡列表 ──
    stock_bubbles = [build_stock_bubble(i, s) for i, s in enumerate(stocks[:10], 1)]

    # 全部放進同一個 Carousel
    carousel = {
        "type": "flex",
        "altText": f"📡 台股法人飆股雷達 {target_date} TOP{len(stocks)}",
        "contents": {
            "type": "carousel",
            "contents": [header_bubble] + stock_bubbles,
        },
    }

    return [carousel]


# ─────────────────────────────────────────────
#  API 路由
# ─────────────────────────────────────────────
@router.post("/send-daily")
async def send_daily_notification(
    min_score: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """發送每日 TOP 潛力股通知 (LINE Flex Message + Goodinfo 連結)"""
    result = await db.execute(select(func.max(StockDailyData.date)))
    target_date = result.scalar_one_or_none()
    if not target_date:
        raise HTTPException(status_code=404, detail="尚無股票資料")

    rows = await db.execute(
        select(StockDailyData, Stock.stock_name)
        .join(Stock, Stock.stock_id == StockDailyData.stock_id)
        .where(and_(
            StockDailyData.date == target_date,
            StockDailyData.score >= min_score,
        ))
        .order_by(desc(StockDailyData.score))
        .limit(20)
    )
    rows = rows.all()

    if not rows:
        return {"success": False, "message": "今日無符合條件的股票"}

    stocks = []
    for row, stock_name in rows:
        bd = {}
        try:
            bd = json.loads(row.score_breakdown or "{}")
        except Exception:
            pass
        stocks.append({
            "stock_id":                row.stock_id,
            "stock_name":              stock_name,
            "close_price":             float(row.close_price or 0),
            "score":                   row.score,
            "score_breakdown":         bd,
            "foreign_consecutive_buy": row.foreign_consecutive_buy,
            "trust_consecutive_buy":   row.trust_consecutive_buy,
            "volume_ratio":            float(row.volume_ratio or 0),
        })

    messages = format_flex_message(stocks, target_date.isoformat())
    send_result = await send_line_message(messages, broadcast=True)

    return {
        "success":      send_result["success"],
        "message":      "通知已發送 ✅" if send_result["success"] else f"發送失敗：{send_result.get('error')}",
        "stocks_count": len(stocks),
    }


@router.post("/test")
async def test_notification():
    """發送測試通知（含 Goodinfo 連結）"""
    test_stocks = [{
        "stock_id":                "2330",
        "stock_name":              "台積電",
        "close_price":             950.0,
        "score":                   85,
        "score_breakdown":         {"foreign_consecutive_buy_5": 1, "is_60d_high": 1},
        "foreign_consecutive_buy": 5,
        "trust_consecutive_buy":   0,
        "volume_ratio":            2.3,
    }]
    messages = format_flex_message(test_stocks, "2025-01-01")
    result   = await send_line_message(messages, broadcast=True)
    return {
        "success": result["success"],
        "message": "測試訊息已發送，請查看 LINE ✅" if result["success"] else f"發送失敗：{result.get('error')}",
    }