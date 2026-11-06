# CESCOM Holenarasipura DCB — project memory (CLAUDE.md)

**Read this file first in any new session on this project, including when picking it up cold on another machine.**
- **This file is the working memory:**
  - the rules (§0);
  - the **START HERE** summary just below;
  - a dated log of every pass (§1–§17), with root causes and verbatim results.
- **`HANDOFF.md` (v3, 2026-10-07)** is the complete portable handoff: setup on a new machine, the pipeline internals
  (the 11 Excel bugs), the data facts, and a reuse checklist.
- **The two files are kept in step.** If they ever disagree, the newest dated section here wins.

Standing rule: no "done" claim without real, verbatim output. No invented numbers, no smoothed-over failures;
limitations are documented, not hidden.

---

## START HERE — current state (2026-10-07)

**What it is:** a CESC DCB assistant for Holenarasipura Division, FY 2015-16 to 2025-26.
- **Interface:** typed and **voice**, English and Kannada; a FastAPI app in `dcb-ai-app/`.
- **Data:** it reads the **locked** star schema (`DCB_Star_Schema/`) and the checked **mart** (`DCB_Mart/`, rebuilt by
  `build_mart.py`, 21 checks).
- **Model:** the internal gateway over the VPN only (`SQL_VERIFY_*`, `qwen4-deploy`). There is no Gemini or any other
  provider.
- **Voice:** qwen3-asr and qwen3-tts on the same gateway (`ASR_*`, `TTS_*`). The TTS clones
  `dcb-ai-app/voices/officer_serena.wav`.

**Run (Windows):**
```powershell
cd "<path>\New folder\dcb-ai-app"
.\.venv\Scripts\python -m uvicorn app.main:app --port 8010
```
- Open `http://127.0.0.1:8010`: login (any name) → dashboard; the assistant is at `/chat`, with the voice button beside
  Ask.
- Use 127.0.0.1, not the machine's IP: the browser only allows the microphone on a secure address.
- **New machine:** `python -m venv .venv`, then `.\.venv\Scripts\python -m pip install -r requirements.txt`. Copy
  `.env` from a trusted source, or fill it in from `.env.example`.
- **Rebuild the mart only after editing `DCB_Mart/metadata/*`.** Stop the server first, then run
  `.\.venv\Scripts\python ..\DCB_Mart\build_mart.py`.

**How a question is answered:** deterministic routes first, and the model only for what's left (`app/pipeline.py`).
1. small talk;
2. follow-up rewrite;
3. Kannada → English;
4. screen;
5. verified SQL;
6. tariff booklet;
7. capability note;
8. knowledge base / glossary;
9. router (places, years, tariffs, measures, intents; unknown names refused);
10. reports;
11. slabs;
12. metric lookup;
13. model (one call), then SQL guard, read-only execute, result guards, answer writer, reviewer;
14. premise check.

In voice mode, the spoken answer is written from the verified answer (`app/spoken.py`) under the voice guardrails
(`app/voice_guard.py`). Full detail: HANDOFF §4, then §12 (ladder), §16 (conversation layer) and §17 (voice) here.

**Pages:**
- `/dashboard`, `/insights`, `/reports`, `/about`, `/tenders`, `/chat`;
- RDPR/KPCL layout in the CESC palette (§15);
- Excel MIS / answer reports with an on-screen viewer (§12–§13).

**Latest verified (2026-10-07, live on the VPN, final code; §17.3):**
- **Offline:** pytest **474 passed**.
- **Stress bank:** 334 questions, **334/334 typed** and **334/334 in voice mode**.
- **Follow-ups:** **802/802** turns.
- **Spoken:** **634/660 (96.1 %)** questions and **549/590 (93.1 %)** follow-up turns. Every spoken miss passes typed,
  so it's a hearing miss.
- **Live suites:** stress **139/139**; model regression **49/49**.
- **End to end:** browser with a fake microphone works; the speech round trip is word-perfect.
- **Demo list:** `reports/DEMO_TEST_QUESTIONS.md`, with 310 fully proven questions and 23 demo picks.

**Rules (§0, HANDOFF §6):**
- The 4 `DCB_*` pipeline folders are locked.
- `D:\KPCL POC`, `D:\RDPR POC` and `D:\Meghalaya_voice` are strictly read-only: read them, copy only with permission,
  never edit, never run their code or import their modules.
- Colours come only from the CESC websites.
- Run long suites in the background.

**Where things are documented:**

| Topic | Section |
|---|---|
| Data pipeline | §1, HANDOFF §3 |
| Mart, KPCL port | §12 |
| Raw re-verification, slabs, Kannada, Gemini removed | §13 |
| Tariff booklet, subsidy, AT&C, tenders | §14 |
| Front end | §15 |
| Follow-ups, premise check, small talk | §16 |
| Voice mode and the full test sweep | §17 |

§2–§11 are the earlier history: the original Gemini build, the UI iterations, the pipeline fix. Where they disagree
with the sections above, the later section is right.

---

## 0. THE MOST IMPORTANT RULE — 4 locked folders, never touch them

**`DCB_Segregated_By_SubDivision/`, `DCB_Staging_DB/`, `DCB_Star_Schema/`,
and `DCB_Semantic_Layer/` (all four are direct children of this
`New folder/` directory) are LOCKED.** They were built, verified, and
frozen in an earlier phase of this project (see §1). Everything since —
including everything in this file — is additive work built strictly on
top of them, never inside them.

**Never:**
- Modify, regenerate, or write to any file inside those 4 folders.
- Re-run `DCB_Staging_DB/build_staging.py` or `DCB_Star_Schema/build_star_schema.py`.
- Open `dcb_staging.sqlite` or `dcb_star_schema.sqlite` in anything but
  read-only mode (the app itself enforces this at the connection level —
  see §3, design point 6 — but don't rely on that as your only guard when
  poking at the DB by hand).

**Only ever read from:** `DCB_Star_Schema/dcb_star_schema.sqlite` (via a
read-only connection) and `DCB_Semantic_Layer/semantic_layer.json` /
`example_queries.sql`.

