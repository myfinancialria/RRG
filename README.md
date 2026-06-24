# RRG — Indian Sector Rotation

A beginner-friendly **Relative Rotation Graph (RRG)** for the Indian market. It shows
which sectors money is rotating **into** and **out of** — plotting every NSE sector and
sub-sector against the Nifty 50 on two axes:

- **RS-Ratio** (x) — is the sector stronger or weaker than the market? (100 = in line)
- **RS-Momentum** (y) — is that strength improving or fading?

Four quadrants, rotating clockwise: **Improving → Leading → Weakening → Lagging**.

### 🔗 Live dashboard
**https://myfinancialria.github.io/RRG/**

Updated automatically every weekday evening after the NSE close.

## Features
- **45 indices** — 17 broad NSE sectors + 28 sub-sectors (NBFC, Insurance, Defence,
  Railways, Cement, Chemicals, Telecom, Hospitals, Diagnostics, …), each built as a
  **cap-weighted basket of its real constituents**.
- **Daily / Weekly** toggle, adjustable **trail length**, **animated playback** and a
  **date scrubber** to replay how sectors rotated.
- **Symmetric axes** centred at (100, 100) so the four quadrants are equal and correct.
- **Click any sector** to list the stocks inside it that are in a confirmed uptrend
  (**50-DMA > 200-DMA** and **price > 50-DMA**), with each stock's distance above its 50-DMA.
- Plain-English "what the market is telling us" summary, a how-to-read guide, and a full FAQ.

## How it works
`build_rrg.py` is self-contained: it pulls daily prices from Yahoo Finance (no API key),
builds the indices using cap-weights bundled in `data/weights.json`, computes the JdK
RS-Ratio / RS-Momentum series, scores each constituent's trend, and renders the static
dashboard into `docs/`. A GitHub Actions cron job (`.github/workflows/daily.yml`) runs it
every weekday and commits the refreshed page; GitHub Pages serves `docs/`.

```bash
pip install -r requirements.txt
python build_rrg.py        # writes docs/index.html + docs/rrg.json
```

### Data
- **Prices** — Yahoo Finance, daily, auto-adjusted, ~2 years.
- **Benchmark** — `^NSEI` (Nifty 50).
- **Weights / names** — `data/weights.json`, `data/names.json` (market caps in ₹ crore).
  These move slowly; refresh occasionally. The index taxonomy lives in `data/indices.json`.

## Methodology
JdK z-score (the StockCharts reproduction):

```
RS        = 100 * sector_close / benchmark_close
RS-Ratio  = 100 + zscore(RS, window)                    , EMA-smoothed
RS-Mom    = 100 + zscore( ΔRS-Ratio over `mom`, window ), EMA-smoothed
```

Sector indices are cap-weighted: approximate share count = market_cap / price, then the
fixed basket is valued through time.

---
*For education and research only — not investment advice.*
