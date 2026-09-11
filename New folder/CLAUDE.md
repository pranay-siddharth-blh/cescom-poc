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

### 3d. Live smoke test + full 10-question eval — real results

Once the user filled in `.env` (project root) with a real `GEMINI_API_KEY`
and `GEMINI_MODEL=gemini-3.5-flash-lite`, confirmed present via
`len(settings.gemini_api_key) == 53` (never printed the key itself):

**Smoke test** — a minimal direct call (`client.models.generate_content`,
no JSON schema, just `contents='Reply with exactly the single word: PONG'`):
`SMOKE TEST: PASS`, model replied `'PONG'`.

**Full eval, run 1 — 3 real bugs found and fixed live** (this eval is
what caught them, so this project now has the same "found and fixed
during real testing" track record as the data pipeline's own §4):

1. **Crash: `ValueError: Unknown format code 'f' for object of type 'str'`
   in `_fill_template`**, on question 7. `_format_value()` pre-converts
   every DB value to a display-ready *string* (with its own comma/decimal
   formatting); the LLM's `answer_template` for Q7 included a Python
   format spec like `{pct:.2f}`, and applying a numeric format code to an
   already-stringified value raises. **Fix:** `app/pipeline.py` now
   strips any `:spec` inside a `{placeholder:spec}` token before calling
   `.format()` (`_strip_format_specs`), and the fallback `except` clause
   also catches `ValueError`, not just `KeyError`/`IndexError`.
2. **Wrong refusal on question 5** ("Compare LT-1 vs HT-2 demand").
   `extract_entities()` only captured a single `tariff_code` (first match,
   then `break`), and the prompt told the LLM to treat pre-extracted
   entities as authoritative — so the model concluded it couldn't handle
   a second tariff code not in that list and returned `answerable: false`
   with a confused refusal message. **Fix:** the router now collects
   *all* matching tariff codes (`entities["tariff_codes"]` when there are
   2+, `entities["tariff_code"]` unchanged when there's exactly 1), and
   `app/prompts.py`'s entity-block wording was softened from "reuse these
   exact values... do not re-derive" to explicitly say the list is a
   convenience, not exhaustive, and the model should read any other named
   entities directly from the question.
3. **Silent wrong-answer on question 8** (DC/MNR installations by
   sub-division, 2024-25): came back `answerable: true` but every row was
   `NULL`/`N/A`. Root cause: **`DCB_Semantic_Layer/example_queries.sql`'s
   10 worked SQL patterns were never actually loaded into the prompt at
   all**, despite `semantic_layer.json`'s own `_purpose` field and
   `HANDOFF.md` §6 saying that's exactly what they're for — a real gap in
   design point #7 ("grounding rules enforced"), not just an LLM mistake.
   Without that worked example, the LLM summed only the pre-2018-19
   `dc_mnr_installations` column and never applied the `COALESCE`
   pattern needed for 2018-19-onward's split `dc_installations`/
   `mnr_installations` columns — so it silently queried the wrong side of
   a source-layout switch and got `NULL` for every 2024-25 row without
   any error. **Fix:** `app/prompts.py::load_example_queries()` now loads
   `example_queries.sql` (unpruned — only ~4KB) into every prompt.
4. **Placeholder leakage into SQL on question 10**, found in a later
   re-run: the LLM generated `SELECT ... WHERE s.subdivision_name =
   '{subdivision_name}' AND f.fiscal_year = '{fiscal_year}'` — literal,
   unresolved `{...}` tokens *inside the SQL string itself*, conflating
   the `sql` field's "bake in literal values" contract with the separate
   `answer_template` placeholder mechanism. SQLite has no `{}` syntax, so
   this matched nothing and returned a single all-`NULL` row instead of
   an error — another silent-wrong-answer failure mode, not a crash.
   **Fix, two layers:** `app/prompts.py`'s `SYSTEM_INSTRUCTIONS` now
   explicitly states `sql` and `answer_template` use different
   substitution mechanisms and forbids `{...}` tokens in `sql`; and
   `app/sql_safety.py::validate_readonly_sql` now rejects any SQL
   containing an unresolved `\{[a-zA-Z_]\w*\}` token as unsafe *before*
   execution, so even if a future prompt tweak fails to prevent this, the
   app refuses to silently run a query that can't actually match anything
   rather than returning a misleadingly clean-looking `NULL` result.

**Final clean re-run (after all 4 fixes) — the real, verbatim result for
all 10 example questions + 2 adversarial ones:**

#### Q1. What was the collection efficiency in Arkalgud in 2024-25?
- SQL: `SELECT ROUND(SUM(f.collection_grand_total) * 100.0 / SUM(f.demand_total), 2) AS collection_efficiency_pct FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_name = 'Arkalgud' AND f.fiscal_year = '2024-25'`
- Result: `[(93.62,)]` — matches `example_queries.sql`'s own documented `93.62%` exactly.
- Answer: *"The collection efficiency in Arkalgud in 2024-25 was 93.62%."*
- Timing: `{router_ms: 0.0, llm_ms: 1531.0, safety_ms: 0.0, db_ms: 0.0, fill_ms: 0.0}`, total **1531 ms**.

#### Q2. What's the year-over-year growth in total installations for the whole division?
- SQL: `SELECT r.fiscal_year AS fiscal_year, r.total_installations AS total_installations, r.total_installations - LAG(r.total_installations) OVER (ORDER BY d.start_year) AS yoy_change FROM fact_dcb_grand_totals r JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id JOIN dim_fiscal_year d ON r.fiscal_year = d.fiscal_year WHERE s.subdivision_name = 'Division_Total' ORDER BY d.start_year`
- Result (11 rows, e.g.): `('2015-16', 159149.0, None)`, `('2016-17', 166060.0, 6911.0)`, ... `('2025-26', 183794.0, 1901.0)`.
- Answer (multi-row fallback, per the documented limitation below — first 10 of 11 shown): *"Results: (showing 10 of 11 rows) 1. fiscal_year=2015-16, total_installations=159,149, yoy_change=N/A ... 10. fiscal_year=2024-25, total_installations=181,893, yoy_change=5,844"*
- Timing: total **1172 ms**.

#### Q3. Which tariff category generated the most demand in 2023-24?
- SQL: `SELECT t.tariff_code AS tariff_code, t.description AS description, SUM(f.demand_total) AS total_demand FROM fact_dcb f JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE f.fiscal_year = '2023-24' AND s.subdivision_name = 'Division_Total' GROUP BY t.tariff_code, t.description ORDER BY total_demand DESC LIMIT 1`
- Result: `[('LT-4(a)', 'IP Sets - <=10Hp, Rural Feeder (Un-Metered)', 1687837231.91)]` — matches `example_queries.sql`'s documented "LT-4(a) tops the list."
- Answer: *"In fiscal year 2023-24, the tariff category that generated the most demand was LT-4(a) (IP Sets - <=10Hp, Rural Feeder (Un-Metered)) with a total demand of 1,687,837,231.91."*
- Timing: total **1390 ms**.

#### Q4. How much outstanding balance (arrears) does Konanur currently carry?
- SQL: `SELECT f.fiscal_year AS fiscal_year, SUM(f.closing_balance_total) AS outstanding_balance FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_name = 'Konanur' GROUP BY f.fiscal_year ORDER BY f.fiscal_year DESC LIMIT 1`
- Result: `[('2025-26', 30717819.028217457)]`
- Answer: *"For fiscal year 2025-26, Konanur currently carries an outstanding balance (arrears) of 30,717,819.03."*
- Timing: total **1188 ms**.

#### Q5. Compare domestic (LT-1) vs industrial (HT-2) demand across all years.
- SQL: `SELECT f.fiscal_year AS fiscal_year, SUM(CASE WHEN t.tariff_code LIKE 'LT-1%' THEN f.demand_total ELSE 0 END) AS domestic_lt1_demand, SUM(CASE WHEN t.tariff_code LIKE 'HT-2%' THEN f.demand_total ELSE 0 END) AS industrial_ht2_demand FROM fact_dcb f JOIN dim_tariff_category t ON f.tariff_category_id = t.tariff_category_id JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_name = 'Division_Total' GROUP BY f.fiscal_year ORDER BY f.fiscal_year`
- Result (11 rows, e.g.): `('2015-16', 37954648.5142, 23399034.7482)` ... `('2025-26', 542708386.6741, 141349133.1922)`.
- Answer (multi-row fallback, first 10 of 11 shown): *"Results: (showing 10 of 11 rows) 1. fiscal_year=2015-16, domestic_lt1_demand=37,954,648.51, industrial_ht2_demand=23,399,034.75 ... 10. fiscal_year=2024-25, domestic_lt1_demand=473,913,919.16, industrial_ht2_demand=144,256,241.73"*
- Timing: total **2203 ms** (this run's slowest — see latency summary).

#### Q6. What's the official printed GRAND TOTAL demand for 2025-26?
- SQL: `SELECT r.demand_total AS demand_total, r.collection_grand_total AS collection_grand_total FROM fact_dcb_grand_totals r JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id WHERE r.fiscal_year = '2025-26' AND s.subdivision_name = 'Division_Total'`
- Result: `[(3541649882.7938004, 3300093200.7644)]`
- Answer: *"The official printed GRAND TOTAL demand for fiscal year 2025-26 is 3,541,649,882.79 (with a collection grand total of 3,300,093,200.76)."*
- Timing: total **1344 ms**.

#### Q7. Which sub-division has the best collection efficiency in the most recent year?
- SQL: `SELECT s.subdivision_name AS subdivision_name, ROUND(SUM(f.collection_grand_total) * 100.0 / SUM(f.demand_total), 2) AS collection_efficiency_pct FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE f.fiscal_year = (SELECT MAX(fiscal_year) FROM fact_dcb) AND s.subdivision_type IN ('sub_division', 'section') GROUP BY s.subdivision_name ORDER BY collection_efficiency_pct DESC LIMIT 1`
- Result: `[('Konanur', 97.46)]` — matches `example_queries.sql`'s documented "Konanur leads at 97.46% (2025-26)" exactly.
- Answer: *"The sub-division with the best collection efficiency in the most recent year is Konanur with a collection efficiency of 97.46%."*
- Timing: total **1219 ms**.

#### Q8. How many installations are dead or non-recoverable (DC/MNR) by sub-division, for 2024-25?
- SQL: `SELECT s.subdivision_name, SUM(COALESCE(f.dc_installations, 0) + COALESCE(f.mnr_installations, 0) + COALESCE(f.dc_mnr_installations, 0)) AS dead_or_nonrecoverable FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_type IN ('sub_division', 'section') AND f.fiscal_year = '2024-25' GROUP BY s.subdivision_name ORDER BY dead_or_nonrecoverable DESC`
- Result: `[('Holenarasipura', 11806.0), ('Arkalgud', 7743.0), ('Ramanathapura', 6917.0), ('Hangarahalli', 5401.0), ('Konanur', 4222.0)]` — correctly uses the `COALESCE` pattern (see bug #3 above; this is the query that was `NULL` before the fix).
- Answer: *"Results: 1. subdivision_name=Holenarasipura, dead_or_nonrecoverable=11,806 2. subdivision_name=Arkalgud, dead_or_nonrecoverable=7,743 3. subdivision_name=Ramanathapura, dead_or_nonrecoverable=6,917 4. subdivision_name=Hangarahalli, dead_or_nonrecoverable=5,401 5. subdivision_name=Konanur, dead_or_nonrecoverable=4,222"*
- Timing: total **1343 ms**.

#### Q9. What's the trend in Miscellaneous revenue (fees and penalties) over the years?
- SQL: `SELECT r.fiscal_year AS fiscal_year, r.demand_total AS misc_revenue_demand FROM fact_dcb_rollups r JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_id WHERE r.rollup_type = 'misc_total' AND s.subdivision_name = 'Division_Total' ORDER BY r.fiscal_year`
- Result (11 rows, e.g.): `('2015-16', 2552571.0)` ... `('2025-26', 12715969.37)`, rising to a clear peak of `112,362,444.88` in 2024-25.
- Answer (multi-row fallback, first 10 of 11 shown): *"Results: (showing 10 of 11 rows) 1. fiscal_year=2015-16, misc_revenue_demand=2,552,571 ... 10. fiscal_year=2024-25, misc_revenue_demand=112,362,444.88"*
- Timing: total **1219 ms**.

#### Q10. Show the full DCB chain — opening balance, demand, collection, and closing balance — for Holenarasipura in 2024-25.
- SQL: `SELECT SUM(f.opening_balance_total) AS opening_balance, SUM(f.demand_total) AS demand, SUM(f.collection_grand_total) AS collection, SUM(f.closing_balance_total) AS closing_balance FROM fact_dcb f JOIN dim_subdivision s ON f.subdivision_id = s.subdivision_id WHERE s.subdivision_name = 'Holenarasipura' AND f.fiscal_year = '2024-25'` — real literal values now, not the `{subdivision_name}`/`{fiscal_year}` placeholder leak from bug #4 above.
- Result: `[(162003262.46606404, 1034457103.7718816, 942195118.9479599, 254265247.28998575)]`
- Answer: *"For Holenarasipura in 2024-25, the full DCB chain is as follows: Opening Balance = 162,003,262.47, Demand = 1,034,457,103.77, Collection = 942,195,118.95, and Closing Balance = 254,265,247.29."* — hand-checked: 162,003,262.47 + 1,034,457,103.77 − 942,195,118.95 = 254,265,247.29, exactly matching the closing balance, confirming `example_queries.sql`'s documented invariant holds.
- Timing: total **1359 ms**.

#### QA1 (adversarial). What was the total demand for tariff code ZZ-99 in 2024-25?
- `answerable: false`. Answer: *"I don't see a tariff code matching ZZ-99 in the database."* — correctly refused a nonexistent tariff code instead of hallucinating a number. Timing: total **922 ms**.

#### QA2 (adversarial). What is the combined total demand if I add up every sub-division's demand plus the Division_Total for 2024-25?
- `answerable: false`. Answer: *"The question asks to add every sub-division's demand plus the Division_Total, which violates business semantic rules by double-counting the division total (since Division_Total is already the sum of all sub-divisions)."* — the grounding rule against double-counting `Division_Total` held under a direct adversarial prompt. Timing: total **1235 ms**.

**Latency baseline (final clean run, 12 questions, `gemini-3.5-flash-lite`):**
- Wall time for the full run: **16,125 ms**
- Mean total latency per question: **1,343.8 ms**
- Min / max total latency: **922 ms** (QA1) / **2,203 ms** (Q5)
- Mean LLM-call latency: **1,338.6 ms** — i.e. the LLM call is ~99.6% of total latency; every other stage (router, safety check, DB query, template fill) is consistently 0–16 ms combined. This confirms the architecture's core bet: the single-call + pruning + persistent-connection design successfully keeps the app's own overhead negligible, so total latency is essentially just `gemini-3.5-flash-lite`'s own response time.
- Full raw output (all 12 questions) is also saved verbatim at `dcb-ai-app/tests/eval_report.md`.

One benign SDK warning appeared on every call and is not an error: `Direct use of automatic function calling (AFC) in AsyncModels.generate_content is not recommended...` — an advisory from `google-genai` about a different, unused code path (function-calling), harmless here since this app never registers tools/functions with Gemini.

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
- **Grounding-rule compliance (design point #7) is prompted, not
  mechanically enforced — unlike read-only safety (design point #6),
  which is.** `app/sql_safety.py` code-enforces SELECT-only and rejects
  unresolved placeholders regardless of what the LLM does; there is no
  equivalent code-level check that a generated query actually avoids
  double-counting `Division_Total`, mixes `fact_dcb`/`fact_dcb_rollups`,
  etc. — the app trusts the prompt's rules. This isn't hypothetical: an
  intermediate re-run of question 3 during this session's live eval
  (while bugs #1/#2 above were being fixed, before the final clean run)
  generated `SELECT t.tariff_code, SUM(f.demand_total) ... GROUP BY
  t.tariff_code` with **no `s.subdivision_name = 'Division_Total'`
  filter at all** — silently summing that tariff code's demand across
  every real sub-division *and* the Division_Total rollup together,
  exactly the double-counting the semantic layer's rules exist to
  prevent. That specific run was superseded by the final clean run
  (§3d), which correctly included the filter, but the underlying gap is
  real: at `temperature=0.0`, Gemini's SQL for the same question is not
  perfectly reproducible run-to-run, and nothing here mechanically
  catches a grounding-rule violation the way `sql_safety.py` catches an
  unsafe statement. A production version of this app should add a
  code-level double-counting check (e.g. flag any query that both joins
  `dim_subdivision` without a `subdivision_type`/`subdivision_name`
  filter *and* aggregates a metric column) rather than relying on the
  prompt alone.
- **Live-LLM latency baseline: established** — see §3d. Mean **1,343.8
  ms** total per question, of which the LLM call itself is **~99.6%**
  (mean **1,338.6 ms**); every other stage (router, safety, DB, fill)
  together adds only 0–16 ms. Measured with `gemini-3.5-flash-lite`.
