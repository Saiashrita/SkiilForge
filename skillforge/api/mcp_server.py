"""
skillforge/api/mcp_server.py

MCP (Model Context Protocol) server that exposes SkillForge skills
as tools to VS Code / Cursor / any MCP-compatible client.

Tools exposed:
  - search_skills         → semantic search over skill library
  - get_skill             → get full code for a specific skill
  - list_skills           → list all skills with metadata
  - get_skill_index       → return entire SKILL.md as text
  - add_code_for_analysis → manually submit code for pattern analysis
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

from skillforge.config import settings
from skillforge.core.skill_registry import SkillRegistry
from skillforge.core.pattern_detector import PatternDetector
from skillforge.core.skill_crystallizer import SkillCrystallizer

log = structlog.get_logger(__name__)


def create_mcp_server(
    registry: SkillRegistry,
    detector: PatternDetector,
    crystallizer: SkillCrystallizer,
) -> Server:
    server = Server("skillforge")

    # ── Tool definitions ─────────────────────────────────────────────────────

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="search_skills",
                description=(
                    "Semantically search your personal skill library for reusable code functions. "
                    "Use this before writing any utility/helper code to see if you already have it."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language description of what you want to do",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results to return (default 5)",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="get_skill",
                description="Get the complete source code for a specific skill by name.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "The snake_case skill name",
                        }
                    },
                    "required": ["skill_name"],
                },
            ),
            types.Tool(
                name="list_skills",
                description="List all skills in your library, optionally filtered by category.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": "Filter by category (optional)",
                            "enum": [
                                "io", "parsing", "api_client", "data_transform",
                                "validation", "concurrency", "caching", "string_utils",
                                "filesystem", "networking", "auth", "testing", "logging",
                                "composed",
                            ],
                        }
                    },
                },
            ),
            types.Tool(
                name="get_skill_index",
                description="Return the full SKILL.md index as markdown. Shows all skills with descriptions.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="submit_code",
                description=(
                    "Submit a Python code snippet for pattern analysis. "
                    "SkillForge will detect if it matches existing patterns and may crystallize "
                    "it into a new skill if seen enough times."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "Python code to analyze",
                        },
                        "context": {
                            "type": "string",
                            "description": "What task/file this code came from",
                        },
                        "language": {
                            "type": "string",
                            "description": "Language: python, javascript, typescript, jsx, tsx (default: python)",
                            "default": "python",
                        },
                    },
                    "required": ["code"],
                },
            ),
            types.Tool(
                name="get_stats",
                description="Get statistics about your skill library: total skills, categories, most-used patterns.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    # ── Tool handlers ─────────────────────────────────────────────────────────

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:

        if name == "search_skills":
            query  = arguments["query"]
            top_k  = arguments.get("top_k", settings.max_search_results)
            results = registry.search(query, top_k=top_k)

            if not results:
                return [types.TextContent(
                    type="text",
                    text="No matching skills found. Keep coding — SkillForge will learn from your patterns!",
                )]

            lines = [f"## Skills matching: '{query}'\n"]
            for skill, score in results:
                lines.append(f"### `{skill.skill_name}` (relevance: {score:.2f})")
                lines.append(f"**{skill.description}**")
                lines.append(f"Category: `{skill.category}` | Complexity: {skill.complexity}")
                lines.append(f"Tags: {', '.join(f'`{t}`' for t in skill.tags)}")
                lines.append("\n**Usage:**")
                lines.append(f"```python\n{skill.usage_example}\n```\n")

            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "get_skill":
            skill_name = arguments["skill_name"]
            skill = registry.get(skill_name)
            if not skill:
                return [types.TextContent(
                    type="text",
                    text=f"Skill `{skill_name}` not found. Use `list_skills` to see available skills.",
                )]
            return [types.TextContent(type="text", text=skill.to_python_file())]

        elif name == "list_skills":
            category_filter = arguments.get("category")
            all_skills = registry.all_skills()
            if category_filter:
                all_skills = [s for s in all_skills if s.category == category_filter]

            if not all_skills:
                msg = "No skills in library yet." if not category_filter else f"No skills in category `{category_filter}`."
                return [types.TextContent(type="text", text=msg)]

            lines = [f"## Your Skill Library ({len(all_skills)} skills)\n"]
            # Group by category
            by_cat: dict[str, list] = {}
            for s in sorted(all_skills, key=lambda x: x.skill_name):
                by_cat.setdefault(s.category, []).append(s)

            for cat, skills in sorted(by_cat.items()):
                lines.append(f"\n### {cat.replace('_',' ').title()}")
                for s in skills:
                    lines.append(f"- `{s.skill_name}` — {s.description} (seen {s.frequency}×)")

            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "get_skill_index":
            if settings.skill_index_path.exists():
                content = settings.skill_index_path.read_text(encoding="utf-8")
            else:
                content = "SKILL.md not yet generated. Start SkillForge watcher to begin."
            return [types.TextContent(type="text", text=content)]

        elif name == "submit_code":
            code    = arguments["code"]
            context = arguments.get("context", "manual_submission")
            lang    = arguments.get("language", "python")

            # Determine file extension from language
            ext_map = {"python": ".py", "javascript": ".js", "jsx": ".jsx", "typescript": ".ts", "tsx": ".tsx"}
            ext = ext_map.get(lang, ".py")

            # Write to a temp file and process
            tmp = settings.candidates_dir / f"_submitted{ext}"
            tmp.write_text(code, encoding="utf-8")

            candidates = detector.process_file(tmp, context=context)
            tmp.unlink(missing_ok=True)

            if not candidates:
                return [types.TextContent(
                    type="text",
                    text="Pattern recorded. Submit similar code more times to trigger skill crystallization.",
                )]

            lines = [f"✨ Detected {len(candidates)} promotable pattern(s)! Crystallizing...\n"]
            for candidate in candidates:
                skill = await crystallizer.crystallize(candidate)
                if skill:
                    registry.register(skill)
                    lines.append(f"✅ New skill crystallized: `{skill.skill_name}`")
                    lines.append(f"   {skill.description}")

            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "get_stats":
            all_skills = registry.all_skills()
            by_cat: dict[str, int] = {}
            by_lang: dict[str, int] = {}
            total_patterns = sum(s.frequency for s in all_skills)
            for s in all_skills:
                by_cat[s.category] = by_cat.get(s.category, 0) + 1
                lang = getattr(s, 'language', 'python')
                by_lang[lang] = by_lang.get(lang, 0) + 1

            lines = [
                "## SkillForge Stats\n",
                f"- **Total skills**: {len(all_skills)}",
                f"- **Total pattern observations**: {total_patterns}",
                f"- **Languages**: {', '.join(by_lang.keys()) or 'none'}",
                f"- **Skill index**: `{settings.skill_index_path}`\n",
                "### Skills by category",
            ]
            for cat, count in sorted(by_cat.items(), key=lambda x: -x[1]):
                lines.append(f"- {cat}: {count}")

            if by_lang:
                lines.append("\n### Skills by language")
                for lang, count in sorted(by_lang.items(), key=lambda x: -x[1]):
                    lines.append(f"- {lang}: {count}")

            top = sorted(all_skills, key=lambda s: s.frequency, reverse=True)[:5]
            if top:
                lines.append("\n### Most frequently observed patterns")
                for s in top:
                    lines.append(f"- `{s.skill_name}` — {s.frequency}× observations")

            return [types.TextContent(type="text", text="\n".join(lines))]

        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


async def run_mcp_stdio(
    registry: SkillRegistry,
    detector: PatternDetector,
    crystallizer: SkillCrystallizer,
) -> None:
    """Run the MCP server over stdio (for VS Code/Cursor integration)."""
    server = create_mcp_server(registry, detector, crystallizer)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
