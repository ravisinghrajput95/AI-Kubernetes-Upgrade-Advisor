"""Command-line interface.

Subcommands mirror the platform phases:

  collect    fetch upgrade documentation into kb/raw/
  build-kb   chunk + embed + index the knowledge base
  assess     analyze a cluster (live or snapshot) and write reports
  snapshot   collect a cluster snapshot to a file for offline assessment
  serve      run the API server + web UI

CI gating: ``assess --fail-on`` maps the verdict to the exit code so a
pipeline can block merges on upgrade readiness.

Exit codes: 0 ok · 20 readiness gate failed · 69 dependency unavailable ·
78 bad configuration · 1 unexpected error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import get_settings
from .errors import AdvisorError
from .models import Verdict, validate_upgrade_pair
from .observability import configure_logging, get_logger

log = get_logger(__name__)

GATE_ORDER = [Verdict.READY, Verdict.READY_WITH_CAUTIONS, Verdict.NOT_READY, Verdict.BLOCKED]
EXIT_GATE_FAILED = 20


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="k8s-upgrade-advisor",
        description="AI Kubernetes upgrade intelligence: deterministic compatibility "
        "analysis + RAG-grounded planning.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--log-level", default=None, help="override log level (DEBUG/INFO/…)")
    parser.add_argument("--log-json", action="store_true", help="JSON log output")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_versions(p: argparse.ArgumentParser) -> None:
        p.add_argument("-s", "--source", required=True, help="current version, e.g. 1.28")
        p.add_argument("-t", "--target", required=True, help="target version, e.g. 1.31")

    p_collect = sub.add_parser("collect", help="fetch upgrade documentation")
    add_versions(p_collect)
    p_collect.add_argument(
        "--components", nargs="*", metavar="KEY", help="limit component docs (default: all)"
    )
    p_collect.add_argument("--force", action="store_true", help="ignore cache ages")

    p_build = sub.add_parser("build-kb", help="chunk + embed + index the knowledge base")
    add_versions(p_build)

    p_snapshot = sub.add_parser("snapshot", help="save a cluster snapshot for offline use")
    p_snapshot.add_argument("output", help="snapshot file path (JSON)")
    p_snapshot.add_argument("--context", help="kubeconfig context")
    p_snapshot.add_argument("--kubeconfig", help="kubeconfig path")

    p_assess = sub.add_parser("assess", help="assess upgrade readiness")
    add_versions(p_assess)
    p_assess.add_argument(
        "--snapshot", metavar="FILE", help="assess a saved snapshot instead of live cluster"
    )
    p_assess.add_argument(
        "--save-snapshot", metavar="FILE", help="also save the collected snapshot"
    )
    p_assess.add_argument("--context", help="kubeconfig context")
    p_assess.add_argument("--kubeconfig", help="kubeconfig path")
    p_assess.add_argument(
        "--dry-run", action="store_true", help="deterministic analysis only, no LLM call"
    )
    p_assess.add_argument("--json", action="store_true", help="print report JSON to stdout")
    p_assess.add_argument("--md", metavar="FILE", help="markdown output path")
    p_assess.add_argument("--html", metavar="FILE", help="HTML output path")
    p_assess.add_argument("--no-save", action="store_true", help="do not write report files")
    p_assess.add_argument(
        "--fail-on",
        choices=["never", "blocked", "not-ready", "cautions"],
        default="not-ready",
        help="verdict threshold that fails the exit code (default: not-ready)",
    )

    p_serve = sub.add_parser("serve", help="run the API server and web UI")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(
        level=args.log_level or settings.observability.log_level,
        json_output=args.log_json or settings.observability.log_json,
    )
    try:
        return _dispatch(args, settings)
    except AdvisorError as exc:
        log.error("command_failed", command=args.command, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        return 130


def _dispatch(args: argparse.Namespace, settings) -> int:
    if args.command == "collect":
        return _cmd_collect(args, settings)
    if args.command == "build-kb":
        return _cmd_build_kb(args, settings)
    if args.command == "snapshot":
        return _cmd_snapshot(args)
    if args.command == "assess":
        return _cmd_assess(args, settings)
    if args.command == "serve":
        return _cmd_serve(args, settings)
    raise AdvisorError(f"unknown command {args.command}")


def _cmd_collect(args, settings) -> int:
    from .knowledge import DocumentFetcher, all_sources

    source, target = validate_upgrade_pair(args.source, args.target)
    sources = all_sources(source, target, components=args.components)
    fetcher = DocumentFetcher(settings.knowledge, settings.paths.raw_docs_dir)
    docs = fetcher.fetch_all(sources, force=args.force)
    print(f"collected {len(docs)}/{len(sources)} documents → {settings.paths.raw_docs_dir}")
    return 0 if docs else 1


def _cmd_build_kb(args, settings) -> int:
    import json as _json

    from .knowledge import KnowledgeStore, chunk_documents, select_backend
    from .knowledge.fetcher import RawDocument

    source, target = validate_upgrade_pair(args.source, args.target)
    raw_dir = settings.paths.raw_docs_dir
    paths = sorted(raw_dir.glob("*.json")) if raw_dir.is_dir() else []
    if not paths:
        raise AdvisorError(f"no raw documents in {raw_dir} — run 'collect' first")

    docs = []
    for path in paths:
        try:
            docs.append(RawDocument.load(path))
        except (KeyError, TypeError, _json.JSONDecodeError):
            log.warning("raw_doc_skipped", path=str(path))
    chunks = chunk_documents(docs, settings.knowledge.chunk_chars, settings.knowledge.chunk_overlap)
    embedder = select_backend(
        settings.knowledge.embedding_backend, settings.knowledge.embedding_model
    )
    store = KnowledgeStore.build(
        chunks,
        embedder,
        settings.paths.kb_dir,
        source.minor_str,
        target.minor_str,
        settings.knowledge.chunk_chars,
        settings.knowledge.chunk_overlap,
        doc_count=len(docs),
    )
    print(
        f"knowledge base built: {store.manifest.chunk_count} chunks from "
        f"{store.manifest.doc_count} docs (embedder: {store.manifest.embedder})"
    )
    return 0


def _cmd_snapshot(args) -> int:
    from .collectors import collect_cluster_snapshot, save_snapshot

    snapshot = collect_cluster_snapshot(context=args.context, kubeconfig=args.kubeconfig)
    path = save_snapshot(snapshot, args.output)
    print(f"snapshot saved: {path} ({snapshot.commands_ok}/{snapshot.commands_total} commands ok)")
    return 0


def _cmd_assess(args, settings) -> int:
    from .collectors import collect_cluster_snapshot, load_snapshot, save_snapshot
    from .reporting import render_json, save_reports
    from .service import assess

    if args.snapshot:
        snapshot = load_snapshot(args.snapshot)
    else:
        snapshot = collect_cluster_snapshot(context=args.context, kubeconfig=args.kubeconfig)
        if args.save_snapshot:
            save_snapshot(snapshot, args.save_snapshot)

    report = assess(snapshot, args.source, args.target, settings, dry_run=args.dry_run)

    if args.json:
        print(render_json(report))
    else:
        r = report.readiness
        print(
            f"\nverdict: {r.verdict.value}  readiness: {r.score}/100"
            f"{f' (cap {r.cap})' if r.cap < 100 else ''}  confidence: {r.confidence}/100"
        )
        print(f"findings: {len(report.findings)} ({len(report.blocking_findings)} blocking)")
    if not args.no_save:
        written = save_reports(
            report,
            settings.paths.reports_dir,
            markdown_path=Path(args.md) if args.md else None,
            html_path=Path(args.html) if args.html else None,
            keep=settings.paths.reports_keep,
        )
        for kind, path in written.items():
            print(f"  {kind}: {path}")

    return _gate(report.readiness.verdict, args.fail_on)


def _gate(verdict: Verdict, fail_on: str) -> int:
    if fail_on == "never":
        return 0
    threshold = {
        "blocked": Verdict.BLOCKED,
        "not-ready": Verdict.NOT_READY,
        "cautions": Verdict.READY_WITH_CAUTIONS,
    }[fail_on]
    if GATE_ORDER.index(verdict) >= GATE_ORDER.index(threshold):
        log.info("readiness_gate_failed", verdict=verdict.value, threshold=fail_on)
        return EXIT_GATE_FAILED
    return 0


def _cmd_serve(args, settings) -> int:
    try:
        import uvicorn
    except ImportError as exc:
        raise AdvisorError(
            "API extras not installed — pip install 'k8s-upgrade-advisor[api]'"
        ) from exc
    from .api.app import create_app

    uvicorn.run(
        create_app(settings),
        host=args.host or settings.server.host,
        port=args.port or settings.server.port,
        log_config=None,  # structlog owns logging
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
