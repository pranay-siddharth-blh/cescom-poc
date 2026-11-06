"""
FastAPI app. Two endpoints share app.pipeline.answer_question:
  - POST /api/ask       — Server-Sent Events, streams the final answer to
                           the client as it's assembled (design point #5).
  - POST /api/ask_once  — plain JSON, same pipeline, for scripts/evals
                           where SSE parsing would just add friction.
  - GET  /health         — liveness check.
  - GET  /chat           — the chat UI (app/static/chat.html), a static
                            page that talks to /api/ask itself; no server-
                            side templating needed since it carries no
                            per-request data at load time.

Also a small, separate login-gated dashboard (app/static/login.html +
dashboard.html), backed by app.auth's signed-cookie session and
app.dashboard_data's read-only summary queries. The chat pipeline itself
(/api/ask*) is still NOT gated by this login -- by design, kept
additive/separate (see CLAUDE.md) -- only the landing flow changed: by
request, GET / is now the entry point into the login+dashboard flow
(login page first if not signed in, straight to the dashboard if
already signed in) rather than straight to the chat UI. The chat UI
moved to its own GET /chat path so it's still reachable, just no longer
what opens by default.

Persistent connections (design point #3): the DB connection and the LLM
client are both created once in `lifespan`, stored on `app.state`, and
reused for every request — never re-opened per request.

Added in CLAUDE.md section 12 (ported from the KPCL POC):
  - app.protection middleware: security headers + CSP on every response,
    a request-size cap (413) and a per-IP rate limit (429);
  - at most DCB_MAX_CONCURRENT_QUESTIONS questions are answered at once,
    the rest wait up to DCB_QUEUE_WAIT_SECONDS and then get "busy";
  - SQL, routes, timings, entities and error details are sent only when
    DCB_SHOW_TECHNICAL_DETAILS=true (and /docs exists only then); every
    unexpected error is a fixed message, never exception text;
  - /api/ask streams one `status` event per real pipeline stage as it
    starts, then the answer, the chart, the table, and `done` with a
    report_id when the answer can be downloaded as Excel;
  - GET /api/mis-report (dashboard selection -> Excel MIS workbook, login-
    gated like the dashboard) and GET /api/answer-report/{id} (a chat
    answer -> Excel); a workbook failing its own checks is a 409.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import answer_report, followup, mart_layer, premise, small_talk, spoken, tariff_booklet, tender, voice, voice_guard
from app.excel_common import ReportUnderReview, data_version
from app.guardrails import REFUSALS
from app.mis_report import NotFound, build_mis_report
from app.protection import Protection

from app.auth import COOKIE_NAME, SESSION_MAX_AGE_SECONDS, create_session_token, verify_session_token
from app.config import settings
from app.dashboard_data import build_dashboard_summary, build_subdivision_dashboard_summary
from app.db import get_db, init_db, shutdown_db
from app.insights_data import build_insights_summary
from app.llm import init_llm_client, shutdown_llm_client
from app.models import AskRequest, AskResponse, LoginRequest
from app.pipeline import PipelineResult, answer_question
from app.reports_data import get_entity_series, get_reports_options
from app.semantic_layer import build_router_index, load_semantic_layer
from app.xlsx_view import workbook_view

_CHAT_HTML_PATH = Path(__file__).resolve().parent / "static" / "chat.html"
_LOGIN_HTML_PATH = Path(__file__).resolve().parent / "static" / "login.html"
_DASHBOARD_HTML_PATH = Path(__file__).resolve().parent / "static" / "dashboard.html"
_INSIGHTS_HTML_PATH = Path(__file__).resolve().parent / "static" / "insights.html"
_REPORTS_HTML_PATH = Path(__file__).resolve().parent / "static" / "reports.html"
_ABOUT_HTML_PATH = Path(__file__).resolve().parent / "static" / "about.html"

# Pure UX pacing for the typewriter/reveal effect on streamed answer text --
# purely a delivery-speed knob, not a change to how the answer is generated
# (see ask_stream below: status/sql/chart/done are never delayed, only
# answer_chunk). Tuned by feel: word-by-word at 20ms/word reads as a smooth
# type-out for a short answer and doesn't drag out a long multi-row listing
# into several seconds.
ANSWER_CHUNK_DELAY_SECONDS = 0.02
_TECH = settings.dcb_show_technical_details
_log = logging.getLogger(__name__)
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = init_db()
    if init_llm_client() is None:
        _log.warning("LLM gateway not configured: running without the model (deterministic routes only)")
    app.state.router_index = await build_router_index(db)
    load_semantic_layer()
    mart_layer.load()
    voice.init_voice_client()
    try:
        voice_guard.lexicon()                      # validate voice_lexicon.json once, at startup (warnings logged)
    except Exception:  # noqa: BLE001 -- voice mode is optional; the app runs without it
        _log.exception("voice lexicon could not be loaded")
    warm_voice = asyncio.create_task(_warm_tts_cache())
    yield
    warm_voice.cancel()
    shutdown_db()
    await shutdown_llm_client()
    await voice.shutdown_voice_client()


app = FastAPI(title="DCB NL2SQL Chatbot", lifespan=lifespan, docs_url="/docs" if _TECH else None, redoc_url=None,
              openapi_url="/openapi.json" if _TECH else None)
app.add_middleware(Protection)
# At most this many questions are answered at once (each may hold one call to the shared gateway); the rest wait up
# to DCB_QUEUE_WAIT_SECONDS, then get a polite "busy" answer instead of piling up.
_SLOTS = asyncio.Semaphore(settings.dcb_max_concurrent_questions)

UNDER_REVIEW = ("This report is under review: one of its figures did not pass the report's own integrity check, so it "
                "is not being handed out. The check has been logged. The answer on screen is unaffected.")


@app.exception_handler(RequestValidationError)
async def _bad_request(request: Request, exc: RequestValidationError):
    if _TECH:
        return JSONResponse(status_code=422, content={"detail": exc.errors()})
    too_long = any("too_long" in str(e.get("type", "")) for e in exc.errors())
    return JSONResponse(status_code=400, content={"detail": REFUSALS["too_long" if too_long else "empty"]})


@app.exception_handler(ReportUnderReview)
async def _under_review(request: Request, exc: ReportUnderReview):
    """A report whose own integrity checks failed is never downloaded."""
    _log.error("report withheld on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=409, content={"detail": "under_review", "message": UNDER_REVIEW},
                        headers={"Cache-Control": "no-store"})


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    _log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": REFUSALS["error"]})

# Serves app/static/* under /static (the header logos; chat.html/login.html/
# dashboard.html themselves are served by their own routes below, read
# directly as text, not through this mount, so those routes' behavior is
# unchanged).
app.mount("/static", StaticFiles(directory=_CHAT_HTML_PATH.parent), name="static")


@app.get("/health")
async def health():
    period = mart_layer.load()["data_period"]
    return {"status": "ok", "data_from": period["first_fiscal_year"], "data_to": period["latest_fiscal_year"],
            "llm_configured": settings.llm_configured, "data_version": data_version(), "technical": _TECH,
            "voice": {"speech_in": settings.asr_configured, "speech_out": settings.tts_configured}}


def _current_user(request: Request) -> str | None:
    """None means 'not logged in' -- see app.auth.verify_session_token,
    which never raises, so a missing/garbled/expired cookie all collapse
    to this same None rather than a 500."""
    return verify_session_token(request.cookies.get(COOKIE_NAME))


@app.get("/")
async def root(request: Request):
    """The app's entry point, by request: login page first if not signed
    in, straight to the dashboard if already signed in -- never straight
    to the chat UI anymore (that's GET /chat now)."""
    if not _current_user(request):
        return RedirectResponse(url="/login")
    return RedirectResponse(url="/dashboard")


@app.get("/chat", response_class=HTMLResponse)
async def chat_ui():
    return _CHAT_HTML_PATH.read_text(encoding="utf-8")


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return _LOGIN_HTML_PATH.read_text(encoding="utf-8")


@app.post("/api/login")
async def login(req: LoginRequest):
    # No credential check at all -- any non-blank username is accepted,
    # password isn't checked. Per the project owner's explicit request,
    # this is deliberately an open gate now, not a login: see
    # app/auth.py's docstring for why that's fine for this POC (a signed
    # session cookie still exists so /dashboard isn't wide open to
    # anyone with no cookie at all, but anyone who visits /login can get
    # one). Only requirement is a non-blank username, since it becomes
    # the session's display name.
    username = req.username.strip()
    if not username:
        return JSONResponse({"ok": False, "error": "Enter a username."}, status_code=401)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(username),
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE_SECONDS,
    )
    return response


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
        summary = await build_dashboard_summary(
            get_db(), max_rows=settings.dcb_max_rows, timeout_seconds=settings.dcb_query_timeout_seconds
        )
    except Exception:  # noqa: BLE001 — logged; the caller gets a fixed message, never exception text
        _log.exception("dashboard summary failed")
        return JSONResponse({"error": "Could not build the dashboard summary."}, status_code=500)
    return summary


