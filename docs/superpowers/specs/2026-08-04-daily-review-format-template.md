# Daily Review Output Template

Target structure for `review_claude.py`'s `generate_daily_review()` output, replacing the
current free-form format. See
`docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md` for the chatbot
this feeds into. Placeholders (`{{...}}`) mark where snapshot data or Claude's own generation
fills in — this file documents the target shape, it is not rendered by a template engine.

Sections, in order: **Market Context → Strategy Trades → Investment →
Take — Enter Tomorrow → Missed Entries Worth Discussing → Session Notes.** No Watchlist
section — a TAKE/Pending verdict is a one-day state (fires at next day's open or reverts), so
every currently-Pending ticker belongs in "Take — Enter Tomorrow"; there is no separate
multi-day pending backlog to show.

"Your Trades" (2.0/2.5) splits real open positions by how they were entered: **Strategy Trades**
(entered off an actual strategy signal — mechanical verdict + AI note, as before) and
**Investment** (entered manually as a long-term holding — no strategy verdict exists or is
implied; commentary reads as a thesis check-in, not signal-following). Either subsection is
omitted entirely if its list is empty.

```markdown
# KOIL Daily Review
**Date:** {{date}}
**Time:** {{time}} ET

---

## Market Context
{{market_summary}}

<!-- Claude-generated via the web_search server tool on this call — not app-fetched.
     Claude searches for today's index/yield/oil levels and writes this table + macro
     line itself. -->

| Index | Close | Change |
|---|---|---|
| S&P 500 | {{sp500_close}} | {{sp500_chg}}% |
| Nasdaq | {{nasdaq_close}} | {{nasdaq_chg}}% |
| Dow | {{dow_close}} | {{dow_chg}}% |
| 10Y Yield | {{yield_10y}} | {{yield_chg}} |
| Oil (WTI) | {{oil_price}} | {{oil_chg}}% |

> **Key event:** {{macro_event}}

---

## 2.0 Strategy Trades
*Real open positions entered off an actual strategy signal. Act now.*

{{#each strategy_positions}}
### {{ticker}} | {{current_price}} | {{unrealized_pct}}% | {{strategy_verdict}}

| | Today | Yesterday |
|---|---|---|
| Price | {{current_price}} | {{prior_day.close_price}} |
| Unrealized | {{unrealized_pct}}% | {{prior_day.unrealized_pct}}% |

Verdict: **{{strategy_verdict}}** *(mechanical, from the strategy's own signal)*

{{#if ai_note}}
> **AI note:** {{ai_note}} {{#if decision_changed}}*(changed from yesterday)*{{else}}*(same as yesterday)*{{/if}}
<!-- Claude's own judgment, e.g. "consider exiting early — momentum stalling despite
     still being under TP." Always shown separately from the verdict above, never
     replacing it. Omitted when Claude has nothing notable to add.
     "same/changed from yesterday" is Claude comparing against its own prior AI note for
     this ticker, read from review_memory_summary (see below) — not a new field, a
     prompting requirement on the existing rolling memory. -->
{{/if}}

---
{{/each}}
<!-- Subsection omitted entirely if strategy_positions is empty. -->

---

## 2.5 Investment
*Real open positions entered manually as a long-term holding, not from a strategy signal.
No strategy verdict — never invented for these.*

{{#each investment_positions}}
### {{ticker}} | {{current_price}} | {{unrealized_pct}}%

| | Today | Yesterday |
|---|---|---|
| Price | {{current_price}} | {{prior_day.close_price}} |
| Unrealized | {{unrealized_pct}}% | {{prior_day.unrealized_pct}}% |

{{#if ai_note}}
> **AI note:** {{ai_note}} {{#if decision_changed}}*(changed from yesterday)*{{else}}*(same as yesterday)*{{/if}}
<!-- Reads as a long-term thesis check-in, not tactical signal-following -- no verdict
     line above it since none applies to a manual holding. Same same/changed-from-
     yesterday continuity as Strategy Trades. -->
{{/if}}

---
{{/each}}
<!-- Subsection omitted entirely if investment_positions is empty. -->

---

## Take — Enter Tomorrow
*Tickers currently in TAKE/Pending state — fires at tomorrow's open if untouched.*

{{#each pending_signals}}
### {{ticker}} — Score {{score}}/10

{{stats_summary}}
<!-- win rate / profit factor / VCP matrix / MAE context, whatever's relevant per strategy -->

**Order**

```
Spot: limit ${{spot_limit}} (support ${{support_used}}) — {{order_method}}
```
<!-- Spot-only — no options line (no per-ticker options data exists for a signal with no
     position yet). Every listed signal has already cleared the shared quality filter
     (backend/quality_filter.py), so there is no watch-only tier here. -->

{{spot_vs_options_lean}}
<!-- Claude-authored, not computed — the snapshot has no live options chain/IV/liquidity for a
     signal with no position yet, so this is a general lean, not a claim about this ticker's
     real chain: either "spot is the better fit, enter at $X.XX" (using the order block's own
     number), or a conditional rule of thumb ("options could work if IV is under ~40% and DTE is
     30+ days, keep the spread under $0.10-0.15") calibrated to the setup, never an invented IV
     or strike. One short clause, not a paragraph. -->

> **Verdict:** {{verdict}}

---
{{/each}}

---

## Missed Entries Worth Discussing
*Optional — only appears when there's a real judgment call to make. Tickers where a strategy's
own simulated backtest is IN TRADE but the user never entered, signal fired within the last 3
days. Not every open_signals entry gets written up -- Claude selects which are worth a late
entry, one line each: a short verdict, a short reason, and the same spot-vs-options lean as
above. No stats, no multi-sentence justification — this section is a scan, not a writeup.*

{{#each open_signals_discussed}}
- **{{ticker}} — {{verdict}}.** {{short_reason}} {{spot_vs_options_lean}}
{{/each}}

---

## Session Notes

**What worked / what to note:**
{{#each session_notes}}
- {{this}}
{{/each}}

---
*Generated by KOIL — {{date}} {{time}} ET*
```

## Data sources per section

- **Market Context**: `CONTEXT_TICKERS` (SPY/QQQ/DIA/^TNX/USO, ETF proxies) fetched via the same
  universe cycle as any watchlisted/traded ticker (`app.py`'s `_active_tickers()`), read via
  `_build_market_context()`. Not `web_search` — dropped after proving slow (8-12 searches per
  call) and prone to truncating the rest of the review.
- **Strategy Trades / Investment**: `build_daily_snapshot()` splits its open-positions loop into
  `strategy_positions` and `investment_positions` by each position's entry fill's `strategy_key`
  (`"manual"` → Investment, anything else → Strategy Trades — read directly off the fills
  already fetched per position, no new query). Both carry the same `prior_day` field (from
  `db.get_trade_daily_marks(position_id)`, yesterday's `close_price` diffed against today's).
  `strategy_verdict` only applies to Strategy Trades (mechanical, unchanged); `ai_note` is
  Claude's own generated text either way, not stored input.
- **Take — Enter Tomorrow**: every ticker whose current strategy verdict is TAKE, restricted to
  `quality_filter.DEFAULT_FILTER["strategies"]` (VCPO today) and passing the shared quality bar
  — no watch-only tier, everything listed already cleared the filter. Order limit comes from
  `entry_estimate.py`'s `estimate_entry()`, working off `current_price` directly (no existing
  position needed).
- **Missed Entries Worth Discussing**: `open_signals` in `build_daily_snapshot()` — tickers
  where a strategy's own `open_position` exists (its simulated backtest is IN TRADE) but the
  ticker isn't in the user's real `open_positions`, capped to `days_held <= 3`. Claude selects
  which entries are worth writing up; the section is omitted entirely when nothing qualifies.
- **Session Notes**: Claude-authored, same rolling-memory/enrichment approach as the current
  implementation.

## Decision continuity (per-position, day-over-day)

Already-existing storage covers this — no schema change:
- `daily_reviews.summary_text` keeps every past day's full review, retrievable by `review_date`.
- `review_memory_summary` is the rolling cross-day summary already passed into every
  `generate_daily_review()` call and updated after every review (`update_rolling_memory()`).

The only change needed is in prompting: `update_rolling_memory()`'s extraction prompt must be
told to explicitly retain per-ticker calls ("keep XYZ", "watching for exit"), not just general
themes, and `generate_daily_review()`'s system prompt must instruct Claude to check each open
position's AI note against its own most recent prior call for that ticker (from the rolling
summary) and state plainly whether today's call is the same or has changed — never silently
repeat or silently reverse a decision without saying so.
