"""Dataset trust report and look-ahead (leakage) checks.

``build_trust_report`` describes how a series' values get revised. ``check_pipeline`` runs a user
pipeline with later data altered and removed, to find look-ahead by behaviour, and ``lint_source``
flags code patterns that often leak the future. The design, thresholds and limits are in
docs/adr/0011-trust-report-and-leakage-linter.md.
"""

from vintage_lakehouse.audit.leak_check import (
    FAR_FUTURE,
    LeakCheckResult,
    OriginCheck,
    check_pipeline,
)
from vintage_lakehouse.audit.lint import RULES, LintFinding, lint_paths, lint_source
from vintage_lakehouse.audit.trust import (
    LagStats,
    TrustConfig,
    TrustReport,
    YearStats,
    build_trust_report,
    render_trust_markdown,
    trust_report_to_dict,
)

__all__ = [
    "FAR_FUTURE",
    "RULES",
    "LagStats",
    "LeakCheckResult",
    "LintFinding",
    "OriginCheck",
    "TrustConfig",
    "TrustReport",
    "YearStats",
    "build_trust_report",
    "check_pipeline",
    "lint_paths",
    "lint_source",
    "render_trust_markdown",
    "trust_report_to_dict",
]
