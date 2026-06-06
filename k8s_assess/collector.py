"""
k8s_assess/collector.py

Phase 1 — RAG Foundation
Scrapes Kubernetes release notes, deprecated-API guides, and operator
compatibility matrices.  Stores raw documents as JSON in kb/raw/.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

RAW_DIR = Path(__file__).parent.parent / "kb" / "raw"

# ── Helpers ──────────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")

def _get(url: str, timeout: int = 20) -> Optional[requests.Response]:
    headers = {"User-Agent": "k8s-upgrade-assess/1.0 (compatibility research)"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"    ⚠  fetch failed {url}: {e}")
        return None

def _md_from_html(html: str) -> str:
    """Very light HTML → plain-text conversion (no pandoc needed)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "form", "button"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse blank lines
    lines = [l.rstrip() for l in text.splitlines()]
    compressed = []
    prev_blank = False
    for line in lines:
        blank = line == ""
        if blank and prev_blank:
            continue
        compressed.append(line)
        prev_blank = blank
    return "\n".join(compressed).strip()


@dataclass
class Document:
    doc_id:   str
    source:   str          # 'kubernetes_release_notes', 'cert_manager', …
    url:      str
    title:    str
    content:  str
    metadata: dict = field(default_factory=dict)

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.doc_id}.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "Document":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)


# ── Source definitions ───────────────────────────────────────────────────────

def _k8s_versions_between(source: str, target: str) -> list[str]:
    """Return minor versions src+1 … target inclusive, e.g. 1.27→1.29 → [1.28,1.29]."""
    def _minor(v: str) -> int:
        return int(v.lstrip("v").split(".")[1])
    src_m = _minor(source)
    tgt_m = _minor(target)
    return [f"1.{m}" for m in range(src_m + 1, tgt_m + 1)]


def collect_k8s_release_notes(source: str, target: str) -> list[Document]:
    docs: list[Document] = []
    versions = _k8s_versions_between(source, target)
    # Also include source version for "what's changing FROM"
    versions = [source.lstrip("v")] + versions

    for ver in versions:
        major, minor = ver.split(".")[:2]
        # GitHub raw release notes
        url = (f"https://raw.githubusercontent.com/kubernetes/kubernetes/"
               f"master/CHANGELOG/CHANGELOG-{major}.{minor}.md")
        r = _get(url)
        if not r:
            # fallback to rendered GitHub page
            url = (f"https://github.com/kubernetes/kubernetes/blob/master/"
                   f"CHANGELOG/CHANGELOG-{major}.{minor}.md")
            r = _get(url)
            content = _md_from_html(r.text) if r else f"[unavailable for {ver}]"
        else:
            content = r.text

        doc = Document(
            doc_id=f"k8s_release_notes_{_slug(ver)}",
            source="kubernetes_release_notes",
            url=url,
            title=f"Kubernetes {ver} Release Notes",
            content=content[:120_000],   # cap ~120 KB
            metadata={"k8s_version": ver},
        )
        docs.append(doc)
        print(f"    ✔  k8s release notes {ver} ({len(content):,} chars)")
        time.sleep(0.3)

    return docs


def collect_k8s_deprecated_apis() -> list[Document]:
    docs: list[Document] = []
    sources = [
        ("https://kubernetes.io/docs/reference/using-api/deprecation-guide/",
         "Kubernetes API Deprecation Guide"),
        ("https://kubernetes.io/docs/reference/using-api/deprecation-policy/",
         "Kubernetes Deprecation Policy"),
    ]
    for url, title in sources:
        r = _get(url)
        content = _md_from_html(r.text) if r else f"[unavailable: {url}]"
        doc = Document(
            doc_id=f"k8s_deprecated_apis_{_slug(title)}",
            source="kubernetes_deprecated_apis",
            url=url,
            title=title,
            content=content[:80_000],
            metadata={},
        )
        docs.append(doc)
        print(f"    ✔  {title} ({len(content):,} chars)")
        time.sleep(0.3)
    return docs


def _scrape_github_releases(repo: str, component: str, max_pages: int = 2) -> list[Document]:
    """Scrape GitHub releases page for a component."""
    docs: list[Document] = []
    for page in range(1, max_pages + 1):
        url = f"https://github.com/{repo}/releases?page={page}"
        r = _get(url)
        if not r:
            break
        content = _md_from_html(r.text)
        doc = Document(
            doc_id=f"{_slug(component)}_releases_p{page}",
            source=component,
            url=url,
            title=f"{component} GitHub Releases (page {page})",
            content=content[:80_000],
            metadata={"repo": repo, "page": page},
        )
        docs.append(doc)
        print(f"    ✔  {component} releases page {page} ({len(content):,} chars)")
        time.sleep(0.5)
    return docs


