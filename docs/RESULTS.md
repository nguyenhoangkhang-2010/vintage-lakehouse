# Results: the leakage gap on US influenza surveillance data

These are the first measured results of the project. They come from the experiment designed in
[ADR 0009](adr/0009-leakage-gap-experiment.md) before it was run, and they describe **one series
and three simple baselines**. Nothing here applies to other diseases, countries or forecasting
methods.

Data: weekly weighted influenza-like illness for the US (national), from the Delphi Epidata API
(Carnegie Mellon University), which redistributes CDC FluView data. Only derived statistics are
published here. The licence of the data has not been checked yet (see
[DATA_SOURCES.md](DATA_SOURCES.md)).

## What was measured

Each model forecast 1 to 4 weeks ahead from 137 origins (every 4 weeks, 2015-01-03 to
2025-06-28), twice: from what was known at the origin (real time), and from the same weeks with
their revised values (hindsight, `T_eval = 2026-09-23`). Both were scored with the weighted
interval score (WIS, lower is better) against the same truth.

**Gap** = `(WIS real time - WIS hindsight) / WIS real time`: how much lower a model's error is
when it is given revised data. A positive gap means a hindsight evaluation makes the model look
better than it would have been in real time.

## Headline

Pooled over horizons, with 95% confidence intervals from a block bootstrap over origins:

| Model | WIS real time | WIS hindsight | Absolute degradation | Gap |
|-------|---------------|---------------|----------------------|-----|
| seasonal naive | 0.4758 | 0.4748 | 0.0010 [-0.0007, 0.0028] | 0.2% [-0.2%, 0.6%] |
| ETS | 0.2749 | 0.2573 | 0.0177 [0.0033, 0.0279] | 6.4% [1.3%, 10.0%] |
| gradient boosting | 0.2486 | 0.2422 | 0.0064 [0.0016, 0.0121] | 2.6% [0.7%, 4.9%] |

In words: revised data lowers the mean WIS of ETS by about 6% and that of gradient boosting by
about 3%, and does not measurably change seasonal naive. The intervals for ETS and gradient
boosting exclude zero; the one for seasonal naive does not.

By horizon (gap, 95% interval):

| Horizon | seasonal naive | ETS | gradient boosting |
|---------|----------------|-----|-------------------|
| 1 week | 0.3% [-0.1%, 0.6%] | 1.2% [-4.4%, 7.8%] | 2.3% [-1.3%, 6.4%] |
| 2 weeks | 0.3% [-0.1%, 0.8%] | 8.3% [1.9%, 12.6%] | 4.6% [1.6%, 8.4%] |
| 3 weeks | 0.2% [-0.2%, 0.6%] | 7.6% [1.9%, 11.2%] | 2.1% [-0.3%, 4.7%] |
| 4 weeks | 0.0% [-0.4%, 0.4%] | 5.9% [1.0%, 9.0%] | 1.6% [-0.7%, 4.1%] |

The one-week ETS figure is too uncertain to say anything about.

## Do the models differ?

Differences between pooled gaps (first minus second):

| Comparison | Difference |
|------------|------------|
| seasonal naive minus ETS | -6.2% [-9.8%, -1.1%] |
| seasonal naive minus gradient boosting | -2.4% [-4.6%, -0.5%] |
| ETS minus gradient boosting | 3.8% [-1.6%, 7.2%] |

Both comparisons with seasonal naive exclude zero. ETS and gradient boosting cannot be told
apart on this evidence. This fits what the models do: seasonal naive looks a season back at
values that are long since revised, while ETS extrapolates from the latest values, which are
the most provisional.

## Does the gap grow with the size of recent revisions?

Origins were split into three equal groups by how much their last four reported weeks were later
revised:

| Model | Smallest revisions | Middle | Largest revisions |
|-------|--------------------|--------|-------------------|
| seasonal naive | 0.2% | 0.0% | 0.3% |
| ETS | 1.6% | 2.3% | 13.1% |
| gradient boosting | 0.8% | 2.7% | 3.3% |

For ETS the gap is concentrated in the origins with the largest recent revisions. But the rank
correlation between revision size and the per-origin degradation is weak: 0.12 [-0.09, 0.30] for
seasonal naive, 0.10 [-0.10, 0.24] for ETS and 0.16 [-0.00, 0.34] for gradient boosting. So the
evidence for "the gap grows with revision size" is partial: a pattern in the largest group, not
a clear monotone relation.

## Robustness

- **Excluding the pandemic.** Removing origins from 2020-03-01 to 2021-09-30 (117 origins left)
  changes nothing noticeable: ETS 6.3% [0.8%, 10.3%], gradient boosting 2.4% [0.3%, 4.9%],
  seasonal naive 0.3% [-0.2%, 0.7%].
- **The evaluation time.** The pooled gaps are the same to one decimal at all four evaluation
  times (2025-09-27, 2025-12-27, 2026-06-27, 2026-09-23). This is real, not a bug: the hindsight
  scores do differ between evaluation times, but only for about 3% of the targets (42 of 1,644)
  and by at most 0.005 WIS. Influenza surveillance data settles quickly. It also means this
  grid cannot say what happens with an *early* hindsight time, for instance only a few weeks
  after each origin. That would need an evaluation time defined relative to each origin.
- **The bootstrap block length.** Re-analysing the same scores with blocks of 4, 12, 26 and 52
  weeks (done once, outside the repository) gives intervals of practically the same width
  (ETS 9.2, 8.8, 9.0 and 9.4 percentage points; gradient boosting 4.1, 4.2, 3.8 and 3.7). The
  worry that 12-week blocks would be too short was not borne out in this range.

## What this does not show

- It does not show that the gap is large in general. For these models on this series it is
  small to moderate, and zero for a model that ignores recent values.
- It does not show anything about series that are revised more heavily than this one, or about
  other forecasting methods.
- The models are untuned baselines. A model that leans harder on the latest values could show
  a larger gap, and one tuned in hindsight could look better than any of these.
- The analyses of model differences and revision size are exploratory, with no correction for
  multiple comparisons.
- The relative gap is unstable when real-time WIS is small. Read it together with the absolute
  degradation.

## Reproducing

```text
vintage-lakehouse experiment leakage-gap --out results
```

Data identifier: `b6c40660e97b800d6ab86ee5731b546061459f0373297ef36ffdda0f3a1b64ca` (the
hash of the 17 raw FluView responses, ingested with `--last-issue 202638`). Python 3.10.11,
numpy 2.2.6, pandas 2.3.3, scikit-learn 1.7.2, statsmodels 0.15.0. The run took about 20
minutes, almost all of it gradient boosting for the first evaluation time. Other library
versions can change the figures in the last digit, so they are quoted to one decimal place.
See [EXPERIMENT.md](EXPERIMENT.md).
