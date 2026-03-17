"""
用户画像服务层

包含：
- 用户创建（UUID + 随机用户名）
- 冷启动问卷处理
- 画像四维加权更新算法
- 换题配额管理
"""
import random
import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import UserProfile
from app.schemas.user import (
    KNOWLEDGE_TAGS,
    SELF_RATING_MAP,
    QuestionnaireRequest,
    SkillState,
    SkillUpdatePayload,
)


# ─── 随机用户名生成 ───────────────────────────────────────────────────────────

_ADJECTIVES = [
    "勇猛的", "优雅的", "敏捷的", "沉稳的", "机智的",
    "犀利的", "从容的", "坚韧的", "睿智的", "冷静的",
]

_NOUNS = [
    "二叉树", "哈希表", "递归栈", "动态规划", "滑动窗口",
    "双指针", "快速排序", "链表节点", "优先队列", "并查集",
]


def _gen_username() -> str:
    return random.choice(_ADJECTIVES) + random.choice(_NOUNS)


# ─── 初始画像构造 ─────────────────────────────────────────────────────────────

def _default_skills() -> dict:
    """所有知识点初始化为中等水平，低置信度"""
    return {
        tag: {"level": 0.5, "confidence": 0.2, "question_count": 0}
        for tag in KNOWLEDGE_TAGS
    }


def _skills_from_questionnaire(req: QuestionnaireRequest) -> dict:
    """
    将问卷自评转化为初始画像。
    规则映射，不用 LLM，保证冷启动稳定性。
    置信度统一设为 0.2（有初始估计但未验证）。
    """
    skills = _default_skills()
    for item in req.items:
        if item.tag in skills:
            skills[item.tag] = {
                "level":          SELF_RATING_MAP[item.rating],
                "confidence":     0.2,
                "question_count": 0,
            }
    return skills


# ─── 画像更新算法 ─────────────────────────────────────────────────────────────

def _question_weight(ac_rate: float, tags: list[str]) -> float:
    """
    题目权重 = 区分度 × 知识点纯度
    区分度：通过率在 20%~60% 的题区分效果最好
    纯度：标签越少，对单个知识点的校准价值越高
    """
    # 区分度权重
    if 0.2 <= ac_rate <= 0.6:
        discrimination = 1.0
    elif ac_rate < 0.2:
        discrimination = 0.4
    elif 0.6 < ac_rate <= 0.8:
        discrimination = 0.7
    else:
        discrimination = 0.3

    # 知识点纯度权重
    purity = 1.0 / max(len(tags), 1)

    return discrimination * purity


def _time_coefficient(used: int, expected: int) -> float:
    """
    用时系数：把二元的对/错扩展为连续信号
    远快于预期 → 信号更强；远慢于预期 → 信号更弱
    """
    if expected <= 0:
        return 1.0
    ratio = used / expected
    if ratio < 0.4:    return 1.3
    elif ratio < 0.8:  return 1.1
    elif ratio < 1.2:  return 1.0
    elif ratio < 1.8:  return 0.8
    else:              return 0.6


def _k_value(question_count: int) -> float:
    """
    K值衰减（类 ELO）：答题数越多，单次更新幅度越小
    防止画像后期剧烈抖动
    """
    base_k     = 0.08
    decay_rate = 0.02
    return base_k / (1 + question_count * decay_rate)


