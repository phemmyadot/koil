"""PDF export for the dashboard's ticker cards -- one full-width card per
selected asset, covering Pre-Breakout state and all three strategies
(VEXH/VCP/VCPO), built entirely from the already-computed payload dicts in
app.py's _computed (no recompute, no network/DB access at export time).
"""
from datetime import datetime, timezone
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate,
                                 Spacer, Table, TableStyle)

STRATEGY_LABELS = {"vexh": "VEXH", "strategy_vcp": "VCP", "strategy_vcpo": "VCPO"}

# ---- palette (mirrors the dashboard's own chip color grading) ----
INK = colors.HexColor("#1a2333")
MUTED = colors.HexColor("#67718a")
LINE = colors.HexColor("#e3e7ee")
PANEL = colors.HexColor("#f6f7f9")
GREEN = colors.HexColor("#157f3d")
GREEN_BG = colors.HexColor("#e5f4ea")
RED = colors.HexColor("#b3261e")
RED_BG = colors.HexColor("#fbeae9")
GOLD = colors.HexColor("#8a6d1a")
GOLD_BG = colors.HexColor("#fdf3d8")
ACCENT = colors.HexColor("#2554c7")
ACCENT_BG = colors.HexColor("#e8eefb")

# letter width (8.5in) minus SimpleDocTemplate's left/right margins (0.65in each) below --
# keep in sync with build_pdf()'s leftMargin/rightMargin so cards fill the full usable width.
PAGE_WIDTH = 8.5 * inch - 2 * 0.65 * inch

_styles = {
    "docheader": ParagraphStyle("docheader", fontSize=9, leading=12, textColor=MUTED),
    "ticker": ParagraphStyle("ticker", fontSize=17, leading=20, fontName="Helvetica-Bold", textColor=INK),
    "sub": ParagraphStyle("sub", fontSize=9.5, leading=12, textColor=MUTED),
    "risk": ParagraphStyle("risk", fontSize=8.5, leading=11, textColor=RED, fontName="Helvetica-Bold"),
    "h2": ParagraphStyle("h2", fontSize=10.5, leading=13, fontName="Helvetica-Bold", textColor=INK),
    "score": ParagraphStyle("score", fontSize=9.5, leading=12, fontName="Helvetica-Bold"),
    "label": ParagraphStyle("label", fontSize=7.5, leading=9.5, textColor=MUTED),
    "value": ParagraphStyle("value", fontSize=9, leading=12, textColor=INK, spaceBefore=0.5, spaceAfter=4),
    "value_pos": ParagraphStyle("value_pos", fontSize=9, leading=12, textColor=GREEN,
                                 fontName="Helvetica-Bold", spaceBefore=0.5, spaceAfter=4),
    "value_neg": ParagraphStyle("value_neg", fontSize=9, leading=12, textColor=RED,
                                 fontName="Helvetica-Bold", spaceBefore=0.5, spaceAfter=4),
    "note": ParagraphStyle("note", fontSize=8.5, leading=12, textColor=MUTED, spaceBefore=4),
    "last5": ParagraphStyle("last5", fontSize=7.5, leading=10.5, textColor=MUTED, spaceBefore=3),
    # Horizontal-row rendering: one wrapping line of "label value  ·  label value  ·  ..." per
    # strategy, instead of a tall column of stacked label/value pairs -- much denser use of
    # the page's full width, and a card's height stays short regardless of how many stats it has.
    "rowlabel": ParagraphStyle("rowlabel", fontSize=9, leading=13, textColor=INK),
}


def _score_color(score: int | float | None) -> colors.HexColor:
    if score is None:
        return MUTED
    if score >= 8:
        return GREEN
    if score >= 5:
        return GOLD
    return RED


def _pf_color(pf: float) -> colors.HexColor:
    if pf >= 2.0:
        return GREEN
    if pf >= 1.5:
        return GOLD
    return RED


def _wr_color(wr: float) -> colors.HexColor:
    if wr >= 60:
        return GREEN
    if wr >= 45:
        return GOLD
    return RED


_VALUE_COLOR = {"value": None, "value_pos": GREEN, "value_neg": RED}


