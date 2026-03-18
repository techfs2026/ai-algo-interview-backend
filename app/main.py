from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.redis_client import init_redis, close_redis
from app.core.qdrant_client import init_qdrant, close_qdrant

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_redis()
    await init_qdrant()
    yield
    await close_redis()
    await close_qdrant()


app = FastAPI(
    title="AI Algo Interview System",
    description="AI驱动的算法面试系统",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── 路由注册 ─────────────────────────────────────────
from app.api.v1 import users, interview
app.include_router(users.router, prefix="/api/v1/users", tags=["用户"])
app.include_router(interview.router, prefix="/api/v1/interview", tags=["面试"])
# 后续逐步添加
# from app.api.v1 import questions, interview, analysis
# app.include_router(questions.router, prefix="/api/v1/questions", tags=["题目"])

# app.include_router(analysis.router,  prefix="/api/v1/analysis",  tags=["分析"])


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}