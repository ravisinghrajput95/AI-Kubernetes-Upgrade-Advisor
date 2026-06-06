"""
k8s_assess/reporter.py

Phase 4 — Reporting
Generates:
  • Markdown report
  • HTML report  (self-contained, no external CDN)
  • Risk matrix  (extracted / formatted)
  • Executive summary
  • Upgrade runbook
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


REPORTS_DIR = Path(__file__).parent.parent / "reports"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def _slug(source: str, target: str) -> str:
    sv = source.replace(".", "")
    tv = target.replace(".", "")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"k8s_upgrade_{sv}_to_{tv}_{ts}"


# ── Markdown report ───────────────────────────────────────────────────────────

def save_markdown(content: str, source: str, target: str,
                  path: Path | None = None) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        path = REPORTS_DIR / f"{_slug(source, target)}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Kubernetes Upgrade Assessment\n\n")
        f.write(f"| | |\n|---|---|\n")
        f.write(f"| **Source Version** | `{source}` |\n")
        f.write(f"| **Target Version** | `{target}` |\n")
        f.write(f"| **Generated** | {_now()} |\n\n")
        f.write("---\n\n")
        f.write(content)
    return path


# ── HTML report ───────────────────────────────────────────────────────────────

_HTML_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  font-size: 15px; line-height: 1.7; color: #1a1a2e;
  background: #f4f6fb; padding: 0;
}
.page { max-width: 1000px; margin: 0 auto; padding: 40px 24px 80px; }

/* Header */
.report-header {
  background: linear-gradient(135deg, #0f3460 0%, #16213e 100%);
  color: #fff; border-radius: 12px; padding: 36px 40px; margin-bottom: 32px;
}
.report-header h1 { font-size: 26px; font-weight: 700; margin-bottom: 8px; }
.report-header .meta { display: flex; gap: 32px; flex-wrap: wrap; margin-top: 16px; }
.report-header .meta-item { }
.report-header .meta-item .label { font-size: 11px; opacity: 0.65; text-transform: uppercase; letter-spacing: 0.08em; }
.report-header .meta-item .value { font-size: 18px; font-weight: 600; font-family: monospace; }

/* Verdict banner */
.verdict { border-radius: 8px; padding: 16px 24px; font-size: 18px;
  font-weight: 700; margin-bottom: 28px; display: flex; align-items: center; gap: 12px; }
.verdict.approved   { background: #d4edda; color: #155724; border-left: 6px solid #28a745; }
.verdict.conditional{ background: #fff3cd; color: #856404; border-left: 6px solid #ffc107; }
.verdict.not-recommended { background: #f8d7da; color: #721c24; border-left: 6px solid #dc3545; }
.verdict .icon { font-size: 24px; }

/* Scores */
.scores { display: flex; gap: 20px; margin-bottom: 28px; flex-wrap: wrap; }
.score-card {
  flex: 1; min-width: 180px; background: #fff;
  border-radius: 10px; padding: 20px 24px;
  box-shadow: 0 2px 8px rgba(0,0,0,.07);
  text-align: center;
}
.score-card .score-label { font-size: 12px; text-transform: uppercase;
  letter-spacing: 0.08em; color: #666; margin-bottom: 8px; }
.score-card .score-value { font-size: 42px; font-weight: 800; }
.score-card .score-sub   { font-size: 12px; color: #888; margin-top: 4px; }
.score-green  { color: #28a745; }
.score-yellow { color: #ffc107; }
.score-red    { color: #dc3545; }

/* Risk matrix */
.risk-matrix { width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 14px; }
.risk-matrix th { background: #0f3460; color: #fff; padding: 10px 14px; text-align: left; }
.risk-matrix td { padding: 9px 14px; border-bottom: 1px solid #eee; vertical-align: top; }
.risk-matrix tr:nth-child(even) td { background: #f9fafc; }
.badge {
  display: inline-block; padding: 2px 10px; border-radius: 20px;
  font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
}
.badge-PASS        { background: #d4edda; color: #155724; }
.badge-GOOD        { background: #cce5ff; color: #004085; }
.badge-WARNING     { background: #fff3cd; color: #856404; }
.badge-HIGH-RISK   { background: #ffe5d0; color: #7d2a00; }
.badge-CRITICAL    { background: #f8d7da; color: #721c24; }

/* Content sections */
.section {
  background: #fff; border-radius: 10px; padding: 28px 32px;
  margin-bottom: 24px; box-shadow: 0 2px 8px rgba(0,0,0,.06);
}
.section h2 { font-size: 19px; color: #0f3460; margin-bottom: 16px;
  padding-bottom: 10px; border-bottom: 2px solid #e8ecf4; }
.section h3 { font-size: 16px; color: #1a1a2e; margin: 18px 0 8px; }
.section h4 { font-size: 14px; color: #444; margin: 14px 0 6px; }
p  { margin: 8px 0; }
ul, ol { margin: 8px 0 8px 24px; }
li { margin: 4px 0; }
code { background: #f0f2f8; padding: 2px 6px; border-radius: 4px;
       font-family: 'Courier New', monospace; font-size: 13px; }
pre { background: #1a1a2e; color: #e8ecf4; padding: 16px 20px;
      border-radius: 8px; overflow-x: auto; font-size: 13px;
      line-height: 1.5; margin: 12px 0; }
pre code { background: none; padding: 0; color: inherit; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 14px; }
table th { background: #f0f2f8; padding: 8px 12px; text-align: left;
           border: 1px solid #dde2ee; }
table td { padding: 8px 12px; border: 1px solid #dde2ee; vertical-align: top; }
blockquote { border-left: 4px solid #0f3460; padding: 8px 16px;
             color: #555; margin: 12px 0; background: #f7f8fc; }

/* Runbook */
.runbook-step {
  border: 1px solid #e0e4f0; border-radius: 8px; padding: 16px 20px;
  margin: 12px 0; background: #fafbff;
}
.runbook-step .step-num { font-size: 11px; text-transform: uppercase;
  color: #0f3460; font-weight: 700; letter-spacing: 0.08em; }
.runbook-step .step-title { font-size: 15px; font-weight: 600; margin: 4px 0; }

/* TOC */
.toc { background: #f7f8fc; border-radius: 8px; padding: 20px 24px; margin-bottom: 28px; }
.toc h3 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em;
          color: #666; margin-bottom: 12px; }
.toc ol { margin-left: 20px; }
.toc li { margin: 4px 0; font-size: 14px; }
.toc a { color: #0f3460; text-decoration: none; }
.toc a:hover { text-decoration: underline; }

hr { border: none; border-top: 1px solid #e8ecf4; margin: 24px 0; }

/* Citation chips — rendered from (Title) inline refs */
.cite-chip {
  display: inline-flex; align-items: center; gap: 4px;
  background: #eef2ff; color: #3730a3; border: 1px solid #c7d2fe;
  border-radius: 4px; padding: 1px 7px; font-size: 12px;
  font-weight: 500; white-space: nowrap; vertical-align: middle;
  margin: 0 1px;
}
.cite-chip::before { content: "📄 "; font-size: 11px; }

/* Evidence Used section collapsible doc-id list */
details.doc-ids {
  margin-top: 8px; font-size: 13px; color: #555;
}
details.doc-ids summary {
  cursor: pointer; color: #0f3460; font-size: 12px;
  list-style: none; display: inline-flex; align-items: center; gap: 4px;
}
details.doc-ids summary::before { content: "▶ "; font-size: 10px; }
details.doc-ids[open] summary::before { content: "▼ "; }
details.doc-ids ul { margin: 6px 0 0 16px; }
"""

