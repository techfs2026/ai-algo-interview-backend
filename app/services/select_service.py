"""
选题服务 - 系统第一个技术突破点

完整链路：
1. LLM 根据用户画像生成检索意图（HyDE 变体）
2. 向量检索 + 关键词过滤 → 召回 Top20
3. 去重（已做过 + 语义重复）
4. 四维重排（多样性/能力匹配/题目质量/校准价值）
5. Top3 加权随机抽取 → Top1
"""
import json
import logging
import random
import re
from typing import Any

from pydantic import BaseModel
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchAny, Range
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.llm_resilience import llm_call_with_resilience
from app.models.models import Question, UserProfile

logger   = logging.getLogger(__name__)
settings = get_settings()

from app.core.llm_client import get_embedding as _get_embedding


# ─── LLM 检索意图输出结构 ─────────────────────────────────────────────────────

class SearchIntent(BaseModel):
    semantic_query: str
    difficulty:     list[str]
    tags:           list[str]
    ac_rate_min:    float = 0.20
    ac_rate_max:    float = 0.75
    select_reason:  str
    focus_point:    str


# ─── Step 1：LLM 生成检索意图（HyDE 变体）────────────────────────────────────

INTENT_PROMPT = """/no_think
你是一位算法面试教练，需要为用户选择最合适的练习题。

用户当前状态：
- 技能掌握情况：{skills_summary}
- 近期薄弱点：{weak_skills}
- 已完成题目数：{total_questions}

请生成向量数据库的检索意图，帮助找到最适合该用户当前阶段的题目。

必须只输出 JSON，不要任何其他文字：
{{
    "semantic_query": "和入库文本同风格的语义描述，100字以内，描述想要什么样的题目",
    "difficulty": ["easy"],
    "tags": ["最相关的1-2个标签，如动态规划、哈希表"],
    "ac_rate_min": 0.25,
    "ac_rate_max": 0.70,
    "select_reason": "一句话说明为什么这样选题，给用户看的",
    "focus_point": "本次面试重点考察方向，给用户看的"
}}"""


def _build_skills_summary(skills: dict) -> str:
    """把技能字典转成简洁的文字描述"""
    items = []
    for tag, data in sorted(skills.items(), key=lambda x: x[1]["level"]):
        level = data["level"]
        conf  = data["confidence"]
        if conf > 0.5:  # 只展示置信度够高的
            label = "弱" if level < 0.4 else ("中" if level < 0.7 else "强")
            items.append(f"{tag}({label})")
    return "、".join(items) if items else "暂无评估数据"


def _get_weak_skills(skills: dict, top_n: int = 3) -> list[str]:
    """找出最确认的薄弱点（level低 且 confidence高）"""
    confirmed = [
        (tag, data["level"], data["confidence"])
        for tag, data in skills.items()
        if data["confidence"] > 0.4
    ]
    confirmed.sort(key=lambda x: x[1])  # level 从低到高
    return [tag for tag, _, _ in confirmed[:top_n]]


def _fallback_intent(profile: UserProfile) -> SearchIntent:
    """LLM 失败时的降级方案：规则生成检索意图"""
    weak = _get_weak_skills(profile.skills)
    tag  = weak[0] if weak else "数组"

    # 根据总答题数判断阶段
    if profile.total_questions < 10:
        difficulty = ["easy"]
    elif profile.total_questions < 30:
        difficulty = ["easy", "medium"]
    else:
        difficulty = ["medium"]

    return SearchIntent(
        semantic_query=f"{tag} 入门练习题，考察基本解题思路",
        difficulty=difficulty,
        tags=[tag],
        ac_rate_min=0.30,
        ac_rate_max=0.70,
        select_reason=f"根据你的练习记录，{tag} 是当前最需要加强的方向",
        focus_point=f"{tag} 基础思路与边界处理",
    )


