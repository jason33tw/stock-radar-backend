from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import asyncio
import logging
from datetime import datetime

from database import init_db
from routers import stocks, analysis, notifications, scheduler, debug
from config import settings

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialized")
    yield
    logger.info("Application shutting down")


app = FastAPI(
    title="台股法人飆股雷達",
    description="自動追蹤法人籌碼，篩選潛力飆股",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stocks.router,        prefix="/api/stocks",        tags=["股票"])
app.include_router(analysis.router,      prefix="/api/analysis",      tags=["分析"])
app.include_router(notifications.router, prefix="/api/notifications",  tags=["通知"])
app.include_router(scheduler.router,     prefix="/api/scheduler",     tags=["排程"])
app.include_router(debug.router,         prefix="/api/debug",         tags=["除錯"])


@app.get("/")
async def root():
    return {"message": "台股法人飆股雷達 API", "version": "1.0.0"}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "token_configured": bool(settings.FINMIND_API_TOKEN),
    }
