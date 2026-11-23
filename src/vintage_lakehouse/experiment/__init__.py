"""The leakage-gap experiment: run the baselines in real time and in hindsight, and measure the gap.

The pieces are a configuration and runner (``runner``), the statistics with block-bootstrap
confidence intervals (``analysis``, ``bootstrap``), and the output files (``report``). The
design is recorded in docs/adr/0009-leakage-gap-experiment.md and docs/EXPERIMENT.md.
"""
