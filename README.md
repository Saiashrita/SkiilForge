# SkillForge — Self-Evolving Code Skill Library

> An agent that watches your code, extracts reusable patterns, crystallizes them into composable skills using Gemini, and surfaces them directly inside VS Code / Cursor / any MCP-compatible editor.

---

## What it does

1. **Watches** your working directories for code you write
2. **Detects** repeated structural patterns via AST hashing (not string matching)
3. **Crystallizes** patterns into production-ready, documented skill functions via Gemini
4. **Indexes** everything in a semantic `SKILL.md` with tags and usage examples
5. **Exposes** your skill library as an MCP server so VS Code/Cursor can autocomplete and inject skills

Over time, it builds YOUR personal toolbox — shaped by how you actually code.

---

## Quick Start

```bash
# 1. Install
pip install -e ".[dev]"

# 2. Set Gemini API key
export GEMINI_API_KEY=your_key_here

# 3. Initialize skill library in your project
skillforge init --dir ./my_project

# 4. Start the watcher + MCP server
skillforge start

# 5. In VS Code/Cursor — add MCP server (see editor setup below)
```

---

## Editor Setup

### VS Code / Cursor (MCP)
Add to your `.cursor/mcp.json` or VS Code `settings.json`:
```json
{
  "mcpServers": {
    "skillforge": {
      "command": "skillforge",
      "args": ["mcp"],
      "env": { "GEMINI_API_KEY": "${env:GEMINI_API_KEY}" }
    }
  }
}
```

### Cursor Rules (auto-inject)
SkillForge generates a `.cursorrules` / `.cursor/rules` snippet automatically after crystallizing skills.

---

## Architecture

```
Code Changes (fs watcher)
        │
        ▼
PatternDetector (AST hash)
        │
   freq >= threshold?
        │
        ▼
SkillCrystallizer (Gemini Flash)
        │
        ▼
SkillRegistry ──► SKILL.md index
        │
        ▼
MCP Server ──► VS Code / Cursor / Zed
```

---

## Project Structure

```
skillforge/
├── core/
│   ├── pattern_detector.py      # AST structural hashing
│   ├── skill_crystallizer.py    # Gemini rewrite + generalize
│   ├── skill_registry.py        # Index + semantic search
│   └── composer.py              # Detect skill composition
├── watchers/
│   ├── fs_watcher.py            # watchdog on directories
│   └── session_tracker.py       # tracks pattern frequency
├── api/
│   ├── mcp_server.py            # MCP protocol server
│   └── cli.py                   # skillforge CLI
├── storage/
│   ├── skills/                  # .py skill files
│   ├── candidates/              # pattern candidates (JSON)
│   └── SKILL.md                 # auto-generated index
└── vscode_extension/            # Optional VS Code extension
```