def _kv_row_html(rows: list[tuple[str, str, str]]) -> str:
    """rows: (label, value, value_style_key) -- rendered as one wrapping inline line
    ("Label value  ·  Label value  ·  ...") instead of a stacked column, so a strategy's
    whole stat set reads as a single horizontal strip across the card's full width."""
    parts = []
    for label, value, style_key in rows:
        color = _VALUE_COLOR.get(style_key)
        value_html = f'<font color="#{color.hexval()[2:]}">{value}</font>' if color else value
        parts.append(f'<font color="#67718a">{label}</font> {value_html}')
    return "  &middot;  ".join(parts)


def _open_position_rows(stats: dict) -> list[tuple[str, str, str]]:
    t = stats.get("open_position")
    if not t:
        return []
    sign = "+" if t["unrealized_pct"] >= 0 else ""
    unreal_style = "value_pos" if t["unrealized_pct"] >= 0 else "value_neg"
    return [
        ("Entry", f"{t['entry_date']} @ {t['entry_price']}", "value"),
        ("Target", str(t["target"]), "value"),
        ("Unrealized", f"{sign}{t['unrealized_pct']}%", unreal_style),
        ("Days held", str(t["days_held"]), "value"),
    ]


def _last5_str(last5_trades: list[dict]) -> str:
    if not last5_trades:
        return "&mdash;"
    parts = []
    for t in last5_trades:
        color = "#157f3d" if t["tp_pct"] >= 0 else "#b3261e"
        sign = "+" if t["tp_pct"] >= 0 else ""
        parts.append(f'<font color="{color}">{sign}{t["tp_pct"]}%</font> ({t["days"]}D)')
    return "  &middot;  ".join(parts)


def _strategy_row(payload: dict, strategy: str) -> list:
    """One strategy's flowables: a single-line header ("VEXH  7/10") followed by one wrapping
    line of all its stats (Trades/PF/WR/MAE/MFE/open-position/Avg Days) and one line for
    Last 5 -- a short horizontal strip instead of a tall stacked column, so the full card
    stays compact regardless of how many stats a strategy has."""
    stats = payload.get(strategy)
    label = STRATEGY_LABELS[strategy]

    score = (payload.get("setup_score") or {}).get(strategy)
    score_text = f"{score}/10" if score is not None else "&mdash;/10"
    score_color = _score_color(score).hexval()[2:]  # HexColor -> "rrggbb" for inline <font>
    header_html = f'<b>{label}</b> &nbsp; <font color="#{score_color}"><b>{score_text}</b></font>'
    flow = [Paragraph(header_html, _styles["h2"])]

    if not stats:
        flow.append(Paragraph("&mdash; no data", _styles["note"]))
        return flow

    pf = stats.get("profit_factor", 0)
    wr = stats.get("win_rate", 0)
    rows = [
        ("Trades", str(stats.get("n_trades", 0)), "value"),
        ("Profit factor", f"{pf}", "value_pos" if pf >= 1.5 else "value"),
        ("Win rate", f"{wr}%", "value_pos" if wr >= 50 else "value"),
    ]
    rows.extend(_open_position_rows(stats))

    avg_mae = stats.get("avg_mae_wins_pct")
    avg_mfe = stats.get("avg_mfe_wins_pct")
    if avg_mae is not None:
        rows.append(("Avg MAE (wins)", f"-{avg_mae}%", "value_neg"))
    if avg_mfe is not None:
        rows.append(("Avg MFE (wins)", f"+{avg_mfe}%", "value_pos"))

    avg_days = stats.get("avg_trade_days")
    rows.append(("Avg days", str(avg_days) if avg_days is not None else "&mdash;", "value"))

    flow.append(Paragraph(_kv_row_html(rows), _styles["rowlabel"]))
    flow.append(Paragraph(f"Last 5: {_last5_str(stats.get('last5_trades', []))}", _styles["last5"]))
    return flow


