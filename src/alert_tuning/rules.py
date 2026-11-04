"""Transaction-monitoring rules, each with documented parameters and a sweep grid.

The rules are deliberately simple and readable: the lab is about *how thresholds are chosen
and documented*, not about a clever detection algorithm.
"""
from __future__ import annotations

import calendar
import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable

from .data import Dataset, Txn


@dataclass(frozen=True)
class Alert:
    customer_id: str
    rule: str
    day: dt.date
    score: float
    txn_ids: tuple = ()
    detail: str = ""
    since: dt.date | None = None   # start of the period the alert covers, when it covers more than one day


@dataclass(frozen=True)
class ParamSpec:
    default: float
    grid: tuple
    stricter: str          # 'up': a larger value fires less often; 'down': a smaller value fires less often
    doc: str = ""


@dataclass
class RuleSpec:
    name: str
    typology: str          # planted typology this rule is meant to catch
    func: Callable
    params: dict = field(default_factory=dict)
    description: str = ""
    suppress_days: int = 7


def _suppress(alerts: list[Alert], days: int) -> list[Alert]:
    """Keep one alert per customer per `days` window (analysts do not want ten alerts for one burst)."""
    kept, last = [], {}
    for a in sorted(alerts, key=lambda a: (a.customer_id, a.day)):
        if a.customer_id in last and (a.day - last[a.customer_id]).days <= days:
            continue
        last[a.customer_id] = a.day
        kept.append(a)
    return kept


# ------------------------------------------------------------------ 1 structuring
def cash_structuring(ds: Dataset, band: float, min_count: int, window_days: int) -> list[Alert]:
    T = ds.threshold_amd
    out = []
    for cid in ds.by_customer:
        rows = [t for t in ds.txns_of(cid, "cash_deposit", "in") if band * T <= t.amount < T]
        j = 0
        for i, t in enumerate(rows):
            while (t.day - rows[j].day).days > window_days:
                j += 1
            n = i - j + 1
            if n >= min_count:
                out.append(Alert(cid, "cash_structuring", t.day, n, tuple(r.txn_id for r in rows[j:i + 1]),
                                 f"{n} cash deposits within {window_days} days, each between {band:.0%} and 100% of the threshold"))
    return out


# --------------------------------------------------------------------- 2 fan-in
def fan_in(ds: Dataset, min_senders: int, window_days: int, cashout_ratio: float) -> list[Alert]:
    out = []
    for cid in ds.by_customer:
        rows = ds.txns_of(cid, "domestic_transfer", "in")
        if len(rows) < min_senders:
            continue
        cash = ds.txns_of(cid, "cash_withdrawal", "out")
        j = 0
        for i, t in enumerate(rows):
            while (t.day - rows[j].day).days > window_days:
                j += 1
            window = rows[j:i + 1]
            senders = {r.cp_id for r in window}
            if len(senders) < min_senders:
                continue
            received = sum(r.amount for r in window)
            if cashout_ratio > 0:
                taken = sum(c.amount for c in cash if 0 <= (c.day - t.day).days <= 3)
                if taken < cashout_ratio * received:
                    continue
            out.append(Alert(cid, "fan_in", t.day, len(senders), tuple(r.txn_id for r in window),
                             f"{len(senders)} distinct senders within {window_days} days, {received:,} AMD received"))
    return out


# -------------------------------------------------------------------- 3 fan-out
def fan_out(ds: Dataset, min_funding_amd: int, min_recipients: int, window_days: int) -> list[Alert]:
    out = []
    for cid in ds.by_customer:
        outs = ds.txns_of(cid, "domestic_transfer", "out")
        if len(outs) < min_recipients:
            continue
        for t in ds.by_customer[cid]:
            if t.direction != "in" or t.product not in ("international_transfer", "cash_deposit", "domestic_transfer") or t.amount < min_funding_amd:
                continue
            window = [o for o in outs if 0 <= (o.day - t.day).days <= window_days]
            recipients = {o.cp_id for o in window}
            if len(recipients) >= min_recipients and sum(o.amount for o in window) >= 0.5 * t.amount:
                out.append(Alert(cid, "fan_out", t.day, len(recipients), (t.txn_id,) + tuple(o.txn_id for o in window),
                                 f"{t.amount:,} AMD in, then {len(recipients)} recipients within {window_days} days"))
    return out


