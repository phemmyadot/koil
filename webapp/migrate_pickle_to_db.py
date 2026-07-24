"""One-time migration: read the old pickle caches (price_cache.pkl,
computed_cache.pkl, earnings_cache.pkl, universe_last_screened.txt) and bulk-
insert their contents into app_data.db, so a server that already has real
cached data doesn't lose it and start cold on the first deploy after the
DB migration lands.

Safe to run multiple times -- skips any pickle file that's missing, and
uses the same upsert functions the live app uses, so re-running just
re-writes the same rows. Does NOT delete the old pickle files; remove them
manually once you've confirmed the DB has what you expect.

Run once, manually, after pulling this change and before/after the first
deploy that includes it:
    .venv/Scripts/python.exe -m webapp.migrate_pickle_to_db
"""
import os
import pickle

import pandas as pd

import webapp.db as db

_HERE = os.path.dirname(__file__)


def migrate_price_cache() -> None:
    path = os.path.join(_HERE, "price_cache.pkl")
    if not os.path.isfile(path):
        print("migrate: no price_cache.pkl found, skipping.")
        return
    try:
        with open(path, "rb") as f:
            payload = pickle.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"migrate: price_cache.pkl unreadable ({e}), skipping.")
        return
    bars = payload.get("bars", {})
    fetched_at = payload.get("fetched_at", {})
    n = 0
    for ticker, df in bars.items():
        ts = fetched_at.get(ticker)
        if ts is None or df is None or df.empty:
            continue
        db.upsert_bars(ticker, df, ts)
        n += 1
    print(f"migrate: price_cache.pkl -> {n} tickers' bars migrated.")


def migrate_computed_cache() -> None:
    """Deliberately does NOT migrate old computed_cache.pkl rows: that file
    keyed staleness off a fetch-attempt epoch, and the DB now keys off
    last_bar_date (the actual last stored bar date) -- there's no reliable
    way to derive one from the other after the fact. Every ticker just
    recomputes once on the next compute_all() pass and re-populates
    correctly keyed, same cost as any other cold-start recompute. Left as a
    no-op (rather than removed) so migrate_pickle_to_db.py's __main__ block
    doesn't need editing if this file happens to still be present."""
    path = os.path.join(_HERE, "computed_cache.pkl")
    if os.path.isfile(path):
        print("migrate: computed_cache.pkl found but not migrated (staleness key changed "
              "from fetch-epoch to last_bar_date) -- every ticker will recompute once "
              "on the next pass instead.")
    else:
        print("migrate: no computed_cache.pkl found, skipping.")


def migrate_earnings_cache() -> None:
    path = os.path.join(_HERE, "earnings_cache.pkl")
    if not os.path.isfile(path):
        print("migrate: no earnings_cache.pkl found, skipping.")
        return
    try:
        with open(path, "rb") as f:
            cache = pickle.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"migrate: earnings_cache.pkl unreadable ({e}), skipping.")
        return
    n = 0
    for ticker, (fetched_at, dates) in cache.items():
        db.upsert_earnings_dates(ticker, dates, fetched_at)
        n += 1
    print(f"migrate: earnings_cache.pkl -> {n} tickers' earnings dates migrated.")


def migrate_universe_marker() -> None:
    path = os.path.join(_HERE, "universe_last_screened.txt")
    if not os.path.isfile(path):
        print("migrate: no universe_last_screened.txt found, skipping.")
        return
    with open(path) as f:
        date = f.read().strip()
    if date:
        epoch = pd.Timestamp(date, tz="UTC").timestamp()
        db.set_last_screened_at(epoch)
        print(f"migrate: universe_last_screened.txt -> marker set to {date} ({epoch}).")


if __name__ == "__main__":
    migrate_price_cache()
    migrate_computed_cache()
    migrate_earnings_cache()
    migrate_universe_marker()
    print("migrate: done. Verify with /api/debug/memory, then delete the old .pkl/.txt files manually.")
