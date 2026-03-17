"""
用户相关 API
"""
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.config import get_settings
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


@router.post("/", response_model=CreateUserResponse, summary="创建新用户")
async def create_user(db: AsyncSession = Depends(get_db)):
    """
    创建新用户。
    前端首次访问时调用，生成 UUID + 随机用户名，存入 localStorage。
    """
    service = UserProfileService(db)
    user    = await service.create_user()
    return CreateUserResponse(
        user_id=user.user_id,
        username=user.username,
    )


@router.get("/{user_id}", response_model=UserProfileResponse, summary="获取用户画像")
async def get_user_profile(
    user_id: str,
    db: AsyncSession = Depends(get_db),
):
    service  = UserProfileService(db)
    user     = await service.get_or_404(user_id)
    today    = date.today().isoformat()

    # 计算剩余换题次数
    swap_used      = user.swap_used if user.swap_date == today else 0
    swap_remaining = max(0, settings.daily_swap_limit - swap_used)

    return UserProfileResponse(
        user_id=user.user_id,
        username=user.username,
        skills={
            tag: SkillState(**skill_data)
            for tag, skill_data in user.skills.items()
        },
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
    """
    提交冷启动问卷，初始化用户画像。
    每个用户只能提交一次，重复提交返回 400。

    问卷包含 15 个知识点，每个知识点 5 档自评：
    1=一点不会, 2=会做简单题, 3=会做中等题, 4=会做难题, 5=难不倒我
    """
    service = UserProfileService(db)
    user    = await service.submit_questionnaire(user_id, req)
    today   = date.today().isoformat()
    swap_remaining = max(
        0, settings.daily_swap_limit - (user.swap_used if user.swap_date == today else 0)
    )

    return UserProfileResponse(
        user_id=user.user_id,
        username=user.username,
        skills={
            tag: SkillState(**skill_data)
            for tag, skill_data in user.skills.items()
        },
        calibration_done=user.calibration_done,
        total_questions=user.total_questions,
        swap_remaining=swap_remaining,
        created_at=user.created_at,
        last_active=user.last_active,
    )


@router.get("/questionnaire/schema", summary="获取问卷结构")
async def get_questionnaire_schema():
    """
    返回问卷的知识点列表，前端据此渲染问卷 UI。
    """
    return {
        "tags": KNOWLEDGE_TAGS,
        "rating_labels": {
            1: "一点不会",
            2: "会做简单题",
            3: "会做中等题",
            4: "会做难题",
            5: "难不倒我",
        },
        "description": "请对以下每个知识点进行自我评估",
    }