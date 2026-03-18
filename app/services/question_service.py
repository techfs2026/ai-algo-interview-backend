"""
题目查询服务
负责从 PostgreSQL 查题目元数据，从 Redis 缓存题目内容
"""
import json
import logging

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redis_client import get_redis
from app.models.models import Question

logger   = logging.getLogger(__name__)
settings = get_settings()

LEETCODE_GRAPHQL = "https://leetcode.com/graphql"

CONTENT_QUERY = """
query questionData($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionId title titleSlug difficulty
    content
    codeSnippets { lang langSlug code }
    sampleTestCase hints
  }
}
"""


class QuestionService:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, question_id: int) -> Question | None:
        result = await self.db.execute(
            select(Question).where(Question.id == question_id)
        )
        return result.scalar_one_or_none()

    async def get_indexed_count(self) -> int:
        """已入库向量的题目数量"""
        result = await self.db.execute(
            select(func.count()).where(Question.is_indexed == True)
        )
        return result.scalar_one()

    async def get_content(self, title_slug: str) -> dict | None:
        """
        获取题目完整内容（包含描述、代码模板等）。
        优先从 Redis 缓存读取，未命中则实时调用 LeetCode API。
        """
        redis = await get_redis()
        cache_key = f"question_content:{title_slug}"

        # 查缓存
        cached = await redis.get(cache_key)
        if cached:
            logger.debug(f"缓存命中: {title_slug}")
            return json.loads(cached)

        # 调 LeetCode API
        content = await self._fetch_content_from_leetcode(title_slug)
        if content:
            await redis.setex(
                cache_key,
                settings.question_cache_ttl,
                json.dumps(content, ensure_ascii=False),
            )
        return content

    async def _fetch_content_from_leetcode(self, title_slug: str) -> dict | None:
        """从 LeetCode GraphQL API 拉取题目内容"""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
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
                    return None

                # 整理返回结构
                return {
                    "id":            int(q["questionId"]),
                    "title":         q["title"],
                    "title_slug":    q["titleSlug"],
                    "difficulty":    q["difficulty"].lower(),
                    "content":       q.get("content", ""),
                    "code_snippets": q.get("codeSnippets", []),
                    "sample_testcase": q.get("sampleTestCase", ""),
                    "hints":         q.get("hints", []),
                }
        except Exception as e:
            logger.error(f"拉取题目内容失败 [{title_slug}]: {e}")
            return None