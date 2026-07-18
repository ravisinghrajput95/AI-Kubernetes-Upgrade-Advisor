"""Resilient document fetching with an on-disk cache.

Each source is cached as JSON under ``kb/raw/`` with fetch metadata
(timestamp, ETag, final URL). Re-fetches send If-None-Match / use the cache
when it is younger than the configured max age, so rebuilding the KB is
cheap and polite to upstream doc servers.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ..config import KnowledgeSettings
from ..observability import get_logger, metrics
from ..resilience import retry
from .sources import DocSource

log = get_logger(__name__)

_USER_AGENT = (
    "k8s-upgrade-advisor/2.0 (+https://github.com/ravisinghrajput95/AI-Kubernetes-Upgrade-Advisor)"
)


@dataclass
class RawDocument:
    key: str
    title: str
    url: str
    kind: str
    component: str | None
    k8s_version: str | None
    content: str  # plain text / markdown
    fetched_at: str = ""
    etag: str = ""

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.key}.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> RawDocument:
        return cls(**json.loads(path.read_text(encoding="utf-8")))

    @property
    def age_days(self) -> float:
        if not self.fetched_at:
            return float("inf")
        fetched = datetime.fromisoformat(self.fetched_at)
        return (datetime.now(UTC) - fetched).total_seconds() / 86400


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "button"]):
        tag.decompose()
    lines = [line.rstrip() for line in soup.get_text(separator="\n").splitlines()]
    out: list[str] = []
    blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and blank:
            continue
        out.append(line)
        blank = is_blank
    return "\n".join(out).strip()


class DocumentFetcher:
    def __init__(self, settings: KnowledgeSettings, raw_dir: Path) -> None:
        self.settings = settings
        self.raw_dir = raw_dir
        self.session = requests.Session()
        self.session.headers["User-Agent"] = _USER_AGENT

    def _cached(self, source: DocSource) -> RawDocument | None:
        path = self.raw_dir / f"{source.key}.json"
        if not path.is_file():
            return None
        try:
            return RawDocument.load(path)
        except (json.JSONDecodeError, TypeError):
            log.warning("cache_corrupt", key=source.key)
            return None

    def fetch(self, source: DocSource, force: bool = False) -> RawDocument | None:
        cached = self._cached(source)
        if cached and not force and cached.age_days < self.settings.cache_max_age_days:
            metrics.doc_fetches_total.labels(status="cached").inc()
            return cached

        @retry(
            attempts=self.settings.fetch_retries + 1,
            base_delay=1.0,
            retry_on=(requests.ConnectionError, requests.Timeout),
            on_retry=lambda n, exc: log.warning(
                "fetch_retry", key=source.key, attempt=n, error=str(exc)
            ),
        )
        def _get() -> requests.Response:
            headers = {"If-None-Match": cached.etag} if cached and cached.etag else {}
            resp = self.session.get(
                source.url, timeout=self.settings.fetch_timeout_seconds, headers=headers
            )
            if resp.status_code != 304:
                resp.raise_for_status()
            return resp

        try:
            resp = _get()
        except requests.RequestException as exc:
            metrics.doc_fetches_total.labels(status="error").inc()
            log.warning("fetch_failed", key=source.key, url=source.url, error=str(exc))
            return cached  # stale beats nothing; caller sees age via manifest

        if resp.status_code == 304 and cached:
            cached.fetched_at = datetime.now(UTC).isoformat()
            cached.save(self.raw_dir)
            metrics.doc_fetches_total.labels(status="cached").inc()
            return cached

        content_type = resp.headers.get("Content-Type", "")
        text = html_to_text(resp.text) if "html" in content_type else resp.text
        doc = RawDocument(
            key=source.key,
            title=source.title,
            url=source.url,
            kind=source.kind,
            component=source.component,
            k8s_version=source.k8s_version,
            content=text[:400_000],  # hard cap; k8s CHANGELOGs are enormous
            fetched_at=datetime.now(UTC).isoformat(),
            etag=resp.headers.get("ETag", ""),
        )
        doc.save(self.raw_dir)
        metrics.doc_fetches_total.labels(status="ok").inc()
        log.info("fetched", key=source.key, chars=len(doc.content))
        time.sleep(0.2)  # politeness between live fetches
        return doc

    def fetch_all(self, sources: list[DocSource], force: bool = False) -> list[RawDocument]:
        docs: list[RawDocument] = []
        for source in sources:
            doc = self.fetch(source, force=force)
            if doc is not None:
                docs.append(doc)
        log.info("fetch_complete", requested=len(sources), fetched=len(docs))
        return docs