def collect_cert_manager() -> list[Document]:
    docs: list[Document] = []
    urls = [
        ("https://cert-manager.io/docs/installation/supported-releases/",
         "cert-manager Supported Releases & Compatibility"),
        ("https://cert-manager.io/docs/releases/",
         "cert-manager Release Notes"),
    ]
    for url, title in urls:
        r = _get(url)
        content = _md_from_html(r.text) if r else f"[unavailable]"
        docs.append(Document(
            doc_id=f"cert_manager_{_slug(title)}",
            source="cert_manager",
            url=url,
            title=title,
            content=content[:80_000],
            metadata={},
        ))
        print(f"    ✔  {title} ({len(content):,} chars)")
        time.sleep(0.4)
    docs += _scrape_github_releases("cert-manager/cert-manager", "cert_manager", 1)
    return docs


def collect_ingress_nginx() -> list[Document]:
    docs: list[Document] = []
    urls = [
        ("https://github.com/kubernetes/ingress-nginx#supported-versions-table",
         "ingress-nginx Supported Versions"),
        ("https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/README.md",
         "ingress-nginx README (compatibility table)"),
    ]
    for url, title in urls:
        r = _get(url)
        content = (r.text if url.endswith(".md") else _md_from_html(r.text)) if r else "[unavailable]"
        docs.append(Document(
            doc_id=f"ingress_nginx_{_slug(title)}",
            source="ingress_nginx",
            url=url,
            title=title,
            content=content[:80_000],
            metadata={},
        ))
        print(f"    ✔  {title} ({len(content):,} chars)")
        time.sleep(0.4)
    docs += _scrape_github_releases("kubernetes/ingress-nginx", "ingress_nginx", 1)
    return docs


def collect_metrics_server() -> list[Document]:
    docs: list[Document] = []
    url = "https://raw.githubusercontent.com/kubernetes-sigs/metrics-server/master/README.md"
    r = _get(url)
    content = r.text if r else "[unavailable]"
    docs.append(Document(
        doc_id="metrics_server_readme",
        source="metrics_server",
        url=url,
        title="metrics-server README (compatibility)",
        content=content[:60_000],
        metadata={},
    ))
    print(f"    ✔  metrics-server README ({len(content):,} chars)")
    docs += _scrape_github_releases("kubernetes-sigs/metrics-server", "metrics_server", 1)
    return docs


def collect_argocd() -> list[Document]:
    docs: list[Document] = []
    urls = [
        ("https://argo-cd.readthedocs.io/en/stable/operator-manual/installation/",
         "Argo CD Installation & Compatibility"),
        ("https://argo-cd.readthedocs.io/en/stable/user-guide/supported-versions/",
         "Argo CD Supported Versions"),
    ]
    for url, title in urls:
        r = _get(url)
        content = _md_from_html(r.text) if r else "[unavailable]"
        docs.append(Document(
            doc_id=f"argocd_{_slug(title)}",
            source="argocd",
            url=url,
            title=title,
            content=content[:80_000],
            metadata={},
        ))
        print(f"    ✔  {title} ({len(content):,} chars)")
        time.sleep(0.4)
    docs += _scrape_github_releases("argoproj/argo-cd", "argocd", 1)
    return docs


def collect_istio() -> list[Document]:
    docs: list[Document] = []
    urls = [
        ("https://istio.io/latest/docs/releases/supported-releases/",
         "Istio Supported Releases"),
        ("https://istio.io/latest/news/releases/",
         "Istio Release Notes"),
    ]
    for url, title in urls:
        r = _get(url)
        content = _md_from_html(r.text) if r else "[unavailable]"
        docs.append(Document(
            doc_id=f"istio_{_slug(title)}",
            source="istio",
            url=url,
            title=title,
            content=content[:80_000],
            metadata={},
        ))
        print(f"    ✔  {title} ({len(content):,} chars)")
        time.sleep(0.4)
    docs += _scrape_github_releases("istio/istio", "istio", 1)
    return docs


