# Daily trade review chatbot — implementation plan

Implements `2026-08-04-daily-trade-review-chatbot-design.md`. Not started;
this is the plan only, per the user's "create plan for the ai review"
request — no code written yet.

## Ordering principle

Each phase is independently testable end-to-end before the next starts —
storage → retrieval → generation → chat → enrichment → UI → flag-on. A
phase that's skipped or deferred doesn't block the ones before it from
being real, working, verifiable pieces.

---

## Phase 0 — Dependencies and scaffolding

**Goal:** the new packages install cleanly and the feature flag exists,
before any feature code is written.

- Add to `requirements.txt`: `pypdf`, `python-docx`, `sentence-transformers`,
  `anthropic`. (`numpy` already present.)
- Add `ENABLE_DAILY_REVIEW=false` to `.env` (and `.env`'s equivalent
  example/template file if one exists).
- Add `DAILY_REVIEW_ONBOARDED=false` to `.env` — the once-per-lifecycle
  onboarding gate (design doc, Part 1 "First-visit onboarding prompt").
  Manually toggled to `true` after the user uploads or explicitly skips;
  no code path ever sets this automatically for v1.
- Add `ANTHROPIC_API_KEY` to `.env` if not already present — this app has
  never called the Claude API before, confirm it isn't already configured
  for some other reason before assuming it's new.
- Add `./backend/user_docs:/app/backend/user_docs` volume to
  `docker-compose.yml`, and `backend/user_docs/` to `.gitignore`.
- Verify `sentence-transformers` actually installs and its small model
  (`all-MiniLM-L6-v2`) downloads/loads successfully in this environment
  before building anything on top of it — it's the one new dependency
  with a real risk of friction (model download on first use, disk space,
  torch as a transitive dependency). Spend 10 minutes confirming this
  works in the actual Docker build target, not just locally.

**Verify:** `pip install -r requirements.txt` succeeds; a throwaway
script embeds one string and gets back a 384-dim vector; Docker Compose
config validates with the new volume.

---

## Phase 1 — Schema

**Goal:** every table from the design exists, migrations are idempotent,
nothing else in the app is touched.

New tables in `backend/db.py`, same `CREATE TABLE IF NOT EXISTS` +
migration-function pattern already used throughout that file:

- `user_documents`
- `document_chunks` (nullable `document_id`, `source`, `source_review_id`
  — the final schema from the design doc's Part 1, not the earlier
  superseded version)
- `daily_reviews`
- `review_chat_messages`
- `review_memory_summary`

New `db.py` functions (signatures only, matching the design's Part
1/2/3/4 code sketches):

```python
def insert_user_document(user_id, filename, file_path, file_type, uploaded_at) -> int
def update_user_document_status(document_id, status) -> None
def get_active_user_document(user_id) -> dict | None
def insert_document_chunk(user_id, document_id, source, source_review_id, chunk_index, content, embedding_bytes, created_at) -> int
def get_document_chunks(user_id) -> list[dict]

def insert_daily_review(user_id, review_date, summary_text, embedding_bytes, snapshot_json, created_at) -> int
def get_daily_review(user_id, review_date) -> dict | None
def list_review_chat_messages(review_id) -> list[dict]
def insert_review_chat_message(review_id, role, content, created_at) -> int

def get_review_memory_summary(user_id) -> dict | None
def upsert_review_memory_summary(user_id, summary_text, last_updated_review_id, updated_at) -> None
```

**Verify:** every function callable against a fresh local `app_data.db`
copy (never the real one) via direct REPL calls, same style used earlier
this session to verify migrations — insert a document, insert a chunk,
insert a review, insert a chat message, read them all back. No frontend,
no API endpoints yet.

---

## Phase 2 — Document ingest (upload → parse → chunk → embed → store)

**Goal:** a working, directly-testable pipeline from raw file to searchable
chunks, before any endpoint exposes it.

New module `backend/review_ingest.py`:

```python
def extract_text(file_path: str, file_type: str) -> str
def chunk_text(text: str, target_tokens: int = 650, overlap_tokens: int = 75) -> list[str]
def embed_texts(texts: list[str]) -> list[np.ndarray]   # sentence-transformers, batched
def ingest_document(user_id: int, file_path: str, filename: str, file_type: str) -> int  # returns document_id
```

- `extract_text` dispatches on `file_type`: `pypdf`/`pdfplumber` fallback
  for `.pdf`, `python-docx` for `.docx`, plain `open().read()` for
  `.md`/`.txt`.
- `chunk_text` — simple paragraph-aware splitting with overlap, per the
  design's target sizes. No need for anything fancier at this scale.
- `embed_texts` loads the `sentence-transformers` model once at module
  import (not per call — same "load once, reuse" principle as any other
  expensive resource in this codebase) and batches the encode call.
