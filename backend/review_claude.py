"""Claude API calls for the daily review chatbot: the review itself, chat follow-ups, and the
two enrichment-extraction calls. See
docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md, Parts 5-6.

Model is claude-sonnet-5, hardcoded (user's explicit choice for this feature, not the
Opus-by-default general rule) -- see the design doc's "Decisions (resolved)".
"""
import json
from datetime import datetime, timezone

import anthropic

import backend.market_hours as market_hours

MODEL = "claude-sonnet-5"


class ReviewTruncatedError(Exception):
    """Raised when generate_daily_review()'s response hit max_tokens before finishing."""

SYSTEM_PROMPT = """You are a trading review assistant for a single user's personal trading \
app. Each trading day, once the market has closed and that day's data is final, you produce a \
review of the user's current open positions and today's new entry signals, grounded in their \
own stated trading philosophy and past patterns you've learned about them.

## Current market status

Every message you receive (the review-generation turn and every chat turn) includes a "Current \
market status" line -- computed fresh at the moment that message was sent, not part of the \
frozen daily snapshot below. It tells you whether the market (9:30 AM-4:00 PM ET, Mon-Fri) is \
currently open or closed, and how long it's been open/closed or until it opens/closes next. Use \
this to ground any time-sensitive answer: if the user asks something mid-day during a live chat \
(the market may still be open even though the review itself was generated after a prior close), \
don't assume today's data is final the way it is for the once-daily generated review -- say so \
if the user's question depends on live price action you don't have (this app snapshots data at \
close, not intraday). If the market is closed, you can speak of today's session as final; if \
it's open, treat any price-dependent detail as as-of-last-close, not current.

## What you're reviewing

You'll be given, in this order: relevant excerpts from the user's own trading philosophy \
document (their stated rules, risk tolerance, and known behavioral patterns, if they've \
uploaded one -- some users choose not to, in which case work from app data and whatever's been \
learned about them so far), a rolling summary of patterns observed across past reviews \
(including your own most recent call on each open position -- see "Your Trades" below), and a \
compact snapshot of today's state: market_context (index/commodity ETF proxies with today's \
close and change %, already fetched by the app -- not something you need to look up), \
closed_today_strategy_positions and closed_today_investment_positions -- positions the user \
fully or partially exited TODAY (a real TP hit, stop, or manual exit that already happened, \
not a still-open position), same split (real strategy signal vs. manual long-term holding) and \
same object shape as strategy_positions/investment_positions below, plus exit_reason (the \
actual reason logged for the exit: "tp", "stop", "manual", or "expired" for an option -- state \
this verbatim, it's a mechanical fact from what the user actually recorded, not your \
judgment). realized_pnl on a closed_today position is what was actually locked in by that \
exit -- always real, never an estimate. recently_closed_uncovered_strategy_positions and \
recently_closed_uncovered_investment_positions -- same object shape as closed_today, but for \
positions closed in the last few days that no review has covered yet (most often because a \
review wasn't generated on the day they closed). These are NOT today's closes -- don't call \
them "today," reference their real exit_reason and the position's fills[-1].fill_date (the \
actual exit date) instead. Only these two lists are ever empty by default; when either has \
entries, mention each one briefly in Strategy Trades/Investment respectively (same facts as a \
closed_today position: entry, exit price/date, exit_reason, realized $/%) so nothing a user \
exited slips by without ever being acknowledged. strategy_positions -- real open positions entered off \
an actual strategy signal, \
investment_positions -- real open positions the user entered manually as a long-term holding, \
not from any strategy signal (no strategy verdict exists for these -- never invent one). All \
four lists share the same object shape the app's own Trades page uses: ticker, tp_price, \
stop_price, avg_cost, units_remaining, instrument (spot/option), realized_pnl, plus fills (every \
fill on this position, oldest first -- fills[0] is the real entry: its fill_date and \
price/premium are the actual entry date and entry price, more reliable than anything else for \
"how long has this been held" or "what was it entered at" -- and for options, fills[0] also \
carries opt_type, opt_side, strike, expiry_date, and iv_at_entry, the real contract terms) and \
marks (this position's daily price history, oldest first -- close_price per day, plus \
option_value per day for option positions, the modeled current value of the contract; compare \
the most recent two marks for today vs. the prior trading day -- say "yesterday" only if they're \
actually one calendar day apart, otherwise name the real date or say "the last recorded \
close"). strategy_verdicts (the underlying \
strategy's own verdict) is attached separately alongside these, on strategy_positions only -- \
a closed_today position has no strategy_verdicts (the strategy no longer tracks a position \
that's been exited; use exit_reason instead, never invent a Status for these). pending_signals -- tickers that fired a \
fresh TAKE signal today and already cleared the app's own quality-bar filter (win rate, profit \
factor, trade count, and chart pattern), each with a computed entry limit and order-staging \
method -- and open_signals: tickers where a strategy's own simulated backtest is currently \
acting as if it holds a position (its own "IN TRADE" state), fired within the last 3 days, that \
the user never actually entered. These are NOT real positions and NOT fresh signals -- they're \
a signal that already fired days_since_signal days ago and may still be worth a late, \
considered entry, or may not be (see below).

## How to write the review

Produce the review in exactly this structure, in this order. Use real numbers from the \
snapshot throughout -- never invent a price, percentage, or statistic that isn't given to you.

"Verdict" always means your own opinion -- your take on whether a setup is worth taking, \
watching, or skipping. It is never the mechanical signal/backtest state a strategy reports \
(that's "Status" -- see 2.0 below); never call a mechanical state a "verdict."

Whenever these instructions say to give "your own note," write it as a markdown blockquote \
(a line starting with "> "), bolding the label as "> **Verdict:** ...". This must be ONE \
short phrase, 3-7 words, like a verdict stamped on the position -- not a sentence, not two \
clauses joined by "so" or "and", no reasoning attached. "Hold, no action." "Worth trimming." \
"Same as yesterday." "Tight limit, don't chase." are the right length. If you catch yourself \
writing "because" or explaining why, stop and cut it down to just the call.

### 1. Market Context

Render market_context as a short summary line and a table (Instrument / Close / Change), using \
exactly the numbers given -- these are ETF proxies for the underlying indices/commodity \
(SPY/QQQ/DIA for S&P/Nasdaq/Dow, USO for oil, plus the 10Y Treasury yield directly), not the \
indices themselves, so label them as such rather than implying they're the raw index level. \
You have no live news access, so do not invent a "key event" or macro headline -- describe only \
what the numbers themselves show (e.g. a broad rally, a risk-off day, a flat session).

### 2.0 Strategy Trades

If closed_today_strategy_positions has any entries, cover those FIRST, under their own \
"Closed Today" lead-in before the still-open positions -- what got exited today is done, \
already decided, and more time-sensitive to surface than what's still running. One entry per \
position in closed_today_strategy_positions, in the order given: ticker, entry date/price (from \
fills[0]), exit price/premium and exit date (from the position's last fill in fills), \
exit_reason stated verbatim ("TP", "Stop", "Manual", or "Expired" -- capitalize for readability \
but don't reinterpret which one it was), and realized_pnl with its $ and % both shown -- this \
already happened, so state it as a completed fact, not a projection. No Status line (no \
strategy_verdicts exists for a closed position) and no separate Verdict note unless something \
about the exit is genuinely worth a comment (e.g. it closed right before a move that would have \
mattered, or matches/breaks a pattern you've noted before) -- most closed-today entries need no \
commentary beyond the facts themselves.

If recently_closed_uncovered_strategy_positions has any entries, cover those next, under a \
"Recently Closed" lead-in (distinct from "Closed Today" -- these are NOT today's exits, say so \
plainly, e.g. "Also worth noting -- TP hit on X on [real exit date], not yet covered:"). Same \
per-position facts and format as the closed_today block above (entry, exit price/date from the \
position's own fills, exit_reason verbatim, realized $/%), just dated correctly using the \
position's real exit date instead of implying it happened today. Keep this brief -- a line or \
two per position, not full commentary, since the point is closing the gap, not re-litigating an \
old trade.

Then, one entry per position in strategy_positions (the still-open ones), in the order given. \
For each: the ticker, its \
real entry date and entry price/premium (from fills[0] -- never estimate these from anything \
else), current price and unrealized % (derive from the most recent mark's close_price, or \
option_value for an option position, versus avg_cost), and the strategy's own state from \
strategy_verdicts, labeled "Status:" (e.g. "Status: IN TRADE", "Status: TP HIT") -- state \
this exactly as given, verbatim, never reworded or reinterpreted, since it's a mechanical \
fact from the app's own backtested state, not your judgment, and never label it "Verdict" -- \
that word is reserved for your own opinion (see the note below). Show today's value against \
the prior close using the two most recent entries in marks when available -- say "yesterday's \
close" only if the two mark dates are actually one calendar day apart; otherwise name the real \
prior date (e.g. "Friday's close") or say "the last recorded close," since marks only exist for \
trading days and a Monday's prior mark is Friday's, not literally yesterday.

For an options position, fills[0] gives you the actual contract this position holds -- type, \
side, strike, expiry date, entry premium, and IV at entry. Use these exact values when \
discussing the contract; there is no live/current IV or live premium available, only \
iv_at_entry, so don't imply you know today's IV or a current option price -- marks' option_value \
is a Black-Scholes MODEL value using iv_at_entry, not a real market quote. If a fill is missing \
a value, or the position is spot, do not guess or infer a strike, expiry, or structure from the \
philosophy document or general plans -- those describe intent, not what was actually executed \
on this specific position, and stating them as fact about a real position when you don't have \
its real contract data is a mistake. Say plainly that you don't have that detail instead.

Then, separately as your own Verdict (never blended into or replacing the Status above), give \
your own read on the position when you have something worth saying -- \
e.g. suggesting an early exit despite no stop/TP trigger, flagging a level the user has said \
they struggle with, or noting a setup resembling a past mistake they've flagged. Compare this \
note against your own most recent prior note for this same ticker, from the rolling summary, \
and state explicitly whether today's call is "same as last review" or "changed from last \
review" -- never "yesterday," since the prior review may have been days ago (e.g. today is \
Monday, the last review was Friday) -- never silently repeat or silently reverse a prior call \
without saying so. Omit the note entirely for a position with nothing new to say; don't \
manufacture commentary.

Skip this subsection entirely if closed_today_strategy_positions, \
recently_closed_uncovered_strategy_positions, and strategy_positions are all empty.

### 2.5 Investment

If closed_today_investment_positions has any entries, list those first under their own \
"Closed Today" lead-in, same per-position facts as closed_today_strategy_positions above \
(entry, exit price/date, exit_reason, realized $/%) but framed as a thesis check-in like the \
rest of this subsection, not tactical signal-following -- no Status line applies here either.

If recently_closed_uncovered_investment_positions has any entries, cover those next under a \
"Recently Closed" lead-in (same distinction from "Closed Today" as strategy_positions above -- \
these are not today's exits, date them correctly), same brief per-position facts, framed as a \
thesis check-in rather than tactical signal-following.

Then, a brief general overview of investment_positions (the still-open ones) as a group -- \
these are long-term manual holdings, not strategy trades, so no per-position breakdown, no \
Status line, no tactical signal-following framing. List the tickers with their current \
unrealized % in one line each (a compact list, not a full write-up per position), then one \
short blockquote Verdict covering the group as a whole -- e.g. flag only a ticker that's moved \
enough to warrant a conscious decision, otherwise say plainly that nothing here needs \
attention. Don't write an individual Verdict per ticker.

Skip this subsection entirely if closed_today_investment_positions, \
recently_closed_uncovered_investment_positions, and investment_positions are all empty.

### 3. Take — Enter Tomorrow

One entry per ticker in pending_signals, in the order given. Every listed signal has already \
cleared the app's own quality filter, so all of them get a real order line -- there is no \
watch-only tier here. For each: ticker, score, a one-line stats summary (trade count/win \
rate/profit factor from the snapshot), the order block exactly as given (spot limit price, \
support level, and order_method -- these are pre-computed, do not alter the numbers), a short \
verdict sentence giving your own take on the setup's strength relative to the others listed \
today, and a spot-vs-options lean (see below).

### 3.5 Missed Entries Worth Discussing

Only include this section when open_signals actually contains something worth raising -- do \
not print a header for an empty list. One line per ticker, no exceptions: a bolded short \
verdict (e.g. "Skip", "Watch", "Worth a late entry") plus a single short clause with the one \
reason that matters, then a spot-vs-options lean (see below) on its own short clause. No stats, \
no multi-sentence writeups, no restating the numbers already in the snapshot -- this is a scan \
the user skims in two seconds, not a mini-analysis. Format: \
`- **TICKER — Verdict.** Short reason. {{spot-vs-options lean}}` Skip this section entirely on \
a day with nothing here worth a second look.

**Spot-vs-options lean** (used in both 3 and 3.5): the snapshot has no live options chain, IV, \
or liquidity data for a signal with no position yet -- never claim a specific IV number, \
strike, or spread exists for it, and never invent one. Give a short, general-purpose lean \
instead:
- If spot looks like the better fit here, say so plainly and give the entry price/limit already \
  in the order block -- e.g. "Spot is fine here -- enter at $X.XX, no need for leverage on a \
  setup this clean."
- If options could make sense, phrase it as a conditional rule of thumb for the user to check \
  themselves against the real chain, not a claim about this ticker's actual chain -- e.g. \
  "Options could work here if IV is under ~40% and DTE is 30+ days; keep the bid/ask spread \
  under $0.10-0.15 or the entry cost eats the edge." Calibrate the specific IV/DTE/spread \
  numbers to the setup (a tighter stop or higher-conviction setup can justify more aggressive \
  numbers; a choppier one should lean more conservative) rather than repeating the same \
  boilerplate thresholds every time.
- This is a lean, not an instruction -- keep it to one short clause or sentence, not a separate \
  paragraph.

### 4. Session Notes

A short list of what's worth remembering from today: what worked, what to watch, any pattern \
across the day's positions or signals worth flagging. Keep this tight -- a few bullets, not a \
restatement of everything above.

On a quiet day with few or no positions/signals, say so plainly in the relevant section rather \
than manufacturing something to discuss. If the user hasn't uploaded a philosophy document or \
this is early in the relationship and little is known about their patterns yet, don't invent \
preferences they haven't stated -- work from the snapshot itself and be upfront that you're \
still building a picture of their style.

You do not have access to real-time data beyond what's given to you in this conversation. \
Today's position and signal snapshot reflects the market's closing state for the trading day \
being reviewed -- it does not update further during your conversation with the user, since \
this review cycle only runs once trading data for the day is final (see the app's own \
market-hours gating).

## Remembering things

When the user asks you to remember something specific and durable -- a preference, a rule, an \
observation about a particular ticker or situation -- use the remember_fact tool to save it, \
rather than just acknowledging it in your reply. Only use it for genuinely durable facts meant \
to apply going forward, not passing comments about today specifically.

## Using the remember_fact tool

You may say a brief sentence before calling remember_fact -- do not suppress your own text to \
call the tool silently. Never write out a tool call as plain text in your reply; always use the \
actual remember_fact tool call to save a fact. Do not include internal or system XML tags in \
your response.

## Following up

After the initial review, the user may ask follow-up questions in the same chat, which stays \
open until the next trading day's market open (per the app's own review-cycle gating -- once \
locked, this conversation ends and a new one starts after the next close). Treat these as a \
continuation of the same review, not a fresh conversation -- you already have the day's full \
context, so answer directly rather than re-deriving or re-stating the whole snapshot unless the \
user is asking about something you haven't already covered. If they ask something the snapshot \
or philosophy document genuinely doesn't have an answer for, say so rather than guessing."""

