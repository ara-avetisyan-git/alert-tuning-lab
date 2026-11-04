# alert-tuning-lab

[![CI](https://github.com/ara-avetisyan-git/alert-tuning-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/ara-avetisyan-git/alert-tuning-lab/actions)

A small laboratory for the work that follows every transaction-monitoring launch: **choosing thresholds with evidence, testing what
they miss, and documenting the decision** so that an inspector or a second line can follow it.

Most open repositories show monitoring *rules*. Few show the method for **reducing false alerts without losing true ones** and the
paperwork around it. This lab does, on data with ground truth (from
[`aml-synthetic-data`](https://github.com/ara-avetisyan-git/aml-synthetic-data), a bundled sample is included):

1. **Nine rules** with documented parameters (structuring, fan-in, fan-out, pass-through, dormant wake-up, branch hopping, circular
   transfers, overpayment and refund, profile deviation).
2. **Threshold sweeps**: every parameter combination is run and scored (alerts, false alerts, precision, scenarios detected).
3. **A tuning / holdout split** so thresholds are picked on one part of the scenarios and checked on the other.
4. **Recommendations**: fewest false alerts that still reach a target detection rate, per rule.
5. **Below-the-line (BTL) testing**: a stratified sample of customer-months that did *not* alert, with near-misses oversampled, a
   file for analysts to fill in, and a miss-rate estimate with confidence intervals.
6. **A tuning memo** (Markdown) with the method, the parameter change log, results, limitations and an approval block.

Standard library only; Python 3.10+. The full pipeline runs in about 10 seconds on the bundled sample.

## Quick start

```bash
git clone https://github.com/ara-avetisyan-git/alert-tuning-lab.git
cd alert-tuning-lab
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q

# Everything in one go: sweep, recommend, BTL sample, charts, memo
alert-tuning tune --data examples/data --out tuning_output
```

`tuning_output/` then contains `tuning_memo.md`, `recommended_config.json`, `baseline_config.json`, `sweeps.csv` (every combination
tried), `btl_sample.csv` (for analysts) and `charts/*.svg`.

Step by step:

```bash
alert-tuning rules                                              # rules, parameters, grids
alert-tuning evaluate --data examples/data                      # baseline thresholds
alert-tuning sweep --data examples/data --out tuning_output     # sweeps + recommended config
alert-tuning compare --data examples/data \
    --a examples/baseline_config.json --b tuning_output/recommended_config.json
alert-tuning btl-sample --data examples/data --config tuning_output/recommended_config.json --n 200 --out btl_sample.csv
alert-tuning btl-evaluate --data examples/data --config tuning_output/recommended_config.json --sample btl_sample.csv
```

Your own data: point `--data` at a directory with `customers.csv`, `branches.csv`, `transactions.csv` (or `.csv.gz`) and
`scenarios.csv` in the format described in the
[data dictionary](examples/data/DATA_DICTIONARY.md). For real alerts, build `scenarios.csv` from analyst-confirmed cases
(suspicious) and confirmed false positives (look-alike).

## What it produces on the bundled sample

Sample: 1,000 customers, 63,000 transactions, 9 months, 54 planted suspicious scenarios and 162 benign look-alikes.

| | Starting thresholds | Recommended |
|---|---:|---:|
| Alerts | 976 | 107 |
| False alerts | 724 | 6 |
| Share of alerts that are useful | 26% | 94% |
| Suspicious scenarios detected (any rule) | 54 / 54 | 54 / 54 |
| Analyst hours at 20 min per alert | 325 | 36 |

The starting thresholds are the rules' built-in defaults, deliberately sensitive, a plausible first configuration after launch.

**Out-of-dataset check.** The recommended thresholds were then applied, unchanged, to a *fresh* dataset (different seed, 2,000
customers, 12 months, 72 planted scenarios):

| Fresh dataset | Starting thresholds | Thresholds tuned on the sample |
|---|---:|---:|
| Alerts | 1,818 | 128 |
| False alerts | 1,496 | 9 |
| Suspicious scenarios detected (any rule) | 72 / 72 | 71 / 72 |

False alerts still fall by about 99%, but one scenario is lost (and two individual rules each miss one of their eight scenarios).
That is the honest shape of tuning: gains are real and so is the leakage, which is why the memo reports holdout results,
warns when the holdout has only a few scenarios, and recommends a BTL test.

**BTL on the recommended thresholds** (300 sampled customer-months below the line): the estimate was 130 suspicious customer-months
(95% interval 41 to 430); the ground truth, available only because the data is synthetic, was 193, inside the interval.
The near-miss stratum showed a 9% suspicious rate against 1% elsewhere, which is why near-misses are oversampled.

## How the tuning works

For each rule the lab runs every combination of its parameter grid (10 to 192 combinations per rule), classifies each alert against the
ground truth and scores it:

| Alert class | Meaning |
|---|---|
| true | falls on a planted suspicious scenario of the rule's own typology (same customer, dates within 7 days) |
| incidental | falls on a suspicious scenario of another typology (still useful) |
| look-alike false alert | falls on a benign look-alike (the interesting kind of false alert) |
| ordinary false alert | falls on nothing planted |

Scenarios are split 60 / 40 by a stable hash into **tuning** and **holdout**. The recommendation for a rule is the combination with the
fewest false alerts on the tuning scenarios that still detects at least the target share (default 90%) of that rule's typology; if the
target is out of reach the best achievable recall is used. The holdout figures are reported next to it.

**BTL sampling.** Customer-months with no alert are split into *near-misses* (a version of the rules with loosened thresholds would
have alerted) and the rest. The sample takes half from each. The miss rate per stratum is combined with the stratum sizes to
estimate how many suspicious customer-months sit below the line, with 95% Wilson intervals. If the analyst has filled
`analyst_decision` in the sample file, those decisions are used instead of the ground truth.

See [docs/METHODOLOGY.md](docs/METHODOLOGY.md) for the details and the rule definitions.

## Read this before quoting any number

- **Synthetic data.** The look-alikes were built to resemble each typology and to be separable from it by a threshold. Real false
  alerts are messier, and real gains will be smaller. The numbers show that the *method* works and what its output looks like; they
  are not detection rates for any institution.
- **Few scenarios per typology** (6 in the sample) make recall coarse (steps of 17%) and some holdout parts contain one or two
  scenarios. The memo flags these cases. Use the larger dataset (`aml-synth generate --customers 2000 --months 12`) for steadier figures.
- **Optimisation on the tuning part can overfit**, as the fresh-dataset check shows.
- Criminals react to thresholds. Tuned thresholds must be re-validated periodically.
- The workload (20 minutes per alert) is an assumption; change it with `--minutes`.
- This is a demonstration project, not a monitoring system, not legal advice, and not a substitute for model validation.

## Project structure

```
src/alert_tuning/
  data.py       loading datasets (csv or csv.gz), indexes, tuning / holdout split
  rules.py      nine rules, parameter specs and sweep grids, loosening for near-misses
  evaluate.py   alert classification and metrics
  sweep.py      sweeps, recommendations, configuration comparison
  btl.py        below-the-line sampling and miss-rate estimation
  charts.py     dependency-free SVG charts
  memo.py       the tuning memo
  cli.py        command-line interface
examples/       bundled synthetic sample, baseline configuration
tests/          pytest suite (33 tests)
docs/           methodology
```

## Roadmap

- Rule logic on real alert exports (`scenarios.csv` from confirmed cases)
- Joint optimisation across rules and alert de-duplication between them
- Segment-specific thresholds (pensioners, small businesses)
- Population stability and drift checks between tuning runs

## License

MIT. Author: Ara Avetisyan, AML/CFT compliance - [LinkedIn](https://www.linkedin.com/in/ara-avetisyan-1795802a)