@app.get("/api/dashboard_summary/{subdivision_name}")
async def subdivision_dashboard_summary(subdivision_name: str, request: Request):
    """Per-sub-division drill-down (design doc: dashboard.html's
    Sub-divisions panel). No separate 'list every sub-division that ever
    existed' endpoint exists -- /api/dashboard_summary's own
    `subdivisions` array already IS that full list (see
    app.dashboard_data's _ALL_SUBDIVISIONS_EVER_SQL), so a second route
    that would just re-shape the same rows would be a redundant
    abstraction, not a needed one.
    `subdivision_name` is untrusted request input; build_subdivision_
    dashboard_summary treats a name that doesn't exactly match a real
    sub_division/section as ValueError → 404, never as a query to run
    with an assumed-safe value (see app.dashboard_data's module
    docstring for exactly how that's enforced given app.db.ReadOnlyDB.run
    has no parameter binding)."""
    if not _current_user(request):
        return JSONResponse({"error": "Not authenticated."}, status_code=401)
    try:
        summary = await build_subdivision_dashboard_summary(
            get_db(),
            subdivision_name=subdivision_name,
            max_rows=settings.dcb_max_rows,
            timeout_seconds=settings.dcb_query_timeout_seconds,
        )
    except ValueError:
        return JSONResponse({"error": "No sub-division or section by that name."}, status_code=404)
    except Exception:  # noqa: BLE001
        _log.exception("sub-division dashboard summary failed")
        return JSONResponse({"error": "Could not build the sub-division summary."}, status_code=500)
    return summary


# ---------------------------------------------------------------------------
# New demo sections (Insights / Reports / About) -- additive, read-only,
# same one persistent DB connection (app.db.get_db()), never the chat
# pipeline's LLM/safety layers, since every query here is fixed and
# hand-written, not LLM-generated. Public the same way /chat is (no login).
# ---------------------------------------------------------------------------


