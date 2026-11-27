# ADR 0008: Baseline forecasters

- Status: Accepted (implemented in `feature/baseline-forecasters`)

## Context

The replay harness needs forecasters that produce quantile forecasts. The research question is
about the *evaluation* of forecasts on revised data, not about building the best forecaster.
Models therefore need to be simple and well understood, cheap enough to run for hundreds of
origins, and different enough in how they use recent values that their reaction to revisions
differs.

## Decision

Three baselines, all producing the same quantile levels:

- **Seasonal naive.** Median = the value one season ago. Spread = quantiles of the errors this
  rule made in the past at a similar point of the season. Written from scratch with no
  dependencies. It ignores the most recent values.
- **ETS.** Additive error, damped additive trend, no seasonal component (statsmodels
  `ETSModel`). The fitted model's prediction interval gives a normal spread per horizon. It
  extrapolates from the latest values. Seasonality is omitted on purpose: a 52-week seasonal
  ETS is slow and unstable, and seasonal naive and gradient boosting already cover seasonality.
- **Gradient boosting.** One scikit-learn `GradientBoostingRegressor` per horizon and quantile
  level (quantile loss) on the last 8 values, the value one season before the target and the
  day of the year as sine and cosine. Direct multi-horizon, fixed seed, only the most recent
  416 rows used.

Shared conventions: the same seven quantile levels; a regular weekly series with gaps
forward-filled; quantiles sorted so they never cross; an optional lower bound; deterministic
output; explicit errors for too short a history (`InsufficientHistory`) and for a model that
cannot be fitted (`ForecastFailure`).

The ETS and gradient boosting baselines sit behind an optional `baselines` extra and are
imported from their own modules, so the core package and `SeasonalNaive` need no third-party
dependencies.

## Why these, and no tuning

No model is tuned. Tuning on the same series would add a way for the results to depend on
choices made with hindsight, which is the very thing the project examines. Hyper-parameters are
set once, documented, and the same for real-time and hindsight runs.

## Alternatives considered

- **ARIMA.** Comparable to ETS for this purpose, and slower to fit. ETS is enough for a
  level-and-trend baseline.
- **Seasonal ETS or SARIMA with a 52-week period.** Slow and fragile; the seasonal part is
  covered by the other two baselines.
- **Gradient boosting with a point forecast and residual quantiles.** Cheaper (one model per
  horizon instead of seven) but the residuals are in-sample and understate the uncertainty.
  Direct quantile regression was kept despite the cost.
- **Neural or foundation models.** Out of scope: they would need data and compute the project
  does not assume, and they would blur the effect being measured.

## Consequences

- The gradient boosting baseline is slow (seconds per forecast). A full replay over many origins
  will thin the origins, which is a decision for the experiment branch.
- The models differ in exposure to revisions, which is intended: a model that looks far back
  (seasonal naive) is barely touched by a revision to the latest value, one that extrapolates
  from it (ETS) is touched a lot. A check on synthetic data shows this and is recorded in
  `docs/BASELINES.md`.
- Models have minimum history requirements. Experiments set `min_history` accordingly.
- The results say something about these models on this data, not about forecasting methods in
  general.
