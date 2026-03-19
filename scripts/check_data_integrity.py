"""
数据完整性检查与清理脚本

数据层级（从上到下建立）：
  PostgreSQL questions → Qdrant vectors → PostgreSQL test_cases

检查三类问题：
  1. DB 有题目但 Qdrant 无向量   → 删除 DB 题目（向量都没有，题目无法被选到）
  2. Qdrant 有向量但 DB 无题目   → 删除 Qdrant 向量（选题后查 DB 会 500）★ 本次 bug 根因
  3. DB 有题目但无测试用例        → 删除题目 + 向量（判题无法进行）

用法：
    python scripts/check_data_integrity.py          # 预览
    python scripts/check_data_integrity.py --fix    # 执行清理
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


# ─── 数据收集 ─────────────────────────────────────────────────────────────────

async def collect_db_state(db: AsyncSession) -> tuple[dict, set]:
    """返回 (questions_dict: {id: Question}, ids_with_cases: set)"""
    q_result  = await db.execute(select(Question).where(Question.is_indexed == True))
    questions = {q.id: q for q in q_result.scalars().all()}

    tc_result    = await db.execute(select(TestCase.question_id).distinct())
    ids_with_cases = {row[0] for row in tc_result.all()}

    return questions, ids_with_cases


async def collect_qdrant_ids(qdrant) -> set[int]:
    """拉取 Qdrant 中所有点的 ID"""
    ids    = set()
    offset = None

    while True:
        results, next_offset = await qdrant.scroll(
            collection_name=settings.qdrant_collection,
            limit=100,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        for point in results:
            ids.add(point.id)

        if next_offset is None:
            break
        offset = next_offset

    return ids


# ─── 三类问题检查 ─────────────────────────────────────────────────────────────

async def check_db_no_vector(
    db_ids:     set[int],
    qdrant_ids: set[int],
    questions:  dict,
    db:         AsyncSession,
    qdrant,
    fix:        bool,
) -> int:
    """问题1：DB 有题目但 Qdrant 无向量"""
    missing_vector = db_ids - qdrant_ids
    if not missing_vector:
        logger.info("✅ 问题1：所有 DB 题目在 Qdrant 都有向量")
        return 0

    logger.warning(f"⚠️  问题1：{len(missing_vector)} 道题目在 Qdrant 无向量")
    for qid in sorted(missing_vector):
        q = questions.get(qid)
        logger.warning(f"   [{q.difficulty.upper():6s}] {q.title} (id={qid})")

    if not fix:
        logger.info("   → 预览模式，加 --fix 执行")
        return len(missing_vector)

    # 删除顺序：先删 test_cases，再删 questions（避免外键约束）
    await db.execute(delete(TestCase).where(TestCase.question_id.in_(missing_vector)))
    await db.execute(delete(Question).where(Question.id.in_(missing_vector)))
    logger.info(f"   DB：已删除 {len(missing_vector)} 道题目及其测试用例")
    return len(missing_vector)


async def check_vector_no_db(
    db_ids:     set[int],
    qdrant_ids: set[int],
    qdrant,
    fix:        bool,
) -> int:
    """问题2：Qdrant 有向量但 DB 无题目（选题后查 DB 会 500，本次 bug 根因）"""
    orphan_vectors = qdrant_ids - db_ids
    if not orphan_vectors:
        logger.info("✅ 问题2：Qdrant 无孤立向量")
        return 0

    logger.warning(f"⚠️  问题2：{len(orphan_vectors)} 条 Qdrant 向量在 DB 无对应题目")
    logger.warning(f"   孤立 ID: {sorted(orphan_vectors)}")
    logger.warning("   ⬆ 这是选题成功但创建 session 500 的根本原因")

    if not fix:
        logger.info("   → 预览模式，加 --fix 执行")
        return len(orphan_vectors)

    from qdrant_client.models import PointIdsList
    await qdrant.delete(
        collection_name=settings.qdrant_collection,
        points_selector=PointIdsList(points=list(orphan_vectors)),
    )
    logger.info(f"   Qdrant：已删除 {len(orphan_vectors)} 条孤立向量")
    return len(orphan_vectors)


async def check_no_test_cases(
    questions:      dict,
    ids_with_cases: set[int],
    db:             AsyncSession,
    qdrant,
    fix:            bool,
) -> int:
    """问题3：DB 有题目但无测试用例"""
    no_cases = {qid for qid in questions if qid not in ids_with_cases}
    if not no_cases:
        logger.info("✅ 问题3：所有题目都有测试用例")
        return 0

    logger.warning(f"⚠️  问题3：{len(no_cases)} 道题目无测试用例")
    for qid in sorted(no_cases):
        q = questions[qid]
        logger.warning(f"   [{q.difficulty.upper():6s}] {q.title} (id={qid})")

    if not fix:
        logger.info("   → 预览模式，加 --fix 执行")
        return len(no_cases)

    # 删 Qdrant 向量
    try:
        from qdrant_client.models import PointIdsList
        await qdrant.delete(
            collection_name=settings.qdrant_collection,
            points_selector=PointIdsList(points=list(no_cases)),
        )
        logger.info(f"   Qdrant：已删除 {len(no_cases)} 条向量")
    except Exception as e:
        logger.warning(f"   Qdrant 删除失败（继续）: {e}")

    # 先删 test_cases（虽然没有，但保证顺序正确），再删 questions
    await db.execute(delete(TestCase).where(TestCase.question_id.in_(no_cases)))
    await db.execute(delete(Question).where(Question.id.in_(no_cases)))
    logger.info(f"   DB：已删除 {len(no_cases)} 道题目")
    return len(no_cases)


# ─── 数据概况 ─────────────────────────────────────────────────────────────────

def print_summary(questions: dict, ids_with_cases: set, qdrant_ids: set) -> None:
    by_diff: dict = {}
    for q in questions.values():
        d = q.difficulty
        by_diff.setdefault(d, {"total": 0, "has_vector": 0, "has_cases": 0})
        by_diff[d]["total"]    += 1
        if q.id in qdrant_ids:    by_diff[d]["has_vector"] += 1
        if q.id in ids_with_cases: by_diff[d]["has_cases"]  += 1

    logger.info("\n" + "="*60)
    logger.info("数据概况")
    logger.info("="*60)
    logger.info(f"{'难度':<8} {'DB题数':>6} {'有向量':>6} {'有用例':>6} {'完整率':>8}")
    logger.info("-"*45)
    total_q = total_v = total_c = 0
    for diff in ("easy", "medium", "hard"):
        if diff not in by_diff:
            continue
        d    = by_diff[diff]
        rate = d["has_cases"] / d["total"] * 100 if d["total"] else 0
        logger.info(f"{diff:<8} {d['total']:>6} {d['has_vector']:>6} {d['has_cases']:>6} {rate:>7.0f}%")
        total_q += d["total"]
        total_v += d["has_vector"]
        total_c += d["has_cases"]

    rate = total_c / total_q * 100 if total_q else 0
    logger.info("-"*45)
    logger.info(f"{'合计':<8} {total_q:>6} {total_v:>6} {total_c:>6} {rate:>7.0f}%")
    logger.info(f"\nQdrant 总向量数: {len(qdrant_ids)}")
    logger.info("="*60 + "\n")


# ─── 主流程 ───────────────────────────────────────────────────────────────────

async def run(fix: bool) -> None:
    engine            = create_async_engine(settings.database_url, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from qdrant_client import AsyncQdrantClient
    qdrant = AsyncQdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)

    async with AsyncSessionLocal() as db:
        # 收集当前状态
        questions, ids_with_cases = await collect_db_state(db)
        qdrant_ids                = await collect_qdrant_ids(qdrant)
        db_ids                    = set(questions.keys())

        # 打印概况
        print_summary(questions, ids_with_cases, qdrant_ids)

        total_issues = 0
        total_issues += await check_db_no_vector(db_ids, qdrant_ids, questions, db, qdrant, fix)
        total_issues += await check_vector_no_db(db_ids, qdrant_ids, qdrant, fix)
        total_issues += await check_no_test_cases(questions, ids_with_cases, db, qdrant, fix)

        if fix and total_issues > 0:
            await db.commit()
            logger.info(f"\n✅ 清理完成，共处理 {total_issues} 个问题")
        elif total_issues == 0:
            logger.info("✅ 数据完整，无需清理")
        else:
            logger.info(f"\n📋 发现 {total_issues} 个问题，加 --fix 执行清理")

    await qdrant.close()
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="检查并修复数据完整性")
    parser.add_argument("--fix", action="store_true", help="执行清理（默认预览模式）")
    args = parser.parse_args()

    if not args.fix:
        logger.info("📋 预览模式（加 --fix 执行实际清理）\n")

    asyncio.run(run(fix=args.fix))