def _extract_verdict(content: str) -> tuple[str, str]:
    """Try to extract APPROVED/CONDITIONAL/NOT RECOMMENDED from the AI output."""
    for pat in [
        r"UPGRADE DECISION[:\s]+([A-Z ]+)",
        r"(APPROVED|CONDITIONAL|NOT RECOMMENDED)",
    ]:
        m = re.search(pat, content, re.IGNORECASE)
        if m:
            v = m.group(1).strip().upper()
            if "NOT" in v or "NOT RECOMMENDED" in v:
                return "NOT RECOMMENDED", "not-recommended"
            if "CONDITIONAL" in v:
                return "CONDITIONAL", "conditional"
            if "APPROVED" in v:
                return "APPROVED", "approved"
    return "CONDITIONAL", "conditional"


def _extract_approval_basis(content: str) -> str:
    """Extract the Approval Basis sentence from the AI output."""
    m = re.search(
        r"\*{0,2}Approval Basis[:\*]{0,3}\s*(.+?)(?:\n|$)",
        content, re.IGNORECASE
    )
    if m:
        basis = m.group(1).strip().strip('"').strip("*").strip()
        if len(basis) > 20:
            return basis
    return ""

def _extract_score(content: str, label: str) -> str:
    """Extract a numeric score from AI output."""
    patterns = [
        rf"{label}[^\d]{{0,20}}(\d{{1,3}})\s*/\s*100",
        rf"{label}[^\d]{{0,20}}(\d{{1,3}})\s*%",
        rf"{label}[^\d]{{0,20}}(\d{{1,3}})",
    ]
    for pat in patterns:
        m = re.search(pat, content, re.IGNORECASE)
        if m:
            return m.group(1)
    return "N/A"

