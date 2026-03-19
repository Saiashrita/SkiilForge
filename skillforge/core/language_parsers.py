"""
skillforge/core/language_parsers.py

Multi-language parser abstraction layer.
Handles Python (via stdlib ast) and JS/TS/JSX/TSX (via tree-sitter).
Produces language-agnostic ParsedUnit objects for downstream pattern detection.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger(__name__)

# Extension → language mapping
_EXT_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
}

# Extensions that use tree-sitter
_TREE_SITTER_EXTS: set[str] = {".js", ".jsx", ".ts", ".tsx"}


@dataclass(frozen=True, slots=True)
class ParsedUnit:
    """Language-agnostic representation of a detected code unit."""

    name: str
    language: str
    source_code: str
    node_type: str  # "function" | "class" | "component" | "hook" | "arrow_fn"
    decorators: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    exports: bool = False
    line_start: int = 0
    line_end: int = 0
    n_lines: int = 0


# ── Python Parser (stdlib ast) ───────────────────────────────────────────────


def _parse_python(source: str, path: Path) -> list[ParsedUnit]:
    """Parse Python source into ParsedUnit list using stdlib ast."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        log.warning("python_parse_error", path=str(path), error=str(exc))
        return []

    units: list[ParsedUnit] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            units.append(_python_function_to_unit(node, source))
        elif isinstance(node, ast.ClassDef):
            units.append(_python_class_to_unit(node, source))

    return units


