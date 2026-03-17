"""
LLM客户端
DeepSeek和QWen都兼容OpenAI SDK格式，通过配置切换，无需改代码。
"""
from openai import AsyncOpenAI
from app.core.config import get_settings

app_settings = get_settings()

# 主模型客户端（深度分析用）
llm_client = AsyncOpenAI(
    api_key=app_settings.llm_api_key,
    base_url=app_settings.llm_base_url,
)

# Embedding客户端
embedding_client = AsyncOpenAI(
    api_key=app_settings.embedding_api_key,
    base_url=app_settings.embedding_base_url,
)


async def get_embedding(text: str) -> list[float]:
    """生成文本向量"""
    resp = await embedding_client.embeddings.create(
        model=app_settings.embedding_model,
        input=text,
    )
    return resp.data[0].embedding