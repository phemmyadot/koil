"""Crypto v2's own universe discovery -- reuses crypto_universe.py's screener internals
(_fetch_all_pages, _is_excluded, MEME_MARKET_CAP_FLOOR) since the data source is unchanged, but
classifies into the document's 4-tier category (large/mid/low/meme, doc §7's Asset.category)
instead of v1's 2-bucket large_cap/meme split -- v2 runs one unified engine across the whole
universe (market cap only affects execution_score/position sizing, never which entry logic
runs, per doc §7's own note), so it doesn't need per-bucket strategy configs the way v1 does.

"meme" here is a market-cap-percentile label, not a genuine meme-coin detector (that would need
off-chain/narrative metadata this app doesn't have) -- same honest simplification v1 already
makes with its own meme bucket.
"""
import backend.crypto_universe as crypto_universe

# Percentile bands over the non-excluded, floor-filtered candidate pool, ranked by market cap
# descending. Untuned starting points, same as everything else in crypto_v2/config.py.
LARGE_CAP_PERCENTILE = 0.10
MID_CAP_PERCENTILE = 0.30  # cumulative: top 10-30%
LOW_CAP_PERCENTILE = 0.60  # cumulative: top 30-60%
# below 60% (and above MEME_MARKET_CAP_FLOOR) -> meme


def discover() -> dict:
    """One _fetch_all_pages() call (the expensive part -- 16 screener requests, paced ~0.3s
    apart) feeding all three discovery outputs, rather than each of classify/market-cap/volume
    triggering its own redundant fetch. Returns {"categories": {...}, "market_caps": {...},
    "quote_volumes": {...}}."""
    quotes = [q for q in crypto_universe._fetch_all_pages() if not crypto_universe._is_excluded(q["symbol"])]
    market_caps = {q["symbol"]: q.get("marketCap") or 0.0 for q in quotes if q.get("marketCap")}
    quote_volumes = {q["symbol"]: (q.get("regularMarketVolume") or q.get("volume24Hr") or 0.0) for q in quotes}

    floored = [q for q in quotes if (q.get("marketCap") or 0) >= crypto_universe.MEME_MARKET_CAP_FLOOR]
    floored.sort(key=lambda q: q.get("marketCap") or 0, reverse=True)
    symbols = [q["symbol"] for q in floored]
    n = len(symbols)
    large_cut = int(n * LARGE_CAP_PERCENTILE)
    mid_cut = int(n * MID_CAP_PERCENTILE)
    low_cut = int(n * LOW_CAP_PERCENTILE)
    categories = {
        "large_cap": symbols[:large_cut],
        "mid_cap": symbols[large_cut:mid_cut],
        "low_cap": symbols[mid_cut:low_cut],
        "meme": symbols[low_cut:],
    }
    return {"categories": categories, "market_caps": market_caps, "quote_volumes": quote_volumes}
