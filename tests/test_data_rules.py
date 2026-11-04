import datetime as dt

import pytest

from alert_tuning import rules
from alert_tuning.data import split_of


def test_dataset_loads(ds):
    assert len(ds.customers) == 1000 and len(ds.txns) > 50_000
    assert ds.threshold_amd == 2_000_000
    assert sum(1 for s in ds.scenarios if s.kind == "suspicious") == 54
    assert all(ds.txns[i].ts <= ds.txns[i + 1].ts for i in range(0, len(ds.txns) - 1, 997))


def test_split_is_stable_and_roughly_60_40():
    assert split_of("S0001") == split_of("S0001")
    share = sum(split_of(f"S{i:04d}") == "tune" for i in range(2000)) / 2000
    assert 0.55 < share < 0.65


@pytest.mark.parametrize("rule", list(rules.RULES))
def test_every_rule_runs_at_defaults(ds, rule):
    alerts = rules.run_rule(ds, rule, {})
    assert alerts and all(a.rule == rule for a in alerts)
    assert all(ds.start <= a.day <= ds.end for a in alerts)


def test_structuring_alerts_only_use_deposits_in_the_band(ds):
    T = ds.threshold_amd
    by_id = {t.txn_id: t for t in ds.txns}
    for a in rules.run_rule(ds, "cash_structuring", {"band": 0.85, "min_count": 3}):
        assert all(0.85 * T <= by_id[i].amount < T and by_id[i].product == "cash_deposit" for i in a.txn_ids)
        assert len(a.txn_ids) >= 3


def test_stricter_parameters_never_raise_more_alerts(ds):
    loose = len(rules.run_rule(ds, "fan_in", {"min_senders": 3}))
    strict = len(rules.run_rule(ds, "fan_in", {"min_senders": 8}))
    assert strict <= loose
    assert len(rules.run_rule(ds, "profile_deviation", {"multiple": 10.0})) <= len(rules.run_rule(ds, "profile_deviation", {"multiple": 2.0}))


def test_unknown_rule_or_parameter_is_rejected(ds):
    with pytest.raises(KeyError):
        rules.run_config(ds, {"no_such_rule": {}})
    with pytest.raises(KeyError):
        rules.run_rule(ds, "fan_in", {"no_such_param": 1})


def test_alert_suppression_keeps_one_per_window():
    day = dt.date(2025, 3, 3)
    alerts = [rules.Alert("C1", "r", day + dt.timedelta(days=d), 1) for d in (0, 2, 5, 9, 30)]
    kept = rules._suppress(alerts, 7)
    assert [a.day for a in kept] == [day, day + dt.timedelta(days=9), day + dt.timedelta(days=30)]


def test_relaxed_parameters_fire_at_least_as_often(ds):
    base = rules.full_params("cash_structuring", {})
    loose = rules.relaxed("cash_structuring", base)
    assert len(rules.run_rule(ds, "cash_structuring", loose)) >= len(rules.run_rule(ds, "cash_structuring", base))
    assert rules.relaxed("branch_hopping", rules.full_params("branch_hopping", {}))["min_regions"] == 2
