"""Report generation from :class:`AssessmentReport` — JSON, Markdown, HTML."""

from __future__ import annotations

from pathlib import Path

from ..models import AssessmentReport
from .html import render_html
from .markdown import render_markdown


def render_json(report: AssessmentReport) -> str:
    return report.model_dump_json(indent=2)


def save_reports(
    report: AssessmentReport,
    reports_dir: Path,
    markdown_path: Path | None = None,
    html_path: Path | None = None,
    json_path: Path | None = None,
) -> dict[str, Path]:
    """Write all three artifacts; explicit paths override the default
    directory layout ``reports/<id>.{md,html,json}``."""
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
    return written


__all__ = ["render_html", "render_json", "render_markdown", "save_reports"]
