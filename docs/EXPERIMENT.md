# Running the leakage-gap experiment

The design and the analysis plan are in [ADR 0009](adr/0009-leakage-gap-experiment.md), and the
measured results in [RESULTS.md](RESULTS.md). This page is about running it and reading the
output.

## Run it

You need the data ingested (see the README, "Loading data") and the project installed with the
extras: `pip install -e ".[dev]"`. Run from the project folder.

```text
vintage-lakehouse experiment leakage-gap --out results
```

By default this replays 137 origins (every 4 weeks, 2015 to mid 2025), horizons 1 to 4, all three
baselines, at four evaluation times, then bootstraps every scenario. It prints progress and the
full report, and takes tens of minutes, almost all of it gradient boosting.

Useful options (see `vintage-lakehouse experiment leakage-gap --help`):

| Option | Use |
|--------|-----|
| `--models seasonal_naive,ets` | Skip the slow gradient boosting model |
| `--origin-step-weeks 8` | Fewer origins, faster, wider intervals |
| `--n-boot 500` | Faster bootstrap for a quick look |
| `--block-weeks 26` | Longer bootstrap blocks (a robustness check) |
| `--seed N` | Another bootstrap seed |
| `--evaluation-time 2026-09-23` | Primary `T_eval` (must be after the last origin) |

## What is written to `--out`

| File | Content |
|------|---------|
| `report.md` | The tables below, ready to read |
| `leakage_gap.csv` | Every estimate: scenario, model, horizon, WIS, gap and intervals |
| `model_comparisons.csv` | Differences between models' pooled gaps |
| `revision_size_groups.csv`, `revision_size_correlation.csv` | The revision-size analysis |
| `scores.csv` | Every scored forecast, for your own analysis |
| `manifest.json` | Configuration, data identifier, library versions, timings |

`results/` is ignored by git: the numbers are derived from data whose licence has not been
checked. Quote them in documents instead, with the manifest's data identifier.

## Reading the report

- **Gap** = `(WIS real time - WIS hindsight) / WIS real time`. Positive: the model looks better
  on revised data than it would have in real time. It can be negative.
- **The interval** reflects which origins were sampled, using a block bootstrap. It does not
  include model misspecification or the choice of `T_eval`; the sensitivity table shows the
  latter.
- **A relative gap is unstable when real-time WIS is small.** Check `degradation` (WIS units).
- **Pooled** is a ratio of mean WIS over all horizons, so longer horizons weigh more.

## Reproducing a run

Two runs with the same configuration and data give identical CSV files. The manifest's
`dataset_id` must match: it is a hash of the raw responses the store was built from. Figures can
differ in the last digits if the versions of statsmodels or scikit-learn differ.
