#!/usr/bin/env python3
"""Update the DART dashboard data, README table, and static HTML page."""

from __future__ import annotations

import csv
import html
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


def zh_status(score: float) -> str:
    if score >= 70:
        return "高压"
    if score >= 40:
        return "观察"
    return "平稳"


def status_class(score: float) -> str:
    if score >= 70:
        return "stress"
    if score >= 40:
        return "watch"
    return "calm"


def quadrant_meta(quad: tuple[str, str]) -> dict[str, str]:
    name = quad[0]
    if "Quadrant IV" in name:
        return {
            "code": "Q4",
            "title": "高利率 + 叙事转弱",
            "plain": "估值和故事一起受压，科技股最容易出现戴维斯双杀。",
            "stance": "先控仓位，再等利率或叙事至少一个变量改善。",
        }
    if "Quadrant I" in name:
        return {
            "code": "Q1",
            "title": "高利率 + 强叙事",
            "plain": "故事还撑得住，但贴现率在拉扯估值，属于热闹但不便宜的区域。",
            "stance": "可以继续观察强势资产，但要警惕利率再上行引发估值重定价。",
        }
    if "Quadrant III" in name:
        return {
            "code": "Q3",
            "title": "低利率 + 弱叙事",
            "plain": "钱变便宜了，但市场暂时缺少能点燃风险偏好的故事。",
            "stance": "更适合做观察名单，等待新叙事或盈利兑现。",
        }
    return {
        "code": "Q2",
        "title": "低利率 + 强叙事",
        "plain": "资金成本友好，故事也有增量，是风险资产最顺风的区域。",
        "stance": "趋势窗口更好，但仍要看叙事是否开始拥挤。",
    }


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
    by_key = {metric.key: metric for metric in metrics}
    qmeta = quadrant_meta(quad)
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    overall = statistics.mean([by_key["D"].score, by_key["A"].score, by_key["R"].score, by_key["T"].score])
    narrative_pressure = statistics.mean([by_key["A"].score, by_key["T"].score])
    map_x = clamp(narrative_pressure)
    map_y = clamp(by_key["D"].score)
    axis_copy = {
        "D": ("贴现率", "钱还贵不贵", "越高越压估值"),
        "A": ("AI资本开支", "故事有没有真金白银", "越低越强"),
        "R": ("风险溢价", "市场胆子大不大", "越高越谨慎"),
        "T": ("叙事半衰期", "AI故事是否拥挤", "越高越拥挤"),
    }
    cards = []
    for metric in metrics:
        if metric.key not in axis_copy:
            continue
        zh, question, hint = axis_copy[metric.key]
        safe_value = html.escape(metric.value)
        cards.append(
            f"""
            <article class="signal {status_class(metric.score)}">
              <div class="signal-top">
                <span class="letter">{metric.key}</span>
                <span class="pill">{zh_status(metric.score)}</span>
              </div>
              <h3>{zh}</h3>
              <p class="question">{question}</p>
              <div class="bar" aria-label="{metric.key} score"><span style="width: {metric.score:.0f}%"></span></div>
              <div class="score-row"><strong>{metric.score:.0f}</strong><span>/100</span></div>
              <p class="value">{safe_value}</p>
              <p class="hint">{hint}</p>
            </article>
            """
        )

    rows = "\n".join(
        f"<tr><td>{html.escape(m.key)}</td><td>{html.escape(m.label)}</td><td>{html.escape(m.value)}</td><td>{m.score:.0f}</td><td>{zh_status(m.score)}</td><td>{html.escape(m.source)}</td></tr>"
        for m in metrics
    )
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DART 市场仪表盘</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #141a1f;
      --muted: #65727d;
      --line: #dce3e8;
      --bg: #f5f7f8;
      --panel: #ffffff;
      --green: #0f766e;
      --yellow: #b7791f;
      --red: #b42318;
      --blue: #2563eb;
    }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    header {{
      margin-bottom: 22px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: clamp(30px, 5vw, 54px);
      line-height: 1.05;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 0 0 14px;
      font-size: 26px;
      letter-spacing: 0;
    }}
    h3 {{
      margin: 10px 0 4px;
      font-size: 18px;
      letter-spacing: 0;
    }}
    p {{
      margin: 0;
      line-height: 1.55;
    }}
    .eyebrow {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .08em;
    }}
    .sub {{
      color: var(--muted);
      max-width: 840px;
      line-height: 1.6;
    }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(320px, .95fr);
      gap: 18px;
      align-items: stretch;
      margin: 22px 0;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px;
    }}
    .verdict {{
      display: grid;
      gap: 16px;
    }}
    .verdict-badge {{
      display: inline-flex;
      width: fit-content;
      align-items: center;
      gap: 8px;
      border: 1px solid #b7d7d2;
      background: #e9f6f3;
      color: var(--green);
      border-radius: 999px;
      padding: 7px 12px;
      font-weight: 800;
    }}
    .big-score {{
      display: flex;
      align-items: baseline;
      gap: 6px;
      margin-top: 4px;
    }}
    .big-score strong {{
      font-size: 62px;
      line-height: 1;
    }}
    .big-score span {{
      color: var(--muted);
      font-weight: 700;
    }}
    .takeaway {{
      border-left: 4px solid var(--green);
      padding-left: 14px;
      color: #263238;
      font-size: 17px;
    }}
    .map {{
      position: relative;
      min-height: 360px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-template-rows: 1fr 1fr;
      gap: 8px;
    }}
    .quad {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: #fbfcfd;
      min-height: 132px;
    }}
    .quad.active {{
      border-color: var(--blue);
      box-shadow: 0 0 0 3px rgba(37, 99, 235, .14);
      background: #eff6ff;
    }}
    .quad strong {{
      display: block;
      margin-bottom: 6px;
    }}
    .quad span {{
      color: var(--muted);
      font-size: 13px;
    }}
    .dot {{
      position: absolute;
      left: calc({map_x:.0f}% - 9px);
      bottom: calc({map_y:.0f}% - 9px);
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: var(--blue);
      border: 3px solid white;
      box-shadow: 0 6px 18px rgba(37, 99, 235, .35);
    }}
    .axis-label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }}
    .x-label {{
      text-align: center;
      margin-top: 10px;
    }}
    .y-label {{
      writing-mode: vertical-rl;
      position: absolute;
      left: -18px;
      top: 82px;
    }}
    .signals {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin: 24px 0;
    }}
    .signal {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      min-height: 230px;
      display: grid;
      align-content: start;
      gap: 8px;
    }}
    .signal-top {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
    }}
    .letter {{
      width: 34px;
      height: 34px;
      border-radius: 50%;
      display: inline-grid;
      place-items: center;
      background: #eef2f6;
      font-weight: 900;
    }}
    .pill {{
      border-radius: 999px;
      padding: 5px 9px;
      font-size: 12px;
      font-weight: 700;
    }}
    .calm .pill {{ background: #e8f5ef; color: var(--green); }}
    .watch .pill {{ background: #fff4db; color: var(--yellow); }}
    .stress .pill {{ background: #fde8e5; color: var(--red); }}
    .question, .hint {{
      color: var(--muted);
      font-size: 13px;
    }}
    .value {{
      font-weight: 700;
      min-height: 44px;
    }}
    .bar {{
      height: 10px;
      overflow: hidden;
      background: #eef2f6;
      border-radius: 999px;
      margin-top: 4px;
    }}
    .bar span {{
      display: block;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--green), var(--yellow), var(--red));
    }}
    .score-row {{
      display: flex;
      align-items: baseline;
      gap: 4px;
    }}
    .score-row strong {{
      font-size: 32px;
      line-height: 1;
    }}
    .score-row span {{
      color: var(--muted);
      font-weight: 700;
    }}
    .table-wrap {{
      margin-top: 24px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
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
    .mobile-note {{
      display: none;
      color: var(--muted);
      font-size: 13px;
      margin: 8px 0 0;
    }}
    @media (max-width: 760px) {{
      .hero {{ grid-template-columns: 1fr; }}
      .signals {{ grid-template-columns: 1fr; }}
      .map {{ min-height: 320px; }}
      .y-label {{ display: none; }}
      .mobile-note {{ display: block; }}
      table {{ display: block; overflow-x: auto; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div class="eyebrow">DART Market Dashboard</div>
      <h1>现在市场在哪个房间？</h1>
      <p class="sub">把利率、AI资本开支、风险偏好和科技叙事压缩成一张图。更新时间：{updated}。</p>
    </header>

    <section class="hero">
      <div class="panel verdict">
        <div class="verdict-badge">{qmeta['code']} · {qmeta['title']}</div>
        <div>
          <div class="eyebrow">综合压力</div>
          <div class="big-score"><strong>{overall:.0f}</strong><span>/100</span></div>
        </div>
        <p class="takeaway">{qmeta['plain']}</p>
        <p class="sub">{qmeta['stance']}</p>
      </div>

      <div class="panel">
        <h2>DART 四象限</h2>
        <div class="map">
          <div class="quad {'active' if qmeta['code'] == 'Q4' else ''}"><strong>Q4 双杀压力</strong><span>高利率 + 弱叙事</span></div>
          <div class="quad {'active' if qmeta['code'] == 'Q1' else ''}"><strong>Q1 泡沫狂欢</strong><span>高利率 + 强叙事</span></div>
          <div class="quad {'active' if qmeta['code'] == 'Q3' else ''}"><strong>Q3 估值废墟</strong><span>低利率 + 弱叙事</span></div>
          <div class="quad {'active' if qmeta['code'] == 'Q2' else ''}"><strong>Q2 戴维斯双击</strong><span>低利率 + 强叙事</span></div>
          <span class="dot" title="Current position"></span>
          <div class="axis-label y-label">贴现率压力向上</div>
        </div>
        <div class="axis-label x-label">叙事/兑现压力向右</div>
        <p class="mobile-note">蓝点是当前坐标，越靠右表示叙事越拥挤或兑现压力越大，越靠上表示利率压力越高。</p>
      </div>
    </section>

    <section class="signals">
      {''.join(cards)}
    </section>

    <section class="table-wrap">
      <h2>底层数据</h2>
      <table>
        <thead><tr><th>轴</th><th>指标</th><th>最新值</th><th>分数</th><th>状态</th><th>来源</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""
    DOCS_INDEX.write_text(textwrap.dedent(html_doc), encoding="utf-8")


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