@app.get("/insights", response_class=HTMLResponse)
async def insights_page():
    return _INSIGHTS_HTML_PATH.read_text(encoding="utf-8")


@app.get("/api/insights_summary")
async def insights_summary():
    try:
        return await build_insights_summary(
            get_db(), max_rows=settings.dcb_max_rows, timeout_seconds=settings.dcb_query_timeout_seconds
        )
    except Exception:  # noqa: BLE001
        _log.exception("insights summary failed")
        return JSONResponse({"error": "Could not build the insights summary."}, status_code=500)


@app.get("/reports", response_class=HTMLResponse)
async def reports_page():
    return _REPORTS_HTML_PATH.read_text(encoding="utf-8")


@app.get("/api/reports_options")
async def reports_options():
    try:
        return await get_reports_options(
            get_db(), max_rows=settings.dcb_max_rows, timeout_seconds=settings.dcb_query_timeout_seconds
        )
    except Exception:  # noqa: BLE001
        _log.exception("report options failed")
        return JSONResponse({"error": "Could not load the report options."}, status_code=500)


@app.get("/api/reports_series")
async def reports_series(entity_type: str, value: str):
    """entity_type is 'subdivision' or 'tariff'; `value` is untrusted
    request input -- validated against the real, freshly-fetched option
    list (get_reports_options) before app.reports_data.get_entity_series
    ever builds a query with it, matching app.dashboard_data's own
    convention given app.db.ReadOnlyDB.run has no parameter binding."""
    if entity_type not in ("subdivision", "tariff"):
        return JSONResponse({"error": "entity_type must be 'subdivision' or 'tariff'."}, status_code=400)
    db = get_db()
    options = await get_reports_options(
        db, max_rows=settings.dcb_max_rows, timeout_seconds=settings.dcb_query_timeout_seconds
    )
    valid_values = options["subdivisions"] if entity_type == "subdivision" else options["tariff_categories"]
    if len(value) > 60 or value not in valid_values:
        return JSONResponse({"error": f"Unknown {entity_type}."}, status_code=404)
    try:
        return await get_entity_series(
            db, entity_type, value, max_rows=settings.dcb_max_rows, timeout_seconds=settings.dcb_query_timeout_seconds
        )
    except ValueError:
        return JSONResponse({"error": "No data for that selection."}, status_code=404)
    except Exception:  # noqa: BLE001
        _log.exception("report series failed")
        return JSONResponse({"error": "Could not load that series."}, status_code=500)


@app.get("/about", response_class=HTMLResponse)
async def about_page():
    return _ABOUT_HTML_PATH.read_text(encoding="utf-8")


@app.get("/api/glossary")
async def glossary():
    """Not a DB query -- reuses app.semantic_layer's own already-loaded
    JSON file (DCB_Semantic_Layer/semantic_layer.json), the exact same
    glossary/tariff_code_glossary the chat pipeline's own prompt is built
    from, read-only, never written to. The tariff-code glossary there is the
    CESC tariff booklet's (overlaid in memory, app.semantic_layer), and
    `booklet` carries the full booklet for the About page's tariff section."""
    layer = load_semantic_layer()
    return {"glossary": layer["glossary"], "tariff_code_glossary": layer["tariff_code_glossary"],
            "booklet": tariff_booklet.page_data()}


# ---------------------------------------------------------------------------------------------- tenders
_TENDERS_HTML_PATH = Path(__file__).resolve().parent / "static" / "tenders.html"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@app.get("/tenders", response_class=HTMLResponse)
async def tenders_page():
    return _TENDERS_HTML_PATH.read_text(encoding="utf-8")


async def _json_body(request: Request, cap: int) -> dict | None:
    body = await request.body()
    if len(body) > cap:
        return None
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


@app.get("/api/tender/rules")
async def tender_rules():
    """The rules the tender page applies: GeM modes (GFR Rule 149), CESC delegation of powers, penalty tables, GeM's
    null-and-void clause list."""
    return {"gem_modes": [{"up_to": None if c == float("inf") else c, "mode": n, "text": t} for c, n, t in tender.GEM_MODES],
            "ladder": tender.plan(1)["ladder"], "notes": tender.DOP_NOTES, "single_tender": tender.SINGLE_TENDER_GUIDELINES,
            "penalty_full": tender.PENALTY_FULL_SUPPLY, "penalty_part": tender.PENALTY_PART,
            "penalty_part_in_time": tender.PENALTY_PART_IN_TIME, "prohibited": tender.GEM_PROHIBITED,
            "above_md": [{"up_to": None if c == float("inf") else c, "who": w, "text": t} for c, w, t in tender.DOP_ABOVE_MD]}


@app.post("/api/tender/plan")
async def tender_plan(request: Request):
    data = await _json_body(request, 4096)
    if data is None:
        return JSONResponse({"error": "Send JSON: {estimated_value, kind, method}."}, status_code=400)
    try:
        value = float(str(data.get("estimated_value") or 0).replace(",", ""))
    except ValueError:
        return JSONResponse({"error": "estimated_value must be a number (rupees)."}, status_code=400)
    if value < 0 or value > 1e12:
        return JSONResponse({"error": "estimated_value is out of range."}, status_code=400)
    return tender.plan(value, str(data.get("kind") or "goods"), str(data.get("method") or "open"))


