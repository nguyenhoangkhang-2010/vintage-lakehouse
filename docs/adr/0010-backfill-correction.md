# ADR 0010: Backfill-aware correction and how its benefit is measured

- Status: Accepted. Written before the correction was run on the evaluation origins.

## Context

ADR 0009 measured the leakage gap: ETS looks 6.4% better and gradient boosting 2.6% better when
they are fitted on revised data rather than on what was known at the time. Hypothesis H3 asks
whether that gap can be partly **recovered in real time**, by modelling how provisional values get
revised and correcting for it before forecasting.

What was looked at before writing this: the size of revisions by age, on data known before
2015-01-03 only (259 weeks, 2010 to 2014). The first release of a week was on average 9% below its
settled value (median ratio 1.09; median difference +0.12 wILI points); one week later the bias was
about 1%, and from the third report on it was below 1% in median. No evaluation origin (2015 or
later) was used to choose anything below.

## Decisions

### The correction

A correction is a **wrapper around any forecaster**. It rewrites the history, then calls the
unchanged forecaster on the rewritten history:

1. **Lag** of a value = how many periods before the latest reported period it lies (0 for the
   latest). The lag measures how many reports the value has been through, whatever its
   `knowledge_time` says.
2. **Training pairs** at a cutoff `c`: for every period `p` that was already *settled* at `c`
   (at least `settle` = 26 periods before the latest period reported at `c`) and every lag
   `k < max_lag` = 4, the pair is the value of `p` as it stood when the latest reported period
   was `p + k` (a first release of `p + k` is the clock), and the value of `p` at `c`.
3. **Estimate**, per lag, the **median ratio** settled / provisional (primary variant). A lag with
   fewer than `min_samples` = 30 pairs, or with a provisional value of 0 or less, is not
   corrected (factor 1). Lags from `max_lag` on are not corrected.
4. **Apply** the factor of each lag to the values of the history, then forecast.

The cutoff is the latest `knowledge_time` found in the history it is given. In real time that is
the last report available at the origin, so the estimate can only use what was known then. The
forecaster's spread is not widened to reflect the uncertainty of the correction (see limitations).

Variants, all fixed now:

| Variant | What changes |
|---------|--------------|
| `backfill` (primary) | multiplicative, median ratio per lag |
| `backfill-additive` | difference instead of ratio, median per lag, result floored at 0 |
| `backfill-placebo` | the factors of the primary variant applied to the *wrong* lags (lag order reversed within `max_lag`) |

The placebo is a control: it has the same machinery and the same average size of change but
puts it on the wrong values. If it helped as much as the primary variant, the benefit would not
be coming from knowing which values are provisional.

The parameters (26, 4, 30, median, ratio) are chosen from the description above, not tuned. A
second look at other values would be a robustness analysis reported next to this one, not a
replacement.

### Where it is evaluated

Only in real time. A hindsight history is already settled, so correcting it makes no sense; the
leakage gap of the uncorrected model is the reference for what could be recovered. Everything else
is as in ADR 0009: US national weighted ILI, 137 origins every 4 weeks from 2015-01-03 to
2025-06-28, horizons 1 to 4, primary `T_eval` 2026-09-23, untuned baselines. The scores of the
base models are recomputed in the same run (same code, same data) rather than copied.

A **development window** (origins every 4 weeks from 2012-01-07 to 2014-10-04, `T_eval`
2015-01-03, seasonal naive and ETS, 36 origins) is used for smoke tests and debugging of the
pipeline only. Nothing in the parameters or the variant list above was changed after seeing it.

What it showed, recorded so that the evaluation is read with it in mind: in that window the
leakage gap of ETS was *negative* (-4.7%, interval -16% to +21%), i.e. a first release that is
too low happened to help ETS, and the primary (multiplicative) correction made ETS worse
(improvement -0.052 WIS, relative -16%, interval -33% to +14%), while the additive variant and the
placebo were close to neutral. For seasonal naive all variants changed the score by less than 1%.
The window is short and its gap is not significantly different from zero, so this is a warning
about ETS, not a finding. The evaluation origins may behave differently, and the plan stands.

### Outcomes

For each base model and variant, over matched triples (same origin and horizon):

- `a` = real-time WIS of the base model, `b` = its hindsight WIS, `c` = real-time WIS of the
  corrected model.
- **Improvement** `mean(a - c)` in WIS units and relative improvement `sum(a - c) / sum(a)`.
- **Recovery** `sum(a - c) / sum(a - b)`: the share of the leakage gap that the correction
  recovers. 100% would match hindsight; 0% is no help; negative means it hurts.

All with the moving-block bootstrap of ADR 0009 (12-week blocks over origins, 2,000 resamples,
seed 0), recomputed on the same resample for every quantity. **Recovery is reported only for
models whose gap interval from ADR 0009 excludes zero** (ETS, gradient boosting); for the
others the denominator is too close to zero for the ratio to mean anything, and only the
improvement is given. Exploratory, no p-values, no multiple-comparison correction.

Also fixed now, before the run on the evaluation origins:

- **Differences between variants:** for each model, the difference in mean improvement of every
  other variant relative to the primary one, from the same resamples.
- **Sensitivity:** the whole analysis again without origins from 2020-03-01 to 2021-09-30, as in
  ADR 0009 (an analyst's choice, stated and not tuned).
- **Per horizon:** improvement per horizon for the primary variant.

Per-lag factors estimated at the last origin, and the lag-0 factor at every origin, are reported
to show what the correction does and how stable it is.

### What the results will not claim

- Anything about other series, models that are not the three baselines, or other revision
  patterns. A correction fitted on one series is not evidence for another.
- That recovering the gap means better forecasts in general. The comparison is between the same
  model with and without correction on the same scored targets.
- That the leftover gap is "due to" anything specific.

## Safeguards (tested)

- Changing or adding any version with `knowledge_time` after the cutoff does not change the
  estimated factors or the forecast (a leak test), and a store truncated at the cutoff gives the
  same factors as the full store.
- Factors are hand-computed on a small series with known revisions.
- With no revisions in the data the correction is the identity and the corrected forecast equals
  the base forecast.
- The placebo and variants are distinct, and every variant is deterministic.

## Addendum after the run

The plan above was not changed after seeing results. Outcome (`docs/RESULTS.md`): H3 was not
supported. The primary correction made ETS worse (relative -9.3%, interval -15.4% to -1.8%),
had no detectable effect on gradient boosting (+0.4%, interval -1.2% to +1.8%), and the placebo
was no better. As the development window had warned, ETS was hurt.

A diagnosis, made afterwards and not part of the plan: the first-release bias that the expanding
window learns (about +10% in 2011 to 2013) is near zero from 2015 on, so the correction is
wrong in the early evaluation years. The revision pattern is not stationary. A recent-window
estimate is a candidate follow-up, but it was suggested by these results and has not been run.

## Consequences

- The correction needs access to the vintages of past periods, not only the latest value of each,
  so it is built on a `VintageTable` loaded from a store, and not on the plain `Forecaster` input.
  The cutoff rule keeps this honest, and the leak test checks it.
- The correction only touches the last few values, so it can recover at most the part of the gap
  that comes from those. Revisions beyond `max_lag` are left alone.
- Replays can now be restricted to real time (`ReplayConfig.modes`), to avoid forecasting
  hindsight histories that would be discarded.
