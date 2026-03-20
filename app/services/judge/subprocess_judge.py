"""
subprocess 本地判题实现

核心改进：
- 根据变量名识别参数类型（TreeNode / ListNode / 普通）
- wrapper 注入数据结构定义 + 序列化/反序列化函数
- 输出自动序列化（TreeNode → list，ListNode → list）
- 降级机制：识别失败时按普通参数处理，不崩溃
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
    "python":  ["python3"],
    "python3": ["python3"],
}

EXEC_TIMEOUT = 10


# ─── 参数类型识别 ─────────────────────────────────────────────────────────────
#
# 优先级：签名类型注解 > 变量名语义
# 签名有明确注解时直接用，否则用变量名判断，再不行就 raw

# 变量名兜底已移除：LeetCode Python3 模板都有完整类型注解，签名判断已足够
# 没有注解时按 raw 处理，不做猜测


def _split_params(param_str: str) -> list[str]:
    """
    按逗号分割参数，正确处理括号嵌套。
    避免把 List[int, str] 里的逗号当作参数分隔符。
    """
    params, depth, cur = [], 0, ""
    for ch in param_str:
        if ch in "([":
            depth += 1
            cur += ch
        elif ch in ")]":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            if cur.strip():
                params.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        params.append(cur.strip())
    return params


def _annotation_to_ptype(annotation: str) -> str:
    """把类型注解字符串转成 param_type"""
    ann = annotation.strip()
    if "TreeNode" in ann:
        return "tree"
    if "ListNode" in ann:
        return "list_node"
    return "raw"


def _parse_signature(code: str) -> tuple[list[str], str]:
    """
    从函数签名或 docstring 解析参数类型列表和返回类型。

    返回：(param_types, return_type)
    param_types: ["raw", "tree", "list_node", ...]
    return_type: "tree" | "list_node" | "raw"

    支持两种格式：

    格式1：Python3 类型注解
        def invertTree(self, root: Optional[TreeNode]) -> Optional[TreeNode]:

    格式2：Python2 docstring 风格（LeetCode 旧版模板）
        def invertTree(self, root):
            # :type root: Optional[TreeNode]
            # :rtype: Optional[TreeNode]
    """
    # ── 格式1：类型注解 ──────────────────────────────────────────────
    # 先去掉注释行，避免匹配到 # class TreeNode 里的 __init__ 等
    code_no_comments = "\n".join(
        line for line in code.split("\n")
        if not line.strip().startswith("#")
    )
    m = re.search(
        r"def\s+\w+\s*\(self\s*,?\s*(.*?)\)\s*(?:->\s*([^:]+))?\s*:",
        code_no_comments, re.DOTALL
    )
    if m:
        param_str  = (m.group(1) or "").strip()
        return_str = (m.group(2) or "").strip()

        param_types = []
        for param in _split_params(param_str):
            annotation = param.split(":", 1)[1].strip() if ":" in param else ""
            param_types.append(_annotation_to_ptype(annotation))

        # 有任何一个参数带了注解（非 raw），说明是格式1，直接返回
        if any(t != "raw" for t in param_types) or return_str:
            return param_types, _annotation_to_ptype(return_str)

    # ── 格式2：docstring :type / :rtype ─────────────────────────────
    # 按参数顺序匹配 :type param: annotation
    type_matches  = re.findall(r":type\s+(\w+)\s*:\s*(.+)", code_no_comments)
    rtype_match   = re.search(r":rtype\s*:\s*(.+)", code_no_comments)

    if type_matches:
        param_types = [_annotation_to_ptype(ann.strip()) for _, ann in type_matches]
        return_type = _annotation_to_ptype(rtype_match.group(1).strip()) if rtype_match else "raw"
        return param_types, return_type

    # ── 无法识别：全部 raw ───────────────────────────────────────────
    return [], "raw"


def _detect_param_types(input_str: str, code: str) -> list[tuple[str, str, str]]:
    """
    解析输入行，返回 [(varname, val_str, param_type), ...]
    param_type: "tree" | "list_node" | "raw"

    只用签名类型注解判断，没有注解一律 raw，不做变量名猜测。
    """
    lines      = [l.strip() for l in input_str.strip().split("\n") if l.strip()]
    sig_types, _ = _parse_signature(code)

    result = []
    for i, line in enumerate(lines):
        if "=" in line:
            varname, _, val_str = line.partition("=")
            varname = varname.strip()
            val_str = val_str.strip()
        else:
            varname = f"arg{i}"
            val_str = line.strip()

        ptype = sig_types[i] if i < len(sig_types) else "raw"
        result.append((varname, val_str, ptype))

    return result


def _get_return_type(code: str) -> str:
    """从签名提取返回类型，用于指导序列化"""
    _, return_type = _parse_signature(code)
    return return_type


# ─── 数据结构辅助代码（注入到 wrapper）──────────────────────────────────────

DS_HELPERS = '''
# ── 注入的数据结构定义 ──────────────────────────────────────────────
from typing import Optional, List, Tuple, Dict, Set

class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right
    def __repr__(self):
        return f"TreeNode({self.val})"

class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next
    def __repr__(self):
        return f"ListNode({self.val})"

def _build_tree(vals):
    """列表 → 二叉树（LeetCode 层序格式）"""
    if not vals or vals[0] is None:
        return None
    nodes = [TreeNode(v) if v is not None else None for v in vals]
    for i, node in enumerate(nodes):
        if node is None:
            continue
        li, ri = 2 * i + 1, 2 * i + 2
        if li < len(nodes):
            node.left = nodes[li]
        if ri < len(nodes):
            node.right = nodes[ri]
    return nodes[0]

def _build_list(vals, pos=-1):
    """
    列表 → 链表，支持有环链表。
    pos = -1：无环（默认）
    pos >= 0：尾节点的 next 指向第 pos 个节点（0-indexed），形成环
    """
    if not vals:
        return None
    dummy = ListNode(0)
    cur = dummy
    nodes = []
    for v in vals:
        cur.next = ListNode(v)
        cur = cur.next
        nodes.append(cur)
    # 构建环
    if pos >= 0 and pos < len(nodes):
        nodes[-1].next = nodes[pos]
    return dummy.next

def _serialize(val, return_type="raw"):
    """
    自动序列化输出值为 JSON 兼容格式。
    TreeNode → list，ListNode → list，其他原样。

    return_type: 来自签名解析，指导 None 的序列化行为：
    - "tree" / "list_node" → None 表示空结构，返回 []
    - "raw" → None 表示真正的 null，返回 None
    """
    if val is None:
        if return_type in ("tree", "list_node"):
            return []   # 空树/空链表 → []（LeetCode 约定）
        return None     # 普通 None → null
    if isinstance(val, TreeNode):
        # 层序遍历序列化
        res, q = [], [val]
        while q:
            n = q.pop(0)
            if n is None:
                res.append(None)
            else:
                res.append(n.val)
                q.append(n.left)
                q.append(n.right)
        # 去掉末尾的 None
        while res and res[-1] is None:
            res.pop()
        return res
    if isinstance(val, ListNode):
        res, cur, seen = [], val, set()
        while cur and id(cur) not in seen:
            seen.add(id(cur))
            res.append(cur.val)
            cur = cur.next
        return res
    if isinstance(val, list):
        return [_serialize(v, "raw") for v in val]   # 列表元素递归，子元素用 raw
    return val
# ── 数据结构定义结束 ────────────────────────────────────────────────
'''

PYTHON_WRAPPER = '''__HELPERS__

__USER_CODE__

import json as _json, sys as _sys

def _main():
    sol = Solution()
    try:
        result = sol.__METHOD__(__ARGS__)
        result = _serialize(result, "__RETURN_TYPE__")
        print(_json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"RUNTIME_ERROR: {e}", file=_sys.stderr)
        _sys.exit(1)

_main()
'''


def _extract_method(code: str) -> str:
    """
    提取 Solution 类的主方法名。
    跳过注释行，避免匹配到注释里的 __init__ 等。
    """
    for line in code.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = re.search(r"def\s+(\w+)\s*\(self", stripped)
        if m and m.group(1) != "__init__":
            return m.group(1)
    return "solve"


def _normalize_val_str(val_str: str) -> str:
    """LeetCode JSON 格式 → Python literal 格式"""
    s = val_str.strip()
    s = re.sub(r'\bnull\b',  'None',  s)
    s = re.sub(r'\btrue\b',  'True',  s)
    s = re.sub(r'\bfalse\b', 'False', s)
    return s


def _build_arg_expr(val_str: str, ptype: str) -> str:
    """
    把原始值字符串转成 Python 调用表达式。

    raw:       "[1,2,3]"              → "[1, 2, 3]"
    tree:      "[4,2,7,null,null,6]"  → "_build_tree([4, 2, 7, None, None, 6])"
    list_node: "[1,2,3]"              → "_build_list([1, 2, 3])"
    """
    normalized = _normalize_val_str(val_str)

    try:
        val = ast.literal_eval(normalized)
        val_repr = repr(val)
    except Exception:
        val_repr = normalized

    if ptype == "tree":
        return f"_build_tree({val_repr})"
    elif ptype == "list_node":
        return f"_build_list({val_repr})"
    else:
        return val_repr


def _build_args_with_cycle(params: list[tuple[str, str, str]]) -> str:
    """
    构建方法调用参数字符串，特殊处理有环链表：
    当参数序列中出现 list_node 后紧跟变量名为 pos 的参数时，
    把 pos 合并到 _build_list 调用里，而不是单独传给方法。

    例：
    params = [("head", "[3,2,0,-4]", "list_node"), ("pos", "1", "raw")]
    → "_build_list([3, 2, 0, -4], 1)"   (pos 被消耗，不单独传)
    """
    arg_exprs = []
    i = 0
    while i < len(params):
        varname, val_str, ptype = params[i]

        if ptype == "list_node":
            # 看下一个参数是不是 pos
            if (i + 1 < len(params)
                    and params[i + 1][0].lower() == "pos"):
                pos_val_str = _normalize_val_str(params[i + 1][1])
                try:
                    pos_val = ast.literal_eval(pos_val_str)
                except Exception:
                    pos_val = -1

                normalized = _normalize_val_str(val_str)
                try:
                    val = ast.literal_eval(normalized)
                    val_repr = repr(val)
                except Exception:
                    val_repr = normalized

                arg_exprs.append(f"_build_list({val_repr}, {pos_val})")
                i += 2   # 跳过 pos 参数
                continue

        arg_exprs.append(_build_arg_expr(val_str, ptype))
        i += 1

    return ", ".join(arg_exprs)


def _build_script(code: str, language: str, input_str: str) -> str | None:
    if language not in ("python", "python3"):
        return None

    method      = _extract_method(code)
    params      = _detect_param_types(input_str, code)
    return_type = _get_return_type(code)

    args = _build_args_with_cycle(params)

    logger.debug(
        f"[判题] 签名解析: 参数=[{', '.join(f'{v}:{t}' for v, _, t in params)}]"
        f" 返回={return_type} args={args[:80]}"
    )

    return (PYTHON_WRAPPER
            .replace("__HELPERS__",     DS_HELPERS)
            .replace("__USER_CODE__",   code)
            .replace("__METHOD__",      method)
            .replace("__ARGS__",        args)
            .replace("__RETURN_TYPE__", return_type))


# ─── 输出标准化 ───────────────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    """标准化输出用于对比，消除格式差异"""
    s = s.strip()
    s = s.replace("True", "true").replace("False", "false").replace("None", "null")

    try:
        parsed = json.loads(s)
        if isinstance(parsed, float) and parsed == int(parsed):
            parsed = int(parsed)
        return json.dumps(parsed, sort_keys=False, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        pass

    try:
        parsed = ast.literal_eval(s)
        return json.dumps(parsed, sort_keys=False, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        pass

    return s.lower().strip()


# ─── 执行 ─────────────────────────────────────────────────────────────────────

def _run_sync(script: str, language: str) -> dict:
    runner = LANGUAGE_RUNNERS.get(language.lower())
    if not runner:
        return {"stdout": "", "stderr": f"不支持的语言: {language}", "returncode": -1}

    with tempfile.NamedTemporaryFile(
        suffix=".py", delete=False, mode="w", encoding="utf-8"
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
        return {
            "stdout":     r.stdout.strip(),
            "stderr":     r.stderr.strip(),
            "returncode": r.returncode,
        }
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
        first_fail: dict | None = None

        for case in cases:
            r = await self._run_one(code, language, case["input"], case["expected"])
            if r["passed"]:
                passed += 1
            else:
                if r.get("error"):
                    errors.append(r["error"])
                if first_fail is None:
                    first_fail = {
                        "input":    case["input"],
                        "expected": case["expected"],
                        "actual":   r.get("actual", ""),
                    }

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
            passed=passed,
            total=total,
            status=status,
            error_message=errors[0] if errors else "",
            failed_input=first_fail["input"]    if first_fail and status == "Wrong Answer" else None,
            failed_expected=first_fail["expected"] if first_fail and status == "Wrong Answer" else None,
            failed_actual=first_fail["actual"]   if first_fail and status == "Wrong Answer" else None,
        )

    async def _run_one(self, code, language, input_str, expected) -> dict:
        script = _build_script(code, language, input_str)
        if not script:
            return {"passed": False, "error": f"不支持的语言: {language}", "actual": ""}

        r = await _run(script, language)
        if r["returncode"] != 0:
            return {
                "passed": False,
                "error":  r["stderr"][:300] or "运行时错误",
                "actual": "",
            }

        actual    = _normalize(r["stdout"])
        expected_ = _normalize(expected)
        passed    = (actual == expected_)

        if not passed:
            logger.debug(
                f"判题不通过\n"
                f"  输入:  {input_str!r}\n"
                f"  期望:  {expected_!r}\n"
                f"  实际:  {actual!r}"
            )

        return {
            "passed": passed,
            "actual": r["stdout"].strip(),
        }

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