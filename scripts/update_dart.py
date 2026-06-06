#!/usr/bin/env python3
"""Update the DART dashboard data, README table, and static HTML page."""

from __future__ import annotations

import csv
import json
import math
import statistics
import sys
import textwrap
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
README = ROOT / "README.md"
DOCS_INDEX = ROOT / "docs" / "index.html"


@dataclass
class Metric:
    key: str
    label: str
    value: str
    score: float
    direction: str
    source: str
    note: str


def safe(callable_obj, fallback):
    try:
        return callable_obj()
    except Exception as exc:
        print(f"warning: {exc}", file=sys.stderr)
        return fallback


def fetch_text(url: str, timeout: int = 8) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "dart-dashboard/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def parse_csv_rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(text.splitlines()))


def latest_fred_value(series_id: str) -> tuple[str, float]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    rows = parse_csv_rows(fetch_text(url))
    for row in reversed(rows):
        raw = row.get(series_id, "").strip()
        if raw and raw != ".":
            return row["observation_date"], float(raw)
    raise ValueError(f"No FRED value found for {series_id}")


def latest_stooq_close(symbol: str) -> tuple[str, float]:
    url = f"https://stooq.com/q/d/l/?s={urllib.parse.quote(symbol)}&i=d"
    rows = parse_csv_rows(fetch_text(url))
    for row in reversed(rows):
        raw = row.get("Close", "").strip()
        if raw:
            return row["Date"], float(raw)
    raise ValueError(f"No Stooq close found for {symbol}")


def coingecko_btc() -> tuple[str, float]:
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
    data = json.loads(fetch_text(url))
    return datetime.now(timezone.utc).date().isoformat(), float(data["bitcoin"]["usd"])


def gdelt_ai_count() -> tuple[str, int]:
    query = urllib.parse.quote('"artificial intelligence" OR "AI capex" OR "AI chips"')
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc?"
        f"query={query}&mode=timelinevolraw&format=json&timespan=30d"
    )
    data = json.loads(fetch_text(url))
    timeline = data.get("timeline", [])
    total = sum(int(point.get("value", 0)) for point in timeline)
    return datetime.now(timezone.utc).date().isoformat(), total


def manual_metrics() -> dict[str, dict[str, str]]:
    path = DATA_DIR / "manual_metrics.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["metric"]: row for row in csv.DictReader(handle)}


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def score_high_bad(value: float, low: float, high: float) -> float:
    return clamp((value - low) / (high - low) * 100)


def score_high_good(value: float, low: float, high: float) -> float:
    return 100 - score_high_bad(value, low, high)


def fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def metric_status(score: float) -> str:
    if score >= 70:
        return "Stress"
    if score >= 40:
        return "Watch"
    return "Calm"


def build_metrics() -> list[Metric]:
    manual = manual_metrics()

    today = date.today().isoformat()
    dgs10_date, dgs10 = safe(lambda: latest_fred_value("DGS10"), (f"fallback {today}", 4.5))
    real10_date, real10 = safe(lambda: latest_fred_value("DFII10"), (f"fallback {today}", 2.1))
    baa_date, baa = safe(lambda: latest_fred_value("BAA10Y"), (f"fallback {today}", 2.2))
    vix_date, vix = safe(lambda: latest_fred_value("VIXCLS"), (f"fallback {today}", 18.0))
    nasdaq_date, nasdaq = safe(lambda: latest_fred_value("NASDAQCOM"), (f"fallback {today}", 21000.0))
    btc_date, btc = safe(coingecko_btc, (f"fallback {today}", 0.0))
    gdelt_date, ai_mentions = safe(gdelt_ai_count, (f"fallback {today}", 25000))

    capex = float(manual["ai_capex_index"]["value"])
    capex_yoy = float(manual["ai_capex_yoy_pct"]["value"])

    discount_score = statistics.mean(
        [
            score_high_bad(dgs10, 3.5, 5.25),
            score_high_bad(real10, 1.25, 2.75),
        ]
    )
    risk_score = statistics.mean(
        [
            score_high_bad(vix, 13, 32),
            score_high_bad(baa, 1.5, 3.5),
        ]
    )
    capex_score = statistics.mean(
        [
            score_high_good(capex, 35, 85),
            score_high_good(capex_yoy, 0, 55),
        ]
    )
    narrative_score = score_high_bad(math.log10(max(ai_mentions, 1)), 3.3, 5.0)

    return [
        Metric(
            "D",
            "Discount Rate",
            f"10Y {fmt_pct(dgs10)} / Real 10Y {fmt_pct(real10)}",
            discount_score,
            "Higher score = higher valuation pressure",
            f"FRED DGS10 {dgs10_date}; DFII10 {real10_date}",
            "DCF denominator pressure.",
        ),
        Metric(
            "A",
            "AI Capex Cycle",
            f"Index {capex:.0f}/100 / YoY {capex_yoy:.0f}%",
            capex_score,
            "Higher score = weaker capex confirmation",
            f"Manual metrics as of {manual['ai_capex_index']['as_of']}",
            manual["ai_capex_index"]["note"],
        ),
        Metric(
            "R",
            "Risk Premium",
            f"VIX {vix:.2f} / BAA10Y {fmt_pct(baa)}",
            risk_score,
            "Higher score = lower market risk appetite",
            f"FRED VIXCLS {vix_date}; BAA10Y {baa_date}",
            "Market fear plus credit spread stress.",
        ),
        Metric(
            "T",
            "Tech Narrative Half-Life",
            f"30d AI media mentions {ai_mentions:,}",
            narrative_score,
            "Higher score = crowded or aging narrative",
            f"GDELT 2.0 {gdelt_date}",
            "Proxy for narrative saturation, not a truth oracle.",
        ),
        Metric(
            "Market",
            "Risk Asset Tape",
            f"Nasdaq {nasdaq:,.0f} / BTC ${btc:,.0f}",
            statistics.mean([risk_score, discount_score]),
            "Context only",
            f"FRED NASDAQCOM {nasdaq_date}; CoinGecko BTC {btc_date}",
            "Cross-market context for the table.",
        ),
    ]