- `ingest_document` ties it together: extract → chunk → embed → write
  `user_documents` (status `processing` → `ready`, or `failed` on any
  step's exception) → write one `document_chunks` row per chunk with
  `source='upload'`.

**Verify:** run `ingest_document` directly against a real sample PDF/
docx/md file (create small test fixtures) via REPL — confirm
`document_chunks` rows land with correct `chunk_index`, non-null
`embedding`, and `get_active_user_document` returns the right row after.
Test all three file types, not just one.

---

## Phase 3 — Retrieval

**Goal:** given a query, get back the right chunks, verified against
real embedded content from Phase 2 — not synthetic vectors.

New functions in `backend/review_ingest.py` (or a small `review_retrieval.py`
if `review_ingest.py` is getting large — judge at implementation time):

```python
def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float
def top_k_chunks(user_id: int, query_text: str, k: int = 5) -> list[dict]
```

`top_k_chunks` embeds `query_text` (one call to the same
`sentence-transformers` model), loads `get_document_chunks(user_id)`,
scores by cosine similarity, returns the top K rows (full row dicts —
`content` is what actually goes in the prompt, `source`/`created_at` are
useful for debugging/logging).

**Verify:** ingest a real multi-topic test document (e.g. one with a
clear "position sizing" section and a clear "risk tolerance" section far
apart in the text), then query with something semantically close to one
section and confirm the returned top-K actually favors the right chunk
over the other. This is the one part of the whole feature where "it
compiles" isn't enough evidence — cosine similarity over a real small
local embedding model needs an actual semantic check, not just a shape
check.

---

## Phase 4 — Snapshot builder (current app data, compact)

**Goal:** `build_daily_snapshot()` exactly as sketched in the design's
Part 2, wired to real data.

New function in `backend/app.py` (or a new `backend/review_snapshot.py` —
judge based on how large this gets; it reads from several existing
modules, might be cleaner standalone):

```python
def build_daily_snapshot(user_id: int = DEFAULT_USER_ID) -> dict
```

Reuses existing functions, no new computation:
- `db.list_positions("open")` + `_position_with_state()` (already exists)
  for each position's avg_cost/units/realized_pnl.
- The existing unrealized-%/near-TP-stop calculations already used by
  `_update_trade_marks_and_alerts()` — reuse the same math, don't
  reimplement it.
- Each held/watchlisted ticker's current `verdict` per strategy from
  `_computed` (already in memory).
- Today's rows from `db.list_notifications()` filtered to today's date.

**Verify:** call directly against the real local dev DB (with at least
one real or synthetic open position) and eyeball the returned dict —
confirm it's compact (a few hundred tokens rendered as JSON, not
kilobytes) and every field is populated correctly against what the
Trades page itself shows for the same position.

---

## Phase 5 — Claude client wrapper and system prompt

**Goal:** one well-tested wrapper around the Claude API calls this
feature needs, isolated from the rest of the app.

New module `backend/review_claude.py`:

```python
SYSTEM_PROMPT = "..."  # frozen, versioned in this file -- see design Part 5's caching table

def generate_daily_review(snapshot: dict, retrieved_chunks: list[dict], memory_summary: str | None) -> str
def chat_reply(review: dict, chat_history: list[dict], retrieved_chunks: list[dict], user_message: str) -> tuple[str, list[dict]]
    # returns (assistant_text, tool_calls_made) -- tool_calls_made feeds Phase 7's enrichment write
def extract_enrichment_fact(review_summary_text: str) -> str | None
def update_rolling_memory(prior_summary: str | None, new_review_text: str) -> str
```

- Model: `claude-sonnet-5` per the user's decision, hardcoded (not
  env-configurable for v1 — no stated need for that yet).
- `cache_control` breakpoint after the frozen `SYSTEM_PROMPT` block, per
  the design's Part 5 layering — this is the one piece of prompt-caching
  wiring that actually matters here; get the block ordering right
  (system → retrieved chunks → memory summary → snapshot → chat →
  current message) from the start rather than retrofitting later.
- `chat_reply` is the one function that needs the `remember_fact` tool
  declared (Part 6) — tool-use loop here can be the simple manual-loop
  form (not the beta tool runner) since there's only ever one possible
  tool call per turn and no multi-step agentic behavior needed.

**Verify:** call each function directly against real `ANTHROPIC_API_KEY`
with realistic (not placeholder) snapshot/chunk data assembled from
Phases 2–4, inspect the actual Claude output for sanity, and confirm
`response.usage.cache_read_input_tokens` is nonzero on a second call with
the same system prompt (proves the caching wiring is actually working,
not just present in the code).

---

## Phase 6 — Backend endpoints

**Goal:** wire Phases 1–5 into real HTTP endpoints, feature-flag-gated.

