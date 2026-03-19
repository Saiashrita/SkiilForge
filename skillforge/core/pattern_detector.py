"""
skillforge/core/pattern_detector.py

Multi-language, AST-based structural pattern detector.
Supports Python, JS/TS/JSX/TSX via the language_parsers abstraction layer.
Features: exact hash matching, fuzzy similarity matching, class/component detection,
decorator-aware normalization, and co-occurrence tracking.
"""
from __future__ import annotations

import ast
import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import structlog

from skillforge.core.language_parsers import ParsedUnit, parse_file

log = structlog.get_logger(__name__)


@dataclass
class PatternCandidate:
    """A code pattern that has been detected frequently enough for crystallization."""

    structural_hash: str
    source_code: str
    abstract_signature: str
    language: str
    frequency: int
    first_seen_file: str
    contexts: list
    complexity_score: float
    node_type: str = "function"
    decorators: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    first_seen_ts: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "PatternCandidate":
        data = json.loads(raw)
        # Backwards compatibility: fill missing fields
        data.setdefault("node_type", "function")
        data.setdefault("decorators", [])
        data.setdefault("calls", [])
        return cls(**data)


# ── AST Normalization (Python) ───────────────────────────────────────────────


class _Normalizer(ast.NodeTransformer):
    """Normalize Python AST for structural comparison.

    - Renames all variables/args to _v0, _v1, ...
    - Replaces constants with their type name
    - Preserves function/class structure and decorator names
    """

    def __init__(self) -> None:
        self._map: dict[str, str] = {}
        self._counter: int = 0

    def _normalize_name(self, name: str) -> str:
        if name not in self._map:
            self._map[name] = f"_v{self._counter}"
            self._counter += 1
        return self._map[name]

    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = self._normalize_name(node.id)
        return node

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.arg = self._normalize_name(node.arg)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        node.value = type(node.value).__name__
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        # Preserve decorator names (they carry semantic meaning)
        node.name = "skill_fn"
        self.generic_visit(node)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        node.name = "skill_cls"
        self.generic_visit(node)
        return node


# ── Hashing ──────────────────────────────────────────────────────────────────


def _hash_python(source_code: str) -> str:
    """Hash a Python code snippet by normalizing its AST structure."""
    try:
        tree = ast.parse(source_code)
        normalized = _Normalizer().visit(tree)
        return hashlib.sha256(ast.dump(normalized).encode()).hexdigest()[:16]
    except Exception as exc:
        log.debug("python_hash_error", error=str(exc))
        return ""


def _hash_js_ts(source_code: str) -> str:
    """Hash a JS/TS code snippet using token-level normalization.

    Since we can't use ast.dump for JS, we normalize the token sequence:
    - Replace identifiers with placeholders
    - Replace string/number literals with type markers
    - Hash the resulting normalized string
    """
    import re

    normalized = source_code
    # Replace string literals
    normalized = re.sub(r"'[^']*'", "'STR'", normalized)
    normalized = re.sub(r'"[^"]*"', '"STR"', normalized)
    normalized = re.sub(r"`[^`]*`", "`STR`", normalized)
    # Replace numeric literals
    normalized = re.sub(r"\b\d+\.?\d*\b", "NUM", normalized)
    # Replace identifiers (but preserve keywords and structural tokens)
    js_keywords = {
        "function", "const", "let", "var", "return", "if", "else", "for", "while",
        "switch", "case", "break", "continue", "class", "extends", "import", "export",
        "default", "from", "async", "await", "try", "catch", "finally", "throw",
        "new", "this", "super", "typeof", "instanceof", "in", "of", "true", "false",
        "null", "undefined", "void", "delete", "yield", "static", "get", "set",
        "interface", "type", "enum", "implements", "abstract", "private", "protected",
        "public", "readonly", "as", "is", "keyof", "infer", "extends", "declare",
        "module", "namespace", "require", "React", "useState", "useEffect",
        "useCallback", "useMemo", "useRef", "useContext", "useReducer",
    }
    tokens = re.findall(r"[a-zA-Z_$][a-zA-Z0-9_$]*|[^\s]", normalized)
    counter = 0
    name_map: dict[str, str] = {}
    normalized_tokens: list[str] = []
    for token in tokens:
        if re.match(r"^[a-zA-Z_$]", token) and token not in js_keywords:
            if token not in name_map:
                name_map[token] = f"_v{counter}"
                counter += 1
            normalized_tokens.append(name_map[token])
        else:
            normalized_tokens.append(token)

    result = " ".join(normalized_tokens)
    return hashlib.sha256(result.encode()).hexdigest()[:16]


