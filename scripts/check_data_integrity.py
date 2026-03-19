"""
数据完整性检查与清理脚本

检查并修复两类问题：
1. 有题目但无测试用例 → 删除题目（同时清理 Qdrant 向量）
2. 有测试用例但题目不存在 → 删除孤立测试用例

用法：
    # 预览模式（只看问题，不改数据）
    python scripts/check_data_integrity.py

    # 执行清理
    python scripts/check_data_integrity.py --fix

    # 只检查某类问题
    python scripts/check_data_integrity.py --fix --only questions  # 只删没有用例的题目
    python scripts/check_data_integrity.py --fix --only testcases  # 只删孤立用例
"""
import asyncio
import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.models import Question, TestCase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger   = logging.getLogger(__name__)
settings = get_settings()


async def check_and_fix(fix: bool = False, only: str = "all") -> None:
    engine            = create_async_engine(settings.database_url, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Qdrant 连接（用于同步删除向量）
    from qdrant_client import AsyncQdrantClient
    qdrant = AsyncQdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)

    async with AsyncSessionLocal() as db:

        # ── 问题1：有题目但无测试用例 ────────────────────────────────────────
        if only in ("all", "questions"):
            await _check_questions_without_testcases(db, qdrant, fix)

        # ── 问题2：有测试用例但题目不存在 ───────────────────────────────────
        if only in ("all", "testcases"):
            await _check_orphan_testcases(db, fix)

        if fix:
            await db.commit()
            logger.info("✅ 数据库变更已提交")

    await qdrant.close()
    await engine.dispose()


async def _check_questions_without_testcases(
    db:     AsyncSession,
    qdrant,
    fix:    bool,
) -> None:
    """找出有题目但无测试用例的记录"""

    # 所有已入库的题目
    result    = await db.execute(select(Question).where(Question.is_indexed == True))
    questions = result.scalars().all()

    # 所有有测试用例的 question_id
    tc_result = await db.execute(select(TestCase.question_id).distinct())
    has_cases = {row[0] for row in tc_result.all()}

    no_cases = [q for q in questions if q.id not in has_cases]

    if not no_cases:
        logger.info("✅ 问题1：所有题目都有测试用例")
        return

    logger.warning(f"⚠️  问题1：{len(no_cases)} 道题目没有测试用例")
    for q in no_cases:
        logger.warning(f"   [{q.difficulty.upper():6s}] {q.title} (id={q.id})")

    if not fix:
        logger.info("   → 预览模式，跳过删除。加 --fix 执行清理")
        return

    # 删除：先删 Qdrant 向量，再删数据库记录
    ids_to_delete = [q.id for q in no_cases]

    # 删 Qdrant
    try:
        await qdrant.delete(
            collection_name=settings.qdrant_collection,
            points_selector=ids_to_delete,
        )
        logger.info(f"   Qdrant：已删除 {len(ids_to_delete)} 条向量")
    except Exception as e:
        logger.warning(f"   Qdrant 删除失败（继续）: {e}")

    # 删数据库
    await db.execute(
        delete(Question).where(Question.id.in_(ids_to_delete))
    )
    logger.info(f"   数据库：已删除 {len(ids_to_delete)} 道题目")


async def _check_orphan_testcases(db: AsyncSession, fix: bool) -> None:
    """找出有测试用例但题目不存在的孤立记录"""

    tc_result  = await db.execute(select(TestCase))
    all_cases  = tc_result.scalars().all()

    q_result   = await db.execute(select(Question.id))
    valid_qids = {row[0] for row in q_result.all()}

    orphans = [tc for tc in all_cases if tc.question_id not in valid_qids]

    if not orphans:
        logger.info("✅ 问题2：无孤立测试用例")
        return

    orphan_qids = {tc.question_id for tc in orphans}
    logger.warning(f"⚠️  问题2：{len(orphans)} 条孤立测试用例（涉及 {len(orphan_qids)} 个不存在的题目 ID）")
    for qid in sorted(orphan_qids):
        count = sum(1 for tc in orphans if tc.question_id == qid)
        logger.warning(f"   question_id={qid}：{count} 条用例")

    if not fix:
        logger.info("   → 预览模式，跳过删除。加 --fix 执行清理")
        return

    await db.execute(
        delete(TestCase).where(TestCase.question_id.in_(orphan_qids))
    )
    logger.info(f"   数据库：已删除 {len(orphans)} 条孤立测试用例")


async def print_summary(fix: bool) -> None:
    """打印数据库整体概况"""
    engine            = create_async_engine(settings.database_url, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with AsyncSessionLocal() as db:
        q_result  = await db.execute(select(Question).where(Question.is_indexed == True))
        questions = q_result.scalars().all()

        tc_result = await db.execute(select(TestCase))
        testcases = tc_result.scalars().all()

        tc_by_q = {}
        for tc in testcases:
            tc_by_q.setdefault(tc.question_id, 0)
            tc_by_q[tc.question_id] += 1

        by_diff = {}
        for q in questions:
            by_diff.setdefault(q.difficulty, {"total": 0, "with_cases": 0})
            by_diff[q.difficulty]["total"] += 1
            if q.id in tc_by_q:
                by_diff[q.difficulty]["with_cases"] += 1

    await engine.dispose()

    logger.info("\n" + "="*50)
    logger.info("数据库概况")
    logger.info("="*50)
    logger.info(f"{'难度':<10} {'总题数':>6} {'有用例':>6} {'覆盖率':>8}")
    logger.info("-"*35)
    total_q = total_c = 0
    for diff in ("easy", "medium", "hard"):
        if diff not in by_diff:
            continue
        d     = by_diff[diff]
        rate  = d["with_cases"] / d["total"] * 100 if d["total"] else 0
        logger.info(f"{diff:<10} {d['total']:>6} {d['with_cases']:>6} {rate:>7.0f}%")
        total_q += d["total"]
        total_c += d["with_cases"]

    rate = total_c / total_q * 100 if total_q else 0
    logger.info("-"*35)
    logger.info(f"{'合计':<10} {total_q:>6} {total_c:>6} {rate:>7.0f}%")
    logger.info("="*50 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="检查并修复题目与测试用例的数据一致性")
    parser.add_argument("--fix",  action="store_true", help="执行清理（默认预览模式）")
    parser.add_argument("--only", default="all",
                        choices=["all", "questions", "testcases"],
                        help="只处理某类问题")
    args = parser.parse_args()

    if not args.fix:
        logger.info("📋 预览模式（加 --fix 执行实际清理）\n")

    asyncio.run(print_summary(args.fix))
    asyncio.run(check_and_fix(fix=args.fix, only=args.only))