@app.post("/api/tender/draft")
async def tender_draft(request: Request):
    data = await _json_body(request, 262144)
    if data is None:
        return JSONResponse({"error": "The tender form could not be read."}, status_code=400)
    form = tender.form_from(data)
    body, s = tender.render_html(form)
    return {"html": body, "checks": s["checks"], "plan": s["plan"], "suggested": s["suggested"],
            "file_name": tender.file_name(form, "docx")}


@app.post("/api/tender/docx")
async def tender_docx(request: Request):
    data = await _json_body(request, 262144)
    if data is None:
        return JSONResponse({"error": "The tender form could not be read."}, status_code=400)
    form = tender.form_from(data)
    blob = await asyncio.to_thread(tender.render_docx, form)
    return Response(content=blob, media_type=_DOCX, headers={
        "Content-Disposition": f'attachment; filename="{tender.file_name(form, "docx")}"', "Cache-Control": "no-store"})


@app.post("/api/tender/analyse")
async def tender_analyse(request: Request):
    """Bid Assist-style reading of a tender: JSON {pdf_base64 | text, profile?}. The PDF is read in memory only."""
    data = await _json_body(request, settings.dcb_max_upload_bytes)
    if data is None:
        return JSONResponse({"error": "Send a PDF (as pdf_base64) or the tender text, at most 15 MB."}, status_code=400)
    text = str(data.get("text") or "")[:2_000_000]
    if data.get("pdf_base64"):
        import base64
        try:
            raw = base64.b64decode(str(data["pdf_base64"]), validate=False)
            if not raw.startswith(b"%PDF"):
                raise ValueError("not a PDF")
            text = await asyncio.to_thread(tender.pdf_text, raw)
        except Exception:  # noqa: BLE001 -- a broken / encrypted PDF is a 400, never a 500
            _log.info("tender PDF could not be read")
            return JSONResponse({"error": "That PDF could not be read (scanned or protected PDFs have no text to read)."},
                                status_code=400)
    if len(text.strip()) < 40:
        return JSONResponse({"error": "No readable text was found. A scanned PDF has no text layer; paste the text instead."},
                            status_code=400)
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else None
    return await asyncio.to_thread(tender.analyse, text, profile)


# ---------------------------------------------------------------------------------------------- Excel reports
@app.get("/api/mis-report")
async def mis_report(request: Request, subdivision: str | None = None, fy: str | None = None):
    """The dashboard selection (whole division or one sub-division, a fiscal year) as an Excel MIS workbook.
    Login-gated like the dashboard; unknown values are a 404, never a query; a failed check is a 409."""
    if not _current_user(request):
        return JSONResponse({"error": "Not authenticated."}, status_code=401)
    if (subdivision and len(subdivision) > 40) or (fy and len(fy) > 10):
        return JSONResponse({"error": "Not found."}, status_code=404)
    try:
        data, name = await build_mis_report(get_db(), subdivision or None, fy or None, settings.dcb_query_timeout_seconds)
    except NotFound as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return Response(content=data, media_type=_XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@app.get("/api/mis-report/view")
async def mis_report_view(request: Request, subdivision: str | None = None, fy: str | None = None):
    """The same workbook as /api/mis-report, rendered for the on-screen viewer (app/xlsx_view.py, report-viewer.js)."""
    if not _current_user(request):
        return JSONResponse({"error": "Not authenticated."}, status_code=401)
    if (subdivision and len(subdivision) > 40) or (fy and len(fy) > 10):
        return JSONResponse({"error": "Not found."}, status_code=404)
    try:
        wb, name = await build_mis_report(get_db(), subdivision or None, fy or None, settings.dcb_query_timeout_seconds,
                                          as_workbook=True)
    except NotFound as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    view = await asyncio.to_thread(workbook_view, wb)
    return JSONResponse({"name": name, **view}, headers={"Cache-Control": "no-store"})


@app.get("/api/answer-report/{report_id}/view")
async def answer_report_view(report_id: str):
    """One kept chat answer's workbook, rendered for the on-screen viewer."""
    gone = JSONResponse({"detail": "This report is no longer available. Please ask the question again."}, status_code=404)
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,40}", report_id or ""):
        return gone
    kept = answer_report.get(report_id, data_version())
    if kept is None:
        return gone
    wb, name = answer_report.build_answer_report(kept, as_workbook=True)
    view = await asyncio.to_thread(workbook_view, wb)
    return JSONResponse({"name": name, **view}, headers={"Cache-Control": "no-store"})


