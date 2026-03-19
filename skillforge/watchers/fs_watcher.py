"""
skillforge/watchers/fs_watcher.py
Uses polling observer for reliable Windows support.
"""
from __future__ import annotations

import asyncio
import fnmatch
import time
from pathlib import Path
from typing import Callable, Coroutine, Any

import structlog
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers.polling import PollingObserver  # Windows-reliable

log = structlog.get_logger(__name__)

FileCallback = Callable[[Path], Coroutine[Any, Any, None]]


class _DebounceHandler(FileSystemEventHandler):
    def __init__(
        self,
        callback: FileCallback,
        loop: asyncio.AbstractEventLoop,
        extensions: list,
        ignore_patterns: list,
        debounce_secs: float = 2.0,
    ) -> None:
        super().__init__()
        self._callback       = callback
        self._loop           = loop
        self._extensions     = set(extensions)
        self._ignore_patterns = ignore_patterns
        self._debounce_secs  = debounce_secs
        self._pending: dict[str, asyncio.TimerHandle] = {}

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(str(event.src_path))

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(str(event.src_path))

    def _schedule(self, path_str: str) -> None:
        path = Path(path_str)
        if path.suffix not in self._extensions:
            return
        for pattern in self._ignore_patterns:
            if fnmatch.fnmatch(path_str, pattern):
                return
        if path_str in self._pending:
            self._pending[path_str].cancel()
        handle = self._loop.call_later(
            self._debounce_secs,
            self._dispatch,
            path,
        )
        self._pending[path_str] = handle
        log.debug("file_event_scheduled", path=path.name)

    def _dispatch(self, path: Path) -> None:
        self._pending.pop(str(path), None)
        log.debug("file_dispatching", path=path.name)
        asyncio.run_coroutine_threadsafe(
            self._callback(path),
            self._loop,
        )


class FileSystemWatcher:
    def __init__(
        self,
        callback: FileCallback,
        loop: asyncio.AbstractEventLoop,
        extensions: list | None = None,
        ignore_patterns: list | None = None,
        debounce_secs: float = 2.0,
    ) -> None:
        from skillforge.config import settings
        self._callback   = callback
        self._loop       = loop
        self._extensions = extensions or settings.watch_extensions
        self._ignore     = ignore_patterns or settings.watch_ignore_patterns
        self._debounce   = debounce_secs or settings.debounce_seconds

        # PollingObserver works reliably on Windows (checks every 1s)
        self._observer   = PollingObserver(timeout=1)
        self._handler    = _DebounceHandler(
            callback        = callback,
            loop            = loop,
            extensions      = self._extensions,
            ignore_patterns = self._ignore,
            debounce_secs   = self._debounce,
        )
        self._watched_dirs: list[Path] = []

    def watch(self, directory: Path, recursive: bool = True) -> None:
        directory = directory.resolve()
        if not directory.exists():
            raise FileNotFoundError(f"Watch directory not found: {directory}")
        self._observer.schedule(self._handler, str(directory), recursive=recursive)
        self._watched_dirs.append(directory)
        log.info("watching", directory=str(directory), recursive=recursive)

    def start(self) -> None:
        self._observer.start()
        log.info(
            "watcher_started",
            dirs=[str(d) for d in self._watched_dirs],
            extensions=self._extensions,
        )

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
        log.info("watcher_stopped")