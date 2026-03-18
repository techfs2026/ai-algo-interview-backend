"""
AI 代码分析服务

核心设计：
1. 根据判题结果走六条不同分析路径
2. 流式输出，用户实时看到分析过程
3. 输出质量控制：负向约束 + 篇幅限制
"""
import logging
import time
from typing import AsyncGenerator

from app.core.llm_client import chat_stream
from app.core.config import get_settings
from app.schemas.analysis import JudgeResult

logger   = logging.getLogger(__name__)
settings = get_settings()

DIFFICULTY_TIME = {"easy": 20, "medium": 30, "hard": 45}

# Token 限制
# 单次 chunk 推送不超过 MAX_CHUNK_CHARS 字符（防止某个 chunk 过大卡住前端打字）
# 总输出不超过 MAX_TOTAL_TOKENS（防止模型无限生成）
MAX_TOTAL_TOKENS = 500   # 5节合计约 30+80+100+80+40 = 330字，留 buffer
MAX_CHUNK_CHARS  = 120   # 单个 chunk 字符上限


# ─── 六条分析路径 ─────────────────────────────────────────────────────────────

def _focus(result: JudgeResult, code: str) -> str:
    p, t, s = result.passed, result.total, result.status

    if p == t and s == "Accepted":
        return "全部通过。分析复杂度（给出推导过程）、有无更优解法，以及代码可读性。"

    if 0 < p < t:
        return f"通过 {p}/{t}。推测哪类输入会失败（边界？溢出？特殊情况？），指出逻辑缺陷位置，给修复方向。"

    if s == "Compilation Error":
        return f"编译错误：{result.error_message[:150]}。定位错误原因，说明正确写法。"

    if s == "Time Limit Exceeded":
        return "超时。找出时间复杂度瓶颈，给出优化方向和目标复杂度，说明核心思路。"

    if p == 0 and len(code.strip()) > 50:
        return "完全未通过，但有代码。肯定正确的思路部分，找出关键错误，给修复方向。"

    return "几乎没有代码。从最直观的暴力解开始引导，分析数据结构选择和边界处理。"


# ─── Prompt ───────────────────────────────────────────────────────────────────

SYSTEM = """你是一位资深算法面试官，正在点评候选人的代码。

风格要求：
- 语气专业，像和候选人直接对话
- 数学公式用 LaTeX：行内 $O(n)$，独立公式 $$T(n) = 2T(n/2) + O(n)$$
- 禁止给出完整正确代码
- 严格遵守每节字数上限，超出立刻截止"""

USER_TMPL = """题目：{title}（{diff} · 通过率 {ac:.0%}）
标签：{tags}
语言：{lang}
用时：{used}分钟（参考 {ref} 分钟）
判题：{status}（{passed}/{total} 用例）

```{lang}
{code}
```

分析重点：{focus}

请严格按以下结构输出，每节不超过括号内的字数限制：

## 整体评价（≤30字）
一句话说清这份代码的水平和核心特点。

## 复杂度分析（≤80字）
时间/空间复杂度 + 简要推导过程，用 LaTeX 写复杂度符号。

## 关键问题（≤100字）
针对分析重点，具体指出问题位置或亮点，不说废话。

## 改进建议（≤80字）
1~2条，每条必须指向具体代码行或逻辑块。

## 延伸思考
一个相关进阶问题，激发继续思考。"""


# ─── 流式输出 ─────────────────────────────────────────────────────────────────

async def analyze_code_stream(
    code:      str,
    language:  str,
    time_used: int,
    result:    JudgeResult,
    question:  dict,
) -> AsyncGenerator[dict, None]:
    """
    流式输出代码分析。

    yield 格式：
    {"type": "chunk",   "content": "..."}
    {"type": "metrics", "tokens_used": N}
    {"type": "done"}
    {"type": "error",   "message": "..."}
    """
    ref_mins  = DIFFICULTY_TIME.get(question.get("difficulty", "medium"), 30)
    used_mins = round(time_used / 60, 1)
    focus     = _focus(result, code)

    prompt = USER_TMPL.format(
        title=question.get("title", "未知题目"),
        diff=question.get("difficulty", "medium").upper(),
        ac=question.get("ac_rate", 0.5),
        tags="、".join(question.get("tags", [])[:5]) or "暂无",
        lang=language,
        used=used_mins,
        ref=ref_mins,
        status=result.status,
        passed=result.passed,
        total=result.total,
        code=code[:3000],
        focus=focus,
    )

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": prompt},
    ]

    buf          = ""
    total_chars  = 0
    last_t       = time.time()
    BOUNDARIES   = {"。", ".", "！", "!", "？", "?", "\n", "：", ":"}

    try:
        async for text in chat_stream(
            messages=messages,
            max_tokens=MAX_TOTAL_TOKENS,
            temperature=0.6,
        ):
            now = time.time()
            if now - last_t > settings.stream_chunk_timeout:
                if buf:
                    yield {"type": "chunk", "content": buf}
                yield {"type": "error", "message": "分析中断，超过 5 秒无新内容"}
                return
            last_t = now

            buf         += text
            total_chars += len(text)

            # 总 token 超限：推送剩余 buf，终止
            if total_chars > MAX_TOTAL_TOKENS * 2:
                if buf:
                    yield {"type": "chunk", "content": buf}
                    buf = ""
                break

            # 单 chunk 超限：强制截断推送
            if len(buf) >= MAX_CHUNK_CHARS:
                yield {"type": "chunk", "content": buf}
                buf = ""
                continue

            # 按语义边界推送
            if any(buf.endswith(b) for b in BOUNDARIES) and len(buf) >= 8:
                yield {"type": "chunk", "content": buf}
                buf = ""

        if buf:
            yield {"type": "chunk", "content": buf}

        yield {"type": "metrics", "tokens_used": total_chars // 2}
        yield {"type": "done"}

    except Exception as e:
        logger.error(f"代码分析流式输出异常: {e}")
        if buf:
            yield {"type": "chunk", "content": buf}
        yield {"type": "error", "message": "分析服务暂时不可用，请稍后重试"}