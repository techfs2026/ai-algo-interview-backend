"""
AI 代码分析服务

设计思路：
- 四条路径：未实现 / 编译错误 / 答案错误 / 通过
- 两层结构：客观诊断（基于判题结果）+ 思维引导（基于代码内容）
- 思维引导是核心价值：让用户知道自己哪里没想清楚，而不是直接给答案
"""
import logging
import time
from typing import AsyncGenerator

from app.core.llm_client import chat_stream
from app.core.config import get_settings
from app.schemas.analysis import JudgeResult

logger   = logging.getLogger(__name__)
settings = get_settings()

DIFFICULTY_TIME  = {"easy": 20, "medium": 30, "hard": 45}
MAX_TOTAL_TOKENS = 600
MAX_CHUNK_CHARS  = 150


# ─── 路径判断 ──────────────────────────────────────────────────────────────────

def _determine_path(result: JudgeResult, code: str) -> str:
    """
    四条路径：
    - not_implemented : 代码极少，基本没写
    - compile_error   : 语法错误，代码跑不起来
    - wrong           : 有代码，但答案不对（WA / TLE / RE / 部分通过）
    - accepted        : 全部通过
    """
    real_lines = [
        l for l in code.split("\n")
        if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("//")
    ]
    if len(real_lines) < 5:
        return "not_implemented"

    if result.status == "Compilation Error":
        return "compile_error"

    if result.status == "Accepted" and result.passed == result.total:
        return "accepted"

    return "wrong"


# ─── System Prompt ────────────────────────────────────────────────────────────

SYSTEM = """/no_think
你是一位资深算法面试官，正在点评候选人刚提交的代码。

核心原则：
- 像和候选人直接对话，语气专业但不刻板
- 你看到了他的代码，说的每句话都要有代码依据，绝对不能泛泛而谈
- 禁止给出完整的正确代码或完整的解题思路
- 只给方向和线索，保留候选人自己思考的空间
- 复杂度用 LaTeX：行内 $O(n)$，独立公式 $$T(n) = ...$$
- 严格控制篇幅，宁少勿多"""


# ─── 四套 Prompt ──────────────────────────────────────────────────────────────

# 路径0：未实现
PROMPT_NOT_IMPLEMENTED = """题目：{title}（{diff} · 通过率 {ac:.0%}）
标签：{tags}

候选人几乎没有写代码，只有这些：
```{lang}
{code}
```

请引导他开始思考，按以下结构输出：

## 先想暴力解（≤40字）
从最朴素的角度，这道题可以怎么做？不用考虑效率。

## 数据结构选择（≤60字）
解这道题，脑子里第一个想到的数据结构是什么？为什么？

## 一个起点（≤40字）
给他一个能立刻动手的切入点，但不要给完整思路。"""


# 路径1：编译错误
PROMPT_COMPILE_ERROR = """题目：{title}（{diff}）
语言：{lang}
错误信息：{error}

候选人的代码：
```{lang}
{code}
```

只处理语法问题，按以下结构输出：

## 错误定位（≤50字）
哪里出了语法问题，一句话说清楚，直接指向代码位置。

## 正确写法（≤50字）
正确的语法应该是什么样的，用代码片段说明。

## 继续（≤30字）
修完语法后，下一步应该关注什么逻辑问题。"""


# 路径2：答案错误（WA / TLE / RE / 部分通过）
PROMPT_WRONG = """题目：{title}（{diff} · 通过率 {ac:.0%}）
标签：{tags}
语言：{lang}
用时：{used}分钟（参考 {ref} 分钟）
判题结果：{status}（通过 {passed}/{total} 个用例）
{error_info}

候选人的代码：
```{lang}
{code}
```

按以下结构输出，每节严格控制字数：

## 思路诊断（≤50字）
他用的是什么思路？这个方向对不对？一句话定性。

## 问题所在（≤80字）
具体指出代码里哪个地方有问题（指向具体的变量名/逻辑块/行为），
推测哪类输入会触发错误（边界值？负数？空输入？重复元素？）。
如果是超时，指出复杂度瓶颈在哪一行或哪个循环。

## 修复方向（≤60字）
给一个能让他立刻尝试的方向，但不给完整答案。
必须指向代码中具体的位置。

## 思考一下（≤40字）
一个问题，让他自己想清楚卡住的地方。"""


