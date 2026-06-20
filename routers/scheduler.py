from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime
import logging

from database import get_db
from update_service import update_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/update")
async def trigger_update(
    background_tasks: BackgroundTasks,
    target_date: str = Query(default=None, description="YYYY-MM-DD, 預設今天"),
):
    """手動觸發資料更新"""
    if update_service.is_running:
        return {"status": "already_running", "message": "更新任務執行中，請稍後"}

    parsed_date = None
    if target_date:
        try:
            parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
        except:
            return {"status": "error", "message": "日期格式錯誤，請使用 YYYY-MM-DD"}

    background_tasks.add_task(update_service.run_daily_update, parsed_date)
    return {
        "status": "started",
        "message": f"資料更新已開始 ({parsed_date or date.today()})",
    }


@router.post("/sync-stocks")
async def sync_stock_list(db: AsyncSession = Depends(get_db)):
    """同步股票清單"""
    count = await update_service.sync_stock_list(db)
    return {"status": "success", "new_stocks": count}


@router.get("/status")
async def get_scheduler_status():
    """取得更新狀態"""
    return {
        "is_running": update_service.is_running,
        "message": "更新任務執行中" if update_service.is_running else "閒置中",
    }
