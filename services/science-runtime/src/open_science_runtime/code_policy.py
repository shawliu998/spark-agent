from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

from .fixed_analysis_policy import (
    COMPILED_ANALYSIS_POLICY_ID,
    COMPILED_ANALYSIS_TEMPLATE,
    FIXED_ANALYSIS_POLICY_ID,
    GENERAL_ANALYSIS_POLICY_ID,
    AnalysisPolicyId,
    AnalysisPolicyTemplate,
    FixedAnalysisPolicyError,
    FixedAnalysisTemplate,
    validate_fixed_analysis_code,
)

_BLOCKED_MODULES = frozenset({"pty", "socket", "subprocess"})
_BLOCKED_DYNAMIC_MODULES = _BLOCKED_MODULES | {"os"}
_BLOCKED_OS_MEMBERS = frozenset({"kill", "killpg", "popen", "system"})
_BLOCKED_IPYTHON_METHODS = frozenset({"getoutput", "system"})
_BLOCKED_SHELL_MAGICS = frozenset({"bash", "script", "sh", "sx", "system"})
_BLOCKED_DYNAMIC_CODE_CALLS = frozenset({"compile", "eval", "exec"})


@dataclass(frozen=True)
class CodePolicyViolation:
    message: str
    line: int | None

    def describe(self) -> str:
        return f"line {self.line}: {self.message}" if self.line is not None else self.message


class CodePolicyError(ValueError):
    pass


def validate_python_code(
    code: str,
    *,
    policy_profile_id: AnalysisPolicyId = GENERAL_ANALYSIS_POLICY_ID,
    policy_template: AnalysisPolicyTemplate | None = None,
    approved_code_sha256: str | None = None,
) -> None:
    """Reject the MVP's known shell and process escape forms before Jupyter sees code.

    This is intentionally a small, deterministic defense-in-depth policy, not a
    Python sandbox. The container remains the execution security boundary.
    """

    if policy_profile_id == FIXED_ANALYSIS_POLICY_ID:
        if policy_template not in {"baseline", "repair-1", "repair-2"}:
            raise CodePolicyError("fixed analysis policy requires a template")
        if approved_code_sha256 is not None:
            raise CodePolicyError("fixed analysis policy does not accept an approved code hash")
        try:
            validate_fixed_analysis_code(
                code,
                template=cast(FixedAnalysisTemplate, policy_template),
            )
        except FixedAnalysisPolicyError as error:
            raise CodePolicyError(str(error)) from error
        return
    if policy_profile_id == COMPILED_ANALYSIS_POLICY_ID:
        if (
            policy_template != COMPILED_ANALYSIS_TEMPLATE
            or approved_code_sha256 is None
            or hashlib.sha256(code.encode("utf-8")).hexdigest()
            != approved_code_sha256
        ):
            raise CodePolicyError("compiled analysis code does not match its approval")
        _validate_general_code(code)
        return
    if (
        policy_profile_id != GENERAL_ANALYSIS_POLICY_ID
        or policy_template is not None
        or approved_code_sha256 is not None
    ):
        raise CodePolicyError("analysis policy profile is invalid")

    _validate_general_code(code)


def _validate_general_code(code: str) -> None:

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as error:
        location = f"line {error.lineno}: " if error.lineno is not None else ""
        raise CodePolicyError(f"{location}code must be valid Python syntax") from error

    checker = _CodePolicyChecker()
    checker.visit(tree)
    if checker.violations:
        raise CodePolicyError(checker.violations[0].describe())


