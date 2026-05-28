"""Tiny JSON-on-disk store for "which issues have we already triaged locally"."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

_STATE_DIR = Path(__file__).resolve().parent / "state"
_SEEN = _STATE_DIR / "seen.json"


def _load() -> dict[str, str]:
    if not _SEEN.exists():
        return {}
    try:
        return json.loads(_SEEN.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict[str, str]) -> None:
    _STATE_DIR.mkdir(exist_ok=True)
    _SEEN.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def is_seen(issue_number: int) -> bool:
    return str(issue_number) in _load()


def mark_seen(issue_number: int, when_iso: str) -> None:
    data = _load()
    data[str(issue_number)] = when_iso
    _save(data)


def list_seen() -> Iterable[tuple[int, str]]:
    for k, v in sorted(_load().items(), key=lambda kv: int(kv[0])):
        yield int(k), v


def reset() -> None:
    if _SEEN.exists():
        _SEEN.unlink()