def quadrant(metrics: list[Metric]) -> tuple[str, str]:
    by_key = {metric.key: metric for metric in metrics}
    high_rate = by_key["D"].score >= 55
    weak_story = statistics.mean([by_key["A"].score, by_key["T"].score]) >= 55

    if high_rate and weak_story:
        return "Quadrant IV: High Rate + Weakening Narrative", "Davis double kill pressure."
    if high_rate and not weak_story:
        return "Quadrant I: High Rate + Strong Narrative", "Bubble carnival conditions."
    if not high_rate and weak_story:
        return "Quadrant III: Low Rate + Weak Narrative", "Valuation rubble conditions."
    return "Quadrant II: Low Rate + Strong Narrative", "Davis double click window."


def markdown_table(metrics: list[Metric]) -> str:
    lines = [
        "| Axis | Metric | Latest | Score | Status | Source |",
        "|---|---|---:|---:|---|---|",
    ]
    for metric in metrics:
        lines.append(
            f"| {metric.key} | {metric.label} | {metric.value} | "
            f"{metric.score:.0f}/100 | {metric_status(metric.score)} | {metric.source} |"
        )
    return "\n".join(lines)


def replace_block(text: str, start: str, end: str, replacement: str) -> str:
    before, marker, rest = text.partition(start)
    if not marker:
        raise ValueError(f"Missing marker {start}")
    _, marker_end, after = rest.partition(end)
    if not marker_end:
        raise ValueError(f"Missing marker {end}")
    return before + start + "\n" + replacement.strip() + "\n" + end + after


def write_readme(metrics: list[Metric], quad: tuple[str, str]) -> None:
    text = README.read_text(encoding="utf-8")
    text = replace_block(text, "<!-- DART_TABLE_START -->", "<!-- DART_TABLE_END -->", markdown_table(metrics))
    quad_text = f"**{quad[0]}**: {quad[1]}\n\nUpdated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    text = replace_block(text, "<!-- DART_QUADRANT_START -->", "<!-- DART_QUADRANT_END -->", quad_text)
    README.write_text(text, encoding="utf-8")


def write_data(metrics: list[Metric], quad: tuple[str, str]) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "quadrant": {"name": quad[0], "interpretation": quad[1]},
        "metrics": [metric.__dict__ for metric in metrics],
    }
    (DATA_DIR / "dart_latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (DATA_DIR / "dart_latest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(metrics[0].__dict__.keys()))
        writer.writeheader()
        writer.writerows(metric.__dict__ for metric in metrics)


def write_html(metrics: list[Metric], quad: tuple[str, str]) -> None:
    rows = "\n".join(
        f"<tr><td>{m.key}</td><td>{m.label}</td><td>{m.value}</td><td>{m.score:.0f}</td><td>{metric_status(m.score)}</td><td>{m.source}</td></tr>"
        for m in metrics
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DART Market Dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #172026;
      --muted: #60707a;
      --line: #d8e0e5;
      --bg: #f7f9fa;
      --accent: #0f766e;
      --warn: #b45309;
    }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    main {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 40px 20px 56px;
    }}
    header {{
      display: grid;
      gap: 10px;
      margin-bottom: 28px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(32px, 5vw, 58px);
      line-height: 1;
      letter-spacing: 0;
    }}
    .quadrant {{
      font-size: 18px;
      color: var(--accent);
      font-weight: 700;
    }}
    .sub {{
      color: var(--muted);
      max-width: 760px;
      line-height: 1.6;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin: 24px 0;
    }}
    .tile {{
      background: white;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      min-height: 132px;
    }}
    .axis {{
      color: var(--muted);
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: .08em;
    }}
    .score {{
      font-size: 34px;
      font-weight: 800;
      margin: 10px 0 4px;
    }}
    .status {{
      color: var(--warn);
      font-weight: 700;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 14px;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .08em;
    }}
    tr:last-child td {{
      border-bottom: 0;
    }}
    @media (max-width: 760px) {{
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      table {{ display: block; overflow-x: auto; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>DART Market Dashboard</h1>
      <div class="quadrant">{quad[0]}</div>
      <div class="sub">{quad[1]} Updated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.</div>
    </header>
    <section class="grid">
      {''.join(f'<div class="tile"><div class="axis">{m.key} · {m.label}</div><div class="score">{m.score:.0f}</div><div class="status">{metric_status(m.score)}</div><p>{m.value}</p></div>' for m in metrics if m.key in {'D', 'A', 'R', 'T'})}
    </section>
    <table>
      <thead><tr><th>Axis</th><th>Metric</th><th>Latest</th><th>Score</th><th>Status</th><th>Source</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </main>
</body>
</html>
"""
    DOCS_INDEX.write_text(textwrap.dedent(html), encoding="utf-8")


def main() -> int:
    try:
        metrics = build_metrics()
    except Exception as exc:
        print(f"update failed: {exc}", file=sys.stderr)
        return 1
    quad = quadrant(metrics)
    write_data(metrics, quad)
    write_readme(metrics, quad)
    write_html(metrics, quad)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
