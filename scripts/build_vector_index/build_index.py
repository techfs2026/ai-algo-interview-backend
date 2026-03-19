"""
向量建库主脚本

位置：scripts/build_vector_index/build_index.py

用法：
    python scripts/build_vector_index/build_index.py
    python scripts/build_vector_index/build_index.py --difficulty easy
    python scripts/build_vector_index/build_index.py --limit 5
    python scripts/build_vector_index/build_index.py --resume
"""
import asyncio
import argparse
import logging
import sys
import os
import time

# 项目根目录（build_vector_index 的上上级）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import httpx
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance, PayloadSchemaType
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from app.core.config import get_settings
from app.models.models import Question
from scripts.build_vector_index.leetcode_client import fetch_question_by_slug, parse_question_meta
from scripts.build_vector_index.semantic_expander import expand_question_semantic, build_index_text
from scripts.build_vector_index.question_slugs import get_slugs_by_difficulty

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger   = logging.getLogger(__name__)
settings = get_settings()

VECTOR_SIZE   = settings.embedding_vector_size
BATCH_SIZE    = 3
REQUEST_DELAY = 1.5


# ─── Embedding ────────────────────────────────────────────────────────────────

embedding_client = AsyncOpenAI(
    api_key=settings.embedding_api_key,
    base_url=settings.embedding_base_url,
)


async def get_embedding(text: str) -> list[float]:
    resp = await embedding_client.embeddings.create(
        model=settings.embedding_model,
        input=text,
    )
    return resp.data[0].embedding


# ─── Qdrant 初始化 ────────────────────────────────────────────────────────────

async def ensure_collection(client: AsyncQdrantClient) -> None:
    collections = await client.get_collections()
    names       = [c.name for c in collections.collections]

    if settings.qdrant_collection not in names:
        await client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        logger.info(f"创建 collection: {settings.qdrant_collection}")
    else:
        logger.info(f"collection 已存在: {settings.qdrant_collection}")

    # 幂等建索引，已存在不报错
    await client.create_payload_index(
        collection_name=settings.qdrant_collection,
        field_name="difficulty",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    await client.create_payload_index(
        collection_name=settings.qdrant_collection,
        field_name="ac_rate",
        field_schema=PayloadSchemaType.FLOAT,
    )
    logger.info("payload 索引已就绪 (difficulty, ac_rate)")


# ─── 单题处理 ─────────────────────────────────────────────────────────────────

async def process_one(
    slug:        str,
    http_client: httpx.AsyncClient,
    qdrant:      AsyncQdrantClient,
    db:          AsyncSession,
    resume:      bool,
) -> str:
    """返回 'ok' | 'skip' | 'fail'"""
    if resume:
        result   = await db.execute(select(Question).where(Question.title_slug == slug))
        existing = result.scalar_one_or_none()
        if existing and existing.is_indexed:
            return "skip"

    # 1. 拉取题目
    raw = await fetch_question_by_slug(slug, client=http_client)
    if not raw:
        return "fail"

    meta = parse_question_meta(raw)
    if meta["is_paid"]:
        logger.debug(f"跳过付费题: {slug}")
        return "skip"

    # 2. LLM 语义扩展
    expansion = await expand_question_semantic(meta)
    if not expansion:
        logger.warning(f"语义扩展失败: {meta['title']}")
        return "fail"

    # 3. Embedding
    index_text = build_index_text(meta, expansion)
    try:
        vector = await get_embedding(index_text)
    except Exception as e:
        logger.error(f"Embedding 失败 [{meta['title']}]: {e}")
        return "fail"

    # 4. 存 PostgreSQL
    result = await db.execute(select(Question).where(Question.id == meta["id"]))
    q      = result.scalar_one_or_none()
    if q is None:
        q = Question(
            id=meta["id"],
            title=meta["title"],
            title_slug=meta["title_slug"],
            difficulty=meta["difficulty"],
            is_paid=meta["is_paid"],
            tags=meta["tags"],
            ac_rate=meta["ac_rate"],
        )
        db.add(q)

    q.core_skills      = expansion.core_skills
    q.suitable_level   = expansion.suitable_level
    q.thinking_pattern = expansion.thinking_pattern
    q.semantic_text    = expansion.semantic_text
    q.is_indexed       = True
    await db.flush()

    # 5. 存 Qdrant
    await qdrant.upsert(
        collection_name=settings.qdrant_collection,
        points=[PointStruct(
            id=meta["id"],
            vector=vector,
            payload={
                "title":          meta["title"],
                "title_slug":     meta["title_slug"],
                "difficulty":     meta["difficulty"],
                "tags":           meta["tags"],
                "ac_rate":        meta["ac_rate"],
                "core_skills":    expansion.core_skills,
                "suitable_level": expansion.suitable_level,
                "index_text":     index_text,
            }
        )],
    )

    logger.info(f"✅ [{meta['difficulty'].upper():6s}] {meta['title']}")
    return "ok"


# ─── 主流程 ───────────────────────────────────────────────────────────────────

async def build_index(difficulty: str = "all", limit: int = 0, resume: bool = False) -> None:
    slugs = get_slugs_by_difficulty(difficulty)
    if limit > 0:
        slugs = slugs[:limit]

    total = len(slugs)
    logger.info(f"开始建库 | 题目数={total} difficulty={difficulty} resume={resume}")

    engine            = create_async_engine(settings.database_url, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    qdrant            = AsyncQdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)

    await ensure_collection(qdrant)

    ok = skip = fail = 0
    start = time.time()

    async with httpx.AsyncClient(timeout=30) as http_client:
        async with AsyncSessionLocal() as db:
            for i in range(0, total, BATCH_SIZE):
                batch   = slugs[i:i + BATCH_SIZE]
                results = await asyncio.gather(
                    *[process_one(s, http_client, qdrant, db, resume) for s in batch],
                    return_exceptions=True,
                )

                for r in results:
                    if isinstance(r, Exception):
                        fail += 1
                        logger.error(f"异常: {r}")
                    elif r == "ok":    ok   += 1
                    elif r == "skip":  skip += 1
                    else:              fail += 1

                await db.commit()

                done = min(i + BATCH_SIZE, total)
                logger.info(f"进度 {done}/{total} | 成功={ok} 跳过={skip} 失败={fail}")

                if done < total:
                    await asyncio.sleep(REQUEST_DELAY)

    await qdrant.close()
    await engine.dispose()

    elapsed = time.time() - start
    logger.info(
        f"\n{'='*40}\n"
        f"建库完成！耗时 {elapsed:.0f}s\n"
        f"成功={ok} | 跳过={skip} | 失败={fail}\n"
        f"{'='*40}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LeetCode 题目向量建库")
    parser.add_argument("--difficulty", default="all", choices=["all", "easy", "medium", "hard"])
    parser.add_argument("--limit",  type=int, default=0)
    parser.add_argument("--resume", action="store_true", help="断点续传，跳过已入库题目")
    args = parser.parse_args()
    asyncio.run(build_index(args.difficulty, args.limit, args.resume))