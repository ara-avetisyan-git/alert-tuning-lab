# Methodology

## 1. Why tuning needs a method
A monitoring rule has thresholds (amount, count, window, ratio). Set too low, analysts drown in false alerts; set too high, the
institution misses what the rule exists for. Regulators and second-line functions expect to see **why** a threshold was chosen,
**what it was tested against**, and **what it might miss**. This lab turns that into a repeatable procedure.

## 2. Ground truth
Each dataset contains `scenarios.csv`: planted *suspicious* scenarios and benign *look-alike* scenarios, each with participants and dates.
With real data the equivalent is a list of analyst-confirmed cases (suspicious) and confirmed false positives (look-alike).

## 3. Rules
| Rule | Fires when | Main parameters |
|---|---|---|
| `cash_structuring` | several cash deposits just below the reporting threshold in a window | `band`, `min_count`, `window_days` |
| `fan_in` | many distinct senders pay one account in a window, optionally followed by cash-out | `min_senders`, `window_days`, `cashout_ratio` |
| `fan_out` | a large credit is followed by payments to many recipients | `min_funding_amd`, `min_recipients`, `window_days` |
| `pass_through` | international money in is sent on within hours; may require repetition | `ratio`, `window_hours`, `min_amount`, `min_events` |
| `dormant_wakeup` | an account idle for N days moves a large amount in a short burst | `dormant_days`, `burst_days`, `min_multiple`, `min_amount` |
| `branch_hopping` | the same pension is paid in several regions within hours | `min_regions`, `window_hours` |
| `circular` | money returns to the sender through a ring of accounts | `window_days`, `tolerance`, `min_cycle_len` |
| `overpayment_refund` | a large utility payment is followed by a large refund | `min_amount`, `refund_ratio`, `window_days` |
| `profile_deviation` | monthly inbound volume far exceeds the declared profile | `multiple`, `min_amount`, `consecutive_months` |

Alerts of one rule for one customer within 7 days are merged into one, as an alert-management system would.
`alert-tuning rules` prints every parameter, default and grid.

## 4. Evaluation
An alert is matched to a scenario of the same customer whose dates overlap the alert's period (7-day margin). Classes: *true*
(suspicious scenario of the rule's own typology), *incidental* (another typology), *look-alike false alert*, *ordinary false alert*.

- **False alerts** = look-alike + ordinary.
- **Alert productivity** (called precision in the code) = (true + incidental) / alerts.
- **Detection (recall)** of a rule = suspicious scenarios of its typology with at least one *true* alert / all such scenarios.
- **Workload** = alerts x minutes per alert (default 20, an assumption).

## 5. Tuning and holdout
Scenarios are assigned to *tuning* (about 60%) or *holdout* (about 40%) by an MD5 hash of the scenario id, so the split is stable across
runs and machines. An alert belongs to the split of the scenario it matches, or, if it matches none, to the split of its customer id.
Thresholds are chosen on the tuning part; the holdout part is reported as an out-of-sample check.

## 6. Recommendation rule
For each rule, among all grid combinations with at least one alert on the tuning data:

1. keep those whose tuning recall is at least `min(target, best achievable)` (target default 90%);
2. choose the one with the fewest tuning false alerts;
3. break ties by higher precision, then by the smallest change from the starting thresholds.

Choosing the *smallest sufficient change* matters for governance: every changed parameter has to be justified in the change log.

## 7. Below-the-line testing
**Population:** customer-months with any activity and no alert under the configuration being tested.
**Near-misses:** customer-months that a loosened copy of the configuration (thresholds moved by a factor of 0.7, window lengths widened)
would have alerted on. They are the most likely place for misses.
**Sample:** half near-misses and half others by default (`--near-share`), drawn with a fixed seed.
**Review:** analysts mark `analyst_decision` as `suspicious` or `not_suspicious`. In the synthetic setting, ground truth stands in.
**Estimate:** per-stratum suspicious rate with a 95% Wilson interval, multiplied by the stratum size, summed. A high rate in the
near-miss stratum is the signal to loosen the rule.

Because the lab knows the truth, it also reports how many suspicious customer-months were really missed, to show whether the estimate's
interval covered it. With real data that figure does not exist, which is the reason for BTL testing.

## 8. The memo
`tuning_memo.md` contains: purpose and scope, method, portfolio result, rule-by-rule table, detection by typology on the holdout,
parameter change log, BTL result, limitations, automatic warnings (holdout detection lost, very small holdout) and an approval block.
It is a template to adapt to your institution's model-governance standard, not a compliance guarantee.

## 9. What would change with real data
- Labels come from case management, not from a generator, and are incomplete: *unlabelled does not mean innocent*.
- Look-alikes are not tagged; false positives are only those analysts closed as such.
- Population drift (new products, seasonality) needs regular re-tuning and stability checks.
