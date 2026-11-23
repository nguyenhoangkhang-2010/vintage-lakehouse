"""Tests for the experiment report, manifest and output files."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from experiment_fixture import revised_store, small_config
from replay_fixture import LastValue
from vintage_lakehouse.experiment.report import (
    build_manifest,
    dataset_id,
    render_report,
    write_outputs,
)
from vintage_lakehouse.experiment.runner import (
    ExperimentAnalyses,
    ExperimentConfig,
    ExperimentResult,
    run_analyses,
    run_experiment,
)
from vintage_lakehouse.forecast import SeasonalNaive
from vintage_lakehouse.ingestion.lineage import IngestReport, LineageLog

UTC = timezone.utc
NOW = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)


def finished_run() -> tuple[ExperimentConfig, ExperimentResult, ExperimentAnalyses]:
    cfg = small_config(models=("seasonal_naive",))
    result = run_experiment(revised_store(), [SeasonalNaive()], cfg)
    return cfg, result, run_analyses(result)


def lineage_report(sha: str | None, status: str = "ok") -> IngestReport:
    return IngestReport(
        run_id="r",
        source="s",
        request="q",
        started_at=NOW,
        finished_at=NOW,
        status=status,
        error=None if status == "ok" else "boom",
        rows_fetched=1,
        vintages=1,
        new_versions=1,
        duplicates=0,
        new_periods=1,
        revisions=0,
        unchanged=0,
        max_abs_revision=None,
        raw_sha256=sha,
    )


def test_the_dataset_id_hashes_the_raw_responses_whatever_their_order(tmp_path: Path) -> None:
    first, second = LineageLog(tmp_path / "a.jsonl"), LineageLog(tmp_path / "b.jsonl")
    for sha in ("bbb", "aaa"):
        first.append(lineage_report(sha))
    for sha in ("aaa", "bbb", "aaa"):  # repeated and reordered
        second.append(lineage_report(sha))
    expected = hashlib.sha256(b"aaa\nbbb").hexdigest()
    assert dataset_id(first) == dataset_id(second) == expected


def test_failed_runs_and_missing_hashes_do_not_count_towards_the_dataset_id(tmp_path: Path) -> None:
    log = LineageLog(tmp_path / "log.jsonl")
    log.append(lineage_report("aaa"))
    log.append(lineage_report("zzz", status="failed"))
    log.append(lineage_report(None))
    assert dataset_id(log) == hashlib.sha256(b"aaa").hexdigest()


def test_there_is_no_dataset_id_without_lineage(tmp_path: Path) -> None:
    assert dataset_id(LineageLog(tmp_path / "missing.jsonl")) is None


def test_the_manifest_describes_the_run() -> None:
    cfg, result, _ = finished_run()
    manifest = build_manifest(
        result, data_id="abc", argv=["experiment", "leakage-gap"], started_at=NOW, finished_at=NOW
    )
    assert manifest["dataset_id"] == "abc"
    assert manifest["command"] == ["experiment", "leakage-gap"]
    assert manifest["config"]["series_id"] == cfg.series_id
    assert manifest["config"]["evaluation_time"] == cfg.evaluation_time.isoformat()
    assert manifest["config"]["horizons"] == [1, 2]
    assert "vintage-lakehouse" in manifest["packages"]
    assert manifest["python"]
    assert set(manifest["scored_forecasts"]) == {t.isoformat() for t in cfg.evaluation_times()}
    json.dumps(manifest)  # must be serialisable


def test_the_report_contains_the_setup_the_headline_and_the_caveats() -> None:
    cfg, result, analyses = finished_run()
    text = render_report(result, analyses, data_id="abc", generated_at=NOW)
    assert text.startswith("# Leakage-gap experiment")
    assert "Data identifier: `abc`" in text
    assert "## Headline: T_eval=" in text
    assert "| seasonal_naive | all |" in text
    assert "## Sensitivity" in text
    assert "excluding origins" in text
    assert "## Does the gap grow with the size of recent revisions?" in text
    assert "## Reading these numbers" in text
    assert "block" in text and "bootstrap" in text
    assert f"seed {cfg.seed}" in text


def test_models_are_compared_in_the_report_when_there_are_several() -> None:
    cfg = small_config(models=("seasonal_naive",))
    result = run_experiment(revised_store(), [SeasonalNaive(), LastValue()], cfg)
    text = render_report(result, run_analyses(result), data_id=None, generated_at=NOW)
    assert "## Do the models differ?" in text
    assert "Data identifier: `unknown`" in text


def test_the_files_are_written_and_consistent_with_the_analyses(tmp_path: Path) -> None:
    cfg, result, analyses = finished_run()
    manifest = build_manifest(result, data_id=None, argv=[], started_at=NOW, finished_at=NOW)
    report = render_report(result, analyses, data_id=None, generated_at=NOW)
    paths = write_outputs(tmp_path / "out", result, analyses, manifest, report)

    assert {p.name for p in paths} == {
        "leakage_gap.csv",
        "model_comparisons.csv",
        "revision_size_groups.csv",
        "revision_size_correlation.csv",
        "scores.csv",
        "manifest.json",
        "report.md",
    }
    assert all(p.exists() for p in paths)

    with (tmp_path / "out" / "leakage_gap.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    expected = sum(len(a.estimates) for a in analyses.by_scenario.values())
    assert len(rows) == expected
    primary = [r for r in rows if r["scenario"] == cfg.primary.label and r["horizon"] == ""]
    [pooled] = primary
    estimate = next(e for e in analyses.by_scenario[cfg.primary].estimates if e.horizon is None)
    assert float(pooled["gap"]) == estimate.gap
    assert int(pooled["n_pairs"]) == estimate.n_pairs

    with (tmp_path / "out" / "scores.csv").open(encoding="utf-8", newline="") as handle:
        scores = list(csv.DictReader(handle))
    assert len(scores) == sum(len(r) for r in result.rows_by_eval.values())
    assert {s["mode"] for s in scores} == {"realtime", "hindsight"}

    loaded = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    assert loaded["config"]["series_id"] == cfg.series_id
    assert (tmp_path / "out" / "report.md").read_text(encoding="utf-8") == report


def test_the_output_directory_is_created_and_files_use_unix_line_endings(tmp_path: Path) -> None:
    _, result, analyses = finished_run()
    manifest = build_manifest(result, data_id=None, argv=[], started_at=NOW, finished_at=NOW)
    paths = write_outputs(tmp_path / "deep" / "nested", result, analyses, manifest, "# r\n")
    assert all(b"\r\n" not in p.read_bytes() for p in paths)
