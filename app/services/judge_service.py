"""
判题服务 - subprocess 本地执行方案

设计原则：
- 用 LeetCode 示例测试用例做基础判题
- subprocess + timeout 保证不卡死
- 没有完整隔离（面试项目可接受）
- 面试时说明：生产环境换 Judge0/Piston
"""
import asyncio
import logging
import os
import re
import subprocess
import tempfile
from html.parser import HTMLParser

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Question, TestCase
from app.schemas.analysis import JudgeResult

logger = logging.getLogger(__name__)

LANGUAGE_RUNNERS = {
    "python":     ["python3"],
    "python3":    ["python3"],
    "javascript": ["node"],
}

EXEC_TIMEOUT = 5


# ─── HTML 解析：从题目描述提取示例 ───────────────────────────────────────────

class ExampleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.examples = []
        self._in_pre  = False
        self._current = ""

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self._in_pre  = True
            self._current = ""

    def handle_endtag(self, tag):
        if tag == "pre" and self._in_pre:
            self._in_pre = False
            text = self._current.strip()
            if text:
                self.examples.append(text)

    def handle_data(self, data):
        if self._in_pre:
            self._current += data

    def handle_entityref(self, name):
        if self._in_pre:
            entities = {"lt": "<", "gt": ">", "amp": "&", "nbsp": " "}
            self._current += entities.get(name, "")

    def handle_charref(self, name):
        if self._in_pre:
            try:
                self._current += chr(int(name[1:], 16) if name.startswith("x") else int(name))
            except Exception:
                pass


def parse_examples(html: str) -> list[dict]:
    """从题目 HTML 解析示例，返回 [{"input": "...", "expected": "..."}]"""
    parser = ExampleParser()
    parser.feed(html or "")

    examples = []
    for block in parser.examples:
        lines       = [l.strip() for l in block.split("\n") if l.strip()]
        input_lines = []
        output_line = ""

        for line in lines:
            low = line.lower()
            if low.startswith("input:"):
                input_lines.append(line.split(":", 1)[1].strip())
            elif low.startswith("output:"):
                output_line = line.split(":", 1)[1].strip()
            elif input_lines and not low.startswith("explanation:") and not output_line:
                input_lines.append(line)

        if input_lines and output_line:
            examples.append({
                "input":    "\n".join(input_lines),
                "expected": output_line,
            })

    return examples


# ─── 代码包装 ─────────────────────────────────────────────────────────────────

PYTHON_WRAPPER = """{user_code}

import json as _json
def _run():
    sol = Solution()
    inputs = {inputs}
    result = sol.{method}({args})
    print(_json.dumps(result, ensure_ascii=False, separators=(',', ':')))
_run()
"""

JS_WRAPPER = """{user_code}

const inputs = {inputs};
let result;
try {{
    const sol = new Solution();
    result = sol.{method}({args});
}} catch(e) {{
    result = {method}({args});
}}
console.log(JSON.stringify(result));
"""


def _extract_method(code: str, language: str) -> str:
    if language in ("python", "python3"):
        m = re.search(r"def\s+(\w+)\s*\(self", code)
        return m.group(1) if m else "solve"
    m = re.search(r"(?:function|var|let|const)\s+(\w+)\s*[=\(]", code)
    return m.group(1) if m else "solve"


def _parse_input(input_str: str) -> tuple[dict, str]:
    import ast
    params = {}
    for name, val in re.findall(r'(\w+)\s*=\s*(.+?)(?=,\s*\w+\s*=|$)', input_str.strip()):
        try:
            params[name] = ast.literal_eval(val.strip().rstrip(","))
        except Exception:
            params[name] = val.strip()
    args = ", ".join(repr(v) for v in params.values())
    return params, args


def _wrap(code: str, language: str, input_str: str) -> str | None:
    method = _extract_method(code, language)
    params, args = _parse_input(input_str)

    if language in ("python", "python3"):
        return PYTHON_WRAPPER.format(
            user_code=code, inputs=repr(params), method=method, args=args
        )
    elif language == "javascript":
        return JS_WRAPPER.format(
            user_code=code, inputs=str(params), method=method, args=args
        )
    return None


# ─── 执行 ─────────────────────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    s = s.strip()
    try:
        import json
        return json.dumps(json.loads(s), separators=(',', ':'), ensure_ascii=False)
    except Exception:
        return s.lower().replace(" ", "").replace("'", '"')


def _run_sync(script: str, language: str) -> dict:
    runner = LANGUAGE_RUNNERS.get(language.lower())
    if not runner:
        return {"stdout": "", "stderr": f"不支持 {language}", "returncode": -1}

    suffix = ".py" if language in ("python", "python3") else ".js"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="w", encoding="utf-8") as f:
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
        return {"stdout": "", "stderr": "执行超时", "returncode": -1}
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


# ─── 判题服务 ─────────────────────────────────────────────────────────────────

class JudgeService:

    async def judge(
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

        total  = len(cases)
        status = "Accepted" if passed == total else (
            "Compilation Error"
            if any("Error" in e for e in errors)
            else "Wrong Answer"
        )
        return JudgeResult(
            passed=passed, total=total, status=status,
            error_message=errors[0] if errors else "",
        )

    async def _run_one(self, code, language, input_str, expected) -> dict:
        script = _wrap(code, language, input_str)
        if not script:
            return {"passed": False, "error": f"不支持的语言: {language}"}

        r = await _run(script, language)
        if r["returncode"] != 0:
            return {"passed": False, "error": r["stderr"][:200] or "运行错误"}

        passed = _normalize(r["stdout"]) == _normalize(expected)
        return {"passed": passed}

    async def _compile_check(self, code: str, language: str) -> JudgeResult:
        if language in ("python", "python3"):
            r = await _run(code + "\n", language)
            ok = r["returncode"] == 0 or "SyntaxError" not in r["stderr"]
            return JudgeResult(
                passed=1 if ok else 0, total=1,
                status="Accepted" if ok else "Compilation Error",
                error_message="" if ok else r["stderr"][:200],
            )
        return JudgeResult(passed=0, total=1, status="No Test Cases")

    async def get_test_cases(
        self,
        question_id: int,
        db:          AsyncSession,
        content:     dict | None = None,
    ) -> list[dict]:
        result = await db.execute(
            select(TestCase).where(TestCase.question_id == question_id)
        )
        cached = result.scalars().all()
        if cached:
            return [{"input": t.input_data, "expected": t.expected} for t in cached]

        if not content or not content.get("content"):
            return []

        examples = parse_examples(content["content"])
        if not examples:
            return []

        for ex in examples:
            db.add(TestCase(
                question_id=question_id,
                input_data=ex["input"],
                expected=ex["expected"],
                case_type="sample",
            ))
        try:
            await db.flush()
        except Exception as e:
            logger.warning(f"保存测试用例失败: {e}")

        return examples

    async def health_check(self) -> bool:
        """subprocess 方案始终可用"""
        return True


judge_service = JudgeService()