"""Loading a dataset directory (as written by aml-synthetic-data) into indexed structures."""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple


class Txn(NamedTuple):
    txn_id: str
    ts: dt.datetime
    customer_id: str
    branch_id: str
    region: str
    channel: str
    product: str
    direction: str
    amount: int
    cp_id: str
    transfer_id: str
    is_suspicious: int
    typology: str
    lookalike: str
    scenario_id: str

    @property
    def day(self) -> dt.date:
        return self.ts.date()


@dataclass
class Scenario:
    scenario_id: str
    kind: str
    typology: str
    customers: frozenset
    start: dt.date
    end: dt.date

    @property
    def split(self) -> str:
        return split_of(self.scenario_id)


def split_of(key: str) -> str:
    """Stable 60 / 40 split into 'tune' and 'holdout' by a hash of the key."""
    return "tune" if int(hashlib.md5(key.encode()).hexdigest(), 16) % 10 < 6 else "holdout"


@dataclass
class Dataset:
    directory: Path
    customers: dict
    txns: list
    scenarios: list
    threshold_amd: int
    start: dt.date
    end: dt.date
    by_customer: dict = field(default_factory=dict)
    by_cust_product: dict = field(default_factory=dict)
    scen_by_customer: dict = field(default_factory=lambda: defaultdict(list))

    @property
    def months(self) -> float:
        return ((self.end - self.start).days + 1) / 30.4

    def txns_of(self, customer_id: str, product: str | None = None, direction: str | None = None) -> list:
        rows = self.by_customer.get(customer_id, []) if product is None else self.by_cust_product.get((customer_id, product), [])
        if direction:
            rows = [t for t in rows if t.direction == direction]
        return rows


def _open(directory: Path, name: str):
    plain, packed = directory / name, directory / (name + ".gz")
    if plain.exists():
        return open(plain, newline="", encoding="utf-8")
    if packed.exists():
        return gzip.open(packed, "rt", newline="", encoding="utf-8")
    raise FileNotFoundError(f"{name}(.gz) not found in {directory}")


def load(directory: str | Path) -> Dataset:
    d = Path(directory)
    with _open(d, "customers.csv") as fh:
        customers = {r["customer_id"]: r for r in csv.DictReader(fh)}
    for c in customers.values():
        c["expected_monthly_amd"] = int(c["expected_monthly_amd"])
    with _open(d, "branches.csv") as fh:
        region_of = {r["branch_id"]: r["region"] for r in csv.DictReader(fh)}
    txns = []
    with _open(d, "transactions.csv") as fh:
        for r in csv.DictReader(fh):
            txns.append(Txn(
                r["txn_id"], dt.datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S"), r["customer_id"], r["branch_id"],
                region_of.get(r["branch_id"], ""), r["channel"], r["product"], r["direction"], int(r["amount_amd"]),
                r["counterparty_id"], r["transfer_id"], int(r["is_suspicious"]), r["typology"], r["lookalike"], r["scenario_id"]))
    txns.sort(key=lambda t: (t.ts, t.txn_id))
    scenarios = []
    with _open(d, "scenarios.csv") as fh:
        for r in csv.DictReader(fh):
            scenarios.append(Scenario(r["scenario_id"], r["kind"], r["typology"], frozenset(r["customer_ids"].split(";")),
                                      dt.date.fromisoformat(r["start"]), dt.date.fromisoformat(r["end"])))
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8")) if (d / "manifest.json").exists() else {}
    cfg = manifest.get("config", {})
    start = dt.date.fromisoformat(cfg["start"]) if "start" in cfg else min(t.day for t in txns)
    end = dt.date.fromisoformat(cfg["end"]) if "end" in cfg else max(t.day for t in txns)
    ds = Dataset(d, customers, txns, scenarios, int(cfg.get("threshold_amd", 2_000_000)), start, end)
    by_customer, by_cp = defaultdict(list), defaultdict(list)
    for t in txns:
        by_customer[t.customer_id].append(t)
        by_cp[(t.customer_id, t.product)].append(t)
    ds.by_customer, ds.by_cust_product = dict(by_customer), dict(by_cp)
    for s in scenarios:
        for c in s.customers:
            ds.scen_by_customer[c].append(s)
    return ds
