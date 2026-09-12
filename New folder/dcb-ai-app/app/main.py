"""
FastAPI app. Two endpoints share app.pipeline.answer_question:
  - POST /api/ask       — Server-Sent Events, streams the final answer to
                           the client as it's assembled (design point #5).
  - POST /api/ask_once  — plain JSON, same pipeline, for scripts/evals
                           where SSE parsing would just add friction.
  - GET  /health         — liveness check.
  - GET  /               — the chat UI (app/static/chat.html), a static
                            page that talks to /api/ask itself; no server-
                            side templating needed since it carries no
                            per-request data at load time.

Also a small, separate login-gated dashboard (app/static/login.html +
dashboard.html), backed by app.auth's signed-cookie session and
app.dashboard_data's read-only summary queries. Deliberately NOT wired
into the chat pipeline above or into /api/ask*'s auth (there is none) --
the dashboard is additive, same as the rest of this app's history of
additive-only changes (see CLAUDE.md).

Persistent connections (design point #3): the DB connection and the LLM
client are both created once in `lifespan`, stored on `app.state`, and
reused for every request — never re-opened per request.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.auth import COOKIE_NAME, SESSION_MAX_AGE_SECONDS, create_session_token, verify_session_token
from app.config import settings
from app.dashboard_data import build_dashboard_summary
from app.db import get_db, init_db, shutdown_db
from app.llm import init_llm_client
from app.models import AskRequest, AskResponse, LoginRequest
from app.pipeline import answer_question
from app.semantic_layer import build_router_index

_CHAT_HTML_PATH = Path(__file__).resolve().parent / "static" / "chat.html"
_LOGIN_HTML_PATH = Path(__file__).resolve().parent / "static" / "login.html"
_DASHBOARD_HTML_PATH = Path(__file__).resolve().parent / "static" / "dashboard.html"

# Pure UX pacing for the typewriter/reveal effect on streamed answer text --
# purely a delivery-speed knob, not a change to how the answer is generated
# (see ask_stream below: status/sql/chart/done are never delayed, only
# answer_chunk). Tuned by feel: word-by-word at 20ms/word reads as a smooth
# type-out for a short answer and doesn't drag out a long multi-row listing
# into several seconds.
ANSWER_CHUNK_DELAY_SECONDS = 0.02


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = init_db()
    init_llm_client()
    app.state.router_index = build_router_index(db)
    yield
    shutdown_db()


app = FastAPI(title="DCB NL2SQL Chatbot", lifespan=lifespan)

# Serves app/static/* under /static (the header logos; chat.html itself is
# still served by the GET / route below, read directly as text, not through
# this mount, so that route's behavior is unchanged).
app.mount("/static", StaticFiles(directory=_CHAT_HTML_PATH.parent), name="static")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def chat_ui():
    return _CHAT_HTML_PATH.read_text(encoding="utf-8")


def _current_user(request: Request) -> str | None:
    """None means 'not logged in' -- see app.auth.verify_session_token,
    which never raises, so a missing/garbled/expired cookie all collapse
    to this same None rather than a 500."""
    return verify_session_token(request.cookies.get(COOKIE_NAME))


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return _LOGIN_HTML_PATH.read_text(encoding="utf-8")


@app.post("/api/login")
async def login(req: LoginRequest):
    # Plain equality, not hmac.compare_digest -- see app/auth.py's
    # docstring: this is a POC single-shared-login gate, not a system
    # being hardened against timing attacks on its own username/password.
    if req.username == settings.dashboard_username and req.password == settings.dashboard_password:
        response = JSONResponse({"ok": True})
        response.set_cookie(
            COOKIE_NAME,
            create_session_token(req.username),
            httponly=True,
            samesite="lax",
            max_age=SESSION_MAX_AGE_SECONDS,
        )
        return response
    return JSONResponse({"ok": False, "error": "Invalid username or password."}, status_code=401)


@app.post("/api/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/api/me")
async def me(request: Request):
    username = _current_user(request)
    if not username:
        return JSONResponse({"error": "Not authenticated."}, status_code=401)
    return {"username": username}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    if not _current_user(request):
        return RedirectResponse(url="/login")
    return _DASHBOARD_HTML_PATH.read_text(encoding="utf-8")


@app.get("/api/dashboard_summary")
async def dashboard_summary(request: Request):
    if not _current_user(request):
        return JSONResponse({"error": "Not authenticated."}, status_code=401)
    try:
        summary = build_dashboard_summary(
            get_db(), max_rows=settings.dcb_max_rows, timeout_seconds=settings.dcb_query_timeout_seconds
        )
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller, not swallowed
        return JSONResponse({"error": f"Could not build dashboard summary: {exc}"}, status_code=500)
    return summary


async def _run(question: str) -> AskResponse:
    db = get_db()
    result = await answer_question(
        question=question,
        db=db,
        router_index=app.state.router_index,
        max_rows=settings.dcb_max_rows,
        timeout_seconds=settings.dcb_query_timeout_seconds,
    )
    return AskResponse(
        question=result.question,
        answerable=result.answerable,
        sql=result.sql,
        columns=result.columns,
        rows=[list(r) for r in result.rows],
        truncated=result.truncated,
        answer=result.answer,
        clarification_message=result.clarification_message,
        entities=result.entities,
        timing_ms=result.timing_ms,
        chart_data=result.chart_data,
    )


@app.post("/api/ask_once", response_model=AskResponse)
async def ask_once(req: AskRequest):
    return await _run(req.question)


@app.post("/api/ask")
async def ask_stream(req: AskRequest):
    async def event_stream():
        yield _sse("status", {"stage": "thinking"})
        result = await _run(req.question)
        yield _sse("sql", {"sql": result.sql})
        yield _sse("chart", {"chart_data": result.chart_data.model_dump() if result.chart_data else None})
        # Chunk the already-assembled answer so the client can render it
        # progressively (see CLAUDE.md on why this isn't token-level LLM
        # streaming — Gemini's structured-output mode returns the whole
        # JSON object at once, so what we stream here is the deterministic
        # template-fill output, in small pieces, over SSE). Word-by-word,
        # paced with a small sleep between chunks, is what actually makes
        # this look/feel like typing rather than one paste -- the delay is
        # UX pacing only, applied here (not before this point), so it never
        # delays status/sql/chart, only the answer text itself.
        words = result.answer.split(" ")
        chunk = 1
        for i in range(0, len(words), chunk):
            yield _sse("answer_chunk", {"text": " ".join(words[i : i + chunk]) + " "})
            await asyncio.sleep(ANSWER_CHUNK_DELAY_SECONDS)
        yield _sse(
            "done",
            {
                "answerable": result.answerable,
                "timing_ms": result.timing_ms,
                "truncated": result.truncated,
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
