"""Parameter sweeps, recommendations and configuration-level comparison."""
from __future__ import annotations

import csv
import itertools
import json
import math
from pathlib import Path

from .data import Dataset
from .evaluate import evaluate, per_typology_recall
from .rules import RULES, default_config, full_params, run_config, run_rule

SUBSETS = ("all", "tune", "holdout")


def _metrics(ds: Dataset, alerts, typology: str, minutes: float) -> dict:
    return {s: evaluate(ds, alerts, {typology}, s, minutes) for s in SUBSETS}


def sweep_rule(ds: Dataset, rule: str, minutes: float = 20.0) -> list[dict]:
    spec = RULES[rule]
    names = list(spec.params)
    rows = []
    for combo in itertools.product(*(spec.params[n].grid for n in names)):
        params = dict(zip(names, combo))
        rows.append({"rule": rule, "params": params, **_metrics(ds, run_rule(ds, rule, params), spec.typology, minutes)})
    return rows


def evaluate_rule(ds: Dataset, rule: str, params: dict, minutes: float = 20.0) -> dict:
    params = full_params(rule, params)
    return {"rule": rule, "params": params, **_metrics(ds, run_rule(ds, rule, params), RULES[rule].typology, minutes)}


def _distance(params: dict, baseline: dict) -> int:
    return sum(1 for k, v in params.items() if v != baseline.get(k))


def recommend(rows: list[dict], baseline_params: dict, target_recall: float = 0.9) -> dict | None:
    """Fewest false alerts on the *tuning* scenarios that still reach the target recall.

    If the target is unreachable for this rule on this data, the best achievable recall is used,
    and ties are broken by higher precision and then by the smallest change from the baseline.
    """
    usable = [r for r in rows if r["tune"]["scenarios"] > 0 and not math.isnan(r["tune"]["recall"])]
    if not usable:
        return None
    need = min(target_recall, max(r["tune"]["recall"] for r in usable)) - 1e-9
    eligible = [r for r in usable if r["tune"]["recall"] >= need and r["tune"]["alerts"] > 0]
    return min(eligible, key=lambda r: (r["tune"]["false_alerts"], -r["tune"]["precision"], _distance(r["params"], baseline_params)))


def run_tuning(ds: Dataset, baseline_config: dict | None = None, target_recall: float = 0.9, minutes: float = 20.0,
               rules: list[str] | None = None) -> dict:
    baseline_config = baseline_config or default_config()
    out = {"rules": {}, "target_recall": target_recall, "minutes_per_alert": minutes}
    recommended_config = {}
    for rule in rules or list(RULES):
        base_params = full_params(rule, baseline_config.get(rule))
        rows = sweep_rule(ds, rule, minutes)
        baseline = evaluate_rule(ds, rule, base_params, minutes)
        rec = recommend(rows, base_params, target_recall) or baseline
        recommended_config[rule] = rec["params"]
        out["rules"][rule] = {"baseline": baseline, "recommended": rec, "rows": rows}
    out["baseline_config"] = {r: full_params(r, baseline_config.get(r)) for r in out["rules"]}
    out["recommended_config"] = recommended_config
    out["portfolio"] = {
        "baseline": portfolio_metrics(ds, out["baseline_config"], minutes),
        "recommended": portfolio_metrics(ds, recommended_config, minutes),
    }
    return out


def portfolio_metrics(ds: Dataset, config: dict, minutes: float = 20.0) -> dict:
    alerts = run_config(ds, config)
    result = {s: evaluate(ds, alerts, None, s, minutes) for s in SUBSETS}
    result["per_typology"] = {s: per_typology_recall(ds, alerts, s) for s in SUBSETS}
    return result


def write_sweeps_csv(results: dict, path: str | Path) -> None:
    rows = []
    for rule, block in results["rules"].items():
        for r in block["rows"]:
            row = {"rule": rule, **{f"param_{k}": v for k, v in r["params"].items()}}
            for subset in SUBSETS:
                m = r[subset]
                row.update({f"{subset}_alerts": m["alerts"], f"{subset}_false_alerts": m["false_alerts"],
                            f"{subset}_recall": "" if math.isnan(m["recall"]) else round(m["recall"], 4),
                            f"{subset}_precision": round(m["precision"], 4)})
            rows.append(row)
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def save_config(config: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def load_config(path: str | Path) -> dict:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    for rule, params in config.items():
        if rule not in RULES:
            raise KeyError(f"unknown rule '{rule}' in {path}")
        full_params(rule, params)   # validates parameter names
    return config
