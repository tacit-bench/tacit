"""Adapters: turn Tacit items into a model's requests and its answers into prediction rows
{"id", "probs": {option key: p}, "latency_ms", "cost_usd", "served_as"}.

  basal     a running basal-serve (POST /v1/systemone). Start it without its own calibration (--no-calibration) so
            the probabilities are the model's raw ones; Tacit applies the dev-fitted calibration itself.
  openrouter  a decision API with the request shape of OpenRouter's /api/alpha/decisions (Jev-like models).
  file      a JSON or JSONL file of probabilities produced elsewhere.

HTTP uses the standard library only. API keys are read from the environment, never written anywhere.
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .data import option_keys, read_jsonl


def _post(url, body, headers=None, timeout=120, retries=3):
    """POST JSON -> (data, status, latency_ms of the last attempt). Retries transport errors, 429 and 5xx."""
    data, status, lat = None, None, None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data, status = json.loads(r.read()), r.status
        except urllib.error.HTTPError as e:
            data, status = {"error": {"http": e.code, "body": e.read().decode(errors="replace")[:500]}}, e.code
        except Exception as e:  # timeouts, connection errors
            data, status = {"error": {"exception": repr(e)[:300]}}, None
        lat = (time.perf_counter() - t) * 1000
        if "error" not in data or not (status is None or status == 429 or status >= 500):
            break
        time.sleep(2 * (attempt + 1))
    return data, status, lat


# ------------------------------------------------------------------ basal-serve
def basal_request(item):
    """One /v1/systemone request with one question "q", as in the system-1-ai experiments: noul criteria keyed
    true / false, choice and score criteria keyed by position ("0", "1", ...) with option_keys hidden."""
    q = {"type": item["type"], "instructions": item["question"]}
    texts = [o["text"] for o in item["options"]]
    if item["type"] == "noul":
        q["criteria"] = {"true": texts[0], "false": texts[1]}
    else:
        q["criteria"] = {str(i): t for i, t in enumerate(texts)}
        q["option_keys"] = "hide"
    return {"state": item["state"], "questions": {"q": q}}


def basal_parse(item, resp):
    ans = ((resp or {}).get("answers") or {}).get("q") or {}
    pr = ans.get("probabilities")
    if not isinstance(pr, dict):
        return None
    keys = option_keys(item)
    if item["type"] == "noul":
        return {"yes": float(pr.get("true", 0.0)), "no": float(pr.get("false", 0.0))}
    return {k: float(pr.get(str(i), 0.0)) for i, k in enumerate(keys)}


class BasalServe:
    name = "basal"

    def __init__(self, url="http://127.0.0.1:8000"):
        self.url = url.rstrip("/") + "/v1/systemone"

    def __call__(self, item):
        data, status, lat = _post(self.url, basal_request(item))
        return dict(id=item["id"], probs=basal_parse(item, data), latency_ms=lat, cost_usd=None,
                    http_status=status, error=(data or {}).get("error"))


# ------------------------------------------------------------------ OpenRouter decisions API
def decisions_request(item, model):
    """noul: criteria {"true", "false"}, answer P(yes); choice: criteria keyed by the option keys; score: criteria as
    the list of level texts (lowest first), probabilities keyed by list position."""
    q = {"type": item["type"], "instructions": item["question"]}
    if item["type"] == "noul":
        q["criteria"] = {"true": item["options"][0]["text"], "false": item["options"][1]["text"]}
    elif item["type"] == "score":
        q["criteria"] = [o["text"] for o in item["options"]]
    else:
        q["criteria"] = {o["key"]: o["text"] for o in item["options"]}
    return {"model": model, "state": item["state"], "questions": {"q": q}}


def decisions_parse(item, resp):
    ans = ((resp or {}).get("answers") or {}).get("q")
    if not ans:
        return None
    keys = option_keys(item)
    if item["type"] == "noul":
        p = ans.get("noul")
        return None if p is None else {"yes": float(p), "no": 1.0 - float(p)}
    pr = ans.get("probabilities") or {}
    if item["type"] == "score":
        v = {k: float(pr.get(str(i), 0.0)) for i, k in enumerate(keys)}
    else:
        v = {k: float(pr.get(k, 0.0)) for k in keys}
    if sum(v.values()) <= 0:
        if item["type"] == "choice" and ans.get("choice") in keys:
            return {k: float(k == ans["choice"]) for k in keys}
        return None
    return v


class OpenRouterDecisions:
    name = "openrouter"

    def __init__(self, model, url="https://openrouter.ai/api/alpha/decisions", key_env="OPENROUTER_API_KEY",
                 provider=None):
        """provider: OpenRouter provider preferences sent with every request, e.g. {"data_collection": "deny",
        "zdr": true}; with these OpenRouter returns an error rather than route to a provider that does not comply."""
        self.model, self.url, self.provider = model, url, provider
        self.key = os.environ.get(key_env)
        if not self.key:
            raise SystemExit(f"set {key_env} in the environment")

    def __call__(self, item):
        body = decisions_request(item, self.model)
        if self.provider:
            body["provider"] = self.provider
        data, status, lat = _post(self.url, body, headers={"Authorization": f"Bearer {self.key}", "X-Title": "tacit"})
        usage = (data or {}).get("usage") or {}
        return dict(id=item["id"], probs=decisions_parse(item, data), latency_ms=lat, cost_usd=usage.get("cost"),
                    served_as=(data or {}).get("model"), provider=(data or {}).get("provider"),
                    http_status=status, error=(data or {}).get("error"))


# ------------------------------------------------------------------ probabilities file
def from_file(path, items):
    """A JSON object {id: {key: p}} or JSONL rows {"id", "probs", "latency_ms"?, "cost_usd"?} -> prediction rows
    for the given items (unknown ids are an error; items without a prediction are left out)."""
    p = Path(path)
    text = p.read_text(encoding="utf-8").lstrip()
    if text.startswith("{") and not p.suffix == ".jsonl":
        rows = [dict(id=k, probs=v) for k, v in json.loads(text).items()]
    else:
        rows = read_jsonl(p)
    known = {it["id"]: it for it in items}
    out = []
    for r in rows:
        if r["id"] not in known:
            raise ValueError(f"{path}: prediction for unknown id {r['id']}")
        bad = set(r["probs"]) - set(option_keys(known[r["id"]]))
        if bad:
            raise ValueError(f"{path}: {r['id']}: unknown option keys {sorted(bad)}")
        out.append(dict(id=r["id"], probs=r["probs"], latency_ms=r.get("latency_ms"), cost_usd=r.get("cost_usd")))
    return out