This repo (the parent folder one level above `New folder/` — its actual
drive/path varies by machine; it was `D:\Cescom POC 2\` on the machine
this was first built on, but don't assume that path) exists partly so
this rule is independently checkable: before and after any change,
`git status` run from the repo root should show nothing
under `New folder/DCB_*`, and a SHA-256 hash of every file in those 4
folders should be unchanged. Both checks have been run after every single
change across every session of this project so far and have always come
back clean, **with one specific, deliberate exception — read this before
assuming "clean" still means "byte-identical":**

**`DCB_Semantic_Layer/example_queries.sql` specifically has been edited**
in a later session (§10.2/§10.3), twice, both times purely additive (new
numbered worked examples appended — #11, #12 — nothing removed or
changed; confirmed via `git diff --stat`: `34 insertions(+), 0
deletions(-)`, one file). The other 3 locked folders, and every `.sqlite`
file in all 4 (including `DCB_Semantic_Layer` has none), remain untouched
— checked the same way, still clean. This was done deliberately: this
file is the "10 worked NL question → SQL pairs" the chatbot's own prompt
loads directly (§2, design point 7) — extending it with a verified new
pattern is prompt-engineering on the *chatbot layer*, not pipeline
regeneration, and doesn't touch the reconciled data these 4 folders exist
to protect. But it does contradict this section's own literal wording
("never... modify... any file", "only ever *read* from... example_queries.sql")
and the "always come back clean" claim two sentences up — both written
before this happened and not true without qualification anymore. Flagged
here rather than quietly left inconsistent: if strict byte-for-byte
immutability of all 4 folders (not just the 3 SQLite-backed ones) turns
out to matter for this project going forward, `example_queries.sql`
needs to move out from under this rule explicitly (e.g. treated as
`dcb-ai-app`-adjacent prompt content instead), or future additions to it
need to stop.

**A second, larger exception (§11): `DCB_Staging_DB/build_staging.py`,
`DCB_Star_Schema/build_star_schema.py`, and BOTH locked `.sqlite` files
were deliberately modified and regenerated** in a later session, unlike
the `example_queries.sql` exception above which never touched the pipeline
itself. This was NOT a silent violation — the project owner was explicitly
asked to confirm overriding this rule before anything was touched (given
real bugs were found in the pipeline's own category/rollup-label
classification, not just the chatbot layer sitting on top of it), chose
"unlock and fix in place," and a full backup of the pre-fix build scripts
and both `.sqlite` files was taken outside the repo before any edit. See
§11 for the full bug report, root causes, fixes, and verification. The
git commit immediately before this session's changes is the rollback
point if this ever needs to be undone. Going forward, this repo's own
locked-folder checking convention (`git status`/hash manifest showing
"clean") needs to be read against THAT commit, not against some earlier,
now-superseded baseline — the 4 folders are locked again as of §11's
commit, just not at the same content as before it.

---

## 1. What this project is (full detail: `HANDOFF.md`)

CESCOM (Karnataka electricity board) Demand-Collection-Balance (DCB)
billing data for Holenarasipura Division, 11 fiscal years (2015-16
through 2025-26), sourced from one raw Excel workbook
(`HNPURA DIVISION DCB FY 2015 TO 2026.xlsx`, also committed to this repo).
A 4-stage pipeline (built and locked before this file's chatbot work
began) turns that workbook into a queryable star-schema SQLite database:

1. `DCB_Segregated_By_SubDivision/` — raw split, human-readable, no reshaping.
2. `DCB_Staging_DB/dcb_staging.sqlite` — flat untyped staging table.
3. `DCB_Star_Schema/dcb_star_schema.sqlite` — typed star schema:
   `dim_fiscal_year`, `dim_subdivision`, `dim_tariff_category`,
   `fact_dcb` (safe to SUM directly), `fact_dcb_rollups` /
   `fact_dcb_grand_totals` (the report's own printed subtotals — **never**
   SUM these alongside `fact_dcb`, they're a different grain),
   `fact_dcb_subdivisions` (a view over `fact_dcb` that structurally
   excludes the `Division_Total` rollup row), `reconciliation_report`.
   **Verified: 0.0% average and max reconciliation deviation across 292
   checks**, at the time this stage was locked.
4. `DCB_Semantic_Layer/` — glossary, tariff-code glossary, grounding
   rules, 10 pre-verified NL→SQL pairs (`example_queries.sql`), meant to
   be loaded straight into an LLM prompt (`semantic_layer.json`).

11 real messy-Excel bugs were found and fixed while building that
pipeline (multi-row header drift across two eras, forward-fill bleed
across merged headers, an embedded orphan mini-table, character-
corruption typos like `ag`→`AF`/`C`→`B`, tariff-code forward-fill on
~1,046 rows, a rollup-row misclassification between `lt_ht_total` and
`grand_total`, floating-point noise fracturing decimal tariff codes,
etc.) — full list in `HANDOFF.md` §4, not repeated here.

**Real facts about this data, not bugs, that will look like bugs to a
fresh reader if undocumented:**
- **Saligrama** closed as a sub-division after FY2017-18 — it legitimately
  has no rows after that year.
- **Division_Office** only exists FY2020-21 through FY2023-24 — an
  administrative unit with no consumer installations, so its rows are
  correctly all-zero, not missing/broken.
- `demand_revenue_subtotal` legitimately does **not** equal
  `demand_fixed_charges + demand_others + demand_energy_charges` for the
  flat-fee Misc. revenue tariff codes (`61.xxxx`/`62.xxxx`) — that's how
  the source report itself computes those rows, not an extraction bug.

## 2. What's built on top: `dcb-ai-app/` — the NL2SQL chatbot

> **Historical (the original 2026-09 build).** The current architecture, file inventory, routes and provider are in
> **START HERE** above and **HANDOFF.md §2 and §4**. This section's file tree, the "Gemini only" provider and the
> route list are out of date and kept only as history.

A FastAPI backend in `dcb-ai-app/`, additive only — it only *reads*
`DCB_Star_Schema/dcb_star_schema.sqlite` and
`DCB_Semantic_Layer/semantic_layer.json`, both opened read-only, never
written to. This was scoped in `HANDOFF.md` §6 as "FastAPI + Gemini +
Neon Postgres, matching bescom-ai's architecture" — this build stays on
local SQLite (no cloud DB exists for this project; the schema is close
enough to Postgres that porting later is mostly `CREATE TABLE` +
`SERIAL`/`JSONB` syntax, not a rewrite, per `HANDOFF.md` §6.1).
`bescom-ai` itself wasn't found on this machine as a literal folder to
copy from — "matching its architecture" was interpreted as the 7
latency-minimizing design points below, given as an explicit spec.

**Current provider (2026-10-01): the internal OpenAI-compatible gateway over the VPN only** (`SQL_VERIFY_*` in
`.env`, `app/llm.py`); Gemini was removed entirely in §13.6. The rest of this paragraph is the earlier history.
**Provider at the time: Gemini only.** An optional second (OpenAI-compatible)
provider was added, then fully reverted — see §5.6. A *second*, unrelated
attempt at the same kind of swap (an internal vLLM gateway, see §10.6)
happened in a later session and was also fully reverted, for a different
reason (an unreachable/untrusted-TLS internal network, not a latency
verdict). There is no alt-provider code, config, or env var left anywhere
in the project either time; `app/config.py` has exactly two LLM-related
settings (`gemini_api_key`, `gemini_model`), confirmed by direct
inspection as of this handoff (§10.6's revert re-confirmed this with a
`git diff --stat` showing zero changes left in `app/llm.py`/`app/config.py`
relative to this baseline).

### Full file inventory — everything outside the 4 locked folders

```
New folder/
├── .env.example                          # tracked — placeholders only
├── .gitignore                            # covers .env
├── CLAUDE.md                             # this file
├── HANDOFF.md                            # pipeline-stage detail (pre-existing)
├── HNPURA DIVISION DCB FY 2015 TO 2026.xlsx   # raw source workbook
├── test_questions.json                   # §10 regression suite — 49 NL2SQL questions
├── run_frontend_tests.py                 # §10 — drives the live chat UI via Playwright
├── reports/                              # §10 — the regression report (.md + .html)
├── test_run_results_*/                   # §10 — raw before/after run output (results.json/.md
│                                          # + per-question screenshots); disposable, not needed
│                                          # to run the app, kept as evidence for §10's findings
└── dcb-ai-app/
    ├── .gitignore                        # __pycache__/, *.pyc, .pytest_cache/
    ├── README.md                         # how to run this folder
    ├── requirements.txt
    ├── app/
    │   ├── __init__.py
    │   ├── auth.py                       # dashboard login: signed-cookie session (§9)
    │   ├── config.py                     # pydantic-settings, .env loading
    │   ├── dashboard_data.py             # dashboard's read-only summary queries (§9)
    │   ├── db.py                         # ReadOnlyDB — read-only SQLite wrapper
    │   ├── llm.py                        # Gemini client init + generate_query_plan()
    │   ├── main.py                       # FastAPI app, routes, lifespan
    │   ├── models.py                     # LLMQueryPlan pydantic model
    │   ├── pipeline.py                   # answer_question() — the shared request path
    │   ├── prompts.py                    # build_prompt(), SYSTEM_INSTRUCTIONS (§10 additions)
    │   ├── semantic_layer.py             # entity extraction + prompt pruning (§10 additions)
    │   ├── sql_safety.py                 # read-only allowlist + double-count guard (§10 fix)
    │   └── static/
    │       ├── chat.html                 # the chat UI (GET /chat, §9.6) -- gov-portal shell,
    │       │                             # conversational interaction, inline charts (§8)
    │       ├── login.html                # dashboard sign-in page (§9)
    │       ├── dashboard.html            # login-gated summary dashboard (§9)
    │       ├── cesc_logo_clean.png       # CESC dept mark, background/border cropped out —
    │       │                             # the one actually used in chat.html/dashboard.html
    │       │                             # now (§10); cesc_logo_reference.png below is its
    │       │                             # uncropped source, kept but no longer referenced
    │       ├── cesc_logo_reference.png   # CESC dept mark -- third-party crop, see §8.5
    │       └── cescom_official_logo.png  # Karnataka state emblem (still used in the sidebar
    │                                     # "DCB Insights" brand mark and login.html)
    └── tests/
        ├── __init__.py
        ├── questions.py                  # the 10 + 2 eval questions, shared source
        ├── dry_run_no_llm.py             # DB/safety/router tests, no API key needed
        ├── pipeline_mock_test.py         # full FastAPI wiring, LLM mocked
        ├── double_count_guard_test.py    # code-level Division_Total guard, 14 cases (§10)
        ├── eval_queries.py               # live Gemini eval — needs GEMINI_API_KEY
        └── eval_report.md                # generated output of the last eval_queries.py run
```

(`tests/alt_provider_config_test.py` existed briefly during the
alt-provider detour and was deleted when it was reverted — see §5.6.)

### Architecture — 7 deliberate design decisions

| # | Decision | Why | Where |
|---|---|---|---|
| 1 | **One LLM call per question**, returning SQL + an answer template with `{placeholder}` tokens, together as one structured JSON object (Gemini's native `response_schema` mode, not prompt-engineered JSON). The template is filled in Python from the query result + pre-extracted entities — **no second LLM call** ever phrases the final answer. As of §8.4, an optional `chart_data` (`ChartSpec`) joins the same single call — the LLM names *which of its own SELECT columns* to plot, never literal numbers, for exactly this reason: an earlier design that asked for literal chart values broke this same invariant and produced fabricated/empty charts on live eval. | Removes a full network round-trip from every question — the single biggest lever against a slow/mediocre LLM API "feeling" slow. | `app/models.py` (`LLMQueryPlan`, `ChartSpec`, `ChartData`), `app/llm.py`, `app/pipeline.py::_fill_template`, `_build_chart_data` |
| 2 | **Semantic-layer pruning** — a deterministic keyword/entity router (subdivision names + fiscal years loaded from the DB at startup, tariff codes and glossary terms matched against the question text) selects only the relevant subset of `semantic_layer.json` before it goes in the prompt. `rules`, `common_metrics`, `joins`, and every table's schema are always kept (small, load-bearing for correctness); `glossary` and `tariff_code_glossary` are pruned. No LLM/embeddings in the router itself — it's sub-millisecond (measured mean: 0.00ms over 12 questions). | Keeps the prompt lean as the semantic layer grows, without becoming its own latency source. | `app/semantic_layer.py` |
| 3 | **Persistent connections** — the SQLite connection and the Gemini client are both created once in FastAPI's `lifespan` and reused for every request; never re-opened per request. | Avoids repeated connection/handshake overhead on every question. | `app/db.py::init_db`, `app/llm.py::init_llm_client`, `app/main.py::lifespan` |
| 4 | **Async throughout** — async route handlers, Gemini called via `client.aio`; the (sync) SQLite call runs in a dedicated single-worker thread pool so one slow query doesn't block the event loop. | Lets FastAPI serve other requests while one is waiting on the LLM or DB. | `app/llm.py`, `app/db.py::ReadOnlyDB.run` |
| 5 | **SSE streaming of the final answer** to the client as it's assembled. | Perceived latency — first bytes reach the client sooner, and the structure is ready if token-level LLM streaming is added later. | `app/main.py::ask_stream` |
| 6 | **Read-only SQL enforcement**, two independent layers: (a) a text-level allowlist (`SELECT`/`WITH` only, forbidden-keyword regex, no multi-statement `;`, no unresolved `{placeholder}` tokens), (b) the SQLite connection itself opened with `mode=ro` in its URI — so even SQL that slipped past layer (a) physically cannot write. Plus a query timeout (default 8s) and a row cap (default 200, via `fetchmany(cap+1)` + truncation, not text-level `LIMIT` injection — safer against queries that already have their own `ORDER BY`/`LIMIT`/CTEs). | Defense in depth against a malformed or adversarial LLM response. | `app/sql_safety.py`, `app/db.py` |
| 7 | **Grounding rules enforced in the prompt** — every rule from `semantic_layer.json["rules"]` is always included (never pruned): never SUM `fact_dcb` and `fact_dcb_rollups` together, don't double-count `Division_Total` against the sub-divisions, treat `fiscal_year` as text. **The Division_Total rule is also code-enforced**, not just prompted — see §5.5 for the fix. | These are exactly the double-counting bugs the star schema's own design guards against — see `HANDOFF.md` §4.7. | `app/prompts.py::build_prompt`, `app/semantic_layer.py::prune_semantic_layer`, `app/sql_safety.py::check_division_total_double_counting` |

### Request flow

```
question
  → app.semantic_layer: extract_entities() + prune_semantic_layer()   [router, no LLM, ~0.00ms measured]
  → app.prompts.build_prompt()                                        [string assembly]
  → app.llm.generate_query_plan()                                     [THE one LLM call — Gemini]
  → app.sql_safety.validate_readonly_sql()                            [text-level allowlist + double-count guard]
  → app.db.ReadOnlyDB.run()                                           [mode=ro connection, timeout, row cap]
  → app.pipeline._fill_template()                                     [deterministic, no LLM]
  → StreamingResponse (SSE) or plain JSON
```

`app/pipeline.py::answer_question()` is the one function both the SSE
endpoint (`POST /api/ask`), the plain-JSON endpoint (`POST /api/ask_once`,
for scripts/evals), the chat UI (`GET /`, which calls `POST /api/ask`),
and the eval harness (`tests/eval_queries.py`) all call — every entry
point exercises the identical code path; only the delivery mechanism
differs.

### Routes (current, in `app/main.py`)

- `GET /` — **the app's entry point, by request (§9.6):** redirects to
  `/login` if not signed in, or straight to `/dashboard` if already
  signed in. No longer serves the chat UI directly.
- `GET /chat` — the chat UI (`app/static/chat.html`), moved here from
  `GET /` in §9.6 so it's still reachable, just no longer what opens by
  default. Still fully unauthenticated, same as always.
- `GET /static/*` — static assets (the two header logo files, §8.1), via a
  `StaticFiles` mount added in §8; `chat.html`/`login.html`/`dashboard.html`
  are still served by their own routes, read directly as text, not
  through this mount.
- `POST /api/ask` — SSE streaming endpoint the chat UI calls. Events:
  `status`, `sql`, `chart` (`chart_data` or `null`, §8.4), `answer_chunk`
  (one or more), `done`.
- `POST /api/ask_once` — plain JSON, same pipeline, easier for scripts/evals.
- `GET /health` — liveness check, returns `{"status":"ok"}`.
- `GET /docs` — FastAPI's auto-generated Swagger UI (unaffected by any of the above).
- `GET /login`, `POST /api/login`, `POST /api/logout`, `GET /api/me`,
  `GET /dashboard`, `GET /api/dashboard_summary` — the dashboard added in
  §9 (its own login flow now also the app's landing flow, per §9.6;
  `/api/ask*` above remain unauthenticated regardless).

---

## 3. How to run this from scratch (assume you've never seen this project)

> **Current (2026-10-07): follow START HERE above, HANDOFF.md §5, or `dcb-ai-app/README.md`.** Run on port 8010.
> The model is the internal gateway over the VPN (`SQL_VERIFY_*`), and voice uses `ASR_*` / `TTS_*` in the same
> `.env`. There is no Gemini key or setting any more (§13.6). The Gemini instructions and the old test list below are
> history only; do not follow them.

> **Handing this project to someone else (zip + send)?** Zip the entire
> `New folder/` directory — the one this file and `HANDOFF.md` live in —
> not just `dcb-ai-app/` on its own. The app will not start with only
> `dcb-ai-app/`: `.env`, the 4 locked pipeline folders (`DCB_Star_Schema/`
> is where the actual database lives), and `DCB_Semantic_Layer/` are all
> siblings of `dcb-ai-app/`, not inside it, and `app/config.py` resolves
> every one of those paths relative to `New folder/` (see its
> `PROJECT_ROOT` comment) — there is no absolute, machine-specific path
> anywhere in the code. `.env` itself is git-ignored (see `.gitignore`)
> but is a real file on disk right now with a working `GEMINI_API_KEY` in
> it — a plain folder zip (not a `git archive`/fresh clone, which would
> respect `.gitignore` and silently drop it) includes it, so the
> recipient can run the app immediately with no key of their own to
> obtain. That also means: whoever receives that zip gets your live
> Gemini key. Treat it accordingly (rotate the key afterward if the zip
> goes somewhere you don't fully trust).

**Prerequisites:** Python 3.10+ (pydantic-settings and the `google-genai`
SDK both need it), a Gemini API key from
[aistudio.google.com/apikey](https://aistudio.google.com/apikey) (already
provisioned in this project's own `.env` — see callout above).

```bash
cd "New folder/dcb-ai-app"
pip install -r requirements.txt

# .env lives at the PROJECT ROOT (New folder/.env), not inside dcb-ai-app/
cp ../.env.example ../.env
# then edit ../.env and fill in:
#   GEMINI_API_KEY=<your real key>
#   GEMINI_MODEL=gemini-3.5-flash-lite    (or whatever model string you're using)
```

**Run the server:**
```bash
uvicorn app.main:app --reload
```
Then open `http://127.0.0.1:8000/` in a browser — it lands on the login
page (any username, no real password check — see §9.5), and takes you
to the dashboard next. The chat UI is at `http://127.0.0.1:8000/chat`
(also linked from both the dashboard and the login page); `/docs` is
still the Swagger testing page.

**Run the tests** (from inside `dcb-ai-app/`):
```bash
python -m tests.dry_run_no_llm            # no API key needed
python -m tests.pipeline_mock_test        # no API key needed (LLM mocked)
python -m tests.double_count_guard_test   # no API key needed -- code-level Division_Total guard
python -m tests.eval_queries              # needs a real GEMINI_API_KEY in .env — writes tests/eval_report.md
```

**Before touching anything**, verify the 4 locked folders are untouched
— from the repo root (one level above `New folder/`; whatever drive/path
it's at on this machine):
```bash
git status --short   # should show nothing under "New folder/DCB_*"
```

---

## 4. Bug history — every real bug found and fixed, root cause + fix

All of these were found through actual execution/testing, not assumed
safe, and each was fixed with a real, re-verified regression pass
afterward. Listed in the order they were found.

### 4.1 — `ValueError: Unknown format code 'f' for object of type 'str'` in `_fill_template`
**Found:** first live Gemini eval, question 7. **Root cause:**
`_format_value()` pre-converts every DB value to a display-ready
*string*; the LLM's `answer_template` for that question included a
Python format spec like `{pct:.2f}`, and applying a numeric format code
to an already-stringified value raises. **Fix:** `app/pipeline.py` now
strips any `:spec` inside a `{placeholder:spec}` token before calling
`.format()` (`_strip_format_specs`), and the fallback `except` clause
also catches `ValueError`, not just `KeyError`/`IndexError`.

### 4.2 — Wrong refusal on "Compare LT-1 vs HT-2 demand"
**Found:** first live Gemini eval, question 5. **Root cause:**
`extract_entities()` only captured a single `tariff_code` (first match,
then `break`), and the prompt told the LLM to treat pre-extracted
entities as authoritative — so the model concluded it couldn't handle a
second tariff code not in that list and returned `answerable: false`
with a confused refusal. **Fix:** the router now collects *all* matching
tariff codes (`entities["tariff_codes"]` when there are 2+,
`entities["tariff_code"]` unchanged when there's exactly 1), and
`app/prompts.py`'s entity-block wording was softened from "reuse these
exact values... do not re-derive" to say the list is a convenience, not
exhaustive.

### 4.3 — Silent wrong-answer (all-`NULL`) on DC/MNR installations query
**Found:** first live Gemini eval, question 8. **Root cause:**
`DCB_Semantic_Layer/example_queries.sql`'s 10 worked SQL patterns were
**never actually loaded into the prompt at all**, despite
`semantic_layer.json`'s own `_purpose` field saying that's exactly what
they're for. Without the worked `COALESCE(dc_installations,0) +
COALESCE(mnr_installations,0) + COALESCE(dc_mnr_installations,0)`
pattern, the LLM summed only the pre-2018-19 `dc_mnr_installations`
column and got `NULL` for every 2024-25 row — `answerable: true`, with a
silently wrong (empty) result, not an error. **Fix:**
`app/prompts.py::load_example_queries()` now loads `example_queries.sql`
(unpruned — only ~4KB) into every prompt.

### 4.4 — Unresolved `{placeholder}` tokens leaking into generated SQL
**Found:** a later re-run, question 10. **Root cause:** the LLM
generated `SELECT ... WHERE s.subdivision_name = '{subdivision_name}'
AND f.fiscal_year = '{fiscal_year}'` — literal, unresolved `{...}`
tokens *inside the SQL string itself*, conflating the `sql` field's
"bake in literal values" contract with the separate `answer_template`
placeholder mechanism. SQLite has no `{}` syntax, so this matched
nothing and returned a single all-`NULL` row instead of an error.
**Fix, two layers:** `app/prompts.py`'s `SYSTEM_INSTRUCTIONS` now
explicitly forbids `{...}` tokens in `sql`; and
`app/sql_safety.py::validate_readonly_sql` now rejects any SQL
containing an unresolved `\{[a-zA-Z_]\w*\}` token as unsafe *before*
execution, so even a future prompt regression can't silently produce
this failure mode again.

### 4.5 — Division_Total double-counting was prompt-only, not code-enforced
**Found:** an intermediate re-run during the first live eval generated
`SELECT t.tariff_code, SUM(f.demand_total) ... GROUP BY t.tariff_code`
with **no `subdivision_name`/`subdivision_type` filter at all** —
silently summing that tariff code's demand across every real
sub-division *and* the `Division_Total` rollup together. That specific
run was superseded before it shipped, but nothing code-level would have
stopped it if it hadn't been. **Fix:**
`app/sql_safety.py::check_division_total_double_counting()` (called from
`validate_readonly_sql()`, so every query goes through it) — see §5.5 for
the full design and proof; this is the single most significant
correctness fix made across the project's chatbot layer.

### 4.6 — `Settings()` crashed at import time on blank `LLM_TLS_VERIFY=`/`LLM_TIMEOUT=`
**Found:** while building the (since-reverted) optional second LLM
provider. **Root cause:** the user's own instruction was to leave those
two new env vars blank in `.env` while `LLM_PROVIDER` was also blank;
declaring them as plain `bool`/`float` pydantic fields meant
`Settings()` tried to parse the empty string as a boolean/number and
raised `ValidationError` **at module-import time** — which would have
broken the entire app, Gemini included, the moment those blank lines
were added to `.env`. First fix attempt (returning pydantic's
`PydanticUndefined` sentinel from a `mode="before"` validator, expecting
fallthrough to the field default) was tested directly and confirmed
**not** to work in this pydantic version (raises a different
`bool_type`/`float_type` error on `PydanticUndefined` itself). Final fix
used before the whole feature was reverted: two `field_validator(mode="before")`
methods that returned the literal defaults (`True` / `120.0`) directly
on a blank string. **This entire code path no longer exists** — see §5.6.

### 4.7 — DC/MNR COALESCE pattern (bug 4.3's root cause) recurring far more often than assumed
**Found:** during the UI-redesign session's step-4 final regression, a
live `eval_queries.py` run returned Q8 (DC/MNR installations by
sub-division) with Holenarasipura = **23,578** instead of the
long-verified **11,806**. Re-running the identical question 6 times in a
row to characterize it (rather than assume a one-off): **2/6 correct,
4/6 wrong** — a 67% failure rate, not a rare fluke. **Root cause:** even
with `DCB_Semantic_Layer/example_queries.sql`'s worked COALESCE pattern
loaded unpruned into every prompt (bug 4.3's original fix), the LLM does
not reliably copy the exact three-column sum verbatim once the question's
own SQL shape differs even slightly from the worked example (this
session's Q8 grouped by sub-division only, filtered to one fiscal year,
vs. the worked example's all-years shape) — on the wrong runs, the
generated SQL summed `dc_installations` twice
(`COALESCE(f.dc_installations, 0) + COALESCE(f.mnr_installations, 0) +
COALESCE(f.dc_installations, 0)`) and silently dropped
`dc_mnr_installations` entirely, roughly doubling the true figure. This
is the same underlying risk bug 4.3 first identified, recurring at a much
higher rate than "no live-LLM run has yet demonstrated a real violation"
(this file's own prior wording) suggested. **Fix:**
`DCB_Semantic_Layer/example_queries.sql` is inside a locked folder (§0)
and cannot be edited, so the fix went into `app/prompts.py`'s
`SYSTEM_INSTRUCTIONS` instead — a new explicit hard rule naming all three
columns exactly, warning specifically against repeating one column while
omitting another, and citing the measured 4/6 failure rate as a concrete
reason to double-check before finalizing. **Re-verified: 12/12 correct**
across two more batches of 6 runs each after the fix, up from 2/6 before
— full regression (`dry_run_no_llm`, `pipeline_mock_test`,
`double_count_guard_test`, all 10 + 2 adversarial via `eval_queries`) also
re-confirmed clean afterward, so the new hard rule didn't shift any other
question's behavior.

---

## 5. Verification history (real, verbatim results — condensed but complete)

### 5.1 Locked folders — untouched, every session (through this handoff — see §0 and §10.2/§10.3 for the one later exception)

The repo root (one level above `New folder/`) was `git init`'d before any chatbot code was written,
with a baseline commit of the raw workbook + all 4 locked folders, plus a
separate SHA-256 hash manifest as a second, independent check. That
SHA-256 re-check has been re-run after every change across every session
of this project **up through this handoff** and has returned all-`OK`
(28/28 tracked files, zero mismatches) **every single time** — see §6. A
later session did deliberately edit `DCB_Semantic_Layer/example_queries.sql`
(purely additive, no `.sqlite` file in any of the 4 folders ever touched);
see §0's own note and §10.2/§10.3 for exactly what changed and why. This
subsection is left as the real, verbatim record of what was true *at this
handoff specifically* — not edited to retroactively read as still 100%
true today.

### 5.2 Non-LLM components — `tests/dry_run_no_llm.py`

Proves the DB layer, safety layer, and router independent of Gemini
(no API key needed):
- **DB opens read-only, and a write is physically blocked at the
  connection level** even bypassing the text-level safety check —
  `sqlite3.OperationalError: attempt to write a readonly database`.
- **Safety layer**: accepts a plain `SELECT`; rejects `DELETE`, `DROP`, a
  stacked `SELECT 1; DELETE ...`, `UPDATE`, and `ATTACH DATABASE`.
- **Router**: correct entity extraction on all 12 eval questions (10 real
  + 2 adversarial). Mean router latency: **0.00ms**.
- **3 reference SQL queries** re-run through `app.db.ReadOnlyDB.run()`
  match `example_queries.sql`'s own documented expected output exactly:
  **93.62%** collection efficiency (Arkalgud, 2024-25); **LT-4(a)** tops
  2023-24 demand; **Konanur** leads at **97.46%** collection efficiency
  (2025-26, the latest year).

### 5.3 Full pipeline wiring — `tests/pipeline_mock_test.py`

The LLM call is monkeypatched to a fixed, known `LLMQueryPlan` so
FastAPI routing, lifespan, DB execution, and template-fill are proven
correct without a real API key:
- `POST /api/ask_once` → `200`, `answerable: true`, template placeholder
  filled with the **real** DB value (93.62%, matching §5.2).
- `POST /api/ask` (SSE) → real event stream: `event: status`,
  `event: sql`, `event: chart` (`chart_data: null` for this non-chart
  question — see §8.4), `event: answer_chunk` (×2), `event: done` with
  `timing_ms`.
- Safety-net proof: mocked LLM plan with `sql: "DELETE FROM fact_dcb"` →
  `200` with `answerable: false`, *"Sorry, the generated query didn't
  pass the read-only safety check."* — never reached the DB.

### 5.4 Live 10-question + adversarial eval — real Gemini, real verbatim results

Every one of these has been re-run and re-confirmed multiple times across
sessions (most recently as part of this handoff, §6) with results
identical in substance every time (small millisecond-level timing
variance is normal LLM-side non-determinism, not a regression):

| # | Question | Result | Answer (verbatim, latest run) |
|---|---|---|---|
| Q1 | Collection efficiency, Arkalgud, 2024-25 | `93.62` | *"The collection efficiency in Arkalgud in 2024-25 was 93.62%."* |
| Q2 | YoY growth in total installations, whole division | 11 rows, 2015-16→2025-26 | Multi-row listing (see §5.7 truncation note — 10 of 11 shown) |
| Q3 | Top tariff category by demand, 2023-24 | `LT-4(a)`, 1,687,837,231.91 | *"...LT-4(a) (IP Sets - <=10Hp, Rural Feeder (Un-Metered)) with a total demand of 1,687,837,231.91."* |
| Q4 | Konanur outstanding balance (arrears) | `2025-26`, 30,717,819.03 | *"...Konanur currently carries an outstanding balance (arrears) of 30,717,819.03."* |
| Q5 | LT-1 vs HT-2 demand across all years | 11 rows | Multi-row listing (10 of 11 shown) |
| Q6 | Official GRAND TOTAL demand, 2025-26 | 3,541,649,882.79 demand / 3,300,093,200.76 collection | *"...GRAND TOTAL demand is 3,541,649,882.79 with a collection grand total of 3,300,093,200.76."* |
| Q7 | Best collection efficiency, latest year | `Konanur`, `97.46` | *"...Konanur with an efficiency of 97.46%."* |
| Q8 | DC/MNR installations by sub-division, 2024-25 | Holenarasipura 11,806 → Konanur 4,222 | 5-row listing, correctly non-null (was the query broken by bug 4.3) |
| Q9 | Misc. revenue trend over the years | 11 rows, peak 112,362,444.88 in 2024-25 | Multi-row listing (10 of 11 shown) |
| Q10 | Full DCB chain, Holenarasipura, 2024-25 | opening 162,003,262.47 + demand 1,034,457,103.77 − collection 942,195,118.95 = closing 254,265,247.29 | Hand-checked: the arithmetic invariant holds exactly. |
| QA1 | Nonexistent tariff code `ZZ-99` | `answerable: false` | *"I don't see a tariff code matching 'ZZ-99' in the database..."* |
| QA2 | Explicit attempt to double-count Division_Total | `answerable: false` | *"...would double-count the data since Division_Total already represents the sum of all sub-divisions..."* |

**Latency baseline** (established first eval; re-confirmed within normal
variance on every subsequent run, most recently §6):
- Mean total latency per question: **~1,343.8 ms** (first run); most
  recent confirmation run: **1,352.8 ms** (min 1,078 / max 1,516 ms).
- **The LLM call is ~99.6% of total latency** — every other stage
  (router, safety check, DB query, template fill) is consistently 0–16 ms
  combined. This confirms the architecture's core bet: single-call +
  pruning + persistent-connection design keeps the app's own overhead
  negligible, so total latency is essentially just Gemini's own response
  time.
- One benign SDK warning on every call, not an error: *"Direct use of
  automatic function calling (AFC) in AsyncModels.generate_content is
  not recommended..."* — advisory about an unused code path
  (function-calling); harmless, this app never registers tools/functions
  with Gemini.

### 5.5 Division_Total double-counting — code-enforced (not just prompted)

**Design:** `app/sql_safety.py::check_division_total_double_counting()`
(called from `validate_readonly_sql()`, so every query goes through it)
triggers only when a query both (a) references the raw `fact_dcb` table
(not the safe-by-construction `fact_dcb_subdivisions` view, and not the
differently-grained `fact_dcb_rollups`/`fact_dcb_grand_totals`, out of
scope for this guard) and (b) uses `SUM`/`COUNT`/`AVG`/`TOTAL`. It then
checks for one of several whitelisted patterns that provably prevent
`Division_Total` from blending with other sub-divisions in the same
aggregate: a single `subdivision_name`/`subdivision_type` equality (safe
regardless of value, since `subdivision_name` is `UNIQUE`); an explicit
`!= 'Division_Total'`/`!= 'division'`; an `IN (...)` list excluding
those values; a single `subdivision_id = <int>`; or `GROUP BY` on any
subdivision-identifying column. **It fails closed** — if it can't
positively prove the query safe, it refuses to execute rather than
guessing a "corrected" filter.

**Proof — `tests/double_count_guard_test.py`, code only, no LLM
involved:** 6 constructed dangerous cases (the exact shape that
motivated this fix, a bare unfiltered `SUM`, the literal QA2 double-count
ask reconstructed as raw SQL, `COUNT` with no filter, a filter that
*looks* safe but still includes `'division'`, two `subdivision_name`
equalities combined via `OR`) — all correctly **blocked**. 7 real safe
shapes (single-subdivision filter, `Division_Total`-only filter, a
correctly-scoped `IN (...)`, `GROUP BY subdivision_name`, a
non-aggregate query, the `fact_dcb_subdivisions` view, and
`fact_dcb_rollups`/`fact_dcb_grand_totals`) — all correctly **accepted**.
**13/13 cases pass**, confirmed again as part of this handoff (§6).

Full regression after this fix landed: all 10 example questions + both
adversarial re-run through the real pipeline, results identical in
substance to the pre-fix baseline (confirming the guard doesn't change
behavior for correct queries, only blocks the incorrect ones).

**Honestly-scoped remaining gaps** (documented in the guard's own
module-level docstring, not hidden):
1. It's a text-level heuristic, not a real SQL parser — it doesn't
   understand boolean structure across *different* columns. A
   hypothetical `subdivision_type = 'sub_division' OR subdivision_name =
   'Division_Total'` could in principle slip through, since each
   column's filter is checked independently. No query this app's prompt
   has ever produced combines filters with `OR` across different
   columns, so this is theoretical, not observed.
2. `subdivision_id IN (...)` with raw integers is blocked outright
   rather than analyzed (judging it safe would need a DB lookup this
   deliberately DB-free, fast text check avoids).
3. This guard is scoped to `fact_dcb` specifically. The same *category*
   of risk (mixing grains/rollup-types) could in principle exist for
   `fact_dcb_rollups` too (e.g. summing multiple `rollup_type` values
   together) and is not covered — no such failure has been observed in
   this project's testing.

### 5.6 Optional second LLM provider — added, then fully reverted

An alternative, OpenAI-compatible LLM provider path (`LLM_PROVIDER` /
`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_TLS_VERIFY` /
`LLM_TIMEOUT`, matching the sibling `D:\BLH-BESCOM` project's convention)
was added for latency comparison — fully wired end-to-end
(`app/config.py` settings + `app/llm.py` dispatcher,
`_generate_query_plan_alt()`, client init via the `openai` SDK) and
pointed at a Kompact-AI-style internal endpoint
(`Qwen/Qwen2.5-Coder-7B-Instruct`). It was never proven to work
end-to-end through this app in any session (the one live smoke-test
attempt was interrupted before it ran); the user's own out-of-session
testing against that endpoint found it too slow to be worth pursuing, so
the whole addition was **reverted**. **Current state, confirmed by direct
inspection at handoff time:**
- `app/config.py` has exactly two LLM-related settings:
  `gemini_api_key`, `gemini_model`. No `llm_provider`/`llm_base_url`/etc.
  fields, no `field_validator`s for blank-string bool/float handling
  (that whole class of bug no longer exists because the fields that
  triggered it are gone), no `alt_provider_active` property.
- `app/llm.py` has only the Gemini path — no `_alt_client`, no
  `_generate_query_plan_alt`, no `_validate_alt_provider_config`, no
  `openai` import.
- `.env` / `.env.example` have exactly `GEMINI_API_KEY` /
  `GEMINI_MODEL` — no `LLM_*` variables left in either file.
- `requirements.txt` no longer lists `openai`.
- `tests/alt_provider_config_test.py` is deleted.
- Post-revert full regression (all 10 + adversarial) matched the
  established baseline exactly. Gemini is the only provider going
  forward — there is no toggle, no dead code path, nothing partially
  wired.

### 5.7 Chat UI — `GET /` (previously a 404, only `/docs` worked)

Added `app/static/chat.html` (self-contained static page, no
templating dependency — everything comes from client-side calls to
`/api/ask` and `/health`) and `GET / -> HTMLResponse(...)` in
`app/main.py`. Layout (sidebar + main chat + composer, message bubbles,
"Try asking" suggestion chips from the real `example_queries.sql`
questions, a status indicator, a response timer, collapsible "Generated
SQL" and timing-breakdown sections) follows the same pattern as the
sibling `D:\BLH-BESCOM` project's chat UI for a consistent house style,
but is a new, self-contained file — not copied — and parses this app's
own real SSE event shapes (`event: status/sql/answer_chunk/done`).
Branding: deep navy (`#003a70`/`#0056a3`) primary, amber accent
(`#f5a623`), a plain emoji mark (no fabricated CESCOM logo file — none
was available to reference honestly).

**Real verification, through the actual rendered UI in a browser:**
`GET /` → `200` (previously `404`); 3 real questions sent through the
live UI all matched previously-verified figures exactly (93.62%
Arkalgud, the Holenarasipura DCB chain, LT-4(a) top tariff); 1
adversarial question rendered a distinct amber "not answerable" pill
with the honest refusal text, no blank state or broken rendering;
existing endpoints (`/health`, `/docs`, `/api/ask_once`) all re-confirmed
unchanged. `app/pipeline.py`, `app/sql_safety.py`, `app/semantic_layer.py`
were not touched for this change.

**A known, documented limitation from this feature at the time:** when a
query legitimately returns many rows, the UI/backend showed a generic
**"showing N of M rows"** truncation and listed at most 10 — deterministic
and honest (it never hid that truncation happened), but not a polished
per-question chart or table view, and a bad fit for this dataset
specifically: `dim_fiscal_year` has 11 rows, so *every* full-history trend
answer was truncated by exactly one row, always. **Fixed in §10.1** — the
cap is 20 now, chosen to comfortably cover an 11-year trend with headroom
to grow, while still bounding a genuinely large listing (e.g. all ~136
tariff categories). The underlying "generic listing, not a polished
per-question view" gap is still real (see §7) — only the specific
off-by-a-fixed-amount truncation is what §10.1 fixed.

---

## 6. Final regression at this handoff (real, run just before this commit)

This handoff is the UI redesign (§8): government-portal shell +
conversational interaction + inline charts, plus the bug 4.7 fix it
surfaced. Full regression re-run after every change in this round,
most recently just before this commit:

- `python -m tests.dry_run_no_llm` — pass, all assertions correct
  (93.62% / LT-4(a) / Konanur 97.46% all reconfirmed).
- `python -m tests.pipeline_mock_test` — pass, SSE stream + safety-net
  proof both correct. The stream now includes the `chart` event
  (§8.4) — `chart_data: null` for this non-chart question, correctly.
- `python -m tests.double_count_guard_test` — pass, **13/13 cases**
  correct. Unaffected by this round's changes (`sql_safety.py` untouched).
- `python -m tests.eval_queries` (live Gemini, all 10 + 2 adversarial) —
  pass. Mean total latency **1,495.4 ms** (min 1,140.8 / max 1,981.2 ms) —
  a modest, expected increase from the longer prompt (chart_data
  instructions + the new DC/MNR hard rule), not a regression; still
  consistent with the original ~1,343.8 ms baseline's order of magnitude.
  Q1 = 93.62%, Q7 = Konanur 97.46%, Q8 = Holenarasipura 11,806 (bug 4.7,
  fixed and re-verified — see below), both adversarial refusals correct,
  Q2/Q5/Q8/Q9's `chart_data` all present with real values matching their
  own rows exactly, all other figures identical in substance to §5.4.
- **Bug 4.7 fix, verified with real numbers, not assumed:** before the
  `app/prompts.py` fix, 6 consecutive live runs of Q8 measured **2/6
  correct, 4/6 wrong**. After the fix, **18/18 correct** across three
  more batches of 6 (including one batch re-run after trimming the fix's
  wording — see below) — zero wrong since.
- **Prompt-hygiene check on the bug 4.7 fix itself:** the first version
  of the new hard rule in `app/prompts.py` baked the measured "4 of 6"
  failure-rate statistic directly into `SYSTEM_INSTRUCTIONS`, meaning it
  would have been sent to Gemini on every single live request going
  forward — a historical finding, not a durable instruction. Caught
  before this handoff; trimmed to keep only the durable instruction
  (name all three columns explicitly, always COALESCE them) in the live
  prompt, with the measured rate now living only in bug 4.7's entry
  above. Re-verified correct (6/6) after trimming, and the full non-LLM
  suite re-confirmed unaffected.
- Locked-folder check (§0 ritual), immediately before this commit:
  `git status --short` — nothing under `New folder/DCB_*`; `git diff`
  against the baseline commit (`8f2596d`) for all 4 folders — empty;
  independent check via git's own blob object IDs (not raw `sha256sum`,
  which gives false-positive mismatches on this machine from Windows
  `core.autocrlf` line-ending normalization on checkout — confirmed as a
  normalization artifact, not real content drift, then re-checked the
  correct way): **28/28 `OK`, zero real mismatches**.
- `.env` confirmed **not tracked** by git (`git ls-files` — no match);
  `.env.example` confirmed to contain only placeholder values.
- `git status` (from the repo root) — clean of anything unexpected at
  commit time (only this round's real changes staged).

---

## 7. Known limitations (honest, not hidden)

- **Single shared SQLite connection, serialized.** Concurrent requests
  queue rather than truly parallelize at the DB layer. Fine for a POC's
  traffic; would need a real connection pool (or WAL mode with multiple
  reader connections) to scale beyond that.
- **Multi-row template fallback is generic, not model-authored.** When a
  query legitimately returns many rows, `_fill_template` falls back to a
  generic `"1. col=val, col=val\n2. ..."` listing (capped at 10 rows,
  with an honest "showing N of M" note) instead of a per-question chart
  or table. Deliberate trade-off to keep the fill step deterministic and
  LLM-call-free — see §8, this is exactly what the planned UI redesign
  targets.
- **SSE "streaming" streams the already-assembled answer, not live LLM
  tokens.** Gemini is called once in structured-JSON mode, which returns
  the whole object at once — there's no partial-JSON token stream to
  relay. What's streamed is the deterministic template-fill output,
  chunked so the UI can render progressively; it's real SSE, just not
  token-level LLM streaming.
- **Row cap (default 200) is not injected into the SQL text** — it's
  enforced by `fetchmany(cap+1)` after execution, so a query that itself
  computes an expensive full aggregate before the cap still pays that
  cost. Deliberate (safer against breaking `ORDER BY`/window queries than
  text-level `LIMIT` injection); the wall-clock timeout is the real cost
  bound.
- **Grounding-rule compliance is prompted for all rules, code-enforced
  for one.** The Division_Total double-counting rule is code-enforced
  (§5.5). Other grounding rules (e.g. never mixing
  `fact_dcb`/`fact_dcb_rollups`) remain prompt-only — no live-LLM run has
  yet demonstrated a real violation of those the way it did for
  Division_Total, so no equivalent guard has been built for them yet.
  Should one ever be observed, the same fail-closed pattern in
  `sql_safety.py` is the template to follow.
- **Abandoned: the optional second LLM provider** (§5.6) — not a
  limitation of the current app, but worth knowing it was tried: an
  OpenAI-compatible endpoint was fully wired, found too slow in
  real-world testing, and completely removed. If a future session wants
  to try a different alternative provider, there is no leftover
  scaffolding to build on — it would start from scratch, cleanly.

## 8. UI redesign — built and verified (government-portal shell, conversational interaction, inline charts, three-pane layout)

§7 (previous revision of this file) listed three standalone direction
options with no decision made. **The decision made: not picking one of
the three — combining two, by role.** Government-portal style governs
the shell/chrome (header, branding, accessibility toolbar); the
conversational/Perplexity-style pattern governs the interaction itself
(bubbles, chips, inline charts). The "data-terminal/Linear-style"
direction was not used. All of the below is real, built, and verified —
not a plan.

### 8.1 Shell/chrome — government-portal style

`app/static/chat.html`'s header now matches the layout convention of
`cescmysore.karnataka.gov.in`: two logos side by side (Karnataka state
emblem + the CESC department mark) with department title text between
them, an explicit-text-labeled accessibility toolbar (no icon-only
controls anywhere), and a light theme throughout.

- **Logos:** `cescom_official_logo.png` (Karnataka state emblem) and
  `cesc_logo_reference.png` (CESC, Mysore's circular department mark),
  both placed in `app/static/`. `cesc_logo_reference.png` was cropped
  from a third-party source, not pulled from the `.gov.in` domain itself
  — flagged in a code comment directly above its `<img>` tag in
  `chat.html`, so this isn't mistaken for an official file later; swap it
  for one if an official source is ever found. **Still genuinely open**
  — see §8.5.
- **Static file serving:** `app/main.py` previously had no
  `StaticFiles` mount at all (only `GET /` reading `chat.html`'s text
  directly) — added `app.mount("/static", StaticFiles(...))`, additive
  only, the existing routes are untouched.
- **Accessibility toolbar**, matching what the real CESC site has:
  - Font-size stepper (A− / A / A+) — adjusts a `--font-scale` CSS
    custom property on `:root`; because nearly all text sizing on the
    page is expressed in `rem`, this one property scales the page's
    typography together.
  - High-contrast toggle — swaps the palette (below) for plain
    black/white with thicker borders and no gradients/shadows, via a
    `body.hc` class.
  - EN/Kannada language toggle — **scoped honestly, by design**: it
    swaps UI chrome text only (labels, buttons, headers) via a small
    static string table (`I18N` in `chat.html`). It does **not** attempt
    to translate the chatbot's own generated answers, the generated SQL,
    or the user's typed question — those stay in English (the LLM
    prompt/semantic layer/DB are English-only). A code comment directly
    above the `I18N` table says so explicitly, so this isn't mistaken
    for full i18n later.

### 8.2 Palette — sampled from the real logo files, not the old placeholder

Old navy(`#003a70`/`#0056a3`)+amber(`#f5a623`) scheme replaced entirely.
New: **navy `#1a2f5c`** (primary, sampled from the CESC mark's blue
disc) on a white/very-light-gray background, **red `#c0202c`** (from the
wordmark) used *only* as a sparing accent — error states, the "not
answerable" pill — never decoratively. **WCAG contrast, computed
directly, not assumed:** navy-on-white ≈ 13.1:1 (AAA), red-on-white ≈
6.0:1 (AA, both normal and large text) — both comfortably clear passes.
High-contrast mode overrides every color variable to plain black/white.

### 8.3 Conversational interaction pattern

Kept the existing chat.html's bones (message bubbles, generous
whitespace, the conversation as the primary visual object) and restyled
to the new palette. The "Generated SQL" + timing-breakdown panel is
reframed as a collapsed-by-default "Technical details (SQL & timing)"
disclosure for reviewers/auditors, not part of the default visual
language.

**Superseded by §8.6 below:** this subsection originally said the old
dark sidebar was dropped entirely and the suggestion chips lived as a
horizontal row above the composer. A later pass (§8.6) brought the
sidebar back — purposefully this time, not empty nav — as part of a
three-pane layout, and the chips now live there instead.

### 8.4 Inline charts — real design, not the one originally specified

**The `chart_data` feature works, but not with the exact literal shape
first specified** (`{"type", "labels": [...], "values": [...],
"series_label"}` authored directly by the LLM) — that shape was tried
and, on live-eval testing, produced either fabricated numbers or empty
arrays on every multi-row question (below). The final design:

- **`app/models.py`:** `LLMQueryPlan.chart_data` is a `ChartSpec` — the
  LLM names `type` ("bar"/"line"), `label_column`, `value_column` (two
  of its own SELECT's column aliases), and `series_label`. It is a
  **reference to columns, not literal data** — mirroring
  `answer_template`'s `{placeholder}` mechanism (design point #1): the
  LLM produces this in the same single call as `sql`, before the query
  has run, so it cannot know real result values any more than
  `answer_template` can.
- **`app/pipeline.py::_build_chart_data()`** builds the real `ChartData`
  (the original `{type, labels, values, series_label}` shape — this part
  of the spec is unchanged, it's what reaches the frontend) deterministically
  from the actual query result after it runs. Fails closed to `None` — no
  chart, not a guess — if `label_column`/`value_column` don't match a real
  column, or `value_column` isn't numeric.
- **Why:** a live-eval run with the original literal-array design showed,
  on every multi-row question tested: Q2 got `values=[120000.0, 122000.0,
  ...]` — fabricated, not matching the real rows
  (`159149.0, 166060.0, ...`) at all; Q5, Q8, Q9 all got
  `labels=[] values=[]` — empty. The one-LLM-call architecture (design
  point #1) exists specifically so the model is never trusted to author
  real numbers; the original `chart_data` design broke that invariant,
  the column-reference redesign restores it.
- **`app/prompts.py`** tells the model to populate `chart_data` only for
  genuinely comparison-/trend-shaped questions (year-over-year, X vs Y,
  multi-row breakdowns), leaving it null for single-value answers.
- **Frontend:** Chart.js (CDN, `cdnjs.cloudflare.com`, pinned to the real
  published version `4.5.1` — see bug (b) below), styled entirely in
  navy/gray — no Chart.js default rainbow colors, red reserved for error
  states only per §8.2. **Originally rendered above the answer text in
  the same bubble** (Perplexity-style placement) — §8.6 later moved this
  to a dedicated right-hand chart panel instead, larger and more
  prominent; see §8.6 for the current placement, this subsection's own
  "Verified results" table below is otherwise still accurate (the
  column-reference design and its correctness guarantee didn't change).

**Three real bugs found and fixed this round** (same format as §4, not
summarized):

**(a) SSE `chart` event never emitted.** `app/pipeline.py` computed
`chart_data` correctly, and `AskResponse` (used by `POST /api/ask_once`)
carried it — but `app/main.py::ask_stream` (the SSE endpoint the live
chat UI actually calls) only ever emitted `status`/`sql`/`answer_chunk`/
`done` events; `chart_data` never reached the frontend no matter what
Chart.js code got written. **Found:** by inspection, before any frontend
code was written. **Fix:** added `yield _sse("chart", {"chart_data":
result.chart_data.model_dump() if result.chart_data else None})` right
after the `sql` event. Confirmed via a live `curl` SSE call for Q2,
showing the real event with real labels/values, before writing any
frontend code.

**(b) Wrong Chart.js CDN version → `Chart is not defined`.** The first
CDN URL used a guessed version string (`4.4.4`) that doesn't exist on
cdnjs (404); Chrome's Opaque Response Blocking silently discarded the
failed script load with no clear error until `Chart(...)` was called and
threw `ReferenceError: Chart is not defined`. **Found:** via a Playwright
console-error capture on the first in-browser test (`chart_data` arrived
correctly over SSE; the failure was purely a missing global). **Fix:**
queried cdnjs's own API for real published versions and pinned to
`4.5.1`, confirmed `200` before using it.

**(c) Bubble `textContent` overwriting the chart.** The existing
bot-message rendering set `.textContent` directly on the bubble element
for every streamed answer chunk — which would silently wipe out an
already-rendered Chart.js `<canvas>` the next time the streamed text
updated, since a chart and the streaming text lived in the same element.
**Found:** by design review, before it ever manifested as a visible
break (this one never shipped broken). **Fix (at the time):** each bot
bubble had two independent children — a `.chart-container` and a text
element — updated separately. §8.6 later removed the bubble's chart
container altogether (the chart moved to the right-hand panel), which
sidesteps this whole class of bug rather than just fixing one instance
of it — the bubble now holds only the text element, so there's nothing
left for streamed text to clobber.

**Verified results** — live in the browser (Playwright + real Chrome,
real Gemini calls, no mocking), confirmed against the real query rows,
not just "a chart appeared":

| Q | Chart type rendered | Values match real SQL rows? |
|---|---|---|
| Q1 (single value) | *(none — correctly no chart)* | n/a |
| Q2 (installations trend) | line | ✅ exact match |
| Q5 (LT-1 vs HT-2 demand) | bar | ✅ exact match |
| Q8 (DC/MNR by sub-division) | bar | ✅ exact match |
| Q9 (misc. revenue trend) | line | ✅ exact match |

Zero console errors across all five. (A first pass of this same browser
test showed false negatives for Q5/Q8/Q9/Q1 — traced to the *test
harness* matching stale "Responded in" text left over from an earlier
question in the same session, not an app bug; confirmed by testing Q5
alone on a fresh page, then fixed the harness to wait for each new
message bubble specifically.)

### 8.6 Three-pane layout — sidebar (back, purposefully) + center chat + chart panel

A follow-up pass moved from the single-column chat (§8.1–§8.4) to a
three-pane layout: left sidebar, center chat, right chart panel.
Frontend-only (`app/static/chat.html`; `app/main.py` untouched by this
pass) — no change to what data the backend sends, no locked-folder or
`db.py`/`sql_safety.py`/`semantic_layer.py`/`pipeline.py`/`llm.py`
changes. Built and verified in the same four-step cadence as §8.1–§8.4.

- **Left sidebar** — brought back after §8.3 dropped it, purposefully
  this time:
  - **Suggested questions**: the same chips from `example_queries.sql`
    (§8.3), relocated here from the composer area. Confirmed a pure
    relocation, not a behavior change — clicking one still
    populates-and-submits exactly as before.
  - **Chart history**: session-only, client-side state — a plain JS
    array (`chartHistory`, most recent first), **not** persisted
    anywhere (no backend storage, no `localStorage`) — gone on reload,
    same scoping as the conversation itself. Each entry keeps a
    reference to its own bot message bubble element, so clicking an
    entry both redisplays that chart in the panel and scrolls
    to/highlights (`.highlight-flash`, a brief box-shadow pulse) the
    corresponding exchange in the center chat.
- **Right chart panel** — replaces the inline-in-bubble chart placement
  from §8.4. When an answer includes `chart_data`, the chart renders
  here instead of in the message bubble — larger and more prominent than
  a chart squeezed into a bubble ever was. The bubble itself now holds
  only the text answer.
  - **One shared render path, deliberately**: both the live `chart` SSE
    event and clicking an older chart-history entry call the exact same
    `renderChartInPanel()`. This is what makes the obvious edge case
    "just work" rather than needing separate state-tracking: after
    manually selecting an old chart from history, asking a brand-new
    chart-bearing question still correctly switches the panel to the
    new chart, never gets stuck on the old manual pick — verified
    directly (asked Q2, then Q5, clicked back to Q2 in the sidebar, then
    asked Q8 fresh: panel ended up showing Q8's bar chart, not Q2's line
    chart). Any future chart-related feature should route through this
    same function rather than adding a second path.
  - A non-chart answer (e.g. Q1) deliberately leaves the panel
    untouched — it always shows "the most recently produced chart," not
    "a chart for the most recent message," so there's nothing to update
    to when the latest answer has none of its own.
- **Responsive collapse**: below a 1024px breakpoint, the sidebar and
  chart panel become fixed-position off-canvas drawers (slide-in
  overlays with a backdrop) instead of squeezing the center chat into a
  sliver, opened via explicit text-labeled "Menu"/"Charts" toggle
  buttons in the header (no icon-only controls, consistent with the
  accessibility toolbar's own rule). Tested at actual tablet width
  (768×1024, not a narrowed desktop window): both drawers open/close
  correctly (confirmed via real bounding-box positions, not just
  Playwright's `is_visible()`, which doesn't account for the
  off-canvas `transform: translateX()` — sidebar sits at `x:-280`,
  chart panel at `x:768` i.e. just past the viewport edge, when
  collapsed), the chat remains full-width and usable, a chart-bearing
  question renders correctly inside the opened Charts drawer, and both
  the chart history and suggestion chips work correctly from inside the
  opened Menu drawer (including submitting a question directly from a
  chip while the drawer is open).
- **Palette, real logos, accessibility toolbar, and the "Technical
  details" disclosure are unchanged** — this was a layout/information-
  architecture change, not a re-theme.

No new bugs were found or fixed during this pass — unlike §8.4's three,
this one shipped clean through all four steps. Full regression
(`dry_run_no_llm`, `pipeline_mock_test`, `double_count_guard_test`)
re-confirmed unaffected, as expected for a frontend-only change with no
backend contract change.

### 8.5 What's actually still open

- **Sourcing an official, non-third-party CESC logo file.** The
  currently-shipped `cesc_logo_reference.png` is "good enough for this
  POC," per its own in-code comment, but was cropped from a third-party
  source, not the `.gov.in` domain. Swap it if an official file is ever
  supplied.
- **"New chat" doesn't reset the chart panel or chart history** (§8.6).
  Clicking "+ New chat" clears the visible conversation, but the
  sidebar's chart-history list and the right-hand chart panel both keep
  showing whatever was there before — a history entry's bubble
  reference becomes a detached DOM node (clicking it is harmless, just a
  no-op scroll/highlight), and the panel keeps the last real chart in
  view even though it no longer corresponds to anything on screen.
  Noticed during §8.6's build, not fixed — out of that pass's explicit
  scope (chip relocation, chart history, right-panel wiring, responsive
  collapse), not asked for, and "New chat"'s intended chart-related
  semantics weren't specified. A real gap for whoever picks this up
  next, not hidden.
- **Everything else originally discussed in this section — the real
  CESCOM logo and Chart.js visualization — is done, not just planned.**
  Removed from this list; see §8.1/§8.4/§8.6 above for what was actually
  built and how it was verified.

### 8.7 Chat UI polish — welcome screen, composer, sidebar (reference: a supplied "Script" AI-chat screenshot)

A further pass restyled three specific pieces of `chat.html`, taking
cues from a reference screenshot of an unrelated AI-chat product
("Script") the project owner supplied — only the parts that actually
fit a DB Q&A tool were carried over; generic AI-assistant features from
that reference (voice message, image generation, avatar creation) were
**not** added, since faking controls with no real behavior behind them
would violate this project's own honest-UI convention.

- **Welcome screen**: the plain one-line hint text is replaced by a
  centered heading + subtext + a 2-column grid of 5 clickable suggestion
  cards (icon + label, same underlying questions as the sidebar chips) —
  shown before the first message and again after "+ New chat"
  (`renderWelcomeScreen()`), removed the instant the first question is
  actually sent.
- **Composer**: input + send button now read as one unified rounded
  pill (`.composer-box`) instead of two separately-bordered controls,
  plus a short caption below it echoing the disclosure the old hint text
  carried ("Answers come from a real SQL query… see Technical details").
- **Sidebar polish**: small icons next to the "Suggested questions" /
  "Chart history" section headings (icon+text, never icon-only, per this
  app's own accessibility rule); a **real** search box above the chart-
  history list that filters it by question text — not decorative,
  mirrors the exact search-filter convention `app/static/dashboard.html`
  (§9) already uses over its own sub-division/tariff lists; and a new
  promo card pinned to the bottom of the sidebar cross-linking to
  `/dashboard`, the reciprocal direction of the link dashboard.html's own
  sidebar promo already provides back to the chat UI.

**One real bug found and fixed:** the initial `renderWelcomeScreen()`
call was placed right after the chip-wiring code, but `I18N` (used by
`t()`, which the welcome screen calls to render its text) is declared
with `const` much further down the same script — a classic temporal-
dead-zone bug, thrown as `Cannot access 'I18N' before initialization`
and caught immediately by a live console-error check before any further
testing. **Fix:** moved the initial call to the true end of the script,
after every other top-level declaration; `renderWelcomeScreen` itself is
a hoisted function declaration, so defining it earlier in the file was
never the problem, only the misplaced *call*.

**Verified live** (Playwright + real Chrome): welcome screen renders
with all 5 cards, zero console errors after the fix; clicking a card
submits the question and removes the welcome screen; "+ New chat"
brings it back; the chart-history search filter correctly narrows a
2-entry list to 1 when searching a substring of one question; the
sidebar promo card's link resolves to `/dashboard`; the EN/Kannada
toggle correctly translates the new welcome/composer/sidebar strings
while leaving chart data untouched; high-contrast mode applies the
existing 2px-border treatment to all the new elements; the mobile
sidebar drawer (§8.6) still renders the new search box and promo card
correctly at real tablet width. Full non-LLM regression
(`dry_run_no_llm`, `pipeline_mock_test`, `double_count_guard_test`)
re-confirmed unaffected — frontend-only change, `app/main.py` untouched.

**Follow-up, found right after this shipped: "chart history isn't
working."** It wasn't broken — a fresh Playwright test asking a
comparison question populated it correctly, zero console errors. The
actual cause: all 5 suggested questions (both the sidebar chips and the
`WELCOME_CARDS` welcome-screen cards, §8.7 above) were single-value or
adversarial questions — **none of them were comparison/trend-shaped**,
so none of them ever produced `chart_data` (§8.4's whole design). A user
who only ever clicks the provided suggestions — the obvious, discoverable
thing to do — could never see the chart panel or chart history do
anything, even though the underlying feature worked correctly the whole
time. **Fix:** swapped 2 of the 5 slots (`chip2`, `chip4`, in both the
sidebar chips and `WELCOME_CARDS`) for this project's own known
chart-bearing eval questions — the LT-1 vs HT-2 comparison (Q5, bar
chart) and the year-over-year installations trend (Q2, line chart) —
with matching icons, both EN and Kannada strings updated, and each
chip/card label now says "(chart)" so it's obvious which ones demonstrate
it. Kept `chip1`/`chip3`/`chip5` (a quick single-value answer, a
real-world arrears lookup, and the adversarial nonexistent-tariff-code
demo) unchanged — still useful, still deliberately chart-free contrast.
**Verified live:** clicking the new chip2/chip4 (from both the sidebar
and the welcome screen) each populate chart history and render a real
chart in the panel; zero console errors; non-LLM regression re-confirmed
unaffected.

### 8.8 Right-hand chart panel removed, by request — charts moved back inline

**§8.6's three-pane layout (sidebar / center chat / right chart panel) no
longer exists.** By explicit request ("remove that [right part] and show
the graphical representation in the chat only"), the dedicated chart
panel is gone; charts render inline again, above the answer text within
their own message bubble — the same placement §8.4 originally shipped,
before §8.6 moved it to the panel. This app is back to a two-pane layout:
sidebar (suggested questions, chart history, dashboard promo — all
unchanged) + center chat, now given the full width the panel used to
share.

- **What changed:** `renderChartInPanel()` and `#chartPanelBody` are
  gone. `send()` restores the pre-§8.6 pattern: each bot message bubble
  gets its own `.chart-container` div (created hidden, shown only if
  `chart_data` arrives), rendered via the same `renderChart()` both
  versions always shared. The `#chartToggle` ("Charts") header button and
  its drawer/backdrop wiring are removed — only the sidebar's "Menu"
  drawer remains at narrow widths, confirmed via bounding-box position
  (collapsed `x:-280`, open `x:0`) same as before.
- **Chart history's role changed along with it:** since each past chart
  now lives permanently in its own message bubble (not swapped in and
  out of one shared panel), clicking a history entry no longer needs to
  redraw anything — `selectChartHistoryEntry()` now only scrolls to and
  highlights (`.highlight-flash`) the exchange it came from. Simpler than
  §8.6's version, and the "asking a new chart-bearing question after
  clicking an old history entry" edge case §8.6 had to reason about
  doesn't exist anymore either — there's no shared "current chart" state
  left to get stuck.
- **Unused CSS/i18n removed alongside it:** `.chart-panel*` rules, the
  `.chart-container.panel` size modifier, and the `chartToggle`/
  `chartPanelTitle`/`chartPanelEmpty` string-table entries (both
  languages) — not left behind as dead code.

**Verified live:** a chart-bearing question renders its chart inside that
message's own bubble (confirmed via `.chart-container canvas` scoped to
that specific `.msg.bot`, not just present somewhere on the page); a
non-chart question's bubble has no chart container at all; clicking a
chart-history entry scrolls to and highlights the right bubble; the
sidebar drawer still collapses/opens correctly at real tablet width
(768px) with the "Charts" button confirmed gone. Zero console errors.
Full non-LLM regression re-confirmed unaffected — frontend-only change.

### 8.9 Chart history removed from the sidebar entirely, by request

**The sidebar's "Chart history" feature (§8.6/§8.8) is gone** — no
list of past charts, no search box over it, no click-to-scroll/
highlight. The sidebar now holds only "Suggested questions" (chips) and
the dashboard promo card. Inline chart rendering itself (§8.8 — each
chart-bearing answer's chart lives in its own message bubble) is
**unaffected**: charts still render exactly the same way, there's just
no separate index of them anymore.

- Removed: the `chartHistoryList` sidebar section (heading, search
  input, list), `renderChartHistoryList()`, `applyHistorySearchFilter()`,
  `addChartHistoryEntry()`, `selectChartHistoryEntry()`, the
  `chartHistory` array and `historySearchQuery` state, the
  `.highlight-flash` bubble animation (nothing calls it anymore), and
  the now-unused `sidebarChartHistory`/`sidebarNoCharts`/
  `historyChartBar`/`historyChartLine`/`historySearchPlaceholder` string-
  table entries in both languages — not left behind as dead code.
  `send()`'s `chart` event handler goes back to a single line
  (`renderChart(chartContainer, obj.chart_data)`), no history bookkeeping.
- Left alone: `renderChart()`, the per-bubble `.chart-container`, the
  sidebar's suggested-question chips (`chip2`/`chip4` still say
  "(chart)" and still demonstrate the feature, per §8.7's
  discoverability fix — that fix wasn't about history, it still applies).

**Verified live:** the sidebar has no "Chart history" heading or list at
all; a chart-bearing question's chart still renders correctly inside its
own message bubble exactly as before; zero console errors on load or
after asking. Full non-LLM regression re-confirmed unaffected —
frontend-only change.

---

## 9. Dashboard + login gate — built and live-verified (see §9.3)

Added on request: a summary dashboard (KPIs, demand trend, top
sub-divisions/tariff categories) modeled structurally on a reference
screenshot of an unrelated project-management template ("Donezo") the
project owner supplied — same shell shape (left sidebar, topbar with
search, a KPI row, two 3-column card rows, a floating action button, a
promo card pinned to the bottom of the sidebar) — with every number and
list re-derived from this project's own real data, and a login page in
front of it since the reference and the request both implied one.
Entirely additive: no existing route, file, or behavior changed except
`app/main.py` (new routes only) and `app/config.py` (new settings only).

### 9.1 New files

- `app/auth.py` — signed-cookie session (stdlib `hmac`/`hashlib`/`base64`
  only, no new dependency). **Originally one shared username/password;
  by explicit request, the password check was removed entirely — see
  the update below this subsection.** No user table, no password
  hashing, no CSRF token, no rate limiting regardless. **This is a POC
  gate, not real auth** — see its own docstring before this app is ever
  reachable by anyone but one person on one laptop.
- `app/dashboard_data.py` — the dashboard's only DB access: fixed,
  hand-written aggregate queries via `app.db.ReadOnlyDB.run` (the same
  one persistent connection everything else uses — no second connection
  opened). Follows `DCB_Semantic_Layer/semantic_layer.md`'s rules exactly
  like a correct hand-written query would (division total via
  `subdivision_type = 'division'`, never `fact_dcb` summed with
  `fact_dcb_rollups`, efficiency recomputed from summed totals, never
  averaged from `pct_collection_efficiency_with_adj`). Not LLM-generated,
  so `app.sql_safety`'s guard doesn't apply here — reviewed once, by hand.
- `app/static/login.html`, `app/static/dashboard.html` — same navy/red
  palette as `chat.html` (§8.2), kept in both files' `:root` blocks; if
  one changes, mirror the change in the other two.

### 9.2 What the dashboard actually shows (all real, all live-queried)

- 4 KPI cards for the latest fiscal year (2025-26 as of this build):
  Total Demand, Total Collection, Outstanding Balance, Collection
  Efficiency — each with a real YoY delta against 2024-25, colored by
  whether that direction is actually good (up is good for
  demand/collection, bad for outstanding balance).
- A demand-trend bar chart across all 11 fiscal years (division total).
- The 5 real sub-divisions (`subdivision_type IN ('sub_division',
  'section')`, i.e. excluding `Division_Total`/`Division_Office` per
  semantic-layer rule 2), each with its own collection-efficiency badge.
- The top 5 tariff codes by demand for the latest year, with each one's
  share of total demand.
- A "Data integrity" tile citing the pipeline's own verified 0.0%
  reconciliation deviation (§5.1) — a static historical fact from the
  locked pipeline's build, not a live-recomputed number.
- An "Ask DCB AI" card and a sidebar promo card, both linking back to
  `GET /` — the dashboard and the chat UI cross-link each other rather
  than being two disconnected surfaces.

### 9.3 What was and wasn't verified, honestly

This was originally built and reviewed in an environment with no
outbound access to PyPI, so §9's routes couldn't be exercised through a
real `uvicorn` process there — see the superseded version of this
subsection in git history for exactly what that first pass did verify
(direct calls to `build_dashboard_summary`/`create_session_token`, a
mock-server Playwright pass, the non-LLM test suite). **That one real
gap has since been closed, in a different session/environment with
working network access:**

- **Full live click-through, real `uvicorn`, real cookies, real HTTP**
  (Playwright driving real Chrome against `http://127.0.0.1:8001`):
  hitting `/dashboard` unauthenticated correctly redirects to `/login`;
  logging in with the real (default) credentials redirects to
  `/dashboard` and loads real data; logging out redirects to `/login`;
  **hitting `/dashboard` again after logout redirects to `/login`
  again** — proving the session cookie is actually cleared server-side,
  not just a client-side redirect.
- **KPI numbers cross-checked against this file's own long-established
  baseline (§5.4/§6), not just "a number appeared":** Total Demand
  ₹354.16 Cr, Total Collection ₹330.01 Cr, Collection Efficiency 93.2% —
  these are `3,541,649,882.79` and `3,300,093,200.76` (§5.4's Q6/§6
  verified FY2025-26 grand totals) converted to crore and divided,
  computed independently and matched exactly, not eyeballed.
- 5 subdivision rows, 5 tariff rows, 2 canvases (demand bar chart +
  efficiency donut) all rendered with real data.
- Client-side search filter tested for real: typing "Konanur" correctly
  narrows the sub-division list to 1 visible row.
- Mobile drawer tested at a real narrow viewport (700px): sidebar
  correctly off-canvas by default (bounding box `x:-260`), correctly
  slides on-screen on toggle (`x:0`).
- The "Ask DCB AI" cross-link was clicked for real and correctly lands
  on the chat UI (`GET /`).
- Zero console/page errors across the entire click-through.
- `tests/dry_run_no_llm`, `tests/double_count_guard_test`, and
  `tests/pipeline_mock_test` (this last one specifically couldn't be run
  in the original environment) all re-run in this session — all three
  still pass unchanged, confirming the dashboard addition doesn't touch
  the chat pipeline, `sql_safety`, or the DB layer's read-only guard.

This feature is now held to the same standard as everything else in
this file (§5, §6): live-verified, not just evidenced.

### 9.4 Known limitations (in addition to §7 and §8.5)

- **Login is now an open gate, not a real check — see §9.5.** The
  bullet below is what was true when this dashboard first shipped; it
  no longer is.
- ~~Single shared login, not per-user accounts — see `app/auth.py`.~~
  Superseded by §9.5: there's no login *check* left at all now.
- The dashboard has no auto-refresh; "↻ Refresh data" (and the floating
  button) re-fetch on click only.
- `/api/ask*` remain fully unauthenticated — logging into the dashboard
  does not gate the chat endpoints, by design (kept additive/separate),
  but worth knowing if that's ever expected to change together.

### 9.5 Login gate opened up: the password check was removed, by explicit request

**Found:** the project owner tried the dashboard and got "Invalid
username or password" — the default POC credentials (`admin`/`changeme`)
were never communicated anywhere the owner would see them before hitting
the login form, so the rejection, while functioning exactly as designed,
looked like a bug. Confirmed those defaults still worked
(`curl /api/login` with `admin`/`changeme` → `{"ok":true}`) before
concluding it wasn't an actual credential/config problem.

**Change, on explicit request ("remove that and allow whatever i type in
user name and password"):** `POST /api/login` (`app/main.py`) no longer
compares against any stored credential at all. It accepts **any
non-blank username** and does not look at the password field's value —
the HTML form still requires one be typed (`required` on the input),
but the server ignores it. `Settings.dashboard_username`/
`dashboard_password` (`app/config.py`) are removed as now-unused config,
along with their `.env.example` entries — matching this project's own
convention of not leaving dead settings behind (see §5.6's alt-provider
revert for the precedent). `Settings.session_secret` stays: the session
cookie handed out is still HMAC-signed and still can't be forged or
tampered with, it just no longer certifies *who* is holding it.
`app/auth.py`'s docstring and `login.html`'s on-page hint were both
updated to say this plainly, not leave the old "one shared account"
framing standing after it stopped being true.

**Verified live:** `curl /api/login` with a made-up username/password
(`whoever` / `literally anything`) → `{"ok":true}`; blank username →
correctly rejected (`{"ok":false,"error":"Enter a username."}`, not a
silent accept). Full Playwright click-through through the real login
form with a made-up username (`sourav` / `whatever123`) → lands on
`/dashboard`, real data loads, and the topbar correctly displays
`sourav` as the signed-in name, proving the typed username actually
flows through to the session, not just accepted-and-ignored. Zero
console errors. Full non-LLM regression (`dry_run_no_llm`,
`pipeline_mock_test`, `double_count_guard_test`) re-confirmed unaffected.

**What this means in plain terms, so it isn't buried:** `/dashboard` is
now reachable by anyone who can reach this app's URL and is willing to
type anything into the login form — there is no access control left on
it at all, only a cookie that proves a session was created, not that
whoever created it was supposed to. Fine for a solo-laptop POC; revisit
before this is ever exposed anywhere another person could reach it.

### 9.6 Landing flow flipped: `GET /` now opens login → dashboard, not the chat UI

**By explicit request** ("first thing should get open is the login
page, after login next has to be the dashboard"). Before this, `GET /`
served the chat UI directly and `/dashboard` was a separate, secondary
surface reachable only by navigating there. Now:

- `GET /` — checks the session cookie and redirects: to `/login` if not
  signed in, straight to `/dashboard` if already signed in. Serves
  nothing of its own anymore, just routes.
- `GET /chat` — the chat UI, moved here from `GET /` so it's still fully
  reachable, just no longer the default landing page. Still completely
  unauthenticated — this change is scoped to the *landing flow only*, it
  does **not** gate the chat pipeline behind login. If that's also
  wanted, it hasn't been asked for yet and isn't done.
- Every internal link that used to point at `/` meaning "the chat" was
  updated to `/chat`: `login.html`'s "← Ask a question instead",
  `dashboard.html`'s "Ask DCB AI" nav link, its "Ask a question"/"Ask
  now" buttons, and its sidebar promo's "Open chat" link. The
  login→dashboard redirect on successful sign-in (`login.html`'s own
  JS) already pointed at `/dashboard` before this change — that half of
  the request was already true, only the "`/` opens login first" half
  was new.

**Verified live:** a fresh (no cookie) request to `/` redirects to
`/login`; logging in redirects to `/dashboard` with real data; a
follow-up request to `/` with the now-valid session cookie redirects
straight to `/dashboard`, skipping login, as intended; `/chat` still
serves the full chat UI unauthenticated; all four updated cross-links
resolve to `/chat`, not a dead `/`. Full non-LLM regression
(`dry_run_no_llm`, `pipeline_mock_test`, `double_count_guard_test`)
re-confirmed unaffected — this is a routing change, not a chat-pipeline
change.

### 9.7 Sub-divisions panel: efficiency-% badge replaced with a collection-vs-outstanding bar

**By explicit request, through three design iterations in one sitting —
final shape below, earlier ones kept here only as real history, not
followed.** Each row in the dashboard's "Sub-divisions" panel used to end
in one `<span class="row-badge">` showing `efficiency_pct` as a colored
percentage pill (`effBadgeClass()`: green ≥95%, amber ≥90%, red below).
That's gone. `data.subdivisions` already carried both `demand_total` and
`collection_grand_total` per sub-division (from `GET
/api/dashboard_summary`) — this stayed a `dashboard.html`-only change
throughout all three iterations; nothing in `app/dashboard_data.py`,
`app/main.py`, or any of the 4 locked folders was touched.

**What actually shipped:** one bar per row, sitting BELOW the row's
icon/name/subtitle (not trailing beside them), split into two side-by-side
segments that together make up the whole bar — green (`--good`) =
`collection_grand_total`, red (`--red`) = outstanding balance for the
current fiscal year, i.e. `demand_total - collection_grand_total` (not
`closing_balance_total`, which is cumulative arrears across years — this
matches the "Collection efficiency" donut chart's own convention
elsewhere on the same dashboard, just per sub-division instead of
division-wide). `row-sub` now reads e.g. "₹115.38 Cr collected · ₹10.35
Cr outstanding" instead of the old "₹125.73 Cr demand".

- **Two earlier shapes, both replaced, not layered:** the first version
  put two separate bars (Demand + Collection) trailing the row, matching
  the request as originally written; a follow-up ("i don't need two diff
  bars make it one only... below subdivision only not in the side") cut
  that to one bar below the row with collection overlaid inside a demand-
  length track; a second follow-up ("green part for collection and red
  part for balance... show in red and green" inside one bar) corrected
  that overlay to the side-by-side split described above, which is what's
  actually in the file now. Each iteration fully replaced the previous
  one's CSS/markup, not layered on top of it.
- **Scaling: superseded by §9.8, corrected below rather than left
  standing.** This bullet originally said every row's bar length was
  normalized against one shared `maxDemand` across all 5 sub-divisions,
  so total bar length encoded relative demand size and only the
  collection/outstanding split within that length was row-specific. That
  shipped, was verified, and was real — but it also meant every row
  except the single largest sub-division rendered with a visibly empty,
  unfilled tail. Reported live as a bug ("why there r empty bar... shud
  be filled in green and red") and fixed in §9.8: every row's bar now
  always fills 100%, split by collected/outstanding as a % of *that
  row's own* `demand_total`, not a shared scale. `barPct(value, denom)`
  still does the clamped percentage math and still replaces the
  now-fully-unused `effBadgeClass()` (removed, not left as dead code) —
  only what gets passed as `denom` changed.
- **Red for the outstanding segment is a deliberate, one-off exception**,
  called out in a CSS comment right above `.row-bar-fill.balance` — this
  file's usual convention (`.badge-bad`, `.load-error`, etc.) reserves
  `--red` for actual error/bad states. Here it's paired with `--good`
  purely as a collected-vs-outstanding color pairing, not a precedent for
  using red elsewhere for anything that isn't a real error.
- **High-contrast mode — asked about, not built.** The request said to
  follow "the same override pattern the `:root/.hc` block already uses
  ... in this file," but `dashboard.html` has no high-contrast mode at
  all (no `.hc` class, no toggle) — that infrastructure exists only in
  `chat.html` (`#contrastToggle` + `body.hc`), confirmed by grepping both
  files before writing any code. Flagged to the project owner rather than
  either silently inventing a toggle dashboard.html doesn't have or
  silently skipping the requirement; told explicitly to skip it and use
  solid, already-accessible colors instead — which is what shipped. If a
  high-contrast mode is ever added to `dashboard.html` (porting
  `chat.html`'s), this widget still needs its own `.hc` override then.
- **The narrow-panel truncation problem from the first two-bars-trailing
  iteration is now moot.** The Sub-divisions panel is the narrowest of 3
  columns in that row (`grid-template-columns:1.6fr 1fr 1fr`); putting
  two bars + labels in a fixed-width trailing slot there had forced real
  sub-division names to truncate hard ("Hol…", "Ark…"). Moving the (now
  single) bar to its own full-width line below the row sidesteps that
  entirely — `row-title` now shows every sub-division's full name with
  room to spare, confirmed live (screenshot), not just reasoned about.

**Verified live**, real `uvicorn` instance, real login, real
`/api/dashboard_summary` data (not mocked) — via a Playwright script
(same tool used for this project's earlier regression-testing work, not
a new dependency), re-run after each of the three iterations landed, not
just the final one: all 5 sub-divisions render one correctly-colored,
correctly-proportioned bar each, its two segments' `left`/`width` meeting
exactly (e.g. Holenarasipura: `91.77% + 8.23% = 100%`) — verified against
the actual rendered inline `style` values, not eyeballed; **zero browser
console errors or page errors** on every pass (Playwright's `console`/
`pageerror` listeners). High-contrast mode was not re-verified for the
reason above — there is none in this file to verify. Full non-LLM
regression (`dry_run_no_llm`, `pipeline_mock_test`,
`double_count_guard_test`) re-run from `dcb-ai-app/` after the final
iteration and re-confirmed unaffected — this is a `dashboard.html`-only
change, nothing in the request/router/safety path was touched.

### 9.8 Sub-divisions bar's empty-tail bug, then a top-area layout reorganization, both by explicit request in one sitting

**Part 1 — the empty-bar bug from §9.7's own shared-scale design.**
Reported live, plainly: *"why there r empty bar inside Arkalgud,
Ramanathapura, Hangarahalli, Konanur there shud be filled in green and
red part not the empty one."* Root cause was exactly what §9.7's own
"One shared scale" bullet had described and verified as working-as-
designed: every row's bar length was `demand_total / maxDemand`, so only
Holenarasipura (the largest sub-division) ever reached 100% — the other
4 rows were each shorter than their track, leaving a plain, undecorated
gray tail. Correct behavior for a "compare relative demand size across
rows" bar; wrong for what was actually wanted, which was every row's bar
fully filled, split by that row's OWN collected/outstanding ratio. Fixed
by changing `barPct`'s second argument at each call site from the shared
`maxDemand` to that row's own `demand_total` — collected % + outstanding
% now always sum to 100 for every row, no shared scale left anywhere in
this widget. (Relative demand size across sub-divisions is still visible
elsewhere on the dashboard — the "Demand trend" chart, and each row's own
₹ Cr figures — so nothing was lost, just moved out of this specific bar's
job.) `git diff` on this part: `app/static/dashboard.html` only, a few
lines in `render()` plus the `barPct`/CSS comments describing the old
scale, corrected rather than left stale (see §9.7's own correction note
above pointing here).

**Part 2 — dashboard layout reorganization, by explicit numbered
request.** Five asks: (1) remove the "Collection Efficiency" KPI card
from the top KPI row (down to 3 cards: Total Demand, Total Collection,
Outstanding Balance) without touching the separate donut panel of the
same name; (2) move the Sub-divisions panel out of its old 1-of-3 slot
in `row-3col` into the space freed by that removed card, since — even
before §9.7's bug, and worse after it added full ₹ Cr text per row —
Sub-divisions never had enough width or height there for 5 clear rows;
(3) rebalance the now-2-panel Demand-trend/donut row now that it isn't
sharing a row with Sub-divisions; (4) use judgment on the exact grid
restructuring, just don't leave anything visibly lopsided; (5) don't
touch anything else. Implemented as two new CSS classes rather than
repurposing `row-3col` (which stayed exactly as-is, since the
Top-tariff-categories/Live-installations row below still genuinely uses
its 3 column tracks): `.top-area` — the new top-level grid pairing a
left column against the Sub-divisions panel on the right — and
`.row-2col` for Demand-trend + the donut. The now-orphaned "Collection
Efficiency" KPI DOM references (`#kpiEfficiency`, `#kpiEfficiencyDelta`,
and the `effDeltaPts` calculation feeding them) were removed from
`render()`, not left to silently no-op against deleted elements —
`k.collection_efficiency_pct` itself is still read, just only for the
untouched donut panel now.

**Part 2b — an immediate follow-up on Part 2, same sitting.** The first
cut of the reorganization put Demand-trend/donut in their own full-width
row *below* `.top-area`, with `.top-area{align-items:start}` deliberately
— chosen so Sub-divisions' extra height (5 rows) wouldn't stretch the
short 3-card KPI row into an awkward, mostly-empty tall row. That traded
one visible gap for another: the space beside/below the 3 KPI cards (
Sub-divisions is taller than they are) sat empty, reported live just as
plainly: *"fit in demand trend and collection efficiency in the empty
space try to fit that na."* Fixed by moving those two panels INTO the
left column, stacked below the KPI row (new wrapper `.top-area-left`,
`display:flex;flex-direction:column`), and switching `.top-area` back to
`align-items:stretch` so the Sub-divisions panel on the right fills that
combined left-column height instead of leaving a gap beside it — the
exact opposite fix direction from Part 1's #9.7 predecessor bug, but a
different empty-space problem, not a regression of the first one.

**Verified live, real `uvicorn`, real login, real data, both parts** —
via the same Playwright approach as §9.7, extended to cover every
breakpoint this file defines (1280px desktop, plus 1099/859/759/479 —
one pixel under each of the 1100/860/760/480 breakpoints):
- Console/page errors: **zero, at every width**, via Playwright's
  `console`/`pageerror` listeners (not just checked once at desktop
  width and assumed clean elsewhere).
- Horizontal overflow: **none, at every width** — checked
  programmatically (`document.documentElement.scrollWidth >
  document.documentElement.clientWidth`), not just eyeballed.
- Full-page screenshots reviewed at every width, not just the
  error/overflow checks: 3 KPI cards + Demand trend + donut all render
  with real data in the left column; Sub-divisions renders all 5
  sub-divisions with real, correctly-filled (Part 1's fix) bars on the
  right, matching the left column's height with no visible gap (Part 2b's
  fix); at ≤1100px `.top-area` correctly stacks to one column (KPI row,
  then Demand-trend/donut, then Sub-divisions, each full width); at
  ≤760px `.row-2col` also stacks; the sidebar's drawer/backdrop behavior
  at ≤760px and the KPI row's own 2-up/1-up collapses at 860/480 all
  still work exactly as before, unaffected by any of this.
- Full non-LLM regression (`dry_run_no_llm`, `pipeline_mock_test`,
  `double_count_guard_test`) re-run from `dcb-ai-app/` after both parts
  landed and re-confirmed unaffected — this stayed a
  `dashboard.html`-only change throughout; nothing in
  `app/dashboard_data.py`, `app/main.py`, the request/router/safety
  path, or any of the 4 locked folders was touched.

### 9.9 KPI card subtitles dropped; navy CESC banner merged into the white top bar

Two small, unrelated-to-each-other requests, same sitting.

**"This fiscal year" removed from the 3 KPI cards.** Each of Total
Demand / Total Collection / Outstanding Balance had a small `This fiscal
year` line under its value — dropped entirely, by explicit request, no
replacement text. Three lines removed from `dashBody`'s markup; nothing
else about those cards changed. Verified live (screenshot) and via
Playwright's console-error listener (zero) — full non-LLM regression
suite re-confirmed unaffected.

**The navy `.gov-banner` strip (CESC logo + "Chamundeshwari Electricity
Supply Corporation..." + "DCB — Demand, Collection & Balance", full-width,
sitting above the plain white `.dash-topbar`) is gone as a separate
element.** By explicit request — read literally the first message was
hard to parse ("this blue box make this place right below the white
space there in the user name section next to only place with blue bg"),
so before touching anything a clarifying question was asked with two
concrete options (swap the two rows' order, vs. merge them into one) —
answered "merge into one row." Implemented: the banner's own navy
background is gone; its logo + two text lines now sit on the LEFT of
`.dash-topbar` itself (new `.topbar-brand`), plain ink/muted text on the
same white background as the user-avatar/name/logout section already on
the right — one row instead of two, exactly matching the confirmed
option. `flex:1 1 auto; min-width:0` plus `text-overflow:ellipsis` on
both brand text lines so a long CESC name truncates gracefully rather
than pushing the user avatar off-screen on a narrow viewport; the mobile
media-query rule that used to shrink `.gov-banner` at ≤640px was ported
to `.topbar-brand`, plus a new ≤760px rule dropping the DCB subtitle
line first (same breakpoint the hamburger/drawer toggle appears at, now
sharing the row).

**Verified live**, real `uvicorn`, real login, real data, via Playwright:
screenshots at 1280px (desktop), 759px, and 400px all show one clean
white row — brand text on the left, user info + avatar on the right, no
leftover navy strip; **zero console/page errors and zero horizontal
overflow at every one of those widths** (checked programmatically, not
just eyeballed). Full non-LLM regression (`dry_run_no_llm`,
`pipeline_mock_test`, `double_count_guard_test`) re-run and re-confirmed
unaffected — `dashboard.html` only, nothing in the request/router/safety
path or any locked folder touched.

### 9.10 Demand trend chart: ₹/% toggle added

**By explicit request, with an honest discrepancy worth recording.** The
request's own framing said the Demand trend chart "currently shows only
year-over-year percentage change" and asked for the ₹ view to be "added
back." Read `renderDemandChart()` before touching anything, per the
request's own instruction not to assume — **the file's actual state at
the time had no % mode and no toggle anywhere: `renderDemandChart` showed
only the original ₹ Cr bars**, unchanged since it was first built. Grepped
the whole file for `yoy`/`growth`/`percent`/`toggle` to be sure nothing
related was hiding elsewhere — the only `_yoy_pct` hits were the
unrelated KPI-card delta badges. Whatever prior session or context the
request was recalling isn't reflected in this file as it stands. Noted
here rather than silently "fixed" as if a real regression had occurred;
the requested *end state* (a working ₹/% toggle, both views correct) is
unambiguous regardless of that history, so it was built as asked rather
than stopping to ask again over a detail that doesn't change the work.

**What shipped:** a small two-button segmented toggle (`₹` / `%`) in the
Demand-trend panel-head, styled from this file's own `.btn-solid`/
`.btn-outline` navy-solid-vs-outline pairing (new `.chart-toggle`,
sized down to fit a panel-head rather than the page-level
heading-actions row it's otherwise used in) — not a one-off style, and
not copied from `chat.html`'s `.lang-btn` either, though that was the
other option on the table. `demandChartMode` (`'cr'` default | `'pct'`)
and `lastYearlyDemand` are plain module-level JS variables, reset every
page load — no `localStorage`, no backend round-trip; clicking a button
just calls `renderDemandChart(lastYearlyDemand)` again with the new mode,
same destroy/recreate `Chart.js` pattern this file already uses
everywhere else. `%` mode computes YoY change client-side from
`data.yearly_demand` (`yoyDemandPct()`, no new API call) — 2015-16 has no
prior year, so its value is `null`, not a fabricated 0%, which Chart.js
renders as a genuinely empty bar rather than implying that year was
flat. `%` mode also colors each bar green (`--good`)/red (`--red`) by
sign (growth vs decline) — `₹` mode stays the original single navy
color, unaffected. Axis ticks, tooltip formatting, and the dataset label
all switch with the mode (`₹123` vs `123%`; `₹123 Cr` vs `+12.3% vs prior
year` in the tooltip) — nothing from one mode's formatting leaks into
the other.

**Verified live**, real `uvicorn`, real login, real data — via Playwright
reading `demandChart.data.datasets[0]` directly off the live `Chart.js`
instance (not just eyeballing the canvas), cross-checked against the
same figures computed independently by hand from the real
`/api/dashboard_summary` response for **all 11 years, not a 2-year spot
check**:
```
₹ Cr:  100.31, 145.7, 122.96, 128.99, 160.68, 174.66, 182.78, 192.58, 319.79, 305.9, 354.16
%:     null,   45.2, -15.6,  4.9,    24.6,   8.7,    4.7,    5.4,    66.1,   -4.3,  15.8
```
Both matched exactly, in both directions of the toggle (`₹`→`%`→`₹`,
confirmed the label/data revert cleanly, not just switch once). 2015-16
in `%` mode: `null`, bar genuinely absent from the chart (screenshot),
not a 0%-tall bar. Bar colors in `%` mode: green for every positive year,
red for the two negative years (2017-18, 2024-25) — matches sign exactly.
Zero console/page errors. Full non-LLM regression (`dry_run_no_llm`,
`pipeline_mock_test`, `double_count_guard_test`) re-run from
`dcb-ai-app/` and re-confirmed unaffected — `dashboard.html` only,
nothing in `app/dashboard_data.py`, the request/router/safety path, or
any locked folder was touched.

### 9.11 Demand trend chart: ₹/% toggle reverted

**By explicit request** ("revert this togel thing i did'nt like this make
it rupee only just like before") — the §9.10 toggle was disliked outright
and removed in full, restoring the Demand-trend chart to exactly the
single ₹-only bar chart it was before §9.10, not a redesign.

**What was removed:** the `.chart-toggle` CSS rule, the two-button
`₹`/`%` markup in the Demand-trend `panel-head` (back to the plain
`<h3>Demand trend</h3><span class="hint">All 11 fiscal years, division
total</span>`), the `demandChartMode`/`lastYearlyDemand` module-level
variables, the `yoyDemandPct()` and `setDemandChartMode()` functions, the
mode-branching inside `renderDemandChart()` (collapsed back to the single
₹-only body), and the two `#demandModeCr`/`#demandModePct` `onclick`
bindings. `renderDemandChart(yearly)` is now the same plain function it
was before §9.10: navy bars, `₹` Cr values, no mode argument.

**Verified live**, real `uvicorn`, real login, via Playwright reading
`demandChart.data.datasets[0]` directly off the live `Chart.js` instance:
`#demandModeCr`/`#demandModePct` do not exist in the DOM, `.chart-toggle`
matches zero elements, the panel-head is back to its single-line form,
and the chart data is the original 11-year ₹ Cr series exactly —
`100.31, 145.7, 122.96, 128.99, 160.68, 174.66, 182.78, 192.58, 319.79,
305.9, 354.16` — with the single navy `#1a2f5c` color, no per-bar
green/red. Zero console/page errors. Confirmed via `grep` that
`demandModeCr`, `demandModePct`, `demandChartMode`, `lastYearlyDemand`,
`yoyDemandPct`, `setDemandChartMode`, and `chart-toggle` have zero
remaining occurrences anywhere in `dashboard.html`. Full non-LLM
regression (`dry_run_no_llm`, `pipeline_mock_test`,
`double_count_guard_test`) re-run from `dcb-ai-app/` and passed —
`dashboard.html` only, nothing else touched.

### 9.12 Per-sub-division drill-down

**By explicit request, backend built and verified in full before any
frontend code was written**, per the request's own instruction not to
write both blind and debug together.

**Backend (`app/dashboard_data.py`, `app/main.py`):**
- `_ALL_SUBDIVISIONS_EVER_SQL` replaces the old latest-year-only
  subdivisions query. It finds each sub_division/section's own
  `MIN`/`MAX` fiscal_year across ALL its `fact_dcb_rollups` grand_total
  rows, then joins back to that sub-division's own last active year's
  figures — not the division's latest year. For the 5 currently-active
  sub-divisions this is byte-for-byte the same result as before (their
  last active year already equals the division's latest); the only
  change is Saligrama now appears too, with `first_fiscal_year=2015-16`,
  `last_fiscal_year=2017-18`, and its own 2017-18 figures rather than
  being silently absent. `/api/dashboard_summary`'s `subdivisions` array
  now carries this full list, each row tagged `is_active` (its own
  `last_fiscal_year == the division's latest_fy`) — computed once,
  server-side, not inferred by the frontend from date math.
- `build_subdivision_dashboard_summary(db, subdivision_name, ...)` (new)
  returns the same kpis/yearly_demand/yearly_live_installations/
  top_tariff_categories shape as `build_dashboard_summary`, scoped to one
  sub-division by exact name, using ITS OWN latest fiscal year as
  "current" — for Saligrama that's 2017-18, for everyone else 2025-26.
  `yearly_demand`/`yearly_live_installations` only include years that
  sub-division actually has a row for (3 for Saligrama, 11 for the rest)
  — no zero-padding to a fake full series. Top tariff categories filter
  `fact_dcb` by `subdivision_name` directly (not the `subdivision_type
  IN (...)` list `build_dashboard_summary` uses for the division-wide
  view). KPI math (`_kpis`, factored out and shared by both functions)
  is identical to the division-wide version: fact_dcb_rollups' own
  grand_total row per year (never a fresh SUM over fact_dcb — rule 1),
  collection efficiency recomputed from summed totals (rule 5).
- **Route: `GET /api/dashboard_summary/{subdivision_name}`** (new,
  login-gated same as the existing endpoint) → calls the function above,
  404s with a real error message if the name doesn't exactly match a
  real sub_division/section. **No separate "list every sub-division that
  ever existed" endpoint was added** — `/api/dashboard_summary`'s own
  `subdivisions` array already is that full list (see above), so a
  second route reshaping the same rows would have been a redundant
  abstraction the request didn't actually need, not a missing one.
- **`subdivision_name` is untrusted request input**, unlike every other
  string this file interpolates into SQL (`fiscal_year`, always sourced
  from our own prior query result). `app.db.ReadOnlyDB.run` has no
  parameter binding (confirmed by reading `app/db.py`: `_execute` just
  calls `self._conn.execute(sql)`), so the defense is two-layered:
  `_sql_quote` escapes the string so it can't break out of the SQL
  literal, AND a name that doesn't exactly match a real
  `sub_division`/`section` row simply returns zero rows, which
  `build_subdivision_dashboard_summary` treats as `ValueError` → the
  route 404s — never as "run some other query with it." Verified directly
  (not assumed) with a real SQL-injection-shaped string,
  `"Saligrama' OR '1'='1"`, passed straight into
  `build_subdivision_dashboard_summary`: correctly raised `ValueError`,
  same as any other nonexistent name — confirmed the escaping holds
  before ever wiring up a route in front of it.

**Backend verification (real DB, before any frontend code existed):**
ran `build_dashboard_summary`/`build_subdivision_dashboard_summary`
directly against the real `dcb_star_schema.sqlite` for Holenarasipura
(always-active), Konanur (a `section`, not `sub_division` — confirmed
included, not silently dropped by a type-list mismatch), and Saligrama
(confirmed `yearly_demand`/`yearly_live_installations` stop at 2017-18,
`fiscal_year` in the response is `2017-18` not `2025-26`, nothing implies
a later year). Also ran with `Division_Total`, `Division_Office`,
`NoSuchPlace`, and the injection-shaped string above — all four correctly
raised `ValueError`. Then re-verified the same cases through the actual
FastAPI routes via real `curl` against a real `uvicorn` instance:
unauthenticated request → `401`; `Division_Total` → `404` with the real
error message in the body; Holenarasipura/Konanur/Saligrama → `200` with
the exact same figures the direct function calls produced.

**A real, unsmoothed data surprise, found and left as-is, not hidden:**
Saligrama's own last active year (2017-18) has `demand_total = 0.0` —
confirmed in both `fact_dcb_rollups` and `fact_dcb` (every one of its
tariff categories shows `0.0` demand that year too) — while
`collection_grand_total` is still real money, `₹7.96 Cr`, presumably
collections against carried-over arrears after billing had already
stopped. That makes `outstanding_balance` (`demand_total -
collection_grand_total`) come out **negative**, `-₹7.96 Cr` — collected
more than was billed that year. Left exactly as the formula produces,
not clamped to 0 and not hidden — see `_kpis`'s own docstring note in
`app/dashboard_data.py`. `collection_efficiency_pct` is `null` for that
same reason (division by zero demand, guarded, not `NaN` or a crash).
share_pct for every one of its top-5 tariff categories is also `null`.

**Frontend (`app/static/dashboard.html`):** Sub-divisions panel rows
(`.row-stacked`) are now clickable — `renderSubdivisionsList` renders
the full ever-existed list (from `divisionData.subdivisions`, fetched
once and cached) with an inactive row showing a `Closed` badge and a
subtitle naming its own last active year explicitly (`FY 2017-18 (last
active year)`) rather than implying current-year figures; its
proportional collected/outstanding bar is replaced by a flat neutral
`.row-bar-fill.no-data` fill when `demand_total` is 0 (Saligrama),
since a %-of-zero bar would look identical to the empty-tail bug §9.8
already fixed, not like "no data." Clicking a row calls
`showSubdivisionView(name)`, which fetches the new endpoint and re-fills
the KPI row/Demand trend/Collection efficiency donut/Top tariff
categories/Live installations panels via a shared `renderScopedPanels(data)`
— the same function whether the data came from the division-wide or the
per-sub-division endpoint, since both share the same JSON shape. The
heading updates to `"{name} — Demand, Collection & Balance"` with a
subtitle stating the FY (and "Closed after …" when scoped to an inactive
sub-division) — the exact wording the request itself gave as an example.
Re-clicking the already-selected row, the new "← All sub-divisions" link
in the heading, or the sidebar's existing "Dashboard" nav item (now
wired, previously did nothing) all call `showGeneralView()`. The
selected subdivision is reflected in the URL (`?subdivision=Name`) via
`history.pushState`, with a `popstate` listener so the browser's
Back/Forward buttons work, and the initial page load honors
`?subdivision=` if already present — bookmarkable, as the request asked
for as a nice-to-have. An invalid `?subdivision=` (e.g. a stale bookmark
for a sub-division that turns out not to exist) shows the error banner
and falls back to the general view rather than leaving the page blank.

**A real bug found and fixed during live verification, not just in
review:** a first Playwright screenshot of the general (all-sub-divisions)
view showed "← All sub-divisions" visible even though
`backToGeneralBtn.hidden` was confirmed `true` via `page.evaluate`. Root
cause: `.btn-outline`'s own `display:inline-flex` and the browser's
default `[hidden]{display:none}` rule have equal CSS specificity, and
the page's own stylesheet is applied after the browser's default one, so
`.btn-outline`'s `display` silently won regardless of the `hidden`
attribute/property. Fixed with an explicit `#backToGeneralBtn[hidden]
{display:none}` override, scoped to just that element since nothing
else on this page toggles `hidden` on a `.btn-outline`/`.btn-solid`.
Re-verified live after the fix: the button is genuinely absent (not just
logically "hidden") in the general view, and genuinely present in every
scoped view.

**Verified live**, real `uvicorn`, real login, via Playwright, clicking
through **every** sub-division including Saligrama: KPI/chart/donut/
tariff/live-installations figures matched the direct-function-call
numbers above exactly for each one; Saligrama's view showed `₹0 Cr`
demand, `₹7.96 Cr` collection, `₹-7.96 Cr` outstanding, `—%` efficiency,
the `Closed` badge, "Closed after 2017-18 — showing FY 2017-18 (last
active year)" in the subtitle, and the neutral no-data bar — a real
screenshot, not just the JSON. Re-clicking a selected row returns to the
general view (verified with a clean single-click sequence after an
earlier same-session double-click test gave a false negative — a bug in
the *test script's* click ordering, not the app, confirmed by isolating
it). The "← All sub-divisions" button and the sidebar's "Dashboard" nav
item both return to the general view. `?subdivision=Konanur` loaded
directly by URL renders Konanur's scoped view on first load; browser
Back from there returns to the general view via `popstate`.
`?subdivision=NoSuchPlace` shows the error banner and falls back to
the general view instead of a blank page. Zero console/page errors
throughout (the one exception — an expected `404` network log line from
the intentional invalid-name test — is the browser logging that
request's own real response, not a JS error). Full non-LLM regression
(`dry_run_no_llm`, `pipeline_mock_test`, `double_count_guard_test`)
re-run from `dcb-ai-app/` and passed. Only `app/dashboard_data.py`,
`app/main.py`, and `app/static/dashboard.html` were touched — all
queries here only ever read `DCB_Star_Schema/dcb_star_schema.sqlite`
through the existing read-only connection; no locked folder was touched.

**Follow-up, by explicit request ("remove saligrama in the dashboard"):**
Saligrama no longer appears in the Sub-divisions panel or in
`/api/dashboard_summary`'s `subdivisions` array — `build_dashboard_summary`
now filters that list down to rows where `is_active` is true right after
computing it, so `_ALL_SUBDIVISIONS_EVER_SQL` still finds Saligrama and
`is_active` is still computed correctly for it, it's just not appended to
what's returned. This is a general "active only" filter, not a
name-specific exclusion, so it would also hide any other sub-division
that closes in the future, not just this one. `build_subdivision_
dashboard_summary` and its route are untouched — `GET
/api/dashboard_summary/Saligrama` still works if called directly (real,
verified: `200`, correct scoped data) — only the list that renders as
clickable rows was restricted. Verified live: 5 rows render (down from
6), row order/figures for the 5 active ones unchanged, zero console
errors, screenshot confirms no empty gap left behind. Full non-LLM
regression re-run and passed. Only `app/dashboard_data.py` touched.

---

## 11. Pipeline-level category/metric/block resolution bugs — root-caused and fixed (locked folders unlocked for this, by explicit request — see §0)

**Reported symptom:** the chatbot returned 0 for HT-2 demand in every
year, and a badly inflated LT-1 demand for the two most recent fiscal
years. Root-caused to real bugs in `DCB_Staging_DB/build_staging.py` and
`DCB_Star_Schema/build_star_schema.py` (not just the chatbot layer on
top), confirmed by hand against the raw source workbook before writing
any fix — not assumed from the symptom description alone, which turned
out to only partly match what was actually wrong.

### 11.1 What was and wasn't already correct

Before touching anything, each of the 4 claims in the bug report was
checked directly against the pipeline and the raw sheet:

- **"Metric columns move position across years (Demand-Total: AM→AR)"** —
  true (confirmed: column 39 for 2015-16..2024-25, column 44 for
  2025-26) — but `build_column_map`/`canonical_field` in
  `build_staging.py` were ALREADY resolving every metric column by its
  own header text, not a fixed index, and correctly landed
  `demand_total` at the right field regardless of position in both
  cases. No bug here; not changed.
- **"Block layout (7 vs 6 blocks, Section vs Sub-Division) changes
  across years"** — true (7 blocks 2015-18/2020-24, 6 blocks
  2018-20/2024-26, matching Saligrama closing and Division_Office's
  window) — but `classify_block`/`find_real_width` were ALREADY
  detecting this dynamically by title text, already handled "SECTION:
  KONANUR" correctly. No bug here; not changed.
- **"Parent categories (LT-2..LT-6, HT-2, HT-3) never appear as their
  own literal tariff-category value, only as a Sub Total of X label"** —
  true, and correctly captured in `fact_dcb_rollups` already (e.g.
  "Sub Total of HT-2" existed with the right number for every year) —
  the actual bug was one level up: **nothing in the app knew to look
  there**. `dcb-ai-app`'s router only ever matched literal
  `dim_tariff_category.tariff_code` values, which structurally can
  never include a bare "HT-2" — so a generated `WHERE tariff_code =
  'HT-2'` matched zero rows, and if wrapped in `COALESCE(SUM(...), 0)`
  (a completely reasonable thing for an LLM to write) silently produced
  one row with value 0 instead of erroring. This is the actual
  mechanism behind "returned 0 for HT-2" — see §11.3.
- **"Sub Total ofIPP" (no space) / "LT-5 b" (double space) as real
  label variants** — both confirmed to exist verbatim in the source.
  `extract_tariff_code()`'s regex required `\s+` between "of" and the
  category name, so "Sub Total ofIPP" silently extracted NO code at all
  (`tariff_code_summarized = NULL`) for every subdivision in 2025-26 —
  a real, confirmed bug, fixed in §11.4.

### 11.2 The actual LT-1 inflation bug (NOT what the initial report guessed)

Hand-traced directly against the 2024-25 and 2025-26 raw sheets (not
assumed from the symptom). The 2024-25 Holenarasipura block:

```
176  1  LT-1  Bhagyajyothi & Kutirajyothi (BV/KJ) (Metered) Upto 58 Units)
177     LT-1  Bhagyajyothi & Kutirajyothi (BV/KJ) (Metered) 58 Units & Above
179  Sub Total of LT-1                                    <- 32,374,451.83
181  2  LT-1  Ltng., heating & Motive Power, BMA & Municipal Corp
182     LT-1  Ltng.,heating & Motive Power, BMA & Municipal Corp (FL)
183     LT-1  Ltng. ,heating & Motive Power, Urban Local Bodies
184     LT-1  Ltng. ,heating & Motive Power, Urban Local Bodies (FL)
186  Sub Total of LT-1 A                                  <- 146,588,170.12
188  3  LT-2(i)  Ltng. ,heating & Motive Power, Pvt. Institu. in ULB's & CC
189     LT-2(ii) ...
191  Sub Total of LT-2                                    <- (separate, correct, unaffected)
```

The "Ltng., heating & Motive Power..." description family is LT-2's in
every other year (confirmed: `dim_tariff_category` has these exact
description strings under `LT-2(a)(ii)` elsewhere) — but the 2024-25
sheet's own `tariff_category` cell literally says `LT-1` for rows
181-184 too, a real one-off source mislabeling (the sheet's own compiler
apparently noticed the clash and named its subtotal "Sub Total of LT-1
A" to disambiguate, rather than "LT-2"). `fact_dcb` groups by
`(fiscal_year, subdivision, tariff_category, description)`, so both
groups landed under `tariff_code='LT-1'` (different descriptions, so not
deduplicated) — meaning **any query that filtered/summed `fact_dcb` by
`tariff_code = 'LT-1'` or `LIKE 'LT-1%'` silently included both groups**,
inflating the total from 32.37M to 178.96M (2024-25) — this exact
`LIKE 'LT-1%'` pattern was, in fact, baked into
`DCB_Semantic_Layer/example_queries.sql`'s own worked example #5
("compare domestic LT-1 vs industrial HT-2 demand"), a "pre-verified"
example the chatbot's prompt is told to follow — so the wrong pattern
was being actively taught to the LLM, not just occasionally guessed.

2025-26 goes one step further: the sheet prints a THIRD row, `Total of
LT-1` (203,421,441.74), confirmed by direct arithmetic to be exactly
`Sub Total of LT-1` (35,357,859.15) + `Sub Total of LT-1 A`
(168,063,582.60) — the source's own explicit combined figure for that
year only. `classify_row_type()` didn't recognize a bare "Total of X"
(no "Sub" prefix) as any real rollup type at all — it fell into the
generic `other_total` bucket, which isn't in `build_star_schema.py`'s
`ROLLUP_TYPES` tuple, so **this row's real figures were silently dropped
from `fact_dcb_rollups` entirely**, for every subdivision, every time
the pipeline ran. Confirmed the same root cause (a spacing/prefix
variant classification gap) also silently dropped `HT - TOTAL`/
`LT - TOTAL` (spaced dash) block-total rows in every year they used that
spelling — `classify_row_type`'s substring checks (`"ht-total" in text`)
never matched `"ht - total"` (space-hyphen-space).

### 11.3 Also found via the standing reconciliation check itself (§11.5): `revised_arr` wrongly treated as additive

Generalizing the reconciliation check to every metric (not the previous
fixed 4) immediately surfaced a pre-existing bug unrelated to the LT-1/
HT-2 report: `revised_arr` ("Average Revenue Realized" — a rate, revenue
per unit of energy realized, per `semantic_layer.json`'s own glossary
entry for "ARR") was being summed across tariff categories in `fact_dcb`
like any other additive column — `is_additive()`'s `NON_ADDITIVE_PREFIXES`
never listed it, and neither did `semantic_layer.json`'s own
`non_additive_ratios` array, despite the glossary calling it a rate one
line above. Reconciliation diffs for this one column ran as high as
1,134,413% before the fix (summed value ~100-3700, sheet's own printed
figure ~0.01-2) — an extreme, obvious signal, not a subtle one. Fixed in
both places (§11.4) — this is exactly the kind of latent bug the
generalized standing check (§11.5) exists to catch automatically, not
something anyone was specifically looking for.

### 11.4 Fixes made

- **`DCB_Staging_DB/build_staging.py`** — `classify_row_type()` rewritten
  to normalize whitespace/hyphen variants before matching (`_compact()`)
  instead of matching each literal spelling one at a time, so
  `"HT - TOTAL"`/`"LT - TOTAL"` classify correctly as `ht_total`/
  `lt_total` (previously silently dropped as `other_total`). Added a new
  `combined_total` row type for a bare `"Total of X"` label (no "Sub"
  prefix), distinguished from `sub_total` by checking whether "sub"
  appears before the "total of" match — previously also dropped as
  `other_total`. `SRC`/`OUT_DIR` changed from a hardcoded machine-specific
  absolute path to resolve relative to the script's own location (both
  still override-able via `DCB_SRC_WORKBOOK`/`DCB_STAGING_OUT_DIR` env
  vars), since the project has already been handed off across machines
  once and will be again.
- **`DCB_Star_Schema/build_star_schema.py`** — `extract_tariff_code()`'s
  regex changed from `\s+` to `\s*` between "of" and the category name
  (fixes the `"Sub Total ofIPP"` no-space case), plus whitespace-collapse
  on the extracted code. `ROLLUP_TYPES` gained `"combined_total"` so
  those rows (and their real figures) reach `fact_dcb_rollups` instead of
  being silently dropped. `is_additive()` gained an explicit
  `NON_ADDITIVE_EXACT = {"revised_arr"}` set (§11.3).
  `RECON_METRICS` changed from a fixed 4-metric list to every additive
  column the data actually has (§11.5). `STAGING_DB`/`OUT_DIR` made
  relative/env-override-able, same reasoning as above.
- **`DCB_Semantic_Layer/semantic_layer.json`** — `non_additive_ratios`
  gained `"revised_arr"`; `fact_dcb_rollups.key_columns` documentation
  updated for `combined_total` and the whitespace/case-insensitive
  matching convention; a new `rules` entry states the family-level
  resolution rule in one place (read `fact_dcb_rollups` by
  `tariff_code_summarized`, never re-derive from `fact_dcb`, prefer
  `combined_total` over `sub_total` when both exist, absence = zero rows
  = `answerable=false`, never a fabricated 0).
- **`DCB_Semantic_Layer/example_queries.sql`** — worked example #5
  (LT-1 vs HT-2 comparison) CORRECTED, not just left standing next to a
  new one — its `tariff_code LIKE 'LT-1%'` pattern was demonstrated above
  to be the actual mechanism that inflated LT-1 for 2024-25/2025-26, so
  leaving a "pre-verified" example asserting a wrong pattern would have
  undermined the rest of this fix. Rewritten to read
  `fact_dcb_rollups.tariff_code_summarized` with an explicit
  `combined_total`-preferred `ORDER BY .. LIMIT 1` per side, and a new
  worked example #13 added for the simpler single-category/single-year
  case (`"What was the HT-2 demand in 2015-16?"`, the report's own
  original question) demonstrating the same pattern directly. This is a
  deliberate departure from this file's previous "purely additive only"
  precedent (§0/§10.2/§10.3) — flagged here rather than left unstated,
  since a demonstrably wrong worked example is a correctness bug in its
  own right, not something to leave standing for consistency with a
  no-longer-appropriate precedent.
- **`dcb-ai-app/app/semantic_layer.py`** — `RouterIndex` gained
  `rollup_category_codes` (distinct `tariff_code_summarized` values from
  `fact_dcb_rollups`, `rollup_type IN ('sub_total','combined_total')`,
  loaded once at startup via `build_router_index`). `extract_entities()`
  matches the question against these (via the same `_normalize()`
  whitespace/case-collapsing already used for tariff descriptions, with
  the same "most specific match wins" subsumption already used for
  literal tariff codes) and adds `entities["parent_category_matches"]`
  when found — grounding "HT-2 demand" the same way `tariff_code`/
  `tariff_category_matches` already ground other entity types, so the
  LLM is told the real, exact `tariff_code_summarized` string to filter
  on rather than guessing spelling/whitespace itself.
- **`dcb-ai-app/app/prompts.py`** — `SYSTEM_INSTRUCTIONS`'s existing
  family-level guidance (previously: always `GROUP BY tariff_code` over
  `fact_dcb`) now branches: if the router found a
  `parent_category_matches` entry, read `fact_dcb_rollups` directly
  (with the `combined_total`-preferred pattern) instead; otherwise (a
  plain family with no lettered sub-splits, e.g. LT-1 as most years
  actually see it) the original `fact_dcb` `GROUP BY` still applies.

### 11.5 Standing regression test: `DCB_Star_Schema/test_grand_total_reconciliation.py`

Re-runs the real ingestion pipeline (`build_staging.py` then
`build_star_schema.py`) against the real source workbook, into a
throwaway scratch directory (via the env var overrides added in §11.4 —
never touches the checked-in `.sqlite` files), then asserts three things
for the freshly-built output: (1) no rollup label was classified into a
bucket nothing reads (the `HT - TOTAL`/`Total of LT-1` bug class), (2) no
`fact_dcb` row exists with every numeric column truly NULL, (3) for every
`(fiscal_year, subdivision)` with a printed `grand_total` row, `fact_dcb`
summed per EVERY additive metric column (not a fixed handful) reconciles
against it within tolerance (1% relative OR 2 absolute units, whichever
is larger).

**Honestly scoped, not oversold:** because this sums ALL of `fact_dcb`
regardless of which category each row got attributed to, it would NOT by
itself have caught the LT-1 mislabeling bug (§11.2) — that bug didn't
change fact_dcb's total, only which category individual rows were
(mis)attributed to. What it WOULD catch (and did catch, live, during
this fix — see §11.3): a category silently dropped, a metric column
resolved to the wrong header, a non-additive column wrongly summed, or a
future year's sheet using yet another rollup-label spelling the
classifier doesn't recognize. Together with the label-classification
fixes in §11.4 (which is what actually closes the loop on "a new layout
quirk gets caught automatically" for the label/dropped-row class of bug),
this is a meaningfully stronger safety net than before, not a complete
guarantee against every possible future quirk.

Two real, tiny, hand-verified (not assumed) source-data imperfections
are within the test's tolerance and don't fail it: `diff_billed_vs_live`
is off by exactly 1 unit in 2 (subdivision, 2025-26) cells (111 vs 112,
232 vs 233 — independently re-summed from every real leaf row by hand to
confirm this is the SHEET's own printed total being 1 higher than its
own category rows, not a missing row); `collection_fac_total` is off by
up to 0.132% for Division_Total in 2015-16/2016-17/2017-18 (a few
thousand rupees on ~20M, again the sheet's own printed total being
slightly below its own summed category rows). Both documented in the
test's own docstring with the exact numbers, matching this project's
"no smoothed-over failures" convention — not silently tolerance-tuned
away without a citation.

### 11.6 Verification performed

- Full re-run of both build scripts against the real source workbook;
  `reconciliation_report` avg/max abs diff_pct: 0.00046% / 0.893% across
  4,526 checks (was checking only 4 metrics × 292 rows before this fix,
  and never would have surfaced `revised_arr`'s bug at all).
  `test_grand_total_reconciliation.py` passes.
- Direct SQL verification (not just trusting the reconciliation
  aggregate) that HT-2 demand is now real and non-zero for all 11 years,
  Division_Total: 23.4M (2015-16) up to 141.3M (2025-26), 0 anywhere.
- Direct SQL verification that LT-1 for 2024-25 now correctly separates
  `Sub Total of LT-1` (32.37M, real LT-1) from `Sub Total of LT-1 A`
  (146.59M, the mislabeled group) as two distinct rollup rows, and that
  2025-26 has all three (`sub_total` ×2 + `combined_total`) with the
  `combined_total`-preferred query pattern correctly returning 542.71M
  (the sheet's own `Total of LT-1` figure).
- `dcb-ai-app`'s full non-LLM regression suite
  (`dry_run_no_llm`/`pipeline_mock_test`/`double_count_guard_test`)
  re-run against the rebuilt star schema — all pass; the 3 long-verified
  reference figures (93.62% Arkalgud, LT-4(a) top tariff 2023-24, Konanur
  97.46%) are byte-identical to every prior session's result, confirming
  this fix didn't disturb anything it wasn't meant to touch.
- The router's new `parent_category_matches` extraction verified directly
  against real questions ("HT-2 demand in 2015-16" → correctly matches
  `HT-2`; "HT-2(a) demand..." → correctly matches only the more specific
  `HT-2(a)`, not also the less specific `HT-2`; "HT-7 demand in 2015-16"
  → correctly matches the entity, and the resulting SQL pattern correctly
  returns zero rows since HT-7 wasn't billed yet that year).
- **Live end-to-end LLM verification (done in a follow-up session once
  the internal vLLM gateway became reachable — it was not from the
  environment this fix was originally written in, a real `ConnectTimeout`
  at the time, not skipped):** every scenario above re-asked through the
  real running app (`POST /api/ask_once`, real Gemini/vLLM calls, no
  mocking) and confirmed correct, matching the hand-verified SQL-level
  figures exactly:
  - *"What was the HT-2 demand in 2015-16?"* → LLM correctly wrote the
    `fact_dcb_rollups`/`parent_category_matches` pattern from worked
    example #13 → **23,399,034.75**.
  - *"What was the LT-1 demand in 2024-25?"* → **98,589,118.03** (was
    178,962,621.96, inflated, before this fix).
  - *"What was the LT-1 demand in 2025-26?"* → **542,708,386.67** —
    LLM correctly preferred the `combined_total` ("Total of LT-1") row
    over the two separate `sub_total` rows.
  - *"Compare LT-1 vs HT-2 demand across all years"* → LLM copied
    corrected worked example #5's `ORDER BY ... LIMIT 1` pattern
    verbatim; all 11 years' values matched the hand-verified figures in
    §11.2/§11.6 exactly.
  - *"What was the LT-6 EVM Vehicle Charging Station demand in
    2015-16?"* (genuinely doesn't exist that early) → `answerable=false`
    with an honest explanation that the LT-6 family wasn't billed that
    year — not a fabricated 0.
  - *"What was the HT-7 demand in 2015-16?"* (also doesn't exist yet)
    → SQL correctly matched zero rows → "No matching data was found for
    that question." — the existing honest fallback, not a fabricated 0.
  - Baseline regression check: *"What was the collection efficiency in
    Arkalgud in 2024-25?"* → **93.62%**, byte-identical to every prior
    session's result — confirms this fix didn't disturb anything outside
    its scope, live through the real LLM too, not just the non-LLM test
    suite.

---

## 12. KPCL-POC port (2026-09-30) — mart, deterministic routes, reviewer, hardening, Excel, UI, tests

Improvements taken from the sibling `D:\KPCL POC` (read-only: only read, never written) and adapted to DCB data. The
4 locked folders were **not** touched: SHA-256 of all 29 files compared before and after — byte-identical.

### 12.1 Data layer — `DCB_Mart/` (new, built, not locked)

`DCB_Mart/build_mart.py` reads the star schema `mode=ro` and writes `dcb_mart.sqlite` (`mart_*`, `semantic_*`,
`ledger_*`), `mart_semantic_layer.json` and `dcb-ai-app/integrity_manifest.json`. Hand-written metadata in
`DCB_Mart/metadata/`: column docs, a 20-KPI metric registry, a capability manifest, known issues KI-01…KI-12.
**15 build checks, all PASS** (families add up to the printed grand total for the division and every sub-division on
all 28 metrics; 847 family totals equal the printed family subtotal; golden figures; etc. — `DCB_Mart/README.md`).
Facts the build surfaced (details in `known_issues.json`):
- **The printed "Sub Total of LT-5" is 0 in 2015-16…2023-24** (real total in "LT-5 (a+b)"). The chat prompt's family
  rule and the Reports page both read that row, so both reported LT-5 = 0. Both now use the mart (correct every year).
- **Division total ≠ sum of sub-divisions from 2019-20** (up to 3.475 %, 2024-25 — corrected in 13.1): miscellaneous revenue (mainly 62.9177) is
  booked only in the division block. `semantic_layer.json`'s rule 3 calls the two interchangeable; they are not.
- **LT-1 from 2024-25 includes the lighting/heating slabs printed under LT-1** (LT-2(a) earlier). §11 reported LT-1
  2024-25 as 98,589,118.03 (the "Sub Total of LT-1" row only); the mart and the deterministic answer now give
  **473,913,919** — the same convention as the 2025-26 sheet's own "Total of LT-1" (542,708,386.67), with a caveat
  on every LT-1 / LT-2 answer from 2024-25. **This is a deliberate figure change; confirm with CESC.**

### 12.2 AI layer — `dcb-ai-app/app/`

Order of routes (`pipeline.py`): screen → greeting / identity → **verified queries** (`DCB_Mart/example_queries.sql`,
14 reviewed pairs, conservative matching) → glossary → **capability notes** → router (`router.py`: sub-divisions with
typos, "this / last year", FY24, "since 2020", families incl. `l.t.-1`, slabs, unknown names) → **reports**
(`reports.py`: trend, year comparison, family comparison, sub-division series, LT/HT split, sub-division ranking, tariff
ranking, summary) → **metric lookup** (`metric_lookup.py`: 1–3 registry metrics × one scope × one year, bound params) →
the model. Slab-level wording (a description, "BMA", "10 HP", "EV charging", "industries") skips the deterministic routes
and goes to the model, whose star-schema prompt is kept as tuned in §10 plus one short rule: whole-family totals come
from `mart_fact_family_*` (a first version with the full mart schema + examples in the prompt dropped the live suite to
34/49 and was cut back). Model path: one retry on a cut-off/malformed reply; **one repair round** fed the exact guard or
SQL error; **one repair round on an empty result** (latest-year filter on a closed slab, two `=` on one column…); impossible
results sent back; subject-mismatch check; then **reviewer** (`review.py`, second model pass, 5 error categories,
deterministic veto of provably false objections; rejected twice → nothing shown). Answers: `answer.py` formats ₹ as
crore / lakh with the exact amount, narrates many-row results, builds the table and (multi-series) chart from real rows.
`IDENTITY_ANSWER` fixed (it described "electricity consumption, billing, and account data").

### 12.3 Guardrails and security

- `db.py`: star `mode=ro` + mart ATTACHed `mode=ro` + `PRAGMA query_only`; an **authorizer** on every statement (reads of
  the 7 reviewed star tables and `mart_*` only; no PRAGMA / ATTACH / writes / recursive CTE / `load_extension`);
  **bound parameters** (`params`) — `dashboard_data.py` and `reports_data.py` no longer paste request values into SQL.
- `sql_safety.py` adds: table allowlist, `RECURSIVE` / transactions refused (`replace()` now allowed), no AVG/SUM of a
  ratio column, KPI-named columns must use their formula, no aggregate over a join of two mart facts, no summing of
  year-end positions across years, contradictory `col = 'A' AND col = 'B'` refused, `NULLS LAST` on ascending sorts.
- `guardrails.py`: fixed-wording refusals for empty / too long / pasted SQL / injection / harmful (meter tampering) /
  write requests / personal data / system questions / off-topic; scrubbing of model text; safe model refusals.
- `protection.py`: CSP (no inline scripts — every page script moved to `static/*.js` unchanged), no framing, no sniffing,
  413 over 16 KB, **60 requests/min per IP** (429); `main.py`: 8 questions at a time ("busy" after 20 s), fixed 500
  message, SQL / routes / timings / `/docs` only with `DCB_SHOW_TECHNICAL_DETAILS=true` (the SQL used to stream to everyone).

### 12.4 Excel reports (`mis_report.py`, `answer_report.py`, `excel_common.py`)

Dashboard button **"MIS report (Excel)"** (whole division or the drilled-in sub-division; login-gated): Summary (KPIs vs
previous year, observations, integrity checks), Year trend, Sub-divisions, Tariff families, Tariff slabs, Notes. The
Summary is checked equal to the dashboard's own object. Chat: every data answer gets **Download Excel report** (answer
kept server-side 3 h under a random id; Report / Data / Chart / Notes). Every workbook: formula-like text stored as text,
"Proof of concept – not for official use" footer, DRAFT properties, checks printed; **a failed check withholds it (409)**.

### 12.5 UI (Cescom look kept, by request — only responsiveness / immersion borrowed)

Chat: live stage steps (Understanding → Fetching figures → Double-checking → Writing answer, streamed from the real
pipeline), bold / bullet rendering (HTML-escaped first), collapsible data table with CSV, Copy, Excel download, multi-series
charts in the navy/red palette, `aria-live`, phone tweaks. Dashboard: MIS button, phone-width action row, API-docs link
only in technical mode. Verified in headless Edge (DevTools protocol): dashboard, insights, reports, about, chat at
1366×900 and 390×844 — no page errors, no CSP violations, no horizontal overflow.

### 12.6 Verification (real runs, 2026-09-30)

- `python -m pytest tests -q -p no:cacheprovider` → **129 passed** (~2 s, offline, model mocked).
- `python tests\run_stress.py` (live) → **139/139**; every expected figure recomputed from the star schema.
- `python -m tests.run_regression` (live gateway) → 49-question suite **46/49 OK** by the old error-marker rule. The
  **original pre-port pipeline, run the same day on the same gateway, scored 37/49** (the recorded 49/49 of 16 Sep no
  longer reproduces — the gateway model's behaviour changed). The 3 misses answer "no data": HT-4 (zero demand in
  every year — correct), a slab spelling the model mistyped, a self-join the model wrote. 82 categorized questions:
  every one routed as its category intends.
- Existing scripts: `double_count_guard_test` 14/14, `definitional_question_test` 258/258, `dry_run_no_llm` and
  `pipeline_mock_test` pass.

### 12.7 Known limitations (as of 12; see 13 for what changed)

- ~~The model path still varies with the gateway model (see 12.6); slab-level questions depend on it.~~ Slab-level
  questions are deterministic since 13.2.
- ~~Excel viewer and KPCL's knowledge base were not ported; Kannada covers UI chrome only.~~ Ported in 13.3–13.5.
- Kept answers live in server memory (lost on restart); no per-user sessions for `/api/ask*` (unchanged, by design;
  options listed in 13.7, none implemented).
- Rebuild order: stop the server → `python DCB_Mart/build_mart.py` → start the server.

---

## 13. 2026-10-01 pass — raw re-verification, deterministic slabs, Excel viewer, knowledge base, Kannada, Gemini removed

`D:\KPCL POC` was only read (never written). The 4 locked folders were not touched: SHA-256 of every file compared
before and after the mart rebuild — identical. No git commit was made.

### 13.1 The three data facts, re-verified straight from the raw workbook

New standing script **`DCB_Mart/verify_against_raw.py`** (~11 s) re-reads `HNPURA DIVISION DCB FY 2015 TO 2026.xlsx`
with openpyxl (read-only) — no pipeline code, no star schema: blocks are found by their own `Sl.No` header rows, metric
columns by their header text, line items carry their tariff code forward as printed — and checks the mart against it.
Writes `reports/raw_reverification.md`; exit code 1 on any failure. Result: **PASS, 0 failures.**

- **LT-1 2024-25 = 473,913,919.16 — confirmed, and it is the sheet's own LT-1.** The 2024-25 / 2025-26 sheets print four
  slabs (lighting, heating & motive power in BMA & municipal corporations / urban local bodies, each with a free-lighting
  variant) under code LT-1 with their own subtotal "Sub Total of LT-1 A". Division 2024-25: "Sub Total of LT-1" (BJ/KJ)
  98,589,118.03 + "Sub Total of LT-1 A" 375,324,801.13 = 473,913,919.16 = every LT-1 line item = the mart. 2025-26:
  110,025,120.93 + 432,683,265.74 = 542,708,386.67 = the sheet's own printed "Total of LT-1". Every sub-division agrees the
  same way. Up to 2023-24 the same four descriptions are printed as LT-2(a)(i), LT-2(a)(i)(FL), LT-2(a)(ii),
  LT-2(a)(ii)(FL). So 98.6M is only the BJ/KJ part; 473.9M follows the sheet's code. Still a reporting convention for
  CESC to confirm, but no longer a guess. Slab answers (13.2) match by description, so they stay comparable across years.
- **LT-5 — confirmed, with a sharper cause.** Up to 2023-24 the row printed "Sub Total of LT-5" closes the **LT-5(a)**
  block (areas under BMA / municipal limits); it is the LT-5(a) subtotal, mislabelled. LT-5(a) had **0 installations and 0
  demand in every block and every year**, so the row reads 0. The family total is "Sub Total of LT-5 (a+b)" (= "LT-5 b"),
  which equals every LT-5 line item and the mart in all 63 block-years. From 2024-25 LT-5 is one block (the sub-division
  blocks label its subtotal "Sub Total of LT-5 b"). Holenarasipura 2015-16 prints its LT-5(a) rows with code "LT-5(b)"
  (all zero, no effect).
- **Division vs sub-divisions — confirmed, number corrected: up to 3.475 %, not 3.4 %.** Gap by year: 0 for 2015-16 to
  2018-19 (2018-19 collection off by ₹6); 2019-20 +0.503 %; 2020-21 +0.003 %; 2021-22 +0.010 %; 2022-23 +0.575 %; 2023-24
  +1.065 %; **2024-25 +3.475 % (₹102,725,031)**; 2025-26 +0.206 %. The entire gap is miscellaneous revenue booked only in
  the division block — mainly 62.9177, also 62.9327 (the largest in 2022-23), 62.9017, 62.9027, 62.9097. Non-misc
  categories agree to the rupee. In 2025-26 the collection agrees exactly; only demand differs.
- **Also found:** Division_Office is not all-zero. In 2020-21 it carries ₹2,410 of 61.9067 ("other receipts").
- `known_issues.json` KI-01 / KI-02 / KI-04 / KI-05 rewritten with these verified figures (KI-02's caveat now says "by
  up to 3.5 %"); the mart was rebuilt (15/15 checks) so the app's caveats carry them.

### 13.2 Slab-level questions are deterministic — `app/slab_lookup.py`

Before, any slab wording ("BMA lighting", "unmetered IP sets", "EV charging", "industries", "LT-4(a)", a quoted
description) went to the model, and the answer varied with the gateway. Now it is answered from
`mart_fact_slab_*_year` (KPCL-style ladder: deterministic first, ask instead of guessing):

1. **Resolve** the wording to exact `(tariff_code, description)` slabs from `mart_dim_tariff`:
   - **Consumer segments** are matched by description, so a series survives the 2024-25 re-coding (LT-2(a) → LT-1,
     LT-6 → LT-6 A/B/C, LT-7 b → LT-3 b). The segments: domestic, BJ/KJ, BMA, urban local bodies, private institutions,
     commercial, hoardings, IP sets, horticulture, industrial (LT-5 + HT-2(a)), water supply, public lighting, EV
     charging, temporary, apartments, lift irrigation, agricultural farms, agriculture, govt hospitals and railway.
   - **Qualifiers** narrow the set and never widen it: free / office lighting, prepaid, (un)metered, rural / urban
     feeder, village panchayat, ULB areas, HP bands and kV levels.
   - Slab codes, quoted descriptions, a family plus a qualifier ("LT-4 unmetered"), or one clear fuzzy match.
2. **Ambiguous** fuzzy matches get a clarifying list. A consumer group the data has no single category for
   ("government", "temples", "schools", "rural") gets the slabs that mention it, or a plain "no such category".
   Found live and fixed: *"share of LT demand from government connections"* used to go to the model, which summed LT-1
   over all demand (15.49 %), and the reviewer passed it. Now: "none of the slabs asked about is for government; the
   slabs that mention it are HT-2(c)(i) …".
3. **Answer** with registry expressions and bound parameters. The forms are: one year (with the previous year), a
   series, sub-divisions side by side, segments compared ("domestic vs commercial") and slabs ranked. **Every answer
   lists exactly which slabs were counted.** A code asked for after it was renamed says so ("LT-2(b)(i) … from 2024-25
   printed under LT-2(i)").
- Wired in `pipeline.py` before the model (route `slab:*`); the Excel report notes the method.
- Tests: `tests/test_slab_lookup.py` checks every figure against the star schema. The model-path tests now switch the
  slab route off explicitly (`model_path` fixture), because their old vehicle question is answered deterministically.

### 13.3 Excel viewer (ported from KPCL) — `app/xlsx_view.py`, `static/report-viewer.js/.css`

- `GET /api/mis-report/view` (login-gated) and `GET /api/answer-report/{id}/view` render **the very workbook the
  download writes**. The preview shows cells through their number formats, fills, merges, the frozen header, sheet tabs
  and charts drawn with Chart.js; every cell is HTML-escaped.
- **Dashboard:** "MIS report (Excel)" now opens the viewer, which has its own Download button. **Chat:** "View report"
  sits next to "Download Excel report".
- The viewer CSS was restyled to the Cescom palette (navy, red only for errors; high-contrast aware). It does not use
  KPCL's colours.
- **One real bug found and fixed in the ported code.** Excel keeps 15 significant digits, so 29,131,030.054999996 is
  stored as …055 and shown as .06, while the viewer rounded the in-memory double to .05. `format_value` now rounds at
  15 digits.
- Tests: `tests/test_report_view.py` compares every cell of the view against the downloaded file (division, Arkalgud,
  Konanur 2023-24, a chat answer), plus a login check, a 404 on an unknown name and escaping.

### 13.4 Knowledge base (ported pattern) — `DCB_Mart/metadata/knowledge_base.yaml`, `app/knowledge.py`

- **Content:** hand-written CESC content in English and Kannada:
  - the organisation: summary, formation (2005, out of MESCOM), ownership, area (5 districts), regulation
    (KERC / Electricity Act), head office and what this assistant covers;
  - the division and each sub-division / section;
  - KERC, the Electricity Act, the ESCOMs, KPTCL;
  - the DCB terms: the DCB statement, collection efficiency, arrears, installations, DC/MNR, FAC, LT/HT, tariff
    categories, miscellaneous revenue, fiscal year;
  - the schemes behind categories: Bhagyajyothi / Kutirajyothi, Gruha Jyothi, IP-set subsidy.
  - It holds no figures, and no office holders or tariff rates (they change).
- **Routing:** general questions with no data intent are answered from it before the data routes. It is also the
  fallback when the model is offline or declines. "Tell me about Arkalgud" still gets the data summary; places are
  explained only on what / where / why.
- **Glossary precedence:** where the knowledge base has an entry it wins over the locked glossary. In particular the
  glossary's DC/MNR text ("Dead Connections / Non-Recovery") is wrong: **MNR = meter not recording, DC =
  disconnected**. "What is LT-4?" still uses the glossary.
- **Integrity:** the YAML is now in `integrity_manifest.json`.

### 13.5 Kannada — `app/kannada.py` (KPCL only translates UI labels; this goes further, deterministically)

- **Rewrite:** a Kannada question is rewritten into English for the routes, with no model and no translation service.
  It maps Kannada digits, place names with any case suffix ("ಅರಕಲಗೂಡಿನಲ್ಲಿ", and phonetic spellings like
  "ಆರ್ಕಲ್‌ಗುಡ್"), DCB terms, tariff words, year phrases and question words ("X ಎಂದರೇನು" → "what is X").
- **Answers:** a figure answer starts with "ನಿಮ್ಮ ಪ್ರಶ್ನೆಯನ್ನು ಹೀಗೆ ಅರ್ಥೈಸಲಾಗಿದೆ: “…”", showing how the question was
  understood, so a misreading is visible. Figures and their wording stay English. Knowledge-base answers come back in
  Kannada.
- **Fallback:** a question with nothing recognisable goes through unchanged, as before.
- **UI:** the chat's language choice is remembered (localStorage, failure-safe), the viewer is translated, and the
  welcome text now says English or Kannada.

### 13.6 Gemini removed everywhere — the model is only the VPN gateway in `.env`

- **Code and dependencies:**
  - `config.py`: the `gemini_api_key` / `gemini_model` settings are gone.
  - `requirements.txt`: `google-genai` removed (and uninstalled from `.venv`); `pyyaml` added for the knowledge base.
  - Gemini comments and the eval scripts' Gemini checks now check `settings.llm_configured` (the `SQL_VERIFY_*`
    gateway).
- **Plaintext key removed:** `CESCOM POC (17).txt` held a **live Gemini key in plaintext**; the key lines were removed.
  **That key should be revoked**: it was in a plaintext file that travels with a zip of this folder.
- **Configuration:** `.env` and `.env.example` hold only the gateway settings. §2/§3's Gemini setup text above is
  historical.

### 13.7 Kept answers stay in server memory — decided after checking KPCL

- **KPCL's design:** answers are kept in memory only — 3 h, at most 500, oldest dropped, forgotten when the data
  version changes, nothing written to disk. Cescom's `answer_report.py` already does exactly that.
- **Why no persistence:** disk persistence (SQLite / JSON / Redis / Postgres) was judged not worth it for this POC.
  - It would add the only writable file to an otherwise read-only app.
  - A mart rebuild changes the data version and needs a restart anyway, so kept answers would be invalidated then.
  - When a kept answer is lost, the user just asks again.
- **When to revisit:** if the app is deployed with several workers or servers (then Redis or Postgres).

### 13.9 KPCL's immersive / responsive UI elements, in the Cescom look (2026-10-01, by request)

KPCL's whole frontend was read (chat, dashboard, insights, report viewer; read-only). Its interaction patterns were
ported; its styling was not. New shared `static/ux.js` + `static/ux.css` use only Cescom's tokens (navy, red, slate,
green; the Reports page keeps its own Entity A/B colours).

- **Chart cards everywhere:** KPCL's chart-type toggle, with icons and labels.
  - The types are **Bar / Line / Doughnut** on every chart. Area was removed by request, and the earlier "Share"
    special case is gone.
    - A doughnut has one slice per year or category; several series become concentric rings, with a caption saying
      which ring is which.
    - Tooltips show the real figure, plus the share of the total for additive measures (not for %).
    - Negative values draw as empty slices.
  - Gradient bars, area fills, 650 ms ease-out animation, tooltips and axis ticks in the answer's unit (₹ crore /
    lakh, %, counts), and an SVG fallback when the Chart.js CDN is unreachable.
  - On phones the toggle is icon-only. Dashboard and Reports remember each panel's chosen type (localStorage).
- **Chat:**
  - **Waiting and streaming:** an indeterminate sweep bar above the live stage steps and a blinking caret while the
    answer types out.
  - **Scroll and input:** auto-scroll only when the reader is near the bottom (scrolling up to re-read is never yanked
    back); one question at a time (Enter can no longer send a second question while one is running; IME-safe); "/"
    focuses the question box.
  - **Buttons:** icon buttons for Copy, View report and Download (with an XLSX badge, a spinner while busy, a tick when
    done, and a toast either way).
  - **Entrances:** animated reveals for the chart, table and actions.
- **Dashboard:**
  - **KPIs:** numbers count up, and the donut % counts up with an eased fill. Each KPI card has a sparkline of its
    series over the years; `/api/dashboard_summary`'s `yearly_demand` now also carries `collection_grand_total` and
    `closing_balance_total` (additive fields).
  - **Panels:** Demand trend and Live installations get the toggle; sub-division bars grow in from zero; KPI cards
    enter staggered; MIS downloads show a toast.
- **Reports:** the toggle on all three comparison charts. **Insights:** staggered card entrance.
- **Fixes along the way:**
  - Family and sub-division comparison charts now carry their unit (`reports.py`); their series columns are named
    after the families / places, so the chat axis used to show raw rupees.
  - The "Demand trend" header wraps cleanly.
- **Reduced motion:** every animation is switched off under `prefers-reduced-motion`.
- **Verified in headless Edge:**
  - chat toggles switch the chart type;
  - the dashboard count-ups land on the API figures (₹354.16 Cr / ₹330.01 Cr / ₹24.16 Cr / 93.2 %), with 3 sparklines;
  - the remembered chart type survives a reload, and a drill-down redraw leaves one toggle per panel;
  - Reports has 3 toggles; the MIS viewer and View report still work;
  - no horizontal overflow at 390 px on any page;
  - **zero console errors or CSP violations**.
- **Tests:** pytest 190 passed, stress 139/139.

### 13.10 Meeting action items built without needing a decision (2026-10-01)

These are the items from the CESC meeting note (consumers in comparisons, amount + % DCB, MNR, FPPCA) that the data
already supports.
- **Mart:**
  - **New columns:** `dc_installations`, `mnr_installations` (2018-19 on), `dc_column_installations` (the sheet's own
    DC column), `ob_fac`, `closing_balance_fac`.
  - **New derived ratios:** billed %, DC/MNR % (as printed), MNR %, metered / assessed consumption %, FPPCA share of
    demand, FPPCA collection %, FPPCA per unit, collection / arrears per installation.
  - **New build check:** the app's DCB percentages reproduce the sheet's printed ones. **474 printed values matched**
    (collection efficiency, live %, billed %, DC/MNR %, metered %, assessed %, arrears ÷ demand). 17/17 checks pass.
- **Formulas found by testing every printed grand-total row:**
  - The sheet's "% of DC/MNR" is the DC column alone ÷ total installations (MNR not included from 2018-19).
  - Its "% collection efficiency without adjustment" is **not computed one way** (cash/demand in some years, + tax
    adjustment in others, neither in 2025-26), so it is **not shown** (KI-14, awaiting CESC).
- **Registry:** 36 metrics (was 20): FPPCA billed / collected / outstanding / share / collection efficiency / per unit,
  DC, MNR, MNR %, DC/MNR %, billed %, metered / assessed %, assessed and metered consumption, collection and arrears per
  consumer. Synonyms include "FPPCA", "fuel and power purchase cost adjustment", "MNR", "meter not recording", and
  Kannada words for FPPCA / MNR.
- **Known issues:**
  - KI-13: FPPCA was a refund in 2021-22.
  - KI-14: "% without adjustment" is not computed one way.
  - KI-15: DC = direct connection (confirmed by CESC, 2026-10-01: supply without a meter while a faulty meter awaits replacement; KERC: new meter within 3 days, gap billed pro-rata on the previous month; no caveat now). Earlier it was unconfirmed.
  - KI-16: DC and MNR are separate only from 2018-19.
  - Caveats can now target metrics (`metric:key@fy`).
- **DC wording:** every place that asserted what DC means (column docs, knowledge base, MIS Notes, metric synonyms
  like "dead connections") is now neutral.
- **Chat:**
  - FPPCA / MNR / DC / % questions are answered deterministically.
  - "share" inside a metric's own name is no longer read as a breakdown request.
  - FPPCA's full name no longer trips the "losses not in this data" note.
  - A measure missing for a year (MNR in 2016-17) is named, with its caveat.
- **Dashboard:**
  - The KPI cards show % alongside amounts (collected / not yet collected % of demand, installations).
  - Sub-division rows show collected % and outstanding %.
  - **New row:** "DCB at a glance (%)" (the sheet's percentages, change in points, per-consumer figures), "FPPCA"
    (billed / collected / outstanding / per unit with %), and "Meters: MNR & DC". The `indicators` block in
    `/api/dashboard_summary` (division and drill-down) comes from the mart.
- **Reports:**
  - A side-by-side table for the latest common year: consumers and their share, live %, demand share, collection %,
    and demand / collection / arrears per consumer.
  - Three per-consumer charts (with toggles).
  - The two entities are aligned on the union of their years (Saligrama no longer cuts the chart to 3 years).
  - Sub-division series now read the mart (the same printed totals).
- **Excel MIS:**
  - 17 more measures on every sheet; ratios get no "change %" and are never summed.
  - FPPCA and assessed-consumption / MNR observations.
  - Notes corrected.
  - A real bug fixed: column letters beyond Z (`chr(65+i)` gave '['), also in the answer report.
- **Verification:**
  - pytest **204 passed** (new `tests/test_new_measures.py`: every figure against the star schema's printed rows),
    stress 139/139, regression 49/49.
  - `verify_against_raw.py` PASS.
  - Headless Edge: all panels render with live data, no overflow at 390 px, zero console / CSP errors.
  - The locked folders' hashes are unchanged.

### 13.6a `.env` = the KPCL project's settings (2026-10-01, by request)

- **Gateway:** `SQL_VERIFY_MODEL` / `_BASE_URL` / `_API_KEY` are now copied exactly from `D:\KPCL POC\KPCL POC\webapp\.env`
  (the KPCL file was only read).
- **App settings:** KPCL's values under this app's `DCB_` names: query timeout 30 s, max rows 200, prompt budget
  20000 (declared, not read by any code), technical details off.
- **Cescom only:** `SESSION_SECRET`, now a random value, because KPCL has no login. The unused `SQL_VERIFY_ENABLED`
  line was dropped.
- **Backup:** the previous `.env` was backed up outside the project, in the session scratchpad.
- **Re-verified with the new key:** pytest 190 passed, stress 139/139, regression suite 49/49 (model path answering).

### 13.8 Verification (real runs, 2026-10-01)

- **Mart:** `DCB_Mart/build_mart.py` → 15/15 PASS. `DCB_Mart/verify_against_raw.py` → PASS (0 failures). The locked
  folders' hashes were identical before and after.
- **Offline tests:** `pytest tests -q -p no:cacheprovider` → **190 passed** (was 129).
- **Live stress bank:** `tests\run_stress.py` → **139/139**.
- **Live regression:** `python -m tests.run_regression` → suite **49/49 OK** (was 46/49 in 12.6; 37/49 for the
  pre-port pipeline). Across the 49 + 82 questions the model was used for only 5 (3 `llm`, 2 `llm_declined`).
- **UI (headless Edge over the DevTools protocol):**
  - the dashboard MIS viewer opens with its 6 sheets and draws its chart;
  - the chat's "View report" opens the answer workbook's 4 sheets;
  - the slab and Kannada answers render, and the Kannada choice survives a reload;
  - at 390 px there is no horizontal overflow;
  - **zero console errors, page errors or CSP violations.**

## 14. 2026-10-01 second pass — tariff booklet everywhere, subsidy, AT&C, % everywhere, tenders (GeM + Bid Assist)

Done at the user's request, item by item from the meeting list (item 4, MNR / DC: DC = direct connection is confirmed and applied; MNR replacement tracking is still on hold).

### 14.1 The CESC tariff booklet is the single source for every tariff (item 8)
- **Booklet:** `CESCOM Tariff Categories.pdf` has 49 phone scans and no text layer. It was read page by page, and the full
  text was transcribed into `CESCOM Tariff Categories - transcription.md` (project root). Booklet page 2 is missing from the scan.
- **Structured source:** `DCB_Mart/metadata/tariff_booklet.json`. It holds:
  - the 22 categories (12 LT, 10 HT), each with who it applies to, notes, page and 3 years of rates;
  - 24 general rules;
  - `data_code_map`, which maps the codes the DCB sheets printed in every year to today's category:
    - LT-2(a) domestic → LT-1;
    - HT-3(a)(i) → HT-7;
    - HT-3b → HT-6;
    - LT-7(b) → LT-3(b).
- **Mart (`build_mart.py`):**
  - `booklet_code` on the slab dims and facts;
  - `mart_fact_booklet_division_year` / `_subdivision_year`, comparable across all 11 years;
  - `mart_tariff_booklet_category` / `_rate` / `_rule`;
  - `mart_tariff_rate_check`, the data's billed energy rate vs the booklet.
  - Build checks:
    - every LT/HT slab maps to a category;
    - the booklet rows add up to the LT + HT families;
    - booklet energy rates agree with the billed rate within 6%: 14 categories, LT-1 581 vs 580, LT-4(a) 817 vs CDT 830, HT-7 475 vs 475.
- **Chat:** `app/tariff_booklet.py` runs before the glossary, with no model call. It answers:
  - rates ("What is the LT-3 tariff rate?");
  - membership ("Which category is a homestay?", with an HT preference);
  - "what is LT-4(a)";
  - lists of sub-categories;
  - rules (late-payment interest, reconnection, ToD, electricity tax, power factor, DERS, cross-subsidy surcharge, wheeling…);
  - "does the billed rate match the booklet".

  Data questions are never taken. There are guards for data words, fiscal years and billed/collected wording; see the
  regression note in 14.6.
- **Glossary:** the locked semantic layer's tariff-code glossary predates the 2024-25 restructuring (LT-1 was
  "usage-slab tiered"). It is replaced in memory in `semantic_layer.load_semantic_layer()`, so the prompt, the glossary
  route and About all read the booklet. The file on disk is never written.
- **About page:** the tariff-code glossary comes from the booklet. New sections cover every category with 3 years of
  rates and notes (filterable), plus the general rules.
- **Labels:**
  - family labels in `FAMILY_LABELS` follow the booklet;
  - the KB entries (LT/HT, BJ/KJ, Gruha Jyothi, IP sets, tariff categories) were rewritten, and new entries added for
    AT&C, billing efficiency, adjustment/subsidy and the booklet;
  - KI-01 was reworded as a real tariff restructuring.
- **Dashboard, Insights, Reports, MIS:** all show tariff categories on the booklet basis.
  - Insights' "largest jump" used to show LT-1's 2024-25 renumbering as growth.

### 14.2 Subsidy / Gruha Jyothi (item 6)
- **Cash vs adjustment:** collections are printed as "Chq/cash" and "Adjust".
  - The mart adds `collection_cash_total` and `collection_adjust_total` (summed over the 5 heads), plus `adjustment_share_pct`.
  - Build check: cash + adjust = the printed collection total on every row.
  - Misc revenue lines put stray values in "Adjust" that are not in their total (KI-17). They are counted as cash; on
    the grand total the printed misc subtotal is used.
- **What the data shows:**
  - Domestic LT-1 (booklet basis) had an adjusted share of 16–31% up to 2022-23, 60% in 2023-24 (Gruha Jyothi from
    August 2023), and 82–84% in 2024-25 / 2025-26.
  - LT-4(a) is 100% adjustment, which matches the booklet's note that GoK pays the CDT.
  - **Confirmed by the user for CESC (2026-10-01): the adjustment is the Government subsidy.** KI-18 is accepted, and the
    labels everywhere now read "Government subsidy" (no longer "mostly").
- **DCB lines the booklet does not name (KI-20, `tariff_booklet.json` `data_lines`):**
  - LT-5(b) is LT-5. The booklet has only LT-5, with no sub-categories, and the user checked this; LT-5(a) has no
    installations here.
  - "(FL) Free Lighting" is LT-1 Domestic: 1–8 installations, 85–95% subsidy.
  - "(OL) Office Lighting" is LT-3(a): "all offices", paid 99–100% by adjustment.
  - (i) / (ii) split a category into urban local bodies / city corporations and village panchayats.
  - These lines are in the tariff glossary and the KB, and a bare "What is LT-5(b)?" says the booklet has only LT-5.
  - The live regression caught two over-captures, both now fixed and tested: "What is LT-5(b) … slab in Saligrama"
    stays a slab question, and "round to the nearest rupee" is not the bill-rounding rule.
- **Where it appears:**
  - chat metrics `adjustment_collection`, `adjustment_share` and `cash_collection`, with synonyms such as "subsidy" and
    "gruha jyothi subsidy";
  - a dashboard indicator, an Insights card, Reports series and MIS columns.

### 14.3 AT&C loss (item 7)
- **No energy input in the data.** The raw workbook (every sheet) was searched; it has no energy-input figures.
- **What the sheet does have:** its own consolidation block prints "Billing Efficiency (4/3)" = installations billed / live.
- **Formula:** `atc_loss_pct = 100 − billing efficiency × collection efficiency`, with that billing efficiency (KI-19).
  - It is negative in arrears-recovery years.
  - FY 2025-26: division 6.95%, Hangarahalli 11.64%, Konanur 2.59%.
- **Chat:** metrics `atc_loss` and `billing_efficiency`. `mart_layer.metric_sql` now brackets the numerator and
  denominator, because AT&C's numerator is a difference of products.
- **Pivot reports:** `mart_layer.metric_sql_filtered` rewrites any formula per series, so AT&C trends by sub-division
  and family are correct.
- **Capability "losses":** AT&C is now answered. Energy and technical losses are still "not in data".

### 14.4 Amount and % everywhere (items 2, 3)
- **"Without adjustment":** `collection_efficiency_without_adj_pct` uses the sheet's header formula (34 − Adj)/30 =
  cash/demand. KI-14 is now accepted. The column the sheet prints is inconsistent and is not used.
- **Chart tooltips (bar/line, `ux.js`):**
  - share between the series at that point, or of the whole for a category chart;
  - YoY % change on a fiscal-year axis, and points for ratios.
- **Chat:** an amount for a sub-division, family or LT/HT now adds its "% of Holenarasipura Division" (or of the
  sub-division).
- **Reports (consumer population):**
  - side table: consumer population as % of the two and of the division; growth; live % and billed %; demand % of the
    two and of the division; cash and adjustment with their %; billing efficiency; AT&C;
  - 6 new % charts: consumer share, consumer growth, demand share, subsidy share, cash efficiency, AT&C.
- **Dashboard:**
  - 4 new indicator bars;
  - top categories show booklet names and the % of consumers;
  - the demand KPI shows live %.

### 14.5 Tenders — GeM format + CESC delegation of powers + Bid Assist-style analyser (items 1, 9, 10)
- **Research:**
  - gem.gov.in;
  - a published GeM bid PDF, GEM/2025/B/5970949 (36 pages, saved as `tests/fixtures/`). It gave the exact format,
    standard clauses and the 15-item null-and-void list;
  - GFR Rule 149, as revised 10.07.2024: ≤ ₹50k direct purchase; ₹50k–₹10 lakh L1; > ₹10 lakh bid / RA;
  - nexizo.ai, which blocks fetching (403), so its published feature descriptions were used: key-field extraction,
    eligibility check, shortlisting;
  - the DoP manual, pages re-read for exact figures:
    - invite tenders: EEE 25k / SEE 1 L / CEE 3 L / MD full;
    - accept open tenders: EEE 1 L / SEE 5 L / CEE 10 L / MD 3 Cr, then CPC 3–10 Cr and Board > 10 Cr;
    - accept limited tenders: 25k / 50k / 1 L / 5 L;
    - KTPP cap: no limited tender above ₹5 lakh;
    - the single-tender guidelines;
    - the Annexure-1/2 penalty tables.
- **Module:** `app/tender.py` (no LLM):
  - `plan()`;
  - `draft`: the GeM bid-document sections plus CESC's internal approval note, as HTML, and as a dependency-free
    `.docx` (hand-written OOXML);
  - a compliance check against GeM's list (brand/make, physical documents, except an EMD/PBG demand draft; samples,
    foreign certification, specific-organisation experience, MSE/MII exclusion, external links, tender fee, EMD against
    GTC);
  - `analyse()`, which reads bilingual GeM PDFs by value shape (skipping Hindi glyph debris) and any tender text, and
    gives an eligibility verdict for a bidder profile.
- **API:** `/api/tender/rules|plan|draft|docx|analyse`. Protection allows 256 KB for tender forms and 15 MB for the PDF
  (`DCB_MAX_UPLOAD_BYTES`).
- **Dependency:** `pypdf` (added to `requirements.txt`).
- **Page:** `/tenders` (new nav item), in the Reports page shell:
  - a live plan;
  - the DoP ladder with the deciding officer highlighted;
  - the checks list;
  - preview, Word download and print.

### 14.6 Verification (real runs)
- **Mart:** 21/21 checks PASS.
- **pytest:** **280 passed**, including the new `test_tariff_booklet.py` and `test_tender.py`.
- **Stress:** 139/139.
- **Live regression:** **49/49 OK**.
  - It caught one wrong capture ("Govt charitable … — demand in Saligrama" taken by a booklet rule). Fixed: rules never
    take data questions, and the alias was narrowed. A test was added.
- **Headless Edge:**
  - dashboard, Insights, Reports (both modes, 12 charts), About (22 categories, 24 rules) and Tenders (draft flags
    "Make: Siemens", .docx; analyser verdict) all render;
  - zero page errors;
  - no horizontal overflow at 390 px.

## 15. 2026-10-05 — front end rebuilt to the RDPR / KPCL design, in the CESC website palette

By request, the whole front end now follows the RDPR / KPCL front end (layout, components, motion), coloured only from the
CESC websites. This **replaces** §12.5 / §13.9's "keep the old Cescom look". `D:\RDPR POC`, `D:\KPCL POC` and
`D:\Meghalaya_voice` were only read; the user permitted copying from them. No backend file changed; the 4 locked folders
and `DCB_Mart/` were not touched.

### 15.1 Palette (sampled from the live CESC sites on 2026-10-05)
- `cescmysore.karnataka.gov.in` (`style.css`): primary navy `#072577`, red `#d82221`, secondary orange `#e54825`,
  amber `#e38f00`, blue `#2271a0`, teal `#0f7291`, violet `#7c49ab`.
- `cescmysore.in` (consumer portal): `#151b54` main, `#121d81` hover, `#1725a5` side nav.
- Karnataka flag hairline `#ffcd00` over `#d82221` (also on the gov portal).
- The tokens are in `static/app.css :root`. Older names (`--red`, `--warn-bg` …) are aliased there.

### 15.2 What changed (all in `dcb-ai-app/app/static/`)
- **Shared design system: `app.css`.** Taken from RDPR's `app.css` and recoloured. It has:
  - the portal header, navy app bar, flag rule and paper sidebar with the drawer;
  - the gradient hero cards, count-up KPI cards (sparkline, ring, alert), panels and the sortable league table;
  - ranked bars, indicator bars, the segmented toggle with a sliding thumb, the MIS buttons and toasts;
  - skeletons and the loading bar.
- **Shell: `nav.js`.** It now drives the whole shell:
  - the section list, the EN / ಕನ್ನಡ switch for shell labels (key `dcb-lang`, shared with the chat) and the signed-in
    user chip with log out;
  - the drawer, the once-per-session opening splash (`Shell.ready()`) and the two-logo transition between pages.
  - `renderSiteNav()` is kept for compatibility.
- **Dashboard** (`dashboard.html/.js`, rewritten; same APIs and `?subdivision=` drill-down):
  - a "What stands out" takeaways card and a navy headline card (total demand, sparkline, collection / outstanding /
    installations);
  - five KPI cards: collection, outstanding, collection-efficiency ring, live %, AT&C;
  - demand vs collection (chart toggle) and a tariff-category share donut;
  - a sortable sub-division league with a collected / due split bar;
  - the DCB %, FPPCA and meters panels, top tariff categories as ranked bars, and live installations.
  - The sidebar lists the sub-divisions. "Ask AI about this" opens the chat with a stress-tested question
    (`latest figures` / `show me <sub-division> numbers`).
- **Insights:** RDPR's grouped KPI cards with a needs-attention / going-well / for-information filter. Each card has an
  "Ask the assistant" link to a verified question.
- **Reports, About, Tenders:** new shell; the same element IDs and scripts, minus their old transition code.
  - The Reports and Tenders tabs use the sliding segmented toggle.
  - Entity A / B are CESC navy / orange.
- **Login:** the splash as the page, with a white card.
- **Chat** (`chat.html/.js`, rewritten on RDPR's chat):
  - grouped sidebar questions (20) and 6 welcome cards, all answered live on 2026-10-05;
  - live stages, the typing caret, chart cards (`ux.js`, multi-series kept);
  - a data table with CSV, the View / Download report buttons and Copy;
  - Markdown tables (the tariff-booklet answers);
  - `?q=` deep links.
  - The text size and high contrast controls are kept, in the sidebar foot.
- **`ux.js` / `ux.css`:** CESC chart palette, RDPR's sliding-thumb chart toggle, a pointer guide line and staggered bar
  entrance.
- **Report viewer:** recoloured.
- **Assets:**
  - Chart.js 4.5.1 and the Noto fonts are vendored in `static/vendor/` (copied from RDPR), so pages work offline.
  - The CDN script tags are gone; the CSP still allows cdnjs, harmlessly.
  - New favicon `cesc-mark.svg`.
- **Backup:** the previous static folder is backed up in the session scratchpad (`static_backup_2026-10-05`), not in
  the project.

### 15.3 Verification (real runs, 2026-10-05; Cescom on port 8010, because 8000 was running the RDPR app)
- **pytest:** 284 passed.
- **Headless Edge (DevTools protocol) on all 6 pages at 1366×900 and 390×844:** zero console, page or CSP errors, and no
  horizontal overflow. Drill-down `?subdivision=Konanur` works.
- **Chat in the browser:**
  - LT-1 vs HT-2: the chart with its toggle, then the doughnut, the table, View report and Download;
  - "what is LT-4" renders its Markdown table;
  - "demand for LT-9" is refused;
  - the Kannada question is answered;
  - the report viewer opens;
  - Kannada and high contrast work.
- **Suggested questions:** 30 were checked with `/api/ask_once`. "government subsidy share of collection…" asks for
  clarification, so "subsidy share in …" / "subsidy share trend" are used instead.

### 15.4 Not done (open)
- **Voice mode (RDPR / KPCL / Meghalaya) is not ported.** It needs:
  - the gateway's ASR / TTS keys;
  - `voice.py`, `voice_guard.py` and `spoken.py`, re-tuned for DCB terms and sub-division names;
  - `/api/voice/*` routes;
  - CSP `'wasm-unsafe-eval'` and `microphone=(self)`;
  - spoken tests on the VPN.
  It waits on the user's decision.
- **Kannada scope:** the switch translates the shell, the chat and its question list. Dashboard, Reports, Insights, About
  and Tenders body text stays English, as before.
- **About text:** "How this app works" is unchanged text from §8 and describes the original single-model-call design.

### 15.5 Same day, by request: the original Cescom dashboard layout back, RDPR / KPCL look kept
The user wanted only the look from RDPR / KPCL, not a new dashboard. The dashboard now has the original Cescom
arrangement and boxes (§9.8–§9.12, §13.10):
- **Top left:** the 3 KPI cards (Total Demand as the navy feature card, Total Collection, Outstanding Balance), with
  their deltas, % lines and sparklines. Below them, Demand trend (demand only, with the chart toggle) and the
  Collection-efficiency donut.
- **Right:** the clickable Sub-divisions panel with collected / outstanding bars. A click opens that sub-division's
  dashboard (`?subdivision=`); a second click, "All sub-divisions" or Back returns.
- **Below:** Top tariff categories (the original rows) beside Live installations, then the DCB %, FPPCA and Meters row.

Also changed:
- The takeaways card, headline card, 5-KPI row, share donut, league table and sidebar sub-division list are removed.
- "What stands out" moved to the top of **Insights**. It reads `/api/dashboard_summary` and stays hidden when signed out.
- The "Ask the assistant" card now sits right under the navigation on every page and no longer shrinks or cuts off.

Verified in headless Edge at 1920 / 1366 / 390 px:
- KPIs 354.16 / 330.01 / 24.16 Cr;
- 5 sub-division rows, and the Arkalgud drill-down and back both work;
- all 6 pages: zero errors, no overflow.

### 15.6 Same day, by request: sidebar, first screen and AI-mode trims
- **Sidebar (dashboard, Insights, Reports, Tenders, About):**
  - Menu order: Dashboard, Ask the assistant, Insights, Reports, Tenders, About & glossary.
  - The big "Ask the assistant" card is gone. A quiet **Data status** card replaces it: latest FY, the period, whether
    the assistant's model is reachable, and the data version (from `/health`, `nav.js`).
- **Dashboard, first screen:** on screens at least 1100 × 640 px, `fitTop()` sizes the top area to end at the bottom of
  the window. That area holds the KPI cards, Demand trend, Collection efficiency and Sub-divisions; nothing in it is
  cut off, and the rest follows below. Phones keep the natural flow.
  - The chart switch sits on the title row and the hint on the line below.
- **Doughnut:** sized to match Bar and Line (`ux.js`: radius 74%, cutout 62%).
- **AI mode:**
  - Text size and High contrast are removed, with their code; a saved choice is no longer applied.
  - The welcome screen is now the title, its ornament and the six suggested questions. The paragraph and the
    4-figure stats box are removed.
- **Verified in headless Edge:**
  - at 1920×780 / 1920×920 / 1366×768 / 1366×900 the top area ends 18 px above the window bottom, and no child spills;
  - every page shows zero errors and no overflow.

### 15.7 Same day: the dashboard no longer shifts when a sub-division is clicked
- **Cause:** a sub-division's longer title, plus the back button beside it, pushed the View / MIS / Ask AI buttons onto
  a second row. The heading grew from 81 px to 135 px, so everything below dropped 54 px; at 1366×768 the panels' bottoms
  were also cut off again.
- **Fix:** the heading now has one fixed height.
  - The eyebrow row holds the line "Sub-division dashboard · Demand, Collection & Balance" and a compact
    "All sub-divisions" pill.
  - The title is just the sub-division name.
  - The sub-line is one line, and the action buttons never wrap on screens above 1100 px.
  - Smaller screens still wrap naturally.
- **Measured in headless Edge at 1920×920 and 1366×768:** division → each of the 5 sub-divisions → back keeps the
  heading at 133/88, the top area at 239, and every panel at an identical top and height. Zero errors, no overflow on
  any page.

## 16. 2026-10-05 — architecture compared with RDPR / KPCL; conversation layer, answer writing and charts upgraded

The full comparison is in `reports/architecture_comparison_2026-10-05.md`. RDPR and KPCL were only read.

**What was already on a par:** the data layer (mart, named checks, publish only on PASS), the semantic contract, the
read-only guards, the SQL guard, the deterministic routes and the reviewer.

**What was added:**
- **`app/small_talk.py`** (L0): replies to greetings, thanks and goodbyes, and strips abusive words from real questions.
  Route `small_talk:<kind>`.
- **`app/followup.py`** (L0'): rewrites "and Konanur?", "what about 2023-24?", "and the demand?", "and HT-2?" into the
  whole question.
  - `AskRequest.previous`, SSE `followup` event, and `done.asked_as` / `done.context`.
  - The chat sends `previous`, shows "Taking this as", and clears it on New conversation.
- **`app/premise.py`** (L12): recomputes stated rupee / % / installation figures through `metric_lookup`; a contradicted
  figure leads the answer with a correction.
- **`app/answer.py`:**
  - RDPR-style narration: the range plus latest vs first for years; the leader with its share and runners-up for
    rankings.
  - `polish()` runs on every route: the exact rupee amount stays in the headline only (unless asked), and caveats get
    a "Note:" label.
- **`reports.py` `_series_story`:** growth multiples, and which series was higher in how many years.
- **`slab_lookup.py`:** `clean_desc`, `short_name` and a compact "What was counted" list.
- **`static/ux.js` charts:**
  - horizontal bars for many or long category names;
  - flat (shortened) fiscal-year ticks;
  - truncated labels with full-name tooltips, at most 6 value ticks;
  - narrow-range percentage lines zoom to their range;
  - capitalised titles.

**Tests:**
- pytest **315 passed**, including the new `tests/test_conversation.py`; `hello` now routes to `small_talk:greeting`.
- Stress **139/139** (on port 8010).
- Live regression **49/49**.

**Not done:** voice mode (needs the ASR / TTS keys and backend; waits on the user).

### 16.1 Same day: Insights and About trimmed
- **Insights:** the "What stands out" card is removed (markup and `loadTakeaways`). The filter row and the 7 insight
  cards remain.
- **About & glossary, rebuilt to remove repetition:**
  - a 5-step "How the assistant answers" strip, replacing the old long text;
  - one search box over three tabs: Terms (18), Tariff categories (22) and Booklet rules (24). The search ignores
    spaces and hyphens ("homestay" finds "home stay") and opens the tab that has matches.
  - **Tariff categories** merges the old tariff-code glossary into the booklet: one card per category with its
    FY 2025-26 fixed / energy rates; full description, three-year rates and notes under "Details"; old DCB codes
    (LT-2(a), HT-3b, (FL), (OL), 61.xxxx …) in one compact list.
  - **Terms are display-only fixes** (`TERM_FIX` in `about.js`); the locked semantic layer is not changed. They give
    the confirmed DC / MNR meaning and FPPCA wording, and hide the database-internal "Sub Total / LT-TOTAL" entry.
- **Verified in headless Edge** at 1366 and 390 px: no errors, no overflow. Content height is about 1,300 px at 1366.

## 17. 2026-10-05 to 10-07 — voice mode (ported from Meghalaya_voice → KPCL → RDPR, tuned for DCB) and the full test sweep

**Guardrail kept:** `D:\RDPR POC`, `D:\KPCL POC` and `D:\Meghalaya_voice` were only read. Nothing in them was edited
or run, and no RDPR module was imported. Copied from RDPR, with the user's permission:
- the voice front end (`app/static/voice/*`);
- the Silero VAD files (`app/static/vendor/vad/*`, checksums in `VERSIONS.txt`);
- `voices/officer_serena.wav`;
- the ASR_ / TTS_ lines appended to the project-root `.env` (the original is backed up in the session scratchpad).

The four locked `DCB_*` folders were not touched.

### 17.1 How a spoken question flows
1. **In the browser:** Silero VAD records the question.
2. **`POST /api/voice/transcribe`:**
   - qwen3-asr on the VPN gateway, with `ASR_CONTEXT`: the subject, the DCB terms and **the division's six place
     names**. This follows KPCL, not RDPR, and was measured on 2026-10-07: the place-name questions went from 147 to 212
     of 232 spoken trials (§17.3). It started terms-only, RDPR style.
   - **Guards:**
     - silence;
     - too short / fragment, except that a follow-up such as "and Konanur?" and a lone greeting are kept
       (`followup.is_fragment`, `small_talk.kind`);
     - too long;
     - other script — a short mixed-script transcript is first spelt out in Latin letters by `voice_guard.latinise`
       ("And कौन अनुर?" → Konanur);
     - the prompt echoed back, or read on after the question;
     - the same question repeated within seconds.
   - **`spoken.clean_transcript_ex`:**
     - sub-division names by sound and consonant skeleton (Arakalagodu / Erkalgu → Arkalgud, Hangara halli →
       Hangarahalli);
     - spoken years ("twenty twenty four twenty five", "FY twenty two to twenty three", "in 2020 to 23" → 2022-23);
     - spoken tariff codes ("L T four A", "L4", "HT to a" → LT-4(a) / LT-4 / HT-2(a));
     - acronyms (D C B, F P P C A, A T and C / "80 and C", M N R);
     - "an" heard for "and", and the Gruha Jyothi / arrears / assessed / billed mishearings.
   - **"Did you mean …?"** only for a truly ambiguous word: "FY 24", a bare "2024", "LT for" / "HT to" (four / two
     homophones).
3. **`/api/ask` with `mode:"voice"`:** the normal pipeline, unchanged. The SSE `spoken` event is sent before the
   answer text, and `done.spoken_answer` repeats it.
4. **`main._spoken_for_voice`, under the voice guardrails (`app/voice_guard.py`):**
   - a refusal that may be a mishearing → "I heard: … Could you say it again?";
   - a model answer that failed the second check → "on your screen" only;
   - otherwise the place and period are read back (unless the answer says them);
   - every spoken figure must be a screen figure with its unit, else "The figures are on your screen".
5. **`/api/voice/speak/stream`:** Qwen3-TTS, Base clone of the officer clip, 24 kHz PCM; `spoken.pronounce` runs first.
   - The persona is `dcb_voice_instructions`: 427 characters, because the gateway refuses more than 500.
   - Fillers are cached at startup.

**`app/spoken.py`** writes 1–3 sentences from the verified answer, with no model:
- **Headline:** a single figure is said exactly; a ranking gives both ends; a trend gives the story (range, highest /
  lowest), never the first rows.
- **Left out:** brackets, slab descriptions, "(whole division)", formulas, screen-only notes.
- **Definitions** are said whole, without their asides.
- **Capability notes:** first sentence only.
- **Units:** kW, kVA and HP are said in full.

**Security:**
- CSP adds `'wasm-unsafe-eval'` only.
- `Permissions-Policy: microphone=(self)`.
- Audio body cap `VOICE_MAX_AUDIO_BYTES` (transcribe only).
- A separate voice rate budget.
- `/health` now reports `voice.speech_in` / `speech_out`.

### 17.2 Pipeline fixes found by the voice sweep (they apply to typed questions too)
- **Unknown places:** a capitalised unknown place after "in / of / for / at" ("Channarayapatna") is refused by name,
  never answered with the division's figures (`router._NAME_AFTER_RE`). "hn pura sub-division" is no longer flagged.
- **Partial slab codes:** "HT-2(a)" resolves to its printed sub-slabs (HT-2(a)(ii)).
- **Tariff booklet:** "What is the HT-2(a) demand charge?" goes to the booklet. A general-knowledge question ("CESC's
  share price") never reaches the tariff route.
- **Refusals:**
  - `capability.check` now runs before the knowledge routes, so "technical loss in Konanur" gets the losses note.
  - "each / every / individual consumer" and "paid online" are refused.
  - "arrears per consumer" (a real average) is still answered.
- **Knowledge base:**
  - Matching ignores singular vs plural ("a DC installation").
  - "What sub-divisions does the division have?" gets the place card.
- **Subsidy questions:** "subsidy … government" is the GoK payment, not the government tariff slab.
- **Shares:** "What share of demand comes from IP sets?" now gives the group's share of all demand.
- **Ranking with "why":** "Which sub-divisions need attention first, and why?" gets the low-collection-efficiency
  ranking, not a demand trend.
- **Typos:** misspelt measure words ("colection efficency", "demnad", "arrers", "instalations") are read as the
  measure (`router._fix_measure_typos`). Real word forms sharing a 7-letter stem ("collects", "collecting",
  "installed") are left alone.
- **Follow-ups:**
  - "fy 22-23" is one period span.
  - "the number of installations" counts as a follow-up.
  - A `previous` over 600 characters is ignored (the model now allows up to 4,000).
- **Answer text:**
  - The AT&C trend now carries "%" (`answer.col_unit` falls back to the measure registry).
  - "had the higher demand than" → "had higher demand than".

### 17.3 Tests (question banks at RDPR / KPCL length and style), all run live on the VPN on 2026-10-07 on the final code
| Suite | File | Result |
|---|---|---|
| Offline pytest (incl. `tests/test_voice.py`, 159 tests) | `python -m pytest tests -q -p no:cacheprovider` | **474 passed** |
| Stress bank, typed (334 questions; RDPR 318, KPCL 213) | `python tests/run_bank.py --mode text` → `reports/STRESS_TEST_QUESTIONS.md` | **334/334** |
| Stress bank, voice mode (typed) | `python tests/run_bank.py` → `reports/STRESS_TEST_QUESTIONS_VOICE.md` | **334/334** |
| Follow-up conversations (152 conversations, 802 turns) | `python tests/run_followup_stress.py` → `reports/FOLLOWUP_STRESS_TEST.md` | **802/802**; fresh seeds 4242 and 99 also 100% |
| Spoken stress (660 trials: Ravi + Heera) | `python tests/run_voice_stress.py` → `reports/VOICE_STRESS_TEST_QUESTIONS.md` | **634/660 (96.1%)**; all 26 misses pass typed (hearing). RDPR 92.5%, KPCL 99.2% |
| Spoken follow-ups (590 turns) | `python tests/run_voice_followup_stress.py` → `reports/VOICE_FOLLOWUP_STRESS_TEST.md` | **549/590 (93.1%)**; all 41 misses pass typed. RDPR 91.3% |
| Test bank / sample Q&A docs | `python tests/make_question_docs.py` → `reports/chatbot_test_questions.md`, `chatbot_sample_questions.md` | **46/46 answered, 10/10 refused** |
| **Demo list** | `python tests/make_demo_questions.py` → **`reports/DEMO_TEST_QUESTIONS.md`** | **310 proven** (typed + voice mode + both voices), 4 typed-only, 20 "type it or say it clearly", 0 not proven; 23 demo picks, 9 follow-up conversations |
| Live typed stress (server on 8010) | `python tests/run_stress.py --base http://127.0.0.1:8010` → `reports/stress_test.md` | **139/139** |
| Live model regression | `python -m tests.run_regression` → `reports/regression_kpcl_port.md` | **49/49**, 0 worse |
| Browser, end to end | headless Edge, fake microphone playing a recorded question | heard → answered → spoken (3 streamed sentences), no page errors |
| Speech round trip | TTS of an answer fed back to ASR | word-perfect; 13.3 s of audio generated in 3.8 s |

**What the live spoken runs changed:**
- **ASR_CONTEXT** now names the six places, KPCL style. This took the place-name questions from 147 to 212 of 232
  spoken trials, and the whole spoken run from 472 to 634 of 660.
- **Name scoring:** a consonant-skeleton comparison ("Erkalgu" → Arkalgud).
- **Year repairs:** "in 2020 to 23" → 2022-23; "FY twenty two to twenty three" → FY 2022-23.
- **Clean-up rules:** "An in …" → "and in …"; "HT to a" → HT-2(a); "L4 / H2 + measure" → LT-4 / HT-2; "80 and C" →
  AT&C; "Assist consumption" → assessed; "build rate" → billed; "Chenpura" / "Hn Purak" → HN Pura.
- **Devanagari:** a short transcript like "And कौन अनुर?" is spelt out in Latin letters (→ Konanur). A whole sentence
  in another language is still refused.
- **Small talk:** a lone "hello" / "thank you" is answered, not dropped.
- **Typed-path fixes found live:** "average billed rate per unit" and "subsidy share for LT-1" are answered by the
  deterministic route; "average" / "share" are absorbed into the measure name.
- **Test fix:** `speakify` had voiced codes as "L four"; it now says "L T four".

**Known residue (hearing only):**
- The Windows voice's odd readings ("ARKALGUD" in capitals, "arakalagud") were snapped once each to "Holenarasipura
  Division". The voice reads the place back.
- "Konanur" was heard as "Continuous" / "Call a new"; "write off" as "Right of".

**Notes on the banks:**
- **Independent figures:** every expected figure is recomputed from the locked star schema by SQL independent of
  the chatbot.
- **Consumption:** checked against the line items, because Holenarasipura's 2023-24 grand-total consumption cell is
  blank in the sheet. The app sums the rows: 127,582,101 units.
- **Spoken checks:** single figures must also be in what the voice says.
- **Spoken runners:** each failure is re-asked as typed text, to separate a hearing miss from a pipeline miss.

### 17.4 Coverage against the KPCL / RDPR question files, and the demo list
Every question file in the two reference projects has a Cescom counterpart in `reports/`, all checked:

| KPCL / RDPR file | Cescom file |
|---|---|
| STRESS_TEST_QUESTIONS.md | STRESS_TEST_QUESTIONS.md |
| STRESS_TEST_QUESTIONS_VOICE.md | STRESS_TEST_QUESTIONS_VOICE.md |
| VOICE_STRESS_TEST_QUESTIONS.md | VOICE_STRESS_TEST_QUESTIONS.md |
| FOLLOWUP_STRESS_TEST.md | FOLLOWUP_STRESS_TEST.md |
| VOICE_FOLLOWUP_STRESS_TEST.md | VOICE_FOLLOWUP_STRESS_TEST.md |
| docs/chatbot_test_questions.md | chatbot_test_questions.md |
| docs/chatbot_sample_questions.md | chatbot_sample_questions.md |

Their question kinds map onto Cescom's data:
- station / zilla / taluk / GP → sub-division and section;
- company / state → the division;
- category / property → tariff family and slab;
- revenue head / units → measure;
- month / quarter / season → refused with a note, because the DCB statement is yearly;
- client-style (Cescom 26; KPCL 16, RDPR 20), names, time edge, relative time, definition, capability, guardrail →
  all present.

`reports/DEMO_TEST_QUESTIONS.md` is generated from the live reports, never by hand. A question counts as proven only
when it passed typed, in voice mode, and spoken by both voices. The 23 demo picks go first; the follow-up
conversations list their spoken pass counts.

### 17.5 Files this pass added or changed (all inside `New folder/`; nothing in the four `DCB_*` folders)

**Front end (copied from RDPR with permission, then re-branded):**
- `app/static/voice/{voice-agent.js, voice-ui.js, voice.css, pcm-stream.js, voice-text.js}`: `window.DCBChat`,
  `dcb-voice-prefs`, recoloured to the CESC palette.
- `app/static/vendor/vad/*` and `voices/officer_serena.wav`.
- `chat.html` / `chat.js`: voice button, `mode:"voice"`, the `spoken` event.

**New app modules:**
- `app/voice.py`: the ASR / TTS client.
- `app/voice_guard.py`: guards, read-back, unit check, `latinise`.
- `app/spoken.py`: spoken answers, transcript clean-up, `pronounce`.
- `app/voice_lexicon.json`.

**Changed app modules:**
- `main.py`: voice routes, `ASR_CONTEXT`, `_spoken_for_voice`, `/health` voice status.
- `config.py`: `ASR_*` / `TTS_*` settings and the persona; `models.py`: `mode`, `spoken_answer`, `previous` ≤ 4,000.
- `protection.py`: CSP, microphone permission, audio cap, voice rate limit.
- `small_talk.py`: spoken forms.
- `followup.py`: `is_fragment`, the "fy 22-23" span, fillers, `MAX_PREVIOUS`.
- The §17.2 fixes in `router.py`, `pipeline.py`, `capability.py`, `knowledge.py`, `guardrails.py`, `tariff_booklet.py`,
  `slab_lookup.py`, `reports.py` and `answer.py`.

**Tests:**
- New: `tests/test_voice.py`, `voice_bank.py`, `followup_bank.py`, `run_bank.py`, `run_followup_stress.py`,
  `run_voice_stress.py`, `run_voice_followup_stress.py`, `make_wavs.ps1`, `make_question_docs.py`,
  `make_demo_questions.py`.
- Changed: `run_stress.py` (adds a `cr` format for crore-only summary cards).

**Config and docs:**
- `requirements.txt`: adds `numpy`.
- `.env` (voice lines) and `.env.example` (voice placeholders).
- `HANDOFF.md` v3, `dcb-ai-app/README.md`, and this section.

### 17.6 Pitfalls hit in this pass (avoid them next time)
- **Regex in shell heredocs:** a `\b` written through a shell heredoc or an unescaped Python string becomes a backspace
  (`\x08`). The regex then silently never matches. Check with `grep -c $'\x08' file`; write patch scripts with the
  Write tool or raw strings.
- **TTS persona length:** the gateway refuses TTS instructions over 500 characters (HTTP 400). The persona is 427.
- **Bare years:** "March 2025" must not trigger the bare-year confirmation (the month guard in
  `_confusable_and_years`).
- **Test voicing:** `speakify` in the spoken runner once voiced "LT-4" as "L four", which made 50 false failures.
  Always read what the test voice actually said (the `said` column) before tuning the app.
- **Grand-total checks:** the Holenarasipura 2023-24 grand-total consumption cell is blank. Check consumption against
  line items, not `fact_dcb_grand_totals`.
- **Stale servers:** the long-running server must be restarted after code changes. The in-process runners
  (`ASGITransport`) always use the current code; a server on 8010 does not.

## Appendix — sibling project reference

`D:\BLH-BESCOM\` is a separate, unrelated-data sibling project (BESCOM,
not CESCOM) whose `CLAUDE.md` this file's documentation rigor was
explicitly modeled on, and whose chat UI (`src/webapp/templates/chat.html`)
this project's own `app/static/chat.html` follows for layout/UX
consistency. No code or content was copied between the two projects —
only conventions (env-var naming, documentation structure, UI layout
pattern) were deliberately matched where noted above.
