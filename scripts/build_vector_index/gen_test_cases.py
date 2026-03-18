"""
测试用例生成脚本

为已入库的题目生成测试用例，存入 test_cases 表。
测试用例来源：
1. 优先从 LeetCode API 的题目 HTML 解析示例（免费，准确）
2. 解析失败时用 LLM 补充生成

用法：
    # 为所有已入库题目生成测试用例
    python scripts/gen_test_cases.py

    # 只处理 easy 题
    python scripts/gen_test_cases.py --difficulty easy

    # 限制数量（测试用）
    python scripts/gen_test_cases.py --limit 10

    # 强制重新生成（覆盖已有的）
    python scripts/gen_test_cases.py --force
"""
import asyncio
import argparse
import logging
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.models import Question, TestCase
from app.services.judge_service import parse_examples

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger   = logging.getLogger(__name__)
settings = get_settings()

LEETCODE_GRAPHQL = "https://leetcode.com/graphql"

CONTENT_QUERY = """
query questionData($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionId title titleSlug
    content
    sampleTestCase
    exampleTestcases
  }
}
"""

# ─── LLM 生成测试用例（备用方案）─────────────────────────────────────────────

GEN_PROMPT = """/no_think
为以下算法题生成测试用例。

题目：{title}
难度：{difficulty}
标签：{tags}

生成 3 个测试用例（输入 + 期望输出），包含：
- 1 个普通用例
- 1 个边界用例（空数组/单元素/负数/零等）
- 1 个稍复杂的用例

注意：
- input 格式要和 LeetCode 示例一致，如 "nums = [2,7,11,15]\\ntarget = 9"
- expected 是函数的返回值，如 "[0,1]"
- 不要包含 "Output:" 前缀

只输出 JSON，不要任何其他文字：
{{
  "cases": [
    {{"input": "...", "expected": "...", "type": "normal"}},
    {{"input": "...", "expected": "...", "type": "edge"}},
    {{"input": "...", "expected": "...", "type": "normal"}}
  ]
}}"""


async def llm_gen_cases(question: Question) -> list[dict]:
    """用 LLM 生成测试用例（解析失败时的备用方案）"""
    import httpx as _httpx
    import json

    prompt = GEN_PROMPT.format(
        title=question.title,
        difficulty=question.difficulty,
        tags="、".join(question.tags or []),
    )

    try:
        if settings.llm_provider == "ollama":
            base = settings.llm_base_url.replace("/v1", "")
            async with _httpx.AsyncClient(timeout=60) as c:
                r = await c.post(f"{base}/api/chat", json={
                    "model":   settings.llm_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "think":   False,
                    "stream":  False,
                    "options": {"temperature": 0.2},
                })
                raw = r.json()["message"]["content"]
        else:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
            )
            resp = await client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
                temperature=0.2,
            )
            raw = resp.choices[0].message.content or ""

        # 提取 JSON
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return []
        data = json.loads(match.group(0))
        return data.get("cases", [])

    except Exception as e:
        logger.warning(f"LLM 生成测试用例失败 [{question.title}]: {e}")
        return []


# ─── 从 LeetCode API 获取示例 ─────────────────────────────────────────────────

async def fetch_examples(title_slug: str, client: httpx.AsyncClient) -> list[dict]:
    """从 LeetCode API 拉取题目内容，解析示例测试用例"""
    try:
        resp = await client.post(
            LEETCODE_GRAPHQL,
            json={
                "query":     CONTENT_QUERY,
                "variables": {"titleSlug": title_slug},
            },
            headers={
                "Content-Type": "application/json",
                "User-Agent":   "Mozilla/5.0",
                "Referer":      "https://leetcode.com/problemset/",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        q    = data.get("data", {}).get("question")
        if not q:
            return []

        # 从 HTML 解析示例
        examples = parse_examples(q.get("content", ""))
        return examples

    except Exception as e:
        logger.warning(f"拉取题目内容失败 [{title_slug}]: {e}")
        return []


# ─── 主处理函数 ───────────────────────────────────────────────────────────────

async def process_question(
    question:    Question,
    http_client: httpx.AsyncClient,
    db:          AsyncSession,
    force:       bool = False,
) -> str:
    """
    为单道题生成测试用例。
    返回："ok" | "skip" | "fail"
    """
    # 检查是否已有测试用例
    if not force:
        result = await db.execute(
            select(TestCase).where(TestCase.question_id == question.id).limit(1)
        )
        if result.scalar_one_or_none():
            return "skip"

    # 如果 force，先删除已有的
    if force:
        existing = await db.execute(
            select(TestCase).where(TestCase.question_id == question.id)
        )
        for tc in existing.scalars().all():
            await db.delete(tc)

    # 方式1：从 LeetCode API 解析示例
    examples = await fetch_examples(question.title_slug, http_client)

    # 方式2：LLM 生成（解析失败时）
    if not examples:
        logger.info(f"[{question.title}] HTML解析无结果，尝试LLM生成")
        llm_cases = await llm_gen_cases(question)
        examples  = [
            {"input": c["input"], "expected": c["expected"]}
            for c in llm_cases
        ]

    if not examples:
        logger.warning(f"[{question.title}] 无法生成测试用例，跳过")
        return "fail"

    # 存入数据库
    for ex in examples:
        tc = TestCase(
            question_id=question.id,
            input_data=ex["input"],
            expected=ex["expected"],
            case_type=ex.get("type", "sample"),
        )
        db.add(tc)

    await db.flush()
    logger.info(f"✅ [{question.difficulty.upper():6s}] {question.title} → {len(examples)} 个用例")
    return "ok"


# ─── 主流程 ───────────────────────────────────────────────────────────────────

async def gen_test_cases(
    difficulty: str  = "all",
    limit:      int  = 0,
    force:      bool = False,
) -> None:

    engine            = create_async_engine(settings.database_url, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with AsyncSessionLocal() as db:
        # 查询已入库的题目
        query = select(Question).where(Question.is_indexed == True)
        if difficulty != "all":
            query = query.where(Question.difficulty == difficulty)
        query = query.order_by(Question.id)

        result    = await db.execute(query)
        questions = result.scalars().all()

        if limit > 0:
            questions = questions[:limit]

        total = len(questions)
        logger.info(f"待处理题目: {total} 道 | difficulty={difficulty} force={force}")

        ok = skip = fail = 0

        async with httpx.AsyncClient(timeout=20) as http_client:
            for i, q in enumerate(questions, 1):
                status = await process_question(q, http_client, db, force=force)

                if status == "ok":    ok   += 1
                elif status == "skip": skip += 1
                else:                  fail += 1

                if i % 10 == 0:
                    await db.commit()
                    logger.info(f"进度 {i}/{total} | 成功={ok} 跳过={skip} 失败={fail}")

                # 避免频繁请求 LeetCode
                if status != "skip":
                    await asyncio.sleep(1.0)

        await db.commit()

    await engine.dispose()

    logger.info(
        f"\n{'='*40}\n"
        f"完成！成功={ok} 跳过={skip} 失败={fail}\n"
        f"{'='*40}"
    )


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="为题目生成测试用例")
    parser.add_argument("--difficulty", default="all",
                        choices=["all", "easy", "medium", "hard"])
    parser.add_argument("--limit", type=int, default=0,
                        help="限制处理数量（0=不限制）")
    parser.add_argument("--force", action="store_true",
                        help="强制重新生成（覆盖已有数据）")
    args = parser.parse_args()

    asyncio.run(gen_test_cases(
        difficulty=args.difficulty,
        limit=args.limit,
        force=args.force,
    ))