In `backend/app.py`, all gated by `if not ENABLE_DAILY_REVIEW: raise
HTTPException(404, ...)` at the top of each:

```python
GET  /api/review/onboarding-status # { onboarded: bool } -- reads DAILY_REVIEW_ONBOARDED directly from env, no DB involved
POST /api/review/document          # multipart upload -- calls ingest_document (does NOT set DAILY_REVIEW_ONBOARDED -- that's still a manual step, see Phase 0)
GET  /api/review/document          # current document status/filename, for the settings UI
GET  /api/review/status            # { can_start: bool, active_review: {...} | null } -- Part 7's gate, polled by the frontend to enable/disable the button
POST /api/review/daily             # trigger the review -- rejects (400) if review_available_to_start(now) is False
GET  /api/review/daily/{date}      # fetch a specific day's review + its chat history (read-only for locked/past reviews)
POST /api/review/daily/{date}/chat # send a follow-up message -- rejects (400) if that review's status != 'active', or if now is outside the chat-continues window (Part 7)
```

`GET /api/review/onboarding-status` is deliberately trivial — reads
`os.environ.get("DAILY_REVIEW_ONBOARDED", "false")`, no DB write, no
side effect. Nothing in this phase ever flips the var itself; per the
design doc, that's an intentional manual step outside the app.

