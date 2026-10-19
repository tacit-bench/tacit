import math

import pytest

from odruch.metrics import accuracy, brier, cp_interval, cp_upper, ece, percentile, wilson


def test_wilson_known_value():
    lo, hi = wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-4)
    assert hi == pytest.approx(0.9433, abs=1e-4)


def test_wilson_edges():
    assert wilson(0, 0) == (None, None)
    lo, hi = wilson(0, 20)
    assert lo == 0.0 and 0 < hi < 0.2
    lo, hi = wilson(20, 20)
    assert hi == 1.0 and 0.8 < lo < 1


def test_cp_upper_zero_errors_rule_of_three():
    # 0 errors in n: upper bound = 1 - alpha^(1/n)
    for n in (50, 299, 1000):
        assert cp_upper(0, n) == pytest.approx(1 - 0.05 ** (1 / n), rel=1e-6)
    # 299 error-free decisions are the minimum for a 1% bound
    assert cp_upper(0, 299) <= 0.01 < cp_upper(0, 298)


def test_cp_upper_matches_beta_quantile():
    # e = n - 1: P(X <= n-1) = 1 - p^n = alpha  =>  p = (1 - alpha)^(1/n)
    assert cp_upper(9, 10) == pytest.approx(0.95 ** (1 / 10), rel=1e-6)
    assert cp_upper(10, 10) == 1.0
    assert cp_upper(0, 0) == 1.0


def test_cp_interval_contains_point_and_is_two_sided():
    lo, hi = cp_interval(6, 879)
    assert lo < 6 / 879 < hi
    # values reported in system-1-ai experiment 10 for 6 errors in 879: [0.25%, 1.48%]
    assert round(100 * lo, 2) == 0.25 and round(100 * hi, 2) == 1.48
    assert cp_interval(0, 10)[0] == 0.0
    assert cp_interval(10, 10)[1] == 1.0
    assert cp_interval(0, 0) == (None, None)


def test_accuracy_dict():
    a = accuracy([True, True, False, True])
    assert a["value"] == 0.75 and a["correct"] == 3 and a["n"] == 4
    assert a["ci95"][0] < 0.75 < a["ci95"][1]


def test_ece_perfect_and_hand_computed():
    assert ece([1.0, 1.0], [True, True]) == 0.0
    # bin (0.5, 0.6]: conf 0.55 x2, acc 0.5 -> |0.55-0.5| * 0.5 ; bin (0.9, 1.0]: conf 0.95 x2, acc 1 -> 0.05 * 0.5
    assert ece([0.55, 0.55, 0.95, 0.95], [True, False, True, True]) == pytest.approx(0.05)
    assert ece([], []) is None


def test_ece_bin_edges_are_right_closed():
    # 0.6 belongs to (0.5, 0.6], 0.61 to (0.6, 0.7]
    assert ece([0.6], [True]) == pytest.approx(0.4)
    assert ece([0.6, 0.61], [True, False]) == pytest.approx(0.5 * 0.4 + 0.5 * 0.61)


def test_brier():
    assert brier([[1.0, 0.0]], [0]) == 0.0
    assert brier([[0.0, 1.0]], [0]) == 2.0
    assert brier([[0.5, 0.5], [0.7, 0.2, 0.1]], [0, 1]) == pytest.approx((0.5 + (0.49 + 0.64 + 0.01)) / 2)


def test_percentile():
    xs = [1, 2, 3, 4, 5]
    assert percentile(xs, 50) == 3
    assert percentile(xs, 95) == pytest.approx(4.8)
    assert percentile([], 50) is None
    assert math.isclose(percentile([10.0], 95), 10.0)
