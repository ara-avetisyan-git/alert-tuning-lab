import json
import xml.etree.ElementTree as ET

import pytest

from alert_tuning import btl, rules
from alert_tuning.cli import main


def test_wilson_interval_properties():
    lo, hi = btl.wilson(0, 100)
    assert lo == 0.0 and 0 < hi < 0.05
    lo, hi = btl.wilson(50, 100)
    assert lo < 0.5 < hi
    assert btl.wilson(0, 0) == (0.0, 1.0)


def test_sample_is_deterministic_and_stratified(ds):
    config = rules.default_config()
    a = btl.draw_sample(ds, config, n=80, seed=3)
    b = btl.draw_sample(ds, config, n=80, seed=3)
    assert a["rows"] == b["rows"]
    assert btl.draw_sample(ds, config, n=80, seed=4)["rows"] != a["rows"]
    assert {r["stratum"] for r in a["rows"]} <= {"near_miss", "other"}
    assert len(a["rows"]) == 80
    alerted = btl.alerted_months(rules.run_config(ds, config))
    assert all((r["customer_id"], tuple(map(int, r["month"].split("-")))) not in alerted for r in a["rows"])


def test_evaluation_uses_analyst_labels_when_present(ds):
    config = rules.default_config()
    sample = btl.draw_sample(ds, config, n=40, seed=1)
    truth = btl.evaluate_sample(ds, sample["rows"])
    assert not truth["used_analyst_labels"]
    for r in sample["rows"]:
        r["analyst_decision"] = "not_suspicious"
    labelled = btl.evaluate_sample(ds, sample["rows"])
    assert labelled["used_analyst_labels"]
    assert all(s["positives"] == 0 for s in labelled["strata"].values())


def test_estimate_brackets_the_known_truth_on_the_example(ds):
    config = rules.default_config()
    pop = btl.build_population(ds, config)
    population = {"near_miss": len(pop["near"]), "other": len(pop["other"])}
    est = btl.evaluate_sample(ds, btl.draw_sample(ds, config, n=300, seed=2)["rows"], population)["estimated_missed"]
    actual = btl.actual_missed(ds, config)["missed"]
    assert est["low"] <= actual <= est["high"]


def test_cli_rules_evaluate_compare(example_dir, capsys, tmp_path):
    assert main(["rules"]) == 0
    assert "cash_structuring" in capsys.readouterr().out
    assert main(["evaluate", "--data", str(example_dir)]) == 0
    assert "ALL RULES" in capsys.readouterr().out
    cfg_a, cfg_b = tmp_path / "a.json", tmp_path / "b.json"
    cfg_a.write_text(json.dumps(rules.default_config()))
    cfg_b.write_text(json.dumps({"fan_in": {"min_senders": 8}}))
    assert main(["compare", "--data", str(example_dir), "--a", str(cfg_a), "--b", str(cfg_b)]) == 0


def test_cli_reports_errors_cleanly(example_dir, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"nope": {}}')
    assert main(["evaluate", "--data", str(example_dir), "--config", str(bad)]) == 2
    assert "error" in capsys.readouterr().err
    assert main(["evaluate", "--data", str(tmp_path / "missing")]) == 2


def test_btl_cli_round_trip(example_dir, tmp_path, capsys):
    out = tmp_path / "sample.csv"
    assert main(["btl-sample", "--data", str(example_dir), "--n", "60", "--out", str(out)]) == 0
    assert main(["btl-evaluate", "--data", str(example_dir), "--sample", str(out)]) == 0
    assert "estimated suspicious customer-months" in capsys.readouterr().out


def test_tune_pipeline_writes_a_complete_report(example_dir, tmp_path):
    out = tmp_path / "report"
    assert main(["tune", "--data", str(example_dir), "--out", str(out), "--btl-n", "100"]) == 0
    for name in ("tuning_memo.md", "recommended_config.json", "baseline_config.json", "sweeps.csv", "btl_sample.csv"):
        assert (out / name).exists()
    memo = (out / "tuning_memo.md").read_text(encoding="utf-8")
    for heading in ("## 3. Portfolio result", "## 5. Parameter change log", "## 6. Below-the-line", "## 7. Limitations", "## 8. Approval"):
        assert heading in memo
    assert "Synthetic data" in memo
    for svg in (out / "charts").glob("*.svg"):
        ET.parse(svg)                      # every chart is well-formed XML
    assert len(list((out / "charts").glob("*.svg"))) == 10
    assert main(["evaluate", "--data", str(example_dir), "--config", str(out / "recommended_config.json")]) == 0
