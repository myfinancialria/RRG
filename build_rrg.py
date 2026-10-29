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
import os, sys, json, datetime as dt, html, re
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
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

# Broad-sector context used in the dashboard's daily research brief. These are
# deliberately structural, not company-specific recommendations; the current
# headlines are fetched separately at build time.
SWOT = {
    "Bank": {
        "strengths": ["Deposit franchise and recurring interest income", "Operating leverage as credit grows"],
        "weaknesses": ["Credit costs can rise quickly", "Net-interest margins are rate-sensitive"],
        "opportunities": ["Formalisation and retail-credit penetration", "Digital distribution and cross-sell"],
        "threats": ["Asset-quality cycle or deposit competition", "Regulatory capital and lending constraints"],
    },
    "Financial Services": {
        "strengths": ["Diversified exposure to credit, insurance and asset management", "Strong domestic financialisation trend"],
        "weaknesses": ["Mixed business quality across the basket", "Sensitive to liquidity and funding costs"],
        "opportunities": ["Household savings moving to market products", "Under-penetrated protection and retirement products"],
        "threats": ["Tighter regulation or mis-selling scrutiny", "Credit and market-cycle drawdowns"],
    },
    "IT": {
        "strengths": ["Export revenues and recurring client relationships", "High cash generation and asset-light models"],
        "weaknesses": ["Demand is tied to global technology budgets", "Large-cap concentration in the basket"],
        "opportunities": ["Cloud, AI and enterprise modernisation spend", "Vendor consolidation and productivity deals"],
        "threats": ["Rupee strength and wage inflation", "Discretionary cuts or faster automation"],
    },
    "Pharma": {
        "strengths": ["Defensive demand and diversified product portfolios", "India's manufacturing and export base"],
        "weaknesses": ["Regulatory observations can be company-specific", "Price erosion in generic markets"],
        "opportunities": ["Chronic therapies and complex generics", "Domestic healthcare access expansion"],
        "threats": ["USFDA or pricing action", "Currency, litigation and supply-chain risk"],
    },
    "Healthcare": {
        "strengths": ["Non-discretionary demand and rising formal care", "Capacity additions can compound earnings"],
        "weaknesses": ["Execution and occupancy vary by operator", "Capex and reimbursement discipline matter"],
        "opportunities": ["Insurance coverage, diagnostics and premiumisation", "Tier-2/3 hospital expansion"],
        "threats": ["Pricing controls and talent costs", "Execution delays or competitive capacity"],
    },
    "Auto": {
        "strengths": ["Large domestic replacement and first-time buyer market", "Operating leverage in upcycles"],
        "weaknesses": ["Demand is cyclical and financing-sensitive", "Commodity and currency inputs affect margins"],
        "opportunities": ["SUV, premium, EV and export mix", "Platform sharing and localisation"],
        "threats": ["Input-cost spikes and discounts", "Technology transition and regulation"],
    },
    "FMCG": {
        "strengths": ["Resilient demand and distribution depth", "Pricing power in branded categories"],
        "weaknesses": ["Valuations can embed long growth expectations", "Rural recovery may be uneven"],
        "opportunities": ["Premiumisation and rural consumption", "Distribution expansion and innovation"],
        "threats": ["Raw-material inflation and downtrading", "Private labels and local competition"],
    },
    "Consumer Durables": {
        "strengths": ["Premiumisation and formal retail penetration", "Brand and distribution moats"],
        "weaknesses": ["Discretionary demand is seasonal", "Working capital can expand with growth"],
        "opportunities": ["Cooling, electronics and housing-linked demand", "Omnichannel and premium products"],
        "threats": ["Commodity and currency pressure", "Promotional intensity or weak urban demand"],
    },
    "Infrastructure": {
        "strengths": ["Government and private capex visibility", "Large order books in core assets"],
        "weaknesses": ["Execution and working capital are material", "Conglomerate mix can obscure segment quality"],
        "opportunities": ["Transport, power and urban infrastructure spending", "Manufacturing-led supply-chain investment"],
        "threats": ["Project delays and cost overruns", "Funding, land and policy bottlenecks"],
    },
    "Metal": {
        "strengths": ["Operating leverage to prices and volumes", "Domestic infrastructure demand"],
        "weaknesses": ["Highly cyclical earnings", "Energy and freight costs are significant"],
        "opportunities": ["Capacity additions and value-added products", "Domestic demand resilience"],
        "threats": ["China/global oversupply", "Commodity-price and trade-policy shocks"],
    },
    "Oil & Gas": {
        "strengths": ["Scale and strategic domestic importance", "Integrated businesses can buffer parts of the cycle"],
        "weaknesses": ["Earnings depend on volatile commodities and spreads", "Policy can influence downstream economics"],
        "opportunities": ["Gas adoption, refining upgrades and energy transition", "Domestic exploration and infrastructure"],
        "threats": ["Crude-price and geopolitical shocks", "Carbon policy and substitution"],
    },
    "Energy": {
        "strengths": ["Rising electricity demand and long asset lives", "Visible transition and grid investment"],
        "weaknesses": ["Capital intensity and leverage", "Regulated returns can cap upside"],
        "opportunities": ["Renewables, storage, transmission and data centres", "Industrial electrification"],
        "threats": ["Fuel availability and tariff intervention", "Execution, financing and policy risk"],
    },
    "Media": {
        "strengths": ["Digital reach and scalable content economics", "Advertising and subscription optionality"],
        "weaknesses": ["Revenue is discretionary and volatile", "Audience fragmentation"],
        "opportunities": ["Streaming, sports and regional content", "Digital monetisation and ad-tech"],
        "threats": ["Platform bargaining power", "Content costs, regulation and weak ad cycles"],
    },
    "Realty": {
        "strengths": ["Urbanisation and formal developer consolidation", "Operating leverage in strong markets"],
        "weaknesses": ["High capital intensity and execution risk", "Regional concentration"],
        "opportunities": ["Premium housing, commercial and redevelopment", "Lower financing costs and formalisation"],
        "threats": ["Rates, approvals and inventory cycles", "Construction inflation and liquidity stress"],
    },
    "PSE (Public Sector)": {
        "strengths": ["Strategic assets and policy-linked visibility", "Potential for balance-sheet repair and dividends"],
        "weaknesses": ["Ownership and capital-allocation constraints", "Basket mixes unrelated businesses"],
        "opportunities": ["Public capex, disinvestment and efficiency reforms", "Energy, defence and manufacturing themes"],
        "threats": ["Policy intervention and tender timing", "Valuation mean reversion after crowded rallies"],
    },
}