- **`review_available_to_start(now)`** (design doc Part 7) is a small,
  directly-unit-testable function — port it into `backend/review_gating.py`
  alongside a matching `chat_still_open(review, now)` (true while
  `status == 'active'` and `now.time()` is either ≥ 4pm or < 7am — i.e.
  the window hasn't rolled past 7am since the review was created).
  Both reused by the two endpoints above AND by `GET /api/review/status`
  for the frontend's button-enable check — one source of truth, not
  duplicated logic between "can I show the button" and "will the POST
  actually succeed."
- `POST /api/review/daily`: check `review_available_to_start` first (400
  if not) → build snapshot (Phase 4) → retrieve chunks (Phase 3, query =
  rendered snapshot) → load memory summary → call `generate_daily_review`
  (Phase 5) → embed the result → insert `daily_reviews` row
  (`review_date` = today's trading date, `status='active'`) → call
  `extract_enrichment_fact` → if non-null, insert an enrichment
  `document_chunks` row (Phase 7 ties in here) → call
  `update_rolling_memory` → upsert `review_memory_summary`.
- `POST /api/review/daily/{date}/chat`: load that review → if
  `status == 'locked'`, reject (400, clear message). If `status ==
  'active'` but `chat_still_open()` is now False (past 7am, never
  explicitly locked yet — the lazy-lock case from Part 7's open
  question), insert the `role='system'` lock notice, set
  `status='locked'`, THEN reject this request too (the lock happens as a
  side effect of the access attempt, but the message that triggered it
  still doesn't get a real reply). Otherwise: retrieve chunks (query =
  the user's message) → call `chat_reply` → insert both the user's
  message and the assistant's reply as `review_chat_messages` rows → if
  `chat_reply` reports a `remember_fact` tool call was made, write that
  enrichment chunk too.

**Verify:** exercise every endpoint with `curl` against the real local
dev server, feature flag on. The time-window logic specifically needs
tests that don't depend on actually waiting for real clock time —
`review_available_to_start`/`chat_still_open` take `now` as a parameter
exactly so this is possible; write direct unit-style checks (REPL is
fine, matching this session's established pattern) for: market open →
False; market closed but fetch stale → False; market closed and fetch
fresh, 4pm–11:59pm → True; same but 2am → False (new-trigger case); an
active review at 6:59am → chat still open; the same review at 7:00am
exactly → chat closed. Also confirm a real trigger doesn't regenerate a
second time same trading date, and confirm a `remember_fact` request
actually persists a retrievable chunk (verify via Phase 3's
`top_k_chunks` directly, not just "the API returned 200").

---

## Phase 7 — Enrichment write paths

Already threaded through Phase 6 above (`extract_enrichment_fact`,
`remember_fact` tool call) — called out as its own phase here only
because it's worth a **dedicated verification pass**, not because it's
separate code: confirm both write paths (automatic post-review, and
user-triggered mid-chat) actually produce chunks that Phase 3's retrieval
picks up in a *later, independent* request — i.e. the loop actually
closes (enrichment written today is retrievable tomorrow), not just that
the insert succeeds.

**Verify:** trigger a review that should produce an auto-enrichment fact
(craft a snapshot with something notably repeatable, e.g. a position
near stop for several days running), confirm the fact lands in
`document_chunks`; separately, in a chat, explicitly ask the bot to
remember something distinctive, confirm the tool call fires and the fact
lands; then in a **new, separate** request, query for something related
to either fact and confirm `top_k_chunks` surfaces it.

---

## Phase 8 — Frontend

**Goal:** the button, the review display, the chat UI — feature-flag
gated the same way other flags are checked in this codebase (need to
confirm the actual pattern; none of `.env`'s existing `SHOW_*` vars are
wired to anything today per earlier investigation this session, so this
may be the first real feature-flag wiring in the app — check whether
flags are meant to be read via `/api/meta` or a dedicated config
endpoint before inventing a new mechanism).

- New dedicated page, `AnalyzerPage` (per the design doc's "Analyzer"
  naming) — a real route in `router.tsx`, not a modal off an existing
  page. Nav entry in `AppShell`, hidden entirely when the flag is off
  (checked via whatever the resolved flag-delivery mechanism turns out
  to be — see the flag-plumbing note below).
- **First-visit onboarding prompt**: on page load, check
  `GET /api/review/onboarding-status`. If `onboarded: false`, show a
  blocking prompt (not the normal page content) offering two paths:
  upload the philosophy document now (file picker, `.pdf`/`.docx`/`.md`/
  `.txt` accept filter, calls `POST /api/review/document`), or "start
  clean" (no document). **Neither button flips the env var itself** —
  per the design, that's a manual step. After either choice, show a
  simple confirmation state ("Uploaded — ask your operator to set
  `DAILY_REVIEW_ONBOARDED=true` and restart" / "Got it — ask your
  operator to set `DAILY_REVIEW_ONBOARDED=true` and restart") rather
  than silently proceeding as if onboarding is complete, since it isn't
  yet from the app's own point of view until that var actually flips.
- Document upload UI (post-onboarding, if the user wants to check status
  later): shows current filename/status from `GET /api/review/document`.
  Given the user's "upload once" decision, this can be genuinely simple —
  no version list, no replace-confirmation flow beyond maybe a basic
  "already uploaded — replace?" guard so a re-upload isn't accidental.
- **Button state**: polls (or fetches on page load/focus)
  `GET /api/review/status` and renders per Part 7's table — disabled
  7am–4pm and during the post-close-fetch-pending window, enabled once
  `can_start` is true, and if `active_review` is non-null the page shows
  that review + its chat instead of the trigger button (there's only
  ever one active cycle at a time, per "there will always be one chat
  context").
- Review trigger + display: calls `POST /api/review/daily`, shows
  `summary_text` (likely markdown-rendered — check what rendering
  approach, if any, the app already uses elsewhere before adding a new
  markdown library).
- Chat thread UI: message list (`review_chat_messages`, including
  `role='system'` lock-notice rows rendered distinctly from
  `'assistant'`) + input, calls `POST /api/review/daily/{date}/chat` per
  message, appends both sides of the exchange. Input is disabled (not
  just erroring on submit) once the loaded review's `status` is
  `'locked'`, or once local time crosses 7am while the page is open —
  the frontend can compute `chat_still_open` client-side for
  responsiveness (grey out immediately at 7:00am without waiting for a
  failed request), but the backend's own check (Phase 6) is what's
  actually authoritative.
- Past-days navigation: simple date picker or list of past
  `daily_reviews` dates, each opening that day's fixed review+chat via
  `GET /api/review/daily/{date}` — always **read-only** for any review
  whose `status` is `'locked'` (which, per Part 7, is every past cycle by
  definition — only the current cycle, if any, can ever be `'active'`).

**Verify:** Playwright, same pattern used throughout this session — flag
on, walk through upload → trigger review (only when actually inside the
enabled window — may need to mock/override `now` server-side for a
repeatable test rather than waiting for real 4pm) → see summary → send a
chat message → see reply → navigate to a past day → confirm read-only.
Flag off — confirm the page/nav entry is genuinely absent, and confirm
hitting the API endpoints directly still 404s (per Phase 6's guard).

---

## Explicitly out of scope for this plan (per the design doc)

- Automatic/scheduled daily review generation (button-only, confirmed).
- Multi-user auth (the `user_id`/`DEFAULT_USER_ID` scaffolding is in
  place per the design, but no login flow — this plan doesn't touch
  auth).
- Document version-management UI.
- Rolling memory summary version history.
- Compaction of an individual day's chat thread (Part 5's fallback,
  only relevant if a single day's conversation gets unusually long —
  not built proactively, revisit if it actually comes up).

## Resolved since the first draft of this plan

The past-day-chat question this plan originally flagged as open is now
answered by the design doc's Part 7: exactly one review cycle is ever
`'active'` at a time (4pm trading-date-close → next 7am), every other
review is permanently `'locked'` the moment its own cycle ends — so
"can you chat on a past day" is simply "no, `status != 'active'`
rejects it," not a separate policy decision layered on top.
