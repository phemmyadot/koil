"""PDF export for the dashboard's ticker cards -- one full-width card per
selected asset, covering Pre-Breakout state and the single strategy currently
selected in the dashboard's Advance Filter, built entirely from the
already-computed payload dicts in app.py's _computed (no recompute, no
network/DB access at export time).
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
    """Target/Unrealized/Days held for an already-filled position -- entry date/price itself
    now lives in the strategy's header line (see _entry_status_html), not here."""
    t = stats.get("open_position")
    if not t:
        return []
    sign = "+" if t["unrealized_pct"] >= 0 else ""
    unreal_style = "value_pos" if t["unrealized_pct"] >= 0 else "value_neg"
    return [
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


def _entry_status_html(payload: dict, stats: dict) -> str:
    """'Entry <date> @ <price>' once a position is actually filled, using the real
    per-strategy open_position entry. Just 'Pending' when the strategy's own breakout
    condition fired on the latest close but nothing has filled yet -- no date or price shown
    because the actual fill happens at the NEXT bar's open, which hasn't happened yet and
    can't be known in advance (see STRATEGY_ARCHITECTURE.md)."""
    op = stats.get("open_position")
    if op:
        return f'Entry {op["entry_date"]} @ {op["entry_price"]}'
    if stats.get("signal_today"):
        color = ACCENT.hexval()[2:]
        return f'<font color="#{color}">Pending</font>'
    return ""


def _strategy_row(payload: dict, strategy: str) -> list:
    """One strategy's flowables: a single-line header ("VEXH  7/10 - Pending/Entry ...")
    followed by one wrapping line of all its stats (Trades/PF/WR/MAE/MFE/open-position/Avg
    Days) and one line for Last 5 -- a short horizontal strip instead of a tall stacked
    column, so the full card stays compact regardless of how many stats a strategy has."""
    stats = payload.get(strategy)
    label = STRATEGY_LABELS[strategy]

    score = (payload.get("setup_score") or {}).get(strategy)
    score_text = f"{score}/10" if score is not None else "&mdash;/10"
    score_color = _score_color(score).hexval()[2:]  # HexColor -> "rrggbb" for inline <font>
    header_html = f'<b>{label}</b> &nbsp; <font color="#{score_color}"><b>{score_text}</b></font>'
    if stats:
        status = _entry_status_html(payload, stats)
        if status:
            header_html += f" &nbsp;-&nbsp; {status}"
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


def _asset_section(payload: dict, strategy: str) -> KeepTogether:
    """Pre-Breakout (ticker-level, not strategy-specific, so always shown) + the one selected
    strategy's row, stacked as horizontal strips spanning the full card width -- scoped to
    strategy since the export mirrors whatever's selected in the dashboard's Advance Filter."""
    row_flows = [_prebreak_row(payload), _strategy_row(payload, strategy)]

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


def build_pdf(payloads: list[dict], strategy: str,
              generated_at_local: datetime | None = None) -> bytes:
    """payloads: the enriched per-ticker dicts already computed by app.py
    (same shape sent to the frontend via /api/tickers), one full-width card
    per entry, in the given order. strategy: the payload key ("vexh",
    "strategy_vcp", or "strategy_vcpo") to show -- matches the dashboard's
    Advance Filter strategy selector, so the export only covers whichever
    strategy the user is currently looking at, not all three.
    generated_at_local: the export moment already converted to the requesting
    browser's local timezone by app.py's endpoint (falls back to UTC here if
    not provided, e.g. when called directly/in tests)."""
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                             leftMargin=0.65 * inch, rightMargin=0.65 * inch,
                             topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    if generated_at_local is not None:
        tz_label = generated_at_local.tzname() or "local"
        generated_at = generated_at_local.strftime(f"%Y-%m-%d %H:%M {tz_label}")
    else:
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    strategy_label = STRATEGY_LABELS.get(strategy, strategy)
    title_row = Table(
        [[Paragraph(f"Exhaustion Dashboard Export &mdash; {strategy_label}", ParagraphStyle(
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
        story.append(_asset_section(payload, strategy))
    doc.build(story)
    return buf.getvalue()
