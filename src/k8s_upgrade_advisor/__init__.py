"""k8s-upgrade-advisor — AI Kubernetes Upgrade Intelligence Platform.

Deterministic compatibility analysis first, RAG-grounded LLM reasoning second:
the analyzer produces provable findings (deprecated APIs, version skew, addon
compatibility) and the LLM explains, sequences, and plans around them — it is
never the source of truth for compatibility facts.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Single source of truth: the version declared in pyproject.toml and baked
    # into the built wheel/container. Avoids the drift where a hardcoded string
    # here silently lags the released version.
    __version__ = _pkg_version("k8s-upgrade-advisor")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
