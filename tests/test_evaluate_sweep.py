import math

import pytest

from alert_tuning import rules, sweep
from alert_tuning.evaluate import classify, evaluate, per_typology_recall


def _scenario(ds, typology, kind="suspicious"):
    return next(s for s in ds.scenarios if s.kind == kind and s.typology == typology)


def test_classification_categories(ds):
    s = _scenario(ds, "structuring")
    cid = sorted(s.customers)[0]
    hit = rules.Alert(cid, "cash_structuring", s.start, 3)
    assert classify(ds, hit).category == "true"
    other_rule = rules.Alert(cid, "fan_in", s.start, 3)
    assert classify(ds, other_rule).category == "incidental"
    look = _scenario(ds, "structuring", "lookalike")
    assert classify(ds, rules.Alert(sorted(look.customers)[0], "cash_structuring", look.start, 3)).category == "lookalike_fp"
    plain = next(c for c in ds.customers if c not in ds.scen_by_customer)
    assert classify(ds, rules.Alert(plain, "cash_structuring", s.start, 3)).category == "ordinary_fp"


def test_metrics_are_internally_consistent(ds):
    alerts = rules.run_rule(ds, "fan_out", {})
    m = evaluate(ds, alerts, {"fan_out"})
    assert m["alerts"] == m["true"] + m["incidental"] + m["lookalike_fp"] + m["ordinary_fp"] == len(alerts)
    assert m["false_alerts"] == m["lookalike_fp"] + m["ordinary_fp"]
    assert 0 <= m["precision"] <= 1 and 0 <= m["recall"] <= 1
    tune, hold = evaluate(ds, alerts, {"fan_out"}, "tune"), evaluate(ds, alerts, {"fan_out"}, "holdout")
    assert tune["alerts"] + hold["alerts"] == m["alerts"]
    assert tune["scenarios"] + hold["scenarios"] == m["scenarios"]


def test_no_alerts_means_zero_recall(ds):
    m = evaluate(ds, [], {"fan_out"})
    assert m["alerts"] == 0 and m["recall"] == 0 and m["precision"] == 0


def test_per_typology_recall_covers_every_typology(ds):
    alerts = rules.run_config(ds, rules.default_config())
    table = per_typology_recall(ds, alerts)
    assert len(table) == 9 and all(0 <= d <= n for d, n in table.values())


def test_sweep_covers_the_whole_grid(ds):
    rows = sweep.sweep_rule(ds, "branch_hopping")
    assert len(rows) == 2 * 5
    assert all({"all", "tune", "holdout"} <= set(r) for r in rows)


def test_recommendation_reaches_the_target_with_fewest_false_alerts(ds):
    rows = sweep.sweep_rule(ds, "overpayment_refund")
    base = rules.full_params("overpayment_refund", {})
    rec = sweep.recommend(rows, base, 0.9)
    assert rec["tune"]["recall"] >= 0.9
    ok = [r for r in rows if r["tune"]["recall"] >= 0.9 and r["tune"]["alerts"] > 0]
    assert rec["tune"]["false_alerts"] == min(r["tune"]["false_alerts"] for r in ok)


def test_recommendation_falls_back_to_best_achievable_recall(ds):
    rows = sweep.sweep_rule(ds, "branch_hopping")
    best = max(r["tune"]["recall"] for r in rows)
    rec = sweep.recommend(rows, rules.full_params("branch_hopping", {}), target_recall=1.01)
    assert rec["tune"]["recall"] == pytest.approx(best)


def test_run_tuning_does_not_make_things_worse_on_tuning_data(ds):
    res = sweep.run_tuning(ds, rules=["fan_in", "profile_deviation"])
    for block in res["rules"].values():
        assert block["recommended"]["tune"]["false_alerts"] <= block["baseline"]["tune"]["false_alerts"]
        assert block["recommended"]["tune"]["recall"] >= min(0.9, block["baseline"]["tune"]["recall"]) - 1e-9 or math.isnan(block["baseline"]["tune"]["recall"])


def test_config_round_trip_and_validation(tmp_path):
    path = tmp_path / "c.json"
    sweep.save_config(rules.default_config(), path)
    assert sweep.load_config(path) == rules.default_config()
    path.write_text('{"fan_in": {"bogus": 1}}', encoding="utf-8")
    with pytest.raises(KeyError):
        sweep.load_config(path)
