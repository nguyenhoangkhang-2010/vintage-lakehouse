# ADR 0009: The leakage-gap experiment

- Status: Accepted. Written before the experiment was run on the full data.

## Context

The primary question is by how much hindsight evaluation overstates real-time forecast skill
(see `docs/RESEARCH_QUESTION.md`). The replay harness and three baselines exist (ADR 0007 and
0008). What remains is to decide, before looking at results, exactly what is computed and how
uncertainty is quantified, so that the analysis cannot drift towards whatever looks interesting.

Only toy and smoke-test numbers had been seen when this was written: a toy forecaster and 92
origins, which suggested gaps of a few percent. No full run existed.

## Decisions

### Design of the run

- **Series:** US national weighted ILI from Delphi FluView, as ingested (ADR 0004).
- **Origins:** every 4 weeks from 2015-01-03 to 2025-06-28 (137 origins), identical for all
  models. The start leaves at least five years of history for the models; the end leaves more
  than a year for the last targets to settle before the primary `T_eval`. A 4-week cadence
  keeps gradient boosting affordable (about 4.4 s per origin).
- **Horizons:** 1 to 4 weeks ahead of the latest reported week.
- **Models:** seasonal naive, ETS and gradient boosting, untuned, with a lower bound of 0
  (ADR 0008).
- **Primary `T_eval`:** 2026-09-23, the last date the legacy API serves, so hindsight data is
  the most settled version available.
- **Efficiency:** real-time forecasts do not depend on `T_eval`, so they are computed once and
  reused. A test checks that caching changes no score.

### Primary outcome

For each model, the **pooled leakage gap** at the primary `T_eval` over all origins and horizons:
`(sum WIS real time - sum WIS hindsight) / sum WIS real time`, with a 95% confidence interval.
Per-horizon figures and the absolute degradation (WIS units) are reported alongside. The
relative gap is unstable when the real-time WIS is small, which is why the absolute figure is
always shown.

### Uncertainty

A **moving-block bootstrap over origins**: blocks of 3 consecutive origins (12 weeks), 2,000
resamples, percentile intervals, seed 0. Every statistic is recomputed on the same resample, so
differences between models keep their correlation.

Limitation, stated up front: a flu season spans about 30 weeks, longer than the 12-week block,
so dependence beyond a block is not preserved and the intervals may still be somewhat too
narrow. The block length is a parameter, and a longer block is checked as a robustness analysis.

### Secondary analyses (fixed now)

- **Sensitivity to `T_eval`:** the primary time and 13, 26 and 52 weeks after the last origin
  (52 is dropped if it is not earlier than the primary).
- **Sensitivity to the origin set:** each of the above again without origins from 2020-03-01
  to 2021-09-30, when influenza surveillance was unusual (COVID-19). The window is an
  analyst's choice and is stated, not tuned.
- **Differences between models:** the difference of pooled gaps, for every pair, with the same
  bootstrap (hypothesis H2).
- **Size of recent revisions (H1):** for each origin, the mean absolute revision of its last 4
  reported weeks between then and the primary `T_eval`. Origins are split into three equal
  groups, and the Spearman correlation with the per-origin WIS degradation is given with a
  bootstrap interval.

These are exploratory. No p-values are reported and there is no correction for multiple
comparisons. Statements will describe intervals ("the interval excludes zero"), not
significance.

### What the results will not claim

- Anything about other series, diseases, countries or forecasting methods.
- That a gap is "caused by" revisions beyond what the design shows.
- Anything about the H3 hypothesis (a backfill-aware correction). That is the next branch.

### Outputs

One command writes a report, CSV files for every table, the per-forecast scores, and a manifest
with the configuration, an identifier of the data (a hash of the raw responses), library
versions and timings.

## Addendum after the run

The plan above was not changed after seeing results. Two things were learned, recorded here
without altering the plan:

- The pooled gap was the same to one decimal at all four evaluation times. Checking showed this
  is real (hindsight scores do differ, but for about 3% of targets and by at most 0.005 WIS), so
  the `T_eval` grid is uninformative for *early* hindsight times. An evaluation time relative to
  each origin is a candidate follow-up, not part of this experiment.
- The worry about too-short bootstrap blocks was checked by re-analysing the same scores with
  4, 12, 26 and 52-week blocks: interval widths were practically unchanged. This check was done
  once outside the repository.

Results are in `docs/RESULTS.md`.

## Consequences

- A gap can be negative. A first release that is biased low can, by accident, pull a naive
  forecast towards a falling series. The relative gap is defined as an improvement, so such
  cases appear as negative numbers and are reported as they are.
- Results may differ in the last digits across library versions (statsmodels, scikit-learn).
  The manifest records the versions, and figures are quoted to one decimal place.
- The run takes tens of minutes, dominated by gradient boosting.
- Until the warehouse path fix lands, the command must be run from the project folder against
  a store created there.
