"""Snapshot persistence — save a collected cluster to JSON and load it back.

This is what enables air-gapped assessment: collect where kubectl works,
assess where the KB and LLM access live.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from ..errors import CollectionError
from ..models.cluster import ClusterSnapshot
from ..observability import get_logger

log = get_logger(__name__)


def save_snapshot(snapshot: ClusterSnapshot, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    log.info("snapshot_saved", path=str(out), commands=snapshot.commands_total)
    return out


def load_snapshot(path: str | Path) -> ClusterSnapshot:
    p = Path(path)
    if not p.is_file():
        raise CollectionError(f"snapshot file not found: {p}")
    try:
        snapshot = ClusterSnapshot.model_validate_json(p.read_text(encoding="utf-8"))
    except (ValidationError, UnicodeDecodeError) as exc:
        raise CollectionError(f"invalid snapshot file {p}: {exc}") from exc
    snapshot.source = "file"
    log.info("snapshot_loaded", path=str(p), commands=snapshot.commands_total)
    return snapshot
