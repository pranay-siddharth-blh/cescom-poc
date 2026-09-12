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

Persistent connections (design point #3): the DB connection and the LLM
client are both created once in `lifespan`, stored on `app.state`, and
reused for every request — never re-opened per request.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.db import get_db, init_db, shutdown_db
from app.llm import init_llm_client
from app.models import AskRequest, AskResponse
from app.pipeline import answer_question
from app.semantic_layer import build_router_index
from app.config import settings

_CHAT_HTML_PATH = Path(__file__).resolve().parent / "static" / "chat.html"


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
        # template-fill output, in small pieces, over SSE).
        words = result.answer.split(" ")
        chunk = 6
        for i in range(0, len(words), chunk):
            yield _sse("answer_chunk", {"text": " ".join(words[i : i + chunk]) + " "})
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
