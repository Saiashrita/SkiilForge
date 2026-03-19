"""
tests/test_pattern_detector.py
Tests for AST-based pattern detection.
"""
import ast
import textwrap
from pathlib import Path
import pytest
import tempfile

from skillforge.core.pattern_detector import (
    PatternDetector,
    PatternCandidate,
    _structural_hash,
    _ASTNormalizer,
    _infer_abstract_signature,
)


# ─────────────────────────────────────────────────────────────────────────────
# Structural hash tests — the core invariant
# ─────────────────────────────────────────────────────────────────────────────

FUNC_A = textwrap.dedent("""
    def load_config(path: str) -> dict:
        with open(path, "r") as f:
            data = json.load(f)
        return data
""")

FUNC_B = textwrap.dedent("""
    def read_settings(filepath: str) -> dict:
        with open(filepath, "r") as handle:
            result = json.load(handle)
        return result
""")

FUNC_DIFFERENT = textwrap.dedent("""
    def compute_total(items: list) -> float:
        total = 0.0
        for item in items:
            total += item.price
        return total
""")


def _parse_first_fn(code: str) -> ast.FunctionDef:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise ValueError("No function found")


class TestStructuralHash:
    def test_same_structure_same_hash(self):
        """Two functions that do the same thing with different names → same hash."""
        hash_a = _structural_hash(_parse_first_fn(FUNC_A))
        hash_b = _structural_hash(_parse_first_fn(FUNC_B))
        assert hash_a == hash_b, "Structurally identical functions must hash identically"

    def test_different_structure_different_hash(self):
        hash_a = _structural_hash(_parse_first_fn(FUNC_A))
        hash_c = _structural_hash(_parse_first_fn(FUNC_DIFFERENT))
        assert hash_a != hash_c, "Structurally different functions must hash differently"

    def test_hash_is_deterministic(self):
        h1 = _structural_hash(_parse_first_fn(FUNC_A))
        h2 = _structural_hash(_parse_first_fn(FUNC_A))
        assert h1 == h2


# ─────────────────────────────────────────────────────────────────────────────
# PatternDetector integration tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPatternDetector:
    def test_no_promotion_below_threshold(self, tmp_path: Path):
        detector = PatternDetector(min_lines=3, min_frequency=3, min_complexity=1.0)
        f = tmp_path / "test.py"
        f.write_text(FUNC_A)
        results = detector.process_file(f, "ctx1")
        assert results == [], "Should not promote with only 1 occurrence"

    def test_promotes_at_threshold(self, tmp_path: Path):
        detector = PatternDetector(min_lines=3, min_frequency=2, min_complexity=1.0)
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text(FUNC_A)
        f2.write_text(FUNC_B)  # same structure, different names

        r1 = detector.process_file(f1, "file_a")
        assert r1 == [], "First occurrence: no promotion"

        r2 = detector.process_file(f2, "file_b")
        assert len(r2) == 1, "Should promote on second occurrence"
        assert r2[0].frequency == 2
        assert r2[0].language == "python"

    def test_no_duplicate_promotion(self, tmp_path: Path):
        detector = PatternDetector(min_lines=3, min_frequency=2, min_complexity=1.0)
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f3 = tmp_path / "c.py"
        f1.write_text(FUNC_A)
        f2.write_text(FUNC_B)
        f3.write_text(FUNC_A)

        detector.process_file(f1, "ctx1")
        r2 = detector.process_file(f2, "ctx2")
        r3 = detector.process_file(f3, "ctx3")

        assert len(r2) == 1, "Promoted on second occurrence"
        assert r3 == [], "Must NOT re-promote already-promoted pattern"

    def test_skips_non_python(self, tmp_path: Path):
        detector = PatternDetector(min_lines=1, min_frequency=1, min_complexity=0.0)
        f = tmp_path / "test.ts"
        f.write_text("function hello() { return 42; }")
        results = detector.process_file(f)
        assert results == [], "Should skip non-Python files"

    def test_ignores_tiny_functions(self, tmp_path: Path):
        detector = PatternDetector(min_lines=10, min_frequency=1, min_complexity=0.0)
        tiny = "def add(a, b):\n    return a + b\n"
        f = tmp_path / "tiny.py"
        f.write_text(tiny)
        results = detector.process_file(f)
        assert results == [], "Should skip functions below min_lines"

    def test_persists_to_disk(self, tmp_path: Path):
        candidates_dir = tmp_path / "candidates"
        candidates_dir.mkdir()
        detector = PatternDetector(
            min_lines=3, min_frequency=2,
            min_complexity=1.0, candidates_dir=candidates_dir
        )
        f1, f2 = tmp_path / "a.py", tmp_path / "b.py"
        f1.write_text(FUNC_A)
        f2.write_text(FUNC_B)
        detector.process_file(f1)
        detector.process_file(f2)

        json_files = list(candidates_dir.glob("*.json"))
        assert len(json_files) == 1, "Should persist candidate to disk"
        candidate = PatternCandidate.from_json(json_files[0].read_text())
        assert candidate.frequency == 2


# ─────────────────────────────────────────────────────────────────────────────
# Abstract signature tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAbstractSignature:
    def test_captures_call_chain(self):
        code = textwrap.dedent("""
            def fetch_data(url):
                response = requests.get(url)
                data = json.loads(response.text)
                return data
        """)
        node = _parse_first_fn(code)
        sig = _infer_abstract_signature(node)
        assert "get" in sig or "loads" in sig, f"Signature should contain call names, got: {sig}"
