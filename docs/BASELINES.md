# Baseline forecasters

Three deliberately simple forecasters for the replay harness. They are not tuned and not meant
to win: the project measures how much *evaluation on revised data* overstates real-time skill,
and for that a small set of well-understood models with different exposure to revisions is
more useful than one clever model. Design decisions are in
[ADR 0008](adr/0008-baseline-forecasters.md).

| Model | Idea | Quantiles come from | Minimum history | Extra dependencies |
|-------|------|---------------------|-----------------|--------------------|
| `seasonal_naive` | The value one season (52 weeks) ago | Past seasonal errors, from a similar point of the season | `season + min_errors` (60 periods) | none |
| `ets` | Exponential smoothing, additive error, damped additive trend, no seasonality | The fitted model's prediction interval, treated as normal | 52 periods | `baselines` |
| `gbm` | Gradient boosting on lagged values, one model per horizon and quantile | Quantile regression, one model per level | `season + lags + min_train_rows` (100 periods) | `baselines` |

Install the optional dependencies with `pip install -e ".[baselines]"` (included in `.[dev]`).

## Shared conventions

- **Quantile levels.** All models produce the same levels, `0.025, 0.1, 0.25, 0.5, 0.75, 0.9,
  0.975`, so their weighted interval scores are comparable.
- **Regular weekly series.** The history is turned into one value per 7 days. A missing period
  is filled with the value before it.
- **Lower bound.** `lower_bound=0.0` clips quantiles at zero, which suits rates such as
  weighted ILI. Clipping can only move a forecast towards a plausible value.
- **No crossing.** Quantile values are sorted across levels, so they are always non-decreasing.
- **Deterministic.** The same history and targets always give the same forecast. The gradient
  boosting models use a fixed seed.
- **Errors are explicit.** A history that is too short raises `InsufficientHistory`. A model
  that cannot be fitted raises `ForecastFailure`. Set `ReplayConfig.min_history` to the largest
  `min_history` of the models in use, so that short origins are skipped instead of failing.

## Sanity tests

Each baseline has tests that check it behaves the way its definition says, on series where the
right answer is known. These are checks of correctness, not measurements of skill.

| Model | Sanity check | What it shows |
|-------|--------------|---------------|
| `seasonal_naive` | An exactly periodic series is forecast exactly, with no spread | The seasonal lookup is aligned correctly |
| `seasonal_naive` | A constant seasonal error of +1 shifts the forecast by 1 | The error distribution feeds the quantiles |
| `seasonal_naive` | Quantiles match values worked out by hand from the past errors | The interpolation is the one documented |
| `seasonal_naive` | `window` selects errors from a similar point of the season, falling back to all errors when too few | The window logic |
| `ets` | A constant series is forecast as that constant | The level is estimated correctly |
| `ets` | A rising series is forecast to keep rising, close to the next value | The trend component works |
| `ets` | The interval never narrows as the horizon grows | Uncertainty accumulates |
| `gbm` | An exactly periodic series is forecast close to the next season | It learns the seasonal feature |
| `gbm` | Features and labels line up for a known series, and the row cap keeps the most recent rows | No off-by-one in the training set |
| all | Valid quantile forecast, deterministic, history not modified, bad targets rejected | The contract with the replay harness |

## Exposure to revisions (a check on synthetic data, not a result)

On a synthetic series that repeats one 52-week pattern, with each week first published 10% too
low and revised a week later, the three models react very differently. This is the kind of
difference the project measures on real data.

| Model | WIS in real time | WIS in hindsight | Reading |
|-------|------------------|------------------|---------|
| `seasonal_naive` | 0.0018 | 0.0000 | Median identical in both runs; only its interval is slightly affected |
| `ets` | 0.84 | 0.019 | Extrapolates from the latest value, so a provisional value hurts a lot |
| `gbm` (small settings) | 0.47 | 0.48 | Not harmed here, but also a weak forecaster with these settings |

These numbers come from `tests/test_baselines_replay.py` on invented data. They show that the
harness distinguishes the models, nothing about influenza or any real series.

## Cost

The gradient boosting baseline trains one model per quantile level for every horizon at every
origin: with 7 levels and 4 horizons that is 28 small models per forecast, so it is by far the
slowest. Measured once on a laptop (Windows, Python 3.10) with the Iceberg store and the
national FluView series, replaying 92 origins with 4 horizons in both modes:

| Model | Time per origin (real time + hindsight) |
|-------|------------------------------------------|
| `seasonal_naive` | 0.16 s (almost all of it reading the store) |
| `ets` | 0.23 s |
| `gbm` | 4.4 s |

At that speed 548 weekly origins would take about 40 minutes for `gbm`, against seconds for
the others, so the experiment will thin the origins for it (every 6 weeks took about 7
minutes). `max_train_rows` (default 416, about 8 years of weekly data) caps the cost as the
history grows. These timings are indicative: one run, one machine.

## Using a baseline with the replay

```python
from vintage_lakehouse.forecast import SeasonalNaive
from vintage_lakehouse.forecast.ets import ETSForecaster
from vintage_lakehouse.replay import ReplayConfig, leakage_gap, run_replay

models = [SeasonalNaive(lower_bound=0.0), ETSForecaster(lower_bound=0.0)]
config = ReplayConfig(
    series_id="delphi/fluview/nat/wili",
    origins=origins,
    horizons=[1, 2, 3, 4],
    evaluation_time=t_eval,
    min_history=max(model.min_history for model in models),
)
gaps = leakage_gap(run_replay(store, models, config).rows)
```