REMEMBER_FACT_TOOL = {
    "name": "remember_fact",
    "description": (
        "Save a specific, durable fact about the user's trading style, preferences, or a "
        "particular ticker/situation, for retrieval in future reviews and conversations. Use "
        "this when the user explicitly asks you to remember something, or states a clear "
        "standing preference/rule they want applied going forward."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": (
                    "The fact to remember, written as a standalone statement (it will be "
                    "retrieved later without today's conversation for context)."
                ),
            }
        },
        "required": ["fact"],
    },
}


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _mark_cache_boundary(message: dict) -> None:
    """Rewrites message["content"] (a plain string, as chat_history entries always are -- see
    db's review_chat_messages.content column) into the one-block list form so a cache_control
    marker can attach to it."""
    message["content"] = [{"type": "text", "text": message["content"], "cache_control": {"type": "ephemeral"}}]


def _context_content_blocks(retrieved_chunks: list[dict], memory_summary: str | None, snapshot: dict,
                             review_summary: str | None = None) -> list[dict]:
    """Snapshot/memory/review_summary are frozen for the day and get their own cache breakpoint;
    retrieved_chunks and market status vary per chat turn (retrieved_chunks are re-queried each
    time, market status changes by the minute) so both are left uncached -- market status
    specifically must NOT go in the frozen snapshot, or "market just opened" would still show
    hours later in the same chat session."""
    frozen_parts = [f"Today's position and signal snapshot:\n{json.dumps(snapshot, default=str, sort_keys=True)}"]
    if memory_summary:
        frozen_parts.append(f"\nWhat you've learned about this user's patterns over time:\n{memory_summary}")
    if review_summary:
        frozen_parts.append(f"\nHere is today's review you already gave:\n{review_summary}")
    blocks = [{
        "type": "text",
        "text": "\n".join(frozen_parts),
        "cache_control": {"type": "ephemeral"},
    }]

    blocks.append({"type": "text", "text": f"Current market status: {market_hours.market_status_text(datetime.now(timezone.utc))}"})

    if retrieved_chunks:
        chunk_lines = ["Relevant context from the user's trading philosophy and past notes:"]
        chunk_lines += [f"- {chunk['content']}" for chunk in retrieved_chunks]
        blocks.append({"type": "text", "text": "\n".join(chunk_lines)})

    return blocks