def _prebreak_row(payload: dict) -> list:
    pb = payload.get("prebreak")
    flow = [Paragraph("<b>Pre-Breakout</b>", _styles["h2"])]
    if not pb:
        flow.append(Paragraph("&mdash; no data", _styles["note"]))
        return flow
    rows = [
        ("State", f"{pb['state']} ({pb['score']})", "value_pos" if pb["score"] >= 7 else "value"),
        ("Bollinger", "COMPRESSED" if pb["bb_squeeze"] else "EXPANDED", "value"),
        ("Volume", "DRY" if pb["vol_dry_up"] else "NORMAL/HIGH", "value"),
        ("Resistance", "COILING" if pb["near_resistance"] else "CLEAR", "value"),
        ("Trend", "BULLISH" if pb["is_bullish_trend"] else "BEARISH",
         "value_pos" if pb["is_bullish_trend"] else "value_neg"),
        ("Squeeze", f"{pb['squeeze_counter']} bars", "value"),
    ]
    flow.append(Paragraph(_kv_row_html(rows), _styles["rowlabel"]))
    return flow


def _max_days_in_trade(payload: dict) -> int | None:
    """Longest days-held across any open position on this ticker (VEXH/VCP/VCPO) -- same
    figure the dashboard card shows next to the earnings-risk flag."""
    max_days = None
    for key in STRATEGY_LABELS:
        stats = payload.get(key)
        op = stats.get("open_position") if stats else None
        if op is not None:
            max_days = op["days_held"] if max_days is None else max(max_days, op["days_held"])
    return max_days


def _card_header(payload: dict) -> Table:
    """Ticker + price/date on the left, earnings-risk flag (if any) right-aligned on
    the same row -- a real header bar with its own background instead of stacked text."""
    left = [Paragraph(payload["ticker"], _styles["ticker"]),
            Paragraph(f"${payload['price']:.2f}  &middot;  {payload.get('date', '')}", _styles["sub"])]
    right = []
    if payload.get("earnings_risk"):
        days_in_trade = _max_days_in_trade(payload)
        suffix = f" &mdash; {days_in_trade}D in trade" if days_in_trade is not None else ""
        right.append(Paragraph(f"&#9888; EARNINGS RISK{suffix}", _styles["risk"]))

    header = Table([[left, right]], colWidths=[PAGE_WIDTH * 0.7, PAGE_WIDTH * 0.3])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (0, 0), 12),
        ("RIGHTPADDING", (1, 0), (1, 0), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, 0), (-1, -1), 1, LINE),
    ]))
    return header


def _asset_section(payload: dict) -> KeepTogether:
    """Pre-Breakout + the 3 strategies as 4 stacked horizontal strips (each spanning the
    full card width), not 4 side-by-side columns -- far denser use of the page's width,
    since each strip is one or two wrapped lines tall instead of a whole narrow column."""
    row_flows = [_prebreak_row(payload)] + [_strategy_row(payload, key) for key in STRATEGY_LABELS]

    body_rows = []
    for flow in row_flows:
        body_rows.append([flow])
    body = Table(body_rows, colWidths=[PAGE_WIDTH - 24])
    body.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, LINE),
    ]))

    card = Table([[_card_header(payload)], [body]], colWidths=[PAGE_WIDTH])
    card.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("BOX", (0, 0), (-1, -1), 1, LINE),
    ]))
    # Keep each full card on one page where possible instead of splitting a ticker
    # across a page break.
    return KeepTogether([card])


def build_pdf(payloads: list[dict]) -> bytes:
    """payloads: the enriched per-ticker dicts already computed by app.py
    (same shape sent to the frontend via /api/tickers), one full-width card
    per entry, in the given order."""
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                             leftMargin=0.65 * inch, rightMargin=0.65 * inch,
                             topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title_row = Table(
        [[Paragraph("Exhaustion Dashboard Export", ParagraphStyle(
            "title", fontSize=14, leading=17, fontName="Helvetica-Bold", textColor=INK)),
          Paragraph(f"Generated {generated_at}", _styles["docheader"])]],
        colWidths=[PAGE_WIDTH * 0.6, PAGE_WIDTH * 0.4])
    title_row.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story = [title_row, Spacer(1, 4),
             Table([[""]], colWidths=[PAGE_WIDTH], rowHeights=[1],
                   style=TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1.5, INK)])),
             Spacer(1, 16)]
    for i, payload in enumerate(payloads):
        if i > 0:
            story.append(Spacer(1, 16))
        story.append(_asset_section(payload))
    doc.build(story)
    return buf.getvalue()
