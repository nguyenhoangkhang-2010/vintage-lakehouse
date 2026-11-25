# Auditing a dataset and a pipeline

Three commands, designed in [ADR 0011](adr/0011-trust-report-and-leakage-linter.md). Run them
from the project folder with the data ingested (see the README, "Loading data").

## Trust report: how are this dataset's values revised?

```text
vintage-lakehouse audit trust-report --out results/trust.md
```

Options: `--series`, `--as-of 2020-01-01` (use only what was known then), `--format json`,
`--settle N` (how many later periods make a period "settled", default 26). It prints facts, not a
score: coverage, revision size by age, how long values take to settle, bias by calendar year, and
a few plain-language findings with fixed thresholds (ADR 0011). About a minute on the national
FluView series.

On the FluView national series (US weighted ILI, 873 weeks) it reports, among other things:

- the first release differs from the settled value by 5.1% on average (13.2% at the 90th
  percentile), and 79.6% of first releases are more than 1% away from their final value;
- **the bias drifts**: the median settled/first-release ratio was 1.12 to 1.13 in 2011 and 2012,
  0.975 in 2016 and between 0.99 and 1.02 from 2021. That is the pattern that made the backfill correction
  of [RESULTS.md](RESULTS.md) fail. The report raises it without anyone having run a forecast;
- values keep changing slightly for a long time: the median period takes 641 days from first
  release to last revision, and 90% need more than 26 weeks to stay within 1% of their final
  value.

Read the findings as prompts to look, not verdicts. The thresholds (2% bias, 5 points of drift,
4 periods to settle) are conventions, not tested limits.

## Leak check: does my pipeline use the future?

A pipeline is a function `pipeline(store, series_id, origin)` that returns a number, a mapping or
a sequence of numbers. The check runs it at each origin on the real data, on data where every
version published after the origin is altered, and on data where it is removed. A pipeline that
only uses what was known at the origin cannot tell them apart.

```text
vintage-lakehouse audit leak-check --pipeline clean_pipeline:pipeline --pipeline-path examples/audit
vintage-lakehouse audit leak-check --pipeline leaky_pipeline:pipeline --pipeline-path examples/audit
```

Defaults: origins every 52 weeks from 2015-01-03 to 2024-12-28 (`--first-origin`,
`--last-origin`, `--step-weeks`). Exit code 0 only if at least one origin proved clean and none
leaked, failed or was nondeterministic; 1 otherwise. On the real FluView data the clean example
passes at all five origins tried, and the example with a seeded leak (it reads a snapshot of
today's data) is flagged at all five:

```text
2015-01-03  LEAK  altering later data changed the result by 2.69917; removing later data changed ...
LEAK at 5 of 5 origins.
```

What it cannot do: find a leak at origins that do not exercise it, look inside a model, see data
the pipeline reads from outside the store, or handle a pipeline that is not deterministic (that
is reported, not guessed). An origin with no later data is *inconclusive*, never clean. It
runs your code, so use it on your own pipelines.

## Lint: patterns that often leak

```text
vintage-lakehouse audit lint path/to/pipeline.py path/to/folder
```

An AST scan for: negative `shift` (LA001), back-fill (LA002), the wall clock (LA003), `as_of`
called with a fixed time (LA004), `revisions()` (LA005), a shuffled `train_test_split` (LA006)
and a centred rolling window (LA007). Exit code 1 if anything is found.

It is a heuristic and the examples show both errors:

- **A false negative:** the leaky example above passes the lint, because the far-future time
  goes through a variable. The leak check catches it.
- **False positives:** run on this project's own source it reports nine findings, all benign:
  timestamps for logs and the stores' own use of `revisions()`. A negative shift used to build
  a *target* is also fine.

So: use the lint for review prompts, and the leak check as the test.