def _python_function_to_unit(node: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> ParsedUnit:
    """Convert a Python function/async function AST node to ParsedUnit."""
    decorators = []
    for dec in node.decorator_list:
        try:
            decorators.append(ast.unparse(dec))
        except Exception:
            decorators.append("unknown_decorator")

    calls = _extract_python_calls(node)
    snippet = ast.get_source_segment(source, node) or ""
    line_end = node.end_lineno or node.lineno
    n_lines = line_end - node.lineno

    return ParsedUnit(
        name=node.name,
        language="python",
        source_code=snippet,
        node_type="function",
        decorators=decorators,
        calls=calls,
        exports=False,
        line_start=node.lineno,
        line_end=line_end,
        n_lines=n_lines,
    )


def _python_class_to_unit(node: ast.ClassDef, source: str) -> ParsedUnit:
    """Convert a Python class AST node to ParsedUnit."""
    decorators = []
    for dec in node.decorator_list:
        try:
            decorators.append(ast.unparse(dec))
        except Exception:
            decorators.append("unknown_decorator")

    calls = _extract_python_calls(node)
    snippet = ast.get_source_segment(source, node) or ""
    line_end = node.end_lineno or node.lineno
    n_lines = line_end - node.lineno

    return ParsedUnit(
        name=node.name,
        language="python",
        source_code=snippet,
        node_type="class",
        decorators=decorators,
        calls=calls,
        exports=False,
        line_start=node.lineno,
        line_end=line_end,
        n_lines=n_lines,
    )


def _extract_python_calls(node: ast.AST) -> list[str]:
    """Extract unique function/method call names from a Python AST subtree."""
    calls: list[str] = []
    seen: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = ""
            if isinstance(child.func, ast.Attribute):
                try:
                    name = f"{ast.unparse(child.func.value)}.{child.func.attr}"
                except Exception:
                    name = child.func.attr
            elif isinstance(child.func, ast.Name):
                name = child.func.id
            if name and name not in seen:
                seen.add(name)
                calls.append(name)
    return calls


# ── JS/TS/JSX/TSX Parser (tree-sitter) ──────────────────────────────────────

# Lazy-loaded tree-sitter languages to avoid import cost on every call
_ts_parser: Optional[object] = None
_ts_languages: dict[str, object] = {}


def _get_ts_language(ext: str) -> object:
    """Get or create the tree-sitter Language object for a given extension."""
    global _ts_parser, _ts_languages

    if ext in _ts_languages:
        return _ts_languages[ext]

    try:
        import tree_sitter as ts
    except ImportError:
        raise RuntimeError(
            "tree-sitter not installed. Run: pip install tree-sitter "
            "tree-sitter-javascript tree-sitter-typescript"
        )

    if _ts_parser is None:
        _ts_parser = ts.Parser()

    if ext in (".js", ".jsx"):
        import tree_sitter_javascript as ts_js
        lang = ts.Language(ts_js.language())
    elif ext == ".ts":
        import tree_sitter_typescript as ts_ts
        lang = ts.Language(ts_ts.language_typescript())
    elif ext == ".tsx":
        import tree_sitter_typescript as ts_ts
        lang = ts.Language(ts_ts.language_tsx())
    else:
        raise ValueError(f"Unsupported extension: {ext}")

    _ts_languages[ext] = lang
    return lang


def _parse_js_ts(source: str, path: Path) -> list[ParsedUnit]:
    """Parse JS/TS/JSX/TSX source into ParsedUnit list using tree-sitter."""
    ext = path.suffix.lower()
    language_name = _EXT_LANG.get(ext, "javascript")

    try:
        import tree_sitter as ts
        lang = _get_ts_language(ext)
        parser = ts.Parser(lang)
        tree = parser.parse(source.encode("utf-8"))
    except Exception as exc:
        log.warning("treesitter_parse_error", path=str(path), error=str(exc))
        return []

    units: list[ParsedUnit] = []
    source_bytes = source.encode("utf-8")

    _walk_js_node(tree.root_node, source_bytes, language_name, units, is_exported=False)

    return units


def _walk_js_node(
    node: object,
    source_bytes: bytes,
    language: str,
    units: list[ParsedUnit],
    is_exported: bool = False,
) -> None:
    """Recursively walk tree-sitter nodes and extract functions/classes/components."""
    node_type = node.type

    # Export declarations — mark child as exported
    if node_type in ("export_statement", "export_default_declaration"):
        for child in node.children:
            _walk_js_node(child, source_bytes, language, units, is_exported=True)
        return

    # Function declarations
    if node_type in ("function_declaration", "generator_function_declaration"):
        unit = _js_function_node_to_unit(node, source_bytes, language, is_exported)
        if unit:
            units.append(unit)
        return

    # Arrow functions / function expressions assigned to variables
    if node_type in ("lexical_declaration", "variable_declaration"):
        unit = _js_variable_fn_to_unit(node, source_bytes, language, is_exported)
        if unit:
            units.append(unit)
            return

    # Class declarations
    if node_type == "class_declaration":
        unit = _js_class_to_unit(node, source_bytes, language, is_exported)
        if unit:
            units.append(unit)
        return

    # Recurse into children
    for child in node.children:
        _walk_js_node(child, source_bytes, language, units, is_exported=False)


def _get_node_text(node: object, source_bytes: bytes) -> str:
    """Extract text from a tree-sitter node."""
    return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: object, type_name: str) -> Optional[object]:
    """Find the first child node of a given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_children_by_type(node: object, type_name: str) -> list[object]:
    """Find all children of a given type."""
    return [c for c in node.children if c.type == type_name]


def _extract_js_calls(node: object, source_bytes: bytes) -> list[str]:
    """Extract function call names from a tree-sitter subtree."""
    calls: list[str] = []
    seen: set[str] = set()
    _collect_js_calls(node, source_bytes, calls, seen)
    return calls


def _collect_js_calls(
    node: object, source_bytes: bytes, calls: list[str], seen: set[str]
) -> None:
    """Recursively collect call expressions."""
    if node.type == "call_expression":
        fn_node = node.children[0] if node.children else None
        if fn_node:
            name = _get_node_text(fn_node, source_bytes)
            # Clean up long chains — take only the last part if short enough
            if len(name) > 60:
                parts = name.split(".")
                name = ".".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
            if name and name not in seen:
                seen.add(name)
                calls.append(name)

    for child in node.children:
        _collect_js_calls(child, source_bytes, calls, seen)


def _is_react_component(name: str, node: object, source_bytes: bytes) -> bool:
    """Check if a function is a React component (PascalCase + returns JSX)."""
    if not name or not name[0].isupper():
        return False

    text = _get_node_text(node, source_bytes)
    jsx_indicators = ("<", "jsx", "React.createElement", "/>")
    return any(ind in text for ind in jsx_indicators)


def _is_react_hook(name: str) -> bool:
    """Check if a function is a React custom hook (starts with 'use')."""
    return name.startswith("use") and len(name) > 3 and name[3].isupper()


def _classify_js_unit(name: str, node: object, source_bytes: bytes) -> str:
    """Classify a JS/TS function into: function, component, hook, or arrow_fn."""
    if _is_react_hook(name):
        return "hook"
    if _is_react_component(name, node, source_bytes):
        return "component"
    # Check if it's an arrow function
    text = _get_node_text(node, source_bytes)
    if "=>" in text and node.type != "function_declaration":
        return "arrow_fn"
    return "function"


def _js_function_node_to_unit(
    node: object, source_bytes: bytes, language: str, is_exported: bool
) -> Optional[ParsedUnit]:
    """Convert a tree-sitter function_declaration to ParsedUnit."""
    name_node = _find_child_by_type(node, "identifier")
    if not name_node:
        return None

    name = _get_node_text(name_node, source_bytes)
    source_code = _get_node_text(node, source_bytes)
    calls = _extract_js_calls(node, source_bytes)
    unit_type = _classify_js_unit(name, node, source_bytes)
    n_lines = node.end_point[0] - node.start_point[0]

    decorators = _extract_js_decorators(node, source_bytes)

    return ParsedUnit(
        name=name,
        language=language,
        source_code=source_code,
        node_type=unit_type,
        decorators=decorators,
        calls=calls,
        exports=is_exported,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        n_lines=n_lines,
    )


def _js_variable_fn_to_unit(
    node: object, source_bytes: bytes, language: str, is_exported: bool
) -> Optional[ParsedUnit]:
    """Extract arrow functions / function expressions from variable declarations."""
    for child in node.children:
        if child.type == "variable_declarator":
            name_node = _find_child_by_type(child, "identifier")
            value_node = None
            for c in child.children:
                if c.type in ("arrow_function", "function_expression", "function"):
                    value_node = c
                    break

            if not name_node or not value_node:
                continue

            name = _get_node_text(name_node, source_bytes)
            source_code = _get_node_text(node, source_bytes)
            calls = _extract_js_calls(value_node, source_bytes)
            unit_type = _classify_js_unit(name, value_node, source_bytes)
            n_lines = node.end_point[0] - node.start_point[0]

            return ParsedUnit(
                name=name,
                language=language,
                source_code=source_code,
                node_type=unit_type,
                decorators=[],
                calls=calls,
                exports=is_exported,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                n_lines=n_lines,
            )

    return None


def _js_class_to_unit(
    node: object, source_bytes: bytes, language: str, is_exported: bool
) -> Optional[ParsedUnit]:
    """Convert a tree-sitter class_declaration to ParsedUnit."""
    name_node = _find_child_by_type(node, "identifier") or _find_child_by_type(
        node, "type_identifier"
    )
    if not name_node:
        return None

    name = _get_node_text(name_node, source_bytes)
    source_code = _get_node_text(node, source_bytes)
    calls = _extract_js_calls(node, source_bytes)
    decorators = _extract_js_decorators(node, source_bytes)
    n_lines = node.end_point[0] - node.start_point[0]

    return ParsedUnit(
        name=name,
        language=language,
        source_code=source_code,
        node_type="class",
        decorators=decorators,
        calls=calls,
        exports=is_exported,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        n_lines=n_lines,
    )


def _extract_js_decorators(node: object, source_bytes: bytes) -> list[str]:
    """Extract decorator-like patterns from JS/TS nodes."""
    decorators: list[str] = []
    # TypeScript/experimental decorators
    for child in node.children:
        if child.type == "decorator":
            decorators.append(_get_node_text(child, source_bytes))
    return decorators


# ── Public API ───────────────────────────────────────────────────────────────


def parse_file(path: Path) -> list[ParsedUnit]:
    """
    Parse a source file into a list of ParsedUnit objects.

    Supports: .py, .js, .jsx, .ts, .tsx
    Uses stdlib ast for Python, tree-sitter for JS/TS/JSX/TSX.
    Gracefully returns [] on unsupported files or parse errors.

    Args:
        path: Path to the source file.

    Returns:
        List of ParsedUnit objects found in the file.
    """
    ext = path.suffix.lower()
    if ext not in _EXT_LANG:
        return []

    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        log.warning("file_read_error", path=str(path), error=str(exc))
        return []

    if not source.strip():
        return []

    if ext == ".py":
        return _parse_python(source, path)
    elif ext in _TREE_SITTER_EXTS:
        return _parse_js_ts(source, path)

    return []


def get_language(path: Path) -> Optional[str]:
    """Get the language name for a file path, or None if unsupported."""
    return _EXT_LANG.get(path.suffix.lower())