def _score_color(score_str: str) -> str:
    try:
        s = int(score_str)
        if s >= 75: return "score-green"
        if s >= 50: return "score-yellow"
        return "score-red"
    except ValueError:
        return "score-yellow"


def _render_citations(html: str) -> str:
    """
    Post-process rendered HTML to turn citation patterns into styled chips.

    Patterns handled:
      (Kubernetes 1.29 Release Notes)       → chip with doc title
      (cert-manager Supported Releases)     → chip
      ([DOC 3])                             → small grey technical ref (kept but dimmed)
      ([DOC 3] Title)                       → chip using title

    The Evidence Used section is left mostly intact but [DOC N] refs there
    are wrapped in a collapsible <details> block.
    """
    # 1. ([DOC N] Some Title) → chip using the title
    html = re.sub(
        r'\(\[DOC\s*\d+\]\s+([^)]{5,80})\)',
        lambda m: f'<span class="cite-chip">{m.group(1).strip()}</span>',
        html
    )
    # 2. Bare ([DOC N]) → small dimmed technical ref
    html = re.sub(
        r'\(\[DOC\s*(\d+)\]\)',
        r'<span style="font-size:11px;color:#999;vertical-align:super">[§\1]</span>',
        html
    )
    # 3. (Some Citation Title) patterns — only in likely evidence contexts
    #    Match parenthetical that looks like a document title (capitalised words, no punctuation)
    html = re.sub(
        r'\(([A-Z][A-Za-z0-9 \-:/.]+(?:Release Notes|Compatibility|Guide|Matrix|Deprecation|'
        r'Changelog|Releases|Documentation|Upgrade|Support[a-z]*))\)',
        lambda m: f'<span class="cite-chip">{m.group(1).strip()}</span>',
        html
    )
    return html

def _md_to_html_basic(md: str) -> str:
    """
    Minimal Markdown → HTML converter (no external deps).
    Handles: headers, bold, inline code, code blocks, tables, lists, hr, paragraphs.
    """
    lines = md.split("\n")
    html_lines: list[str] = []
    in_code   = False
    in_ul     = False
    in_ol     = False
    in_table  = False

    def close_list():
        nonlocal in_ul, in_ol
        if in_ul:
            html_lines.append("</ul>")
            in_ul = False
        if in_ol:
            html_lines.append("</ol>")
            in_ol = False

    def close_table():
        nonlocal in_table
        if in_table:
            html_lines.append("</tbody></table>")
            in_table = False

    def inline(text: str) -> str:
        # bold+italic
        text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
        # bold
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
        # italic
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        # inline code
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        # links
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
        return text

    table_header_done = False

    for line in lines:
        # Code fences
        if line.startswith("```"):
            if not in_code:
                close_list(); close_table()
                lang = line[3:].strip()
                html_lines.append(f'<pre><code class="language-{lang}">')
                in_code = True
            else:
                html_lines.append("</code></pre>")
                in_code = False
            continue
        if in_code:
            html_lines.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            continue

        # Horizontal rule
        if re.match(r"^[-*_]{3,}$", line.strip()):
            close_list(); close_table()
            html_lines.append("<hr>")
            continue

        # Headers
        hm = re.match(r"^(#{1,6})\s+(.*)", line)
        if hm:
            close_list(); close_table()
            level = len(hm.group(1))
            text  = inline(hm.group(2))
            anchor = re.sub(r"[^a-z0-9]+", "-", hm.group(2).lower()).strip("-")
            html_lines.append(f'<h{level} id="{anchor}">{text}</h{level}>')
            continue

        # Tables
        if "|" in line and line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # separator row
            if all(re.match(r"^[-:]+$", c) for c in cells if c):
                if not in_table:
                    # first row was header
                    header_row = html_lines.pop()
                    header_cells = re.findall(r"<td>(.*?)</td>", header_row)
                    html_lines.append('<table class="risk-matrix"><thead><tr>')
                    for hc in header_cells:
                        html_lines.append(f"<th>{hc}</th>")
                    html_lines.append("</tr></thead><tbody>")
                    in_table = True
                continue
            if not in_table:
                # start table (no separator row seen yet)
                close_list()
                html_lines.append('<table><thead><tr>')
                for c in cells:
                    html_lines.append(f"<th>{inline(c)}</th>")
                html_lines.append("</tr></thead><tbody>")
                in_table = True
                continue
            # data row — badge-ify status cells
            html_lines.append("<tr>")
            for i, c in enumerate(cells):
                c_inline = inline(c)
                # try to apply badge to second column if it looks like a status
                upper = c.strip().upper()
                badge_map = {
                    "PASS": "PASS", "GOOD": "GOOD", "WARNING": "WARNING",
                    "HIGH RISK": "HIGH-RISK", "CRITICAL": "CRITICAL",
                }
                if upper in badge_map and i in (1, 2):
                    c_inline = f'<span class="badge badge-{badge_map[upper]}">{c}</span>'
                html_lines.append(f"<td>{c_inline}</td>")
            html_lines.append("</tr>")
            continue
        else:
            close_table()

        # Ordered list
        olm = re.match(r"^(\d+)\.\s+(.*)", line)
        if olm:
            close_list() if in_ul else None
            if not in_ol:
                html_lines.append("<ol>")
                in_ol = True
            html_lines.append(f"<li>{inline(olm.group(2))}</li>")
            continue

        # Unordered list
        ulm = re.match(r"^[-*+]\s+(.*)", line)
        if ulm:
            if in_ol:
                close_list()
            if not in_ul:
                html_lines.append("<ul>")
                in_ul = True
            html_lines.append(f"<li>{inline(ulm.group(1))}</li>")
            continue

        # Close lists if indentation dropped
        if line.strip() == "":
            close_list()
            close_table()
            html_lines.append("")
            continue

        close_list()
        close_table()
        # Blockquote
        if line.startswith("> "):
            html_lines.append(f"<blockquote>{inline(line[2:])}</blockquote>")
            continue
        # Plain paragraph text
        html_lines.append(f"<p>{inline(line)}</p>")

    close_list()
    close_table()
    return "\n".join(html_lines)


