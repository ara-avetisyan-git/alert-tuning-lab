"""Below-the-line (BTL) testing: sample what the rules did NOT alert on and look for misses."""
from __future__ import annotations

import csv
import datetime as dt
import math
import random
from collections import defaultdict
from pathlib import Path

from .data import Dataset
from .rules import RULES, Alert, relaxed, run_config, run_rule

Month = tuple


def _months_between(start: dt.date, end: dt.date) -> list[Month]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def alerted_months(alerts: list[Alert]) -> set:
    return {(a.customer_id, m) for a in alerts for m in _months_between(a.since or a.day, a.day)}


def activity_months(ds: Dataset) -> set:
    return {(t.customer_id, (t.day.year, t.day.month)) for t in ds.txns}


def truth_positive(ds: Dataset, customer_id: str, month: Month) -> bool:
    first = dt.date(month[0], month[1], 1)
    last = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    return any(s.kind == "suspicious" and s.start <= last and s.end >= first for s in ds.scen_by_customer.get(customer_id, ()))


def wilson(positives: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = positives / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def build_population(ds: Dataset, config: dict, factor: float = 0.7) -> dict:
    """Not-alerted customer-months split into 'near_miss' (a loosened rule would have alerted) and 'other'."""
    real = alerted_months(run_config(ds, config))
    near: dict = defaultdict(set)
    for rule, params in config.items():
        for a in run_rule(ds, rule, relaxed(rule, params if params else {}, factor) if params else {}):
            for m in _months_between(a.since or a.day, a.day):
                key = (a.customer_id, m)
                if key not in real:
                    near[key].add(rule)
    population = activity_months(ds) - real
    near_keys = set(near) & population
    return {"alerted": real, "near": {k: sorted(near[k]) for k in near_keys}, "other": population - near_keys}


def month_context(ds: Dataset, customer_id: str, month: Month) -> dict:
    rows = [t for t in ds.by_customer.get(customer_id, []) if (t.day.year, t.day.month) == month]
    return {"month_inbound_amd": sum(t.amount for t in rows if t.direction == "in"),
            "month_outbound_amd": sum(t.amount for t in rows if t.direction == "out"), "txn_count": len(rows)}


def draw_sample(ds: Dataset, config: dict, n: int = 200, near_share: float = 0.5, seed: int = 1, factor: float = 0.7) -> dict:
    rng = random.Random(seed)
    pop = build_population(ds, config, factor)
    near_keys, other_keys = sorted(pop["near"]), sorted(pop["other"])
    n_near = min(len(near_keys), round(n * near_share))
    n_other = min(len(other_keys), n - n_near)
    picked = [(k, "near_miss") for k in rng.sample(near_keys, n_near)] + [(k, "other") for k in rng.sample(other_keys, n_other)]
    rng.shuffle(picked)
    rows = []
    for i, ((cid, month), stratum) in enumerate(picked, 1):
        c = ds.customers[cid]
        rows.append({"sample_id": f"B{i:04d}", "customer_id": cid, "customer_name": c["name"], "segment": c["segment"],
                     "month": f"{month[0]}-{month[1]:02d}", "stratum": stratum,
                     "near_miss_rules": ";".join(pop["near"].get((cid, month), [])),
                     "expected_monthly_amd": c["expected_monthly_amd"], **month_context(ds, cid, month),
                     "analyst_decision": "", "analyst_note": ""})
    return {"rows": rows, "population": {"near_miss": len(near_keys), "other": len(other_keys), "alerted": len(pop["alerted"])},
            "seed": seed, "near_share": near_share, "factor": factor}


def write_sample(sample: dict, path: str | Path) -> None:
    rows = sample["rows"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_sample(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def evaluate_sample(ds: Dataset, rows: list[dict], population: dict | None = None) -> dict:
    """Miss rate per stratum with 95% Wilson intervals; uses analyst decisions when present, else ground truth."""
    strata: dict = {}
    for stratum in ("near_miss", "other"):
        sel = [r for r in rows if r["stratum"] == stratum]
        pos = 0
        for r in sel:
            y, m = r["month"].split("-")
            decision = (r.get("analyst_decision") or "").strip().lower()
            if decision in ("suspicious", "not_suspicious"):
                pos += decision == "suspicious"
            else:
                pos += truth_positive(ds, r["customer_id"], (int(y), int(m)))
        lo, hi = wilson(pos, len(sel))
        strata[stratum] = {"n": len(sel), "positives": pos, "rate": pos / len(sel) if sel else 0.0, "ci_low": lo, "ci_high": hi}
    result = {"strata": strata, "used_analyst_labels": any((r.get("analyst_decision") or "").strip() for r in rows)}
    if population:
        est = {}
        for stratum, key in (("near_miss", "near_miss"), ("other", "other")):
            s, size = strata[stratum], population[key]
            est[stratum] = {"population": size, "estimate": s["rate"] * size, "low": s["ci_low"] * size, "high": s["ci_high"] * size}
        result["estimated_missed"] = {"estimate": sum(e["estimate"] for e in est.values()),
                                      "low": sum(e["low"] for e in est.values()), "high": sum(e["high"] for e in est.values()),
                                      "by_stratum": est}
    return result


def actual_missed(ds: Dataset, config: dict, factor: float = 0.7) -> dict:
    """Only possible with ground truth: how many suspicious customer-months were really not alerted."""
    pop = build_population(ds, config, factor)
    hidden = [k for k in (set(pop["near"]) | pop["other"]) if truth_positive(ds, k[0], k[1])]
    return {"missed": len(hidden), "of_near_miss": sum(1 for k in hidden if k in pop["near"])}