def compute_skill_update(
    current: dict,
    payload: SkillUpdatePayload,
) -> dict:
    """
    四维加权画像更新：
      更新量 = 答题结果 × 题目权重 × 用时系数 × K值衰减

    同时更新 level 和 confidence：
    - 结果符合预期 → confidence 小幅上升
    - 结果不符预期 → level 调整 + confidence 大幅上升（信息量更大）
    - 结果矛盾     → confidence 下降
    """
    updated = dict(current)

    skill = updated.get(payload.tag, {
        "level": 0.5, "confidence": 0.2, "question_count": 0
    })

    level      = skill["level"]
    confidence = skill["confidence"]
    count      = skill["question_count"]

    # 计算权重
    q_weight   = _question_weight(payload.ac_rate, payload.tags)
    time_coef  = _time_coefficient(payload.time_used, payload.expected_time)
    k          = _k_value(count)
    delta      = k * q_weight * time_coef

    # 估计这道题对应的难度水平
    difficulty_level_map = {"easy": 0.3, "medium": 0.6, "hard": 0.85}
    question_level = difficulty_level_map.get(payload.difficulty.lower(), 0.5)

    # 判断结果是否符合预期
    expected_pass = level >= question_level
    surprising    = payload.passed != expected_pass  # 结果出乎意料

    # 更新 level
    if payload.passed:
        new_level = min(1.0, level + delta)
    else:
        new_level = max(0.0, level - delta * 0.6)  # 失败降权幅度小于成功升权

    # 更新 confidence
    alpha = 0.08   # 出乎意料时的置信度增量（信息量大）
    beta  = 0.04   # 符合预期时的置信度增量
    gamma = 0.05   # 矛盾时的置信度降量

    if surprising:
        new_confidence = min(1.0, confidence + alpha)
    elif not surprising and payload.passed:
        new_confidence = min(1.0, confidence + beta)
    else:
        new_confidence = max(0.0, confidence - gamma)

    updated[payload.tag] = {
        "level":          round(new_level,      4),
        "confidence":     round(new_confidence, 4),
        "question_count": count + 1,
    }

    return updated


# ─── CRUD 服务 ────────────────────────────────────────────────────────────────

class UserProfileService:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_user(self) -> UserProfile:
        """创建新用户，生成 UUID 和随机用户名"""
        profile = UserProfile(
            user_id=str(uuid.uuid4()),
            username=_gen_username(),
            skills=_default_skills(),
        )
        self.db.add(profile)
        await self.db.flush()
        return profile

    async def get_user(self, user_id: str) -> UserProfile | None:
        result = await self.db.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_or_404(self, user_id: str) -> UserProfile:
        from fastapi import HTTPException
        user = await self.get_user(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        return user

    async def submit_questionnaire(
        self,
        user_id: str,
        req: QuestionnaireRequest,
    ) -> UserProfile:
        """处理冷启动问卷，初始化画像"""
        user = await self.get_or_404(user_id)

        if user.calibration_done:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="已完成初始问卷，无法重复提交")

        user.skills            = _skills_from_questionnaire(req)
        user.calibration_done  = True
        user.last_active       = datetime.utcnow()

        await self.db.flush()
        return user

    async def update_skill_after_answer(
        self,
        user_id: str,
        payload: SkillUpdatePayload,
    ) -> UserProfile:
        """答题后更新画像（异步回写调用此方法）"""
        user = await self.get_or_404(user_id)

        user.skills         = compute_skill_update(user.skills, payload)
        user.total_questions = user.total_questions + 1
        user.last_active    = datetime.utcnow()

        # 更新答题历史
        if payload.passed:
            solved = list(user.solved_ids)
            if payload.question_id not in solved:
                solved.append(payload.question_id)
            user.solved_ids = solved
        else:
            failed = list(user.failed_ids)
            if payload.question_id not in failed:
                failed.append(payload.question_id)
            user.failed_ids = failed

        await self.db.flush()
        return user

    async def check_swap_quota(self, user_id: str) -> tuple[bool, int]:
        """
        检查换题配额。
        返回 (可以换题, 剩余次数)
        """
        from app.core.config import get_settings
        settings   = get_settings()
        user       = await self.get_or_404(user_id)
        today      = date.today().isoformat()

        # 日期变了，重置配额
        if user.swap_date != today:
            user.swap_date = today
            user.swap_used = 0
            await self.db.flush()

        remaining = settings.daily_swap_limit - user.swap_used
        return remaining > 0, remaining

    async def consume_swap_quota(self, user_id: str) -> int:
        """消耗一次换题机会，返回剩余次数"""
        from app.core.config import get_settings
        settings = get_settings()
        user     = await self.get_or_404(user_id)
        today    = date.today().isoformat()

        if user.swap_date != today:
            user.swap_date = today
            user.swap_used = 0

        user.swap_used = user.swap_used + 1
        await self.db.flush()

        return settings.daily_swap_limit - user.swap_used