@app.get("/api/answer-report/{report_id}")
async def answer_report_download(report_id: str):
    """Excel for one chat answer the server kept (the id comes with the answer; kept for 3 hours)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,40}", report_id or ""):
        return JSONResponse({"detail": "This report is no longer available. Please ask the question again."}, status_code=404)
    kept = answer_report.get(report_id, data_version())
    if kept is None:
        return JSONResponse({"detail": "This report is no longer available. Please ask the question again."}, status_code=404)
    data, name = answer_report.build_answer_report(kept)
    return Response(content=data, media_type=_XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


# ================================================================================================ voice
# Speech in and out around the unchanged pipeline (the Meghalaya voice agent's contract, as integrated into KPCL and
# tuned in RDPR; ported here 2026-10-05).

# The speech-to-text prompt: one plain paragraph naming the subject, its terms and the division's six place names.
# RDPR (thousands of GPs) leaves places out, because a long list makes qwen3-asr snap to a wrong listed name; KPCL (a short,
# fixed list of stations) names them. This data has six places, all valid, so it follows KPCL: on the spoken stress test
# (tests/run_voice_stress.py, 2026-10-07) naming them took the place-name questions from 147 to 212 of 232 spoken trials
# ("Erkalgu", "Hanger Huli", "whole Ennoresepura", "Coimanur" were heard before). An unclear name can still be snapped to
# the wrong one of the six; the voice reads the place back with every answer, so the officer hears it. No tariff codes
# are listed (LT-1 / LT-4 / HT-2 must be heard, not guessed).
ASR_CONTEXT = (
    "Spoken questions in Indian English to the CESC electricity billing data assistant, about the Demand Collection "
    "Balance statement of Holenarasipura Division, its sub-divisions Arkalgud, Hangarahalli, Holenarasipura, "
    "Ramanathapura and Saligrama, Konanur section, and its LT and HT tariff categories. Terms: demand, "
    "collection, collection efficiency, arrears, outstanding balance, opening balance, closing balance, installations, "
    "live installations, DC and MNR installations, consumption in units, FPPCA, AT and C loss, billing efficiency, "
    "government subsidy, Gruha Jyothi, Bhagya Jyothi, IP sets, tariff rate, lakh, crore, financial year.")
_PROMPT_WORDS = set(re.findall(r"[a-z]+", ASR_CONTEXT.lower()))
_ECHO_TERMS = [{"arrears"}, {"outstanding"}, {"opening"}, {"installations"}, {"consumption"}, {"fppca"}, {"billing"},
               {"subsidy"}, {"gruha", "jyothi"}, {"bhagya"}, {"tariff"}, {"mnr"}, {"efficiency"}, {"statement"}]


_PROMPT_SEQ = re.findall(r"[a-z]+", ASR_CONTEXT.lower())
_PROMPT_5GRAMS = {tuple(_PROMPT_SEQ[i:i + 5]) for i in range(len(_PROMPT_SEQ) - 4)}


def _strip_prompt_tail(text: str) -> str:
    """After a question with a long silence behind it, the model can carry on reading its own prompt: "... in FY
    2024-25? Spoken questions in Indian English to the CESC ...". Cut the transcript where five words in a row
    of the prompt begin and the rest (10+ words) is almost all prompt. A question may use a few prompt words in a row
    ("DC and MNR installations"), but never runs on through the prompt."""
    toks = list(re.finditer(r"[A-Za-z]+", text or ""))
    low = [t.group(0).lower() for t in toks]
    for i in range(len(low) - 4):
        tail = low[i:]
        if tuple(low[i:i + 5]) in _PROMPT_5GRAMS and len(tail) >= 10 and                 sum(w in _PROMPT_WORDS for w in tail) / len(tail) >= 0.85:
            return text[:toks[i].start()].rstrip(" ,;:-")
    return text


def _is_prompt_echo(text: str) -> bool:
    """On silence or noise the model can return its prompt. A transcript made of the prompt's own words that runs
    through four or more of its distinct terms is that echo, not a question (a question names one or two)."""
    words = re.findall(r"[a-z]+", (text or "").lower())
    if len(words) < 10:
        return False
    in_prompt = sum(w in _PROMPT_WORDS for w in words) / len(words)
    return in_prompt >= 0.85 and sum(bool(g & set(words)) for g in _ECHO_TERMS) >= 4


@app.get("/api/voice/config")
async def voice_config():
    return {"speech_in": settings.asr_configured, "speech_out": settings.tts_configured,
            "stream": settings.tts_configured and settings.tts_stream_enabled,
            "speed": settings.tts_configured and settings.tts_speed_enabled,
            "sample_rate": settings.tts_stream_sample_rate}


def _rejected(kind: str, message: str, heard: str = "") -> dict:
    return {"text": "", "heard": heard, "confirm": None, "rejected": kind, "message": message}


@app.post("/api/voice/transcribe")
async def voice_transcribe(request: Request, language: str = "en", session_id: str = "default", expect: str = ""):
    """One spoken question as a WAV body -> {"text", "heard", "confirm", "rejected", "message"}.
      text      the cleaned question to ask ("" = nothing to ask)
      heard     what the speech model wrote, before clean-up
      confirm   {"prompt", "options": [{"label", "question"}]} when the clean-up was unsure: ask before answering
      rejected  "too_long" | "language" | "duplicate" | "echo" | "no_speech" | "too_short" | null, with "message" to
                say (none for duplicate / echo / no_speech / too_short: those are noise, not a question)
    expect=confirm: the panel is waiting for an answer to "Did you mean ...?", so one word ("Hassan", "yes") is fine;
    expect=bargein: words said over a reply ("stop"); the panel itself ignores fragments and the reply's own echo.
    413 over the byte cap, 503 when speech-to-text is down (the page then offers typing instead)."""
    if not re.fullmatch(r"[a-z]{2,3}", language or ""):
        language = "en"
    session = re.sub(r"[^A-Za-z0-9._:-]", "", session_id)[:64] or "default"
    audio = await request.body()
    if not audio:
        return JSONResponse(status_code=400, content={"detail": "No audio was received."})
    if len(audio) > settings.voice_max_audio_bytes:
        return JSONResponse(status_code=413, content={"detail": "That recording is too long. Please ask one question at a time."})
    secs = voice_guard.wav_seconds(audio)
    if secs is not None and secs > settings.voice_max_seconds:
        return _rejected("too_long", f"That was longer than {int(settings.voice_max_seconds)} seconds. Please ask one short question at a time.")
    voiced = voice_guard.voiced_seconds(audio)
    if voiced is not None and voiced < voice_guard.MIN_VOICED_SECONDS:
        _log.info("transcribe: %.2f s of speech in the recording; not sent to the speech model", voiced)
        return _rejected("no_speech", "")
    hint = ASR_CONTEXT if settings.asr_context_hints_enabled else None
    try:
        raw = await voice.transcribe(audio, "audio.wav", language=language, prompt=hint)
    except voice.VoiceBusy:
        return JSONResponse(status_code=503, headers={"Retry-After": "5"},
                            content={"detail": "The speech service is busy. Please try again in a moment."})
    except voice.VoiceUnavailable as exc:
        _log.warning("transcription failed: %s", exc)
        return JSONResponse(status_code=503, content={"detail": "Speech recognition is unavailable right now."})
    raw = (raw or "").strip()
    if hint:
        whole, raw = raw, _strip_prompt_tail(raw)
        if whole and not raw:
            _log.info("transcribe: dropped an echo of the ASR prompt (no speech)")
            return _rejected("echo", "")
    if hint and _is_prompt_echo(raw):
        _log.info("transcribe: dropped an echo of the ASR prompt (no speech)")
        return _rejected("echo", "")
    if not raw:
        return {"text": "", "heard": "", "confirm": None, "rejected": None, "message": ""}
    raw = voice_guard.latinise(raw)               # "And कौन अनुर?" -> "And kaun anur?" (a short mixed-script transcript)
    if not voice_guard.language_ok(raw):
        return _rejected("language", "I can only take questions in English. Please ask again in English.", raw)
    if len(raw) > settings.voice_max_transcript_chars:
        return _rejected("too_long", "That was a long question. Please ask one thing at a time.", raw)
    cleaned, notes = spoken.clean_transcript_ex(raw)
    # a lone "hello" / "thank you" is small talk, answered; a follow-up such as "and Konanur?" is kept
    if expect not in ("confirm", "bargein") and not followup.is_fragment(cleaned) and not small_talk.kind(cleaned) \
            and voice_guard.too_short(cleaned, voiced, _PROMPT_WORDS | {"fy", "the", "of", "in", "at"}):
        _log.info("transcribe: dropped a fragment that is not a question: %r", raw[:80])
        return _rejected("too_short", "", raw)
    if voice_guard.repeats.seen(session, cleaned):
        return _rejected("duplicate", "", raw)
    confirm = voice_guard.confirmation(cleaned, notes)
    if notes or cleaned.lower() != raw.lower():
        from app import router as _router
        voice_guard.record_mishearing({"heard": raw, "cleaned": cleaned, "notes": notes,
                                       "unknown_names": _router.extract(cleaned).get("unknown_subdivisions", [])})
    return {"text": cleaned, "heard": raw, "confirm": confirm, "rejected": None, "message": ""}


@app.get("/api/voice/health")
async def voice_health(refresh: bool = False):
    """Speech in / speech out reachability (checked at most every two minutes unless refresh=true)."""
    return await voice_guard.speech_health(force=refresh)


_TTS_CACHE: "OrderedDict[tuple, bytes]" = OrderedDict()
_TTS_CACHE_MAX_ITEMS, _TTS_CACHE_MAX_CHARS = 64, 160
_PCM_HEADERS = {"X-Audio-Sample-Rate": str(settings.tts_stream_sample_rate), "X-Audio-Format": "s16le; channels=1",
                "Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _cache_key(text: str, speed: float | None, streamed: bool):
    if len(text) > _TTS_CACHE_MAX_CHARS:
        return None                                # only short fixed phrases (fillers, apologies) are cached
    return (streamed, text, speed, settings.tts_model, settings.tts_task_type, settings.tts_ref_audio_path,
            settings.dcb_voice_instructions, settings.tts_language, settings.tts_stream_sample_rate)


def _cache_put(key, audio: bytes) -> None:
    if key is None or not audio:
        return
    _TTS_CACHE[key] = audio
    _TTS_CACHE.move_to_end(key)
    while len(_TTS_CACHE) > _TTS_CACHE_MAX_ITEMS:
        _TTS_CACHE.popitem(last=False)


async def _warm_tts_cache() -> None:
    """Synthesise the filler phrases once at startup (streamed form), so the first one plays at once."""
    if not (settings.tts_configured and settings.tts_stream_enabled):
        return
    for phrase in spoken.FILLERS:
        said = spoken.pronounce(phrase)
        try:
            _cache_put(_cache_key(said, None, True), b"".join([c async for c in voice.stream_speech(said)]))
        except Exception as exc:  # noqa: BLE001 -- best effort; the first request fills it instead
            _log.info("TTS warm-up skipped: %s", exc)
            return


def _speak_input(body: dict) -> tuple[str, float | None] | JSONResponse:
    text = str(body.get("text") or "").strip()
    if not text or len(text) > 2000:
        return JSONResponse(status_code=400, content={"detail": "Nothing to say."})
    speed = body.get("speed")
    try:
        speed = None if speed in (None, "") else min(2.0, max(0.5, float(speed)))
    except (TypeError, ValueError):
        speed = None
    return text, speed


def _unavailable(said: str, detail: str, retry: bool = False) -> JSONResponse:
    return JSONResponse(status_code=503, headers={"Retry-After": "5"} if retry else None,
                        content={"detail": detail, "spoken_text": said})


@app.post("/api/voice/speak")
async def voice_speak(request: Request):
    """Text -> a whole WAV clip in the officer voice. 503 + spoken_text when it can't (the page then uses the
    browser voice with exactly that text)."""
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "Nothing to say."})
    parsed = _speak_input(body if isinstance(body, dict) else {})
    if isinstance(parsed, JSONResponse):
        return parsed
    text, speed = parsed
    said = spoken.pronounce(text)
    if not settings.tts_configured:
        return _unavailable(said, "Voice output is not enabled.")
    key = _cache_key(said, speed, False)
    audio = _TTS_CACHE.get(key) if key else None
    if audio is None:
        try:
            audio = await voice.speak(said, speed)
        except voice.VoiceBusy:
            return _unavailable(said, "The speech service is busy.", retry=True)
        except voice.VoiceUnavailable as exc:
            _log.warning("TTS failed: %s", exc)
            return _unavailable(said, "Voice output is unavailable right now.")
        _cache_put(key, audio)
    media = "audio/wav" if audio[:4] == b"RIFF" else "audio/mpeg" if audio[:3] == b"ID3" else "audio/wav"
    return Response(content=audio, media_type=media, headers={"Cache-Control": "no-store"})


@app.post("/api/voice/speak/stream")
async def voice_speak_stream(request: Request):
    """Text -> raw PCM as the model decodes it (first audio ~1 s). The first chunk is awaited before the 200 is
    sent, so any failure the page can still act on is a clean 503 with spoken_text. 404 when streaming is off."""
    if not settings.tts_stream_enabled:
        return JSONResponse(status_code=404, content={"detail": "Streamed voice output is off; use /api/voice/speak."})
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "Nothing to say."})
    parsed = _speak_input(body if isinstance(body, dict) else {})
    if isinstance(parsed, JSONResponse):
        return parsed
    text, speed = parsed
    said = spoken.pronounce(text)
    if not settings.tts_configured:
        return _unavailable(said, "Voice output is not enabled.")
    key = _cache_key(said, speed, True)
    cached = _TTS_CACHE.get(key) if key else None
    if cached is not None:
        async def cached_body():
            for i in range(0, len(cached), 16384):
                yield cached[i:i + 16384]
        return StreamingResponse(cached_body(), media_type="audio/pcm", headers=_PCM_HEADERS)
    stream = voice.stream_speech(said, speed)
    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        return _unavailable(said, "Voice output is unavailable right now.")
    except voice.VoiceBusy:
        return _unavailable(said, "The speech service is busy.", retry=True)
    except voice.VoiceUnavailable as exc:
        _log.warning("TTS stream failed before audio: %s", exc)
        return _unavailable(said, "Voice output is unavailable right now.")

    async def body_iter():
        kept = [first] if key is not None else None
        try:
            yield first
            async for chunk in stream:
                if kept is not None:
                    kept.append(chunk)
                yield chunk
            if kept is not None:
                _cache_put(key, b"".join(kept))          # only a complete clip is cached
        except voice.VoiceUnavailable as exc:
            _log.warning("TTS stream cut short: %s", exc)
        finally:
            try:
                await stream.aclose()                    # frees the gateway slot when the page stops listening
            except RuntimeError:
                pass
    return StreamingResponse(body_iter(), media_type="audio/pcm", headers=_PCM_HEADERS)


_LAST_FILLER: "OrderedDict[str, str]" = OrderedDict()


@app.get("/api/voice/filler")
async def voice_filler(session_id: str = "default"):
    """A short phrase spoken the moment a question is captured, never the same twice in a row per session."""
    sid = re.sub(r"[^A-Za-z0-9._:-]", "", session_id)[:64] or "default"
    last = _LAST_FILLER.get(sid)
    choice = next(f for f in spoken.FILLERS if f != last)
    _LAST_FILLER[sid] = choice
    _LAST_FILLER.move_to_end(sid)
    while len(_LAST_FILLER) > 5000:
        _LAST_FILLER.popitem(last=False)
    return {"filler": choice}


# ---------------------------------------------------------------------------------------------- the chat
async def _run(question: str, progress=None, previous: str | None = None, mode: str = "text") -> AskResponse:
    try:
        await asyncio.wait_for(_SLOTS.acquire(), timeout=settings.dcb_queue_wait_seconds)
    except asyncio.TimeoutError:
        _log.warning("busy: no free slot for %r", question[:200])
        return AskResponse(question=question, answerable=False, answer=REFUSALS["busy"])
    try:
        result: PipelineResult = await answer_question(
            question=question,
            db=get_db(),
            router_index=app.state.router_index,
            max_rows=settings.dcb_max_rows,
            timeout_seconds=settings.dcb_query_timeout_seconds,
            progress=progress,
            previous=previous,
        )
    except Exception:  # noqa: BLE001
        _log.exception("pipeline failed for %r", question[:200])
        return AskResponse(question=question, answerable=False, answer=REFUSALS["error"])
    finally:
        _SLOTS.release()
    if result.error:
        _log.warning("answer error for %r: %s", question[:200], result.error)
    report_id = ""
    if answer_report.wants_report(result.route, result.answerable, result.columns, result.rows):
        report_id = answer_report.keep(result.question or question, result.answer, result.route, result.columns, result.rows,
                                       result.truncated, result.chart_data, result.total_row, data_version())
    said = _spoken_for_voice(result.asked_as or result.question or question, result, report_id) if mode == "voice" else ""
    return AskResponse(
        spoken_answer=said,
        question=result.question,
        answerable=result.answerable,
        sql=result.sql if _TECH else "",
        columns=result.columns,
        rows=[list(r) for r in result.rows],
        truncated=result.truncated,
        answer=result.answer,
        clarification_message=result.clarification_message,
        entities=result.entities if _TECH else {},
        timing_ms=result.timing_ms if _TECH else {},
        chart_data=result.chart_data,
        table=result.table if result.answerable else None,
        route=result.route if _TECH else "",
        notes=result.notes,
        verification=(result.verification + ("; " + " | ".join(result.review_notes) if result.review_notes else ""))
        if _TECH else "",
        error=result.error if _TECH else "",
        report_id=report_id,
        asked_as=result.asked_as,
        context=result.context,
    )


def _spoken_for_voice(question: str, r: PipelineResult, report_id: str) -> str:
    """What the voice says, under the voice guardrails (app/voice_guard.py), in this order:
      1. a refusal that may come from mishearing -> "I heard: ... Could you say it again?";
      2. a model-written answer the second check did not pass -> only "the answer is on your screen";
      3. any answer from the data -> "For <place>, <period>: " first (unless the answer already says both), then the
         spoken form, whose every figure must be a screen figure with its unit -- otherwise "the figures are on your
         screen" (never an unchecked number)."""
    try:
        again = voice_guard.ask_again(question, r.route)
        if again:
            return again
        if r.route == "llm" and not str(r.verification).startswith(("ok", "fixed")):
            return voice_guard.MODEL_UNCHECKED
        base = spoken.spoken_answer(question, r.answer, r.route, rows=len(r.rows), has_chart=bool(r.chart_data),
                                    has_report=bool(report_id))
        if not r.route.startswith(premise.DATA_ROUTES) or not r.answerable:
            return base
        base = voice_guard.add_units(base)
        if not (voice_guard.figures_match_screen(base, r.answer) and voice_guard.figures_have_units(base)):
            _log.warning("spoken figures withheld for %r: %r", question[:200], base[:200])
            base = voice_guard.FIGURES_ON_SCREEN + (" " + spoken.REPORT_NOTE if report_id else "")
        return voice_guard.readback(question, r.answer, base) + base
    except Exception:  # noqa: BLE001 -- the spoken form is best-effort; the screen answer stands
        _log.exception("spoken answer failed for %r", question[:200])
        return voice_guard.FIGURES_ON_SCREEN if r.answerable else "Sorry, I couldn't answer that. Please ask again."


