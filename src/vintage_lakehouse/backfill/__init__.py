"""Backfill-aware correction: estimate how provisional values get revised, and correct for it.

``VintageTable`` holds every version of a series, ``estimate_correction`` learns the typical
revision by age from what was known at a cutoff, and ``BackfillCorrected`` wraps any forecaster so
that it sees the corrected history. The design and how its benefit is measured are in
docs/adr/0010-backfill-correction.md.
"""

from vintage_lakehouse.backfill.correction import (
    CorrectionConfig,
    CorrectionFactors,
    estimate_correction,
)
from vintage_lakehouse.backfill.forecaster import BackfillCorrected
from vintage_lakehouse.backfill.vintages import VintageTable

__all__ = [
    "BackfillCorrected",
    "CorrectionConfig",
    "CorrectionFactors",
    "VintageTable",
    "estimate_correction",
]
