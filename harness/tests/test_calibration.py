import math
import random

import pytest

from odruch.calibration import (MIN_ACCEPTED, automate, fit, fit_temperature, levels, served, temper, threshold,
                                threshold_of)
from odruch.evaluate import coverage, evaluate


def row(p, gold, t="noul", fam="f"):
    return dict(id=f"x{random.random()}", type=t, family=fam, p=list(p), gold=gold, pred={})


def test_temper_identity_and_sharpening():
    assert temper([0.7, 0.3], 1.0) == [0.7, 0.3]
    sharp = temper([0.7, 0.3], 0.5)
    assert sharp[0] == pytest.approx(0.49 / (0.49 + 0.09))
    assert sum(temper([0.2, 0.3, 0.5], 3.0)) == pytest.approx(1.0)


def test_fit_temperature_recovers_overconfidence():
    # true P(correct) = 0.8, the model reports a confidence sharpened with T = 0.5 (overconfident)
    rng = random.Random(0)
    rows = []
    for _ in range(4000):
        p = temper([0.8, 0.2], 0.5)
        gold = 0 if rng.random() < 0.8 else 1
        rows.append(row(p, gold))
    assert fit_temperature(rows) == pytest.approx(2.0, rel=0.1)


def test_levels_group_ties():
    cs = [(0.9, True), (0.9, False), (0.8, True)]
    assert levels(cs) == [(0.9, 2, 1), (0.8, 3, 1)]


def test_threshold_needs_enough_error_free_decisions():
    cs = [(0.99, True)] * 298
    assert threshold(cs, 0.01) is None
    cs = [(0.99, True)] * 299
    assert threshold(cs, 0.01) == 0.99


def test_threshold_fixed_sequence_stops_at_first_failure():
    # 400 correct at 0.99, then a block of errors at 0.9, then many correct at 0.8: the scan must stop at 0.9
    cs = [(0.99, True)] * 400 + [(0.9, False)] * 20 + [(0.8, True)] * 5000
    assert threshold(cs, 0.01) == 0.99
    assert threshold(cs, 0.05) == 0.9 or threshold(cs, 0.05) == 0.99


def test_threshold_min_accepted():
    cs = [(0.999, True)] * (MIN_ACCEPTED - 1) + [(0.5, False)] * 10
    assert threshold(cs, 0.05) is None


def test_fit_floor_and_types():
    rows = [row([0.995, 0.005], 0) for _ in range(400)]
    rows += [row([0.7, 0.3], 0) for _ in range(140)] + [row([0.7, 0.3], 1) for _ in range(60)]
    rows += [row([0.9, 0.05, 0.05], 0, t="choice") for _ in range(20)]
    cal = fit(rows)
    assert cal["temperature_n"] == {"noul": 600, "choice": 20, "score": 0}
    assert cal["temperature"]["choice"] == 1.0  # fewer than MIN_TEMP_N decisions
    t1 = threshold_of(cal, 0.01, "noul")
    t5 = threshold_of(cal, 0.05, "noul")
    assert t1 is not None and t5 is not None and t5 <= t1
    assert threshold_of(cal, 0.01, "choice") is None
    assert "reason" in cal["thresholds"]["0.01"]["choice"]
    a, e, n = automate(rows, cal, 0.01)
    assert (a, e, n) == (400, 0, 620)


def test_coverage_and_holds_on_test():
    cal = {"temperature": {}, "thresholds": {"0.01": {"noul": {"confidence": 0.99}},
                                             "0.05": {"noul": {"confidence": 0.9}}}}
    test = [row([0.995, 0.005], 0) for _ in range(98)] + [row([0.995, 0.005], 1) for _ in range(2)]
    test += [row([0.95, 0.05], 0) for _ in range(100)]
    c = coverage(test, cal)
    assert c["0.01"]["automated"] == 100 and c["0.01"]["errors"] == 2
    assert c["0.01"]["holds"] is False
    assert c["0.05"]["automated"] == 200 and c["0.05"]["error"] == pytest.approx(0.01)
    assert c["0.05"]["holds"] is True and c["0.05"]["certified_types"] == ["noul"]


def test_coverage_nothing_certified():
    cal = {"temperature": {}, "thresholds": {"0.01": {"noul": {"confidence": None}}, "0.05": {}}}
    c = coverage([row([0.9, 0.1], 0)], cal)
    assert c["0.01"]["automated"] == 0 and c["0.01"]["holds"] is None and c["0.01"]["certified_types"] == []


def test_evaluate_shape():
    rng = random.Random(1)
    dev = [row([0.97, 0.03], 0 if rng.random() < 0.97 else 1) for _ in range(500)]
    test = [row([0.97, 0.03], 0 if rng.random() < 0.97 else 1) for _ in range(300)]
    cal = fit(dev)
    res = evaluate(test, cal, vendor_cal=None, n_test_items=310, missing=["a"])
    assert res["n"] == 300 and res["n_test_items"] == 310 and res["missing"] == ["a"]
    assert set(res["coverage"]["odruch"]) == {"0.01", "0.05"}
    assert 0 <= res["raw"]["ece"] <= 1 and 0 <= res["dev_calibrated"]["brier"] <= 2


def test_served_ties_pick_first_option():
    assert served([row([0.5, 0.5], 0)], {}) == [(0.5, True)]
    assert served([row([0.5, 0.5], 1)], {}) == [(0.5, False)]
    assert math.isclose(served([row([0.25, 0.75], 1)], {})[0][0], 0.75)
