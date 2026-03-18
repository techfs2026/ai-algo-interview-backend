"""
面试相关 API
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.qdrant_client import get_qdrant
from app.models.models import InterviewSession, Question
from app.schemas.interview import (
    SelectionResult,
    QuestionBrief,
    StartInterviewRequest,
    SwapQuestionRequest,
    SwapQuestionResponse,
)
from app.services.select_service import select_question
from app.services.user_service import UserProfileService

router = APIRouter()

# 题目难度对应的建议用时（秒）
DIFFICULTY_TIME_MAP = {
    "easy":   20 * 60,
    "medium": 30 * 60,
    "hard":   45 * 60,
}


@router.post(
    "/{user_id}/start",
    response_model=SelectionResult,
    summary="开始面试（AI选题）",
)
async def start_interview(
    user_id: str,
    req:     StartInterviewRequest = StartInterviewRequest(),
    db:      AsyncSession = Depends(get_db),
    qdrant=  Depends(get_qdrant),
):
    """
    开始一场面试，AI 根据用户画像自动选题。

    流程：
    1. 获取用户画像
    2. LLM 生成检索意图（HyDE 变体）
    3. 向量检索 + 四维重排
    4. 创建面试会话
    5. 返回题目 + 选题理由
    """
    user_service = UserProfileService(db)
    profile      = await user_service.get_or_404(user_id)

    if not profile.calibration_done:
        raise HTTPException(
            status_code=400,
            detail="请先完成初始问卷再开始面试"
        )

    # 执行选题
    selected, reason, focus = await select_question(profile, qdrant, db)

    if not selected:
        raise HTTPException(
            status_code=503,
            detail="暂时无法选题，请稍后重试"
        )

    # 从数据库取完整题目信息
    from sqlalchemy import select as sa_select
    result   = await db.execute(
        sa_select(Question).where(Question.id == selected["id"])
    )
    question = result.scalar_one_or_none()

    if not question:
        raise HTTPException(status_code=404, detail="题目不存在")

    # 创建面试会话
    session = InterviewSession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        question_id=question.id,
        time_limit=DIFFICULTY_TIME_MAP.get(question.difficulty, 1800),
        status="active",
        select_reason=reason,
    )
    db.add(session)
    await db.flush()

    return SelectionResult(
        session_id=session.id,
        question=QuestionBrief(
            id=question.id,
            title=question.title,
            title_slug=question.title_slug,
            difficulty=question.difficulty,
            tags=question.tags or [],
            ac_rate=question.ac_rate,
            time_limit=session.time_limit,
        ),
        select_reason=reason,
        focus_point=focus,
    )


@router.post(
    "/{user_id}/swap",
    response_model=SwapQuestionResponse,
    summary="换题",
)
async def swap_question(
    user_id: str,
    req:     SwapQuestionRequest,
    db:      AsyncSession = Depends(get_db),
    qdrant=  Depends(get_qdrant),
):
    """
    换题（每日限 2 次）。
    换题原因会作为隐式反馈信号更新用户画像。
    """
    user_service = UserProfileService(db)
    profile      = await user_service.get_or_404(user_id)

    # 检查配额
    can_swap, remaining = await user_service.check_swap_quota(user_id)
    if not can_swap:
        raise HTTPException(
            status_code=400,
            detail=f"今日换题次数已用完（每日限 {2} 次）"
        )

    # 根据换题原因微调画像信号（隐式反馈）
    await _apply_swap_feedback(profile, req, user_service)

    # 重新选题（会排除当前 session 的题目，因为已在 solved_ids 的排除逻辑里）
    selected, reason, focus = await select_question(profile, qdrant, db)
    if not selected:
        raise HTTPException(status_code=503, detail="暂时无法选题，请稍后重试")

    from sqlalchemy import select as sa_select
    result   = await db.execute(
        sa_select(Question).where(Question.id == selected["id"])
    )
    question = result.scalar_one_or_none()

    # 消耗换题配额
    remaining = await user_service.consume_swap_quota(user_id)

    return SwapQuestionResponse(
        question=QuestionBrief(
            id=question.id,
            title=question.title,
            title_slug=question.title_slug,
            difficulty=question.difficulty,
            tags=question.tags or [],
            ac_rate=question.ac_rate,
            time_limit=DIFFICULTY_TIME_MAP.get(question.difficulty, 1800),
        ),
        select_reason=reason,
        swap_remaining=remaining,
    )


@router.get(
    "/session/{session_id}/content",
    summary="获取题目完整内容（含描述、代码模板）",
)
async def get_question_content(
    session_id: str,
    db:         AsyncSession = Depends(get_db),
):
    """
    获取题目的完整描述和代码模板。
    优先从 Redis 缓存读取（TTL 7天），未命中则实时调用 LeetCode API。
    """
    from sqlalchemy import select as sa_select
    result  = await db.execute(
        sa_select(InterviewSession).where(InterviewSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    result   = await db.execute(
        sa_select(Question).where(Question.id == session.question_id)
    )
    question = result.scalar_one_or_none()

    from app.services.question_service import QuestionService
    q_service = QuestionService(db)
    content   = await q_service.get_content(question.title_slug)

    if not content:
        raise HTTPException(status_code=503, detail="暂时无法获取题目内容，请稍后重试")

    return content


# ─── 内部工具 ─────────────────────────────────────────────────────────────────

async def _apply_swap_feedback(profile, req, user_service):
    """
    根据换题原因更新画像（隐式反馈信号）。
    "太难了" → 当前知识点 level 微降
    "太简单了" → 当前知识点 level 微升
    其他 → 不更新 level
    """
    # 这里只做轻微调整，不影响主要画像更新逻辑
    # 完整实现在 Day 5 的 judge + analyze 环节
    pass