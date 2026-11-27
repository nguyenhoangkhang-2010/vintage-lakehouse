"""A forecaster that corrects provisional values for their expected revision before forecasting."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date

from vintage_lakehouse.backfill.correction import CorrectionConfig, estimate_correction
from vintage_lakehouse.backfill.vintages import VintageTable
from vintage_lakehouse.replay.harness import Forecast, Forecaster
from vintage_lakehouse.storage.model import Observation


class BackfillCorrected:
    """Wraps a forecaster and gives it a history corrected for expected revisions.

    The cutoff of the estimate is the latest ``knowledge_time`` in the history it receives. In a
    real-time replay that is the last report available at the origin, so the correction only uses
    what a forecaster could have known then (ADR 0010). It is not meant for hindsight histories,
    which are already settled.

    The wrapped forecaster is called unchanged, on the same periods with the same times; only the
    values of the most recent periods are rewritten. Its forecast is returned as it is.
    """

    def __init__(
        self,
        base: Forecaster,
        table: VintageTable,
        config: CorrectionConfig | None = None,
        *,
        name: str | None = None,
    ) -> None:
        self.name = name or f"{base.name}+backfill"
        self._base = base
        self._table = table
        self._config = config or CorrectionConfig()

    @property
    def min_history(self) -> int:
        """The shortest history the wrapped forecaster accepts (1 if it does not say)."""
        minimum = getattr(self._base, "min_history", 1)
        return minimum if isinstance(minimum, int) else 1

    def forecast(self, history: Sequence[Observation], targets: Sequence[date]) -> Forecast:
        """Forecast with the base forecaster on the corrected history."""
        if not history:
            return self._base.forecast(history, targets)
        cutoff = max(observation.knowledge_time for observation in history)
        factors = estimate_correction(self._table, cutoff, self._config)
        latest = history[-1].event_time
        step = self._config.step_days
        adjusted: list[Observation] = []
        for observation in history:
            days = (latest - observation.event_time).days
            if days % step:
                adjusted.append(observation)
                continue
            value = factors.apply(observation.value, days // step)
            adjusted.append(replace(observation, value=value))
        return self._base.forecast(tuple(adjusted), targets)
