"""
skillforge/config.py

Centralized configuration for SkillForge.
All settings can be overridden via environment variables with SKILLFORGE_ prefix,
or via a .env file.
"""
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SKILLFORGE_",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Backend: "ollama" | "claude" | "gemini" ───────────────────────────────
    llm_backend: str = "gemini"

    # ── Ollama ────────────────────────────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "phi3"

    # ── Claude (optional) ─────────────────────────────────────────────────────
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    claude_model: str = "claude-haiku-4-5-20251001"

    # ── Gemini (optional) ─────────────────────────────────────────────────────
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    gemini_model: str = "gemini-2.5-flash"

    # ── Pattern detection ─────────────────────────────────────────────────────
    pattern_min_lines: int = 5
    pattern_min_frequency: int = 2
    pattern_complexity_threshold: float = 2.0
    similarity_threshold: float = 0.85
    detect_classes: bool = True
    detect_compositions: bool = True
    composition_min_co_occurrence: int = 2

    # ── Supported languages ───────────────────────────────────────────────────
    supported_languages: list = ["python", "javascript", "typescript", "jsx", "tsx"]

    # ── Storage ───────────────────────────────────────────────────────────────
    storage_dir: Path = Path.home() / ".skillforge"

    # ── Watcher ───────────────────────────────────────────────────────────────
    watch_extensions: list = [".py", ".js", ".jsx", ".ts", ".tsx"]
    watch_ignore_patterns: list = [
        "*/.git/*", "*/node_modules/*", "*/__pycache__/*",
        "*/dist/*", "*/build/*", "*/.venv/*", "*/venv/*",
        "*/.next/*", "*/coverage/*",
    ]
    debounce_seconds: float = 2.0

    # ── MCP ───────────────────────────────────────────────────────────────────
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765
    mcp_server_name: str = "skillforge"

    # ── Search ────────────────────────────────────────────────────────────────
    max_search_results: int = 5
    auto_cursor_rules: bool = True

    @property
    def skills_dir(self) -> Path:
        p = self.storage_dir / "skills"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def candidates_dir(self) -> Path:
        p = self.storage_dir / "candidates"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def skill_index_path(self) -> Path:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        return self.storage_dir / "SKILL.md"

    def model_post_init(self, __context: object) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()