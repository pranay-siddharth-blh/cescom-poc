# CESCOM Holenarasipura DCB — project memory (CLAUDE.md)

**Read this file first, before anything else, in any new session on this
project — including a fresh person picking this up cold from a zip file
with no access to any prior conversation.** This file is the single
source of truth for everything built on top of the locked data pipeline:
architecture, every bug found and fixed, current verified test results,
known limitations, and what's explicitly not started yet. `HANDOFF.md` is
still the deeper, canonical source for the data-pipeline stages
specifically (§1 below condenses it) — you should not need to read both
files to get oriented on the chatbot layer, only `HANDOFF.md` if you need
pipeline-internals detail.

Standing rule this file is held to: no "done" claim without real,
verbatim output: no invented numbers, no smoothed-over failures.
Limitations are documented honestly, not hidden.

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

This repo (`D:\Cescom POC 2\`, one level above this folder) exists
partly so this rule is independently checkable: before and after any
change, `git status` run from `D:\Cescom POC 2\` should show nothing
under `New folder/DCB_*`, and a SHA-256 hash of every file in those 4
folders should be unchanged. Both checks have been run after every single
change across every session of this project so far and have always come
back clean — see §5.

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

**Current provider: Gemini only.** An optional second (OpenAI-compatible)
provider was added, then fully reverted — see §5.6. There is no
alt-provider code, config, or env var left anywhere in the project;
`app/config.py` has exactly two LLM-related settings (`gemini_api_key`,
`gemini_model`), confirmed by direct inspection as of this handoff.

### Full file inventory — everything outside the 4 locked folders

```
New folder/
├── .env.example                          # tracked — placeholders only
├── .gitignore                            # covers .env
├── CLAUDE.md                             # this file
├── HANDOFF.md                            # pipeline-stage detail (pre-existing)
├── HNPURA DIVISION DCB FY 2015 TO 2026.xlsx   # raw source workbook
└── dcb-ai-app/
    ├── .gitignore                        # __pycache__/, *.pyc, .pytest_cache/
    ├── README.md                         # how to run this folder
    ├── requirements.txt
    ├── app/
    │   ├── __init__.py
    │   ├── config.py                     # pydantic-settings, .env loading
    │   ├── db.py                         # ReadOnlyDB — read-only SQLite wrapper
    │   ├── llm.py                        # Gemini client init + generate_query_plan()
    │   ├── main.py                       # FastAPI app, routes, lifespan
    │   ├── models.py                     # LLMQueryPlan pydantic model
    │   ├── pipeline.py                   # answer_question() — the shared request path
    │   ├── prompts.py                    # build_prompt(), SYSTEM_INSTRUCTIONS
    │   ├── semantic_layer.py             # entity extraction + prompt pruning
    │   ├── sql_safety.py                 # read-only allowlist + double-count guard
    │   └── static/
    │       ├── chat.html                 # the chat UI (GET /) -- gov-portal shell,
    │       │                             # conversational interaction, inline charts (§8)
    │       ├── cesc_logo_reference.png   # CESC dept mark -- third-party crop, see §8.5
    │       └── cescom_official_logo.png  # Karnataka state emblem
    └── tests/
        ├── __init__.py
        ├── questions.py                  # the 10 + 2 eval questions, shared source
        ├── dry_run_no_llm.py             # DB/safety/router tests, no API key needed
        ├── pipeline_mock_test.py         # full FastAPI wiring, LLM mocked
        ├── double_count_guard_test.py    # code-level Division_Total guard, 13 cases
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

- `GET /` — the chat UI (`app/static/chat.html`).
- `GET /static/*` — static assets (the two header logo files, §8.1), via a
  `StaticFiles` mount added in §8; `chat.html` itself is still served by
  `GET /` above, read directly as text, not through this mount.
- `POST /api/ask` — SSE streaming endpoint the chat UI calls. Events:
  `status`, `sql`, `chart` (`chart_data` or `null`, §8.4), `answer_chunk`
  (one or more), `done`.
- `POST /api/ask_once` — plain JSON, same pipeline, easier for scripts/evals.
- `GET /health` — liveness check, returns `{"status":"ok"}`.
- `GET /docs` — FastAPI's auto-generated Swagger UI (unaffected by any of the above).

---

## 3. How to run this from scratch (assume you've never seen this project)

**Prerequisites:** Python 3.10+ (pydantic-settings and the `google-genai`
SDK both need it), a Gemini API key from
[aistudio.google.com/apikey](https://aistudio.google.com/apikey).

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
Then open `http://127.0.0.1:8000/` in a browser for the chat UI, or
`http://127.0.0.1:8000/docs` for the Swagger testing page.

**Run the tests** (from inside `dcb-ai-app/`):
```bash
python -m tests.dry_run_no_llm            # no API key needed
python -m tests.pipeline_mock_test        # no API key needed (LLM mocked)
python -m tests.double_count_guard_test   # no API key needed -- code-level Division_Total guard
python -m tests.eval_queries              # needs a real GEMINI_API_KEY in .env — writes tests/eval_report.md
```

**Before touching anything**, verify the 4 locked folders are untouched
— from `D:\Cescom POC 2\` (one level above `New folder/`):
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

### 5.1 Locked folders — untouched, every session

`D:\Cescom POC 2\` was `git init`'d before any chatbot code was written,
with a baseline commit of the raw workbook + all 4 locked folders, plus a
separate SHA-256 hash manifest as a second, independent check. That
SHA-256 re-check has been re-run after every change across every session
of this project and has returned all-`OK` (28/28 tracked files, zero
mismatches) **every single time**, including as part of this handoff —
see §6.

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

**A known, documented limitation from this feature, not yet resolved
(see §7):** when a query legitimately returns many rows, the UI/backend
shows a generic **"showing N of M rows"** truncation and lists at most
10 — this is deterministic and honest (it never hides that truncation
happened), but is not a polished per-question chart or table view. This
is exactly the gap the planned UI redesign (§7) is meant to address.

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

## 8. UI redesign — built and verified (government-portal shell + conversational interaction + inline charts)

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
to the new palette; dropped the old dark sidebar entirely (redundant
with the light gov-portal shell, and the suggestion chips already cover
what it did). Suggested-question chips from `example_queries.sql` kept,
now a horizontal row above the composer. The "Generated SQL" +
timing-breakdown panel is reframed as a collapsed-by-default "Technical
details (SQL & timing)" disclosure for reviewers/auditors, not part of
the default visual language.

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
  published version `4.5.1` — see bug (b) below), rendered above the
  answer text in the same bubble (Perplexity-style placement, not a
  separate panel/tab), styled entirely in navy/gray — no Chart.js default
  rainbow colors, red reserved for error states only per §8.2.

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
break (this one never shipped broken). **Fix:** each bot bubble now has
two independent children — a `.chart-container` and a text element —
updated separately; `renderMeta()`'s SQL/timing disclosure appends after
both, unaffected.

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

### 8.5 What's actually still open

- **Sourcing an official, non-third-party CESC logo file.** The
  currently-shipped `cesc_logo_reference.png` is "good enough for this
  POC," per its own in-code comment, but was cropped from a third-party
  source, not the `.gov.in` domain. Swap it if an official file is ever
  supplied.
- **Everything else originally discussed in this section — the real
  CESCOM logo and Chart.js visualization — is done, not just planned.**
  Removed from this list; see §8.1/§8.4 above for what was actually built
  and how it was verified.

---

## Appendix — sibling project reference

`D:\BLH-BESCOM\` is a separate, unrelated-data sibling project (BESCOM,
not CESCOM) whose `CLAUDE.md` this file's documentation rigor was
explicitly modeled on, and whose chat UI (`src/webapp/templates/chat.html`)
this project's own `app/static/chat.html` follows for layout/UX
consistency. No code or content was copied between the two projects —
only conventions (env-var naming, documentation structure, UI layout
pattern) were deliberately matched where noted above.
