"""
skillforge/core/skill_crystallizer.py

LLM-powered skill crystallization engine.
Transforms detected patterns into production-grade, Claude-quality skills.

Supports three backends:
  - ollama  → free, local, no API key needed (default)
  - claude  → Anthropic API (best quality)
  - gemini  → Google Gemini API

Set in .env:
  SKILLFORGE_LLM_BACKEND=ollama
  SKILLFORGE_OLLAMA_MODEL=phi3
"""
from __future__ import annotations

import ast
import json
import re
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from skillforge.config import settings
from skillforge.core.pattern_detector import PatternCandidate

log = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Language-Specific Prompts — designed to produce Claude-quality output
# ─────────────────────────────────────────────────────────────────────────────

_PYTHON_PROMPT = """\
You are a senior Python engineer (top 0.1%). Extract a production-grade, reusable skill \
function from this repeated code pattern.

This code was observed {frequency} times across: {contexts}
Pattern type: {node_type}

RAW PATTERN:
```python
{source_code}
```

Requirements:
1. Generalize it — parametrize every hardcoded value
2. Add complete type hints (use modern Python 3.11+ syntax: X | None instead of Optional[X])
3. Write a comprehensive Google-style docstring with:
   - One-line summary
   - Detailed description if complex
   - Args: with types and descriptions
   - Returns: with type and description
   - Raises: with exception types and when they occur
4. Use specific exception types (never bare except)
5. Name with snake_case verb_noun (e.g., load_json_safely, retry_with_backoff)
6. Include proper imports at the top
7. Add input validation where appropriate
8. The code must be COMPLETE and RUNNABLE — do not truncate

Respond with ONLY a valid JSON object. No markdown fences. No text before or after.

{{
  "skill_name": "verb_noun_style_name",
  "category": "one of: io, parsing, api_client, data_transform, validation, concurrency, caching, string_utils, filesystem, networking, auth, testing, logging",
  "description": "Clear one-line description of what this skill does",
  "tags": ["tag1", "tag2", "tag3"],
  "code": "COMPLETE Python code with imports, function def, docstring, and full body",
  "usage_example": "result = skill_name(arg1, arg2)",
  "complexity": "simple | moderate | complex",
  "composes_with": ["other_skill_names"],
  "parameters": [
    {{"name": "param_name", "type": "str", "default": "None", "description": "What this param does"}}
  ]
}}
"""

_JS_PROMPT = """\
You are a senior JavaScript/TypeScript engineer (top 0.1%). Extract a production-grade, \
reusable skill from this repeated code pattern.

This code was observed {frequency} times across: {contexts}
Pattern type: {node_type} | Language: {language}

RAW PATTERN:
```{language}
{source_code}
```

Requirements:
1. Generalize it — parametrize every hardcoded value
2. Add proper JSDoc documentation with @param, @returns, @throws, @example
3. Use modern ES2022+ syntax (optional chaining, nullish coalescing, etc.)
4. For React patterns: preserve hooks, proper prop types, and component structure
5. Name with camelCase for functions, PascalCase for components
6. Include proper imports
7. Add input validation where appropriate
8. The code must be COMPLETE and RUNNABLE — do not truncate
9. If TypeScript: include proper type definitions

Respond with ONLY a valid JSON object. No markdown fences. No text before or after.

{{
  "skill_name": "descriptive_snake_case_name",
  "category": "one of: io, parsing, api_client, data_transform, validation, concurrency, caching, string_utils, filesystem, networking, auth, testing, logging",
  "description": "Clear one-line description of what this skill does",
  "tags": ["tag1", "tag2", "tag3"],
  "code": "COMPLETE code with imports, function/component, docs, and full body",
  "usage_example": "const result = functionName(arg1, arg2);",
  "complexity": "simple | moderate | complex",
  "composes_with": ["other_skill_names"],
  "parameters": [
    {{"name": "paramName", "type": "string", "default": "null", "description": "What this param does"}}
  ]
}}
"""

_COMPOSITION_PROMPT = """\
You are a senior software architect (top 0.1%). These functions always appear together \
and form a workflow/pipeline pattern. Create a SINGLE higher-level composed skill that \
orchestrates them.

Observed {frequency} times across: {contexts}

COMPONENT FUNCTIONS:
```{language}
{source_code}
```

Requirements:
1. Create one top-level function that orchestrates the pipeline
2. The composed function should call the component functions in order
3. Add comprehensive documentation explaining the workflow
4. Handle errors at each stage with informative messages
5. The code must be COMPLETE — include ALL component functions + the orchestrator

Respond with ONLY a valid JSON object. Same format as above, with:
- category: "composed"
- composes_with: ["list", "of", "component", "function", "names"]
"""


