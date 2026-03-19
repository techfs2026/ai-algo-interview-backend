"""
用户相关 API
路由顺序：静态路径必须在动态路径 /{user_id} 之前注册，
否则 FastAPI 会把 "questionnaire"、"observability" 等当作 user_id 匹配。
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.config import get_settings
from app.models.models import LLMCallLog, InterviewSession
from app.schemas.user import (
    CreateUserResponse,
    QuestionnaireRequest,
    UserProfileResponse,
    SkillState,
    KNOWLEDGE_TAGS,
)
from app.services.user_service import UserProfileService

router   = APIRouter()
settings = get_settings()


# ─── 静态路径（必须在 /{user_id} 之前）──────────────────────────────────────

@router.post("/", response_model=CreateUserResponse, summary="创建新用户")
async def create_user(db: AsyncSession = Depends(get_db)):
    """创建新用户，生成 UUID + 随机用户名，存入 localStorage。"""
    service = UserProfileService(db)
    user    = await service.create_user()
    return CreateUserResponse(user_id=user.user_id, username=user.username)


@router.get("/questionnaire/schema", summary="获取问卷结构")
async def get_questionnaire_schema():
    """返回问卷的知识点列表，前端据此渲染问卷 UI。"""
    return {
        "tags": KNOWLEDGE_TAGS,
        "rating_labels": {
            1: "一点不会", 2: "会做简单题", 3: "会做中等题",
            4: "会做难题",  5: "难不倒我",
        },
        "description": "请对以下每个知识点进行自我评估",
    }


@router.get("/observability/llm", summary="LLM 调用可观测性统计")
async def get_llm_observability(
    hours: int = 24,
    db: AsyncSession = Depends(get_db),
):
    """
    LLM 调用层可观测性统计。
    包含：各场景成功率、延迟分位数、三层容错触发率。
    """
    from datetime import timedelta

    since  = datetime.utcnow() - timedelta(hours=hours)
    result = await db.execute(
        sa_select(LLMCallLog).where(LLMCallLog.created_at >= since)
    )
    logs = result.scalars().all()

    if not logs:
        return {
            "period_hours": hours,
            "total_calls":  0,
            "message":      "暂无数据，开始使用系统后将自动记录",
        }

    total = len(logs)

    repair_count   = sum(1 for l in logs if l.repair_success)
    fallback_count = sum(1 for l in logs if l.fallback_used)
    retry_count    = sum(1 for l in logs if l.attempts > 1 and not l.fallback_used)
    success_count  = total - fallback_count

    latencies = sorted([l.latency_ms for l in logs])

    def pct(data, p):
        return data[min(int(len(data) * p / 100), len(data) - 1)]

    scenes = {}
    for log in logs:
        s = log.scene
        if s not in scenes:
            scenes[s] = {"total": 0, "fallback": 0, "latencies": []}
        scenes[s]["total"]    += 1
        scenes[s]["fallback"] += 1 if log.fallback_used else 0
        scenes[s]["latencies"].append(log.latency_ms)

    by_scene = {}
    for scene, data in scenes.items():
        lats = sorted(data["latencies"])
        by_scene[scene] = {
            "total_calls":    data["total"],
            "success_rate":   round(1 - data["fallback"] / data["total"], 3),
            "fallback_rate":  round(data["fallback"] / data["total"], 3),
            "avg_latency_ms": int(sum(lats) / len(lats)),
            "p95_latency_ms": pct(lats, 95),
        }

    failure_reasons = {}
    for log in logs:
        if log.failure_reason:
            failure_reasons[log.failure_reason] = \
                failure_reasons.get(log.failure_reason, 0) + 1

    return {
        "period_hours": hours,
        "total_calls":  total,
        "overall": {
            "success_rate":   round(success_count / total, 3),
            "repair_rate":    round(repair_count / total, 3),
            "retry_rate":     round(retry_count / total, 3),
            "fallback_rate":  round(fallback_count / total, 3),
            "avg_latency_ms": int(sum(latencies) / len(latencies)),
            "p50_latency_ms": pct(latencies, 50),
            "p95_latency_ms": pct(latencies, 95),
            "p99_latency_ms": pct(latencies, 99),
        },
        "by_scene":        by_scene,
        "failure_reasons": failure_reasons,
        "interpretation": {
            "repair_rate":   "第一层本地修复覆盖率（零成本）",
            "retry_rate":    "第二层带上下文重试触发率",
            "fallback_rate": "第三层降级触发率（越低越好）",
        },
    }


# ─── 动态路径（必须在静态路径之后）──────────────────────────────────────────

@router.get("/{user_id}", response_model=UserProfileResponse, summary="获取用户画像")
async def get_user_profile(user_id: str, db: AsyncSession = Depends(get_db)):
    service      = UserProfileService(db)
    user         = await service.get_or_404(user_id)
    today        = date.today().isoformat()
    swap_used      = user.swap_used if user.swap_date == today else 0
    swap_remaining = max(0, settings.daily_swap_limit - swap_used)

    return UserProfileResponse(
        user_id=user.user_id,
        username=user.username,
        skills={tag: SkillState(**data) for tag, data in user.skills.items()},
        calibration_done=user.calibration_done,
        total_questions=user.total_questions,
        swap_remaining=swap_remaining,
        created_at=user.created_at,
        last_active=user.last_active,
    )


@router.post("/{user_id}/questionnaire", response_model=UserProfileResponse,
             summary="提交冷启动问卷")
async def submit_questionnaire(
    user_id: str,
    req: QuestionnaireRequest,
    db: AsyncSession = Depends(get_db),
):
    service        = UserProfileService(db)
    user           = await service.submit_questionnaire(user_id, req)
    today          = date.today().isoformat()
    swap_remaining = max(
        0, settings.daily_swap_limit - (user.swap_used if user.swap_date == today else 0)
    )
    return UserProfileResponse(
        user_id=user.user_id,
        username=user.username,
        skills={tag: SkillState(**data) for tag, data in user.skills.items()},
        calibration_done=user.calibration_done,
        total_questions=user.total_questions,
        swap_remaining=swap_remaining,
        created_at=user.created_at,
        last_active=user.last_active,
    )


@router.get("/{user_id}/stats", summary="获取用户统计数据")
async def get_user_stats(user_id: str, db: AsyncSession = Depends(get_db)):
    """用户统计数据，用于统计弹窗展示。包含答题历史、知识点画像、近期表现。"""
    service = UserProfileService(db)
    user    = await service.get_or_404(user_id)

    sessions_result = await db.execute(
        sa_select(InterviewSession)
        .where(InterviewSession.user_id == user_id)
        .where(InterviewSession.status == "completed")
        .order_by(InterviewSession.created_at.desc())
        .limit(10)
    )
    recent = sessions_result.scalars().all()

    total_submitted = len([s for s in recent if s.passed is not None])
    total_passed    = len([s for s in recent
                           if s.passed is not None and s.passed == s.total])
    pass_rate  = round(total_passed / total_submitted, 2) if total_submitted > 0 else 0

    times    = [s.time_used for s in recent if s.time_used]
    avg_time = int(sum(times) / len(times)) if times else 0

    skills_sorted = sorted(
        user.skills.items(),
        key=lambda x: x[1]["level"],
        reverse=True,
    )

    return {
        "user_id":         user.user_id,
        "username":        user.username,
        "total_questions": user.total_questions,
        "solved_count":    len(user.solved_ids or []),
        "failed_count":    len(user.failed_ids or []),
        "pass_rate":       pass_rate,
        "avg_time_secs":   avg_time,
        "skills": [
            {
                "tag":            tag,
                "level":          round(data["level"], 3),
                "confidence":     round(data["confidence"], 3),
                "question_count": data["question_count"],
            }
            for tag, data in skills_sorted
        ],
        "created_at":  user.created_at.isoformat(),
        "last_active": user.last_active.isoformat(),
    }