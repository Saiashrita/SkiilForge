"""
skillforge/core/composition_detector.py

Detects multi-function composition patterns (pipelines, toolkits)
by analyzing call-graph relationships and import co-occurrence.

Produces ComposedPattern candidates for crystallization into higher-level skills.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import structlog

from skillforge.core.language_parsers import ParsedUnit

log = structlog.get_logger(__name__)


@dataclass
class ComposedPattern:
    """A group of functions that form a higher-level workflow/pipeline."""

    name: str
    component_names: list[str]
    component_sources: list[str]
    pattern_type: str  # "pipeline" | "toolkit" | "co_occurrence"
    language: str
    frequency: int
    contexts: list[str]
    call_chain: list[str] = field(default_factory=list)

    @property
    def combined_source(self) -> str:
        """Combine all component sources into one block."""
        return "\n\n".join(self.component_sources)


class CompositionDetector:
    """Detects cross-function composition patterns from parsed units."""

    def __init__(
        self,
        min_co_occurrence: int = 2,
        min_chain_length: int = 2,
    ) -> None:
        self.min_co_occurrence = min_co_occurrence
        self.min_chain_length = min_chain_length

        # Track which functions appear together per file
        # key: frozenset of function names, value: list of file contexts
        self._co_occurrences: defaultdict[frozenset[str], list[str]] = defaultdict(list)

        # Track call chains: fn_a -> fn_b -> fn_c
        # key: tuple of call chain, value: list of file contexts
        self._call_chains: defaultdict[tuple[str, ...], list[str]] = defaultdict(list)

        # Already promoted compositions
        self._promoted: set[frozenset[str]] = set()

    def process_units(
        self,
        units: list[ParsedUnit],
        context: str,
    ) -> list[ComposedPattern]:
        """
        Analyze a batch of parsed units from a single file for composition patterns.

        Args:
            units: ParsedUnit objects from one file.
            context: File path or description for tracking.

        Returns:
            List of newly promoted ComposedPattern objects.
        """
        if len(units) < 2:
            return []

        promoted: list[ComposedPattern] = []

        # 1. Co-occurrence tracking
        fn_names = frozenset(u.name for u in units if u.node_type in ("function", "arrow_fn", "hook"))
        if len(fn_names) >= 2:
            self._co_occurrences[fn_names].append(context)
            freq = len(self._co_occurrences[fn_names])

            if freq >= self.min_co_occurrence and fn_names not in self._promoted:
                self._promoted.add(fn_names)
                sources = [u.source_code for u in units if u.name in fn_names]
                lang = units[0].language if units else "python"

                pattern = ComposedPattern(
                    name="_and_".join(sorted(fn_names)[:3]),
                    component_names=sorted(fn_names),
                    component_sources=sources,
                    pattern_type="co_occurrence",
                    language=lang,
                    frequency=freq,
                    contexts=self._co_occurrences[fn_names][:5],
                )
                log.info(
                    "composition_detected",
                    type="co_occurrence",
                    names=sorted(fn_names)[:3],
                    freq=freq,
                )
                promoted.append(pattern)

        # 2. Call-chain detection (pipelines)
        name_to_unit = {u.name: u for u in units}
        for unit in units:
            chain = self._trace_call_chain(unit, name_to_unit, max_depth=5)
            if len(chain) >= self.min_chain_length:
                chain_key = tuple(chain)
                self._call_chains[chain_key].append(context)
                freq = len(self._call_chains[chain_key])

                chain_frozenset = frozenset(chain)
                if freq >= self.min_co_occurrence and chain_frozenset not in self._promoted:
                    self._promoted.add(chain_frozenset)
                    sources = [
                        name_to_unit[n].source_code
                        for n in chain
                        if n in name_to_unit
                    ]
                    lang = units[0].language if units else "python"

                    pattern = ComposedPattern(
                        name="_to_".join(chain[:3]),
                        component_names=list(chain),
                        component_sources=sources,
                        pattern_type="pipeline",
                        language=lang,
                        frequency=freq,
                        contexts=self._call_chains[chain_key][:5],
                        call_chain=list(chain),
                    )
                    log.info(
                        "composition_detected",
                        type="pipeline",
                        chain=chain[:3],
                        freq=freq,
                    )
                    promoted.append(pattern)

        return promoted

    def _trace_call_chain(
        self,
        start_unit: ParsedUnit,
        name_to_unit: dict[str, ParsedUnit],
        max_depth: int = 5,
    ) -> list[str]:
        """
        Trace a call chain starting from a function.

        Example: if fetch_data calls parse_response which calls validate_data,
        returns ["fetch_data", "parse_response", "validate_data"].

        Args:
            start_unit: The function to start tracing from.
            name_to_unit: Mapping of function names to their ParsedUnit.
            max_depth: Maximum chain depth to prevent infinite loops.

        Returns:
            List of function names in the call chain.
        """
        chain: list[str] = [start_unit.name]
        visited: set[str] = {start_unit.name}
        current = start_unit

        for _ in range(max_depth):
            # Find the first call that refers to a local function
            next_fn: Optional[str] = None
            for call_name in current.calls:
                # Handle both simple names and dotted names
                simple_name = call_name.split(".")[-1]
                if simple_name in name_to_unit and simple_name not in visited:
                    next_fn = simple_name
                    break

            if next_fn is None:
                break

            chain.append(next_fn)
            visited.add(next_fn)
            current = name_to_unit[next_fn]

        return chain
