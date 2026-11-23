"""Tests for the moving-block bootstrap."""

from __future__ import annotations

import random
import statistics

import pytest

from vintage_lakehouse.experiment.bootstrap import (
    make_replicates,
    moving_block_indices,
    percentile_interval,
)


def test_a_resample_has_the_same_length_and_stays_in_range() -> None:
    indices = moving_block_indices(25, 4, random.Random(1))
    assert len(indices) == 25
    assert all(0 <= i < 25 for i in indices)


def test_indices_come_in_blocks_of_consecutive_positions() -> None:
    indices = moving_block_indices(30, 5, random.Random(2))
    for start in range(0, 30, 5):
        block = indices[start : start + 5]
        assert block == list(range(block[0], block[0] + 5))


def test_the_last_block_is_truncated_to_fit() -> None:
    indices = moving_block_indices(11, 4, random.Random(3))
    assert len(indices) == 11
    last = indices[8:]
    assert last == list(range(last[0], last[0] + 3))


def test_a_block_as_long_as_the_data_gives_the_original_order() -> None:
    assert moving_block_indices(6, 6, random.Random(0)) == [0, 1, 2, 3, 4, 5]
    assert moving_block_indices(6, 50, random.Random(0)) == [0, 1, 2, 3, 4, 5]


def test_blocks_of_length_one_are_an_ordinary_bootstrap() -> None:
    indices = moving_block_indices(200, 1, random.Random(4))
    assert len(set(indices)) < 200  # with replacement, so some positions repeat


@pytest.mark.parametrize(("n", "length"), [(0, 3), (5, 0)])
def test_invalid_sizes_are_rejected(n: int, length: int) -> None:
    with pytest.raises(ValueError):
        moving_block_indices(n, length, random.Random(0))


def test_replicates_are_reproducible_from_the_seed() -> None:
    first = make_replicates(40, n_boot=20, block_length=4, seed=7)
    again = make_replicates(40, n_boot=20, block_length=4, seed=7)
    other = make_replicates(40, n_boot=20, block_length=4, seed=8)
    assert first == again
    assert first != other
    assert len(first) == 20
    assert all(len(replicate) == 40 for replicate in first)


def test_at_least_one_replicate_is_required() -> None:
    with pytest.raises(ValueError, match="n_boot"):
        make_replicates(10, n_boot=0, block_length=2, seed=0)


def test_percentile_interval_picks_the_expected_quantiles() -> None:
    samples = list(range(1, 102))  # 1 .. 101
    assert percentile_interval(samples, 0.9) == pytest.approx((6.0, 96.0))
    assert percentile_interval(samples, 0.5) == pytest.approx((26.0, 76.0))


def test_percentile_interval_ignores_the_order_of_the_samples() -> None:
    samples = list(range(1, 102))
    shuffled = samples[:]
    random.Random(5).shuffle(shuffled)
    assert percentile_interval(shuffled, 0.9) == percentile_interval(samples, 0.9)


def test_identical_samples_give_a_degenerate_interval() -> None:
    assert percentile_interval([3.0] * 10, 0.95) == (3.0, 3.0)


@pytest.mark.parametrize(("samples", "level"), [([], 0.95), ([1.0], 0.0), ([1.0], 1.0)])
def test_invalid_interval_inputs_are_rejected(samples: list[float], level: float) -> None:
    with pytest.raises(ValueError):
        percentile_interval(samples, level)


def test_blocks_keep_the_dependence_that_single_resampling_destroys() -> None:
    # A random walk is strongly autocorrelated, so the mean of the whole series is much more
    # uncertain than an independent-data bootstrap suggests. Blocks capture that; they should
    # give a clearly wider spread of bootstrap means.
    rng = random.Random(11)
    walk = []
    level = 0.0
    for _ in range(120):
        level += rng.gauss(0.0, 1.0)
        walk.append(level)

    def spread(block_length: int) -> float:
        means = [
            statistics.fmean(walk[i] for i in replicate)
            for replicate in make_replicates(120, n_boot=400, block_length=block_length, seed=3)
        ]
        return statistics.pstdev(means)

    assert spread(12) > 1.5 * spread(1)


def test_blocks_can_start_at_every_position_including_the_last() -> None:
    rng = random.Random(0)
    first_blocks = {tuple(moving_block_indices(6, 3, rng)[:3]) for _ in range(300)}
    assert first_blocks == {(0, 1, 2), (1, 2, 3), (2, 3, 4), (3, 4, 5)}


def test_every_position_can_be_drawn() -> None:
    drawn: set[int] = set()
    rng = random.Random(1)
    for _ in range(100):
        drawn.update(moving_block_indices(20, 4, rng))
    assert drawn == set(range(20))
