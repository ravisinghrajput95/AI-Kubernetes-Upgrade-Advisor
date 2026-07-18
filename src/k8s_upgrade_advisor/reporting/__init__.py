"""Report generation from :class:`AssessmentReport` — JSON, Markdown, HTML."""

from __future__ import annotations

from pathlib import Path

from ..models import AssessmentReport
from .html import render_html
from .markdown import render_markdown


def render_json(report: AssessmentReport) -> str:
    return report.model_dump_json(indent=2)


def prune_reports(reports_dir: Path, keep: int) -> int:
    """Retention: keep the newest ``keep`` assessments (each is an md/html/json
    triplet grouped by report id); delete older artifacts. Returns the number
    of assessments removed. ``keep=0`` disables pruning."""
    if keep <= 0 or not reports_dir.is_dir():
        return 0
    by_id: dict[str, list[Path]] = {}
    for path in reports_dir.iterdir():
        if path.is_file() and path.stem.startswith("assess-"):
            by_id.setdefault(path.stem, []).append(path)
    if len(by_id) <= keep:
        return 0
    # Report ids embed their creation timestamp — lexicographic sort is
    # chronological, and unlike mtime it survives file copies/restores.
    stale = sorted(by_id)[: len(by_id) - keep]
    for report_id in stale:
        for path in by_id[report_id]:
            path.unlink(missing_ok=True)
    return len(stale)


def save_reports(
    report: AssessmentReport,
    reports_dir: Path,
    markdown_path: Path | None = None,
    html_path: Path | None = None,
    json_path: Path | None = None,
    keep: int = 0,
) -> dict[str, Path]:
    """Write all three artifacts; explicit paths override the default
    directory layout ``reports/<id>.{md,html,json}``. ``keep`` applies the
    retention policy after writing (0 = unlimited)."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "markdown": (markdown_path or reports_dir / f"{report.id}.md", render_markdown(report)),
        "html": (html_path or reports_dir / f"{report.id}.html", render_html(report)),
        "json": (json_path or reports_dir / f"{report.id}.json", render_json(report)),
    }
    written: dict[str, Path] = {}
    for kind, (path, content) in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written[kind] = path
    prune_reports(reports_dir, keep)
    return written


__all__ = ["prune_reports", "render_html", "render_json", "render_markdown", "save_reports"]