# ─────────────────────────────────────────────────────────────────────────────
# CrystallizedSkill data class
# ─────────────────────────────────────────────────────────────────────────────


class CrystallizedSkill:
    """A skill that has been crystallized from a detected pattern."""

    def __init__(self, data: dict, candidate: PatternCandidate) -> None:
        self.skill_name: str = data["skill_name"]
        self.category: str = data.get("category", "io")
        self.description: str = data.get("description", "")
        self.tags: list = data.get("tags", [])
        self.code: str = self._unescape_code(data.get("code", ""))
        self.usage_example: str = data.get("usage_example", "")
        self.complexity: str = data.get("complexity", "simple")
        self.composes_with: list = data.get("composes_with", [])
        self.parameters: list = data.get("parameters", [])
        self.pattern_hash: str = candidate.structural_hash
        self.frequency: int = candidate.frequency
        self.contexts: list = candidate.contexts
        self.language: str = candidate.language
        self.node_type: str = getattr(candidate, "node_type", "function")
        self.crystallized_at: float = time.time()

    @staticmethod
    def _unescape_code(code: str) -> str:
        """Fix LLM output that has literal \\n instead of real newlines."""
        if "\\n" in code and "\n" not in code:
            code = code.replace("\\n", "\n")
        if "\\t" in code and "\t" not in code:
            code = code.replace("\\t", "\t")
        # Fix double-escaped quotes
        code = code.replace('\\"', '"')
        code = code.replace("\\'", "'")
        return code

    def to_python_file(self) -> str:
        """Generate the .py skill file content."""
        lang_badge = "🐍" if self.language == "python" else "⚛️"
        header = (
            f"# AUTO-CRYSTALLIZED BY SKILLFORGE {lang_badge}\n"
            f"# Pattern hash : {self.pattern_hash}\n"
            f"# Observed     : {self.frequency}x\n"
            f"# Category     : {self.category}\n"
            f"# Tags         : {', '.join(self.tags)}\n"
            f"# Usage        : {self.usage_example}\n\n"
        )
        return header + self.code + "\n"

    def to_skill_md(self) -> str:
        """Generate a Claude-compliant SKILL.md with proper YAML frontmatter."""
        lang_map = {
            "python": "python", "javascript": "javascript",
            "typescript": "typescript", "jsx": "jsx", "tsx": "tsx",
        }
        code_lang = lang_map.get(self.language, "python")

        # Build trigger phrases from skill name and tags
        trigger_phrases = []
        name_words = self.skill_name.replace("_", " ")
        trigger_phrases.append(f'"{ name_words }"')
        for tag in self.tags[:3]:
            trigger_phrases.append(f'"{ tag }"')

        # YAML frontmatter — exactly as Anthropic spec requires
        lines = [
            "---",
            f"name: {self.skill_name.replace('_', '-')}",
            f"description: {self.description} Use when user asks to {', '.join(trigger_phrases)} or needs {self.category.replace('_', ' ')} utilities. Observed {self.frequency}x in real codebases.",
            f"license: MIT",
            "metadata:",
            f"  category: {self.category}",
            f"  complexity: {self.complexity}",
            f"  language: {self.language}",
            f"  tags: [{', '.join(self.tags)}]",
            f"  observed: {self.frequency}",
            "  author: SkillForge",
            "  version: 1.0.0",
            "---",
            "",
            f"# {self.skill_name.replace('_', ' ').title()}",
            "",
            f"**{self.description}**",
            "",
            "## When to Use",
            "",
            f"Use this skill when you need to {self.description.lower()} ",
            f"Applicable in any {self.language} project requiring {self.category.replace('_', ' ')}.",
            "",
            "## Usage",
            "",
            f"```{code_lang}",
            self.usage_example,
            "```",
            "",
            "## Implementation",
            "",
            f"Copy from `scripts/{self.skill_name}.{self._ext()}` or import directly:",
            "",
            f"```{code_lang}",
            self.code,
            "```",
        ]

        # Parameter table
        if self.parameters:
            lines += [
                "",
                "## Parameters",
                "",
                "| Parameter | Type | Default | Description |",
                "|---|---|---|---|",
            ]
            for p in self.parameters:
                lines.append(
                    f"| `{p.get('name','')}` | `{p.get('type','any')}` "
                    f"| {p.get('default','required')} | {p.get('description','')} |"
                )

        if self.composes_with:
            lines += [
                "",
                "## Composes With",
                "",
                f"{', '.join(f'`{c}`' for c in self.composes_with)}",
            ]

        lines += [
            "",
            "## Examples",
            "",
            "### Basic usage",
            "",
            f"```{code_lang}",
            self.usage_example,
            "```",
            "",
            "## Troubleshooting",
            "",
            "**Skill produces unexpected results**",
            "- Verify all required parameters are provided",
            "- Check input types match expected types",
            "- Review the parameter table above",
            "",
            f"**Import errors**",
            f"- Copy `scripts/{self.skill_name}.{self._ext()}` to your project",
            "- Or paste the implementation directly",
        ]

        return "\n".join(lines)

    def _ext(self) -> str:
        """Return the file extension for this skill's language."""
        return {
            "python": "py", "javascript": "js", "typescript": "ts",
            "jsx": "jsx", "tsx": "tsx",
        }.get(self.language, "py")

    def to_dict(self) -> dict:
        return {
            "skill_name": self.skill_name,
            "category": self.category,
            "description": self.description,
            "tags": self.tags,
            "code": self.code,
            "usage_example": self.usage_example,
            "complexity": self.complexity,
            "composes_with": self.composes_with,
            "parameters": self.parameters,
            "pattern_hash": self.pattern_hash,
            "frequency": self.frequency,
            "contexts": self.contexts,
            "language": self.language,
            "node_type": self.node_type,
            "crystallized_at": self.crystallized_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# SkillCrystallizer
# ─────────────────────────────────────────────────────────────────────────────


class SkillCrystallizer:
    """LLM-powered engine that transforms pattern candidates into production skills."""

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.backend = settings.llm_backend.lower()
        log.info(
            "crystallizer_ready",
            backend=self.backend,
            model=self._active_model(),
        )

    def _active_model(self) -> str:
        if self.backend == "ollama":
            return settings.ollama_model
        elif self.backend == "claude":
            return settings.claude_model
        return settings.gemini_model

    # ── LLM Backends ─────────────────────────────────────────────────────────

    def _call_ollama(self, prompt: str) -> str:
        url = f"{settings.ollama_base_url}/api/generate"
        payload = json.dumps({
            "model": settings.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 4096},
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("response", "")
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama at {settings.ollama_base_url}.\n"
                f"Make sure Ollama is running: open a terminal and run `ollama serve`\n"
                f"Error: {exc}"
            )

    def _call_claude(self, prompt: str) -> str:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("Run: pip install anthropic")

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        msg = client.messages.create(
            model=settings.claude_model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    def _call_gemini(self, prompt: str) -> str:
        try:
            import google.genai as genai
        except ImportError:
            raise RuntimeError("Run: pip install google-genai")

        client = genai.Client(api_key=settings.gemini_api_key)
        return client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
        ).text

    def _call_llm(self, prompt: str) -> str:
        if self.backend == "ollama":
            return self._call_ollama(prompt)
        elif self.backend == "claude":
            return self._call_claude(prompt)
        elif self.backend == "gemini":
            return self._call_gemini(prompt)
        else:
            raise ValueError(
                f"Unknown backend '{self.backend}'. "
                f"Set SKILLFORGE_LLM_BACKEND to: ollama, claude, or gemini"
            )

    # ── Main Crystallization ─────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=15),
        reraise=True,
    )
    async def crystallize(
        self, candidate: PatternCandidate
    ) -> Optional[CrystallizedSkill]:
        """
        Crystallize a pattern candidate into a production-quality skill.

        Args:
            candidate: The PatternCandidate to crystallize.

        Returns:
            A CrystallizedSkill if successful, None otherwise.
        """
        # Choose the right prompt based on language and type
        prompt = self._build_prompt(candidate)

        log.info(
            "crystallizing",
            hash=candidate.structural_hash[:8],
            backend=self.backend,
            language=candidate.language,
            node_type=candidate.node_type,
        )

        try:
            raw = self._call_llm(prompt)
        except Exception as exc:
            log.error("llm_call_failed", error=str(exc))
            return None

        log.debug("llm_raw_response", preview=raw[:300])

        data = self._parse_json(raw)
        if not data:
            log.warning("parse_failed", raw_preview=raw[:400])
            return None

        # Normalize nested LLM responses (e.g. orchestrator_function wrapper)
        data = self._normalize_response(data)

        if not data.get("skill_name") or not data.get("code"):
            log.warning("missing_required_fields", keys=list(data.keys()))
            return None

        skill = CrystallizedSkill(data, candidate)

        # Validate the generated code is syntactically correct
        if not self._validate_code(skill):
            log.warning("code_validation_failed", skill=skill.skill_name)
            # Still save it but log the warning — partial skills are better than none

        # Write skill files
        self._save_skill(skill)

        log.info(
            "crystallized",
            skill=skill.skill_name,
            category=skill.category,
            language=skill.language,
            folder=str(self.skills_dir / skill.skill_name.replace("_", "-")),
        )
        return skill

    async def find_compositions(
        self, *args, **kwargs
    ) -> Optional[CrystallizedSkill]:
        """
        Crystallize a composed pattern into a higher-level skill.

        This is called by the agent when the CompositionDetector finds
        a pattern group (pipeline or co-occurrence).
        """
        from skillforge.core.composition_detector import ComposedPattern

        if args and isinstance(args[0], ComposedPattern):
            composed = args[0]
            # Build a pseudo-candidate from the composed pattern
            candidate = PatternCandidate(
                structural_hash=f"composed_{hash(tuple(composed.component_names)) & 0xFFFFFFFF:08x}",
                source_code=composed.combined_source,
                abstract_signature=" → ".join(composed.component_names[:4]),
                language=composed.language,
                frequency=composed.frequency,
                first_seen_file=composed.contexts[0] if composed.contexts else "",
                contexts=composed.contexts,
                complexity_score=5.0,
                node_type="composed",
                decorators=[],
                calls=composed.component_names,
            )
            return await self.crystallize(candidate)
        return None

    def _build_prompt(self, candidate: PatternCandidate) -> str:
        """Select and fill the right prompt template based on language/type."""
        template_kwargs = {
            "frequency": candidate.frequency,
            "contexts": "; ".join(candidate.contexts[:3]),
            "source_code": candidate.source_code[:3000],
            "node_type": candidate.node_type,
            "language": candidate.language,
        }

        if candidate.node_type == "composed":
            return _COMPOSITION_PROMPT.format(**template_kwargs)
        elif candidate.language == "python":
            return _PYTHON_PROMPT.format(**template_kwargs)
        else:
            return _JS_PROMPT.format(**template_kwargs)

    def _validate_code(self, skill: CrystallizedSkill) -> bool:
        """Validate that generated code is syntactically correct."""
        if skill.language == "python":
            try:
                ast.parse(skill.code)
                return True
            except SyntaxError as exc:
                log.debug("python_syntax_error", skill=skill.skill_name, error=str(exc))
                return False
        # For JS/TS, do a basic structure check
        code = skill.code
        open_braces = code.count("{") + code.count("(") + code.count("[")
        close_braces = code.count("}") + code.count(")") + code.count("]")
        return abs(open_braces - close_braces) <= 1

    def _save_skill(self, skill: CrystallizedSkill) -> None:
        """Save skill as proper Claude Skills folder structure."""
        # kebab-case folder name — required by Claude Skills spec
        folder_name = skill.skill_name.replace("_", "-")
        skill_dir = self.skills_dir / folder_name
        skill_dir.mkdir(parents=True, exist_ok=True)

        # 1. SKILL.md — required, must be exactly this name
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(skill.to_skill_md(), encoding="utf-8")

        # 2. scripts/ folder — actual executable code
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        ext = skill._ext()
        code_file = scripts_dir / f"{skill.skill_name}.{ext}"
        code_file.write_text(skill.code, encoding="utf-8")

        # 3. references/ folder — examples and docs
        refs_dir = skill_dir / "references"
        refs_dir.mkdir(exist_ok=True)
        examples_md = refs_dir / "examples.md"
        examples_md.write_text(
            f"# {skill.skill_name} — Examples\n\n"
            f"## Basic Usage\n\n"
            f"```{skill.language}\n{skill.usage_example}\n```\n\n"
            f"## Observed in\n\n"
            + "\n".join(f"- `{ctx}`" for ctx in skill.contexts[:5]),
            encoding="utf-8",
        )

        # 4. meta.json — internal SkillForge tracking (NOT part of Claude Skills spec)
        meta_file = skill_dir / "meta.json"
        meta_file.write_text(
            json.dumps(skill.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        log.info(
            "skill_saved",
            folder=folder_name,
            skill_md=str(skill_md),
            script=str(code_file),
        )

    @staticmethod
    def _normalize_response(data: dict) -> dict:
        """
        Normalize quirky LLM responses into the expected flat structure.

        Handles common Ollama/local-LLM issues:
        - Nested wrappers: orchestrator_function, function, skill, result
        - 'name' used instead of 'skill_name'
        - Fields buried one level deep instead of top-level
        """
        if not isinstance(data, dict):
            return data

        # Known wrapper keys that local LLMs invent
        _WRAPPER_KEYS = (
            "orchestrator_function", "function", "skill",
            "result", "output", "generated_skill",
        )

        for wrapper_key in _WRAPPER_KEYS:
            nested = data.get(wrapper_key)
            if isinstance(nested, dict):
                # Promote nested fields to top level (don't overwrite existing)
                for k, v in nested.items():
                    if k not in data or data[k] in ("", None, []):
                        data[k] = v
                # Remove the wrapper to keep things clean
                del data[wrapper_key]
                log.debug("normalized_nested_wrapper", key=wrapper_key)

        # Alias: "name" → "skill_name" (LLMs often use just "name")
        if not data.get("skill_name") and data.get("name"):
            data["skill_name"] = data.pop("name")

        return data

    # ── JSON parser ───────────────────────────────────────────────────────────

    _VALID_CATEGORIES = {
        "io", "parsing", "api_client", "data_transform", "validation",
        "concurrency", "caching", "string_utils", "filesystem", "networking",
        "auth", "testing", "logging", "composed",
    }

    def _parse_json(self, raw: str) -> Optional[dict]:
        """
        Robust JSON parser for LLM output.

        Handles common issues from local LLMs (Ollama/phi3):
        - Markdown fences (```json ... ```)
        - Backtick-quoted strings (``"code": `...` ``)
        - Raw newlines/tabs inside JSON string values
        - Trailing commas
        - Invalid category names
        - Falls back to regex field extraction as last resort
        """
        # Step 1: Strip markdown fences
        raw = re.sub(r"```(?:json)?\s*\n?", "", raw.strip())
        raw = raw.strip()

        # Step 2: Convert backtick-quoted values to double-quoted
        # Ollama uses backticks when code contains single quotes
        raw = self._convert_backtick_strings(raw)

        # Step 3: Extract outermost JSON object
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1:
            return None
        raw = raw[start : end + 1]

        # Strategy 1: Direct parse (best case — clean JSON)
        try:
            result = json.loads(raw)
            return self._fix_category(result)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Escape control characters inside JSON strings
        fixed = self._escape_string_contents(raw)
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
        try:
            result = json.loads(fixed)
            return self._fix_category(result)
        except json.JSONDecodeError:
            pass

        # Strategy 3: Aggressive — escape ALL newlines between quotes
        aggressive = self._aggressive_newline_escape(raw)
        aggressive = re.sub(r",\s*([}\]])", r"\1", aggressive)
        try:
            result = json.loads(aggressive)
            return self._fix_category(result)
        except json.JSONDecodeError:
            pass

        # Strategy 4: Regex field extraction (last resort)
        log.debug("json_parse_fallback_to_regex")
        result = self._extract_fields_regex(raw)
        if result and result.get("skill_name") and result.get("code"):
            return self._fix_category(result)

        log.warning("json_parse_error", raw_preview=raw[:200])
        return None

    @staticmethod
    def _convert_backtick_strings(raw: str) -> str:
        """
        Convert backtick-quoted values to double-quoted JSON strings.

        Ollama uses: "code": `import React from 'react'`
        We need:     "code": "import React from 'react'"

        Handles multi-line backtick strings, escaping inner double-quotes
        and newlines for valid JSON.
        """
        def _backtick_to_str(m: re.Match) -> str:
            content = m.group(1)
            # Escape double-quotes inside
            content = content.replace("\\", "\\\\")
            content = content.replace('"', '\\"')
            # Escape newlines/tabs for JSON
            content = content.replace("\n", "\\n")
            content = content.replace("\r", "\\r")
            content = content.replace("\t", "\\t")
            return f'"{content}"'

        # Match: key: `...backtick content...`  (possibly multi-line)
        return re.sub(r"`((?:[^`\\]|\\.)*)`", _backtick_to_str, raw, flags=re.DOTALL)

    def _fix_category(self, data: dict) -> dict:
        """Validate and fix the category field to match allowed values."""
        if not isinstance(data, dict):
            return data
        cat = data.get("category", "io")
        if cat not in self._VALID_CATEGORIES:
            # Try to find the closest match
            cat_lower = cat.lower().replace("/", "_").replace("-", "_").replace(" ", "_")
            # Check if any valid category is a substring
            for valid in self._VALID_CATEGORIES:
                if valid in cat_lower or cat_lower in valid:
                    data["category"] = valid
                    return data
            # Map common LLM inventions
            _CATEGORY_MAP = {
                "ui_components": "data_transform",
                "ui": "data_transform",
                "components": "data_transform",
                "react": "data_transform",
                "frontend": "data_transform",
                "database": "io",
                "http": "api_client",
                "web": "api_client",
                "utils": "string_utils",
                "utility": "string_utils",
                "error_handling": "validation",
                "security": "auth",
            }
            data["category"] = _CATEGORY_MAP.get(cat_lower, "io")
        return data

    @staticmethod
    def _escape_string_contents(raw: str) -> str:
        """Escape newlines/tabs/backslashes inside JSON string values."""
        result = []
        in_string = False
        escape_next = False
        i = 0
        while i < len(raw):
            ch = raw[i]
            if escape_next:
                result.append(ch)
                escape_next = False
                i += 1
                continue
            if ch == "\\" and in_string:
                if i + 1 < len(raw) and raw[i + 1] in '"\\bfnrtu/':
                    result.append(ch)
                else:
                    result.append("\\\\")
                i += 1
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                result.append(ch)
                i += 1
                continue
            if in_string:
                if ch == "\n":
                    result.append("\\n")
                elif ch == "\r":
                    result.append("\\r")
                elif ch == "\t":
                    result.append("\\t")
                else:
                    result.append(ch)
            else:
                result.append(ch)
            i += 1
        return "".join(result)

    @staticmethod
    def _aggressive_newline_escape(raw: str) -> str:
        """Replace all literal newlines between double-quotes with \\n."""
        def _escape_match(m: re.Match) -> str:
            content = m.group(0)
            content = content.replace("\n", "\\n")
            content = content.replace("\r", "\\r")
            content = content.replace("\t", "\\t")
            return content

        return re.sub(r'"(?:[^"\\]|\\.)*"', _escape_match, raw, flags=re.DOTALL)

    @staticmethod
    def _extract_fields_regex(raw: str) -> Optional[dict]:
        """
        Last-resort extraction: pull key fields via regex patterns.
        Handles both double-quoted and backtick-quoted values.
        """
        def _extract(key: str, fallback: str = "") -> str:
            # Try double-quoted first
            pattern = rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"'
            m = re.search(pattern, raw, re.DOTALL)
            if m:
                val = m.group(1)
                val = val.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
                return val
            # Try backtick-quoted
            pattern_bt = rf'"{key}"\s*:\s*`((?:[^`\\]|\\.)*)`'
            m = re.search(pattern_bt, raw, re.DOTALL)
            if m:
                return m.group(1)
            return fallback

        def _extract_list(key: str) -> list:
            pattern = rf'"{key}"\s*:\s*\[(.*?)\]'
            m = re.search(pattern, raw, re.DOTALL)
            if m:
                items = re.findall(r'"([^"]*)"', m.group(1))
                return items
            return []

        skill_name = _extract("skill_name")
        if not skill_name:
            return None

        # For code: try double-quoted, then backtick-quoted
        code = ""
        # Try backtick first (more common with JSX)
        code_bt = re.search(r'"code"\s*:\s*`((?:[^`\\]|\\.)*)`', raw, re.DOTALL)
        if code_bt:
            code = code_bt.group(1)
        else:
            # Try double-quoted
            code_match = re.search(r'"code"\s*:\s*"', raw)
            if code_match:
                start = code_match.end()
                i = start
                while i < len(raw):
                    if raw[i] == "\\" and i + 1 < len(raw):
                        i += 2
                        continue
                    if raw[i] == '"':
                        code = raw[start:i]
                        code = code.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
                        break
                    i += 1

        return {
            "skill_name": skill_name,
            "category": _extract("category", "io"),
            "description": _extract("description", skill_name),
            "tags": _extract_list("tags"),
            "code": code,
            "usage_example": _extract("usage_example", ""),
            "complexity": _extract("complexity", "simple"),
            "composes_with": _extract_list("composes_with"),
            "parameters": [],
        }