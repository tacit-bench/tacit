"""The Odruch calibration protocol (METHODOLOGY.md, "Certified coverage").

Fitted on the public dev split only, then frozen and applied to the hidden test split:
  1. one temperature per question type, minimising the NLL of the gold option on dev (log grid of 241 values over
     [0.25, 8]); a type with fewer than MIN_TEMP_N dev decisions keeps T = 1;
  2. per type and target error r, the threshold is the lowest confidence level whose accepted dev decisions (at least
     MIN_ACCEPTED, equal confidences accepted together) have a one-sided 95% Clopper-Pearson upper bound <= r and a
     point error <= r, scanned from the most confident level down and stopped at the first failing level after a pass;
  3. a threshold certified at a tight target is also valid at a looser one, so the looser target never keeps a
     stricter threshold (floor);
  4. a type without a threshold is never automated at that target.
This is the method of basal-calibrate v2.2.0 (bound-controlled, fixed sequence), reimplemented without the vendor's
shipped files: no blending with a vendor temperature, the same rules for every model.
"""
import math

from . import TARGETS, TYPES
from .metrics import cp_upper

T_GRID = (0.25, 8.0)
T_STEPS = 241
MIN_TEMP_N = 30
MIN_ACCEPTED = 50
ALPHA = 0.05
PROTOCOL = "odruch-devfit-1"


def temper(p, T):
    """softmax(log(p) / T), p clipped at 1e-12."""
    if T == 1.0:
        return list(p)
    z = [math.log(max(x, 1e-12)) / T for x in p]
    m = max(z)
    e = [math.exp(x - m) for x in z]
    s = sum(e)
    return [x / s for x in e]


def nll(rows, T):
    tot = 0.0
    for r in rows:
        z = [math.log(max(x, 1e-12)) / T for x in r["p"]]
        m = max(z)
        tot += m + math.log(sum(math.exp(x - m) for x in z)) - z[r["gold"]]
    return tot / len(rows)


def fit_temperature(rows):
    lo, hi = math.log(T_GRID[0]), math.log(T_GRID[1])
    grid = [math.exp(lo + (hi - lo) * i / (T_STEPS - 1)) for i in range(T_STEPS)]
    losses = [nll(rows, t) for t in grid]
    return grid[losses.index(min(losses))]


def served(rows, temps):
    """[(confidence of the top option, correct)] with the per-type temperatures applied. Ties: first option."""
    out = []
    for r in rows:
        q = temper(r["p"], temps.get(r["type"], 1.0))
        top = max(range(len(q)), key=q.__getitem__)
        out.append((q[top], top == r["gold"]))
    return out


def levels(cs):
    """[(confidence, accepted, errors)] from the most confident level down, equal confidences together."""
    by = {}
    for c, ok in cs:
        n, e = by.get(c, (0, 0))
        by[c] = (n + 1, e + (not ok))
    out, n, e = [], 0, 0
    for c in sorted(by, reverse=True):
        n, e = n + by[c][0], e + by[c][1]
        out.append((c, n, e))
    return out


def threshold(cs, risk, alpha=ALPHA, min_n=MIN_ACCEPTED):
    thr = None
    for c, n, e in levels(cs):
        if n < min_n:
            continue
        if e / n <= risk and cp_upper(e, n, alpha) <= risk:
            thr = c
        elif thr is not None:
            break
    return thr


def no_threshold_reason(cs, risk, alpha=ALPHA, min_n=MIN_ACCEPTED):
    need = max(min_n, math.ceil(math.log(alpha) / math.log(1 - risk)))
    if len(cs) < need:
        return f"a {risk:g} bound needs at least {need} accepted dev decisions without an error; dev has {len(cs)}"
    best = min(((cp_upper(e, n, alpha), n) for _, n, e in levels(cs) if n >= min_n), default=None)
    if best is None:
        return "no confidence level accepts enough dev decisions"
    return f"no confidence level meets the bound (lowest upper bound {best[0]:.4f} at {best[1] / len(cs):.0%} coverage)"


def fit(dev_rows, targets=TARGETS):
    """Dev rows (with gold) -> calibration dict (JSON-serialisable)."""
    temps, temp_n = {}, {}
    for t in TYPES:
        rs = [r for r in dev_rows if r["type"] == t]
        temp_n[t] = len(rs)
        temps[t] = fit_temperature(rs) if len(rs) >= MIN_TEMP_N else 1.0
    thresholds = {}
    for risk in targets:
        per = {}
        for t in TYPES:
            cs = served([r for r in dev_rows if r["type"] == t], temps)
            if not cs:
                continue
            thr = threshold(cs, risk)
            if thr is None:
                per[t] = dict(confidence=None, reason=no_threshold_reason(cs, risk))
            else:
                acc = [ok for c, ok in cs if c >= thr]
                e = len(acc) - sum(acc)
                per[t] = dict(confidence=thr, dev_accepted=len(acc), dev_errors=e,
                              dev_coverage=len(acc) / len(cs), dev_upper95=cp_upper(e, len(acc)))
        thresholds[str(float(risk))] = per
    floor(thresholds, targets)
    return dict(protocol=PROTOCOL, temperature=temps, temperature_n=temp_n, thresholds=thresholds,
                settings=dict(t_grid=list(T_GRID), t_steps=T_STEPS, min_temp_n=MIN_TEMP_N,
                              min_accepted=MIN_ACCEPTED, alpha=ALPHA))


def floor(thresholds, targets):
    rs = sorted(float(r) for r in targets)
    for lo, hi in zip(rs, rs[1:]):
        a, b = thresholds[str(lo)], thresholds[str(hi)]
        for t, e in a.items():
            eh = b.get(t) or {}
            if e.get("confidence") is not None and (eh.get("confidence") is None or eh["confidence"] > e["confidence"]):
                b[t] = dict(e, floored_from=str(lo))


def threshold_of(cal, risk, q_type):
    return ((cal.get("thresholds") or {}).get(str(float(risk))) or {}).get(q_type, {}).get("confidence")


def automate(rows, cal, risk):
    """-> (automated, errors, n): decisions at or above their type's threshold, with the calibration's temperatures."""
    cs = served(rows, cal.get("temperature") or {})
    acc = []
    for r, (c, ok) in zip(rows, cs):
        thr = threshold_of(cal, risk, r["type"])
        if thr is not None and c >= thr:
            acc.append(ok)
    return len(acc), len(acc) - sum(acc), len(rows)
