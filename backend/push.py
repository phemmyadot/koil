"""Web Push send path. Sends a VAPID-signed push payload to every stored subscription --
see docs/superpowers/specs/2026-07-31-pwa-push-design.md. Triggered from app.py's alert engine,
right after it inserts a notifications row, so in-app and push notifications always agree.

VAPID_PRIVATE_KEY is stored in .env with literal \\n escapes (single-line, since the project's
hand-rolled .env loader in build_universe.py is line-based and doesn't support multi-line
values) -- unescaped back into a real PEM here before handing it to pywebpush.
"""
import os

from pywebpush import WebPushException, webpush

import backend.db as db


def _private_key_pem() -> str | None:
    raw = os.environ.get("VAPID_PRIVATE_KEY")
    return raw.replace("\\n", "\n") if raw else None


def vapid_configured() -> bool:
    return bool(os.environ.get("VAPID_PUBLIC_KEY") and _private_key_pem() and os.environ.get("VAPID_SUBJECT"))


def send_push_to_all(payload_json: str, now_iso: str) -> None:
    """Best-effort fan-out -- one subscription failing (expired, revoked) never blocks the
    others or the caller (the alert engine, which must not fail just because push delivery
    had a problem). Silently no-ops if VAPID isn't configured yet."""
    if not vapid_configured():
        return
    private_key = _private_key_pem()
    claims = {"sub": os.environ["VAPID_SUBJECT"]}
    for sub in db.list_push_subscriptions():
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=payload_json,
                vapid_private_key=private_key,
                vapid_claims=dict(claims),
            )
            db.touch_push_subscription(sub["endpoint"], now_iso)
        except WebPushException as e:
            status = e.response.status_code if e.response is not None else None
            if status in (404, 410):
                # Push service says this endpoint is gone for good -- stop trying it.
                db.delete_push_subscription(sub["endpoint"])
            # Any other failure (timeout, 5xx from the push service) is left in place to retry
            # on the next alert -- not this subscription's fault, no reason to drop it.
        except Exception:
            # Network errors etc. -- same "don't let one bad subscription break the loop" rule.
            pass
