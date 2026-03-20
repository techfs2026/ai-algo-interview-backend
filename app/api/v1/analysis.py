"""
代码分析 API
包含：提交判题、流式AI分析、推荐题单
"""
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import InterviewSession, Question, UserProfile
from app.schemas.analysis import AnalysisRequest, JudgeResult
from app.services.analysis_service import analyze_code_stream
from app.services.judge_service import judge_service
from app.services.user_service import UserProfileService, SkillUpdatePayload

logger = APIRouter()
router = APIRouter()


@router.post("/submit", summary="提交代码判题")
async def submit_code(
    req: AnalysisRequest,
    db:  AsyncSession = Depends(get_db),
):
    """
    提交代码判题。

    run_only=True：仅运行模式，只跑测试用例，不写库、不触发 AI 分析、不更新画像。
    run_only=False（默认）：正式提交，写库，后续触发 AI 分析。
    """
    # 获取会话和题目
    session, question = await _get_session_and_question(req.session_id, db)

    # 检查判题服务健康状态
    if not await judge_service.health_check():
        raise HTTPException(
            status_code=503,
            detail="判题服务暂时不可用，请确认 Judge0 已启动（docker compose up -d）"
        )

    # 从数据库读取测试用例
    test_cases = await judge_service.get_test_cases(
        question_id=question.id,
        db=db,
    )

    # 执行判题
    result = await judge_service.judge(
        code=req.code,
        language=req.language,
        test_cases=test_cases,
    )

    # 运行模式：只返回结果，不写库
    if req.run_only:
        return {
            "session_id":     req.session_id,
            "judge_result":   result.model_dump(),
            "question_id":    question.id,
            "question_title": question.title,
            "difficulty":     question.difficulty,
            "tags":           question.tags,
            "run_only":       True,
        }

    # 正式提交：写库
    session.code         = req.code
    session.language     = req.language
    session.time_used    = req.time_used
    session.passed       = result.passed
    session.total        = result.total
    session.submit_count = (session.submit_count or 0) + 1
    session.status       = "submitted"
    session.finished_at  = datetime.utcnow()
    await db.commit()

    return {
        "session_id":     req.session_id,
        "judge_result":   result.model_dump(),
        "question_id":    question.id,
        "question_title": question.title,
        "difficulty":     question.difficulty,
        "tags":           question.tags,
        "run_only":       False,
    }


@router.get("/stream/{session_id}", summary="AI代码分析（流式SSE）")
async def analyze_stream(
    session_id: str,
    db:         AsyncSession = Depends(get_db),
):
    """
    流式输出 AI 代码分析。
    使用 SSE（Server-Sent Events）协议。

    前端接收方式：
    const es = new EventSource('/api/v1/analysis/stream/{session_id}')
    es.onmessage = (e) => {
      const data = JSON.parse(e.data)
      if (data.type === 'chunk') appendText(data.content)
      if (data.type === 'done') es.close()
    }
    """
    session, question = await _get_session_and_question(session_id, db)

    if not session.code:
        raise HTTPException(status_code=400, detail="请先提交代码再获取分析")

    judge_result = JudgeResult(
        passed=session.passed or 0,
        total=session.total or 1,
        status="Accepted" if (session.passed or 0) == (session.total or 1) else "Wrong Answer",
        submit_count=session.submit_count or 1,
    )

    question_dict = {
        "id":         question.id,
        "title":      question.title,
        "difficulty": question.difficulty,
        "tags":       question.tags or [],
    }

    async def event_generator():
        try:
            async for chunk in analyze_code_stream(
                code=session.code,
                language=session.language or "python",
                time_used=session.time_used or 0,
                result=judge_result,
                question=question_dict,
            ):
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        except Exception as e:
            error = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(error)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.post("/complete/{session_id}", summary="分析完成，更新画像并获取推荐题单")
async def complete_analysis(
    session_id: str,
    db:         AsyncSession = Depends(get_db),
):
    """
    AI 分析结束后调用此接口：
    1. 异步更新用户画像
    2. 返回推荐题单（3道题）
    """
    session, question = await _get_session_and_question(session_id, db)

    # 获取用户画像
    result  = await db.execute(
        select(UserProfile).where(UserProfile.user_id == session.user_id)
    )
    profile = result.scalar_one_or_none()

    if profile and session.code:
        # 更新画像
        user_service = UserProfileService(db)
        payload = SkillUpdatePayload(
            tag=question.tags[0] if question.tags else "数组",
            question_id=question.id,
            difficulty=question.difficulty,
            passed=session.passed == session.total,
            time_used=session.time_used or 1800,
            expected_time=_get_expected_time(question.difficulty),
            submit_count=session.submit_count or 1,
            tags=question.tags or [],
            ac_rate=question.ac_rate or 0.5,
        )
        await user_service.update_skill_after_answer(session.user_id, payload)

    # 更新会话状态
    session.status = "completed"
    await db.commit()

    # 生成推荐题单
    recommendations = await _generate_recommendations(
        profile=profile,
        current_question=question,
        passed=(session.passed or 0) == (session.total or 1),
        time_used=session.time_used or 1800,
        db=db,
    )

    return {
        "session_id":      session_id,
        "recommendations": recommendations,
    }