def save_html(content: str, source: str, target: str,
              path: Path | None = None) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        path = REPORTS_DIR / f"{_slug(source, target)}.html"

    verdict_text, verdict_class = _extract_verdict(content)
    readiness  = _extract_score(content, "Readiness Score")
    confidence = _extract_score(content, "Confidence")
    approval_basis = _extract_approval_basis(content)

    verdict_icons = {
        "approved": "✅", "conditional": "⚠️", "not-recommended": "🚫"
    }
    icon = verdict_icons.get(verdict_class, "⚠️")

    approval_basis_html = (
        f'<p style="font-size:13px;font-weight:400;margin-top:10px;opacity:.85;">'
        f'<strong>Approval Basis:</strong> {approval_basis}</p>'
    ) if approval_basis else ""

    body_html = _md_to_html_basic(content)
    body_html = _render_citations(body_html)

    # Build table of contents from h2/h3 headings
    toc_items = re.findall(r'<h[23] id="([^"]+)">([^<]+)</h', body_html)
    toc_html = "<ol>" + "".join(
        f'<li><a href="#{slug}">{title}</a></li>' for slug, title in toc_items
    ) + "</ol>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>K8s Upgrade Assessment: {source} → {target}</title>
  <style>{_HTML_CSS}</style>
</head>
<body>
<div class="page">

  <div class="report-header">
    <h1>⎈ Kubernetes Upgrade Assessment</h1>
    <p style="opacity:.75;margin-top:6px">AI-powered upgrade feasibility &amp; risk analysis</p>
    <div class="meta">
      <div class="meta-item">
        <div class="label">Source Version</div>
        <div class="value">{source}</div>
      </div>
      <div class="meta-item">
        <div class="label">Target Version</div>
        <div class="value">{target}</div>
      </div>
      <div class="meta-item">
        <div class="label">Generated</div>
        <div class="value" style="font-size:14px;padding-top:4px">{_now()}</div>
      </div>
    </div>
  </div>

  <div class="verdict {verdict_class}">
    <span class="icon">{icon}</span>
    <span>Upgrade Decision: <strong>{verdict_text}</strong></span>
  </div>

  <div class="scores">
    <div class="score-card">
      <div class="score-label">Readiness Score</div>
      <div class="score-value {_score_color(readiness)}">{readiness}<span style="font-size:22px">/100</span></div>
      <div class="score-sub">Overall upgrade readiness</div>
    </div>
    <div class="score-card">
      <div class="score-label">Confidence Score</div>
      <div class="score-value {_score_color(confidence)}">{confidence}<span style="font-size:22px">%</span></div>
      <div class="score-sub">Assessment confidence</div>
    </div>
  </div>

  <div class="toc">
    <h3>Table of Contents</h3>
    {toc_html}
  </div>

  <div class="section">
    {body_html}
  </div>

</div>
</body>
</html>"""

    path.write_text(html, encoding="utf-8")
    return path


# ── Main entry ────────────────────────────────────────────────────────────────

def save_reports(content: str, source: str, target: str,
                 md_path: Path | None = None,
                 html_path: Path | None = None) -> tuple[Path, Path]:
    md   = save_markdown(content, source, target, md_path)
    html = save_html(content, source, target, html_path)
    return md, html