# ---------------------------------------------------------------- 4 pass-through
def pass_through(ds: Dataset, ratio: float, window_hours: int, min_amount: int, min_events: int) -> list[Alert]:
    out = []
    for cid in ds.by_customer:
        ins = [t for t in ds.txns_of(cid, "international_transfer", "in") if t.amount >= min_amount]
        if not ins:
            continue
        outs = [t for t in ds.by_customer[cid] if t.direction == "out" and t.product in ("international_transfer", "domestic_transfer")]
        events = []
        for t in ins:
            sent = sum(o.amount for o in outs if dt.timedelta(0) <= o.ts - t.ts <= dt.timedelta(hours=window_hours))
            if sent >= ratio * t.amount:
                events.append(t)
        for k, t in enumerate(events):
            recent = [e for e in events[:k + 1] if (t.day - e.day).days <= 30]
            if len(recent) >= min_events:
                out.append(Alert(cid, "pass_through", t.day, len(recent), tuple(e.txn_id for e in recent),
                                 f"{len(recent)} inbound transfer(s) forwarded >= {ratio:.0%} within {window_hours} h"))
    return out


# ------------------------------------------------------------------ 5 dormant
def dormant_wakeup(ds: Dataset, dormant_days: int, burst_days: int, min_multiple: float, min_amount: int) -> list[Alert]:
    out = []
    for cid, rows in ds.by_customer.items():
        expected = ds.customers[cid]["expected_monthly_amd"]
        for prev, cur in zip(rows, rows[1:]):
            if (cur.day - prev.day).days < dormant_days:
                continue
            burst = [r for r in rows if 0 <= (r.day - cur.day).days <= burst_days]
            volume = sum(r.amount for r in burst)
            if volume >= max(min_multiple * expected, min_amount):
                out.append(Alert(cid, "dormant_wakeup", cur.day, volume / max(expected, 1), tuple(r.txn_id for r in burst),
                                 f"{(cur.day - prev.day).days} idle days, then {volume:,} AMD in {burst_days} days"))
                break
    return out


# ------------------------------------------------------------ 6 branch hopping
def branch_hopping(ds: Dataset, min_regions: int, window_hours: int) -> list[Alert]:
    out = []
    for cid in ds.by_customer:
        rows = ds.txns_of(cid, "pension_payout", "in")
        j = 0
        for i, t in enumerate(rows):
            while t.ts - rows[j].ts > dt.timedelta(hours=window_hours):
                j += 1
            window = rows[j:i + 1]
            regions = {r.region for r in window}
            if len(regions) >= min_regions:
                out.append(Alert(cid, "branch_hopping", t.day, len(regions), tuple(r.txn_id for r in window),
                                 f"pension paid in {len(regions)} regions within {window_hours} h"))
    return out


# ------------------------------------------------------------------ 7 circular
def circular(ds: Dataset, window_days: int, tolerance: float, min_cycle_len: int) -> list[Alert]:
    edges = defaultdict(list)
    for t in ds.txns:
        if t.product == "domestic_transfer" and t.direction == "out" and t.cp_id in ds.customers:
            edges[t.customer_id].append(t)
    lo, hi = 1.0 - tolerance, 1.0 + tolerance
    found: dict[tuple, Alert] = {}
    for start_node, starts in edges.items():
        for e0 in starts:
            stack = [(e0.cp_id, e0.ts, [e0], {start_node, e0.cp_id})]
            while stack:
                node, t_prev, path, seen = stack.pop()
                if len(path) >= 5:
                    continue
                for e in edges.get(node, ()):
                    if e.ts < t_prev or (e.day - e0.day).days > window_days or not lo <= e.amount / e0.amount <= hi:
                        continue
                    if e.cp_id == start_node:
                        if len(path) + 1 >= min_cycle_len:
                            members = frozenset(p.customer_id for p in path + [e])
                            key = (members, e.day)
                            if key not in found:
                                found[key] = (members, e.day, tuple(p.txn_id for p in path + [e]))
                    elif e.cp_id not in seen:
                        stack.append((e.cp_id, e.ts, path + [e], seen | {e.cp_id}))
    out = []
    for members, day, ids in found.values():
        for cid in members:
            out.append(Alert(cid, "circular", day, len(members), ids, f"funds returned through a ring of {len(members)} accounts"))
    return out


