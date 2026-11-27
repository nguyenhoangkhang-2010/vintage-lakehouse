"""Static look-ahead lint: patterns in Python source that commonly leak the future.

This is a heuristic aid for review, not a proof (ADR 0011). It reads source with ``ast`` and
flags patterns that often introduce look-ahead in time-series code. It has false positives (a
negative shift to build a *target* is fine) and false negatives (a leak through a helper in
another module is not seen). The dynamic check in ``vintage_lakehouse.audit.leak_check`` is the
reliable one.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

RULES: dict[str, str] = {
    "LA000": "The file could not be parsed.",
    "LA001": "A negative shift pulls future values into the present row.",
    "LA002": "Back-filling copies later values onto earlier periods.",
    "LA003": "The wall clock makes the result depend on when the code runs, not on the origin.",
    "LA004": "as_of is called with a fixed or wall-clock time instead of the forecast origin.",
    "LA005": "revisions() returns versions published after any origin unless they are filtered.",
    "LA006": "A shuffled train/test split mixes the future into the training set.",
    "LA007": "A centred rolling window uses values after the current period.",
}

_CLOCK_ATTRS = {"now", "utcnow", "today"}
_TIME_CONSTRUCTORS = {
    "datetime",
    "now",
    "utcnow",
    "today",
    "fromisoformat",
    "combine",
    "strptime",
    "date",
}


@dataclass(frozen=True)
class LintFinding:
    """One flagged place in a file."""

    filename: str
    line: int
    column: int
    rule: str
    message: str


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _is_negative(node: ast.expr) -> bool:
    # source such as ``-1`` parses as a unary minus on 1, never as a negative constant
    return isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)


def _is_constant(node: ast.expr, value: object) -> bool:
    return isinstance(node, ast.Constant) and node.value is value


def _is_clock_call(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    owner = _name(func.value)
    if func.attr in _CLOCK_ATTRS and owner in {"datetime", "date"}:
        return True
    return func.attr == "time" and owner == "time"


def _as_of_time_argument(call: ast.Call) -> ast.expr | None:
    if len(call.args) >= 2:
        return call.args[1]
    return _keyword(call, "at")


def _is_fixed_time(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Call):
        return _name(node.func) in _TIME_CONSTRUCTORS
    if isinstance(node, ast.Attribute):
        return node.attr in {"max", "min"} and _name(node.value) in {"datetime", "date"}
    return False


class _Visitor(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.findings: list[LintFinding] = []

    def _add(self, node: ast.AST, rule: str) -> None:
        self.findings.append(
            LintFinding(
                self.filename,
                getattr(node, "lineno", 1),
                getattr(node, "col_offset", 0),
                rule,
                RULES[rule],
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        name = _name(node.func)
        is_method = isinstance(node.func, ast.Attribute)

        if is_method and name == "shift":
            argument = node.args[0] if node.args else _keyword(node, "periods")
            if argument is not None and _is_negative(argument):
                self._add(node, "LA001")
        if is_method and name in {"bfill", "backfill"}:
            self._add(node, "LA002")
        if is_method and name == "fillna":
            method = _keyword(node, "method")
            if (
                isinstance(method, ast.Constant)
                and isinstance(method.value, str)
                and method.value in {"bfill", "backfill"}
            ):
                self._add(node, "LA002")
        if _is_clock_call(node):
            self._add(node, "LA003")
        if is_method and name == "as_of":
            argument = _as_of_time_argument(node)
            if argument is not None and _is_fixed_time(argument):
                self._add(node, "LA004")
        if is_method and name == "revisions":
            self._add(node, "LA005")
        if name == "train_test_split":
            shuffle = _keyword(node, "shuffle")
            if shuffle is None or not _is_constant(shuffle, False):
                self._add(node, "LA006")
        if is_method and name == "rolling":
            center = _keyword(node, "center")
            if center is not None and _is_constant(center, True):
                self._add(node, "LA007")
        self.generic_visit(node)


def lint_source(source: str, filename: str = "<string>") -> list[LintFinding]:
    """Lint Python source text. Findings are ordered by position."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [
            LintFinding(
                filename,
                exc.lineno or 1,
                (exc.offset or 1) - 1,
                "LA000",
                f"Could not parse the file: {exc.msg}.",
            )
        ]
    visitor = _Visitor(filename)
    visitor.visit(tree)
    return sorted(visitor.findings, key=lambda f: (f.line, f.column, f.rule))


def _python_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files += sorted(path.rglob("*.py"))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"no such file or folder: {path}")
    return files


def lint_paths(paths: Iterable[Path]) -> list[LintFinding]:
    """Lint files, and every ``.py`` file under folders."""
    findings: list[LintFinding] = []
    for file in _python_files(paths):
        findings += lint_source(file.read_text(encoding="utf-8"), filename=str(file))
    return findings
