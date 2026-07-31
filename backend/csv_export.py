"""CSV export for the dashboard's ticker data -- one row per selected ticker,
scoped to the single strategy currently selected in the dashboard's Advance
Filter (same scope as pdf_export.py), built entirely from the already-computed
payload dicts in app.py's _computed (no recompute, no network/DB access at
export time).
"""
import csv
import io

STRATEGY_LABELS = {"vexh": "VEXH", "strategy_vcp": "VCP", "strategy_vcpo": "VCPO"}

FIELDNAMES = [
    "ticker", "price", "date", "earnings_risk",
    "prebreak_state", "prebreak_score", "prebreak_bb_squeeze", "prebreak_vol_dry_up",
    "prebreak_near_resistance", "prebreak_is_bullish_trend", "prebreak_squeeze_counter",
    "strategy", "setup_score", "verdict", "verdict_reason", "entry_status",
    "n_trades", "win_rate", "profit_factor", "avg_trade_days",
    "avg_mae_wins_pct", "pct_near_zero_mae", "avg_mfe_wins_pct", "max_trade_pnl_fraction",
    "first_trade_date",
    "open_entry_date", "open_entry_price", "open_target", "open_to_tp_pct",
    "open_unrealized_pct", "open_days_held", "open_mae_pct",
    "last5_tp_pct_1", "last5_days_1", "last5_tp_pct_2", "last5_days_2",
    "last5_tp_pct_3", "last5_days_3", "last5_tp_pct_4", "last5_days_4",
    "last5_tp_pct_5", "last5_days_5",
]


def _entry_status(payload: dict, stats: dict) -> str:
    """Same rule as pdf_export._entry_status_html, plain text instead of PDF markup:
    'Entry <date> @ <price>' once filled; 'Pending' when the signal fired on the latest
    close but hasn't filled yet (fills at the NEXT bar's open, price not yet known)."""
    op = stats.get("open_position")
    if op:
        return f'Entry {op["entry_date"]} @ {op["entry_price"]}'
    if stats.get("signal_today"):
        return "Pending"
    return ""


def _row(payload: dict, strategy: str) -> dict:
    pb = payload.get("prebreak") or {}
    row = {
        "ticker": payload["ticker"],
        "price": payload.get("price"),
        "date": payload.get("date"),
        "earnings_risk": payload.get("earnings_risk"),
        "prebreak_state": pb.get("state"),
        "prebreak_score": pb.get("score"),
        "prebreak_bb_squeeze": pb.get("bb_squeeze"),
        "prebreak_vol_dry_up": pb.get("vol_dry_up"),
        "prebreak_near_resistance": pb.get("near_resistance"),
        "prebreak_is_bullish_trend": pb.get("is_bullish_trend"),
        "prebreak_squeeze_counter": pb.get("squeeze_counter"),
        "strategy": STRATEGY_LABELS.get(strategy, strategy),
    }

    stats = payload.get(strategy)
    if not stats:
        row["verdict_reason"] = "no data"
        return row

    row.update({
        "setup_score": (payload.get("setup_score") or {}).get(strategy),
        "verdict": stats.get("verdict"),
        "verdict_reason": stats.get("verdict_reason"),
        "entry_status": _entry_status(payload, stats),
        "n_trades": stats.get("n_trades"),
        "win_rate": stats.get("win_rate"),
        "profit_factor": stats.get("profit_factor"),
        "avg_trade_days": stats.get("avg_trade_days"),
        "avg_mae_wins_pct": stats.get("avg_mae_wins_pct"),
        "pct_near_zero_mae": stats.get("pct_near_zero_mae"),
        "avg_mfe_wins_pct": stats.get("avg_mfe_wins_pct"),
        "max_trade_pnl_fraction": stats.get("max_trade_pnl_fraction"),
        "first_trade_date": stats.get("first_trade_date"),
    })

    op = stats.get("open_position")
    if op:
        row.update({
            "open_entry_date": op["entry_date"],
            "open_entry_price": op["entry_price"],
            "open_target": op["target"],
            "open_to_tp_pct": op["to_tp_pct"],
            "open_unrealized_pct": op["unrealized_pct"],
            "open_days_held": op["days_held"],
            "open_mae_pct": op["mae_pct"],
        })

    for i, t in enumerate(stats.get("last5_trades", [])[:5], start=1):
        row[f"last5_tp_pct_{i}"] = t["tp_pct"]
        row[f"last5_days_{i}"] = t["days"]

    return row


def build_csv(payloads: list[dict], strategy: str) -> str:
    """payloads: the enriched per-ticker dicts already computed by app.py
    (same shape sent to the frontend via /api/tickers), one row per entry.
    strategy: the payload key ("vexh", "strategy_vcp", or "strategy_vcpo") to
    export -- matches the dashboard's Advance Filter strategy selector."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDNAMES)
    writer.writeheader()
    for payload in payloads:
        writer.writerow(_row(payload, strategy))
    return buf.getvalue()
