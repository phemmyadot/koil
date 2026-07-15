"""
Rebuild webapp/tickers.py from scratch.

Screens Yahoo's equity screener API for the same objective criteria used to
originally curate p.py's target_universe:
    cap 300M+, avg vol >500K, price $5-$50, above SMA200, SMA50 > SMA200,
    weekly volatility > 5%

Run from the project root:
    .\\.venv\\Scripts\\python.exe -m webapp.build_universe
"""
import os
import time

import numpy as np
import pandas as pd
import yfinance as yf

OUT_PATH = os.path.join(os.path.dirname(__file__), "tickers.py")
CHUNK = 100


# Major US listing venues only -- excludes OTC/Pink Sheets (exchange code "PNK"),
# which are essentially never optionable and carry outsized pump-and-dump risk,
# a bigger concern now that the cap floor can go as low as $100M.
MAJOR_EXCHANGES = ["NMS", "NYQ", "NCM", "NGM", "ASE", "PCX"]


def fetch_candidates(min_cap: int = 300_000_000, min_vol: int = 500_000,
                      price_range: tuple[int, int] = (5, 100),
                      exchanges: list[str] | None = MAJOR_EXCHANGES) -> list[str]:
    """Server-side filter: cap/volume/price/exchange. Returns matching symbols."""
    filters = [
        yf.EquityQuery("gt", ["intradaymarketcap", min_cap]),
        yf.EquityQuery("gt", ["avgdailyvol3m", min_vol]),
        yf.EquityQuery("btwn", ["intradayprice", price_range[0], price_range[1]]),
        yf.EquityQuery("eq", ["region", "us"]),
    ]
    if exchanges:
        filters.append(yf.EquityQuery("is-in", ["exchange", *exchanges]))
    query = yf.EquityQuery("and", filters)
    symbols, offset, size, total = [], 0, 250, None
    while True:
        res = yf.screen(query, size=size, offset=offset, sortField="ticker", sortAsc=True)
        quotes = res.get("quotes", [])
        if not quotes:
            break
        symbols.extend(q["symbol"] for q in quotes)
        total = res.get("total")
        offset += size
        if offset >= total:
            break
        time.sleep(0.3)
    return symbols


def passes_technical_filters(close: pd.Series) -> bool:
    """Client-side filter: SMA200/SMA50/weekly-volatility (not exposed by the screener API)."""
    close = close.dropna()
    if len(close) < 210:
        return False
    sma200 = close.rolling(200).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    price = close.iloc[-1]
    if pd.isna(sma200) or pd.isna(sma50):
        return False
    if not (price > sma200 and sma50 > sma200):
        return False
    weekly = close.resample("W-FRI").last().dropna()
    weekly_ret = weekly.pct_change().dropna().tail(52)
    if len(weekly_ret) < 10:
        return False
    return weekly_ret.std() * 100 > 5.0


def screen_technicals(symbols: list[str]) -> list[str]:
    passed = []
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        t0 = time.time()
        try:
            df = yf.download(chunk, period="2y", interval="1d", group_by="ticker",
                              progress=False, auto_adjust=False, threads=True)
        except Exception as e:  # noqa: BLE001 - one bad chunk shouldn't kill the run
            print(f"  chunk {i // CHUNK + 1}: download failed: {e}")
            continue
        for sym in chunk:
            try:
                sub = df[sym] if len(chunk) > 1 else df
                if passes_technical_filters(sub["Close"]):
                    passed.append(sym)
            except Exception:  # noqa: BLE001 - per-symbol data issues are expected/skippable
                continue
        print(f"  chunk {i // CHUNK + 1}/{(len(symbols) + CHUNK - 1) // CHUNK}: "
              f"{len(chunk)} tickers in {time.time() - t0:.1f}s, {len(passed)} passing so far")
    return passed


def write_tickers_file(tickers: list[str], note: str = "") -> None:
    lines = [
        '"""',
        "Screened ticker universe for the exhaustion dashboard.",
        "",
        "Rebuild with: .venv/Scripts/python.exe -m webapp.build_universe",
        "Base criteria: price $5-$50, above SMA200, SMA50 > SMA200, weekly",
        "volatility > 5% -- matches the screen used to curate p.py's",
        "target_universe. Cap/volume floor is tunable (see --min-cap/--min-vol).",
    ]
    if note:
        lines.append(note)
    lines += ['"""', "", "TICKERS = ["]
    tickers = sorted(tickers)
    for i in range(0, len(tickers), 8):
        lines.append("    " + ", ".join(repr(t) for t in tickers[i:i + 8]) + ",")
    lines.append("]")
    with open(OUT_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-cap", type=int, default=300_000_000)
    parser.add_argument("--min-vol", type=int, default=500_000)
    parser.add_argument("--merge", action="store_true",
                         help="union with the existing webapp/tickers.py instead of replacing it")
    parser.add_argument("--allow-otc", action="store_true",
                         help="skip the major-exchange filter (allows OTC/Pink Sheet names through)")
    args = parser.parse_args()

    candidates = fetch_candidates(min_cap=args.min_cap, min_vol=args.min_vol,
                                   exchanges=None if args.allow_otc else MAJOR_EXCHANGES)
    print(f"Screening {len(candidates)} candidates for SMA200/SMA50/weekly-volatility criteria "
          f"(cap>{args.min_cap:,}, vol>{args.min_vol:,})...")
    passed = screen_technicals(candidates)

    if args.merge:
        from webapp.tickers import TICKERS as existing
        before = len(existing)
        merged = sorted(set(existing) | set(passed))
        write_tickers_file(merged, note=f"Merged run: cap>{args.min_cap:,}, vol>{args.min_vol:,} added "
                                         f"{len(merged) - before} new names on top of the base screen.")
        print(f"\nDONE. {len(passed)} passed this run, merged with {before} existing -> {len(merged)} total.")
    else:
        write_tickers_file(passed)
        print(f"\nDONE. {len(passed)} tickers passed all criteria out of {len(candidates)} candidates.")
    print(f"Wrote {OUT_PATH}")
