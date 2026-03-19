"""
选题服务 - 系统第一个技术突破点

完整链路：
1. LLM 根据用户画像生成检索意图（HyDE 变体）
2. 向量检索 + 关键词过滤 → 召回 Top20
3. 去重（已做过）
4. 四维重排（多样性/能力匹配/题目质量/校准价值）
5. Top3 加权随机抽取 → Top1
"""
import json
import logging
import random
import time
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
    # select_reason 和 focus_point 由后端规则生成，不让 LLM 输出，减少约 40% token


# ─── Step 1：LLM 生成检索意图（HyDE 变体）────────────────────────────────────

# 入库文本的实际格式（来自 semantic_expander.py 的 build_index_text）
# semantic_query 必须模仿这个风格，才能保证语义空间对齐
# 入库文本示例（semantic_query 必须模仿此格式，保证语义空间对齐）
_INDEX_TEXT_EXAMPLE = "难度：easy 标签：数组、哈希表 核心技能：哈希表查找 适合水平：入门 解题方向：一次遍历用哈希表找互补数"

INTENT_PROMPT = """/no_think
用户画像：技能={skills_summary} 薄弱点={weak_skills} 已做{total_questions}题

索引文本格式示例：{index_text_example}

为该用户选一道合适的题，输出 JSON（不要任何其他文字）：
{{
    "semantic_query": "仿照示例格式，包含难度/标签/核心技能/适合水平/解题方向，80字以内",
    "difficulty": ["easy"],
    "tags": ["1-2个标签"],
    "ac_rate_min": 0.25,
    "ac_rate_max": 0.70
}}"""


def _build_skills_summary(skills: dict) -> str:
    items = []
    for tag, data in sorted(skills.items(), key=lambda x: x[1]["level"]):
        if data["confidence"] > 0.5:
            label = "弱" if data["level"] < 0.4 else ("中" if data["level"] < 0.7 else "强")
            items.append(f"{tag}({label})")
    return "、".join(items) if items else "暂无评估数据"


def _get_weak_skills(skills: dict, top_n: int = 3) -> list[str]:
    confirmed = [
        (tag, data["level"], data["confidence"])
        for tag, data in skills.items()
        if data["confidence"] > 0.4
    ]
    confirmed.sort(key=lambda x: x[1])
    return [tag for tag, _, _ in confirmed[:top_n]]


def _fallback_intent(profile: UserProfile) -> SearchIntent:
    weak = _get_weak_skills(profile.skills)
    tag  = weak[0] if weak else "数组"
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
    )


def _make_reason_and_focus(intent: SearchIntent, profile: UserProfile) -> tuple[str, str]:
    """
    规则生成 select_reason 和 focus_point，不让 LLM 生成，节省约 40% token。
    """
    tag        = intent.tags[0] if intent.tags else "算法"
    diff       = intent.difficulty[0] if intent.difficulty else "medium"
    diff_label = {"easy": "简单", "medium": "中等", "hard": "困难"}.get(diff, "中等")

    weak    = _get_weak_skills(profile.skills, top_n=1)
    is_weak = weak and tag in weak

    if is_weak:
        reason = f"检测到 {tag} 是你当前薄弱点，针对性练习效果最佳"
    elif profile.total_questions < 5:
        reason = f"新手热身，从 {diff_label} {tag} 题开始建立信心"
    else:
        reason = f"根据你的画像，{diff_label}{tag}题是当前最佳挑战区间"

    focus = f"{tag} 核心思路与边界条件处理"
    return reason, focus


