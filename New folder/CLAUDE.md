# CESCOM Holenarasipura DCB — project memory (CLAUDE.md)

**Read this file first in any new session on this project.** It exists so
a fresh session doesn't have to re-read `HANDOFF.md` end to end or
re-derive the chatbot architecture from scratch. `HANDOFF.md` is still the
canonical, more detailed source for the data-pipeline stages — this file
summarizes it and then documents the chatbot layer built on top, to the
same standard the sibling `D:\BLH-BESCOM\CLAUDE.md` project holds itself
to: no "done" claim without real, verbatim output; limitations documented
honestly, not hidden.

## 1. What this project is (full detail: `HANDOFF.md`)

CESCOM (Karnataka electricity board) Demand-Collection-Balance (DCB)
billing data for Holenarasipura Division, 11 fiscal years (2015-16
through 2025-26), sourced from one raw Excel workbook. A 4-stage pipeline
turns that workbook into a queryable star-schema SQLite database:

1. `DCB_Segregated_By_SubDivision/` — raw split, human-readable, no reshaping.
2. `DCB_Staging_DB/dcb_staging.sqlite` — flat untyped staging table.
3. `DCB_Star_Schema/dcb_star_schema.sqlite` — typed star schema:
   `dim_fiscal_year`, `dim_subdivision`, `dim_tariff_category`,
   `fact_dcb` (safe to SUM), `fact_dcb_rollups` (the report's own printed
   subtotals — never SUM alongside `fact_dcb`), `reconciliation_report`.
   **Verified: 0.0% average and max reconciliation deviation across 292
   checks.**
4. `DCB_Semantic_Layer/` — glossary, tariff-code glossary, grounding
   rules, 10 pre-verified NL→SQL pairs (`example_queries.sql`), meant to
   be loaded straight into an LLM prompt (`semantic_layer.json`).

**All 4 of the above are LOCKED.** Never modify, regenerate, or write to
anything inside those 4 folders, or `dcb_staging.sqlite` /
`dcb_star_schema.sqlite` specifically, or re-run `build_staging.py` /
`build_star_schema.py`. This project's own git repo (initialized at
`D:\Cescom POC 2\`, i.e. one level above this folder) exists specifically
so a hash/diff check against the locked folders is always possible —
before touching anything, `git status` from `D:\Cescom POC 2\` should
show no changes under `New folder/DCB_*`.

11 real messy-Excel bugs were found and fixed while building that
pipeline (multi-row header drift across two eras, forward-fill bleed
across merged headers, an embedded orphan mini-table, character-
corruption typos like `ag`→`AF`/`C`→`B`, tariff-code forward-fill on
~1,046 rows, a rollup-row misclassification between `lt_ht_total` and
`grand_total`, floating-point noise fracturing decimal tariff codes,
etc.) — full list in `HANDOFF.md` §4. Known caveats that are real facts,
not bugs: **Saligrama** closed as a sub-division after FY2017-18;
**Division_Office** only exists FY2020-21–2023-24 (all-zero, an
administrative unit with no consumer installations); `demand_revenue_subtotal`
legitimately doesn't equal `demand_fixed_charges + demand_others +
demand_energy_charges` for the flat-fee Misc. revenue tariff codes
(`61.xxxx`/`62.xxxx`) — that's correct source data, not an extraction bug.

## 2. What this session built: `dcb-ai-app/` — the NL2SQL chatbot

A FastAPI backend in `dcb-ai-app/`, additive only — it only *reads*
`DCB_Star_Schema/dcb_star_schema.sqlite` and `DCB_Semantic_Layer/semantic_layer.json`,
opened read-only, never written to. This was scoped in `HANDOFF.md` §6 as
"FastAPI + Gemini + Neon Postgres, matching bescom-ai's architecture" —
this build stays on local SQLite (no cloud DB exists for this project;
the schema is close enough to Postgres that porting later is mostly
`CREATE TABLE` + `SERIAL`/`JSONB` syntax, not a rewrite, per `HANDOFF.md`
§6.1). `bescom-ai` itself wasn't found on this machine as a literal
folder to copy from — "matching its architecture" was interpreted as the
7 latency-minimizing design points below, which were given as an explicit
spec, not inferred.

### Architecture — 7 deliberate design decisions

| # | Decision | Why | Where |
|---|---|---|---|
| 1 | **One LLM call per question**, returning SQL + an answer template with `{placeholder}` tokens together as one structured JSON object (Gemini's native `response_schema` mode, not prompt-engineered JSON). The template is filled in Python from the query result + pre-extracted entities — **no second LLM call** ever phrases the final answer. | Removes a full network round-trip from every question — the single biggest lever against a slow/mediocre LLM API "feeling" slow. | `app/models.py` (`LLMQueryPlan`), `app/llm.py`, `app/pipeline.py::_fill_template` |
| 2 | **Semantic-layer pruning** — a deterministic keyword/entity router (subdivision names + fiscal years loaded from the DB at startup, tariff codes and glossary terms matched against the question text) selects only the relevant subset of `semantic_layer.json` before it goes in the prompt. `rules`, `common_metrics`, `joins`, and every table's schema are always kept (small, load-bearing for correctness); `glossary` and `tariff_code_glossary` are pruned. No LLM/embeddings in the router itself — it's sub-millisecond (measured: see §3). | Keeps the prompt lean as the semantic layer grows, without becoming its own latency source. | `app/semantic_layer.py` |
| 3 | **Persistent connections** — the SQLite connection and the Gemini client are both created once in FastAPI's `lifespan` and reused for every request; never re-opened per request. | Avoids repeated connection/handshake overhead on every question. | `app/db.py::init_db`, `app/llm.py::init_llm_client`, `app/main.py::lifespan` |
| 4 | **Async throughout** — async route handlers, Gemini called via `client.aio`; the (sync) SQLite call runs in a dedicated single-worker thread pool so one slow query doesn't block the event loop. | Lets FastAPI serve other requests while one is waiting on the LLM or DB. | `app/llm.py`, `app/db.py::ReadOnlyDB.run` |
| 5 | **SSE streaming of the final answer** to the client as it's assembled. | Perceived latency — first bytes reach the client sooner, and the structure is ready if token-level LLM streaming is added later. | `app/main.py::ask_stream` |
| 6 | **Read-only SQL enforcement**, two independent layers: (a) a text-level allowlist (`SELECT`/`WITH` only, forbidden-keyword regex, no multi-statement `;`), (b) the SQLite connection itself opened with `mode=ro` in its URI — so even SQL that slipped past layer (a) physically cannot write. Plus a query timeout (default 8s, enforced via `Connection.interrupt()` from a watchdog thread) and a row cap (default 200, via `fetchmany(cap+1)` + truncation, not text-level `LIMIT` injection — safer against queries that already have their own `ORDER BY`/`LIMIT`/CTEs). | Defense in depth against a malformed or adversarial LLM response. | `app/sql_safety.py`, `app/db.py` |
| 7 | **Grounding rules enforced in the prompt** — every rule from `semantic_layer.json["rules"]` is always included (never pruned): never SUM `fact_dcb` and `fact_dcb_rollups` together, don't double-count `Division_Total` against the sub-divisions, treat `fiscal_year` as text. | These are exactly the double-counting bugs the star schema's own design guards against — see `HANDOFF.md` §4.7 for why `lt_ht_total` vs `grand_total` was a real historical bug here. | `app/prompts.py::build_prompt`, `app/semantic_layer.py::prune_semantic_layer` |

### Request flow

```
question
  → app.semantic_layer: extract_entities() + prune_semantic_layer()   [router, no LLM, ~0.00-0.02ms measured]
  → app.prompts.build_prompt()                                        [string assembly]
  → app.llm.generate_query_plan()                                     [THE one LLM call]
  → app.sql_safety.validate_readonly_sql()                            [text-level allowlist]
  → app.db.ReadOnlyDB.run()                                           [mode=ro connection, timeout, row cap]
  → app.pipeline._fill_template()                                     [deterministic, no LLM]
  → StreamingResponse (SSE) or plain JSON
