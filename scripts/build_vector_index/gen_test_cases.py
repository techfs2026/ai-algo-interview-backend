"""
测试用例生成脚本

从 LeetCode 题目 HTML 解析 Input/Output 示例，存入 test_cases 表。

设计原则：只用 HTML 解析，不用 LLM 生成。
解析失败直接跳过，保证数据干净，宁缺毋滥。

用法：
    python scripts/gen_test_cases.py
    python scripts/gen_test_cases.py --difficulty easy
    python scripts/gen_test_cases.py --limit 10
    python scripts/gen_test_cases.py --force   # 强制覆盖已有数据
"""
import asyncio
import argparse
import json
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.models import Question, TestCase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger   = logging.getLogger(__name__)
settings = get_settings()

LEETCODE_GRAPHQL = "https://leetcode.com/graphql"
CONTENT_QUERY = """
query questionData($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionId title content
  }
}
"""


# ─── HTML 解析 ────────────────────────────────────────────────────────────────

def parse_examples_from_html(html: str) -> list[dict]:
    """
    从 LeetCode 题目 HTML 解析 Input/Output 示例。
    对输出格式做合法性验证，跳过无法可靠对比的用例。
    """
    from html.parser import HTMLParser

    class PreParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.blocks  = []
            self._in_pre = False
            self._cur    = ""

        def handle_starttag(self, tag, attrs):
            if tag == "pre":
                self._in_pre = True
                self._cur    = ""

        def handle_endtag(self, tag):
            if tag == "pre" and self._in_pre:
                self._in_pre = False
                if self._cur.strip():
                    self.blocks.append(self._cur.strip())

        def handle_data(self, data):
            if self._in_pre:
                self._cur += data

        def handle_entityref(self, name):
            if self._in_pre:
                self._cur += {"lt":"<","gt":">","amp":"&","nbsp":" "}.get(name, "")

        def handle_charref(self, name):
            if self._in_pre:
                try:
                    self._cur += chr(
                        int(name[1:], 16) if name.startswith("x") else int(name)
                    )
                except Exception:
                    pass

    parser = PreParser()
    parser.feed(html or "")

    examples = []
    for block in parser.blocks:
        lines        = [l.strip() for l in block.split("\n") if l.strip()]
        input_parts  = []
        output       = ""

        for line in lines:
            low = line.lower()
            if low.startswith("input:"):
                input_parts.append(line.split(":", 1)[1].strip())
            elif low.startswith("output:"):
                output = line.split(":", 1)[1].strip()
            elif input_parts and not output and not low.startswith("explanation:"):
                input_parts.append(line)

        if not input_parts or not output:
            continue

        # 验证输出格式：必须是可靠对比的值
        if not _is_valid_output(output):
            logger.debug(f"跳过无效输出: {output!r}")
            continue

        examples.append({
            "input":    "\n".join(input_parts),
            "expected": output,
        })

    return examples


def _is_valid_output(s: str) -> bool:
    """
    判断输出是否可以被可靠地对比。
    接受：数字、布尔、列表、字符串（JSON 或 Python literal）
    拒绝：null、空值、含省略的描述性文字
    """
    s = s.strip()
    if not s or s.lower() in ("null", "none", ""):
        return False
    # 纯数字
    try:
        float(s)
        return True
    except ValueError:
        pass
    # JSON
    try:
        json.loads(s)
        return True
    except Exception:
        pass
    # Python literal（处理 True/False 等）
    try:
        import ast
        ast.literal_eval(s)
        return True
    except Exception:
        pass
    return False


# ─── 拉取题目内容 ─────────────────────────────────────────────────────────────

async def fetch_content(title_slug: str, client: httpx.AsyncClient) -> str | None:
    """拉取题目 HTML，返回 content 字段"""
    try:
        resp = await client.post(
            LEETCODE_GRAPHQL,
            json={"query": CONTENT_QUERY, "variables": {"titleSlug": title_slug}},
            headers={
                "Content-Type": "application/json",
                "User-Agent":   "Mozilla/5.0",
                "Referer":      "https://leetcode.com/problemset/",
            },
        )
        resp.raise_for_status()
        q = resp.json().get("data", {}).get("question")
        return q.get("content", "") if q else None
    except Exception as e:
        logger.warning(f"拉取内容失败 [{title_slug}]: {e}")
        return None


# ─── 单题处理 ─────────────────────────────────────────────────────────────────

async def process_one(
    question:    Question,
    http_client: httpx.AsyncClient,
    db:          AsyncSession,
    force:       bool,
) -> str:
    """返回 'ok' | 'skip' | 'fail'"""
    if not force:
        existing = await db.execute(
            select(TestCase).where(TestCase.question_id == question.id).limit(1)
        )
        if existing.scalar_one_or_none():
            return "skip"

    if force:
        existing = await db.execute(
            select(TestCase).where(TestCase.question_id == question.id)
        )
        for tc in existing.scalars().all():
            await db.delete(tc)

    html = await fetch_content(question.title_slug, http_client)
    if html is None:
        return "fail"

    # 只用 HTML 解析，不用 LLM，保证数据干净
    examples = parse_examples_from_html(html)

    if not examples:
        logger.warning(f"⚠  [{question.title}] 解析失败，跳过（宁缺毋滥）")
        return "fail"

    for ex in examples:
        db.add(TestCase(
            question_id=question.id,
            input_data=ex["input"],
            expected=ex["expected"],
            case_type="sample",
        ))

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
        query = select(Question).where(Question.is_indexed == True)
        if difficulty != "all":
            query = query.where(Question.difficulty == difficulty)
        query = query.order_by(Question.id)

        result    = await db.execute(query)
        questions = result.scalars().all()
        if limit > 0:
            questions = questions[:limit]

        total = len(questions)
        logger.info(f"待处理: {total} 道 | difficulty={difficulty} force={force}")

        ok = skip = fail = 0

        async with httpx.AsyncClient(timeout=20) as http_client:
            for i, q in enumerate(questions, 1):
                status = await process_one(q, http_client, db, force=force)

                if status == "ok":     ok   += 1
                elif status == "skip": skip += 1
                else:                  fail += 1

                if i % 10 == 0:
                    await db.commit()
                    logger.info(f"进度 {i}/{total} | ok={ok} skip={skip} fail={fail}")

                if status != "skip":
                    await asyncio.sleep(0.8)

        await db.commit()

    await engine.dispose()

    logger.info(
        f"\n{'='*40}\n"
        f"完成！\n"
        f"成功: {ok} 道\n"
        f"跳过（已有数据）: {skip} 道\n"
        f"失败（解析失败，已跳过）: {fail} 道\n"
        f"{'='*40}"
    )


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成题目测试用例（仅 HTML 解析）")
    parser.add_argument("--difficulty", default="all",
                        choices=["all", "easy", "medium", "hard"])
    parser.add_argument("--limit", type=int, default=0,
                        help="限制处理数量（0=不限制）")
    parser.add_argument("--force", action="store_true",
                        help="强制覆盖已有测试用例")
    args = parser.parse_args()
    asyncio.run(gen_test_cases(args.difficulty, args.limit, args.force))