async def generate_search_intent(profile: UserProfile) -> tuple[SearchIntent, float]:
    """返回 (intent, latency_ms)"""
    t0     = time.perf_counter()
    prompt = INTENT_PROMPT.format(
        skills_summary=_build_skills_summary(profile.skills),
        weak_skills="、".join(_get_weak_skills(profile.skills)) or "暂无",
        total_questions=profile.total_questions,
        index_text_example=_INDEX_TEXT_EXAMPLE,
    )

    result, metrics = await llm_call_with_resilience(
        messages=[{"role": "user", "content": prompt}],
        scene="select",
        schema=SearchIntent,
        fallback_fn=lambda: _fallback_intent(profile),
        timeout=settings.llm_timeout_select,
        max_tokens=150,   # 输出字段少了，150 token 足够
    )
    ms = (time.perf_counter() - t0) * 1000

    if metrics.fallback_used:
        logger.warning("选题 LLM 调用失败，使用规则降级")

    return result, ms


# ─── Step 2：向量检索 + 关键词过滤 ───────────────────────────────────────────

async def retrieve_candidates(
    intent: SearchIntent,
    qdrant: AsyncQdrantClient,
    top_k:  int = 20,
) -> tuple[list[dict], float]:
    """返回 (candidates, latency_ms)"""
    t0 = time.perf_counter()

    try:
        t_emb   = time.perf_counter()
        query_vector = await _get_embedding(intent.semantic_query)
        emb_ms  = (time.perf_counter() - t_emb) * 1000
    except Exception as e:
        logger.error(f"生成查询向量失败: {e}")
        return [], 0.0

    must_conditions = [
        FieldCondition(key="difficulty", match=MatchAny(any=intent.difficulty)),
        FieldCondition(key="ac_rate",    range=Range(gte=intent.ac_rate_min, lte=intent.ac_rate_max)),
    ]

    try:
        t_qdrant = time.perf_counter()
        results  = await qdrant.search(
            collection_name=settings.qdrant_collection,
            query_vector=query_vector,
            limit=top_k,
            query_filter=Filter(must=must_conditions),
            with_payload=True,
        )
        qdrant_ms = (time.perf_counter() - t_qdrant) * 1000
    except Exception as e:
        logger.error(f"Qdrant 检索失败: {e}")
        return [], 0.0

    total_ms = (time.perf_counter() - t0) * 1000
    logger.info(f"[选题耗时] 向量检索: embedding={emb_ms:.0f}ms qdrant={qdrant_ms:.0f}ms")

    candidates = [{"id": r.id, "vector_score": r.score, **r.payload} for r in results]
    return candidates, total_ms


# ─── Step 3：去重 ─────────────────────────────────────────────────────────────

def deduplicate(candidates: list[dict], solved_ids: list[int], failed_ids: list[int]) -> list[dict]:
    solved_set = set(solved_ids)
    return [c for c in candidates if c["id"] not in solved_set]


# ─── Step 4：四维重排 ─────────────────────────────────────────────────────────

def _diversity_score(question: dict, recent_tags: list[str]) -> float:
    q_tags  = set(question.get("tags", []))
    recent  = set(recent_tags)
    overlap = q_tags & recent
    if not overlap:       return 1.0
    elif len(overlap)==1: return 0.6
    else:                 return 0.3


def _performance_match_score(question: dict, profile: UserProfile) -> float:
    tags = question.get("tags", [])
    if not tags:
        return 0.5
    primary_tag = tags[0]
    skill  = profile.skills.get(primary_tag, {"level": 0.5, "confidence": 0.2})
    level  = skill["level"]
    q_level = {"easy": 0.3, "medium": 0.6, "hard": 0.85}.get(question.get("difficulty", "medium"), 0.6)
    gap    = q_level - level
    if 0.1 <= gap <= 0.3:   return 1.0
    elif 0.0 <= gap < 0.1:  return 0.7
    elif 0.3 < gap <= 0.5:  return 0.6
    elif gap < 0:           return 0.3
    else:                   return 0.2


def _quality_score(question: dict) -> float:
    ac = question.get("ac_rate", 0.5)
    if 0.2 <= ac <= 0.6:   discrimination = 1.0
    elif 0.6 < ac <= 0.8:  discrimination = 0.7
    else:                  discrimination = 0.4
    relevance = question.get("vector_score", 0.5)
    return discrimination * 0.6 + relevance * 0.4


