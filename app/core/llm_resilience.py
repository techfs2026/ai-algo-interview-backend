"""
LLM调用层 - 统一可靠性框架

三层处理链：本地修复 → 带上下文重试 → 场景化降级
所有LLM调用点通过此框架，不直接调用llm_client。
"""
import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Awaitable

from pydantic import BaseModel

from app.core.config import get_settings
from app.core.llm_client import chat_completion, chat_stream

logger   = logging.getLogger(__name__)
settings = get_settings()


# ─── 埋点数据结构 ─────────────────────────────────────────────────────────────

class LLMCallMetrics(BaseModel):
    scene:          str
    attempts:       int   = 0
    repair_success: bool  = False
    fallback_used:  bool  = False
    total_latency:  float = 0.0
    failure_reason: str   = ""
    tokens_used:    int   = 0


# ─── 第一层：本地修复 ─────────────────────────────────────────────────────────

def repair_json_output(raw: str) -> str | None:
    """
    尝试从LLM输出里修复/提取合法JSON。
    返回修复后的JSON字符串，失败返回None。

    覆盖场景：
    1. JSON被markdown代码块包裹
    2. JSON前后有多余说明文字
    3. 首尾空白
    """
    if not raw or not raw.strip():
        return None

    text = raw.strip()

    # 场景1：被markdown包裹 ```json ... ```
    md_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if md_match:
        text = md_match.group(1).strip()

    # 场景2：提取第一个完整JSON对象
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        text = brace_match.group(0)

    # 验证是否是合法JSON
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        return None


def coerce_types(data: dict, schema: type[BaseModel]) -> dict:
    """
    Pydantic coerce模式类型强转。
    "85" → 85，"true" → True 等安全转换。
    """
    try:
        return schema.model_validate(data).model_dump()
    except Exception:
        return data


# ─── 第二层：带上下文重试 ─────────────────────────────────────────────────────

def build_retry_messages(
    original_messages: list[dict],
    failed_output: str,
    missing_fields: list[str] | None = None,
) -> list[dict]:
    """
    构造重试消息，把失败输出反馈给LLM让其自我修正。
    比原样重试效果好得多。
    """
    hint = "你的输出格式不正确。"
    if missing_fields:
        hint += f"缺少字段：{', '.join(missing_fields)}。"
    hint += "请严格按照JSON格式重新输出，不要包含任何其他文字和markdown标记。"

    return original_messages + [
        {"role": "assistant", "content": failed_output},
        {"role": "user",      "content": hint},
    ]


# ─── 核心框架 ─────────────────────────────────────────────────────────────────

async def llm_call_with_resilience(
    messages:    list[dict],
    scene:       str,
    schema:      type[BaseModel] | None = None,
    fallback_fn: Callable[[], Any] | None = None,
    timeout:     int | None = None,
    model:       str | None = None,
    stream:      bool = False,
) -> tuple[Any, LLMCallMetrics]:
    """
    统一LLM调用入口，内置三层容错。

    Args:
        messages:    对话消息列表
        scene:       场景名（选题/代码分析/反馈生成），用于埋点和超时配置
        schema:      期望的Pydantic输出结构，None表示不做结构化验证
        fallback_fn: 降级函数，三层都失败时调用
        timeout:     超时秒数，None则按scene自动选择
        model:       模型名，None则使用默认模型
        stream:      是否流式输出

    Returns:
        (result, metrics) 元组
    """
    metrics = LLMCallMetrics(scene=scene)
    start   = time.time()

    # 自动选超时
    if timeout is None:
        timeout_map = {
            "select":   settings.llm_timeout_select,
            "analyze":  settings.llm_timeout_analyze,
            "feedback": settings.llm_timeout_feedback,
        }
        timeout = timeout_map.get(scene, 30)

    model = model or settings.llm_model

    # 流式场景单独处理
    if stream:
        result, metrics = await _stream_call(messages, scene, model, timeout, metrics)
        metrics.total_latency = time.time() - start
        await _record_metrics(metrics)
        return result, metrics

    # ── 非流式：三层容错 ──────────────────────────────────────────────────────
    current_messages = messages
    last_raw_output  = ""

    for attempt in range(settings.llm_max_retries + 1):
        metrics.attempts += 1

        try:
            raw = await asyncio.wait_for(
                _call_llm(current_messages, model),
                timeout=timeout,
            )
            last_raw_output = raw

            # 不需要结构化验证，直接返回
            if schema is None:
                metrics.total_latency = time.time() - start
                await _record_metrics(metrics)
                return raw, metrics

            # 第一层：本地修复
            repaired = repair_json_output(raw)
            if repaired:
                try:
                    data = json.loads(repaired)
                    data = coerce_types(data, schema)
                    validated = schema.model_validate(data)

                    if attempt > 0:
                        # 重试成功
                        pass
                    else:
                        metrics.repair_success = True

                    metrics.total_latency = time.time() - start
                    await _record_metrics(metrics)
                    return validated, metrics

                except Exception as e:
                    logger.warning(f"[{scene}] Schema验证失败: {e}")
                    missing = _get_missing_fields(schema, repaired)

                    # 第二层：构造重试消息
                    if attempt < settings.llm_max_retries:
                        current_messages = build_retry_messages(
                            messages, raw, missing
                        )
                        continue

            else:
                logger.warning(f"[{scene}] JSON修复失败，原始输出: {raw[:200]}")
                if attempt < settings.llm_max_retries:
                    current_messages = build_retry_messages(messages, raw)
                    continue

        except asyncio.TimeoutError:
            metrics.failure_reason = "timeout"
            logger.error(f"[{scene}] 超时（{timeout}s），attempt={attempt+1}")
            if attempt < settings.llm_max_retries:
                continue

        except Exception as e:
            metrics.failure_reason = str(e)
            logger.error(f"[{scene}] 调用异常: {e}")
            if attempt < settings.llm_max_retries:
                continue

    # 第三层：降级
    metrics.fallback_used  = True
    metrics.total_latency  = time.time() - start
    logger.error(f"[{scene}] 全部重试失败，触发降级")
    await _record_metrics(metrics)

    if fallback_fn:
        return fallback_fn(), metrics

    return None, metrics