def collect_cilium() -> list[Document]:
    docs: list[Document] = []
    urls = [
        ("https://docs.cilium.io/en/stable/network/kubernetes/compatibility/",
         "Cilium Kubernetes Compatibility"),
        ("https://github.com/cilium/cilium/blob/main/README.md",
         "Cilium README"),
    ]
    for url, title in urls:
        r = _get(url)
        content = _md_from_html(r.text) if r else "[unavailable]"
        docs.append(Document(
            doc_id=f"cilium_{_slug(title)}",
            source="cilium",
            url=url,
            title=title,
            content=content[:80_000],
            metadata={},
        ))
        print(f"    ✔  {title} ({len(content):,} chars)")
        time.sleep(0.4)
    docs += _scrape_github_releases("cilium/cilium", "cilium", 1)
    return docs


def collect_karpenter() -> list[Document]:
    docs: list[Document] = []
    urls = [
        ("https://karpenter.sh/docs/upgrading/compatibility/",
         "Karpenter Compatibility"),
        ("https://karpenter.sh/docs/upgrading/upgrade-guide/",
         "Karpenter Upgrade Guide"),
    ]
    for url, title in urls:
        r = _get(url)
        content = _md_from_html(r.text) if r else "[unavailable]"
        docs.append(Document(
            doc_id=f"karpenter_{_slug(title)}",
            source="karpenter",
            url=url,
            title=title,
            content=content[:80_000],
            metadata={},
        ))
        print(f"    ✔  {title} ({len(content):,} chars)")
        time.sleep(0.4)
    docs += _scrape_github_releases("aws/karpenter-provider-aws", "karpenter", 1)
    return docs


def collect_csi_drivers() -> list[Document]:
    docs: list[Document] = []
    sources = [
        ("https://github.com/kubernetes-sigs/aws-ebs-csi-driver/blob/master/docs/README.md",
         "aws-ebs-csi-driver", "csi_ebs"),
        ("https://raw.githubusercontent.com/kubernetes-sigs/aws-ebs-csi-driver/master/README.md",
         "aws-ebs-csi-driver README", "csi_ebs"),
        ("https://github.com/kubernetes-sigs/gcp-compute-persistent-disk-csi-driver/blob/master/README.md",
         "gcp-pd-csi-driver", "csi_gcp"),
        ("https://github.com/kubernetes-sigs/azuredisk-csi-driver/blob/master/README.md",
         "azure-disk-csi-driver", "csi_azure"),
        ("https://kubernetes-csi.github.io/docs/drivers.html",
         "CSI Drivers List", "csi_drivers"),
    ]
    for url, title, source in sources:
        r = _get(url)
        content = _md_from_html(r.text) if r else "[unavailable]"
        docs.append(Document(
            doc_id=f"{source}_{_slug(title)}",
            source=source,
            url=url,
            title=title,
            content=content[:60_000],
            metadata={},
        ))
        print(f"    ✔  {title} ({len(content):,} chars)")
        time.sleep(0.4)
    return docs


# ── Main entry ───────────────────────────────────────────────────────────────

COLLECTORS = {
    "kubernetes_release_notes": None,    # handled specially (needs versions)
    "kubernetes_deprecated_apis": collect_k8s_deprecated_apis,
    "cert_manager":              collect_cert_manager,
    "ingress_nginx":             collect_ingress_nginx,
    "metrics_server":            collect_metrics_server,
    "argocd":                    collect_argocd,
    "istio":                     collect_istio,
    "cilium":                    collect_cilium,
    "karpenter":                 collect_karpenter,
    "csi_drivers":               collect_csi_drivers,
}


def collect_all(source: str, target: str,
                components: Optional[list[str]] = None,
                force: bool = False) -> list[Document]:
    """
    Collect all documents for the given upgrade path.
    Skips sources whose files already exist in kb/raw/ unless force=True.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    all_docs: list[Document] = []

    selected = components or list(COLLECTORS.keys())

    for name in selected:
        print(f"\n  ▸ Collecting: {name}")

        if name == "kubernetes_release_notes":
            docs = collect_k8s_release_notes(source, target)
        else:
            fn = COLLECTORS.get(name)
            if fn is None:
                print(f"    ⚠  Unknown component '{name}', skipping")
                continue
            # check cache
            existing = list(RAW_DIR.glob(f"{_slug(name)}*.json"))
            if existing and not force:
                print(f"    ↩  cached ({len(existing)} files) — use --force-collect to refresh")
                docs = [Document.load(p) for p in existing]
            else:
                docs = fn()

        for doc in docs:
            doc.save(RAW_DIR)
        all_docs.extend(docs)

    print(f"\n  ✔  Total documents collected: {len(all_docs)}")
    return all_docs
