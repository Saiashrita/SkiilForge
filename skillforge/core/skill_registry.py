"""
skillforge/core/skill_registry.py

Skill registry with TF-IDF semantic search and enhanced SKILL.md generation.
Produces Claude-quality skill index with dependency graphs and maturity badges.
"""
from __future__ import annotations

import json
import pickle
import time
from pathlib import Path
from typing import Optional

import numpy as np
import structlog
from sklearn.metrics.pairwise import cosine_similarity

from skillforge.config import settings
from skillforge.core.skill_crystallizer import CrystallizedSkill

log = structlog.get_logger(__name__)


class SkillRegistry:
    """
    Central skill registry with semantic search and SKILL.md generation.

    Stores crystallized skills, provides TF-IDF semantic search,
    and generates a Claude-quality SKILL.md index.
    """

    def __init__(self, skills_dir: Path, index_path: Path) -> None:
        self.skills_dir = skills_dir
        self.index_path = index_path
        self._embeddings_path = skills_dir.parent / "embeddings.pkl"
        self._skills: dict[str, CrystallizedSkill] = {}
        self._embeddings: dict[str, np.ndarray] = {}
        self._tfidf_vectorizer: Optional[object] = None
        self._load_all()

    # ── Public API ────────────────────────────────────────────────────────────

    def register(self, skill: CrystallizedSkill) -> None:
        """Register a new skill and update the index."""
        self._skills[skill.skill_name] = skill
        self._rebuild_embeddings()
        self.rebuild_index()
        if settings.auto_cursor_rules:
            self._write_cursor_rules()
        log.info("skill_registered", name=skill.skill_name, total=len(self._skills))

    def search(self, query: str, top_k: int = 5) -> list:
        """
        Semantically search skills using TF-IDF embeddings.

        Falls back to keyword search if embedding fails.

        Args:
            query: Natural language description of what you want.
            top_k: Number of results to return.

        Returns:
            List of (CrystallizedSkill, score) tuples sorted by relevance.
        """
        if not self._skills:
            return []

        # Try TF-IDF first
        if self._tfidf_vectorizer is not None and self._embeddings:
            try:
                return self._tfidf_search(query, top_k)
            except Exception as exc:
                log.debug("tfidf_search_fallback", error=str(exc))

        # Fallback to keyword search
        return self._keyword_search(query, top_k)

    def _tfidf_search(self, query: str, top_k: int) -> list:
        """Search using TF-IDF embeddings."""
        from sklearn.feature_extraction.text import TfidfVectorizer

        query_vec = self._embed_text(query).reshape(1, -1)
        results = []
        for name, vec in self._embeddings.items():
            score = float(cosine_similarity(query_vec, vec.reshape(1, -1))[0][0])
            results.append((name, score))
        results.sort(key=lambda x: x[1], reverse=True)
        return [
            (self._skills[name], score)
            for name, score in results[:top_k]
            if name in self._skills
        ]

    def _keyword_search(self, query: str, top_k: int) -> list:
        """Fallback keyword-based search."""
        query_lower = query.lower()
        results = []
        for skill in self._skills.values():
            score = 0.0
            if query_lower in skill.skill_name.lower():
                score += 0.8
            if query_lower in skill.description.lower():
                score += 0.5
            for tag in skill.tags:
                if query_lower in tag.lower():
                    score += 0.3
            # Match against code content
            if query_lower in skill.code.lower():
                score += 0.2
            if score > 0:
                results.append((skill, score))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def get(self, skill_name: str) -> Optional[CrystallizedSkill]:
        return self._skills.get(skill_name)

    def all_skills(self) -> list:
        return list(self._skills.values())

    def skill_count(self) -> int:
        return len(self._skills)

    # ── TF-IDF Embeddings ────────────────────────────────────────────────────

    def _rebuild_embeddings(self) -> None:
        """Rebuild TF-IDF embeddings for all skills."""
        if len(self._skills) < 1:
            return

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer

            # Build corpus: skill description + tags + code tokens
            corpus = {}
            for name, skill in self._skills.items():
                text = (
                    f"{skill.skill_name} {skill.description} "
                    f"{' '.join(skill.tags)} "
                    f"{skill.category} {skill.language} "
                    f"{' '.join(skill.calls if hasattr(skill, 'calls') else [])}"
                )
                corpus[name] = text

            if len(corpus) < 2:
                # TF-IDF needs at least 2 documents; use hash embedding fallback
                for name, text in corpus.items():
                    self._embeddings[name] = self._hash_embed(text)
                self._tfidf_vectorizer = None
            else:
                self._tfidf_vectorizer = TfidfVectorizer(
                    max_features=512,
                    stop_words="english",
                    ngram_range=(1, 2),
                )
                names = list(corpus.keys())
                texts = [corpus[n] for n in names]
                matrix = self._tfidf_vectorizer.fit_transform(texts)
                for i, name in enumerate(names):
                    self._embeddings[name] = matrix[i].toarray().flatten()

            self._persist_embeddings()
            log.debug("embeddings_rebuilt", count=len(self._embeddings))

        except Exception as exc:
            log.warning("embedding_rebuild_failed", error=str(exc))

    def _embed_text(self, text: str) -> np.ndarray:
        """Embed a query string using the fitted TF-IDF vectorizer."""
        if self._tfidf_vectorizer is not None:
            vec = self._tfidf_vectorizer.transform([text])
            return vec.toarray().flatten()
        return self._hash_embed(text)

    @staticmethod
    def _hash_embed(text: str) -> np.ndarray:
        """Fallback embedding using word hashing (no API needed)."""
        import hashlib

        words = text.lower().split()
        vec = np.zeros(256, dtype=np.float32)
        for word in words:
            idx = int(hashlib.md5(word.encode()).hexdigest(), 16) % 256
            vec[idx] += 1.0
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    # ── Enhanced SKILL.md ────────────────────────────────────────────────────

    def rebuild_index(self) -> None:
        """Generate an enhanced SKILL.md with dependency graphs and maturity badges."""
        _EMOJI = {
            "io": "💾", "parsing": "🔍", "api_client": "🌐",
            "data_transform": "🔄", "validation": "✅", "concurrency": "⚡",
            "caching": "🗄️", "string_utils": "📝", "filesystem": "📁",
            "networking": "🔌", "auth": "🔐", "testing": "🧪",
            "logging": "📋", "composed": "🧩",
        }
        _LANG_BADGE = {
            "python": "🐍", "javascript": "📜", "jsx": "⚛️",
            "typescript": "📘", "tsx": "⚛️",
        }

        lines = [
            "---",
            "name: skillforge-library",
            "description: Auto-generated skill library from your code patterns",
            "---",
            "",
            "# 🧠 SkillForge — Auto-Generated Skill Library",
            "",
            f"> Last updated: {time.strftime('%Y-%m-%d %H:%M:%S')}  ",
            f"> Total skills: **{len(self._skills)}**",
            "",
        ]

        if not self._skills:
            lines.append("_No skills crystallized yet. Keep coding — SkillForge is watching!_")
            self.index_path.write_text("\n".join(lines), encoding="utf-8")
            return

        # Category summary table
        by_cat: dict[str, list[CrystallizedSkill]] = {}
        for skill in self._skills.values():
            by_cat.setdefault(skill.category, []).append(skill)

        lines.append("## 📊 Overview")
        lines.append("")
        lines.append("| Category | Count | Languages |")
        lines.append("|---|---|---|")
        for cat, skills in sorted(by_cat.items()):
            emoji = _EMOJI.get(cat, "🔧")
            langs = set(getattr(s, "language", "python") for s in skills)
            lang_badges = " ".join(
                _LANG_BADGE.get(l, "")
                for l in sorted(langs)
            )
            lines.append(f"| {emoji} {cat.replace('_', ' ').title()} | {len(skills)} | {lang_badges} |")
        lines.append("")

        # Dependency graph (Mermaid) — for composed skills
        composed_skills = [s for s in self._skills.values() if s.composes_with]
        if composed_skills:
            lines.append("## 🧩 Dependency Graph")
            lines.append("")
            lines.append("```mermaid")
            lines.append("graph LR")
            for skill in composed_skills:
                for dep in skill.composes_with:
                    lines.append(f"    {dep} --> {skill.skill_name}")
            lines.append("```")
            lines.append("")

        # Skills by category
        lines.append("---")
        lines.append("")

        for cat, skills in sorted(by_cat.items()):
            emoji = _EMOJI.get(cat, "🔧")
            lines.append(f"## {emoji} {cat.replace('_', ' ').title()}")
            lines.append("")

            for skill in sorted(skills, key=lambda s: s.skill_name):
                maturity = self._maturity_badge(skill.frequency)
                lang = getattr(skill, "language", "python")
                lang_badge = _LANG_BADGE.get(lang, "")
                code_lang = lang if lang != "jsx" else "jsx"

                lines.append(f"### `{skill.skill_name}` {lang_badge} {maturity}")
                lines.append(f"**{skill.description}**")
                lines.append("")
                # Link to the skill's own SKILL.md
                folder_name = skill.skill_name.replace("_", "-")
                lines.append(f"See: [`{folder_name}/SKILL.md`]({folder_name}/SKILL.md)")
                lines.append("")
                lines.append(f"- **Complexity**: {skill.complexity}")
                lines.append(f"- **Observed**: {skill.frequency}×")
                lines.append(f"- **Tags**: {', '.join(f'`{t}`' for t in skill.tags)}")

                if skill.composes_with:
                    lines.append(
                        f"- **Composes with**: "
                        f"{', '.join(f'`{c}`' for c in skill.composes_with)}"
                    )

                # Parameter table
                if skill.parameters:
                    lines.append("")
                    lines.append("| Parameter | Type | Default | Description |")
                    lines.append("|---|---|---|---|")
                    for p in skill.parameters:
                        name = p.get("name", "")
                        ptype = p.get("type", "any")
                        default = p.get("default", "required")
                        desc = p.get("description", "")
                        lines.append(f"| `{name}` | `{ptype}` | {default} | {desc} |")

                lines.append("")
                lines.append(f"```{code_lang}")
                lines.append(skill.usage_example)
                lines.append("```")
                lines.append("")

                # Related skills
                related = self._find_related(skill)
                if related:
                    lines.append(
                        f"> **Related**: "
                        f"{', '.join(f'`{r}`' for r in related[:3])}"
                    )
                    lines.append("")

                lines.append("---")
                lines.append("")

        self.index_path.write_text("\n".join(lines), encoding="utf-8")
        log.debug("skill_index_rebuilt", path=str(self.index_path))

    @staticmethod
    def _maturity_badge(frequency: int) -> str:
        """Return a maturity badge based on observation frequency."""
        if frequency >= 10:
            return "🌳"  # mature
        elif frequency >= 5:
            return "🌿"  # established
        return "🌱"  # emerging

    def _find_related(self, skill: CrystallizedSkill) -> list[str]:
        """Find related skills based on tag overlap."""
        if not skill.tags:
            return []

        related: list[tuple[str, int]] = []
        skill_tags = set(skill.tags)

        for other in self._skills.values():
            if other.skill_name == skill.skill_name:
                continue
            overlap = len(skill_tags & set(other.tags))
            if overlap > 0:
                related.append((other.skill_name, overlap))

        related.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in related[:3]]

    def _write_cursor_rules(self) -> None:
        """Update .cursorrules with available skills."""
        rules_path = Path.cwd() / ".cursorrules"
        lang_badge = {"python": "🐍", "javascript": "📜", "jsx": "⚛️", "typescript": "📘", "tsx": "⚛️"}

        skill_lines = [
            f"  - `{s.skill_name.replace('_', '-')}` — {s.description} "
            f"[{s.category}] {lang_badge.get(getattr(s, 'language', 'python'), '')} "
            f"→ see ~/.skillforge/skills/{s.skill_name.replace('_', '-')}/SKILL.md"
            for s in sorted(self._skills.values(), key=lambda s: s.skill_name)
        ]
        rules = "\n".join([
            "# SkillForge — Available reusable skills from YOUR code patterns",
            "# Suggest these before writing new utility code.\n",
            "## Available Skills",
            *skill_lines,
            "\n## Import Pattern",
            "```python",
            "# Copy from ~/.skillforge/skills/<skill-name>/scripts/<skill_name>.py",
            "```",
        ])
        try:
            rules_path.write_text(rules, encoding="utf-8")
        except OSError as exc:
            log.warning("cursorrules_write_error", error=str(exc))

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        """Load all skills from Claude Skills folder structure."""
        # New format: skills/skill-name/meta.json
        for meta_file in self.skills_dir.glob("*/meta.json"):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                skill = _skill_from_dict(data)
                self._skills[skill.skill_name] = skill
            except Exception as exc:
                log.warning("load_skill_error", file=str(meta_file), error=str(exc))

        # Backwards compat: old flat *.meta.json files
        for meta_file in self.skills_dir.glob("*.meta.json"):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                skill = _skill_from_dict(data)
                if skill.skill_name not in self._skills:
                    self._skills[skill.skill_name] = skill
            except Exception as exc:
                log.warning("load_skill_error_legacy", file=str(meta_file), error=str(exc))

        if self._embeddings_path.exists():
            try:
                with open(self._embeddings_path, "rb") as f:
                    self._embeddings = pickle.load(f)
            except Exception:
                self._embeddings = {}

        # Rebuild embeddings if we have skills but no embeddings
        if self._skills and not self._embeddings:
            self._rebuild_embeddings()

        log.info("registry_loaded", skills=len(self._skills))

    def _persist_embeddings(self) -> None:
        try:
            with open(self._embeddings_path, "wb") as f:
                pickle.dump(self._embeddings, f)
        except Exception as exc:
            log.warning("embedding_persist_error", error=str(exc))


def _skill_from_dict(data: dict) -> CrystallizedSkill:
    """Reconstruct a CrystallizedSkill from persisted metadata."""
    from skillforge.core.pattern_detector import PatternCandidate

    candidate = PatternCandidate(
        structural_hash=data.get("pattern_hash", ""),
        source_code="",
        abstract_signature="",
        language=data.get("language", "python"),
        frequency=data.get("frequency", 0),
        first_seen_file=(data.get("contexts") or [""])[0],
        contexts=data.get("contexts", []),
        complexity_score=3.0,
        node_type=data.get("node_type", "function"),
    )
    return CrystallizedSkill(data, candidate)