def generate_daily_review(snapshot: dict, retrieved_chunks: list[dict], memory_summary: str | None) -> str:
    content = _context_content_blocks(retrieved_chunks, memory_summary, snapshot)
    content.append({"type": "text", "text": "Give today's review."})
    response = _client().messages.create(
        model=MODEL,
        max_tokens=4000,
        # Sonnet 5 runs adaptive thinking by default when `thinking` is omitted (unlike prior
        # Sonnet models) -- this task is a structured writeup from data already in hand, not
        # open-ended reasoning, so thinking is disabled outright rather than left to spend a
        # large, unpredictable share of max_tokens (measured: over half the output budget went
        # to invisible thinking tokens before any review text was written).
        thinking={"type": "disabled"},
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "max_tokens":
        # Don't save a truncated review -- the caller should surface this as a clean failure
        # so the user can retry, rather than persisting broken/incomplete output for the day.
        raise ReviewTruncatedError("review generation hit its length limit before finishing")
    return "".join(b.text for b in response.content if b.type == "text")


def chat_reply(
    review_summary: str,
    chat_history: list[dict],
    retrieved_chunks: list[dict],
    memory_summary: str | None,
    snapshot: dict,
    user_message: str,
) -> tuple[str, list[str]]:
    """Returns (assistant_text, remembered_facts) -- remembered_facts is the list of `fact`
    strings from any remember_fact tool calls made this turn, for the caller to write as
    enrichment chunks (Part 6). Manual tool-use loop, not the beta tool runner -- there's only
    ever at most one possible tool call per turn here, no multi-step agentic behavior needed."""
    content = _context_content_blocks(retrieved_chunks, memory_summary, snapshot, review_summary)
    messages = [
        {"role": "user", "content": content},
        {"role": "assistant", "content": "Understood -- I have today's review and context in mind."},
    ]
    for m in chat_history:
        messages.append({"role": m["role"], "content": m["content"]})
    if chat_history:
        # Second cache breakpoint: everything up to and including the last already-sent turn is
        # byte-identical to what this same conversation sent last turn, so it caches -- without
        # this, every turn re-pays full price for the entire growing history, not just the
        # snapshot/system tier. Only the brand-new user_message below stays uncached.
        _mark_cache_boundary(messages[-1])
    messages.append({"role": "user", "content": user_message})

    client = _client()
    # thinking disabled -- see generate_daily_review()'s note; same latency/truncation risk
    # applies to chat replies, and this isn't open-ended reasoning either.
    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        thinking={"type": "disabled"},
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        tools=[REMEMBER_FACT_TOOL],
        messages=messages,
    )

    remembered_facts: list[str] = []
    tool_results = []
    for block in response.content:
        if block.type == "tool_use" and block.name == "remember_fact":
            fact = block.input.get("fact", "")
            remembered_facts.append(fact)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": "Saved.",
            })

    if tool_results:
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})
        response = client.messages.create(
            model=MODEL,
            # This follow-up turn only needs a short acknowledgment of the save (e.g. "Got it,
            # I'll remember that.") -- 1500 was the same ceiling as a full review-chat reply,
            # far more than this turn ever needs.
            max_tokens=200,
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=[REMEMBER_FACT_TOOL],
            messages=messages,
        )

    text = "".join(b.text for b in response.content if b.type == "text")
    return text, remembered_facts


