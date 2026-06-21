# DART Market Dashboard

一个把宏观压力和科技叙事放到同一张表里的轻量仓库。

DART 不是预测器，而是一个坐标系：

- **D**: Discount Rate，贴现率压力，主要看 10 年期美债和实际利率。
- **A**: AI Capex Cycle，AI 资本开支周期，主要看 hyperscalers 的资本开支强度。
- **R**: Risk Premium，风险溢价，主要看 VIX、信用利差和风险资产波动。
- **T**: Tech Narrative Half-Life，科技叙事半衰期，主要看媒体热度和新增叙事速度。

## Latest DART Table

<!-- DART_TABLE_START -->
| Axis | Metric | Latest | Score | Status | Source |
|---|---|---:|---:|---|---|
| D | Discount Rate | 10Y 4.50% / Real 10Y 2.10% | 57/100 | Watch | FRED DGS10 fallback 2026-06-21; DFII10 fallback 2026-06-21 |
| A | AI Capex Cycle | Index 72/100 / YoY 34% | 32/100 | Calm | Manual metrics as of 2026-06-06 |
| R | Risk Premium | VIX 18.00 / BAA10Y 2.20% | 31/100 | Calm | FRED VIXCLS fallback 2026-06-21; BAA10Y fallback 2026-06-21 |
| T | Tech Narrative Half-Life | 30d AI media mentions 25,000 | 65/100 | Watch | GDELT 2.0 fallback 2026-06-21 |
| Market | Risk Asset Tape | Nasdaq 21,000 / BTC $64,083 | 44/100 | Watch | FRED NASDAQCOM fallback 2026-06-21; CoinGecko BTC 2026-06-21 |
<!-- DART_TABLE_END -->

## Quadrant

<!-- DART_QUADRANT_START -->
**Quadrant I: High Rate + Strong Narrative**: Bubble carnival conditions.

Updated: 2026-06-21T13:19:47+00:00
<!-- DART_QUADRANT_END -->

## How It Updates

GitHub Actions runs `scripts/update_dart.py` every day and commits changes back into:

- `data/dart_latest.json`
- `data/dart_latest.csv`
- this README
- `docs/index.html`

You can also run it locally:

```bash
python3 scripts/update_dart.py
```

## Data Sources

The script uses no API keys by default:

- FRED CSV exports for Treasury and credit spread series.
- FRED CSV exports for VIX and Nasdaq Composite context.
- CoinGecko public endpoint for BTC spot price.
- GDELT 2.0 doc API for AI narrative volume.
- `data/manual_metrics.csv` for quarterly AI capex values that should be checked after earnings.

Manual metrics are intentionally explicit. For capex and narrative interpretation, being roughly right with visible assumptions is better than hiding fragile scraping behind false precision.
