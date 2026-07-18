"""Kubernetes version parsing, comparison, and upgrade-path math.

Everything that touches "1.28" as a concept goes through here — no ad-hoc
string splitting anywhere else. Handles the ``v`` prefix, EKS-style
``1.28+``, and full GitVersions like ``v1.28.9-eks-036c24b``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

from ..errors import ConfigurationError

_VERSION_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?(?P<rest>[+.\-].*)?$"
)


@total_ordering
@dataclass(frozen=True)
class KubeVersion:
    major: int
    minor: int
    patch: int | None = None

    @classmethod
    def parse(cls, raw: str) -> KubeVersion:
        m = _VERSION_RE.match(raw.strip())
        if not m:
            raise ConfigurationError(
                f"'{raw}' is not a valid Kubernetes version (expected e.g. '1.29' or 'v1.29.4')"
            )
        return cls(
            major=int(m.group("major")),
            minor=int(m.group("minor")),
            patch=int(m.group("patch")) if m.group("patch") else None,
        )

    @property
    def minor_str(self) -> str:
        """'1.29' — the granularity at which upgrade planning happens."""
        return f"{self.major}.{self.minor}"

    def __str__(self) -> str:
        return self.minor_str if self.patch is None else f"{self.major}.{self.minor}.{self.patch}"

    def _key(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch or 0)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, KubeVersion):
            return NotImplemented
        return self._key() < other._key()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, KubeVersion):
            return NotImplemented
        return self.minor_str == other.minor_str and (self.patch or 0) == (other.patch or 0)

    def __hash__(self) -> int:
        return hash(self._key())

    def same_minor(self, other: KubeVersion) -> bool:
        return (self.major, self.minor) == (other.major, other.minor)

    def next_minor(self) -> KubeVersion:
        return KubeVersion(self.major, self.minor + 1)

    def minors_until(self, target: KubeVersion) -> list[KubeVersion]:
        """Minor versions after self up to and including target.

        1.27 → 1.30 yields [1.28, 1.29, 1.30]. Raises if target is not a
        forward, same-major upgrade — downgrades and major jumps need human
        judgment, not a tool pretending it has one.
        """
        if target.major != self.major:
            raise ConfigurationError(f"cross-major upgrade {self} → {target} is not supported")
        if target._key() <= self._key() and not self.same_minor(target):
            raise ConfigurationError(f"target version {target} must be greater than source {self}")
        return [KubeVersion(self.major, m) for m in range(self.minor + 1, target.minor + 1)]


def validate_upgrade_pair(source: str, target: str) -> tuple[KubeVersion, KubeVersion]:
    """Parse and sanity-check a source→target pair; the single entry point
    used by the CLI and the API so both fail identically."""
    src, tgt = KubeVersion.parse(source), KubeVersion.parse(target)
    if src.same_minor(tgt):
        raise ConfigurationError(f"source and target are the same minor version ({src})")
    src.minors_until(tgt)  # raises on downgrade / cross-major
    return src, tgt