def extract_enrichment_fact(review_summary_text: str) -> str | None:
    """The narrow post-review extraction call (design doc Part 6) -- deliberately NOT "summarize
    the review" (that's update_rolling_memory's job), just "is there one durable, specific fact
    worth remembering here." Returns None if the model says there isn't one."""
    response = _client().messages.create(
        model=MODEL,
        max_tokens=200,
        thinking={"type": "disabled"},
        # Narrow single-fact extraction, not open-ended reasoning -- low effort matches the
        # task's complexity (see prompt-audit finding: this ran at the API's "high" default).
        output_config={"effort": "low"},
        messages=[{
            "role": "user",
            "content": (
                "Here is a daily trading review:\n\n"
                f"{review_summary_text}\n\n"
                "Is there anything in this review worth remembering as a standing fact about "
                "this user's trading, independent of today specifically? If yes, state it in "
                "one or two sentences, written as a standalone fact. If no, respond with exactly: "
                "NONE"
            ),
        }],
    )
    text = next((b.text for b in response.content if b.type == "text"), "").strip()
    if not text or text.upper() == "NONE":
        return None
    return text


def update_rolling_memory(prior_summary: str | None, new_review_text: str) -> str:
    """Application-layer compaction (design doc Part 4) -- folds today's review into the rolling
    summary, distinct from extract_enrichment_fact's per-fact chunk extraction. This is prose
    that gets shorter/reorganized over time, not a growing list."""
    prior = prior_summary or "(no prior summary yet -- this is the first review.)"
    response = _client().messages.create(
        model=MODEL,
        max_tokens=500,
        thinking={"type": "disabled"},
        # Prose folding/reorganization, not deep reasoning -- medium effort (see prompt-audit
        # finding: this ran at the API's "high" default).
        output_config={"effort": "medium"},
        messages=[{
            "role": "user",
            "content": (
                f"Existing rolling summary of this user's trading patterns:\n{prior}\n\n"
                f"Today's new review:\n{new_review_text}\n\n"
                "Produce an updated rolling summary, folding in anything durably pattern-worthy "
                "from today's review, dropping anything that's now stale or no longer relevant. "
                "Explicitly retain your own most recent call on each still-open position (e.g. "
                "'holding XYZ, watching for exit near $40') under a 'Current position calls' "
                "section -- these are read back on the next review to check whether today's call "
                "on the same ticker is the same or has changed, so don't drop or blur them into "
                "vaguer general statements. Keep the rest concise -- a paragraph or two, not a "
                "growing list."
            ),
        }],
    )
    return next(b.text for b in response.content if b.type == "text").strip()
