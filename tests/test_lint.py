"""Tests for the static look-ahead lint: every rule has a positive and a negative example."""

from __future__ import annotations

from pathlib import Path

import pytest

from vintage_lakehouse.audit import RULES, lint_paths, lint_source


def rules(source: str) -> list[str]:
    return [f.rule for f in lint_source(source)]


@pytest.mark.parametrize(
    ("rule", "bad", "good"),
    [
        (
            "LA001",
            "x = df['v'].shift(-1)\n",
            "x = df['v'].shift(1)\ny = df['v'].shift(periods=2)\n",
        ),
        ("LA001", "x = df.v.shift(periods=-3)\n", "x = df.v.shift()\n"),
        ("LA002", "x = s.bfill()\n", "x = s.ffill()\n"),
        ("LA002", "x = s.fillna(method='bfill')\n", "x = s.fillna(0.0)\n"),
        (
            "LA003",
            "import datetime\nt = datetime.datetime.now()\n",
            "def f(origin):\n    return origin\n",
        ),
        ("LA003", "from datetime import date\nt = date.today()\n", "t = other.today\n"),
        ("LA003", "import time\nt = time.time()\n", "import time\ntime.sleep(1)\n"),
        (
            "LA004",
            "store.as_of('s', datetime(2030, 1, 1, tzinfo=UTC))\n",
            "store.as_of('s', origin)\nstore.as_of('s', origin - timedelta(days=7))\n",
        ),
        ("LA004", "store.as_of(series, at=datetime.max)\n", "store.as_of(series, at=origin)\n"),
        ("LA004", "store.as_of('s', FAR)\nstore.as_of('s', 1700000000)\n", "x = store.as_of\n"),
        (
            "LA005",
            "v = store.revisions('s', day)[-1]\n",
            "v = store.as_of('s', origin)\n",
        ),
        (
            "LA006",
            "a, b = train_test_split(x, y)\n",
            "a, b = train_test_split(x, y, shuffle=False)\n",
        ),
        (
            "LA006",
            "a, b = model_selection.train_test_split(x, shuffle=True)\n",
            "a, b = model_selection.train_test_split(x, shuffle=False)\n",
        ),
        (
            "LA007",
            "r = s.rolling(5, center=True).mean()\n",
            "r = s.rolling(5).mean()\nq = s.rolling(5, center=False).mean()\n",
        ),
    ],
)
def test_each_rule_fires_on_the_bad_example_and_not_on_the_good_one(
    rule: str, bad: str, good: str
) -> None:
    assert rule in rules(bad)
    assert rule not in rules(good)


def test_findings_carry_position_message_and_rule_description() -> None:
    source = "import pandas\n\nx = s.shift(-1)\n"
    [finding] = lint_source(source, filename="pipeline.py")
    assert (finding.rule, finding.line, finding.filename) == ("LA001", 3, "pipeline.py")
    assert finding.column == 4
    assert "future" in finding.message.lower()
    assert RULES[finding.rule]


def test_findings_are_ordered_by_position() -> None:
    source = "a = s.bfill()\nb = s.shift(-1)\nc = s.rolling(3, center=True).mean()\n"
    findings = lint_source(source)
    assert [(f.line, f.rule) for f in findings] == [(1, "LA002"), (2, "LA001"), (3, "LA007")]


def test_findings_at_the_same_place_are_ordered_by_rule_not_by_traversal() -> None:
    # the outer call (bfill, LA002) is visited first, but both start at the same position
    assert rules("x = s.shift(-1).bfill()\n") == ["LA001", "LA002"]


def test_clean_code_has_no_findings() -> None:
    source = (
        "def pipeline(store, series, origin):\n"
        "    history = store.as_of(series, origin)\n"
        "    return sum(o.value for o in history[-4:]) / 4\n"
    )
    assert lint_source(source) == []


def test_a_syntax_error_is_reported_as_a_finding_not_an_exception() -> None:
    [finding] = lint_source("def broken(:\n", filename="bad.py")
    assert finding.rule == "LA000" and finding.line == 1
    assert "parse" in finding.message.lower()


def test_a_negated_shift_is_flagged_but_a_plain_variable_is_not() -> None:
    assert rules("x = s.shift(-n)\ny = s.shift(k)\n") == ["LA001"]  # -n is a unary minus
    assert rules("y = s.shift(k)\n") == []


def test_lint_paths_reads_files_and_folders(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = s.shift(-1)\n", encoding="utf-8")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "b.py").write_text("y = s.bfill()\n", encoding="utf-8")
    (sub / "notes.txt").write_text("s.bfill()\n", encoding="utf-8")
    findings = lint_paths([tmp_path])
    assert sorted((Path(f.filename).name, f.rule) for f in findings) == [
        ("a.py", "LA001"),
        ("b.py", "LA002"),
    ]
    assert lint_paths([tmp_path / "a.py"])[0].rule == "LA001"


def test_a_missing_path_is_an_error() -> None:
    with pytest.raises(FileNotFoundError):
        lint_paths([Path("no/such/place.py")])


def test_the_seeded_leaking_pipeline_is_flagged() -> None:
    source = (
        "def pipeline(store, series, origin):\n"
        "    final = store.as_of(series, datetime(2100, 1, 1, tzinfo=UTC))\n"
        "    return final[-1].value\n"
    )
    assert "LA004" in rules(source)
