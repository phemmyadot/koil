# Bugs

All fixed and verified live (real API calls, real browser via Playwright):

- [x] "no signal" alert on an exit was indistinguishable from a ticker that never had a signal.
  Now says "EXIT" when the prior state was OPEN, and "NO SIGNAL (pending signal expired)" when
  the prior state was PENDING -- two different events, two different messages.
- [x] Chat message not appearing until refresh. useSendReviewChatMessage now optimistically
  appends the user's message to the cache on send, instead of waiting for the full round-trip
  to invalidate/refetch.
- [x] Chat message area overflow/width. Added `overflow-wrap: anywhere` on message text, and
  discovered the underlying cause was deeper: LightMarkdown had no table support at all, so
  every `| cell | cell |` row rendered as its own broken paragraph. Added real `<table>`
  rendering with horizontal scroll on its own container.
- [x] Partial/cut-off review summary. `generate_daily_review()`'s `max_tokens` was too low
  (2000) for a review covering multiple positions and signals with per-item commentary; raised
  to 4000, plus a visible note appended if a response ever does hit the limit again.
- [x] No thinking animation while the chatbot is processing. Added an animated three-dot
  indicator bubble shown while a chat reply is pending, respecting `prefers-reduced-motion`.
