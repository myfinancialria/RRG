"""
RRG — Relative Rotation Graph for Indian sector rotation (self-contained build).

Fetches daily prices from Yahoo Finance (no API key), builds cap-weighted indices
for every NSE sector and sub-sector, computes JdK RS-Ratio / RS-Momentum, scores
each constituent stock's price trend, and renders a static dashboard. Designed to
run unattended in GitHub Actions once a day.

  Benchmark : ^NSEI (Nifty 50)
  Universe  : constituents listed in data/indices.json
  Weights   : data/weights.json (market cap, ₹ crore)  — refreshed occasionally
  Names     : data/names.json

Outputs
  docs/index.html   the dashboard (GitHub Pages serves /docs)
  docs/rrg.json     raw snapshot

Usage
  python build_rrg.py [--period 2y] [--window 14] [--mom 4] [--smooth 3]
"""
import os, sys, json, datetime as dt
import numpy as np
import pandas as pd
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DOCS = os.path.join(HERE, "docs")
TEMPLATE = os.path.join(HERE, "rrg_template.html")

BENCH = "NIFTY50"          # internal name
BENCH_TICKER = "^NSEI"     # Yahoo ticker for Nifty 50
BENCH_NAME = "Nifty 50"
MIN_MEMBERS = 4

INDICES = json.load(open(os.path.join(DATA, "indices.json")))
WEIGHTS = json.load(open(os.path.join(DATA, "weights.json")))
NAMES = json.load(open(os.path.join(DATA, "names.json")))


def _opt(name, default, cast=str):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def yf_ticker(sym):
    """NSE symbol -> Yahoo Finance ticker."""
    return sym + ".NS"


# ---------------------------------------------------------------- indicators
def zscore(s, w):
    m = s.rolling(w).mean()
    sd = s.rolling(w).std(ddof=0).replace(0, np.nan)
    return (s - m) / sd


SCALE = 1.8     # amplifies the z-score spread so the Trend/Momentum axes are readable (~95–105)


def jdk(sec_close, bench_close, window, mom, smooth):
    rs = 100.0 * sec_close / bench_close
    rs_ratio = (100.0 + SCALE * zscore(rs, window)).ewm(span=smooth, adjust=False).mean()
    rs_mom = (100.0 + SCALE * zscore(rs_ratio.diff(mom), window)).ewm(span=smooth, adjust=False).mean()
    return rs_ratio, rs_mom


def quad(x, y):
    if x >= 100 and y >= 100:
        return "Leading"
    if x >= 100:
        return "Weakening"
    if y >= 100:
        return "Improving"
    return "Lagging"


# ---------------------------------------------------------------- data
def fetch_prices(period):
    """Return a daily close DataFrame (cols = NSE symbols + BENCH)."""
    syms = sorted({s for v in INDICES.values() for s in v["members"]})
    tickers = [BENCH_TICKER] + [yf_ticker(s) for s in syms]
    print(f"Fetching {len(tickers)} tickers from Yahoo Finance ({period}) …")
    def _dl(tk):
        raw = yf.download(tk, period=period, interval="1d",
                          auto_adjust=True, progress=False, threads=True)
        return raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw

    close = _dl(tickers)
    # one retry pass for tickers Yahoo dropped on the first (often transient) call
    missing = [t for t in tickers if t not in close.columns
               or close[t].notna().sum() == 0]
    if missing:
        print(f"  retrying {len(missing)} missing: {', '.join(missing[:8])}…")
        retry = _dl(missing)
        for t in missing:
            if t in retry.columns and retry[t].notna().sum():
                close[t] = retry[t]

    ren = {BENCH_TICKER: BENCH}
    for s in syms:
        ren[yf_ticker(s)] = s
    close = close.rename(columns=ren)
    close = close.dropna(axis=1, how="all").sort_index()
    if BENCH not in close.columns:
        sys.exit("benchmark not fetched")
    close[BENCH] = close[BENCH].ffill()
    got = sum(1 for s in syms if s in close.columns)
    print(f"  got {got}/{len(syms)} constituents, {len(close)} sessions, "
          f"last {close.index[-1].date()}")
    return close


def build_sector_indices(daily_wide):
    out = pd.DataFrame(index=daily_wide.index)
    counts, members_used = {}, {}
    for name, meta in INDICES.items():
        cols = [m for m in meta["members"]
                if m in daily_wide.columns and WEIGHTS.get(m)]
        if len(cols) < MIN_MEMBERS:
            continue
        sub = daily_wide[cols].ffill()
        shares = {}
        for m in cols:
            s = sub[m].dropna()
            if not s.empty and s.iloc[-1] > 0:
                shares[m] = float(WEIGHTS[m]) / s.iloc[-1]
        cols = [m for m in cols if m in shares]
        if len(cols) < MIN_MEMBERS:
            continue
        out[name] = (sub[cols].bfill() * pd.Series(shares)).sum(axis=1)
        counts[name] = len(cols)
        members_used[name] = cols
    return out, counts, members_used


