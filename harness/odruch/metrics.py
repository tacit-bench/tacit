"""Metrics with exact definitions (see METHODOLOGY.md). Standard library only."""
import math
from typing import Iterable, Sequence


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple:
    """Two-sided Wilson score interval (95% by default) for k successes in n trials."""
    if n == 0:
        return (None, None)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def _binom_cdf(e: int, n: int, p: float) -> float:
    """P(X <= e) for X ~ Binomial(n, p), summed in log space."""
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if e >= n else 0.0
    lp, lq = math.log(p), math.log1p(-p)
    lf_n = math.lgamma(n + 1)
    terms = [lf_n - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq for i in range(e + 1)]
    m = max(terms)
    return min(1.0, math.exp(m) * sum(math.exp(t - m) for t in terms))


def cp_upper(e: int, n: int, alpha: float = 0.05) -> float:
    """One-sided Clopper-Pearson upper bound of an error rate: the p with P(X <= e | n, p) = alpha."""
    if n == 0:
        return 1.0
    if e >= n:
        return 1.0
    lo, hi = e / n, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if _binom_cdf(e, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def cp_interval(e: int, n: int, level: float = 0.95) -> tuple:
    """Two-sided Clopper-Pearson interval of e errors in n (each tail (1 - level) / 2)."""
    if n == 0:
        return (None, None)
    a = (1 - level) / 2
    lo = 0.0 if e == 0 else 1 - cp_upper(n - e, n, a)
    hi = 1.0 if e == n else cp_upper(e, n, a)
    return (lo, hi)


def accuracy(correct: Sequence[bool]) -> dict:
    n, k = len(correct), sum(bool(c) for c in correct)
    lo, hi = wilson(k, n)
    return {"value": k / n if n else None, "correct": k, "n": n, "ci95": [lo, hi]}


def ece(confidences: Sequence[float], correct: Sequence[bool], bins: int = 10) -> float:
    """Expected calibration error of the top option: equal-width bins (lo, hi] over (0, 1], weighted by bin share."""
    n = len(confidences)
    if n == 0:
        return None
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(confidences) if (lo < c <= hi) or (b == 0 and c == 0.0)]
        if not idx:
            continue
        mc = sum(confidences[i] for i in idx) / len(idx)
        ma = sum(1.0 if correct[i] else 0.0 for i in idx) / len(idx)
        total += len(idx) / n * abs(mc - ma)
    return total


def brier(prob_vectors: Iterable[Sequence[float]], gold_index: Iterable[int]) -> float:
    """Multi-class Brier score: mean over decisions of sum_k (p_k - y_k)^2, y one-hot on the gold option.
    Range 0 (perfect) to 2."""
    tot, n = 0.0, 0
    for p, g in zip(prob_vectors, gold_index):
        tot += sum((pk - (1.0 if k == g else 0.0)) ** 2 for k, pk in enumerate(p))
        n += 1
    return tot / n if n else None


def percentile(xs: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (q in [0, 100]), as numpy's default."""
    if not xs:
        return None
    s = sorted(xs)
    pos = (len(s) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)
