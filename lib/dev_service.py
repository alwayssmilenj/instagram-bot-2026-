"""Developer tools, safe AST Python evaluation, and code intelligence."""
from __future__ import annotations

import ast
import logging
import math
import sys
import time

LOGGER = logging.getLogger("jinshi_mds")


class SafeCodeEvaluator(ast.NodeVisitor):
    """AST validator to ensure Python code contains only safe mathematical and analytical operations."""

    ALLOWED_NODES = {
        ast.Module, ast.Expr, ast.Expression,
        ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict,
        ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
        ast.USub, ast.UAdd, ast.Not,
        ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot, ast.In, ast.NotIn,
        ast.And, ast.Or,
        ast.Call, ast.Name, ast.Load, ast.Store, ast.comprehension, ast.ListComp, ast.SetComp, ast.DictComp,
        ast.GeneratorExp, ast.FormattedValue, ast.JoinedStr,
    }

    ALLOWED_NAMES = {
        "abs", "round", "min", "max", "sum", "len", "sorted", "reversed",
        "int", "float", "str", "bool", "list", "tuple", "dict", "set",
        "math", "range", "zip", "enumerate", "map", "filter", "all", "any",
    }

    def __init__(self) -> None:
        super().__init__()
        self.local_names: set[str] = set()

    def generic_visit(self, node: ast.AST) -> None:
        if type(node) not in self.ALLOWED_NODES:
            raise ValueError(f"Disallowed Python construct: {type(node).__name__}")
        super().generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        for gen in node.generators:
            self.visit(gen)
        self.visit(node.elt)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        for gen in node.generators:
            self.visit(gen)
        self.visit(node.elt)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        for gen in node.generators:
            self.visit(gen)
        self.visit(node.key)
        self.visit(node.value)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        for gen in node.generators:
            self.visit(gen)
        self.visit(node.elt)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self._extract_targets(node.target)
        self.visit(node.iter)
        for if_clause in node.ifs:
            self.visit(if_clause)

    def _extract_targets(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self.local_names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._extract_targets(elt)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            if node.id not in self.ALLOWED_NAMES and node.id not in self.local_names:
                raise ValueError(f"Disallowed variable or function access: '{node.id}'")
        self.generic_visit(node)

    SAFE_METHODS = {
        "join", "split", "replace", "strip", "lstrip", "rstrip",
        "lower", "upper", "title", "capitalize", "startswith", "endswith",
        "find", "rfind", "index", "count", "format", "center", "zfill",
        "append", "extend", "insert", "pop", "remove", "clear", "sort", "reverse", "copy",
        "keys", "values", "items", "get",
        "add", "discard", "union", "intersection", "difference",
    }

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            raise ValueError("Private attribute access is forbidden")
        if isinstance(node.value, ast.Name) and node.value.id == "math":
            return
        if node.attr in self.SAFE_METHODS:
            self.visit(node.value)
            return
        raise ValueError(f"Attribute access '{node.attr}' is forbidden")


class DevService:
    """Safe Python code runner and developer AI assistant."""

    SAFE_GLOBALS = {
        "__builtins__": {
            "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
            "len": len, "sorted": sorted, "reversed": reversed,
            "int": int, "float": float, "str": str, "bool": bool,
            "list": list, "tuple": tuple, "dict": dict, "set": set,
            "range": range, "zip": zip, "enumerate": enumerate,
            "map": map, "filter": filter, "all": all, "any": any,
        },
        "math": math,
    }

    @classmethod
    def run_python(cls, code: str) -> str:
        clean_code = code.strip().strip("`").lstrip("python\n").strip()
        if not clean_code:
            return "Usage: .runpython <python expression or code>"

        if len(clean_code) > 500:
            return "❌ Code too long (max 500 characters)."

        try:
            tree = ast.parse(clean_code, mode="eval")
            validator = SafeCodeEvaluator()
            validator.visit(tree)

            compiled = compile(tree, "<safe_sandbox>", "eval")
            start = time.perf_counter()
            result = eval(compiled, cls.SAFE_GLOBALS, {})
            duration_ms = (time.perf_counter() - start) * 1000

            res_str = repr(result)
            if len(res_str) > 600:
                res_str = res_str[:600] + "… (truncated)"

            return f"🐍 Python Output ({duration_ms:.2f}ms):\n```\n{res_str}\n```"
        except SyntaxError as syn_err:
            return f"❌ Syntax Error: {syn_err}"
        except ValueError as val_err:
            return f"🛡️ Sandbox Restriction: {val_err}"
        except Exception as err:
            return f"❌ Runtime Error: {type(err).__name__}: {err}"

    @classmethod
    def review_code(cls, code: str, ai_service: object = None) -> str:
        clean_code = code.strip().strip("`").strip()
        if not clean_code:
            return "Usage: .codereview <code snippet>"

        if ai_service is None or not hasattr(ai_service, "reply"):
            return "❌ AI service unavailable for code review."

        prompt = (
            "You are a Principal Software Engineer and Security Auditor.\n"
            "Perform a concise, high-IQ code review of the following code snippet.\n"
            "Highlight:\n"
            "1. Complexity & Performance bottlenecks\n"
            "2. Bugs or edge cases\n"
            "3. Security vulnerabilities\n"
            "4. Refactored snippet\n\n"
            f"Code:\n```\n{clean_code[:1200]}\n```"
        )
        try:
            return ai_service.reply(prompt, "code_reviewer", "dev_sys")
        except Exception as err:
            return f"❌ Code review failed: {err}"

    @classmethod
    def explain_code(cls, code: str, ai_service: object = None) -> str:
        clean_code = code.strip().strip("`").strip()
        if not clean_code:
            return "Usage: .explaincode <code snippet>"

        if ai_service is None or not hasattr(ai_service, "reply"):
            return "❌ AI service unavailable for code explanation."

        prompt = (
            "You are a brilliant Computer Science professor.\n"
            "Explain the following code step-by-step with algorithm breakdown and time/space complexity:\n\n"
            f"```\n{clean_code[:1200]}\n```"
        )
        try:
            return ai_service.reply(prompt, "code_explainer", "dev_sys")
        except Exception as err:
            return f"❌ Code explanation failed: {err}"
