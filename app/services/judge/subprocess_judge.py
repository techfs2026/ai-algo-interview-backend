"""
subprocess 本地判题实现

适用场景：本地开发、Mac/Linux 环境
升级路径：切换到 Judge0 只需换掉此文件，上层接口不变
"""
import ast
import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile

from app.schemas.analysis import JudgeResult
from app.services.judge.base import BaseJudge

logger = logging.getLogger(__name__)

LANGUAGE_RUNNERS = {
    "python":     ["python3"],
    "python3":    ["python3"],
    "javascript": ["node"],
}

EXEC_TIMEOUT = 10


# ─── 输入解析 ─────────────────────────────────────────────────────────────────

def _parse_input_lines(input_str: str) -> tuple[list, str]:
    """
    逐行解析 LeetCode 示例输入，每行独立处理一个参数。
    用 ast.literal_eval 而非正则，正确处理嵌套结构。

    输入：
        nums = [2,7,11,15]
        target = 9
    输出：
        args_repr = "[2, 7, 11, 15], 9"
    """
    args_repr_parts = []

    for line in input_str.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        val_str = line.partition("=")[2].strip() if "=" in line else line

        try:
            val = ast.literal_eval(val_str)
        except Exception:
            val = val_str

        args_repr_parts.append(repr(val))

    return [], ", ".join(args_repr_parts)


# ─── 代码包装 ─────────────────────────────────────────────────────────────────

PYTHON_WRAPPER = '''{user_code}

import json as _json, sys as _sys

def _main():
    sol = Solution()
    try:
        result = sol.{method}({args})
        print(_json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    except Exception as e:
        print(f"RUNTIME_ERROR: {{e}}", file=_sys.stderr)
        _sys.exit(1)

_main()
'''

JS_WRAPPER = '''{user_code}

(function() {{
    try {{
        let sol;
        try {{ sol = new Solution(); }} catch(e) {{}}
        const fn = sol ? sol.{method}.bind(sol) : {method};
        const result = fn({args});
        console.log(JSON.stringify(result));
    }} catch(e) {{
        process.stderr.write("RUNTIME_ERROR: " + e.message + "\\n");
        process.exit(1);
    }}
}})();
'''


def _extract_method(code: str, language: str) -> str:
    if language in ("python", "python3"):
        m = re.search(r"def\s+(\w+)\s*\(self", code)
        return m.group(1) if m else "solve"
    m = re.search(r"(?:function|var|let|const)\s+(\w+)\s*[=\(]", code)
    return m.group(1) if m else "solve"


def _build_script(code: str, language: str, input_str: str) -> str | None:
    method   = _extract_method(code, language)
    _, args  = _parse_input_lines(input_str)

    if language in ("python", "python3"):
        return PYTHON_WRAPPER.format(user_code=code, method=method, args=args)
    elif language == "javascript":
        return JS_WRAPPER.format(user_code=code, method=method, args=args)
    return None


# ─── 输出标准化 ───────────────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    s = s.strip().replace("True", "true").replace("False", "false").replace("None", "null")

    try:
        parsed = json.loads(s)
        if isinstance(parsed, float) and parsed == int(parsed):
            parsed = int(parsed)
        return json.dumps(parsed, sort_keys=False, ensure_ascii=False, separators=(',', ':'))
    except Exception:
        pass

    try:
        parsed = ast.literal_eval(s)
        return json.dumps(parsed, sort_keys=False, ensure_ascii=False, separators=(',', ':'))
    except Exception:
        pass

    return s.lower().strip()


# ─── 执行 ─────────────────────────────────────────────────────────────────────

def _run_sync(script: str, language: str) -> dict:
    runner = LANGUAGE_RUNNERS.get(language.lower())
    if not runner:
        return {"stdout": "", "stderr": f"不支持的语言: {language}", "returncode": -1}

    suffix = ".py" if language in ("python", "python3") else ".js"
    with tempfile.NamedTemporaryFile(
        suffix=suffix, delete=False, mode="w", encoding="utf-8"
    ) as f:
        f.write(script)
        fname = f.name

    try:
        r = subprocess.run(
            runner + [fname],
            capture_output=True, text=True,
            timeout=EXEC_TIMEOUT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return {"stdout": r.stdout.strip(), "stderr": r.stderr.strip(), "returncode": r.returncode}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"执行超时（>{EXEC_TIMEOUT}s）", "returncode": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1}
    finally:
        try:
            os.unlink(fname)
        except Exception:
            pass


async def _run(script: str, language: str) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run_sync, script, language)


# ─── 实现 ─────────────────────────────────────────────────────────────────────

class SubprocessJudge(BaseJudge):
    """subprocess 本地判题实现"""

    @property
    def name(self) -> str:
        return "subprocess"

    async def execute(
        self,
        code:       str,
        language:   str,
        test_cases: list[dict],
    ) -> JudgeResult:
        if not test_cases:
            return await self._compile_check(code, language)

        cases  = test_cases[:3]
        passed = 0
        errors = []

        for case in cases:
            r = await self._run_one(code, language, case["input"], case["expected"])
            if r["passed"]:
                passed += 1
            elif r.get("error"):
                errors.append(r["error"])

        total = len(cases)

        if passed == total:
            status = "Accepted"
        elif errors and any(
            kw in e for e in errors
            for kw in ("SyntaxError", "IndentationError", "Compilation")
        ):
            status = "Compilation Error"
        elif errors and any("超时" in e or "Timeout" in e for e in errors):
            status = "Time Limit Exceeded"
        else:
            status = "Wrong Answer"

        return JudgeResult(
            passed=passed, total=total, status=status,
            error_message=errors[0] if errors else "",
        )

    async def _run_one(self, code, language, input_str, expected) -> dict:
        script = _build_script(code, language, input_str)
        if not script:
            return {"passed": False, "error": f"不支持的语言: {language}"}

        r = await _run(script, language)
        if r["returncode"] != 0:
            return {"passed": False, "error": r["stderr"][:300] or "运行时错误"}

        passed = _normalize(r["stdout"]) == _normalize(expected)
        if not passed:
            logger.debug(
                f"判题不通过\n"
                f"  输入: {input_str!r}\n"
                f"  期望: {_normalize(expected)!r}\n"
                f"  实际: {_normalize(r['stdout'])!r}"
            )
        return {"passed": passed}

    async def _compile_check(self, code: str, language: str) -> JudgeResult:
        if language in ("python", "python3"):
            try:
                compile(code, "<string>", "exec")
                return JudgeResult(passed=1, total=1, status="Accepted")
            except SyntaxError as e:
                return JudgeResult(
                    passed=0, total=1,
                    status="Compilation Error",
                    error_message=str(e),
                )
        return JudgeResult(passed=0, total=1, status="No Test Cases")

    async def health_check(self) -> bool:
        return True