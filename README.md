<div align="center">

```
███████╗██╗  ██╗██╗██╗     ██╗      ███████╗ ██████╗ ██████╗  ██████╗ ███████╗
██╔════╝██║ ██╔╝██║██║     ██║      ██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝
███████╗█████╔╝ ██║██║     ██║      █████╗  ██║   ██║██████╔╝██║  ███╗█████╗  
╚════██║██╔═██╗ ██║██║     ██║      ██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝  
███████║██║  ██╗██║███████╗███████╗ ██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗
╚══════╝╚═╝  ╚═╝╚═╝╚══════╝╚══════╝╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝
```

**An agent that watches you code and builds your personal Claude Skills library — automatically.**

[![Python](https://img.shields.io/badge/Python-3.11+-blue?style=flat-square&logo=python)](https://python.org)
[![Ollama](https://img.shields.io/badge/Ollama-Local%20LLM-black?style=flat-square)](https://ollama.com)
[![MCP](https://img.shields.io/badge/MCP-Editor%20Integration-green?style=flat-square)](https://modelcontextprotocol.io)
[![Claude Skills](https://img.shields.io/badge/Claude-Skills%20Compatible-orange?style=flat-square)](https://claude.ai)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

[**Quick Start**](#quick-start) · [**How It Works**](#how-it-works) · [**Editor Setup**](#editor-setup) · [**Upload to Claude.ai**](#upload-skills-to-claudeai) · [**CLI Reference**](#cli-reference)

</div>

---

## What is SkillForge?

Everyone talks about building Claude Skills manually — writing YAML frontmatter, crafting trigger phrases, packaging folders.

SkillForge takes a different route. It watches your code, detects repeated patterns using **AST structural hashing**, and automatically crystallizes them into **Claude Skills compliant folders** — ready to upload directly to Claude.ai.

No manual writing. No templates. Skills built from patterns in YOUR actual codebase.

```
You write code normally (Ctrl+S)
         ↓
AST structural hashing detects the same pattern twice
         ↓
Local LLM generalizes it into a production-ready skill
         ↓
Proper Claude Skills folder output:
  load-config-safely/
    ├── SKILL.md        ← YAML frontmatter + instructions
    ├── scripts/        ← runnable code
    └── references/     ← usage examples
         ↓
Zip and upload to Claude.ai — done
```

---

## Features

| Feature | Description |
|---|---|
| 🔍 **AST Structural Hashing** | Detects identical patterns even with different variable names |
| 🌐 **Multi-language** | Python (ast) + JavaScript / TypeScript / JSX / TSX (tree-sitter) |
| 🧩 **Composition Detection** | Finds function pipelines and co-occurrence patterns |
| 🤖 **Local LLM** | Runs on Ollama — free, private, no rate limits |
| 📦 **Claude Skills Output** | Proper kebab-case folders with SKILL.md, scripts/, references/ |
| 📡 **MCP Server** | Surfaces skills in VS Code, Cursor, Antigravity |
| 📊 **TF-IDF Search** | Semantic skill search with sklearn embeddings |
| 🔄 **Auto-detect on save** | Ctrl+S triggers analysis — no manual commands needed |
| 📝 **Auto .cursorrules** | Editor AI reads your skills before generating any code |

---

## Prerequisites

| Requirement | Version | Check |
|---|---|---|
| **Python** | 3.11+ | `python --version` |
| **pip** | latest | `pip --version` |
| **Git** | any | `git --version` |
| **Ollama** *(recommended)* | latest | `ollama --version` |

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/Saiashrita/skillforge.git
cd skillforge
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
```

| OS | Activate command |
|---|---|
| **Windows (Git Bash)** | `source .venv/Scripts/activate` |
| **Windows (CMD)** | `.venv\Scripts\activate` |
| **Windows (PowerShell)** | `.venv\Scripts\Activate.ps1` |
| **macOS / Linux** | `source .venv/bin/activate` |

> ⚠️ You must activate the venv every time you open a new terminal.

### 3. Install SkillForge

```bash
# Try this first:
pip install -e ".[dev]"

# If it fails (no dev extras defined):
pip install -e .

# For JS/TS/JSX/TSX support (optional but recommended):
pip install tree-sitter tree-sitter-javascript tree-sitter-typescript
```

### 4. Configure your LLM backend

```bash
# Copy example config:
cp .env.example .env

# Or create .env manually:
echo "SKILLFORGE_LLM_BACKEND=ollama" > .env
echo "SKILLFORGE_OLLAMA_MODEL=phi3" >> .env
```

**Option A — Ollama (free, local, recommended)**
```env
SKILLFORGE_LLM_BACKEND=ollama
SKILLFORGE_OLLAMA_MODEL=phi3
```
```bash
# Download from https://ollama.com/download then:
ollama pull phi3        # ~2.2GB one-time download
```

**Option B — Claude API (best quality)**
```env
SKILLFORGE_LLM_BACKEND=claude
ANTHROPIC_API_KEY=sk-ant-your-key-here
```
Get a key from: https://console.anthropic.com

**Option C — Gemini**
```env
SKILLFORGE_LLM_BACKEND=gemini
GEMINI_API_KEY=your-key-here
```
Get a key from: https://aistudio.google.com/apikey

### 5. Verify installation

```bash
skillforge --help
# Should show all available commands
```

---

## Daily Workflow — 3 Terminals

**Terminal 1 — Ollama server (keep open always):**
```bash
ollama serve
```

**Terminal 2 — SkillForge watcher on your project:**
```bash
cd /path/to/your/project
skillforge start --dir . --verbose
```

**Terminal 3 — your normal work:**
```bash
# Code normally. Every Ctrl+S triggers pattern detection automatically.
# When a pattern appears ≥ 3 times → automatically crystallized into a skill.
```

You'll see output like this in Terminal 2:
```
15:16:06 pattern_promoted    hash=194c4cac fn=load_config freq=3
15:16:06 candidate_queued    hash=194c4cac queue_size=1
15:16:06 crystallizing       backend=ollama hash=194c4cac
15:16:21 crystallized        skill=load-config-safely category=io
15:16:21 skill_added_to_library  total=1
```

---

## How It Works

### 1. AST Structural Hashing

The core insight: two functions that do the same thing with different variable names are **structurally identical**.

```python
# These produce the SAME hash — SkillForge detects them as one pattern
def load_config(filepath):          def read_settings(config_path):
    try:                                try:
        with open(filepath) as f:           with open(config_path) as f:
            return json.load(f)                 return json.load(f)
    except FileNotFoundError:           except FileNotFoundError:
        return {}                           return {}
```

SkillForge normalizes all variable names to positional placeholders before hashing, so both functions hash identically. When the same structural hash appears ≥ 3 times, the pattern is promoted for crystallization.

### 2. Multi-language Parsing

```
Python files    → stdlib ast (zero extra deps)
JS/TS/JSX/TSX  → tree-sitter (detects functions, classes, React components, hooks)
```

### 3. Composition Detection

Beyond individual functions, SkillForge detects cross-function patterns:

- **Pipelines** — function A calls function B calls function C
- **Co-occurrence** — functions that always appear together across files

```
COMPOSED: fetch_to_parse_to_validate   (type=pipeline)
COMPOSED: setup_and_teardown           (type=co_occurrence)
```

### 4. Claude Skills Compliant Output

Every crystallized skill is saved as a proper Claude Skills folder:

```
~/.skillforge/skills/
└── load-config-safely/            ← kebab-case folder name
    ├── SKILL.md                   ← YAML frontmatter + full instructions
    ├── scripts/
    │   └── load_config_safely.py  ← runnable production code
    ├── references/
    │   └── examples.md            ← usage examples + observed files
    └── meta.json                  ← SkillForge internal tracking
```

`SKILL.md` follows the official Anthropic Claude Skills spec:

```yaml
---
name: load-config-safely
description: Load JSON config files safely with error handling. Use when user
  asks to "load config", "read settings", "json io" or needs io utilities.
  Observed 3x in real codebases.
license: MIT
metadata:
  category: io
  complexity: simple
  language: python
  tags: [json, io, error-handling]
  author: SkillForge
  version: 1.0.0
---

# Load Config Safely

**Load JSON config files safely with error handling.**

## Usage

...
```

---

## Upload Skills to Claude.ai

Once SkillForge crystallizes a skill, upload it to Claude.ai in 3 steps:

**Step 1 — Find your skill folder:**
```bash
ls ~/.skillforge/skills/
# load-config-safely/   retry-with-backoff/   fetch-with-timeout/
```

**Step 2 — Zip it:**
```bash
# Windows (Git Bash):
cd ~/.skillforge/skills/
zip -r load-config-safely.zip load-config-safely/

# Or: right-click the folder → Send to → Compressed (zipped) folder
```

**Step 3 — Upload:**
```
Claude.ai → Settings → Capabilities → Skills → Upload skill
```

Done. Claude now has your skill and will trigger it automatically when relevant.

---

## Use on Multiple Projects

SkillForge is a **global tool** — skills learned from one project are available in all others.

```bash
# Install globally once:
pip install --user -e /path/to/skillforge

# Then in ANY project:
cd /path/to/any-other-project
skillforge start --dir .
```

Skills accumulate in `~/.skillforge/skills/` across your entire coding career.

---

## CLI Reference

```bash
# Watch a directory — detects patterns on every Ctrl+S
skillforge start --dir . --verbose

# Start MCP server — for editor integration
skillforge mcp

# Bulk scan existing codebase — great for bootstrapping your library
skillforge scan --dir . --min-frequency 1

# Manually submit a file for immediate crystallization
skillforge submit path/to/useful_function.py

# List all crystallized skills
skillforge list

# Semantic search over your skill library
skillforge search "retry http request"
skillforge search "json file loading"

# Library statistics
skillforge stats

# Initialize in a project (creates .cursorrules for editor AI)
skillforge init .
```

---

## Editor Setup

### Cursor / Antigravity / VS Code (via MCP)

Create `.cursor/mcp.json` in your project:

```json
{
  "mcpServers": {
    "skillforge": {
      "command": "C:\\path\\to\\skillforge\\.venv\\Scripts\\skillforge.exe",
      "args": ["mcp"],
      "env": {}
    }
  }
}
```

> **Windows tip:** Use the full path to `skillforge.exe` inside your venv to avoid PATH issues.

**MCP tools your editor AI gets:**

| Tool | Description |
|---|---|
| `search_skills` | *"search my skills for retry logic"* |
| `get_skill` | *"show me load_config_safely"* |
| `list_skills` | Browse all skills by category |
| `get_skill_index` | Full skill library loaded into context |
| `submit_code` | Manually submit a snippet for analysis |
| `get_stats` | Library statistics |

### Auto .cursorrules

SkillForge automatically writes `.cursorrules` in your project whenever a skill is crystallized. Your editor AI reads this before generating code — suggesting your existing skills instead of writing duplicates.

```
# SkillForge — Available reusable skills from YOUR code patterns
# Suggest these before writing new utility code.

## Available Skills
  - `load-config-safely` — Load JSON config safely [io] 🐍
  - `retry-with-backoff` — Retry any function with exponential backoff [networking] 🐍
  - `fetch-with-timeout` — HTTP fetch with timeout and error handling [api_client] 🐍
```

---

## Where Everything Is Stored

```
~/.skillforge/
├── skills/
│   ├── load-config-safely/
│   │   ├── SKILL.md               ← Claude Skills spec compliant
│   │   ├── scripts/
│   │   │   └── load_config_safely.py
│   │   ├── references/
│   │   │   └── examples.md
│   │   └── meta.json
│   └── retry-with-backoff/
│       └── ...
├── candidates/                     ← patterns waiting to crystallize
├── crystallized.json               ← hashes already processed
├── SKILL.md                        ← master index of all skills
└── embeddings.pkl                  ← TF-IDF search cache
```

---

## Configuration

All settings in your `.env` file:

```env
# ── Backend ──────────────────────────────────────────────────────────
SKILLFORGE_LLM_BACKEND=ollama           # ollama | claude | gemini
SKILLFORGE_OLLAMA_MODEL=phi3            # or llama3.1, mistral, codellama
SKILLFORGE_OLLAMA_BASE_URL=http://localhost:11434

# ── Pattern Detection ──────────────────────────────────────────────────
SKILLFORGE_PATTERN_MIN_FREQUENCY=3      # seen N times before crystallizing
SKILLFORGE_PATTERN_MIN_LINES=8          # minimum function length to track
SKILLFORGE_PATTERN_COMPLEXITY_THRESHOLD=3.0   # cyclomatic complexity floor
SKILLFORGE_SIMILARITY_THRESHOLD=0.85    # fuzzy match threshold (0.0–1.0)

# ── Features ───────────────────────────────────────────────────────────
SKILLFORGE_DETECT_CLASSES=true          # also track classes/components
SKILLFORGE_DETECT_COMPOSITIONS=true     # pipeline detection
SKILLFORGE_AUTO_CURSOR_RULES=true       # auto-update .cursorrules

# ── Languages Watched ──────────────────────────────────────────────────
SKILLFORGE_WATCH_EXTENSIONS=[".py",".js",".jsx",".ts",".tsx"]
```

---

## Recommended Models by RAM

| RAM | Model | Pull command |
|---|---|---|
| 8GB | `phi3` (fast, efficient) | `ollama pull phi3` |
| 16GB | `mistral` or `llama3.1` | `ollama pull mistral` |
| 32GB+ | `llama3.1:70b` (best quality) | `ollama pull llama3.1:70b` |

---

## Project Structure

```
skillforge/
├── skillforge/
│   ├── core/
│   │   ├── agent.py                 ← main orchestrator + file watcher
│   │   ├── pattern_detector.py      ← AST hashing + fuzzy matching
│   │   ├── language_parsers.py      ← Python ast + tree-sitter JS/TS
│   │   ├── composition_detector.py  ← pipeline + co-occurrence detection
│   │   ├── skill_crystallizer.py    ← LLM backends + Claude Skills output
│   │   └── skill_registry.py        ← TF-IDF search + SKILL.md index
│   ├── api/
│   │   ├── cli.py                   ← skillforge CLI (start, mcp, scan, etc.)
│   │   └── mcp_server.py            ← MCP protocol server
│   └── config.py                    ← all settings via .env
├── tests/
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError` | Activate venv: `source .venv/Scripts/activate` |
| `Cannot reach Ollama` | Run `ollama serve` in a separate terminal |
| `ollama pull` fails | Check internet connection and try again |
| `pip install -e .` fails | Ensure Python 3.11+: `python --version` |
| Skills not generating | Verify `.env` LLM backend is set correctly |
| MCP error in editor | Use full path to `skillforge.exe` in mcp.json |
| `tree-sitter` import error | `pip install tree-sitter tree-sitter-javascript tree-sitter-typescript` |
| Low skill count | Run `skillforge scan --dir .` to bootstrap from existing code |
| Pattern never promotes | Lower threshold: `SKILLFORGE_PATTERN_MIN_FREQUENCY=2` in `.env` |

---

## Windows Quick Start (Copy-Paste)

```bash
git clone https://github.com/YOUR_USERNAME/skillforge.git
cd skillforge
python -m venv .venv
source .venv/Scripts/activate
pip install -e .
echo "SKILLFORGE_LLM_BACKEND=ollama" > .env
echo "SKILLFORGE_OLLAMA_MODEL=phi3" >> .env

# Download Ollama from ollama.com/download then:
ollama pull phi3

# Terminal 1 — keep open:
ollama serve

# Terminal 2 — start watcher:
source .venv/Scripts/activate
skillforge start --dir /path/to/your/project --verbose
```

---

## Tech Stack

- **Python 3.11+** — core runtime
- **ast** — Python structural analysis (stdlib, zero deps)
- **tree-sitter** — JS/TS/JSX/TSX parsing
- **Ollama** — local LLM inference (phi3, mistral, llama3.1)
- **Anthropic SDK** — Claude API backend (optional)
- **MCP** — Model Context Protocol for editor integration
- **sklearn TF-IDF** — semantic skill search
- **pydantic-settings** — configuration management
- **structlog** — structured logging
- **typer + rich** — CLI

---

## License

MIT — build on it, fork it, make it yours.

---

<div align="center">

Built by <a href="https://github.com/YOUR_USERNAME">Sourabh Mujumale</a>

⭐ Star this repo if SkillForge saved you from rewriting the same utility function again

</div>