class _CodePolicyChecker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.os_aliases: set[str] = set()
        self.importlib_aliases: set[str] = set()
        self.dynamic_import_aliases: set[str] = {"__import__"}
        self.blocked_callable_aliases: set[str] = set()
        self.ipython_getter_aliases: set[str] = {"get_ipython"}
        self.ipython_instance_aliases: set[str] = set()
        self.violations: list[CodePolicyViolation] = []

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for imported in node.names:
            module_root = imported.name.split(".", maxsplit=1)[0]
            if module_root in _BLOCKED_MODULES:
                self._reject(node, f"importing {module_root} is not allowed")
            if module_root == "os" and (imported.name == "os" or imported.asname is None):
                self.os_aliases.add(imported.asname or "os")
            if module_root == "importlib" and (
                imported.name == "importlib" or imported.asname is None
            ):
                self.importlib_aliases.add(imported.asname or "importlib")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        module_root = module.split(".", maxsplit=1)[0]
        if module_root in _BLOCKED_MODULES:
            self._reject(node, f"importing {module_root} is not allowed")
        if module == "os":
            for imported in node.names:
                local_name = imported.asname or imported.name
                if imported.name == "*" or _is_blocked_os_member(imported.name):
                    self.blocked_callable_aliases.add(local_name)
                    self._reject(node, f"importing os.{imported.name} is not allowed")
        if module == "importlib":
            for imported in node.names:
                if imported.name == "import_module":
                    self.dynamic_import_aliases.add(imported.asname or imported.name)
        if module_root == "IPython":
            for imported in node.names:
                if imported.name == "get_ipython":
                    self.ipython_getter_aliases.add(imported.asname or imported.name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self._record_assignment_aliases(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is not None:
            self._record_assignment_aliases([node.target], node.value)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
        self._record_assignment_aliases([node.target], node.value)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if isinstance(node.value, str) and _unsafe_path_literal(node.value):
            self._reject(
                node,
                "absolute and parent-relative path literals are not allowed",
            )

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in _BLOCKED_DYNAMIC_CODE_CALLS
        ):
            self._reject(node, f"calling {node.func.id} is not allowed")
        if isinstance(node.func, ast.Name) and node.func.id in self.blocked_callable_aliases:
            self._reject(node, f"calling {node.func.id} is not allowed")

        if isinstance(node.func, ast.Attribute):
            member = node.func.attr
            if self._is_os_value(node.func.value) and _is_blocked_os_member(member):
                self._reject(node, f"calling os.{member} is not allowed")
            if self._is_ipython_instance(node.func.value):
                if member in _BLOCKED_IPYTHON_METHODS:
                    self._reject(node, f"get_ipython().{member} is not allowed")
                if member in {"run_cell_magic", "run_line_magic"} and (
                    self._blocked_magic_name(node) is not None
                ):
                    marker = "%%" if member == "run_cell_magic" else "%"
                    self._reject(
                        node,
                        f"IPython {marker}{self._blocked_magic_name(node)} magic is not allowed",
                    )

        if isinstance(node.func, ast.Name) and node.func.id in self.dynamic_import_aliases:
            module_name = self._first_string_argument(node)
            if module_name is None:
                self._reject(node, "computed dynamic imports are not allowed")
            elif module_name.split(".", maxsplit=1)[0] in _BLOCKED_DYNAMIC_MODULES:
                self._reject(node, f"dynamic import of {module_name} is not allowed")

        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.importlib_aliases
            and node.func.attr == "import_module"
        ):
            module_name = self._first_string_argument(node)
            if module_name is None:
                self._reject(node, "computed dynamic imports are not allowed")
            elif module_name.split(".", maxsplit=1)[0] in _BLOCKED_DYNAMIC_MODULES:
                self._reject(node, f"dynamic import of {module_name} is not allowed")

        if isinstance(node.func, ast.Name) and node.func.id == "getattr":
            self._check_dangerous_getattr(node)

        if any(
            keyword.arg == "shell"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        ):
            self._reject(node, "shell=True is not allowed")

        self.generic_visit(node)

    def _record_assignment_aliases(self, targets: list[ast.expr], value: ast.expr) -> None:
        names = {target.id for target in targets if isinstance(target, ast.Name)}
        if not names:
            return
        if self._is_os_value(value):
            self.os_aliases.update(names)
        if isinstance(value, ast.Name) and value.id in self.importlib_aliases:
            self.importlib_aliases.update(names)
        if isinstance(value, ast.Name) and value.id in self.dynamic_import_aliases:
            self.dynamic_import_aliases.update(names)
        if isinstance(value, ast.Name) and value.id in self.ipython_getter_aliases:
            self.ipython_getter_aliases.update(names)
        if self._is_ipython_instance(value):
            self.ipython_instance_aliases.update(names)
        if isinstance(value, ast.Attribute):
            if (
                isinstance(value.value, ast.Name)
                and value.value.id in self.importlib_aliases
                and value.attr == "import_module"
            ):
                self.dynamic_import_aliases.update(names)
            if self._is_os_value(value.value) and _is_blocked_os_member(value.attr):
                self.blocked_callable_aliases.update(names)
                self._reject(value, f"referencing os.{value.attr} is not allowed")
            if self._is_ipython_instance(value.value) and value.attr in _BLOCKED_IPYTHON_METHODS:
                self.blocked_callable_aliases.update(names)
                self._reject(value, f"referencing get_ipython().{value.attr} is not allowed")

    def _is_os_value(self, node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id in self.os_aliases

    def _is_ipython_instance(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.ipython_instance_aliases
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in self.ipython_getter_aliases
        )

    def _blocked_magic_name(self, node: ast.Call) -> str | None:
        magic_name = self._first_string_argument(node)
        if magic_name is None:
            for keyword in node.keywords:
                if keyword.arg == "magic_name" and isinstance(keyword.value, ast.Constant):
                    if isinstance(keyword.value.value, str):
                        magic_name = keyword.value.value
                        break
        normalized = magic_name.lower() if magic_name is not None else None
        return normalized if normalized in _BLOCKED_SHELL_MAGICS else None

    def _check_dangerous_getattr(self, node: ast.Call) -> None:
        if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
            return
        member = node.args[1].value
        if not isinstance(member, str):
            return
        if self._is_os_value(node.args[0]) and _is_blocked_os_member(member):
            self._reject(node, f"accessing os.{member} is not allowed")
        if self._is_ipython_instance(node.args[0]) and member in _BLOCKED_IPYTHON_METHODS:
            self._reject(node, f"accessing get_ipython().{member} is not allowed")

    @staticmethod
    def _first_string_argument(node: ast.Call) -> str | None:
        if not node.args or not isinstance(node.args[0], ast.Constant):
            return None
        return node.args[0].value if isinstance(node.args[0].value, str) else None

    def _reject(self, node: ast.AST, message: str) -> None:
        self.violations.append(
            CodePolicyViolation(message=message, line=getattr(node, "lineno", None))
        )


def _is_blocked_os_member(member: str) -> bool:
    return (
        member in _BLOCKED_OS_MEMBERS
        or member.startswith("spawn")
        or member.startswith("posix_spawn")
    )


def _unsafe_path_literal(value: str) -> bool:
    path = PurePosixPath(value)
    return path.is_absolute() or ".." in path.parts
