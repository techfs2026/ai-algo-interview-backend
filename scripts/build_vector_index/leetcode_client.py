"""
LeetCode GraphQL 客户端
只拉取元数据和题目内容，按需缓存到 Redis（TTL 7天）
"""
import asyncio
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

LEETCODE_GRAPHQL = "https://leetcode.com/graphql"

# 只拉取需要的字段
QUESTION_QUERY = """
query questionData($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionId
    title
    titleSlug
    difficulty
    isPaidOnly
    content
    topicTags { name slug }
    stats
    codeSnippets { lang langSlug code }
    sampleTestCase
    hints
  }
}
"""

# 题号 → slug 映射查询
QUESTION_LIST_QUERY = """
query problemsetQuestionList($skip: Int!, $limit: Int!) {
  problemsetQuestionList: questionList(
    categorySlug: ""
    limit: $limit
    skip: $skip
    filters: {}
  ) {
    total
    questions: data {
      questionId
      titleSlug
      difficulty
      isPaidOnly
      topicTags { name }
      stats
    }
  }
}
"""

HEADERS = {
    "Content-Type":  "application/json",
    "User-Agent":    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer":       "https://leetcode.com/problemset/",
    "Origin":        "https://leetcode.com",
}


async def fetch_question_list(
    skip: int = 0,
    limit: int = 50,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """
    拉取题目列表（元数据，不含题目描述）。
    用于建库时批量获取题号和 slug。
    """
    payload = {
        "query":     QUESTION_LIST_QUERY,
        "variables": {"skip": skip, "limit": limit},
    }
    should_close = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30, headers=HEADERS)

    try:
        resp = await client.post(LEETCODE_GRAPHQL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("problemsetQuestionList", {})
    except Exception as e:
        logger.error(f"拉取题目列表失败: {e}")
        return {}
    finally:
        if should_close:
            await client.aclose()


async def fetch_question_by_slug(
    title_slug: str,
    client: httpx.AsyncClient | None = None,
) -> dict | None:
    """
    通过 titleSlug 拉取单道题目的完整信息。
    返回原始 API 数据，None 表示失败或付费题。
    """
    payload = {
        "query":     QUESTION_QUERY,
        "variables": {"titleSlug": title_slug},
    }
    should_close = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30, headers=HEADERS)

    try:
        resp = await client.post(LEETCODE_GRAPHQL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        q    = data.get("data", {}).get("question")
        if not q:
            return None
        if q.get("isPaidOnly"):
            logger.debug(f"跳过付费题: {title_slug}")
            return None
        return q
    except Exception as e:
        logger.error(f"拉取题目失败 [{title_slug}]: {e}")
        return None
    finally:
        if should_close:
            await client.aclose()


def parse_ac_rate(stats_str: str) -> float:
    """从 stats JSON 字符串里解析通过率"""
    try:
        stats = json.loads(stats_str)
        rate  = stats.get("acRate", "0%")
        return float(str(rate).strip("%")) / 100
    except Exception:
        return 0.5


def parse_question_meta(raw: dict) -> dict:
    """
    从原始 API 数据提取元数据（用于建库和数据库存储）。
    tags 在这里统一转成中文，后续所有逻辑都用中文 tag。
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.core.tag_mapping import tags_to_zh

    en_tags = [t["name"] for t in raw.get("topicTags", [])]
    return {
        "id":          int(raw["questionId"]),
        "title":       raw["title"],
        "title_slug":  raw["titleSlug"],
        "difficulty":  raw["difficulty"].lower(),
        "is_paid":     raw.get("isPaidOnly", False),
        "tags":        tags_to_zh(en_tags),   # ← 存中文 tag
        "ac_rate":     parse_ac_rate(raw.get("stats", "{}")),
        # 内容字段（缓存用）
        "content":        raw.get("content", ""),
        "code_snippets":  raw.get("codeSnippets", []),
        "sample_testcase":raw.get("sampleTestCase", ""),
        "hints":          raw.get("hints", []),
    }