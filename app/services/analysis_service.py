"""
AI 代码分析服务 - 系统第二个技术突破点

核心设计：
1. 根据判题结果走五条不同分析路径
2. 流式输出，用户实时看到分析过程
3. 负向约束 + few-shot 控制输出质量
"""
import logging
from typing import AsyncGenerator

from app.core.llm_client import chat_stream
from app.core.config import get_settings
from app.schemas.analysis import JudgeResult

logger   = logging.getLogger(__name__)
settings = get_settings()


# ─── 五条分析路径 ─────────────────────────────────────────────────────────────

def _build_analysis_focus(
    result:    JudgeResult,
    code:      str,
    question:  dict,
) -> str:
    """
    根据判题结果动态生成分析重点。
    这是五条路径的核心——不同情况的分析侧重点完全不同。
    """
    passed = result.passed
    total  = result.total
    status = result.status

    # 路径一：全部通过
    if passed == total and status == "Accepted":
        return """
代码已通过所有测试用例。
分析重点：
1. 当前解法的时间复杂度和空间复杂度是多少？推导过程要清晰
2. 是否存在更优的算法思路？如果有，描述优化方向（给提示，不给完整代码）
3. 代码是否有更简洁的写法？指出具体可以改进的地方
4. 这道题的变体或进阶问题是什么？"""

    # 路径二：部分通过
    elif 0 < passed < total:
        return f"""
代码通过了 {passed}/{total} 个测试用例。
分析重点：
1. 推测失败的测试用例是什么类型（边界情况？特殊输入？负数？空数组？）
2. 代码逻辑的哪个具体位置没有处理这种情况？（指出行或逻辑块）
3. 如何在不改变整体思路的前提下修复这个问题？（给方向，不给完整代码）
4. 当前解法的复杂度分析"""

    # 路径三：编译/语法错误
    elif status == "Compilation Error":
        return f"""
代码存在编译/语法错误：{result.error_message[:200]}
分析重点：
1. 指出错误的具体位置和原因（用简单语言解释）
2. 正确的语法应该是什么样的
3. 这类错误的常见原因是什么，如何避免"""

    # 路径四：超时
    elif status == "Time Limit Exceeded":
        return """
代码运行超时，时间复杂度过高。
分析重点：
1. 当前解法的时间复杂度是多少？哪个部分是瓶颈？
2. 优化的方向是什么？（如：暴力→哈希表，O(n²)→O(n log n)）
3. 优化后能达到什么复杂度？核心思路是什么？
4. 给出优化思路的关键步骤（不给完整代码）"""

    # 路径五：完全没过 + 有代码
    elif passed == 0 and len(code.strip()) > 50:
        return """
代码没有通过测试用例，但候选人有完整的尝试。
分析重点：
1. 候选人的解题思路是什么？哪里是对的？肯定正确的部分
2. 逻辑上的关键错误在哪一步？具体指出
3. 修正方向是什么？（给提示，不给完整代码）
4. 这道题正确的解题思路应该从哪里入手？"""

    # 路径六：代码为空或极少
    else:
        return """
候选人没有提交有效代码。
分析重点：
1. 这道题的核心思路是什么？从最直观的想法开始引导
2. 应该使用什么数据结构？为什么？
3. 从暴力解法到优化解法的思考路径
4. 关键的边界条件有哪些需要注意？"""


# ─── 主 Prompt ────────────────────────────────────────────────────────────────

ANALYSIS_PROMPT = """/no_think
你是一位经验丰富的算法面试官，正在对候选人的代码进行点评。

题目信息：
- 题目：{title}（{difficulty}）
- 考察知识点：{tags}

候选人代码：
```{language}
{code}
```

判题结果：{status}（通过 {passed}/{total} 个测试用例）
答题用时：{time_used} 分钟（参考用时约 {expected_minutes} 分钟）

{analysis_focus}

请按以下结构输出分析，使用中文，语气像一位耐心的面试官：

## 整体评价
一句话说清楚这份代码的核心特点和水平定位。

## 详细分析
针对上面的分析重点展开，包含复杂度分析（要有推导过程）。

## 具体建议
1~2个最有价值的改进点，必须指出具体的代码位置或逻辑，不要说"可以优化"这种废话。

## 延伸思考
一个相关的进阶问题，激发继续思考。

注意事项：
- 不要重复题目描述
- 不要给出完整的正确代码（用户需要自己思考）
- 改进建议必须具体，不能泛泛而谈
- 篇幅控制在 300~400 字"""


# ─── 流式分析 ─────────────────────────────────────────────────────────────────

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
    {"type": "chunk",   "content": "文字内容"}
    {"type": "metrics", "tokens_used": 234}
    {"type": "done"}
    {"type": "error",   "message": "错误信息"}
    """
    difficulty_time_map = {"easy": 20, "medium": 30, "hard": 45}
    expected_minutes    = difficulty_time_map.get(
        question.get("difficulty", "medium"), 30
    )

    analysis_focus = _build_analysis_focus(result, code, question)

    prompt = ANALYSIS_PROMPT.format(
        title=question.get("title", "未知题目"),
        difficulty=question.get("difficulty", "medium").upper(),
        tags="、".join(question.get("tags", [])[:5]) or "暂无",
        language=language,
        code=code[:3000],   # 防止超长代码撑爆 context
        status=result.status,
        passed=result.passed,
        total=result.total,
        time_used=round(time_used / 60, 1),
        expected_minutes=expected_minutes,
        analysis_focus=analysis_focus,
    )

    messages = [{"role": "user", "content": prompt}]

    buffer         = ""
    total_chars    = 0
    last_chunk_t   = __import__("time").time()

    try:
        async for text in chat_stream(
            messages=messages,
            max_tokens=800,
            temperature=0.5,
        ):
            # chunk 间隔超时检测
            now = __import__("time").time()
            if now - last_chunk_t > settings.stream_chunk_timeout:
                if buffer:
                    yield {"type": "chunk", "content": buffer}
                yield {"type": "error", "message": "分析中断，已超过5秒无新内容"}
                return
            last_chunk_t = now

            buffer      += text
            total_chars += len(text)

            # 按语义边界缓冲后发送
            if any(buffer.endswith(p) for p in ["。", ".", "！", "!", "？", "?", "\n"]):
                yield {"type": "chunk", "content": buffer}
                buffer = ""

        # 发送剩余内容
        if buffer:
            yield {"type": "chunk", "content": buffer}

        yield {"type": "metrics", "tokens_used": total_chars // 2}
        yield {"type": "done"}

    except Exception as e:
        logger.error(f"代码分析流式输出异常: {e}")
        yield {"type": "error", "message": "分析服务暂时不可用，请稍后重试"}