```

`app/pipeline.py::answer_question()` is the one function both the SSE
endpoint (`POST /api/ask`), the plain-JSON endpoint (`POST /api/ask_once`,
for scripts/evals), and the eval harness call — so all three exercise the
identical code path; only the delivery mechanism differs.

## 3. Verification done this session (real, verbatim)

### 3a. Locked folders — untouched

Before any chatbot code was written, `D:\Cescom POC 2\` was `git init`'d
(it wasn't a repo before this session) and a baseline commit made of the
raw workbook + all 4 locked pipeline folders, plus a separate SHA-256
hash manifest of every file in those 4 folders as a second, independent
check. After building `dcb-ai-app/`:

```
$ git status --short          (run from D:\Cescom POC 2\)
?? "New folder/dcb-ai-app/"
```
— nothing else listed, i.e. zero changes anywhere under the 4 locked
folders or the raw workbook. The SHA-256 re-check against the pre-build
baseline manifest also came back with every one of the 28 tracked files
still matching (`sha256sum -c` reported `OK` for all, no mismatches).

### 3b. Non-LLM components — real output, `tests/dry_run_no_llm.py`

Run without any API key (proves the DB layer, safety layer, and router
independent of Gemini). Actual output from this session's run:

- **DB opens read-only, and a write is physically blocked at the
  connection level** even bypassing the text-level safety check:
  `db.run("DELETE FROM fact_dcb", ...)` → `sqlite3.OperationalError:
  attempt to write a readonly database`.
- **Safety layer**: accepted a plain `SELECT`; rejected `DELETE`, `DROP`,
  a stacked `SELECT 1; DELETE ...`, `UPDATE`, and `ATTACH DATABASE`, each
  with the correct reason string.
- **Router**, run against all 12 eval questions (10 real + 2 adversarial):
  correctly extracted `subdivision_name`/`fiscal_year`/`tariff_code`
  entities where present (e.g. `{'subdivision_name': 'Arkalgud',
  'fiscal_year': '2024-25'}` for question 1; `{}` for the year-over-year
  question, which names no specific entity — correct, since it asks about
  every year). Mean router latency across all 12 questions: **0.00ms**
  (rounds to zero at 2-decimal precision — it's plain dict/string
  matching over an 8-item and 11-item list, exactly as designed to never
  be a latency source).
- **3 of the 10 reference SQL queries** (collection efficiency in
  Arkalgud 2024-25, top tariff category by demand 2023-24, best
  collection-efficiency sub-division for the latest year) re-run through
  `app.db.ReadOnlyDB.run()` and matched `example_queries.sql`'s own
  documented expected output exactly: **93.62%** collection efficiency;
  **LT-4(a)** tops 2023-24 demand; **Konanur** leads at **97.46%**
  (2025-26). DB-layer latency for these: 0.00–15.00ms.

### 3c. Full pipeline wiring — real output, `tests/pipeline_mock_test.py`

`app.llm.generate_query_plan` (well: `app.pipeline.generate_query_plan` —
see the note in the test file about why that's the correct patch target,
a real Python import-binding gotcha caught while writing this test, not
an app bug) was monkeypatched to a fixed, known `LLMQueryPlan` so the
FastAPI routing, lifespan, DB execution, and template-fill could be
proven correct without a real API key:

- `POST /api/ask_once` → `200`, `answerable: true`, `answer: "The
  collection efficiency in Arkalgud for 2024-25 was 93.62%."` — the
  template placeholder was filled with the **real** DB value (93.62,
  matching §3b), not a canned string.