async def generate_search_intent(profile: UserProfile) -> SearchIntent:
    """
    Step 1：让 LLM 把用户画像翻译成检索意图。
    这是 HyDE 变体的核心——查询文本和入库文本风格一致，语义空间对齐。
    """
    prompt = INTENT_PROMPT.format(
        skills_summary=_build_skills_summary(profile.skills),
        weak_skills="、".join(_get_weak_skills(profile.skills)) or "暂无",
        total_questions=profile.total_questions,
    )

    result, metrics = await llm_call_with_resilience(
        messages=[{"role": "user", "content": prompt}],
        scene="select",
        schema=SearchIntent,
        fallback_fn=lambda: _fallback_intent(profile),
        timeout=settings.llm_timeout_select,
    )

    if metrics.fallback_used:
        logger.warning("选题 LLM 调用失败，使用规则降级")

    return result


# ─── Step 2：向量检索 + 关键词过滤 ───────────────────────────────────────────

async def retrieve_candidates(
    intent: SearchIntent,
    qdrant: AsyncQdrantClient,
    top_k:  int = 20,
) -> list[dict]:
    """
    Step 2：两路召回
    - 向量检索：语义相似度
    - Qdrant Filter：难度 + 通过率过滤（关键词过滤）
    融合方式：先过滤再向量检索（串行），保证召回质量
    """
    # 生成查询向量
    try:
        query_vector = await _get_embedding(intent.semantic_query)
    except Exception as e:
        logger.error(f"生成查询向量失败: {e}")
        return []

    # 构造过滤条件
    must_conditions = [
        FieldCondition(
            key="difficulty",
            match=MatchAny(any=intent.difficulty),
        ),
        FieldCondition(
            key="ac_rate",
            range=Range(
                gte=intent.ac_rate_min,
                lte=intent.ac_rate_max,
            ),
        ),
    ]

    search_filter = Filter(must=must_conditions)

    # 向量检索
    try:
        results = await qdrant.search(
            collection_name=settings.qdrant_collection,
            query_vector=query_vector,
            limit=top_k,
            query_filter=search_filter,
            with_payload=True,
        )
    except Exception as e:
        logger.error(f"Qdrant 检索失败: {e}")
        return []

    return [
        {
            "id":             r.id,
            "vector_score":   r.score,
            **r.payload,
        }
        for r in results
    ]


# ─── Step 3：去重 ─────────────────────────────────────────────────────────────

def deduplicate(
    candidates:  list[dict],
    solved_ids:  list[int],
    failed_ids:  list[int],
) -> list[dict]:
    """
    两层去重：
    1. 过滤已做过的题（solved + failed 都不重复出）
    2. 保留已失败的题（可以二刷）—— 实际上只过滤 solved
    """
    solved_set = set(solved_ids)
    return [c for c in candidates if c["id"] not in solved_set]


# ─── Step 4：四维重排 ─────────────────────────────────────────────────────────

def _diversity_score(question: dict, recent_tags: list[str]) -> float:
    """多样性：防止连续出同一知识点"""
    q_tags   = set(question.get("tags", []))
    recent   = set(recent_tags)
    overlap  = q_tags & recent
    if not overlap:      return 1.0
    elif len(overlap)==1: return 0.6
    else:                return 0.3


def _performance_match_score(question: dict, profile: UserProfile) -> float:
    """
    能力匹配：难度略高于当前水平时价值最大。
    "跳一跳够得到"的区间得分最高。
    """
    tags = question.get("tags", [])
    if not tags:
        return 0.5

    # 取题目主标签对应的用户水平
    primary_tag = tags[0]
    skill = profile.skills.get(primary_tag, {"level": 0.5, "confidence": 0.2})
    level = skill["level"]

    # 题目难度对应的估计水平
    difficulty_map = {"easy": 0.3, "medium": 0.6, "hard": 0.85}
    q_level = difficulty_map.get(question.get("difficulty", "medium"), 0.6)

    gap = q_level - level
    if 0.1 <= gap <= 0.3:   return 1.0   # 最佳挑战区间
    elif 0.0 <= gap < 0.1:  return 0.7   # 略简单，有巩固价值
    elif 0.3 < gap <= 0.5:  return 0.6   # 略难，有挑战
    elif gap < 0:           return 0.3   # 低于当前水平
    else:                   return 0.2   # 远超当前水平


