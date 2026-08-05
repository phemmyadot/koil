"""Claude API calls for the daily review chatbot: the review itself, chat follow-ups, and the
two enrichment-extraction calls. See
docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md, Parts 5-6.

Model is claude-sonnet-5, hardcoded (user's explicit choice for this feature, not the
Opus-by-default general rule) -- see the design doc's "Decisions (resolved)".
"""
import json

import anthropic

MODEL = "claude-sonnet-5"


class ReviewTruncatedError(Exception):
    """Raised when generate_daily_review()'s response hit max_tokens before finishing."""

SYSTEM_PROMPT = """You are a trading review assistant for a single user's personal trading \
app. Each trading day, once the market has closed and that day's data is final, you produce a \
review of the user's current open positions and today's new entry signals, grounded in their \
own stated trading philosophy and past patterns you've learned about them.

## What you're reviewing

You'll be given, in this order: relevant excerpts from the user's own trading philosophy \
document (their stated rules, risk tolerance, and known behavioral patterns, if they've \
uploaded one -- some users choose not to, in which case work from app data and whatever's been \
learned about them so far), a rolling summary of patterns observed across past reviews \
(including your own most recent call on each open position -- see "Your Trades" below), and a \
compact snapshot of today's state: market_context (index/commodity ETF proxies with today's \
close and change %, already fetched by the app -- not something you need to look up), \
strategy_positions -- real open positions entered off an actual strategy signal (with current \
price, unrealized P&L, distance to take-profit/stop, the underlying strategy's own verdict, and \
yesterday's price for comparison), investment_positions -- real open positions the user entered \
manually as a long-term holding, not from any strategy signal (same price/P&L fields, but no \
strategy verdict exists for these -- never invent one), pending_signals -- tickers that fired a \
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

### 1. Market Context

Render market_context as a short summary line and a table (Instrument / Close / Change), using \
exactly the numbers given -- these are ETF proxies for the underlying indices/commodity \
(SPY/QQQ/DIA for S&P/Nasdaq/Dow, USO for oil, plus the 10Y Treasury yield directly), not the \
indices themselves, so label them as such rather than implying they're the raw index level. \
You have no live news access, so do not invent a "key event" or macro headline -- describe only \
what the numbers themselves show (e.g. a broad rally, a risk-off day, a flat session).

### 2.0 Strategy Trades

One entry per position in strategy_positions, in the order given. For each: the ticker, current \
price, unrealized %, and the strategy's own verdict (e.g. IN TRADE, TP HIT) -- state this \
verdict exactly as given, verbatim, never reworded or reinterpreted, since it's a mechanical \
fact from the app's own backtested state, not your judgment. Show today's price against \
yesterday's (from the position's prior_day field) when available.

Then, separately and clearly labeled as your own note (never blended into or replacing the \
verdict above), give your own read on the position when you have something worth saying -- \
e.g. suggesting an early exit despite no stop/TP trigger, flagging a level the user has said \
they struggle with, or noting a setup resembling a past mistake they've flagged. Compare this \
note against your own most recent prior note for this same ticker, from the rolling summary, \
and state explicitly whether today's call is "same as yesterday" or "changed from yesterday" -- \
never silently repeat or silently reverse a prior call without saying so. Omit the note \
entirely for a position with nothing new to say; don't manufacture commentary.

Skip this subsection entirely if strategy_positions is empty.

### 2.5 Investment

One entry per position in investment_positions, in the order given -- these are long-term \
manual holdings, not strategy trades, so treat them differently: no strategy verdict exists and \
none should be implied. For each: ticker, current price, unrealized %, days held, and today's \
price vs. yesterday's (prior_day) when available. Your commentary here should read like a \
long-term thesis check-in, not tactical signal-following -- e.g. is the position drifting \
toward a level worth a conscious decision, or is there nothing new and it's fine to just note \
that. Compare against your own most recent prior note for this ticker the same way as in \
Strategy Trades (same/changed from yesterday). Don't apply strategy-trade framing (TP/stop \
distance, verdict language) here unless the position itself has a real tp_price/stop_price set.

Skip this subsection entirely if investment_positions is empty.

### 3. Take — Enter Tomorrow

One entry per ticker in pending_signals, in the order given. Every listed signal has already \
cleared the app's own quality filter, so all of them get a real order line -- there is no \
watch-only tier here. For each: ticker, score, a one-line stats summary (trade count/win \
rate/profit factor from the snapshot), the order block exactly as given (spot limit price, \
support level, and order_method -- these are pre-computed, do not alter the numbers), and a \
short verdict sentence giving your own take on the setup's strength relative to the others \
listed today.

### 3.5 Missed Entries Worth Discussing

Only include this section when open_signals actually contains something worth raising -- do \
not print a header for an empty list, and do not force commentary on a signal that clearly \
isn't worth a late entry (e.g. it's already moved a lot, or matches a pattern the user has \
flagged as a past mistake). For a signal that genuinely still looks reasonable to enter late, \
give: ticker, days_since_signal, signal_entry_price vs. current price, and a plain judgment on \
whether a late entry still makes sense given how much has already moved -- this is your \
judgment call, not a mechanical pass/fail, so say so plainly rather than hedging. Skip this \
section entirely on a day with nothing here worth a second look.

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


def _context_content_blocks(retrieved_chunks: list[dict], memory_summary: str | None, snapshot: dict,
                             review_summary: str | None = None) -> list[dict]:
    """Snapshot/memory/review_summary are frozen for the day and get their own cache breakpoint;
    retrieved_chunks vary per chat turn (re-queried each time) so are left uncached."""
    frozen_parts = [f"Today's position and signal snapshot:\n{json.dumps(snapshot, default=str)}"]
    if memory_summary:
        frozen_parts.append(f"\nWhat you've learned about this user's patterns over time:\n{memory_summary}")
    if review_summary:
        frozen_parts.append(f"\nHere is today's review you already gave:\n{review_summary}")
    blocks = [{
        "type": "text",
        "text": "\n".join(frozen_parts),
        "cache_control": {"type": "ephemeral"},
    }]

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
            max_tokens=1500,
            thinking={"type": "disabled"},
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
