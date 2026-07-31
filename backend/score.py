"""Setup-quality scoring, per scoring.md.

Score = setup signal (0-10, 8 dimensions). Distinct from the Advance Filter,
which is an execution-quality gate (PF/WR/trade-count floors). Both are
required before entry -- this module only computes the former.
"""


def compute_score(r: dict, strategy: str = "strategy_vcpo") -> int:
    s = r.get(strategy) or {}
    prebreak = r.get("prebreak") or {}

    # 1. Strategy Quality (0-3)
    pf = s.get("profit_factor", 0)
    wr = s.get("win_rate", 0)
    if pf >= 3.5 and wr >= 75:
        strategy_pts = 3
    elif pf >= 2.5 and wr >= 70:
        strategy_pts = 2
    elif pf >= 2.0 and wr >= 65:
        strategy_pts = 1
    else:
        strategy_pts = 0

    # 2. Sample Size (0-1)
    sample_pts = 1 if s.get("n_trades", 0) >= 20 else 0

    # 3. VCP Matrix Setup (0-1)
    vcp_pts = 1 if prebreak.get("score", 0) >= 4 else 0

    # 4. Compression Quality (0-1)
    compression_pts = 1 if (prebreak.get("bb_squeeze", False)
                             and prebreak.get("vol_dry_up", False)) else 0

    # 5. Signal Timing / MAE (0-1)
    open_pos = s.get("open_position")
    avg_mae = s.get("avg_mae_wins_pct")
    if open_pos and avg_mae is not None:
        timing_pts = 1 if open_pos.get("mae_pct", 0) < avg_mae else 0
    elif open_pos is None and s.get("signal_today", False):
        timing_pts = 1  # fresh signal today, not yet in a position
    else:
        timing_pts = 0  # no signal at all -- stale history isn't a favourable timing signal

    # 6. Outlier PnL Concentration (0-1)
    outlier_pts = 1 if s.get("max_trade_pnl_fraction", 1.0) <= 0.35 else 0

    # 7. Recency of Performance (0-1)
    last5 = s.get("last5_trades", [])
    wins_in_last5 = sum(1 for t in last5 if t.get("tp_pct", 0) > 0)
    recency_pts = 1 if (len(last5) >= 5 and wins_in_last5 >= 4) else 0

    # 8. Earnings Proximity (0-1) -- ticker-level, not nested under s
    earnings_pts = 0 if r.get("earnings_risk", False) else 1

    return (strategy_pts + sample_pts + vcp_pts + compression_pts
            + timing_pts + outlier_pts + recency_pts + earnings_pts)