# ─── 内部工具 ─────────────────────────────────────────────────────────────────

async def _get_session_and_question(
    session_id: str,
    db:         AsyncSession,
) -> tuple:
    result  = await db.execute(
        select(InterviewSession).where(InterviewSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    result   = await db.execute(
        select(Question).where(Question.id == session.question_id)
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="题目不存在")

    return session, question


def _get_expected_time(difficulty: str) -> int:
    """题目难度对应的参考用时（秒）"""
    return {"easy": 1200, "medium": 1800, "hard": 2700}.get(difficulty, 1800)


async def _generate_recommendations(
    profile,
    current_question: Question,
    passed:     bool,
    time_used:  int,
    db:         AsyncSession,
) -> list[dict]:
    """
    生成推荐题单（3道题）：
    - 相关题：根据做题情况动态调整难度
    - 薄弱题：最确认的弱点 + Easy
    - 新知识点：未接触过的标签
    """
    from sqlalchemy import func
    recommendations = []
    solved_ids = set(profile.solved_ids if profile else [])

    # 判断做题情况
    expected_time = _get_expected_time(current_question.difficulty)
    time_ratio    = time_used / expected_time if expected_time else 1.0
    performance   = (
        "顺利" if passed and time_ratio < 0.8 else
        "一般" if passed else
        "差"
    )

    # 1. 相关题（根据做题情况调整难度）
    diff_map    = {"easy": ["easy", "medium"], "medium": ["medium", "hard"], "hard": ["hard"]}
    target_diff = {
        "顺利": diff_map.get(current_question.difficulty, ["medium"])[-1],
        "一般": current_question.difficulty,
        "差":   diff_map.get(current_question.difficulty, ["easy"])[0],
    }.get(performance, current_question.difficulty)

    related = await _find_question(
        db=db,
        tags=current_question.tags[:1],
        difficulty=target_diff,
        exclude_ids=list(solved_ids) + [current_question.id],
    )
    if related:
        reason_map = {"顺利": "你掌握得不错，挑战更高难度", "一般": "巩固相同知识点", "差": "先把基础打扎实"}
        recommendations.append({
            **related,
            "recommend_type": "related",
            "reason": reason_map.get(performance, "相关练习"),
        })

    # 2. 薄弱题
    if profile:
        weak_tag = _get_weakest_tag(profile.skills, exclude_tags=current_question.tags)
        if weak_tag:
            weakness = await _find_question(
                db=db,
                tags=[weak_tag],
                difficulty="easy",
                exclude_ids=list(solved_ids) + [current_question.id],
            )
            if weakness:
                recommendations.append({
                    **weakness,
                    "recommend_type": "weakness",
                    "reason": f"{weak_tag} 是你目前最薄弱的方向，从简单题开始建立信心",
                })

    # 3. 新知识点
    if profile:
        new_tag = _get_untouched_tag(profile.skills)
        if new_tag:
            new_q = await _find_question(
                db=db,
                tags=[new_tag],
                difficulty="easy",
                exclude_ids=list(solved_ids) + [current_question.id],
            )
            if new_q:
                recommendations.append({
                    **new_q,
                    "recommend_type": "new",
                    "reason": f"你还没接触过{new_tag}，这是面试高频考点",
                })

    return recommendations


async def _find_question(
    db:          AsyncSession,
    tags:        list[str],
    difficulty:  str,
    exclude_ids: list[int],
) -> dict | None:
    """在数据库里找一道符合条件的题"""
    from sqlalchemy import func
    result = await db.execute(
        select(Question)
        .where(Question.difficulty == difficulty)
        .where(Question.is_indexed == True)
        .where(~Question.id.in_(exclude_ids or [0]))
        .order_by(func.random())
        .limit(1)
    )
    q = result.scalar_one_or_none()
    if not q:
        return None
    return {
        "id":         q.id,
        "title":      q.title,
        "title_slug": q.title_slug,
        "difficulty": q.difficulty,
        "tags":       q.tags or [],
    }


def _get_weakest_tag(skills: dict, exclude_tags: list[str]) -> str | None:
    """找最确认的薄弱点"""
    candidates = [
        (tag, data["level"], data["confidence"])
        for tag, data in skills.items()
        if data["confidence"] > 0.4 and tag not in exclude_tags
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]


def _get_untouched_tag(skills: dict) -> str | None:
    """找从未做过题的知识点"""
    untouched = [
        tag for tag, data in skills.items()
        if data.get("question_count", 0) == 0
    ]
    if not untouched:
        # 全都做过了，找做题最少的
        return min(skills.items(), key=lambda x: x[1].get("question_count", 0))[0]
    import random
    return random.choice(untouched)