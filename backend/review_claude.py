"""Claude API calls for the daily review chatbot: the review itself, chat follow-ups, and the
two enrichment-extraction calls. See
docs/superpowers/specs/2026-08-04-daily-trade-review-chatbot-design.md, Parts 5-6.

Model is claude-sonnet-5, hardcoded (user's explicit choice for this feature, not the
Opus-by-default general rule) -- see the design doc's "Decisions (resolved)".
"""
import json

import anthropic

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You are a trading review assistant for a single user's personal trading \
app. Each trading day, once the market has closed and that day's data is final, you produce a \
review of the user's current open positions and the state of the strategies they follow, \
grounded in their own stated trading philosophy and past patterns you've learned about them.

## What you're reviewing

You'll be given, in this order: relevant excerpts from the user's own trading philosophy \
document (their stated rules, risk tolerance, and known behavioral patterns, if they've \
uploaded one -- some users choose not to, in which case work from app data and whatever's been \
learned about them so far), a rolling summary of patterns observed across past reviews, and a \
compact snapshot of today's actual open positions with current prices, unrealized P&L, distance \
to take-profit/stop, and the underlying strategy screener's own verdict on each held ticker \
(NO SIGNAL / PENDING / OPEN, from the app's own VEXH/VCP/VCPO strategies -- this is the \
strategy's simulated backtested state, not investment advice, context for you to reference \
alongside the user's real position).

## How to write the review

Be direct and specific. Reference actual positions, actual numbers, and actual stated \
preferences from the user's philosophy document when they're relevant -- don't give generic \
trading advice that could apply to anyone or any portfolio. If something in the current state \
conflicts with a stated rule or preference (e.g. a position near a level they've said they \
struggle with, or a setup that resembles a pattern they've flagged as a past mistake), say so \
plainly rather than hedging around it. If the user hasn't uploaded a philosophy document or \
this is early in the relationship and little is known about their patterns yet, don't invent \
preferences they haven't stated -- work from the position data itself and be upfront that you're \
still building a picture of their style.

Keep the review focused and scannable -- a short summary of what changed and what deserves \
attention, not an exhaustive restatement of every field in the snapshot. On a quiet day with no \
positions near a decision point, say so plainly rather than manufacturing something to discuss.

You do not have access to real-time data beyond what's given to you in this conversation. \
Today's position snapshot reflects the market's closing state for the trading day being \
reviewed -- it does not update further during your conversation with the user, since this \
review cycle only runs once trading data for the day is final (see the app's own market-hours \
gating).

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


def _context_blocks(retrieved_chunks: list[dict], memory_summary: str | None, snapshot: dict) -> str:
    """Renders the retrieved-chunks / memory-summary / snapshot layers (design doc Part 5) as one
    text block, placed in the user turn after the cached system prompt -- these three are NOT
    individually cache_control'd (design doc's own table: retrieved chunks and snapshot change
    every request, memory summary is small enough not to bother)."""
    parts = []
    if retrieved_chunks:
        parts.append("Relevant context from the user's trading philosophy and past notes:")
        for chunk in retrieved_chunks:
            parts.append(f"- {chunk['content']}")
    if memory_summary:
        parts.append(f"\nWhat you've learned about this user's patterns over time:\n{memory_summary}")
    parts.append(f"\nToday's position snapshot:\n{json.dumps(snapshot, default=str)}")
    return "\n".join(parts)


def generate_daily_review(snapshot: dict, retrieved_chunks: list[dict], memory_summary: str | None) -> str:
    context = _context_blocks(retrieved_chunks, memory_summary, snapshot)
    response = _client().messages.create(
        model=MODEL,
        max_tokens=2000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{
            "role": "user",
            "content": f"{context}\n\nGive today's review.",
        }],
    )
    return next(b.text for b in response.content if b.type == "text")


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
    context = _context_blocks(retrieved_chunks, memory_summary, snapshot)
    messages = [
        {
            "role": "user",
            "content": f"{context}\n\nHere is today's review you already gave:\n{review_summary}",
        },
        {"role": "assistant", "content": "Understood -- I have today's review and context in mind."},
    ]
    for m in chat_history:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_message})

    client = _client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
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
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=[REMEMBER_FACT_TOOL],
            messages=messages,
        )

    text = next((b.text for b in response.content if b.type == "text"), "")
    return text, remembered_facts


def extract_enrichment_fact(review_summary_text: str) -> str | None:
    """The narrow post-review extraction call (design doc Part 6) -- deliberately NOT "summarize
    the review" (that's update_rolling_memory's job), just "is there one durable, specific fact
    worth remembering here." Returns None if the model says there isn't one."""
    response = _client().messages.create(
        model=MODEL,
        max_tokens=200,
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
        messages=[{
            "role": "user",
            "content": (
                f"Existing rolling summary of this user's trading patterns:\n{prior}\n\n"
                f"Today's new review:\n{new_review_text}\n\n"
                "Produce an updated rolling summary, folding in anything durably pattern-worthy "
                "from today's review, dropping anything that's now stale or no longer relevant. "
                "Keep it concise -- a paragraph or two, not a growing list."
            ),
        }],
    )
    return next(b.text for b in response.content if b.type == "text").strip()
