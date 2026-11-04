"""Dependency-free SVG charts."""
from __future__ import annotations

import math
from pathlib import Path
from xml.sax.saxutils import escape

W, H = 640, 400
ML, MR, MT, MB = 70, 20, 40, 60


def _svg(body: str, title: str, width: int = W, height: int = H) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="12">'
            f'<rect width="100%" height="100%" fill="white"/>'
            f'<text x="{width / 2}" y="22" text-anchor="middle" font-size="15" font-weight="bold">{escape(title)}</text>{body}</svg>')


def tradeoff_svg(rule: str, rows: list[dict], baseline: dict, recommended: dict, path: str | Path, subset: str = "all") -> None:
    """Every parameter combination as a point: alerts (x) against recall of the rule's own typology (y)."""
    pts = [(r[subset]["alerts"], r[subset]["recall"]) for r in rows if not math.isnan(r[subset]["recall"])]
    if not pts:
        return
    max_x = max(max(p[0] for p in pts), baseline[subset]["alerts"], 1)
    pw, ph = W - ML - MR, H - MT - MB

    def sx(v): return ML + pw * v / max_x
    def sy(v): return MT + ph * (1 - v)

    body = [f'<line x1="{ML}" y1="{MT + ph}" x2="{ML + pw}" y2="{MT + ph}" stroke="#444"/>',
            f'<line x1="{ML}" y1="{MT}" x2="{ML}" y2="{MT + ph}" stroke="#444"/>']
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        body.append(f'<line x1="{ML}" y1="{sy(v)}" x2="{ML + pw}" y2="{sy(v)}" stroke="#ddd"/>'
                    f'<text x="{ML - 8}" y="{sy(v) + 4}" text-anchor="end">{v:.0%}</text>')
    for i in range(5):
        v = max_x * i / 4
        body.append(f'<text x="{sx(v)}" y="{MT + ph + 18}" text-anchor="middle">{v:.0f}</text>')
    for x, y in pts:
        body.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4" fill="#4c78a8" fill-opacity="0.35"/>')
    b, r = baseline[subset], recommended[subset]
    body.append(f'<circle cx="{sx(b["alerts"]):.1f}" cy="{sy(b["recall"]):.1f}" r="7" fill="#e45756" stroke="black"/>')
    body.append(f'<circle cx="{sx(r["alerts"]):.1f}" cy="{sy(r["recall"]):.1f}" r="7" fill="#54a24b" stroke="black"/>')
    body.append(f'<text x="{ML + pw - 4}" y="{MT + 16}" text-anchor="end" fill="#e45756">● current thresholds</text>'
                f'<text x="{ML + pw - 4}" y="{MT + 34}" text-anchor="end" fill="#54a24b">● recommended</text>'
                f'<text x="{ML + pw - 4}" y="{MT + 52}" text-anchor="end" fill="#4c78a8">● other combinations tried</text>')
    body.append(f'<text x="{ML + pw / 2}" y="{H - 14}" text-anchor="middle">alerts raised ({subset} data)</text>')
    body.append(f'<text transform="translate(18 {MT + ph / 2}) rotate(-90)" text-anchor="middle">scenarios detected</text>')
    Path(path).write_text(_svg("".join(body), f"{rule}: alerts against detection"), encoding="utf-8")


def summary_svg(results: dict, path: str | Path) -> None:
    """Alerts per rule: current thresholds against recommended, split into useful and false."""
    rules = list(results["rules"])
    height = 70 + 34 * len(rules)
    left, width = 150, 640
    scale_max = max(max(b["baseline"]["all"]["alerts"], b["recommended"]["all"]["alerts"]) for b in results["rules"].values()) or 1
    pw = width - left - 60
    body = []
    for i, rule in enumerate(rules):
        y = 50 + 34 * i
        body.append(f'<text x="{left - 8}" y="{y + 18}" text-anchor="end">{escape(rule)}</text>')
        for j, (key, colour) in enumerate((("baseline", "#e45756"), ("recommended", "#54a24b"))):
            m = results["rules"][rule][key]["all"]
            w_total = pw * m["alerts"] / scale_max
            w_false = pw * m["false_alerts"] / scale_max
            body.append(f'<rect x="{left}" y="{y + j * 13}" width="{max(w_total, 1):.1f}" height="11" fill="{colour}" fill-opacity="0.35"/>'
                        f'<rect x="{left}" y="{y + j * 13}" width="{max(w_false, 0):.1f}" height="11" fill="{colour}"/>'
                        f'<text x="{left + w_total + 4:.1f}" y="{y + j * 13 + 10}" font-size="10">{m["alerts"]}</text>')
    body.append(f'<text x="{left}" y="{height - 12}" font-size="11">red = current thresholds, green = recommended; '
                f'dark part = false alerts, light part = all alerts</text>')
    Path(path).write_text(_svg("".join(body), "Alerts per rule: current against recommended", width, height), encoding="utf-8")
