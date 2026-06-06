#!/usr/bin/env python3
"""
k8s-upgrade-assess  —  AI-powered Kubernetes upgrade risk assessment
with RAG-backed knowledge base (release notes + operator compatibility matrices)

Usage:
  python main.py --source 1.27 --target 1.29
  python main.py -s 1.27 -t 1.29 --collect-only
  python main.py -s 1.27 -t 1.29 --build-kb
  python main.py -s 1.27 -t 1.29 --assess

Phases:
  1. collect   — scrape release notes + compatibility docs → kb/raw/
  2. build-kb  — chunk + embed + FAISS index              → kb/
  3. assess    — kubectl + RAG + OpenAI                   → reports/

Default (no phase flags): run all three phases end-to-end.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── ANSI ─────────────────────────────────────────────────────────────────────
RESET  = "\033[0m"; BOLD   = "\033[1m"; DIM    = "\033[2m"
RED    = "\033[91m"; YELLOW = "\033[93m"; GREEN  = "\033[92m"
CYAN   = "\033[96m"; BLUE   = "\033[94m"; WHITE  = "\033[97m"

def c(colour: str, text: str) -> str:  return f"{colour}{text}{RESET}"
def banner_line(ch: str = "━", w: int = 72) -> str: return c(CYAN, ch * w)


def print_banner(source: str, target: str) -> None:
    w = 72
    print()
    print(banner_line())
    print(c(BOLD + CYAN, "  ⎈  Kubernetes Upgrade Assessment".center(w)))
    print(banner_line())
    print(c(DIM, f"  Source  : {source}"))
    print(c(DIM, f"  Target  : {target}"))
    print(c(DIM, f"  Time    : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"))
    print(banner_line())
    print()


def print_phase(n: int, title: str) -> None:
    print()
    print(c(BOLD + BLUE, f"  ┌── Phase {n}: {title}"))
    print(c(BLUE, f"  └{'─' * (len(title) + 12)}"))


def print_step(msg: str) -> None: print(c(CYAN,   f"    ▸ {msg}"), flush=True)
def print_ok(msg: str)   -> None: print(c(GREEN,  f"    ✔  {msg}"))
def print_warn(msg: str) -> None: print(c(YELLOW, f"    ⚠  {msg}"))
def print_err(msg: str)  -> None: print(c(RED,    f"    ✖  {msg}"))


# ── Version helpers ───────────────────────────────────────────────────────────

def parse_minor(v: str) -> int:
    try:
        return int(v.lstrip("v").split(".")[1])
    except (IndexError, ValueError):
        return -1


def validate_versions(source: str, target: str) -> list[str]:
    warnings = []
    sm, tm = parse_minor(source), parse_minor(target)
    if sm < 0: warnings.append(f"Cannot parse source version '{source}'")
    if tm < 0: warnings.append(f"Cannot parse target version '{target}'")
    if sm >= 0 and tm >= 0:
        if tm <= sm:
            warnings.append("Target version is not newer than source")
        elif tm - sm > 2:
            warnings.append(
                f"Skipping {tm - sm} minor versions — Kubernetes only supports "
                "upgrading one minor version at a time"
            )
    return warnings


# ── kubectl collection ────────────────────────────────────────────────────────

KUBECTL_COMMANDS: dict[str, list[str]] = {
    "version":             ["version", "--output=json"],
    "nodes":               ["get", "nodes", "-o", "wide"],
    "nodes_yaml":          ["get", "nodes", "-o", "yaml"],
    "namespaces":          ["get", "ns"],
    "api_resources":       ["api-resources", "--verbs=list", "-o", "wide"],
    "api_services":        ["get", "apiservices"],
    "all_resources":       ["get", "all", "-A"],
    "deployments":         ["get", "deploy", "-A", "-o", "wide"],
    "statefulsets":        ["get", "sts", "-A", "-o", "wide"],
    "daemonsets":          ["get", "ds", "-A", "-o", "wide"],
    "jobs":                ["get", "jobs", "-A"],
    "cronjobs":            ["get", "cronjobs", "-A"],
    "crds":                ["get", "crd"],
    "crds_yaml":           ["get", "crd", "-o", "yaml"],
    "validating_webhooks": ["get", "validatingwebhookconfigurations"],
    "mutating_webhooks":   ["get", "mutatingwebhookconfigurations"],
    "storage_classes":     ["get", "sc"],
    "pvs":                 ["get", "pv"],
    "pvcs":                ["get", "pvc", "-A"],
    "top_nodes":           ["top", "nodes"],
    "top_pods":            ["top", "pods", "-A"],
    "pod_security":        ["get", "psp"],
    "cluster_info":        ["cluster-info"],
}

def collect_cluster(verbose: bool = False) -> dict:
    data: dict = {}
    for key, cmd in KUBECTL_COMMANDS.items():
        try:
            r = subprocess.run(
                ["kubectl"] + cmd,
                capture_output=True, text=True, timeout=45
            )
            data[key] = {
                "stdout": r.stdout.strip(),
                "stderr": r.stderr.strip(),
                "ok": bool(r.stdout.strip()),
            }
            if verbose and r.stdout.strip():
                print_ok(f"  kubectl {' '.join(cmd[:3])}")
        except FileNotFoundError:
            data[key] = {"stdout": "", "stderr": "kubectl not found", "ok": False}
            break
        except subprocess.TimeoutExpired:
            data[key] = {"stdout": "", "stderr": "timeout", "ok": False}
    return data


def cluster_summary(data: dict) -> str:
    """Combine key outputs into a short text for query generation."""
    parts = []
    for key in ("version", "nodes", "all_resources", "crds",
                "validating_webhooks", "mutating_webhooks"):
        d = data.get(key, {})
        if d.get("stdout"):
            parts.append(d["stdout"][:2000])
    return "\n".join(parts)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="k8s-upgrade-assess",
        description="AI-powered Kubernetes upgrade risk assessment with RAG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Phase control:
          (default)         run all phases end-to-end
          --collect-only    Phase 1 only: scrape docs to kb/raw/
          --build-kb        Phase 2 only: build FAISS index from kb/raw/
          --assess          Phase 3 only: kubectl + RAG + OpenAI assessment

        Examples:
          python main.py -s 1.27 -t 1.29
          python main.py -s 1.27 -t 1.29 --collect-only
          python main.py -s 1.27 -t 1.29 --build-kb
          python main.py -s 1.27 -t 1.29 --assess --cluster-data cluster.json

        Environment:
          OPENAI_API_KEY    Required for assessment phase
          KUBECONFIG        Optional kubeconfig path
        """),
    )
    parser.add_argument("-s", "--source", required=True,
                        help="Current Kubernetes version (e.g. 1.27)")
    parser.add_argument("-t", "--target", required=True,
                        help="Target Kubernetes version (e.g. 1.29)")

    # Phase selectors
    pg = parser.add_argument_group("Phase control")
    pg.add_argument("--collect-only", action="store_true",
                    help="Run Phase 1 (doc collection) only")
    pg.add_argument("--build-kb", action="store_true",
                    help="Run Phase 2 (KB build) only")
    pg.add_argument("--assess", action="store_true",
                    help="Run Phase 3 (assessment) only — requires existing KB")
    pg.add_argument("--force-collect", action="store_true",
                    help="Re-scrape even if cached docs exist")
    pg.add_argument("--force-kb", action="store_true",
                    help="Rebuild FAISS index even if it exists")
    pg.add_argument("--components", nargs="+", metavar="NAME",
                    help="Limit collection to specific components "
                         "(e.g. cert_manager ingress_nginx)")

    # Cluster data
    cg = parser.add_argument_group("Cluster data")
    cg.add_argument("--cluster-data", metavar="FILE",
                    help="Load pre-collected cluster JSON (skip kubectl)")
    cg.add_argument("--dump-cluster", metavar="FILE",
                    help="Save collected cluster data to JSON")
    cg.add_argument("--skip-collect-cluster", action="store_true",
                    help="Skip kubectl entirely (KB-only assessment)")

    # Output
    og = parser.add_argument_group("Output")
    og.add_argument("--md",   metavar="FILE", help="Markdown report output path")
    og.add_argument("--html", metavar="FILE", help="HTML report output path")
    og.add_argument("--no-save",  action="store_true", help="Do not save reports")
    og.add_argument("--no-color", action="store_true", help="Disable ANSI colour")
    og.add_argument("--dry-run",  action="store_true",
                    help="Build prompt but do not call OpenAI")
    og.add_argument("--top-k", type=int, default=20,
                    help="Number of KB chunks to retrieve (default: 20)")
    og.add_argument("--model", default="gpt-4o",
                    help="OpenAI model (default: gpt-4o)")

    args = parser.parse_args()

    # Disable colour
    if args.no_color or not sys.stdout.isatty():
        global RESET, BOLD, DIM, RED, YELLOW, GREEN, CYAN, BLUE, WHITE
        RESET = BOLD = DIM = RED = YELLOW = GREEN = CYAN = BLUE = WHITE = ""

    # Determine which phases to run
    run_collect = not (args.build_kb or args.assess)
    run_build   = not (args.collect_only or args.assess)
    run_assess  = not (args.collect_only or args.build_kb)
    if args.collect_only: run_collect = True;  run_build  = False; run_assess = False
    if args.build_kb:     run_collect = False; run_build  = True;  run_assess = False
    if args.assess:       run_collect = False; run_build  = False; run_assess = True

    # Version check
    warnings = validate_versions(args.source, args.target)
    print_banner(args.source, args.target)
    for w in warnings:
        print_warn(w)

    # ── Phase 1 ──────────────────────────────────────────────────────────────
    if run_collect:
        print_phase(1, "Knowledge Collection")
        from k8s_assess.collector import collect_all
        docs = collect_all(
            source=args.source,
            target=args.target,
            components=args.components,
            force=args.force_collect,
        )
        print_ok(f"Phase 1 complete — {len(docs)} documents in kb/raw/")

    # ── Phase 2 ──────────────────────────────────────────────────────────────
    if run_build:
        print_phase(2, "Knowledge Base Build")
        from k8s_assess.knowledge_base import build_kb_from_raw
        kb = build_kb_from_raw(force=args.force_kb)
        if kb.is_ready():
            print_ok(f"Phase 2 complete — FAISS index ready ({len(kb._chunks)} chunks)")
        else:
            print_warn("KB is empty; collect docs first with --collect-only")

    # ── Phase 3 ──────────────────────────────────────────────────────────────
    if run_assess:
        if not args.dry_run and not os.environ.get("OPENAI_API_KEY"):
            print_err("OPENAI_API_KEY not set.  Export it first:")
            print(c(DIM, "    export OPENAI_API_KEY='sk-...'"))
            sys.exit(1)

        print_phase(3, "Cluster Inventory")

        # Cluster data
        if args.cluster_data:
            print_step(f"Loading cluster data from {args.cluster_data} …")
            cluster_data = json.loads(Path(args.cluster_data).read_text())
            ok = sum(1 for v in cluster_data.values() if v.get("ok"))
            print_ok(f"Loaded — {ok}/{len(cluster_data)} commands had output")
        elif args.skip_collect_cluster:
            print_warn("Skipping kubectl collection")
            cluster_data = {}
        else:
            print_step("Running kubectl commands (this may take ~60 s) …")
            cluster_data = collect_cluster()
            ok = sum(1 for v in cluster_data.values() if v.get("ok"))
            total = len(cluster_data)
            if ok == 0:
                print_warn("No kubectl commands succeeded — is KUBECONFIG set?")
            else:
                print_ok(f"Cluster data: {ok}/{total} commands succeeded")

            if args.dump_cluster:
                Path(args.dump_cluster).write_text(json.dumps(cluster_data, indent=2))
                print_ok(f"Cluster data saved to {args.dump_cluster}")

        # Load KB
        print_phase(3, "Retrieval")
        from k8s_assess.knowledge_base import KnowledgeBase
        from k8s_assess.retriever import (compute_evidence, build_retrieval_queries,
                                          format_context, build_prompt,
                                          call_openai, SYSTEM_PROMPT, _versions_between)
        from k8s_assess.cluster_profile import ClusterFlavour

        # Compute evidence first (operator detection needs only cluster_data, not chunks)
        chunks: list = []
        evidence = compute_evidence(cluster_data, chunks)   # chunks=[] for now; updated below

        kb = KnowledgeBase()
        if kb.load():
            summary = cluster_summary(cluster_data)
            queries = build_retrieval_queries(args.source, args.target, summary)
            print_step(f"Running {len(queries)} retrieval queries (top_k={args.top_k}) …")
            relevant_versions = _versions_between(args.source, args.target)
            chunks = kb.search_multi(
                queries,
                top_k_each=max(3, args.top_k // len(queries) + 1),
                relevant_versions=relevant_versions,
            )
            chunks = chunks[:args.top_k]
            # Re-compute evidence with chunks so KB coverage is accurate
            evidence = compute_evidence(cluster_data, chunks)
            context = format_context(
                chunks,
                installed_operators=set(evidence.detected_operators),
            )
            print_ok(f"Retrieved {len(chunks)} chunks "
                     f"({len(context):,} chars) from "
                     f"{len({c.source for c in chunks})} sources")
        else:
            print_warn("No FAISS index found — proceeding without KB context")
            print_warn("Run with --collect-only then --build-kb first for best results")
            context = "[No knowledge base available — running on model knowledge only]"

        # Evidence summary (evidence already computed above)
        confidence = evidence.compute_confidence()
        cap, _ = evidence.compute_readiness_cap()
        cp = evidence.cluster_profile

        # ── Cluster detection ─────────────────────────────────────────────
        print_step("Cluster detected:")
        if cp and cp.flavour != ClusterFlavour.GENERIC:
            det_colour = GREEN if cp.confidence >= 0.66 else YELLOW
            print(c(det_colour,  f"      Type       : {cp.display}"))
            print(c(DIM,         f"      Signals    : {', '.join(cp.signals[:3])}"))
            print(c(DIM,         f"      Managed    : {'yes' if cp.is_managed else 'no'}"))
            print(c(DIM,         f"      Upgrade    : {cp.meta.upgrade_mechanism}"))
            if cp.meta.missing_ok:
                print(c(DIM,     f"      Expected missing : {', '.join(cp.meta.missing_ok)}"))
        else:
            print_warn("Cluster type could not be determined — applying generic assumptions")

        # ── Evidence summary ──────────────────────────────────────────────
        print_step("Evidence metrics:")
        print(c(DIM,    f"      Commands    : {evidence.commands_ok}/{evidence.commands_total}"
                        f" ({evidence.command_success_rate:.0%})"))
        print(c(DIM,    f"      KB coverage : {evidence.retrieval_coverage:.0%}"))
        print(c(YELLOW if confidence < 75 else GREEN,
                        f"      Confidence  : {confidence}%  (pre-computed)"))
        print(c(YELLOW if cap < 90 else GREEN,
                        f"      Readiness cap: {cap}/100"))

        installed = evidence.detected_operators
        doc_only  = evidence.doc_only_operators
        if installed:
            print(c(GREEN, f"      Installed operators  : "
                           f"{', '.join(evidence.operators[k].display for k in installed)}"))
        else:
            print(c(DIM,   f"      Installed operators  : none detected"))
        if doc_only:
            print(c(DIM,   f"      KB docs (not installed): "
                           f"{', '.join(evidence.operators[k].display for k in doc_only)}"
                           f"  — will not be flagged as risks"))
        if evidence.unknown_risks:
            print(c(YELLOW, f"      Unknown risks: {len(evidence.unknown_risks)} items"))

        prompt = build_prompt(args.source, args.target, cluster_data, context, evidence)

        if args.dry_run:
            print_ok("Dry-run: printing prompt excerpt and exiting")
            print()
            print(c(DIM, "─" * 72))
            print(prompt[:3000])
            print(c(DIM, f"… [{len(prompt):,} total chars]"))
            print(c(DIM, "─" * 72))
            sys.exit(0)

        # Call OpenAI
        print_phase(3, "Assessment  (streaming …)")
        print(c(DIM, "  " + "─" * 68))
        print()
        report = call_openai(
            prompt=prompt,
            system=SYSTEM_PROMPT,
            model=args.model,
            stream=True,
        )
        print()
        print(c(DIM, "  " + "─" * 68))

        # Save reports
        if not args.no_save:
            print_phase(4, "Reports")
            from k8s_assess.reporter import save_reports
            md_path   = Path(args.md)   if args.md   else None
            html_path = Path(args.html) if args.html else None
            md, html = save_reports(
                report, args.source, args.target, md_path, html_path
            )
            print_ok(f"Markdown : {md}")
            print_ok(f"HTML     : {html}")

    # Done
    print()
    print(banner_line())
    print(c(BOLD + GREEN, "  Assessment complete.".center(72)))
    print(banner_line())
    print()


if __name__ == "__main__":
    main()