def _calibration_score(question: dict, profile: UserProfile) -> float:
    tags = question.get("tags", [])
    if not tags:
        return 0.5
    skill      = profile.skills.get(tags[0], {"confidence": 0.2})
    confidence = skill.get("confidence", 0.2)
    return 1.0 - confidence


def rerank(candidates: list[dict], profile: UserProfile, recent_tags: list[str]) -> list[dict]:
    for c in candidates:
        c["final_score"] = (
            _diversity_score(c, recent_tags)         * 0.20 +
            _performance_match_score(c, profile)     * 0.40 +
            _quality_score(c)                        * 0.25 +
            _calibration_score(c, profile)           * 0.15
        )
    return sorted(candidates, key=lambda x: x["final_score"], reverse=True)


# ─── Step 5：Top3 加权随机抽取 ────────────────────────────────────────────────

def weighted_random_select(ranked: list[dict]) -> dict:
    top3    = ranked[:3]
    weights = [c["final_score"] for c in top3]
    return random.choices(top3, weights=weights, k=1)[0]


# ─── 最近答题知识点 ───────────────────────────────────────────────────────────

async def get_recent_tags(user_id: str, db: AsyncSession, n: int = 5) -> list[str]:
    from app.models.models import InterviewSession
    result = await db.execute(
        select(InterviewSession)
        .where(InterviewSession.user_id == user_id)
        .order_by(InterviewSession.created_at.desc())
        .limit(n)
    )
    sessions     = result.scalars().all()
    question_ids = [s.question_id for s in sessions]
    if not question_ids:
        return []
    q_result  = await db.execute(select(Question).where(Question.id.in_(question_ids)))
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
    """完整选题流程，返回 (选中题目payload, 选题理由, 重点方向)"""

    t_total = time.perf_counter()

    # Step 1：LLM 生成检索意图
    intent, llm_ms = await generate_search_intent(profile)
    logger.info(f"[选题耗时] Step1 LLM意图生成: {llm_ms:.0f}ms")

    # Step 2：向量检索
    candidates, retrieve_ms = await retrieve_candidates(intent, qdrant, top_k=20)
    logger.info(f"[选题耗时] Step2 向量检索: {retrieve_ms:.0f}ms | 召回: {len(candidates)} 道")

    if not candidates:
        logger.warning("向量检索无结果")
        return None, intent.select_reason, intent.focus_point

    # Step 3：去重
    candidates = deduplicate(candidates, profile.solved_ids, profile.failed_ids)
    logger.info(f"[选题耗时] Step3 去重后: {len(candidates)} 道")

    if not candidates:
        logger.warning("去重后无可用题目")
        return None, intent.select_reason, intent.focus_point

    # Step 4：重排
    t4          = time.perf_counter()
    recent_tags = await get_recent_tags(profile.user_id, db)
    ranked      = rerank(candidates, profile, recent_tags)
    logger.info(f"[选题耗时] Step4 重排: {(time.perf_counter() - t4) * 1000:.0f}ms")

    # Step 5：加权随机选取
    selected  = weighted_random_select(ranked)
    total_ms  = (time.perf_counter() - t_total) * 1000

    logger.info(
        f"\n[选题耗时] 总计: {total_ms:.0f}ms "
        f"(LLM={llm_ms:.0f}ms, 检索={retrieve_ms:.0f}ms)\n"
        f"[选题结果] [{selected.get('difficulty','?').upper()}] "
        f"{selected.get('title','?')} "
        f"(score={selected.get('final_score', 0):.3f})"
    )

    # select_reason 和 focus_point 由规则生成，不依赖 LLM
    reason, focus = _make_reason_and_focus(intent, profile)
    return selected, reason, focus