- `POST /api/ask` (SSE) → real event stream observed: `event: status`,
  `event: sql` (echoing the generated SQL back), two `event: answer_chunk`
  events splitting the answer into words, `event: done` carrying
  `timing_ms`.
- Safety-net proof: with the mocked LLM plan set to `sql: "DELETE FROM
  fact_dcb"`, `POST /api/ask_once` returned `200` with `answerable:
  false` and `answer: "Sorry, the generated query didn't pass the
  read-only safety check."` — the unsafe SQL never reached the DB layer.

### 3d. What has NOT been run yet: the real 10-question eval

**`tests/eval_queries.py` (the real Gemini-call eval over the 10
`example_queries.sql` questions + 2 adversarial ones) has not been run
in this session — there is no `GEMINI_API_KEY` in this environment.**
The user explicitly chose "build now, test later" when asked. Everything
in §3a–3c is real evidence that the plumbing around the LLM call is
correct; it is **not** evidence that Gemini actually generates correct
SQL for these 10 questions, and no latency baseline exists yet for the
one stage (`llm_ms`) that will dominate real end-to-end latency.

**Before this project is considered done**, run:
```bash
cd dcb-ai-app
cp .env.example .env   # fill in a real GEMINI_API_KEY
python -m tests.eval_queries
```
and update this section with the real, verbatim SQL/answer/timing output
it produces (it writes `tests/eval_report.md` — paste or link that here,
don't hand-summarize it).

## 4. Known limitations (honest, not hidden)

- **Single shared SQLite connection, serialized.** Design point #3
  (persistent, not per-request) plus point #6 (read-only) means all query
  execution goes through one connection guarded by a lock — concurrent
  requests queue rather than truly parallelize at the DB layer. Fine for
  a POC's traffic; would need a real connection pool (or WAL mode with
  multiple reader connections) to scale beyond that.
- **Multi-row template fallback is generic, not model-authored.** The
  LLM's `answer_template` is designed for the common case of a
  single-row aggregate result. When a query legitimately returns many
  rows (e.g. "top 5 tariff categories"), `_fill_template` falls back to
  a generic `"1. col=val, col=val\n2. ..."` listing (capped at 10 rows)
  instead of trying to force the single-row template onto N rows. This
  is a deliberate trade-off to keep the fill step deterministic and
  LLM-call-free (design point #1) — a more polished multi-row phrasing
  would need either a second LLM call (which the whole architecture is
  built to avoid) or a more elaborate per-row template mini-language,
  which wasn't built this session.
- **SSE "streaming" streams the already-assembled answer, not live LLM
  tokens.** Gemini is called once in structured-JSON mode
  (`response_schema=LLMQueryPlan`), which returns the whole object at
  once — there's no partial-JSON token stream to relay. What's streamed
  is the deterministic template-fill output, chunked client-side-visibly
  so the UI can render progressively; it's real SSE, just not
  token-level LLM streaming. Documented here so it isn't mistaken for
  the latter later.
- **Row cap default (200) is not injected into the SQL text** — it's
  enforced by `fetchmany(cap+1)` after execution, so a query that itself
  computes an expensive full aggregate before the cap would still pay
  that cost. Deliberate (safer against breaking `ORDER BY`/window
  queries than text-level `LIMIT` injection), but means the row cap
  bounds result size, not necessarily query cost — the wall-clock
  timeout is the real cost bound.
- **No live-LLM latency baseline yet** — see §3d.
