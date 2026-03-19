"""
skillforge/api/cli.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
import structlog

app     = typer.Typer(name="skillforge", add_completion=False, pretty_exceptions_enable=False)
console = Console()


def _setup_logging(verbose: bool = False) -> None:
    import logging
    level = logging.DEBUG if verbose else logging.INFO
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
    )


@app.command()
def init(
    directory: Path = typer.Argument(Path("."), help="Project directory to initialize"),
    verbose:   bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Initialize SkillForge in a project directory."""
    _setup_logging(verbose)
    from skillforge.config import settings

    console.print(Panel.fit(
        "[bold cyan]SkillForge Init[/bold cyan]\n"
        f"Storage: [dim]{settings.storage_dir}[/dim]\n"
        f"Skills:  [dim]{settings.skills_dir}[/dim]"
    ))

    cursor_dir = directory / ".cursor"
    cursor_dir.mkdir(exist_ok=True)
    mcp_config = cursor_dir / "mcp.json"
    if not mcp_config.exists():
        import json
        mcp_config.write_text(json.dumps({
            "mcpServers": {
                "skillforge": {
                    "command": "skillforge",
                    "args":    ["mcp"],
                    "env":     {"GEMINI_API_KEY": "${env:GEMINI_API_KEY}"},
                }
            }
        }, indent=2))
        console.print(f"[green]✓[/green] Created {mcp_config}")

    vscode_dir = directory / ".vscode"
    vscode_dir.mkdir(exist_ok=True)

    console.print("\n[bold green]SkillForge initialized![/bold green]")
    console.print("  1. Set key:   [cyan]set GEMINI_API_KEY=your_key[/cyan]")
    console.print("  2. Start:     [cyan]skillforge start --dir .[/cyan]")
    console.print("  3. (debug):   [cyan]skillforge start --dir . --verbose[/cyan]")


@app.command()
def start(
    dirs:    Optional[List[Path]] = typer.Option(None, "--dir", "-d", help="Directories to watch"),
    verbose: bool                 = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start the SkillForge watcher agent."""
    _setup_logging(verbose)
    from skillforge.config import settings
    from skillforge.core.agent import SkillForgeAgent

    watch_dirs: List[Path] = list(dirs) if dirs else [Path(".")]

    console.print(Panel.fit(
        f"[bold cyan]SkillForge Agent[/bold cyan]\n"
        f"Watching: {', '.join(str(d.resolve()) for d in watch_dirs)}\n"
        f"Skills stored at: [dim]{settings.skills_dir}[/dim]\n"
        f"Polling every 1s — edit any .py file to trigger\n"
        f"Press [bold]Ctrl+C[/bold] to stop."
    ))

    agent = SkillForgeAgent()
    for d in watch_dirs:
        agent.add_watch_dir(d)

    try:
        asyncio.run(agent.start())
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/yellow]")


@app.command()
def mcp(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Start SkillForge as MCP server (for VS Code / Cursor / Antigravity)."""
    import logging
    import sys

    # CRITICAL: MCP uses stdio for JSON-RPC communication.
    # Any log output to stdout breaks the protocol.
    # Redirect ALL logs to stderr only.
    if verbose:
        level = logging.DEBUG
    else:
        level = logging.WARNING  # silent by default in MCP mode

    import structlog
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )

    from skillforge.core.agent import SkillForgeAgent
    from skillforge.api.mcp_server import run_mcp_stdio
    agent = SkillForgeAgent()
    asyncio.run(run_mcp_stdio(
        registry     = agent.registry,
        detector     = agent.detector,
        crystallizer = agent.crystallizer,
    ))


@app.command()
def search(
    query:     str  = typer.Argument(...),
    top_k:     int  = typer.Option(5, "--top", "-n"),
    show_code: bool = typer.Option(False, "--code", "-c"),
    verbose:   bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Semantic search over your skill library."""
    _setup_logging(verbose)
    from skillforge.config import settings
    from skillforge.core.skill_registry import SkillRegistry

    registry = SkillRegistry(skills_dir=settings.skills_dir, index_path=settings.skill_index_path)
    results  = registry.search(query, top_k=top_k)

    if not results:
        console.print("[yellow]No skills found yet.[/yellow]")
        raise typer.Exit(0)

    for skill, score in results:
        console.print(f"\n[cyan]{skill.skill_name}[/cyan] [dim]({skill.category})[/dim] score=[green]{score:.2f}[/green]")
        console.print(f"  {skill.description}")
        if show_code:
            console.print(Syntax(skill.code, "python", theme="monokai", line_numbers=True))


@app.command(name="list")
def list_skills(
    category: Optional[str] = typer.Option(None, "--category", "-c"),
    verbose:  bool          = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List all skills in your library."""
    _setup_logging(verbose)
    from skillforge.config import settings
    from skillforge.core.skill_registry import SkillRegistry

    registry = SkillRegistry(skills_dir=settings.skills_dir, index_path=settings.skill_index_path)
    skills   = registry.all_skills()
    if category:
        skills = [s for s in skills if s.category == category]

    if not skills:
        console.print("[yellow]No skills yet. Start the agent and write some code![/yellow]")
        raise typer.Exit(0)

    table = Table(title=f"SkillForge Library ({len(skills)} skills)")
    table.add_column("Name",       style="cyan", no_wrap=True)
    table.add_column("Category",   style="dim")
    table.add_column("Description")
    table.add_column("Seen",       style="green", justify="right")

    for s in sorted(skills, key=lambda x: x.skill_name):
        table.add_row(s.skill_name, s.category,
                      s.description[:60] + ("…" if len(s.description) > 60 else ""),
                      str(s.frequency))
    console.print(table)


@app.command()
def stats(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Show skill library statistics."""
    _setup_logging(verbose)
    from skillforge.config import settings
    from skillforge.core.skill_registry import SkillRegistry

    registry   = SkillRegistry(skills_dir=settings.skills_dir, index_path=settings.skill_index_path)
    all_skills = registry.all_skills()
    by_cat: dict = {}
    for s in all_skills:
        by_cat[s.category] = by_cat.get(s.category, 0) + 1

    console.print(Panel.fit(
        f"[bold cyan]SkillForge Stats[/bold cyan]\n\n"
        f"Total skills:   [bold green]{len(all_skills)}[/bold green]\n"
        f"Storage:        [dim]{settings.storage_dir}[/dim]"
    ))

    if by_cat:
        table = Table(title="By Category")
        table.add_column("Category", style="cyan")
        table.add_column("Count",    style="green", justify="right")
        for cat, count in sorted(by_cat.items(), key=lambda x: -x[1]):
            table.add_row(cat, str(count))
        console.print(table)


if __name__ == "__main__":
    app()