def _hash_unit(unit: ParsedUnit) -> str:
    """Hash a ParsedUnit regardless of language."""
    if unit.language == "python":
        return _hash_python(unit.source_code)
    return _hash_js_ts(unit.source_code)


# ── Complexity Scoring ───────────────────────────────────────────────────────


def _complexity_python(source_code: str) -> float:
    """Calculate cyclomatic complexity for Python code."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return 1.0

    return 1.0 + sum(
        1
        for n in ast.walk(tree)
        if isinstance(
            n,
            (
                ast.If, ast.For, ast.While, ast.ExceptHandler,
                ast.With, ast.comprehension, ast.BoolOp,
            ),
        )
    )


def _complexity_js_ts(source_code: str) -> float:
    """Approximate cyclomatic complexity for JS/TS code."""
    import re

    complexity = 1.0
    patterns = [
        r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b",
        r"\bswitch\b", r"\bcatch\b", r"\bcase\b", r"\?\?",
        r"\?\.", r"\btry\b", r"&&", r"\|\|", r"\?(?!=)",
    ]
    for pattern in patterns:
        complexity += len(re.findall(pattern, source_code))
    return complexity


def _complexity(unit: ParsedUnit) -> float:
    """Calculate complexity score for a ParsedUnit."""
    if unit.language == "python":
        return _complexity_python(unit.source_code)
    return _complexity_js_ts(unit.source_code)


# ── Signature Extraction ────────────────────────────────────────────────────


def _signature(unit: ParsedUnit) -> str:
    """Extract an abstract signature (call chain) from a ParsedUnit."""
    calls = unit.calls[:6]
    if not calls:
        return f"{unit.node_type}::{unit.name}"
    return " → ".join(calls)


# ── Fuzzy Matching ──────────────────────────────────────────────────────────


def _fuzzy_similarity(source_a: str, source_b: str) -> float:
    """
    Calculate fuzzy structural similarity between two code snippets.

    Uses SequenceMatcher on normalized token sequences for a quick
    approximation of AST structural similarity.

    Args:
        source_a: First code snippet.
        source_b: Second code snippet.

    Returns:
        Similarity score between 0.0 and 1.0.
    """
    import re

    def tokenize(src: str) -> list[str]:
        return re.findall(r"[a-zA-Z_]+|[^\s\w]", src)

    tokens_a = tokenize(source_a)
    tokens_b = tokenize(source_b)

    if not tokens_a or not tokens_b:
        return 0.0

    return SequenceMatcher(None, tokens_a, tokens_b).ratio()


# ── Main Detector ────────────────────────────────────────────────────────────


class PatternDetector:
    """
    Multi-language pattern detector.

    Processes files through language_parsers, hashes code units, tracks
    frequency, and promotes candidates when they exceed thresholds.

    Supports exact hash matching and optional fuzzy similarity matching.
    """

    def __init__(
        self,
        min_lines: int = 5,
        min_frequency: int = 2,
        min_complexity: float = 2.0,
        similarity_threshold: float = 0.85,
        detect_classes: bool = True,
        candidates_dir: Optional[Path] = None,
    ) -> None:
        self.min_lines = min_lines
        self.min_frequency = min_frequency
        self.min_complexity = min_complexity
        self.similarity_threshold = similarity_threshold
        self.detect_classes = detect_classes
        self.candidates_dir = candidates_dir

        # hash -> list of {source, context, complexity, unit_type}
        self._seen: defaultdict[str, list[dict]] = defaultdict(list)
        self._promoted: set[str] = set()

        # For fuzzy matching: store source code per hash
        self._hash_source: dict[str, str] = {}

        if candidates_dir:
            self._load_persisted()

    def process_file(
        self,
        path: Path,
        context: str = "",
    ) -> list[PatternCandidate]:
        """
        Parse and analyze a file for repeated code patterns.

        Supports: .py, .js, .jsx, .ts, .tsx

        Args:
            path: Path to the source file.
            context: Optional context string (defaults to file path).

        Returns:
            List of newly promoted PatternCandidate objects.
        """
        context = context or str(path)
        units = parse_file(path)

        if not units:
            return []

        log.debug(
            "file_parsed",
            path=path.name,
            language=units[0].language if units else "unknown",
            units_found=len(units),
        )

        promoted: list[PatternCandidate] = []

        for unit in units:
            # Skip classes if detection is disabled
            if unit.node_type == "class" and not self.detect_classes:
                continue

            # Check minimum size
            if unit.n_lines < self.min_lines:
                log.debug("unit_skip_too_short", name=unit.name, lines=unit.n_lines)
                continue

            # Check complexity
            cmplx = _complexity(unit)
            if cmplx < self.min_complexity:
                log.debug("unit_skip_low_complexity", name=unit.name, complexity=cmplx)
                continue

            # Hash the unit
            h = _hash_unit(unit)
            if not h:
                continue

            # Try fuzzy matching against existing hashes if no exact match
            matched_hash = h
            if h not in self._seen and self._hash_source:
                matched_hash = self._find_fuzzy_match(unit.source_code, h)

            # Track this occurrence
            self._seen[matched_hash].append({
                "source": unit.source_code,
                "context": context,
                "complexity": cmplx,
                "unit_type": unit.node_type,
            })
            self._hash_source[matched_hash] = unit.source_code
            freq = len(self._seen[matched_hash])

            log.info(
                "pattern_seen",
                hash=matched_hash[:8],
                freq=freq,
                required=self.min_frequency,
                name=unit.name,
                type=unit.node_type,
                language=unit.language,
            )

            # Promote if threshold reached
            if freq >= self.min_frequency and matched_hash not in self._promoted:
                self._promoted.add(matched_hash)
                candidate = PatternCandidate(
                    structural_hash=matched_hash,
                    source_code=self._seen[matched_hash][0]["source"],
                    abstract_signature=_signature(unit),
                    language=unit.language,
                    frequency=freq,
                    first_seen_file=self._seen[matched_hash][0]["context"],
                    contexts=[o["context"] for o in self._seen[matched_hash]],
                    complexity_score=cmplx,
                    node_type=unit.node_type,
                    decorators=unit.decorators,
                    calls=unit.calls,
                )
                log.info(
                    "pattern_promoted",
                    hash=matched_hash[:8],
                    name=unit.name,
                    type=unit.node_type,
                    language=unit.language,
                    freq=freq,
                )

                # Persist to disk
                if self.candidates_dir:
                    self._persist_candidate(candidate)

                promoted.append(candidate)

        return promoted

    def get_units(self, path: Path) -> list[ParsedUnit]:
        """Parse a file and return its ParsedUnit objects (for composition detection)."""
        return parse_file(path)

    def _find_fuzzy_match(self, source: str, original_hash: str) -> str:
        """
        Try to find a fuzzy match among existing hashes.

        Args:
            source: Source code to match.
            original_hash: The exact hash (used as fallback).

        Returns:
            The matching hash if similarity > threshold, else original_hash.
        """
        best_score = 0.0
        best_hash = original_hash

        for existing_hash, existing_source in self._hash_source.items():
            if existing_hash == original_hash:
                continue
            score = _fuzzy_similarity(source, existing_source)
            if score > self.similarity_threshold and score > best_score:
                best_score = score
                best_hash = existing_hash
                log.debug(
                    "fuzzy_match_found",
                    score=f"{score:.3f}",
                    original_hash=original_hash[:8],
                    matched_hash=existing_hash[:8],
                )

        return best_hash

    def _persist_candidate(self, candidate: PatternCandidate) -> None:
        """Persist a promoted candidate to disk as JSON."""
        if not self.candidates_dir:
            return
        try:
            p = self.candidates_dir / f"{candidate.structural_hash}.json"
            p.write_text(candidate.to_json(), encoding="utf-8")
        except Exception as exc:
            log.warning("persist_error", error=str(exc))

    def _load_persisted(self) -> None:
        """Load previously persisted candidates from disk."""
        if not self.candidates_dir:
            return
        for f in self.candidates_dir.glob("*.json"):
            try:
                c = PatternCandidate.from_json(f.read_text())
                for ctx in c.contexts:
                    self._seen[c.structural_hash].append({
                        "source": c.source_code,
                        "context": ctx,
                        "complexity": c.complexity_score,
                        "unit_type": c.node_type,
                    })
                self._hash_source[c.structural_hash] = c.source_code
                self._promoted.add(c.structural_hash)
            except Exception as exc:
                log.warning("load_error", file=str(f), error=str(exc))


# ── Backwards-compatible aliases ─────────────────────────────────────────────
# (used by test_pattern_detector.py)

_hash = _hash_python
_complexity_fn = _complexity_python
_signature_fn = _signature