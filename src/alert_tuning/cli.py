"""Command-line interface."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from . import btl as btl_mod
from .charts import summary_svg, tradeoff_svg
from .data import load
from .evaluate import evaluate, per_typology_recall
from .memo import build_memo
from .rules import RULES, default_config, full_params, run_config, run_rule
from .sweep import (SUBSETS, load_config, portfolio_metrics, run_tuning, save_config, write_sweeps_csv)


def _pct(x) -> str:
    return "  n/a" if isinstance(x, float) and math.isnan(x) else f"{x:5.0%}"


def _cmd_rules(args) -> int:
    for name, spec in RULES.items():
        print(f"{name}  [{spec.typology}]  {spec.description}")
        for pname, p in spec.params.items():
            print(f"    {pname:20} default {p.default!s:>10}   grid {list(p.grid)}   {p.doc}")
    return 0


def _cmd_evaluate(args) -> int:
    ds = load(args.data)
    config = load_config(args.config) if args.config else default_config()
    print(f"{'rule':20}{'alerts':>8}{'true':>6}{'incid':>7}{'lookFP':>8}{'ordFP':>7}{'detected':>10}")
    total = []
    for rule in config:
        alerts = run_rule(ds, rule, config[rule])
        total += alerts
        m = evaluate(ds, alerts, {RULES[rule].typology}, "all", args.minutes)
        print(f"{rule:20}{m['alerts']:8}{m['true']:6}{m['incidental']:7}{m['lookalike_fp']:8}{m['ordinary_fp']:7}{m['detected']:>6}/{m['scenarios']}")
    m = evaluate(ds, total, None, "all", args.minutes)
    print(f"{'ALL RULES':20}{m['alerts']:8}{m['true']:6}{m['incidental']:7}{m['lookalike_fp']:8}{m['ordinary_fp']:7}"
          f"{m['detected_any']:>6}/{m['scenarios']}   (any rule; {m['analyst_hours']:.0f} analyst hours at {args.minutes:g} min)")
    return 0


def _cmd_compare(args) -> int:
    ds = load(args.data)
    a, b = portfolio_metrics(ds, load_config(args.a), args.minutes), portfolio_metrics(ds, load_config(args.b), args.minutes)
    print(f"{'':28}{'A':>12}{'B':>12}")
    for subset in SUBSETS:
        for key, label in (("alerts", "alerts"), ("false_alerts", "false alerts"), ("detected_any", "scenarios detected")):
            print(f"{subset + ' ' + label:28}{a[subset][key]:12}{b[subset][key]:12}")
        print(f"{subset + ' recall (any rule)':28}{_pct(a[subset]['recall_any']):>12}{_pct(b[subset]['recall_any']):>12}")
    return 0


def _cmd_sweep(args) -> int:
    ds = load(args.data)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    baseline = load_config(args.baseline) if args.baseline else None
    results = run_tuning(ds, baseline, args.target_recall, args.minutes)
    write_sweeps_csv(results, out / "sweeps.csv")
    save_config(results["recommended_config"], out / "recommended_config.json")
    save_config(results["baseline_config"], out / "baseline_config.json")
    print(f"Wrote sweeps.csv and recommended_config.json to {out}")
    return 0


def _cmd_btl_sample(args) -> int:
    ds = load(args.data)
    config = load_config(args.config) if args.config else default_config()
    sample = btl_mod.draw_sample(ds, config, args.n, args.near_share, args.seed)
    btl_mod.write_sample(sample, args.out)
    pop = sample["population"]
    print(f"Wrote {len(sample['rows'])} customer-months to {args.out} "
          f"(population below the line: {pop['near_miss']:,} near-misses, {pop['other']:,} other). "
          f"Fill the analyst_decision column with 'suspicious' or 'not_suspicious'.")
    return 0


def _cmd_btl_evaluate(args) -> int:
    ds = load(args.data)
    config = load_config(args.config) if args.config else default_config()
    rows = btl_mod.read_sample(args.sample)
    pop = btl_mod.build_population(ds, config)
    population = {"near_miss": len(pop["near"]), "other": len(pop["other"])}
    res = btl_mod.evaluate_sample(ds, rows, population)
    src = "analyst decisions" if res["used_analyst_labels"] else "ground truth (no analyst decisions found)"
    print(f"Evaluated against {src}")
    for name, s in res["strata"].items():
        print(f"  {name:10} n={s['n']:4} suspicious={s['positives']:3} rate={s['rate']:.1%} (95% CI {s['ci_low']:.1%} to {s['ci_high']:.1%})")
    m = res["estimated_missed"]
    print(f"  estimated suspicious customer-months below the line: {m['estimate']:.0f} (95% interval {m['low']:.0f} to {m['high']:.0f})")
    return 0


def _cmd_tune(args) -> int:
    ds = load(args.data)
    out = Path(args.out)
    (out / "charts").mkdir(parents=True, exist_ok=True)
    baseline = load_config(args.baseline) if args.baseline else None
    results = run_tuning(ds, baseline, args.target_recall, args.minutes)
    write_sweeps_csv(results, out / "sweeps.csv")
    save_config(results["baseline_config"], out / "baseline_config.json")
    save_config(results["recommended_config"], out / "recommended_config.json")
    for rule, block in results["rules"].items():
        tradeoff_svg(rule, block["rows"], block["baseline"], block["recommended"], out / "charts" / f"{rule}.svg")
    summary_svg(results, out / "charts" / "summary.svg")
    sample = btl_mod.draw_sample(ds, results["recommended_config"], args.btl_n, 0.5, args.seed)
    btl_mod.write_sample(sample, out / "btl_sample.csv")
    pop = btl_mod.build_population(ds, results["recommended_config"])
    population = {"near_miss": len(pop["near"]), "other": len(pop["other"]), "alerted": len(pop["alerted"])}
    btl = {"population": population, "evaluation": btl_mod.evaluate_sample(ds, sample["rows"], population),
           "actual": btl_mod.actual_missed(ds, results["recommended_config"])}
    (out / "tuning_memo.md").write_text(build_memo(ds, results, btl), encoding="utf-8")
    p = results["portfolio"]
    print(f"Alerts {p['baseline']['all']['alerts']:,} -> {p['recommended']['all']['alerts']:,}; "
          f"false alerts {p['baseline']['all']['false_alerts']:,} -> {p['recommended']['all']['false_alerts']:,}; "
          f"scenarios detected {p['baseline']['all']['detected_any']} -> {p['recommended']['all']['detected_any']} of {p['recommended']['all']['scenarios']}")
    print(f"Report written to {out}/ (tuning_memo.md, recommended_config.json, sweeps.csv, btl_sample.csv, charts/)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alert-tuning", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, config=False):
        p.add_argument("--data", required=True, help="dataset directory (customers, branches, transactions, scenarios)")
        p.add_argument("--minutes", type=float, default=20.0, help="analyst minutes per alert (workload assumption)")
        if config:
            p.add_argument("--config", help="rule configuration JSON (default: built-in baseline)")

    p = sub.add_parser("rules", help="list rules, parameters and sweep grids")
    p.set_defaults(func=_cmd_rules)
    p = sub.add_parser("evaluate", help="alerts and detection for a configuration")
    common(p, True)
    p.set_defaults(func=_cmd_evaluate)
    p = sub.add_parser("compare", help="compare two configurations")
    common(p)
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.set_defaults(func=_cmd_compare)
    p = sub.add_parser("sweep", help="sweep all parameter grids and recommend thresholds")
    common(p)
    p.add_argument("--out", default="tuning_output")
    p.add_argument("--baseline")
    p.add_argument("--target-recall", type=float, default=0.9)
    p.set_defaults(func=_cmd_sweep)
    p = sub.add_parser("btl-sample", help="draw a below-the-line sample for analyst review")
    common(p, True)
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--near-share", type=float, default=0.5)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", default="btl_sample.csv")
    p.set_defaults(func=_cmd_btl_sample)
    p = sub.add_parser("btl-evaluate", help="estimate misses from a reviewed sample")
    common(p, True)
    p.add_argument("--sample", required=True)
    p.set_defaults(func=_cmd_btl_evaluate)
    p = sub.add_parser("tune", help="sweep, recommend, BTL-test and write the tuning memo")
    common(p)
    p.add_argument("--out", default="tuning_output")
    p.add_argument("--baseline")
    p.add_argument("--target-recall", type=float, default=0.9)
    p.add_argument("--btl-n", type=int, default=300)
    p.add_argument("--seed", type=int, default=1)
    p.set_defaults(func=_cmd_tune)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
