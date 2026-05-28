"""Pluggable retriever interface for grounding the triage agent.

Every retriever returns a list of `Chunk` objects. The default `NoOpRetriever`
returns an empty list, which is appropriate until real knowledge sources are
configured in `sources.yml`.

To add a new source:
1. Add a new entry to `sources.yml` with a `type:` field.
2. Implement a connector class with a `retrieve(query: str, k: int) -> list[Chunk]`
   method.
3. Register it in `_load_sources()` below.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

import yaml

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Chunk:
    source: str
    text: str
    url: str | None = None


class Retriever(Protocol):
    def retrieve(self, query: str, k: int = 6) -> list[Chunk]: ...


class NoOpRetriever:
    """Returns no grounding context. Default until sources are configured."""

    def retrieve(self, query: str, k: int = 6) -> list[Chunk]:  # noqa: ARG002
        return []


class CompositeRetriever:
    """Fans a query out across multiple underlying retrievers and merges results."""

    def __init__(self, retrievers: Iterable[Retriever], per_source_k: int = 3):
        self._retrievers = list(retrievers)
        self._k = per_source_k

    def retrieve(self, query: str, k: int = 6) -> list[Chunk]:
        all_chunks: list[Chunk] = []
        for r in self._retrievers:
            try:
                all_chunks.extend(r.retrieve(query, self._k))
            except Exception:  # pragma: no cover - resiliency
                log.exception("Retriever %s failed; continuing without it", type(r).__name__)
        # Naive ranking: keep insertion order, cap at k.
        return all_chunks[:k]


def load_retriever(config_path: Path | None = None) -> Retriever:
    """Build a retriever from `sources.yml`.

    Returns NoOpRetriever if the file is missing, empty, or all sources are disabled.
    """
    cfg_path = config_path or Path(__file__).parent / "sources.yml"
    if not cfg_path.exists():
        log.info("sources.yml not found at %s; using NoOpRetriever", cfg_path)
        return NoOpRetriever()

    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    sources = [s for s in (cfg.get("sources") or []) if s.get("enabled", True)]
    if not sources:
        log.info("No enabled knowledge sources; using NoOpRetriever")
        return NoOpRetriever()

    defaults = cfg.get("defaults", {}) or {}
    per_source_k = int(defaults.get("max_chunks", 6)) // max(1, len(sources)) or 1

    connectors: list[Retriever] = []
    for src in sources:
        kind = src.get("type")
        try:
            connectors.append(_build_connector(kind, src))
        except NotImplementedError:
            log.warning("Source %r of type %r not implemented yet; skipping",
                        src.get("name"), kind)
        except Exception:
            log.exception("Failed to build connector for source %r; skipping",
                          src.get("name"))

    if not connectors:
        return NoOpRetriever()
    return CompositeRetriever(connectors, per_source_k=per_source_k)


def _build_connector(kind: str, cfg: dict) -> Retriever:
    """Factory for source types. Extend as new sources are needed."""
    if kind == "github_repo":
        # Implementation hint: use clients.github_app to download files, glob filter,
        # then return matching chunks. Kept as TODO until the team supplies a repo.
        raise NotImplementedError("github_repo connector pending team input")
    if kind == "ado_wiki":
        raise NotImplementedError("ado_wiki connector pending team input")
    if kind == "sharepoint":
        raise NotImplementedError("sharepoint connector pending team input")
    if kind == "azure_ai_search":
        raise NotImplementedError("azure_ai_search connector pending team input")
    raise NotImplementedError(f"Unknown source type: {kind!r}")


# Convenience for callers that just want an env-overridable retriever.
def default_retriever() -> Retriever:
    override = os.environ.get("WAC_TRIAGE_RETRIEVER")
    if override == "noop":
        return NoOpRetriever()
    return load_retriever()
