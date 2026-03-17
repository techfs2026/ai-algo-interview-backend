from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams
from app.core.config import get_settings

settings       = get_settings()
qdrant_client: AsyncQdrantClient | None = None

VECTOR_SIZE = settings.embedding_vector_size  # 从配置读取，本地768，线上1536


async def init_qdrant() -> None:
    global qdrant_client
    qdrant_client = AsyncQdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
    )
    # 确保collection存在
    collections = await qdrant_client.get_collections()
    names = [c.name for c in collections.collections]

    if settings.qdrant_collection not in names:
        await qdrant_client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        )


async def close_qdrant() -> None:
    if qdrant_client:
        await qdrant_client.close()


async def get_qdrant() -> AsyncQdrantClient:
    """FastAPI依赖注入"""
    if qdrant_client is None:
        raise RuntimeError("Qdrant未初始化")
    return qdrant_client