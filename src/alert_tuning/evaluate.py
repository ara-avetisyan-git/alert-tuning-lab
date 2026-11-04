"""Scoring alerts against ground truth."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .data import Dataset, split_of
from .rules import RULES, Alert

MARGIN = dt.timedelta(days=7)


@dataclass(frozen=True)
class Classified:
    alert: Alert
    category: str          # true | incidental | lookalike_fp | ordinary_fp
    scenario_id: str
    split: str


def classify(ds: Dataset, alert: Alert) -> Classified:
    target = RULES[alert.rule].typology
    lo = (alert.since or alert.day) - MARGIN
    hi = alert.day + MARGIN
    best = None
    for s in ds.scen_by_customer.get(alert.customer_id, ()):
        if s.end + dt.timedelta(0) < lo or s.start > hi:
            continue
        if s.kind == "suspicious" and s.typology == target:
            rank, cat = 0, "true"
        elif s.kind == "suspicious":
            rank, cat = 1, "incidental"
        else:
            rank, cat = 2, "lookalike_fp"
        if best is None or rank < best[0]:
            best = (rank, cat, s)
    if best is None:
        return Classified(alert, "ordinary_fp", "", split_of(alert.customer_id))
    return Classified(alert, best[1], best[2].scenario_id, best[2].split)


def evaluate(ds: Dataset, alerts: list[Alert], typologies: set[str] | None = None, subset: str = "all",
             minutes_per_alert: float = 20.0) -> dict:
    """Metrics for a set of alerts.

    ``typologies`` selects which planted scenarios count towards recall (a single rule's target,
    or every typology for a whole configuration). ``subset`` is 'all', 'tune' or 'holdout'.
    """
    classified = [classify(ds, a) for a in alerts]
    if subset != "all":
        classified = [c for c in classified if c.split == subset]
    counts = {"true": 0, "incidental": 0, "lookalike_fp": 0, "ordinary_fp": 0}
    detected_target, detected_any = set(), set()
    for c in classified:
        counts[c.category] += 1
        if c.category == "true":
            detected_target.add(c.scenario_id)
        if c.category in ("true", "incidental"):
            detected_any.add(c.scenario_id)
    scenarios = [s for s in ds.scenarios if s.kind == "suspicious" and (typologies is None or s.typology in typologies)
                 and (subset == "all" or s.split == subset)]
    ids = {s.scenario_id for s in scenarios}
    n = len(classified)
    false_alerts = counts["lookalike_fp"] + counts["ordinary_fp"]
    months = ds.months
    return {
        "alerts": n,
        "true": counts["true"], "incidental": counts["incidental"],
        "lookalike_fp": counts["lookalike_fp"], "ordinary_fp": counts["ordinary_fp"],
        "false_alerts": false_alerts,
        "precision": (counts["true"] + counts["incidental"]) / n if n else 0.0,
        "scenarios": len(ids),
        "detected": len(ids & detected_target),
        "recall": len(ids & detected_target) / len(ids) if ids else float("nan"),
        "detected_any": len(ids & detected_any),
        "recall_any": len(ids & detected_any) / len(ids) if ids else float("nan"),
        "alerts_per_1000_customers_month": 1000 * n / max(len(ds.customers), 1) / months,
        "analyst_hours": n * minutes_per_alert / 60.0,
    }


def per_typology_recall(ds: Dataset, alerts: list[Alert], subset: str = "all") -> dict:
    classified = [classify(ds, a) for a in alerts]
    if subset != "all":
        classified = [c for c in classified if c.split == subset]
    hit_by_typology: dict[str, set] = {}
    for c in classified:
        if c.category == "true":
            hit_by_typology.setdefault(RULES[c.alert.rule].typology, set()).add(c.scenario_id)
    out = {}
    for typ in sorted({s.typology for s in ds.scenarios if s.kind == "suspicious"}):
        ids = {s.scenario_id for s in ds.scenarios if s.kind == "suspicious" and s.typology == typ and (subset == "all" or s.split == subset)}
        out[typ] = (len(ids & hit_by_typology.get(typ, set())), len(ids))
    return out
