"""
题目语义扩展
用 LLM 把稀疏的元数据扩展成语义丰富的索引文本

建库场景专用：
- 本地 Ollama：走 /api/chat 原生接口，传 think=false，速度快 5~10 倍
- 线上 QWen/DeepSeek：走 OpenAI 兼容接口，云端模型速度本身够快
"""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.core.config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

# 线上用 OpenAI SDK
_openai_client = AsyncOpenAI(
    api_key=settings.llm_api_key,
    base_url=settings.llm_base_url,
)


# ─── 输出结构 ─────────────────────────────────────────────────────────────────

class SemanticExpansion(BaseModel):
    semantic_text:    str
    core_skills:      list[str]
    suitable_level:   str
    thinking_pattern: str
    common_mistakes:  str


EXPAND_PROMPT = """/no_think
你是一位算法题库专家。根据以下题目元数据，生成用于向量检索的语义描述。

题目信息：
- 标题：{title}
- 难度：{difficulty}
- 标签：{tags}
- 通过率：{ac_rate:.0%}

要求：
1. semantic_text：150字以内，描述核心考察能力、解题思路方向、适合什么阶段的学习者
2. core_skills：2-4个具体技能，如"哈希表查找"而非"哈希表"
3. suitable_level：只能是 入门/初级/中级/高级 之一
4. thinking_pattern：一句话描述解题核心思路
5. common_mistakes：一句话描述最常见的错误

必须只输出 JSON，不要任何其他文字：
{{
    "semantic_text": "...",
    "core_skills": ["...", "..."],
    "suitable_level": "...",
    "thinking_pattern": "...",
    "common_mistakes": "..."
}}"""


# ─── Ollama 原生接口调用（本地专用，支持 think=false）────────────────────────

async def _call_ollama_native(prompt: str) -> str:
    """
    走 /api/chat 原生接口，传 think=false 彻底关闭思考模式。
    仅用于本地 Ollama，线上不走这里。
    """
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{settings.llm_base_url.replace('/v1', '')}/api/chat",
            json={
                "model":   settings.llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "think":   False,
                "stream":  False,
                "options": {"temperature": 0.1},
            },
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


# ─── OpenAI SDK 调用（线上 QWen/DeepSeek）───────────────────────────────────

async def _call_openai(prompt: str) -> str:
    resp = await _openai_client.chat.completions.create(
        model=settings.llm_model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000,
        temperature=0.1,
    )
    return resp.choices[0].message.content or ""


# ─── 统一入口 ─────────────────────────────────────────────────────────────────

async def _call_llm(prompt: str) -> str:
    """
    根据 llm_provider 自动选择调用方式：
    - ollama → 原生接口（think=false，快）
    - 其他   → OpenAI 兼容接口
    """
    if settings.llm_provider == "ollama":
        return await _call_ollama_native(prompt)
    else:
        return await _call_openai(prompt)


# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def build_index_text(meta: dict, expansion: SemanticExpansion) -> str:
    """
    拼装入库文本。
    风格必须和查询时 LLM 生成的 semantic_query 保持一致。
    """
    tags_str = "、".join(meta["tags"][:5])
    return f"""题目：{meta['title']}
难度：{meta['difficulty']}
标签：{tags_str}
通过率：{meta['ac_rate']:.0%}
核心技能：{'、'.join(expansion.core_skills)}
适合水平：{expansion.suitable_level}
解题方向：{expansion.thinking_pattern}
常见错误：{expansion.common_mistakes}
语义描述：{expansion.semantic_text}""".strip()


async def expand_question_semantic(meta: dict) -> SemanticExpansion | None:
    """
    调用 LLM 对题目元数据做语义扩展。
    失败返回 None。
    """
    import re

    prompt = EXPAND_PROMPT.format(
        title=meta["title"],
        difficulty=meta["difficulty"],
        tags="、".join(meta["tags"]) if meta["tags"] else "暂无",
        ac_rate=meta["ac_rate"],
    )

    for attempt in range(2):
        try:
            raw = await _call_llm(prompt)
            logger.debug(f"[{meta['title']}] 原始输出: {repr(raw[:200])}")

            # 提取 JSON
            match = re.search(r"\{[\s\S]*\}", raw)
            if not match:
                logger.warning(f"[{meta['title']}] 未找到 JSON，attempt={attempt+1}")
                continue

            data = json.loads(match.group(0))
            return SemanticExpansion(**data)

        except json.JSONDecodeError as e:
            logger.warning(f"[{meta['title']}] JSON 解析失败: {e}, attempt={attempt+1}")
        except Exception as e:
            logger.error(f"[{meta['title']}] 调用失败: {e}")
            break

    return None