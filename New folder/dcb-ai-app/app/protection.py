"""
Server-level guardrails (plain ASGI middleware, so the SSE answer stream is never buffered; pattern ported from the
KPCL POC):

  * security headers on every response: no MIME sniffing, no framing (clickjacking), no referrer leakage, and a
    content-security policy that allows only this server plus cdnjs (Chart.js). Scripts come only from files --
    no inline scripts or inline event handlers are allowed; inline styles are;
  * request-size cap: a body larger than DCB_MAX_REQUEST_BYTES is refused before it is read (413);
  * rate limit: at most DCB_RATE_LIMIT_PER_MINUTE questions / data / report calls per client IP per rolling
    minute (429).

Voice mode (the hands-free voice agent in AI mode, ported from RDPR):
  * the microphone is allowed for this site's own pages (permissions-policy microphone=(self)); the browser still asks
    the user (Allow / Block) the first time voice starts;
  * script-src adds 'wasm-unsafe-eval' so the in-browser speech detector (Silero VAD on ONNX Runtime Web, served from
    static/vendor/vad) can compile its WebAssembly; nothing else is relaxed;
  * /api/voice/transcribe accepts one recorded question (up to VOICE_MAX_AUDIO_BYTES); every other body keeps the cap;
  * /api/voice/* calls (several per spoken turn) have their own per-IP budget, VOICE_RATE_LIMIT_PER_MINUTE.

The concurrency cap (how many questions are answered at the same time) is in app.main._run.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict, deque

from app.config import settings
from app.guardrails import REFUSALS

CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline'; "
       "font-src 'self' data:; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'self'; form-action 'self'; object-src 'none'")
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(self), geolocation=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"content-security-policy", CSP.encode()),
]
LIMITED_PREFIXES = ("/api/ask", "/api/dashboard", "/api/insights", "/api/reports", "/api/mis-report",
                    "/api/answer-report", "/api/login", "/api/tender")
# Tender pages: a draft's form (specifications, items, terms) can pass the general cap, and the analyser takes a PDF.
TENDER_FORM_BYTES = 262144
_VOICE_PREFIX = "/api/voice/"
_AUDIO_UPLOAD = "/api/voice/transcribe"


def body_cap(path: str, default: int) -> int:
    if path == _AUDIO_UPLOAD:
        return settings.voice_max_audio_bytes
    if path.startswith("/api/tender/analyse"):
        return settings.dcb_max_upload_bytes
    if path.startswith("/api/tender"):
        return max(default, TENDER_FORM_BYTES)
    return default


class Protection:
    def __init__(self, app, per_minute: int | None = None, max_bytes: int | None = None):
        self.app = app
        self.per_minute = per_minute if per_minute is not None else settings.dcb_rate_limit_per_minute
        self.max_bytes = max_bytes if max_bytes is not None else settings.dcb_max_request_bytes
        self.hits: dict[str, deque] = defaultdict(deque)
        self.voice_hits: dict[str, deque] = defaultdict(deque)

    async def _reply(self, send, status: int, key: str):
        body = json.dumps({"detail": REFUSALS[key]}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                *SECURITY_HEADERS] + ([(b"retry-after", b"60")] if status == 429 else [])})
        await send({"type": "http.response.body", "body": body})

    def _over_limit(self, client: str, voice: bool = False) -> bool:
        hits = self.voice_hits if voice else self.hits
        limit = settings.voice_rate_limit_per_minute if voice else self.per_minute
        now = time.monotonic()
        q = hits[client]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        if len(hits) > 10000:               # forget idle clients so the table cannot grow without bound
            for k in [k for k, v in hits.items() if not v]:
                del hits[k]
        return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length")
        if length and length.isdigit() and int(length) > body_cap(path, self.max_bytes):
            return await self._reply(send, 413, "too_long")
        if path.startswith(LIMITED_PREFIXES) or path.startswith(_VOICE_PREFIX):
            client = (scope.get("client") or ("unknown", 0))[0]
            if self._over_limit(client, voice=path.startswith(_VOICE_PREFIX)):
                return await self._reply(send, 429, "rate_limited")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = list(message.get("headers") or []) + SECURITY_HEADERS
            await send(message)

        return await self.app(scope, receive, send_with_headers)
