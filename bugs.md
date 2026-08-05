# Bugs

- [x] Review generation was slow (45s+) and sometimes hit its length limit mid-review, leaving
  a stuck "cut off" note permanently saved with no way to regenerate. Root cause: Claude Sonnet 5
  runs adaptive thinking by default when `thinking` isn't set (unlike prior Sonnet models) --
  over half the output budget was going to invisible thinking tokens before any review text was
  written. Fixed by explicitly disabling thinking on all review_claude.py calls (this is
  structured writeup from data already in hand, not open-ended reasoning) -- cut real generation
  time from 48s to ~18-21s. Also stopped saving a truncated review at all: generate_daily_review()
  now raises ReviewTruncatedError instead of appending a note, and the trigger endpoint returns a
  502 so the user can just retry rather than being stuck with broken output for the day.