# -------------------------------------------------------- 8 overpayment / refund
def overpayment_refund(ds: Dataset, min_amount: int, refund_ratio: float, window_days: int) -> list[Alert]:
    out = []
    for cid in ds.by_customer:
        refunds = ds.txns_of(cid, "utility_refund", "in")
        if not refunds:
            continue
        for p in ds.txns_of(cid, "utility_payment", "out"):
            if p.amount < min_amount:
                continue
            for r in refunds:
                if 0 <= (r.day - p.day).days <= window_days and r.amount >= refund_ratio * p.amount:
                    out.append(Alert(cid, "overpayment_refund", r.day, r.amount / p.amount, (p.txn_id, r.txn_id),
                                     f"{p.amount:,} AMD paid, {r.amount:,} AMD refunded after {(r.day - p.day).days} days"))
                    break
    return out


# -------------------------------------------------------------- 9 profile deviation
def profile_deviation(ds: Dataset, multiple: float, min_amount: int, consecutive_months: int) -> list[Alert]:
    out = []
    for cid, rows in ds.by_customer.items():
        expected = ds.customers[cid]["expected_monthly_amd"]
        monthly = Counter()
        ids = defaultdict(list)
        for t in rows:
            if t.direction == "in":
                key = (t.day.year, t.day.month)
                monthly[key] += t.amount
                ids[key].append(t.txn_id)
        hot = sorted(k for k, v in monthly.items() if v >= max(multiple * expected, min_amount))
        run = 0
        for k, prev in zip(hot, [None] + hot):
            consecutive = prev is not None and (k[0] * 12 + k[1]) - (prev[0] * 12 + prev[1]) == 1
            run = run + 1 if consecutive else 1
            if run >= consecutive_months:
                day = dt.date(k[0], k[1], calendar.monthrange(k[0], k[1])[1])
                out.append(Alert(cid, "profile_deviation", day, monthly[k] / max(expected, 1), tuple(ids[k][:20]),
                                 f"{monthly[k]:,} AMD inbound = {monthly[k] / max(expected, 1):.1f}x the declared monthly profile",
                                 dt.date(k[0], k[1], 1)))
    return out