# ─── 流式调用 ─────────────────────────────────────────────────────────────────

async def llm_stream_call(
    messages: list[dict],
    scene:    str,
    model:    str | None = None,
    timeout:  int | None = None,
):
    """
    流式输出生成器。
    使用方式：async for chunk in llm_stream_call(...): yield chunk

    chunk格式：
    {"type": "chunk",   "content": "文字内容"}
    {"type": "metrics", "tokens_used": 234}
    {"type": "done"}
    {"type": "error",   "message": "错误信息"}
    """
    if timeout is None:
        timeout = settings.llm_timeout_analyze

    model = model or settings.llm_model

    try:
        buffer          = ""
        total_tokens    = 0
        last_chunk_time = time.time()

        async for text in chat_stream(
            messages=messages,
            model=model,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        ):
            # chunk间隔超时检测
            now = time.time()
            if now - last_chunk_time > settings.stream_chunk_timeout:
                yield {"type": "error", "message": "流式传输中断，已超过5秒无新内容"}
                return
            last_chunk_time = now

            buffer += text

            # 按语义边界缓冲
            if any(buffer.endswith(p) for p in ["。", ".", "！", "!", "？", "?", "\n"]):
                yield {"type": "chunk", "content": buffer}
                buffer = ""

        # 发送剩余内容
        if buffer:
            yield {"type": "chunk", "content": buffer}
        yield {"type": "metrics", "tokens_used": total_tokens}
        yield {"type": "done"}
        return

    except asyncio.TimeoutError:
        yield {"type": "error", "message": f"请求超时（{timeout}s）"}
    except Exception as e:
        logger.error(f"[{scene}] 流式调用异常: {e}")
        yield {"type": "error", "message": "分析服务暂时不可用"}


# ─── 内部工具函数 ─────────────────────────────────────────────────────────────

async def _call_llm(messages: list[dict], model: str) -> str:
    """调用LLM，返回原始文本输出"""
    return await chat_completion(
        messages=messages,
        model=model,
        max_tokens=settings.llm_max_tokens,
        temperature=settings.llm_temperature,
    )


async def _stream_call(messages, scene, model, timeout, metrics):
    """流式调用包装（非生成器版本，用于metrics统计）"""
    # 流式调用由llm_stream_call处理，这里只做metrics记录
    metrics.attempts = 1
    return None, metrics


def _get_missing_fields(
    schema: type[BaseModel],
    json_str: str,
) -> list[str]:
    """找出Schema中缺失的必填字段"""
    try:
        data     = json.loads(json_str)
        required = [
            name for name, field in schema.model_fields.items()
            if field.is_required()
        ]
        return [f for f in required if f not in data]
    except Exception:
        return []


async def _record_metrics(metrics: LLMCallMetrics) -> None:
    """记录埋点数据（后续接入监控系统）"""
    level = logging.WARNING if metrics.fallback_used else logging.INFO
    logger.log(
        level,
        f"[LLM][{metrics.scene}] "
        f"attempts={metrics.attempts} "
        f"repair={metrics.repair_success} "
        f"fallback={metrics.fallback_used} "
        f"latency={metrics.total_latency:.2f}s "
        f"reason={metrics.failure_reason or 'ok'}"
    )