NEWS_QUERIES = {
    "Bank": "India banks banking credit deposits RBI",
    "Financial Services": "India financial services NBFC insurance mutual funds",
    "IT": "India IT services technology exports AI cloud",
    "Pharma": "India pharma drugmakers healthcare regulation",
    "Healthcare": "India hospitals diagnostics healthcare",
    "Auto": "India auto sales vehicles electric vehicles",
    "FMCG": "India FMCG consumer goods rural consumption",
    "Consumer Durables": "India consumer durables electronics appliances",
    "Infrastructure": "India infrastructure capex roads power construction",
    "Metal": "India steel metals aluminium commodity prices",
    "Oil & Gas": "India oil gas refining energy",
    "Energy": "India power utilities renewable energy grid",
    "Media": "India media entertainment streaming advertising",
    "Realty": "India real estate housing property developers",
    "PSE (Public Sector)": "India public sector PSU policy divestment",
}

INDICES = json.load(open(os.path.join(DATA, "indices.json")))
WEIGHTS = json.load(open(os.path.join(DATA, "weights.json")))
NAMES = json.load(open(os.path.join(DATA, "names.json")))


def _opt(name, default, cast=str):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def yf_ticker(sym):
    """NSE symbol -> Yahoo Finance ticker."""
    return sym + ".NS"


def _clean_html(value):
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", text).strip()


def fetch_news(query, limit=2):
    """Fetch a small, best-effort Google News RSS snapshot for one sector.

    News is supplementary context: a feed outage must never prevent the price
    dashboard from updating. The source URL and publication timestamp are kept
    in the payload so the UI can label the evidence clearly.
    """
    if not query:
        return []
    url = ("https://news.google.com/rss/search?q=" + quote_plus(query) +
           "&hl=en-IN&gl=IN&ceid=IN:en")
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 RRG daily research brief"})
        with urlopen(req, timeout=10) as response:
            root = ET.fromstring(response.read())
        out = []
        for item in root.findall("./channel/item")[:limit]:
            title = _clean_html(item.findtext("title", ""))
            link = item.findtext("link", "")
            description = _clean_html(item.findtext("description", ""))
            source_node = item.find("source")
            source = _clean_html(source_node.text if source_node is not None else "")
            pub = item.findtext("pubDate", "")
            if title and link:
                out.append({"title": title, "link": link, "summary": description[:300],
                            "source": source or "Google News", "published": pub})
        return out
    except Exception as exc:
        print(f"  news unavailable for {query!r}: {exc}")
        return []


def build_research_brief():
    """Return current sector news plus a structural SWOT frame."""
    brief = {}
    for name, meta in INDICES.items():
        if meta.get("kind") != "sector":
            continue
        brief[name] = {
            "swot": SWOT.get(name, {
                "strengths": ["Established sector demand", "Diversified listed exposure"],
                "weaknesses": ["Performance varies across constituents", "Sensitive to the economic cycle"],
                "opportunities": ["Domestic growth and formalisation", "Productivity and market-share gains"],
                "threats": ["Macro, policy and competitive risk", "Valuation or liquidity shocks"],
            }),
            "news": fetch_news(NEWS_QUERIES.get(name, f"India {name} sector")),
            "query": NEWS_QUERIES.get(name, f"India {name} sector"),
        }
    return brief


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
    if got < max(MIN_MEMBERS, int(len(syms) * 0.60)):
        sys.exit(f"only fetched {got}/{len(syms)} constituents; refusing to overwrite the previous dashboard")
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

    print("Fetching sector news snapshots …")
    research = build_research_brief()

    groups = list(dict.fromkeys(v["group"] for v in INDICES.values()))
    snapshot = {
        "as_of": as_of,
        "generated": str(dt.datetime.utcnow().date()),
        "benchmark": BENCH, "benchmark_name": BENCH_NAME,
        "params": {"window": window, "mom": mom, "smooth": smooth},
        "groups": groups, "stocks": stocks, "freqs": freqs,
        "research": research,
        "research_generated": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "data_source": "Yahoo Finance daily adjusted close; Google News RSS for headlines",
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
