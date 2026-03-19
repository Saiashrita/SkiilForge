"""
skillforge/core/agent.py

Production-ready SkillForge agent with:
- Multi-language file watching (Python + JS/TS/JSX/TSX)
- Auto-detect on file save (Ctrl+S)
- Pattern detection → crystallization → composition detection pipeline
- Robust Windows support with polling watcher
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Optional

import structlog

from skillforge.config import settings
from skillforge.core.pattern_detector import PatternDetector, PatternCandidate
from skillforge.core.skill_crystallizer import SkillCrystallizer
from skillforge.core.skill_registry import SkillRegistry
from skillforge.core.composition_detector import CompositionDetector

log = structlog.get_logger(__name__)


class SkillForgeAgent:
    """
    Main SkillForge agent.

    Watches directories for file changes, detects patterns,
    crystallizes skills, and detects cross-function compositions.

    Auto-processes files on save (Ctrl+S) via polling watcher.
    """

    def __init__(self) -> None:
        self.detector = PatternDetector(
            min_lines=settings.pattern_min_lines,
            min_frequency=settings.pattern_min_frequency,
            min_complexity=settings.pattern_complexity_threshold,
            similarity_threshold=settings.similarity_threshold,
            detect_classes=settings.detect_classes,
            candidates_dir=settings.candidates_dir,
        )
        self.crystallizer = SkillCrystallizer(skills_dir=settings.skills_dir)
        self.registry = SkillRegistry(
            skills_dir=settings.skills_dir,
            index_path=settings.skill_index_path,
        )
        self.composition_detector = CompositionDetector(
            min_co_occurrence=settings.composition_min_co_occurrence,
        ) if settings.detect_compositions else None

        self._watch_dirs: list[Path] = []
        self._crystallize_queue: asyncio.Queue = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def add_watch_dir(self, directory: Path) -> None:
        """Add a directory to watch for file changes."""
        resolved = directory.resolve()
        if resolved not in self._watch_dirs:
            self._watch_dirs.append(resolved)
            log.info("watch_dir_added", path=str(resolved))

    async def start(self) -> None:
        """Start the agent: file watcher + crystallization worker."""
        self._loop = asyncio.get_running_loop()
        log.info(
            "agent_started",
            watching=len(self._watch_dirs),
            extensions=settings.watch_extensions,
            languages=settings.supported_languages,
            detect_classes=settings.detect_classes,
            detect_compositions=settings.detect_compositions,
        )

        # Start the polling watcher in a background thread
        watcher_thread = threading.Thread(
            target=self._run_watcher_thread,
            daemon=True,
            name="skillforge-watcher",
        )
        watcher_thread.start()

        try:
            await asyncio.gather(
                self._crystallize_worker(),
                self._health_logger(),
            )
        finally:
            log.info("agent_stopped")

    def _run_watcher_thread(self) -> None:
        """
        Poll-based file watcher that detects Ctrl+S saves.
        Runs in a background thread. 100% reliable on Windows.
        Supports: .py, .js, .jsx, .ts, .tsx
        """
        import os

        log.info("polling_watcher_started", dirs=[str(d) for d in self._watch_dirs])

        file_mtimes: dict[str, float] = {}
        extensions = set(settings.watch_extensions)
        ignore_dirs = {
            ".git", "__pycache__", "node_modules", ".venv", "venv",
            "dist", "build", ".next", "coverage", "skillforge.egg-info",
            ".skillforge",
        }

        def scan_files() -> dict[str, float]:
            result: dict[str, float] = {}
            for watch_dir in self._watch_dirs:
                for root, dirs, files in os.walk(str(watch_dir)):
                    # Skip ignored directories
                    dirs[:] = [d for d in dirs if d not in ignore_dirs]
                    for fname in files:
                        if any(fname.endswith(ext) for ext in extensions):
                            fpath = os.path.join(root, fname)
                            try:
                                result[fpath] = os.path.getmtime(fpath)
                            except OSError:
                                pass
            return result

        # Initial scan
        file_mtimes = scan_files()
        log.info("polling_initial_scan", files_tracked=len(file_mtimes))

        while True:
            time.sleep(1)  # Poll every 1 second — catches Ctrl+S instantly
            try:
                current = scan_files()

                # Detect new or modified files
                for fpath, mtime in current.items():
                    if fpath not in file_mtimes:
                        log.info("file_created", path=Path(fpath).name)
                        self._dispatch_file(Path(fpath))
                    elif mtime != file_mtimes[fpath]:
                        log.info("file_modified", path=Path(fpath).name)
                        self._dispatch_file(Path(fpath))

                file_mtimes = current

            except Exception as exc:
                log.error("watcher_error", error=str(exc))

    def _dispatch_file(self, path: Path) -> None:
        """Thread-safe dispatch to the asyncio event loop."""
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._on_file_changed(path),
                self._loop,
            )

    async def _on_file_changed(self, path: Path) -> None:
        """
        Process a changed file through the full pipeline:
        1. Parse and detect patterns
        2. Queue promoted patterns for crystallization
        3. Run composition detection
        """
        log.debug("processing_file", path=path.name, suffix=path.suffix)

        try:
            # 1. Pattern detection
            candidates = self.detector.process_file(path, context=str(path))
            for candidate in candidates:
                await self._crystallize_queue.put(candidate)
                log.info(
                    "candidate_queued",
                    hash=candidate.structural_hash[:8],
                    language=candidate.language,
                    type=candidate.node_type,
                    queue_size=self._crystallize_queue.qsize(),
                )

            # 2. Composition detection
            if self.composition_detector is not None:
                units = self.detector.get_units(path)
                compositions = self.composition_detector.process_units(
                    units, context=str(path)
                )
                for composed in compositions:
                    # Crystallize the composed pattern
                    skill = await self.crystallizer.find_compositions(composed)
                    if skill:
                        self.registry.register(skill)
                        log.info(
                            "composed_skill_added",
                            skill=skill.skill_name,
                            components=composed.component_names[:3],
                        )

        except Exception as exc:
            log.error("file_processing_error", path=str(path), error=str(exc))

    async def _crystallize_worker(self) -> None:
        """Background worker that crystallizes queued pattern candidates."""
        log.info("crystallize_worker_started")
        while True:
            candidate = await self._crystallize_queue.get()
            try:
                skill = await self.crystallizer.crystallize(candidate)
                if skill:
                    self.registry.register(skill)
                    log.info(
                        "skill_added_to_library",
                        skill=skill.skill_name,
                        language=skill.language,
                        category=skill.category,
                    )
            except Exception as exc:
                log.error("crystallize_error", error=str(exc))
            finally:
                self._crystallize_queue.task_done()

    async def _health_logger(self) -> None:
        """Periodically log agent health status."""
        while True:
            await asyncio.sleep(30)
            log.info(
                "status",
                total_skills=self.registry.skill_count(),
                queue_depth=self._crystallize_queue.qsize(),
                watching=len(self._watch_dirs),
                extensions=settings.watch_extensions,
            )