def compute_stocks(daily_wide, symbols):
    """Absolute price-trend per stock: 50-DMA>200-DMA AND price>50-DMA."""
    out = {}
    for sym in symbols:
        if sym not in daily_wide.columns:
            continue
        s = daily_wide[sym].dropna()
        if len(s) < 210:
            continue
        px = float(s.iloc[-1])
        dma50 = float(s.iloc[-50:].mean())
        dma200 = float(s.iloc[-200:].mean())
        out[sym] = {
            "name": NAMES.get(sym, sym),
            "good": bool(dma50 > dma200 and px > dma50),
            "above50": round((px / dma50 - 1) * 100, 1),
            "r5d": round((px / float(s.iloc[-6]) - 1) * 100, 1) if len(s) > 6 else None,
            "r1m": round((px / float(s.iloc[-22]) - 1) * 100, 1) if len(s) > 22 else None,
        }
    return out


def build_freq(wide, window, mom, smooth, keep, counts, members_used):
    b = wide[BENCH]
    dates = list(wide.index)
    sectors = []
    for name, meta in INDICES.items():
        if name not in wide.columns:
            continue
        ratio, momn = jdk(wide[name], b, window, mom, smooth)
        series = []
        for x, y in zip(ratio.values, momn.values):
            series.append(None if (np.isnan(x) or np.isnan(y))
                          else [round(float(x), 2), round(float(y), 2)])
        if sum(1 for v in series if v) < 5:
            continue
        sectors.append({"symbol": f"{counts.get(name, 0)} stocks", "name": name,
                        "group": meta["group"], "kind": meta["kind"],
                        "members": members_used.get(name, []), "series": series})

    if len(dates) > keep:
        cut = len(dates) - keep
        dates = dates[cut:]
        for s in sectors:
            s["series"] = s["series"][cut:]

    n = len(dates)
    play_start = 0
    for i in range(n):
        live = sum(1 for s in sectors if s["series"][i])
        if sectors and live >= 0.6 * len(sectors):
            play_start = i
            break
    return {"dates": [str(d.date()) for d in dates],
            "play_start": play_start, "sectors": sectors}


def run():
    period = _opt("--period", "2y")
    window = _opt("--window", 14, int)
    mom = _opt("--mom", 4, int)
    smooth = _opt("--smooth", 3, int)
    weekly_keep = _opt("--weekly-keep", 60, int)
    daily_keep = _opt("--daily-keep", 150, int)

    base = fetch_prices(period)
    sect, counts, members_used = build_sector_indices(base)
    sect[BENCH] = base[BENCH]
    print(f"built {len(counts)} cap-weighted sector indices")

    daily = sect.dropna(subset=[BENCH])
    weekly = sect.resample("W-FRI").last().dropna(subset=[BENCH])
    freqs = {
        "weekly": build_freq(weekly, window, mom, smooth, weekly_keep, counts, members_used),
        "daily":  build_freq(daily,  window, mom, smooth, daily_keep, counts, members_used),
    }
    as_of = str(daily.index[-1].date())     # real last trading session

    all_syms = sorted({m for cols in members_used.values() for m in cols})
    stocks = compute_stocks(base, all_syms)
    print(f"scored {len(stocks)} stocks "
          f"({sum(1 for v in stocks.values() if v['good'])} in uptrend)")

    groups = list(dict.fromkeys(v["group"] for v in INDICES.values()))
    snapshot = {
        "as_of": as_of,
        "generated": str(dt.datetime.utcnow().date()),
        "benchmark": BENCH, "benchmark_name": BENCH_NAME,
        "params": {"window": window, "mom": mom, "smooth": smooth},
        "groups": groups, "stocks": stocks, "freqs": freqs,
    }
    os.makedirs(DOCS, exist_ok=True)
    json.dump(snapshot, open(os.path.join(DOCS, "rrg.json"), "w"), separators=(",", ":"))

    payload = json.dumps(snapshot, separators=(",", ":"))
    html = open(TEMPLATE).read()
    html = html[: html.index("/*RRG*/") + 7] + payload + html[html.index("/*ENDRRG*/"):]
    open(os.path.join(DOCS, "index.html"), "w").write(html)
    print(f"wrote docs/index.html  (as_of {as_of}, {len(freqs['weekly']['sectors'])} indices)")


if __name__ == "__main__":
    run()