@app.post("/api/ask_once", response_model=AskResponse)
async def ask_once(req: AskRequest):
    return await _run(req.question, previous=req.previous, mode=req.mode)


def _chunks(text: str) -> list[str]:
    """Split the answer into word-sized pieces, keeping line breaks, so the page can type it out."""
    return re.findall(r"\n|[^ \n]+ ?| ", text)


@app.post("/api/ask")
async def ask_stream(req: AskRequest):
    async def event_stream():
        stages: asyncio.Queue[str] = asyncio.Queue()
        task = asyncio.create_task(_run(req.question, progress=stages.put_nowait, previous=req.previous, mode=req.mode))
        sent: set[str] = set()
        yield _sse("status", {"stage": "thinking"})
        if req.previous:
            asked_as = followup.resolve(req.question, req.previous)
            if asked_as:
                yield _sse("followup", {"asked_as": asked_as})
        while True:
            getter = asyncio.create_task(stages.get())
            finished, _ = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
            if getter in finished:
                stage = getter.result()
                if stage not in sent:
                    sent.add(stage)
                    yield _sse("status", {"stage": stage})
                continue
            getter.cancel()
            break
        while not stages.empty():
            stage = stages.get_nowait()
            if stage not in sent:
                sent.add(stage)
                yield _sse("status", {"stage": stage})
        result = task.result()
        if result.spoken_answer:
            # Voice mode: what to say goes first, so the voice can start while the text is still typing out.
            yield _sse("spoken", {"spoken_answer": result.spoken_answer, "answerable": result.answerable})
        yield _sse("sql", {"sql": result.sql} if _TECH else {"sql": ""})
        yield _sse("chart", {"chart_data": result.chart_data})
        yield _sse("status", {"stage": "answering"})
        # Chunk the already-assembled answer so the client can render it progressively (see CLAUDE.md on why this
        # isn't token-level LLM streaming). Paced so a short answer reads like typing and a long one still finishes
        # in about two seconds; no pause shorter than 20 ms (Windows timers tick every ~15 ms).
        pieces = _chunks(result.answer)
        group = max(1, -(-len(pieces) // 90))
        for i in range(0, len(pieces), group):
            yield _sse("answer_chunk", {"text": "".join(pieces[i:i + group])})
            await asyncio.sleep(ANSWER_CHUNK_DELAY_SECONDS)
        yield _sse("table", {"table": result.table})
        yield _sse(
            "done",
            {
                "answerable": result.answerable,
                "timing_ms": result.timing_ms,
                "truncated": result.truncated,
                "technical": _TECH,
                "route": result.route,
                "verification": result.verification,
                "report_id": result.report_id,
                "asked_as": result.asked_as,
                "context": result.context,
                "spoken_answer": result.spoken_answer,
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
