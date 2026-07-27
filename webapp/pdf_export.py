"""PDF export for the dashboard's ticker cards -- one section per selected
asset, covering Pre-Breakout state and all three strategies (VEXH/VCP/VCPO),
built entirely from the already-computed payload dicts in app.py's
_computed (no recompute, no network/DB access at export time).
"""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                 TableStyle)

STRATEGY_LABELS = {"vexh": "VEXH", "strategy_vcp": "VCP", "strategy_vcpo": "VCPO"}

_styles = {
    "ticker": ParagraphStyle("ticker", fontSize=18, leading=22, spaceAfter=2, fontName="Helvetica-Bold"),
    "sub": ParagraphStyle("sub", fontSize=11, leading=14, textColor=colors.HexColor("#444444")),
    "h2": ParagraphStyle("h2", fontSize=12, leading=15, spaceBefore=10, spaceAfter=4, fontName="Helvetica-Bold"),
    "body": ParagraphStyle("body", fontSize=9.5, leading=13),
    "note": ParagraphStyle("note", fontSize=9, leading=12, textColor=colors.HexColor("#555555")),
}


def _open_position_rows(stats: dict) -> list[str]:
    t = stats.get("open_position")
    if not t:
        return []
    sign = "+" if t["unrealized_pct"] >= 0 else ""
    return [
        f"Entry {t['entry_date']} @ {t['entry_price']}",
        f"Target {t['target']}",
        f"Unrealized {sign}{t['unrealized_pct']}%",
        f"Bars held {t['days_held']}",
    ]


def _last5_str(last5_trades: list[dict]) -> str:
    if not last5_trades:
        return "&mdash;"
    parts = []
    for t in last5_trades:
        sign = "+" if t["tp_pct"] >= 0 else ""
        parts.append(f"{sign}{t['tp_pct']}% - {t['days']}D")
    return ", ".join(parts)


def _strategy_block(payload: dict, strategy: str) -> list:
    """One strategy's flowables: label, score, Trades/PF/WR, open-position fields (if any), Last 5 TP %."""
    stats = payload.get(strategy)
    label = STRATEGY_LABELS[strategy]
    flow = [Paragraph(label, _styles["h2"])]
    if not stats:
        flow.append(Paragraph("&mdash; no data", _styles["note"]))
        return flow

    score = (payload.get("setup_score") or {}).get(strategy)
    score_line = f"Score {score}/10" if score is not None else "Score &mdash;/10"
    lines = [
        score_line,
        f"Trades {stats.get('n_trades', 0)}",
        f"Profit factor {stats.get('profit_factor', 0)}",
        f"Win rate {stats.get('win_rate', 0)}%",
    ]
    lines.extend(_open_position_rows(stats))
    avg_days = stats.get("avg_trade_days")
    lines.append(f"Avg Days {avg_days if avg_days is not None else '&mdash;'}")
    lines.append(f"Last 5 TP % {_last5_str(stats.get('last5_trades', []))}")

    for line in lines:
        flow.append(Paragraph(line, _styles["body"]))
    return flow


def _prebreak_block(payload: dict) -> list:
    pb = payload.get("prebreak")
    flow = [Paragraph("Pre-Breakout", _styles["h2"])]
    if not pb:
        flow.append(Paragraph("&mdash; no data", _styles["note"]))
        return flow
    lines = [
        f"{pb['state']} ({pb['score']})",
        "COMPRESSED" if pb["bb_squeeze"] else "EXPANDED",
        "DRY" if pb["vol_dry_up"] else "NORMAL/HIGH",
        "COILING" if pb["near_resistance"] else "CLEAR",
        "BULLISH" if pb["is_bullish_trend"] else "BEARISH",
        f"{pb['squeeze_counter']} Bars",
    ]
    for line in lines:
        flow.append(Paragraph(line, _styles["body"]))
    return flow


def _asset_section(payload: dict) -> list:
    flow = [
        Paragraph(payload["ticker"], _styles["ticker"]),
        Paragraph(f"Price ${payload['price']:.2f}", _styles["sub"]),
    ]
    pb_flow = _prebreak_block(payload)
    strat_flows = [_strategy_block(payload, key) for key in STRATEGY_LABELS]

    # Pre-Breakout + the 3 strategy blocks laid out as columns side by side,
    # matching the dashboard card's own layout, instead of stacking
    # everything vertically (which would make a multi-asset export very tall).
    table = Table([[pb_flow, *strat_flows]], colWidths=[1.7 * inch] + [1.5 * inch] * 3)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    flow.append(Spacer(1, 6))
    flow.append(table)
    return flow


def build_pdf(payloads: list[dict]) -> bytes:
    """payloads: the enriched per-ticker dicts already computed by app.py
    (same shape sent to the frontend via /api/tickers), one section per
    entry, in the given order."""
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                             leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                             topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    story = []
    for i, payload in enumerate(payloads):
        if i > 0:
            story.append(Spacer(1, 14))
            story.append(Table([[""]], colWidths=[6.9 * inch], rowHeights=[1],
                                style=TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc"))])))
            story.append(Spacer(1, 14))
        story.extend(_asset_section(payload))
    doc.build(story)
    return buf.getvalue()
