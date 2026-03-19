# Editor Integration Guide

## Cursor IDE

### Method 1: MCP Server (Recommended — gives Cursor AI full skill access)

Create or edit `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "skillforge": {
      "command": "skillforge",
      "args": ["mcp"],
      "env": {
        "GEMINI_API_KEY": "${env:GEMINI_API_KEY}"
      }
    }
  }
}
```

After adding this, Cursor's AI (Cmd+K / Cmd+L) will have access to these tools:
- `search_skills` — "search my skills for HTTP retry logic"
- `get_skill` — "show me the parse_json_safely skill"
- `list_skills` — "what caching skills do I have?"
- `submit_code` — "analyze this function for patterns"

### Method 2: Cursor Rules (passive — auto-injects skill list into context)

SkillForge auto-generates `.cursorrules` in your project whenever a skill is crystallized.
This file tells Cursor's AI about your available skills so it suggests them automatically.

No setup needed — just run `skillforge start`.

---

## VS Code

### MCP via Continue extension

Install the [Continue](https://marketplace.visualstudio.com/items?itemName=Continue.continue) extension,
then add to `~/.continue/config.json`:

```json
{
  "mcpServers": [
    {
      "name": "skillforge",
      "command": "skillforge",
      "args": ["mcp"],
      "env": {
        "GEMINI_API_KEY": "${env:GEMINI_API_KEY}"
      }
    }
  ]
}
```

### VS Code Snippets

After running `skillforge init`, a `.vscode/skillforge.code-snippets` is created.
Type `sf` in any Python file to get a SkillForge search trigger comment.

### Task Runner

Add to `.vscode/tasks.json`:
```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "SkillForge: Start Watcher",
      "type": "shell",
      "command": "skillforge start --dir ${workspaceFolder}",
      "isBackground": true,
      "problemMatcher": [],
      "presentation": { "panel": "dedicated", "reveal": "silent" }
    },
    {
      "label": "SkillForge: Search Skills",
      "type": "shell",
      "command": "skillforge search '${input:query}'",
      "presentation": { "panel": "shared", "reveal": "always" }
    }
  ],
  "inputs": [
    {
      "id": "query",
      "type": "promptString",
      "description": "What skill are you looking for?"
    }
  ]
}
```

---

## Any MCP-compatible tool (Zed, Claude Desktop, etc.)

The MCP server runs over **stdio** — the standard protocol.
Add this to any tool's MCP config:

```json
{
  "command": "skillforge",
  "args": ["mcp"]
}
```

With env: `GEMINI_API_KEY=your_key`

---

## Quick Workflow

```
You write code in VS Code/Cursor
         │
         ▼
SkillForge watcher detects saves
         │
  (after seeing pattern 2+ times)
         │
         ▼
Gemini crystallizes → skill saved to ~/.skillforge/skills/
         │
         ▼
.cursorrules updated → Cursor AI now knows about your skill
         │
         ▼
Next time you write similar code:
  - Cursor suggests your existing skill
  - Or: ask "search my skills for X" in chat
```