# 路径3：通过（含可优化）
PROMPT_ACCEPTED = """题目：{title}（{diff} · 通过率 {ac:.0%}）
标签：{tags}
语言：{lang}
用时：{used}分钟（参考 {ref} 分钟）
判题结果：全部通过 ✓

候选人的代码：
```{lang}
{code}
```

按以下结构输出：

## 整体评价（≤30字）
一句话定性：思路清晰度、代码风格、是否有明显亮点。

## 复杂度（≤80字）
时间复杂度和空间复杂度，要有推导过程，不能只给结论。
用 LaTeX 写符号。如果这道题有更优的复杂度，说一句"这道题有更优解"，但不说是什么。

## 可以更好的地方（≤80字）
从代码里找 1~2 个具体的可改进点，比如：
- 变量命名不清晰
- 某个判断可以提前终止
- 某段逻辑可以简化
必须指向具体代码位置，不说空话。

## 进阶挑战（≤50字）
一个让他继续思考的问题，比如"如果输入是有序的，你的解法还成立吗？"
要和这道题强相关。"""


# ─── 构造最终 Prompt ──────────────────────────────────────────────────────────

def _build_prompt(
    path:      str,
    code:      str,
    language:  str,
    time_used: int,
    result:    JudgeResult,
    question:  dict,
) -> str:
    ref_mins  = DIFFICULTY_TIME.get(question.get("difficulty", "medium"), 30)
    used_mins = round(time_used / 60, 1)
    title     = question.get("title", "未知题目")
    diff      = question.get("difficulty", "medium").upper()
    ac        = question.get("ac_rate", 0.5)
    tags      = "、".join(question.get("tags", [])[:4]) or "暂无"
    code_trim = code[:2500]

    if path == "not_implemented":
        return PROMPT_NOT_IMPLEMENTED.format(
            title=title, diff=diff, ac=ac, tags=tags,
            lang=language, code=code_trim,
        )

    if path == "compile_error":
        return PROMPT_COMPILE_ERROR.format(
            title=title, diff=diff, lang=language,
            error=result.error_message[:300],
            code=code_trim,
        )

    if path == "wrong":
        error_info = ""
        if result.error_message:
            error_info = f"错误信息：{result.error_message[:200]}"
        return PROMPT_WRONG.format(
            title=title, diff=diff, ac=ac, tags=tags,
            lang=language, used=used_mins, ref=ref_mins,
            status=result.status,
            passed=result.passed, total=result.total,
            error_info=error_info,
            code=code_trim,
        )

    # accepted
    return PROMPT_ACCEPTED.format(
        title=title, diff=diff, ac=ac, tags=tags,
        lang=language, used=used_mins, ref=ref_mins,
        code=code_trim,
    )


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
    path   = _determine_path(result, code)
    prompt = _build_prompt(path, code, language, time_used, result, question)

    logger.info(f"[代码分析] 路径={path} title={question.get('title','?')}")

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": prompt},
    ]

    buf         = ""
    total_chars = 0
    last_t      = time.time()
    BOUNDARIES  = {"。", ".", "！", "!", "？", "?", "\n", "：", ":"}

    try:
        async for text in chat_stream(
            messages=messages,
            max_tokens=MAX_TOTAL_TOKENS,
            temperature=0.5,     # 稍低，保证分析稳定不随机
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

            if total_chars > MAX_TOTAL_TOKENS * 2:
                if buf:
                    yield {"type": "chunk", "content": buf}
                    buf = ""
                break

            if len(buf) >= MAX_CHUNK_CHARS:
                yield {"type": "chunk", "content": buf}
                buf = ""
                continue

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