def _quality_score(question: dict) -> float:
    """题目质量：区分度 × 向量相似度"""
    ac = question.get("ac_rate", 0.5)
    if 0.2 <= ac <= 0.6:    discrimination = 1.0
    elif 0.6 < ac <= 0.8:   discrimination = 0.7
    else:                   discrimination = 0.4

    relevance = question.get("vector_score", 0.5)
    return discrimination * 0.6 + relevance * 0.4


def _calibration_score(question: dict, profile: UserProfile) -> float:
    """校准价值：置信度越低，越需要这个方向的题来校准"""
    tags = question.get("tags", [])
    if not tags:
        return 0.5
    tag        = tags[0]
    skill      = profile.skills.get(tag, {"confidence": 0.2})
    confidence = skill.get("confidence", 0.2)
    return 1.0 - confidence


def rerank(
    candidates:   list[dict],
    profile:      UserProfile,
    recent_tags:  list[str],
) -> list[dict]:
    """
    四维加权重排：
    能力匹配(0.40) + 题目质量(0.25) + 多样性(0.20) + 校准价值(0.15)
    """
    for c in candidates:
        d1 = _diversity_score(c, recent_tags)
        d2 = _performance_match_score(c, profile)
        d3 = _quality_score(c)
        d4 = _calibration_score(c, profile)

        c["final_score"] = (
            d1 * 0.20 +
            d2 * 0.40 +
            d3 * 0.25 +
            d4 * 0.15
        )

    return sorted(candidates, key=lambda x: x["final_score"], reverse=True)


# ─── Step 5：Top3 加权随机抽取 ────────────────────────────────────────────────

def weighted_random_select(ranked: list[dict]) -> dict:
    """
    从 Top3 里按权重随机抽取，而不是严格取第一。
    引入探索性，避免系统完全确定性，用户体验更自然。
    """
    top3    = ranked[:3]
    weights = [c["final_score"] for c in top3]
    return random.choices(top3, weights=weights, k=1)[0]


# ─── 获取最近答题的知识点 ─────────────────────────────────────────────────────

async def get_recent_tags(
    user_id: str,
    db:      AsyncSession,
    n:       int = 5,
) -> list[str]:
    """获取用户最近 n 道题覆盖的知识点，用于多样性评估"""
    from app.models.models import InterviewSession
    result = await db.execute(
        select(InterviewSession)
        .where(InterviewSession.user_id == user_id)
        .order_by(InterviewSession.created_at.desc())
        .limit(n)
    )
    sessions  = result.scalars().all()
    question_ids = [s.question_id for s in sessions]
    if not question_ids:
        return []

    q_result = await db.execute(
        select(Question).where(Question.id.in_(question_ids))
    )
    questions = q_result.scalars().all()
    tags = []
    for q in questions:
        tags.extend(q.tags or [])
    return list(set(tags))


# ─── 主入口 ───────────────────────────────────────────────────────────────────

async def select_question(
    profile: UserProfile,
    qdrant:  AsyncQdrantClient,
    db:      AsyncSession,
) -> tuple[dict, str, str]:
    """
    完整选题流程，返回 (选中题目payload, 选题理由, 重点方向)
    """
    # Step 1：生成检索意图
    intent = await generate_search_intent(profile)
    logger.info(f"检索意图: {intent.semantic_query[:50]}...")

    # Step 2：向量检索
    candidates = await retrieve_candidates(intent, qdrant, top_k=20)
    logger.info(f"召回候选题: {len(candidates)} 道")

    if not candidates:
        logger.warning("向量检索无结果，返回 None")
        return None, intent.select_reason, intent.focus_point

    # Step 3：去重
    candidates = deduplicate(candidates, profile.solved_ids, profile.failed_ids)
    logger.info(f"去重后: {len(candidates)} 道")

    if not candidates:
        logger.warning("去重后无可用题目")
        return None, intent.select_reason, intent.focus_point

    # Step 4：重排
    recent_tags = await get_recent_tags(profile.user_id, db)
    ranked      = rerank(candidates, profile, recent_tags)

    # Step 5：加权随机选取
    selected = weighted_random_select(ranked)
    logger.info(
        f"选题结果: [{selected.get('difficulty','?').upper()}] "
        f"{selected.get('title','?')} "
        f"(score={selected.get('final_score', 0):.3f})"
    )

    return selected, intent.select_reason, intent.focus_point