"""
LLM 客户端
根据 llm_provider 配置自动选择调用方式：
- ollama：走 /api/chat 原生接口，支持 think=false
- 其他（qwen/deepseek）：走 OpenAI 兼容接口
"""
import json
import logging
from typing import AsyncGenerator

import httpx
from openai import AsyncOpenAI

from app.core.config import get_settings

logger       = logging.getLogger(__name__)
app_settings = get_settings()

# OpenAI 兼容客户端（线上 QWen/DeepSeek 用）
_openai_client = AsyncOpenAI(
    api_key=app_settings.llm_api_key,
    base_url=app_settings.llm_base_url,
)

# Embedding 客户端（OpenAI 兼容，Ollama 也支持 /v1/embeddings）
embedding_client = AsyncOpenAI(
    api_key=app_settings.embedding_api_key,
    base_url=app_settings.embedding_base_url,
)


def _is_ollama() -> bool:
    return app_settings.llm_provider.lower() == "ollama"


def _ollama_base_url() -> str:
    """从 /v1 路径还原出 Ollama 根路径"""
    return app_settings.llm_base_url.replace("/v1", "")


# ─── 非流式调用 ───────────────────────────────────────────────────────────────

async def chat_completion(
    messages:    list[dict],
    model:       str | None   = None,
    max_tokens:  int | None   = None,
    temperature: float | None = None,
) -> str:
    """
    非流式 LLM 调用，返回完整文本。
    自动根据 llm_provider 选择调用方式。
    """
    model       = model       or app_settings.llm_model
    max_tokens  = max_tokens  or app_settings.llm_max_tokens
    temperature = temperature or app_settings.llm_temperature

    if _is_ollama():
        print("ollama chat completion:", messages, model, max_tokens, temperature)
        return await _ollama_chat(messages, model, max_tokens, temperature)
    else:
        return await _openai_chat(messages, model, max_tokens, temperature)


async def _ollama_chat(
    messages:    list[dict],
    model:       str,
    max_tokens:  int,
    temperature: float,
) -> str:
    """Ollama 原生接口，think=false 关闭思考模式"""
    async with httpx.AsyncClient(
        timeout=app_settings.llm_timeout_analyze + 10
    ) as client:
        resp = await client.post(
            f"{_ollama_base_url()}/api/chat",
            json={
                "model":    model,
                "messages": messages,
                "think":    False,
                "stream":   False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            },
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


async def _openai_chat(
    messages:    list[dict],
    model:       str,
    max_tokens:  int,
    temperature: float,
) -> str:
    """OpenAI 兼容接口（QWen/DeepSeek）"""
    resp = await _openai_client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


# ─── 流式调用 ─────────────────────────────────────────────────────────────────

async def chat_stream(
    messages:    list[dict],
    model:       str | None   = None,
    max_tokens:  int | None   = None,
    temperature: float | None = None,
) -> AsyncGenerator[str, None]:
    """
    流式 LLM 调用，yield 文本片段。
    用法：async for chunk in chat_stream(...): ...
    """
    model       = model       or app_settings.llm_model
    max_tokens  = max_tokens  or app_settings.llm_max_tokens
    temperature = temperature or app_settings.llm_temperature

    if _is_ollama():
        async for chunk in _ollama_stream(messages, model, max_tokens, temperature):
            yield chunk
    else:
        async for chunk in _openai_stream(messages, model, max_tokens, temperature):
            yield chunk


async def _ollama_stream(
    messages:    list[dict],
    model:       str,
    max_tokens:  int,
    temperature: float,
) -> AsyncGenerator[str, None]:
    """Ollama 流式，解析 NDJSON 格式"""
    async with httpx.AsyncClient(
        timeout=app_settings.llm_timeout_analyze + 10
    ) as client:
        async with client.stream(
            "POST",
            f"{_ollama_base_url()}/api/chat",
            json={
                "model":    model,
                "messages": messages,
                "think":    False,
                "stream":   True,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data    = json.loads(line)
                    content = data.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if data.get("done"):
                        break
                except json.JSONDecodeError:
                    continue


async def _openai_stream(
    messages:    list[dict],
    model:       str,
    max_tokens:  int,
    temperature: float,
) -> AsyncGenerator[str, None]:
    """OpenAI 兼容流式"""
    stream = await _openai_client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content


# ─── Embedding ────────────────────────────────────────────────────────────────

async def get_embedding(text: str) -> list[float]:
    """
    生成文本向量。
    Ollama 的 /v1/embeddings 兼容 OpenAI SDK，无需特殊处理。
    """
    resp = await embedding_client.embeddings.create(
        model=app_settings.embedding_model,
        input=text,
    )
    return resp.data[0].embedding