P = ParamSpec
RULES: dict[str, RuleSpec] = {
    "cash_structuring": RuleSpec("cash_structuring", "structuring", cash_structuring, {
        "band": P(0.8, (0.6, 0.7, 0.8, 0.85, 0.9), "up", "lower edge of 'just below the threshold', as a share of it"),
        "min_count": P(3, (2, 3, 4, 5), "up", "cash deposits inside the window"),
        "window_days": P(7, (3, 5, 7, 10, 14), "down", "rolling window"),
    }, "Several cash deposits just below the reporting threshold within a few days."),
    "fan_in": RuleSpec("fan_in", "fan_in_mule", fan_in, {
        "min_senders": P(5, (3, 4, 5, 6, 8, 10), "up", "distinct senders in the window"),
        "window_days": P(5, (2, 3, 5, 7), "down", "rolling window"),
        "cashout_ratio": P(0.0, (0.0, 0.5, 0.8), "up", "share of the money withdrawn in cash within 3 days (0 = ignore)"),
    }, "Many different senders pay one account in a short time, optionally followed by cash-out."),
    "fan_out": RuleSpec("fan_out", "fan_out", fan_out, {
        "min_funding_amd": P(500_000, (300_000, 500_000, 1_000_000, 1_500_000, 2_000_000), "up", "size of the funding credit"),
        "min_recipients": P(4, (3, 4, 5, 6, 8), "up", "distinct recipients paid out"),
        "window_days": P(5, (2, 3, 5, 7), "down", "days after the credit"),
    }, "A large credit is quickly split among many recipients."),
    "pass_through": RuleSpec("pass_through", "pass_through", pass_through, {
        "ratio": P(0.8, (0.7, 0.8, 0.9, 0.95), "up", "share of the inbound amount sent on"),
        "window_hours": P(72, (24, 48, 72, 120), "down", "time allowed for sending on"),
        "min_amount": P(100_000, (100_000, 300_000, 500_000, 1_000_000), "up", "smallest inbound transfer considered"),
        "min_events": P(1, (1, 2, 3), "up", "repeated events within 30 days"),
    }, "International money in, almost all of it sent on within hours."),
    "dormant_wakeup": RuleSpec("dormant_wakeup", "dormant_wakeup", dormant_wakeup, {
        "dormant_days": P(60, (30, 60, 90, 120, 150), "up", "idle days before the burst"),
        "burst_days": P(14, (7, 14, 30), "down", "length of the burst considered"),
        "min_multiple": P(3.0, (3.0, 5.0, 10.0, 20.0), "up", "burst volume as a multiple of the profile"),
        "min_amount": P(500_000, (500_000, 1_000_000, 1_500_000), "up", "smallest burst volume"),
    }, "An idle account suddenly moves a large amount."),
    "branch_hopping": RuleSpec("branch_hopping", "branch_hopping", branch_hopping, {
        "min_regions": P(2, (2, 3), "up", "different regions"),
        "window_hours": P(48, (3, 6, 12, 24, 48), "down", "time window (a person cannot be in two regions within hours)"),
    }, "The same pension paid at counters in different regions within hours."),
    "circular": RuleSpec("circular", "circular", circular, {
        "window_days": P(30, (5, 10, 20, 30, 45), "down", "time allowed for the money to come back"),
        "tolerance": P(0.2, (0.05, 0.1, 0.2, 0.3), "down", "allowed difference between the first and later amounts"),
        "min_cycle_len": P(2, (2, 3), "up", "accounts in the ring (2 = A to B and back)"),
    }, "Funds returning to the sender through a closed ring of accounts."),
    "overpayment_refund": RuleSpec("overpayment_refund", "overpayment_refund", overpayment_refund, {
        "min_amount": P(300_000, (200_000, 300_000, 500_000, 800_000), "up", "smallest utility payment considered"),
        "refund_ratio": P(0.1, (0.1, 0.3, 0.5, 0.8, 0.9), "up", "share of the payment refunded"),
        "window_days": P(60, (7, 14, 30, 60), "down", "days from payment to refund"),
    }, "A large utility payment followed by a refund of most of it."),
    "profile_deviation": RuleSpec("profile_deviation", "profile_mismatch", profile_deviation, {
        "multiple": P(2.0, (1.5, 2.0, 3.0, 5.0, 10.0, 20.0), "up", "monthly inbound volume as a multiple of the declared profile"),
        "min_amount": P(300_000, (300_000, 500_000, 1_000_000, 2_000_000), "up", "smallest monthly volume"),
        "consecutive_months": P(1, (1, 2, 3), "up", "months in a row"),
    }, "Inbound volume far above the declared customer profile."),
}


def default_config() -> dict:
    return {name: {p: spec.default for p, spec in rule.params.items()} for name, rule in RULES.items()}


def full_params(rule: str, overrides: dict | None = None) -> dict:
    params = {p: spec.default for p, spec in RULES[rule].params.items()}
    for key, value in (overrides or {}).items():
        if key not in params:
            raise KeyError(f"unknown parameter '{key}' for rule '{rule}'")
        params[key] = value
    return params


def run_rule(ds: Dataset, rule: str, overrides: dict | None = None) -> list[Alert]:
    spec = RULES[rule]
    params = full_params(rule, overrides)
    return _suppress(spec.func(ds, **params), spec.suppress_days)


def run_config(ds: Dataset, config: dict) -> list[Alert]:
    alerts = []
    for rule, overrides in config.items():
        if rule not in RULES:
            raise KeyError(f"unknown rule '{rule}'; available: {', '.join(RULES)}")
        alerts.extend(run_rule(ds, rule, overrides))
    return alerts


def relaxed(rule: str, params: dict, factor: float = 0.7) -> dict:
    """The same rule, loosened, used to find near-misses for below-the-line testing."""
    out = {}
    for name, value in params.items():
        spec = RULES[rule].params[name]
        if name in ("min_regions", "min_cycle_len"):
            out[name] = value      # 1 would make every record an alert
            continue
        scaled = value * factor if spec.stricter == "up" else value / factor
        if isinstance(spec.default, int) and not isinstance(spec.default, bool):
            scaled = max(1, int(round(scaled)))
            if spec.stricter == "up" and scaled == value and value > 1:
                scaled = value - 1
        out[name] = scaled
    return out
