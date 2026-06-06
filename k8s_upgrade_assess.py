#!/usr/bin/env python3
"""
k8s-upgrade-assess: Kubernetes Upgrade Feasibility, Compatibility, and Risk Assessment CLI
Powered by Claude AI (claude-sonnet-4-20250514)
"""

import argparse
import json
import subprocess
import sys
import os
import re
import textwrap
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

# ── ANSI colours ────────────────────────────────────────────────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RED     = "\033[91m"
YELLOW  = "\033[93m"
GREEN   = "\033[92m"
CYAN    = "\033[96m"
BLUE    = "\033[94m"
MAGENTA = "\033[95m"
WHITE   = "\033[97m"

def c(colour: str, text: str) -> str:
    return f"{colour}{text}{RESET}"

# ── kubectl helpers ──────────────────────────────────────────────────────────
def run_kubectl(args: list[str], timeout: int = 30) -> tuple[str, str]:
    """Run a kubectl command; return (stdout, stderr). Never raises."""
    try:
        result = subprocess.run(
            ["kubectl"] + args,
            capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return "", "kubectl not found in PATH"
    except subprocess.TimeoutExpired:
        return "", f"kubectl {' '.join(args)} timed out after {timeout}s"
    except Exception as e:
        return "", str(e)

def collect_cluster_data() -> dict:
    """Run all kubectl commands and return a dict of their outputs."""
    commands = {
        "version":            ["version", "--output=json"],
        "nodes":              ["get", "nodes", "-o", "wide"],
        "nodes_yaml":         ["get", "nodes", "-o", "yaml"],
        "namespaces":         ["get", "ns"],
        "api_resources":      ["api-resources", "--verbs=list", "-o", "wide"],
        "api_services":       ["get", "apiservices"],
        "all_resources":      ["get", "all", "-A"],
        "deployments":        ["get", "deploy", "-A", "-o", "wide"],
        "statefulsets":       ["get", "sts", "-A", "-o", "wide"],
        "daemonsets":         ["get", "ds", "-A", "-o", "wide"],
        "jobs":               ["get", "jobs", "-A"],
        "cronjobs":           ["get", "cronjobs", "-A"],
        "crds":               ["get", "crd"],
        "crds_yaml":          ["get", "crd", "-o", "yaml"],
        "validating_webhooks":["get", "validatingwebhookconfigurations"],
        "mutating_webhooks":  ["get", "mutatingwebhookconfigurations"],
        "storage_classes":    ["get", "sc"],
        "pvs":                ["get", "pv"],
        "pvcs":               ["get", "pvc", "-A"],
        "top_nodes":          ["top", "nodes"],
        "top_pods":           ["top", "pods", "-A"],
        "pod_security":       ["get", "psp"],           # will fail on newer clusters
        "cluster_info":       ["cluster-info"],
    }
    data: dict = {}
    for key, cmd in commands.items():
        stdout, stderr = run_kubectl(cmd, timeout=45)
        data[key] = {"stdout": stdout, "stderr": stderr, "ok": bool(stdout and not stderr)}
    return data

# ── OpenAI API call ───────────────────────────────────────────────────────────
def call_openai(prompt: str, system: str, stream: bool = True) -> str:
    """
    Call the OpenAI Chat Completions API (gpt-4o).
    Uses OPENAI_API_KEY from the environment.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    url = "https://api.openai.com/v1/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    body = json.dumps({
        "model": "gpt-4o",
        "max_tokens": 8000,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        "stream": stream,
    }).encode()

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    collected = []
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            if stream:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if line.startswith("data: "):
                        payload = line[6:]
                        if payload == "[DONE]":
                            break
                        try:
                            event = json.loads(payload)
                            text  = (
                                event.get("choices", [{}])[0]
                                     .get("delta", {})
                                     .get("content", "")
                            )
                            if text:
                                print(text, end="", flush=True)
                                collected.append(text)
                        except (json.JSONDecodeError, IndexError):
                            pass
            else:
                data = json.loads(resp.read())
                text = data["choices"][0]["message"]["content"]
                collected.append(text)
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()
        raise RuntimeError(f"API error {e.code}: {body_err}")

    return "".join(collected)


# ── Prompt builder ───────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a Senior Kubernetes Platform Engineer performing a
comprehensive upgrade readiness review.

Your analysis must be:
- Exhaustive and evidence-based
- Conservative (if compatibility cannot be confirmed, classify it as a risk)
- Structured exactly as the user requests

Use these severity labels consistently:
  PASS | GOOD | WARNING | HIGH RISK | CRITICAL

Always separate findings into:
  * Verified Issues
  * Probable Issues
  * Possible Issues
  * Unknown Risks

For every incompatibility state:
  WHAT WILL BREAK / WHEN / IMPACT / SEVERITY / REMEDIATION

Produce a final Risk Matrix table and Readiness Score (0-100) and
Confidence Score (0-100%) followed by a full Executive Summary."""


def build_prompt(source: str, target: str, cluster_data: dict) -> str:
    """Assemble the full assessment prompt."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def section(title: str, key: str) -> str:
        d = cluster_data.get(key, {})
        out = d.get("stdout", "")
        err = d.get("stderr", "")
        content = out if out else f"(unavailable – {err or 'no output'})"
        return f"\n### {title}\n```\n{content[:8000]}\n```\n"  # cap per section

    cluster_dump = (
        section("kubectl version", "version") +
        section("Nodes (wide)", "nodes") +
        section("Namespaces", "namespaces") +
        section("API Resources", "api_resources") +
        section("API Services", "api_services") +
        section("All Resources (-A)", "all_resources") +
        section("Deployments (-A)", "deployments") +
        section("StatefulSets (-A)", "statefulsets") +
        section("DaemonSets (-A)", "daemonsets") +
        section("Jobs (-A)", "jobs") +
        section("CronJobs (-A)", "cronjobs") +
        section("CRDs", "crds") +
        section("Validating Webhooks", "validating_webhooks") +
        section("Mutating Webhooks", "mutating_webhooks") +
        section("StorageClasses", "storage_classes") +
        section("PersistentVolumes", "pvs") +
        section("PersistentVolumeClaims (-A)", "pvcs") +
        section("Node Resource Usage (top nodes)", "top_nodes") +
        section("Pod Resource Usage (top pods -A)", "top_pods") +
        section("PodSecurityPolicies", "pod_security") +
        section("Cluster Info", "cluster_info")
    )

    return textwrap.dedent(f"""
        # Kubernetes Upgrade Assessment Request
        Generated: {now}

        ## Upgrade Path
        SOURCE_VERSION: {source}
        TARGET_VERSION: {target}

        ## Live Cluster Data
        {cluster_dump}

        ## Instructions
        Using the cluster data above, perform the FULL assessment defined in
        the system prompt.  Cover ALL 17 steps, the Risk Matrix, Readiness
        Score, Confidence Score, and Executive Summary.

        Be specific about resources found in the cluster data.
        If data for a section was unavailable, note it and reduce confidence.
    """).strip()

# ── Pretty header / footer ───────────────────────────────────────────────────
def print_banner(source: str, target: str) -> None:
    width = 70
    print()
    print(c(CYAN, "━" * width))
    print(c(BOLD + CYAN, "  ⎈  Kubernetes Upgrade Assessment".center(width)))
    print(c(CYAN, "━" * width))
    print(c(DIM,  f"  Source : {source}"))
    print(c(DIM,  f"  Target : {target}"))
    print(c(DIM,  f"  Date   : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"))
    print(c(CYAN, "━" * width))
    print()

def print_step(step: str) -> None:
    print(c(BLUE, f"\n▸ {step}"), flush=True)

def print_warning(msg: str) -> None:
    print(c(YELLOW, f"  ⚠  {msg}"))

def print_error(msg: str) -> None:
    print(c(RED, f"  ✖  {msg}"))

def print_ok(msg: str) -> None:
    print(c(GREEN, f"  ✔  {msg}"))

# ── Save report ──────────────────────────────────────────────────────────────
def save_report(content: str, source: str, target: str, output: Optional[str]) -> str:
    if not output:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        sv = source.replace(".", "")
        tv = target.replace(".", "")
        output = f"k8s_upgrade_{sv}_to_{tv}_{ts}.md"
    with open(output, "w") as f:
        f.write(f"# Kubernetes Upgrade Assessment\n")
        f.write(f"**Source:** {source}  |  **Target:** {target}\n\n")
        f.write(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n")
        f.write("---\n\n")
        f.write(content)
    return output

# ── Version helpers ──────────────────────────────────────────────────────────
def parse_version(v: str) -> tuple[int, int, int]:
    v = v.lstrip("v")
    parts = v.split(".")
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(re.sub(r"[^0-9]", "", parts[2])) if len(parts) > 2 else 0
        return (major, minor, patch)
    except (ValueError, IndexError):
        return (0, 0, 0)

def validate_versions(source: str, target: str) -> list[str]:
    warnings = []
    sv = parse_version(source)
    tv = parse_version(target)
    if sv == (0, 0, 0):
        warnings.append(f"Cannot parse source version '{source}'")
    if tv == (0, 0, 0):
        warnings.append(f"Cannot parse target version '{target}'")
    if sv and tv:
        if tv <= sv:
            warnings.append("Target version is not newer than source version")
        minor_diff = tv[1] - sv[1]
        if sv[0] == tv[0] and minor_diff > 2:
            warnings.append(
                f"Skipping {minor_diff} minor versions is unsupported by Kubernetes "
                f"(max supported skip is 1 minor version)"
            )
    return warnings

# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="k8s-upgrade-assess",
        description=(
            "AI-powered Kubernetes upgrade feasibility, compatibility, "
            "and risk assessment tool"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python k8s_upgrade_assess.py --source 1.27 --target 1.29
              python k8s_upgrade_assess.py -s 1.28 -t 1.30 --save report.md
              python k8s_upgrade_assess.py -s 1.27 -t 1.28 --dry-run
              python k8s_upgrade_assess.py -s 1.27 -t 1.28 --skip-collect --cluster-data cluster.json

            Environment:
              OPENAI_API_KEY      Required. Your OpenAI API key.
              KUBECONFIG          Optional. Path to kubeconfig file.
        """),
    )
    parser.add_argument("-s", "--source", required=True,
                        help="Current Kubernetes version (e.g. 1.27)")
    parser.add_argument("-t", "--target", required=True,
                        help="Target Kubernetes version (e.g. 1.29)")
    parser.add_argument("--save", metavar="FILE",
                        help="Save Markdown report to FILE (auto-named if omitted)")
    parser.add_argument("--no-save", action="store_true",
                        help="Do not save the report to disk")
    parser.add_argument("--dry-run", action="store_true",
                        help="Collect cluster data and print prompt, but do not call API")
    parser.add_argument("--skip-collect", action="store_true",
                        help="Skip kubectl collection (use --cluster-data to load JSON)")
    parser.add_argument("--cluster-data", metavar="FILE",
                        help="Load pre-collected cluster data from a JSON file")
    parser.add_argument("--dump-cluster", metavar="FILE",
                        help="Save collected cluster data JSON to FILE for later reuse")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable ANSI color output")
    args = parser.parse_args()

    # ── Disable color if requested ───────────────────────────────────────────
    if args.no_color or not sys.stdout.isatty():
        global RESET, BOLD, DIM, RED, YELLOW, GREEN, CYAN, BLUE, MAGENTA, WHITE
        RESET = BOLD = DIM = RED = YELLOW = GREEN = CYAN = BLUE = MAGENTA = WHITE = ""

    # ── Validate API key ─────────────────────────────────────────────────────
    if not args.dry_run and not os.environ.get("OPENAI_API_KEY"):
        print_error("OPENAI_API_KEY environment variable is not set.")
        print(c(DIM, "  Export it:  export OPENAI_API_KEY='sk-...'"))
        sys.exit(1)

    # ── Version checks ───────────────────────────────────────────────────────
    version_warnings = validate_versions(args.source, args.target)
    print_banner(args.source, args.target)
    if version_warnings:
        for w in version_warnings:
            print_warning(w)
        print()

    # ── Collect cluster data ─────────────────────────────────────────────────
    cluster_data: dict = {}

    if args.cluster_data:
        print_step(f"Loading cluster data from {args.cluster_data} …")
        try:
            with open(args.cluster_data) as f:
                cluster_data = json.load(f)
            print_ok("Cluster data loaded.")
        except Exception as e:
            print_error(f"Failed to load cluster data: {e}")
            sys.exit(1)
    elif not args.skip_collect:
        print_step("Collecting live cluster data via kubectl …")
        print(c(DIM, "  This may take 30–60 seconds.\n"))
        cluster_data = collect_cluster_data()

        ok_count  = sum(1 for v in cluster_data.values() if v.get("ok"))
        tot_count = len(cluster_data)
        if ok_count == 0:
            print_error("No kubectl commands succeeded. Is kubectl configured?")
            print(c(DIM, "  Hint: check KUBECONFIG or run 'kubectl cluster-info'"))
        else:
            print_ok(f"Collected data from {ok_count}/{tot_count} kubectl commands.")

        if args.dump_cluster:
            with open(args.dump_cluster, "w") as f:
                json.dump(cluster_data, f, indent=2)
            print_ok(f"Cluster data saved to {args.dump_cluster}")
    else:
        print_warning("Skipping cluster data collection (--skip-collect).")
        print_warning("Assessment will be based on general knowledge only.")

    # ── Build prompt ─────────────────────────────────────────────────────────
    print_step("Building assessment prompt …")
    prompt = build_prompt(args.source, args.target, cluster_data)

    if args.dry_run:
        print_ok("Dry-run mode – printing prompt and exiting.")
        print()
        print(c(DIM, "─" * 70))
        print(prompt[:4000])
        if len(prompt) > 4000:
            print(c(DIM, f"… [truncated – full prompt is {len(prompt)} chars]"))
        print(c(DIM, "─" * 70))
        sys.exit(0)

    # ── Call Claude ──────────────────────────────────────────────────────────
    print_step("Calling Claude AI for upgrade assessment …")
    print(c(DIM, "  Streaming response:\n"))
    print(c(CYAN, "─" * 70))

    try:
        report = call_openai(prompt, SYSTEM_PROMPT, stream=True)
    except RuntimeError as e:
        print()
        print_error(f"API call failed: {e}")
        sys.exit(1)

    print()
    print(c(CYAN, "─" * 70))

    # ── Save report ──────────────────────────────────────────────────────────
    if not args.no_save:
        try:
            path = save_report(report, args.source, args.target, args.save)
            print_ok(f"Report saved to: {c(BOLD, path)}")
        except OSError as e:
            print_warning(f"Could not save report: {e}")

    print()
    print(c(CYAN, "━" * 70))
    print(c(BOLD + GREEN, "  Assessment complete.".center(70)))
    print(c(CYAN, "━" * 70))
    print()


if __name__ == "__main__":
    main()
