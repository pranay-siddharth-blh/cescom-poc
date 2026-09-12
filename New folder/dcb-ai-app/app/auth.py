"""
Minimal signed-cookie session for the dashboard's login gate.

Deliberately NOT a new dependency (itsdangerous, PyJWT, etc.) -- this
project's requirements.txt is kept intentionally lean (see its own
header comment), and a signed opaque cookie is all a single-shared-login
POC needs: stdlib hmac/hashlib/base64 only, no session store, no
per-user rows.

This is NOT a general-purpose auth system, and is documented as a known
POC limitation (matching this project's honest-about-limitations
convention, CLAUDE.md §7). Originally a single shared username/password;
**by explicit request, `/api/login` (app/main.py) no longer checks a
password at all -- any non-blank username is accepted.** The signed
cookie this module hands out still can't be forged or tampered with
(HMAC over `app.config.settings.session_secret`), but it no longer means
anything about who's holding it -- treat `/dashboard` as effectively
public to anyone who can reach this app's URL. No password hashing, no
rate limiting, no CSRF token, no per-user accounts. Swap for real
per-user accounts + hashed passwords before this app ever needs to keep
anyone out.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

from app.config import settings

COOKIE_NAME = "dcb_session"

# 12h -- long enough to cover a workday without logging back in, short
# enough that a stolen/lingering cookie doesn't stay valid indefinitely.
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60


def _sign(payload: str) -> str:
    digest = hmac.new(settings.session_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _b64encode(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _b64decode(encoded: str) -> str | None:
    padding = "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def create_session_token(username: str) -> str:
    """A username can't contain ':' -- checked at the one call site
    (the login route), not here, since a token that could never be
    produced with a colon-bearing username doesn't need a runtime guard
    on the encode path too; verify_session_token below is what actually
    has to be defensive, since it parses cookies it doesn't control."""
    payload = f"{username}:{int(time.time())}"
    encoded_payload = _b64encode(payload)
    return f"{encoded_payload}.{_sign(encoded_payload)}"


def verify_session_token(token: str | None) -> str | None:
    """Returns the logged-in username if the cookie is valid and not
    expired, else None. Never raises -- a missing, garbled, tampered, or
    expired cookie all just look like 'not logged in' to every caller,
    rather than a 500."""
    if not token or "." not in token:
        return None
    encoded_payload, _, signature = token.rpartition(".")
    # Verify the signature over the still-encoded payload before touching
    # base64/utf-8 decoding at all -- an attacker-supplied token is
    # rejected on the HMAC compare whether or not it's even valid base64.
    if not hmac.compare_digest(_sign(encoded_payload), signature):
        return None
    payload = _b64decode(encoded_payload)
    if payload is None:
        return None
    username, _, issued_at_str = payload.rpartition(":")
    if not username or not issued_at_str.isdigit():
        return None
    if time.time() - int(issued_at_str) > SESSION_MAX_AGE_SECONDS:
        return None
    return username
