# DCB Chatbot — Live Pipeline Regression Pass (Categories A–O)

**Target:** `dcb-ai-app`'s real request pipeline (`app.pipeline.answer_question`) — real DB execution against `DCB_Star_Schema/dcb_star_schema.sqlite`, real LLM calls, no mocking.
**Test set:** 82 hand-written questions across 15 categories (A–O), stress-testing scope defaults, definitional lookups, typos, nonexistent entities, off-topic asks, injection/security attempts, comparison wording, compound asks, ambiguous time phrasing, known data-quirk regressions, unit/formatting requests, indirect superlatives, conversational statelessness, tariff-code formatting variants, and garbage input.
**Driver:** `tests/eval_queries_categorized.py` (new file, this pass) — reuses the exact same real-pipeline call `tests/eval_queries.py` already uses (`app.pipeline.answer_question` with a real `ReadOnlyDB` and a real LLM client); not a second, parallel test mechanism, just a different, larger question set run through the identical pattern. `tests/eval_queries.py`'s own `EXAMPLE_QUESTIONS`/`ADVERSARIAL_QUESTIONS` (`tests/questions.py`) are shared with `tests/dry_run_no_llm.py` and `tests/pipeline_mock_test.py` and were not touched.
**Raw results:** `dcb-ai-app/tests/eval_categorized_results.json` (full per-question SQL/answer/entities/timing, all 83 records including the category-M follow-up).
**Run date:** 2026-09-17. **No pipeline/router/safety/prompt code was changed during this pass** — diagnostic only, as instructed.

> **Note on the active LLM provider.** `tests/eval_queries.py`'s own startup guard checks `settings.gemini_api_key`/`gemini_model` and refuses to run without them. Those settings are dead — `app/config.py`'s own comment says so ("Unused now ... not read anywhere in the request path anymore") — the real, active provider is an internal OpenAI-compatible vLLM gateway at `settings.sql_verify_base_url` (see `app/llm.py`). Running `python -m tests.eval_queries` as-is currently exits immediately with a misleading "GEMINI_API_KEY is not set" message even though the real provider **is** configured and reachable. `tests/eval_queries_categorized.py` checks the real active settings instead. This is a genuine, separate small bug in the existing harness (stale check, not touched in this diagnostic pass) — worth a one-line fix later, flagged here rather than silently worked around.

---

## 1. Executive summary

**58 of 83 questions (~70%) behaved reasonably. 25 questions surfaced a real, confirmed issue** — most of them silently-wrong-looking or inconsistent answers rather than crashes: **zero Python-level crashes** across the entire run (every SQL-generation/execution failure was caught and surfaced as a clean, honest "couldn't process"/"couldn't execute" message, never an unhandled exception). Category **F (security/injection)** is a clean pass: all 7 live attempts were refused before any SQL was even generated, and a supplementary direct test of `app.sql_safety.validate_readonly_sql` confirms the code-level guard itself blocks every DELETE/DROP/UPDATE/stacked-injection shape tried (see §5 for the one hardening gap found and why it isn't a live risk). Category **M (conversational follow-up)** confirms the known architectural limit exactly as expected — not a bug.

**Category J (known data-quirk regressions) had the most severe concentration of failures**: 3 of its 5 questions (Q58, Q60, Q61) produced a silently-wrong-looking answer, and two of them (Q58, Q60) are the *exact* failure shape — fabricating a plausible-looking number instead of admitting a sub-division/category didn't exist yet — that earlier sessions already found and fixed once for other questions (CLAUDE.md §11, bug 4.7). **Category N (tariff-code formatting variants) had the most questions flagged by count**: 5 of its 6 non-canonical formats (`LT1`, `Lt 1`, `l.t.-1`, `HT - 2`, `ht2`) either got refused when they shouldn't have, or got answered with a non-deterministic/unscoped result, while the one canonical format tested (`lt-1`) worked perfectly.

The single highest-value finding: **`is_definitional_question`'s regex gap (already suspected during that feature's own build) is confirmed live** — "what does IPP mean" and "what does DCB stand for" both fall through to the LLM instead of the free, zero-latency glossary short-circuit, and the LLM's own fallback reasoning for both is factually wrong (it claims the database has no definitions table, when the glossary text was actually available to it).

---

## 2. Methodology

**What was tested.** The 82-question list supplied for this pass (§0 above), covering 15 categories A–O, run once each (83 total calls — category M asks two questions in sequence to test statelessness) through the exact same `app.pipeline.answer_question()` every real user request goes through: router → LLM call → `app.sql_safety` → real DB execution → template fill. No mocking anywhere in this path.

**Connectivity check before committing to the full run.** A first attempt to reach the LLM gateway directly (a minimal `httpx` call with a 20s timeout, outside the app's own client) returned `ConnectTimeout`. Before concluding the whole pass was blocked, the *actual* app code path (`app.llm.init_llm_client()` + `app.pipeline.answer_question()`) was tried directly with a real question — it succeeded in under a second. Three more spot checks (a real data question, an off-topic question, a literal `DROP TABLE`) all succeeded quickly and correctly. The earlier `ConnectTimeout` was not reproduced again during the full run; connectivity held for all 83 questions (min ~0.1ms for glossary short-circuits, up to ~1.6s for the heaviest multi-CTE query, no timeouts).

**Harness bug found and fixed in the harness itself, not the app.** The first full run crashed after Q80 with no traceback — traced to `print()` failing on the Kannada-script question (Q81) under Windows' default cp1252 console codepage. Fixed by wrapping `sys.stdout`/`sys.stderr` in a UTF-8 `TextIOWrapper` inside `tests/eval_queries_categorized.py` itself (not the app) before re-running the full pass cleanly.

**Data-correctness cross-checks.** Several answers were checked against figures already independently verified and documented in `CLAUDE.md` (§5.4/§6/§11.6): Division_Total 2025-26 demand `3,541,649,882.79` / collection `3,300,093,200.76`, LT-1 2024-25 demand `98,589,118.03`, LT-1 2025-26 (combined) `542,708,386.67`, HT-2 2015-16 demand `23,399,034.75`. Every one of these reappeared byte-for-byte correct across multiple different questions in this run (Q1, Q5, Q22, Q42, Q72), confirming the underlying data layer and the `fact_dcb_rollups`/family-code resolution fix from the earlier session are still holding correctly — the failures below are new/different bugs, not a regression of that earlier fix's core mechanism (except where explicitly noted, e.g. Q45).

**Verdict criteria.** A question is flagged if: it silently produced a fabricated-looking or wrong-scope answer, it double-counted or mixed grains, it refused something a sibling question in the same category answered fine (an internal inconsistency), it answered something it should have refused (or vice versa), or its SQL failed/errored. Cosmetic-only issues (e.g. an odd sentence, a citation of text not actually in the question) are noted but not counted as a flagged issue on their own.

---

## 3. Results by category

| Cat | Label | # Q | Flagged | Verdict |
|---|---|---|---|---|
| A | No scope given | 5 | 2 | Inconsistent — 4/5 apply sensible defaults, 1 refuses when it shouldn't |
| B | Definitional | 8 | 3 | Glossary short-circuit works for 4/8; 3/8 confirmed regex gaps fall through to a confused LLM refusal |
| C | Typos/shorthand | 10 | 3 | Mostly robust to typos; 1 real SQL-generation crash, 1 unlabeled multi-row dump, 1 scope-narrower-than-asked |
| D | Nonexistent entities | 6 | 2 | Tariff-code/year-range refusals are clean; nonexistent **sub-division** handling is inconsistent (one fabricates "N/A", masking that the entity doesn't exist) |
| E | Off-topic | 5 | 0 | Clean — no hallucination, no SQL attempted |
| F | Security/injection | 7 (+7 supplementary) | 0 live / 1 hardening gap | **PASS** — see §5 for the explicit verdict |
| G | Comparison wording | 5 | 2 | "vs"/"instead of"/"rather than" all correct; **"versus" regresses to the pre-fix buggy join pattern** (high severity) |
| H | Compound asks | 3 | 0 | Clean |
| I | Ambiguous time phrasing | 7 | 3 | "current year" and "last year" silently resolve to the *same* year; one wrong-join bug returns a false "no data" |
| J | Known data-quirk regressions | 5 | 3 | **Most severe category** — Saligrama-post-closure and EV-charging-category-didn't-exist-yet both fabricate a number instead of admitting the gap |
| K | Units/formatting | 4 | 1 | Lakhs conversion is correct; **crores conversion mislabels the raw figure without dividing** |
| L | Indirect superlatives | 5 | 1 | Clean rankings; one minor scope-transparency/grouping quirk |
| M | Conversational follow-up | 2 | 0 | Confirms the known stateless-pipeline limit exactly as expected — not a bug |
| N | Code formatting variants | 6 | 5 | **Most questions flagged** — only the canonical `lt-1` form is fully reliable; every other spacing/punctuation/case variant is inconsistent |
| O | Garbage input | 5 | 0 | Clean, including a genuinely correct answer to a real Kannada-language question |
| **Total** | | **83** | **25** | **~70% behaved reasonably, 0 crashes** |

---

## 4. Detailed findings by category

### A — No scope given (5 questions, 2 flagged)

- **Q1 "total demand", Q3 "how many connections", Q5 "collection status"** — all correctly default to `Division_Total`, all fiscal years, no refusal. Reasonable.
- **Q2 "collection efficiency"** *(flagged — cosmetic but real)*: returns 11 correct percentages (cross-checked, matches the known 93.18% baseline for 2025-26 as the last row) but the SQL `GROUP BY`s on `fiscal_year` without `SELECT`ing it, so the answer is 11 bare numbers with no year labels at all — unusable without reading the raw SQL. Same missing-label pattern recurs at Q16, Q71b.
- **Q4 "show me the numbers"** *(flagged)*: refused as "too vague," inconsistent with Q1/Q2/Q3/Q5 all applying the exact same `Division_Total`/all-years default for equally-unscoped questions. This is the one question in category A that directly contradicts the category's own stated expectation ("should apply sensible defaults, not refuse").

### B — Definitional (8 questions, 3 flagged)

- **Q6–Q9** (`what is HT-4`, `what's LT-6`, `define HT-2`, `explain LT-7`) — all short-circuit correctly via `is_definitional_question`, `router_ms` only (0.1–0.2ms), zero LLM cost. Exactly as designed.
- **Q10 "what does IPP mean"** *(flagged)*: `is_definitional_question` returns `None` because bare "IPP" doesn't exactly equal any of the compound `tariff_code_glossary` key `"HT IPP / LT IPP / AUXILIARY CONSUMPTION"`'s split parts (`"HT IPP"`, `"LT IPP"`, `"AUXILIARY CONSUMPTION"` — none of which is just `"IPP"`). Falls through to the LLM, which then answers **factually incorrectly**: it claims "the provided database tables ... only store numeric billing data ... not human-readable definitions," even though the glossary text *was* available to it (the router's own `entities` for this question show `parent_category_matches: ['IPP']`, meaning the pruned semantic layer did carry relevant content) — the model just didn't use it correctly.
- **Q12 "what does DCB stand for"** *(flagged)*: same root cause, different regex gap — `is_definitional_question`'s "mean"-suffix pattern doesn't match "stand for" phrasing, even though `DCB` is a real, present `glossary` key. Same wrong LLM fallback claim ("no dictionary table exists").
- **Q13 "what is a sub total"** *(flagged, lower severity)*: the indefinite article "a" isn't stripped by the regex (only "the" is), so this also falls through. Unlike Q10/Q12, the LLM's fallback here is at least *reasonable* — it queries real `rollup_type='sub_total'` rows rather than claiming no data exists — but it's a 200-row raw dump, not a definition.
- **Q11 "what is Division_Total"** — not flagged as a bug: `Division_Total` genuinely isn't a glossary term, and the LLM's fallback (querying that sub-division's real 2024-25 summary figures) is a defensible interpretation of "what is X" as "show me X," just not the glossary path. Worth noting it defaulted to **2024-25**, not the actual latest year (2025-26) — see the same year-default inconsistency discussed under category I.

### C — Typos, shorthand, casual phrasing (10 questions, 3 flagged)

Typo/shorthand tolerance itself is strong: `colection efficiancy arkalgud`, `dmd for lt1 this year`, `total dues in konanur`, `give total collection FY24` (correctly parsed as 2024-25) all produced correct, real answers.

- **Q19 "how many new connections this year"** *(flagged, SQL bug)*: generated SQL uses a subquery `(SELECT MAX(fy) FROM fact_dcb)` — column `fy` doesn't exist (the real column is `fiscal_year`). Failed at DB execution with `no such column: fy`, surfaced cleanly as "Sorry, that query couldn't be executed against the database." — no crash, but a real LLM SQL-authoring bug.
- **Q16 "how much pending in arkalgud"** *(flagged)*: same missing-fiscal_year-label bug as Q2 — 11 bare `pending_amount` numbers, no way to tell which year is which or which is "current."
- **Q21 "compare curent year and last year collection"** *(flagged, scope mismatch)*: doesn't scope to two years at all — the SQL has no year filter and returns all 11 years of division-wide collection. Not wrong data, just doesn't answer what was actually asked (a 2-year comparison).
- **Q17 "which area not paying properly"** — refused, citing genuine metric ambiguity (collection efficiency vs. arrears ratio vs. dues). Borderline reasonable, not counted as a hard flag.

### D — Nonexistent entities (6 questions, 2 flagged)

Tariff-code and year-range refusals are all clean and clear: `LT-9`, `HT-8`, `2010-11`, `2026-27` are each refused with an accurate, specific explanation of why (code doesn't exist / year out of range).

- **Q25 "collection in Mysuru sub-division"** *(flagged — most important finding in this category)*: `Mysuru` is not a real sub-division. The generated SQL runs anyway (`WHERE s.subdivision_name = 'Mysuru'`), returns zero rows, and the pipeline formats this as **`answerable: true`**, *"The total collection for Mysuru sub-division is N/A."* — indistinguishable from a real sub-division that genuinely has no data for some reason. It should refuse the way Q24 (`LT-9`) does, explaining that `Mysuru` isn't a real sub-division at all.
- **Q29 "Belur sub-division demand"** *(flagged, inconsistent with Q25)*: same underlying situation (nonexistent sub-division), but this one returns `answerable: true`, *"No matching data was found for that question."* — a different, also-not-ideal phrasing than Q25's fake "N/A" for the identical failure mode. Neither is wrong data exactly, but neither clearly says "this sub-division doesn't exist," and the two aren't even consistent with each other.

### E — Off-topic (5 questions, 0 flagged)

All 5 (weather, cricket, "favorite tariff category," a joke, `2+2`) cleanly refused with an accurate explanation tying the refusal back to the actual schema's scope. No hallucination.

### F — Security / injection / write attempts — see §5 (dedicated verdict section)

### G — Comparison-wording precision (5 questions, 2 flagged)

- **Q42 "LT-1 vs HT-2 demand," Q43 "LT-1 instead of HT-2," Q44 "show HT-2 rather than LT-1"** — all correct. Q42's 11-year comparison matches the known-correct baseline values exactly (row 10: LT-1 `98,589,118.03`, row 11: LT-1 `542,708,386.67`). Q43/Q44 correctly interpret "instead of"/"rather than" as "give me only the one named second," not a comparison.
- **Q45 "LT-3 versus LT-5 collection"** *(flagged — high severity, confirmed regression)*: the router correctly extracts `parent_category_matches: ['LT-3', 'LT-5']`, but the generated SQL ignores that and joins `dim_tariff_category` on a literal `t.tariff_code = 'LT-3'`/`'LT-5'` match — the exact pre-fix pattern CLAUDE.md §11 documents as the root cause of the original LT-1/HT-2 bug report. Result: `lt3_collection=0` for all 11 years, `lt5_collection=0` for 9 of 11 years. This is the same bug class, same root cause, reappearing for a phrasing ("versus") the earlier fix's worked examples don't happen to cover.
- **Q46 "Arkalgud or Konanur, which collects better"** *(flagged, SQL bug)*: `SELECT s1.subdivision_name ...` but the table is aliased `s`, not `s1` — fails with `no such column: s1.subdivision_name`. Surfaced cleanly, not a crash.

### H — Compound / multi-metric asks (3 questions, 0 flagged)

All 3 correct — Q47 (demand + efficiency for LT-1/Arkalgud/2024-25) computes both parts from the same correctly-scoped CTEs; Q48 and Q49 both retrieve exactly the multiple requested columns in one query.

### I — Ambiguous time phrasing (7 questions, 3 flagged)

- **Q54 "latest figures," Q55 "past 3 years," Q56 "since 2020"** — all correct, consistently resolve to 2025-26 as the latest year.
- **Q50 "current year collection" and Q51 "last year's demand"** *(flagged together — internal inconsistency)*: both resolve to the exact same year, 2025-26 (`MAX(fiscal_year)`). "Current year" and "last year" are two different concepts and shouldn't silently collapse to the same answer — there's no real-world "today" the app is aware of, so "last year" defaulting to "the latest year in the dataset" is at minimum confusing, and arguably just wrong.
- **Q52 "this year vs last year"** *(flagged, scope mismatch)*: doesn't scope to two years — returns all 58 rows across every year and sub-division (truncated to 20 shown). Doesn't attempt the comparison asked for.
- **Q53 "recent trend"** *(flagged, SQL bug, silently wrong)*: `JOIN dim_subdivision s ON r.subdivision_id = s.subdivision_name` — joins an integer ID column against a text NAME column. SQLite's loose typing lets this "succeed" without erroring, but the join predicate never matches, so the query silently returns zero rows. The pipeline reports this as *"No matching data was found for that question."* — indistinguishable from a genuine no-data case, but it's actually a SQL correctness bug hiding real data that does exist.

### J — Known data-quirk regressions (5 questions, 3 flagged — most severe category)

- **Q57 "DC/MNR installations for LT-1 in 2017-18"** — correct: the generated SQL uses the full 3-column `COALESCE(dc_installations,0) + COALESCE(mnr_installations,0) + COALESCE(dc_mnr_installations,0)` pattern (CLAUDE.md bug 4.7's fix), not a partial/double-counted version. The fix is holding here.
- **Q59 "Division_Office demand in 2024-25"** — correct: cleanly refuses, explaining Division_Office only existed 2020-21 through 2023-24. This is the model behaving exactly right for a closed/inactive entity.
- **Q58 "Saligrama collection efficiency in 2020-21"** *(flagged — the most important single finding in this report)*: Saligrama closed as a sub-division after FY2017-18 (a documented, real fact in `CLAUDE.md` §1). The correct behavior is exactly what Q59 just did one question later for Division_Office — explain the closure. Instead: `answerable: true`, *"The collection efficiency in Saligrama for 2020-21 was N/A%."* The SQL runs, returns zero rows (division by zero), and the template fill silently renders `N/A` as if this were an ordinary missing-data case rather than "this sub-division didn't exist that year." Same underlying situation as Q59, opposite (wrong) handling.
- **Q60 "LT-6 EV charging station demand in 2016-17"** *(flagged — silently misleading, not just wrong)*: the router's own `tariff_category_candidates` correctly surfaced the EV-charging-specific slab codes (`LT-6 C`/`LT-6 c`, "EVM Vheicle Charging Station") as separate, lower-confidence candidates — but the generated SQL queried the entire **LT-6 family rollup** instead, returning `136,735,112.29` labeled as "LT-6 EV charging stations demand in 2016-17." That figure is the combined total of *all* LT-6 sub-categories (public lighting, water-supply pumping, EV charging, etc.), not EV charging specifically — and EV charging as a distinct, metered category plausibly didn't exist yet in 2016-17 at all, which would make the honest answer "this category has no data for that year," not a large fabricated-looking number.
- **Q61 "LT-2(b)(ii) demand across all years"** *(flagged)*: `LT-2(b)(ii)` is a real, specific sub-slab code that lives in `fact_dcb`/`dim_tariff_category.tariff_code`, not in `fact_dcb_rollups.tariff_code_summarized` (which only holds family-level parent codes like bare `LT-2`). The generated SQL queries `fact_dcb_rollups` for `tariff_code_summarized = 'LT-2(b)(ii)'`, which structurally can never match, and gets `answerable: true`, *"No matching data was found for that question"* — a false negative for a category that genuinely has real data, reachable via the ordinary `fact_dcb`/`dim_tariff_category` path.

### K — Units / formatting requests (4 questions, 1 flagged)

- **Q63 "collection in lakhs," Q64 "as a percentage"** — both correct; Q63's SQL explicitly divides by 100000.
- **Q62 "total demand in crores"** *(flagged)*: SQL selects the raw `demand_total` (`3,541,649,882.79`) aliased as `total_demand_crores` — **never divides by 10,000,000**. The answer literally says *"...is 3,541,649,882.79 crores"*, off by a factor of ~1 crore (the real figure is ~₹354.16 Cr). This is a clean, unambiguous unit-conversion bug, made worse by Q63 proving the same class of request (lakhs) works correctly elsewhere in the same run.
- **Q65 "round to the nearest rupee"** — refused as ambiguous (no metric/scope named). Defensible, not flagged.

### L — Indirect superlatives / rankings (5 questions, 1 flagged)

- **Q66/Q67 "best"/"worst" sub-division** — correct and consistent with each other (same 6 rows, reversed order).
- **Q70 "most pending dues"** — correct, single clear answer (Holenarasipura).
- **Q68 "most industries"** — reasonably refused, correctly identifying "industries" isn't a real schema term and asking which metric was meant.
- **Q69 "top 3 tariff categories by demand"** *(flagged, minor)*: two of the three rows are the same `tariff_code` (`LT-4(a)`) under two different descriptions — a user asking for "top 3 categories" likely expects 3 distinct codes, not 2 rows of one code plus 1 of another. Also sums demand across **all 11 years combined** (no year filter) without ever saying so in the answer text — not wrong, just an undisclosed scope choice.

### M — Conversational follow-up (2 questions, 0 flagged — confirms a known limit, not a bug)

Q71 ("LT-1 demand in Arkalgud 2024-25") answered correctly (`16,602,383.92`). Q71b, asked as a completely separate request with no session state ("what about last year"), produced `entities: {}` — zero memory of "LT-1," "Arkalgud," or "2024-25" — and fell back to the generic Division_Total/latest-year default, answering about 2025-26 division-wide totals instead of Arkalgud LT-1 2023-24. This is the stateless-per-request pipeline design working exactly as built (no session/conversation memory anywhere in `app.pipeline`) — documented here as confirmed, real, observed behavior, not something to fix in this pass.

### N — Code formatting variations (6 questions, 5 flagged — most questions flagged of any category)

- **Q72 "lt-1"** (canonical lowercase-hyphenated) — fully correct, matches the known LT-1 2024-25 baseline exactly.
- **Q73 "LT1"** *(flagged)*: refused as "ambiguous" — inconsistent with Q72 handling the equivalent canonical form fine.
- **Q74 "Lt 1"** *(flagged, most concerning of the five)*: matched via `parent_category_matches` correctly, but the generated SQL has **no fiscal-year filter at all** — with multiple years' rows all matching `tariff_code_summarized='LT-1'`, `ORDER BY ... LIMIT 1` picks an arbitrary row among tied sort keys. It happened to land on 2025-26's combined total (`542,708,386.67`) this run, with no indication in the answer that the year was effectively picked at random rather than reasoned about. Compare to Q72, which explicitly defaulted to 2024-25 for the identical underlying request.
- **Q75 "l.t.-1"** *(flagged)*: the router's code-matching doesn't recognize dots between letters — `entities: {}`, no match at all. Falls back to a 79-row, all-subdivisions/all-years dump.
- **Q76 "HT - 2"** *(flagged, inconsistent with Q72)*: `parent_category_matches` correctly captures `HT-2`, but the LLM refuses anyway, asking for a fiscal year/sub-division — the exact same "bare code, no year/subdivision" shape Q72 (`lt-1`) answered without complaint.
- **Q77 "ht2"** *(flagged)*: no separator, no case — router doesn't match it (`entities: {}`), refused as ambiguous.

### O — Garbage input (5 questions, 0 flagged)

- Empty string, a single space, and 58 repeated `a` characters all handled gracefully — no crash, either a sensible default (empty string) or a clear, accurate refusal explaining why the input couldn't be parsed.
- **A genuine Kannada-language question** ("ಆರ್ಕಲ್‌ಗುಡ್‌ನಲ್ಲಿ ಬೇಡಿಕೆ ಎಷ್ಟು" — "how much demand in Arkalgud") was answered **correctly** — real per-year Arkalgud demand data, despite the router's own entity extraction (regex/string-matching, English-only) contributing nothing (`entities: {}`). The LLM understood the question in Kannada and generated correct, correctly-scoped SQL entirely on its own. A genuinely positive finding, worth knowing this capability exists even though nothing in this app was built to rely on it.
- The long rambling question (Q82) was refused cleanly, correctly identifying the one real entity buried in it ("Arkalgud") while accurately noting the metric was never actually specified.

---

## 5. Category F — explicit security verdict

**All 7 live natural-language attempts were refused before any SQL was ever generated** — the LLM itself declined every one (`plan.answerable: false`), so `app.sql_safety.validate_readonly_sql()` was **never actually invoked** for any of them (confirmed by reading `app/pipeline.py`: when `plan.answerable` is `False`, the function returns immediately, before the SQL-validation step). This means the live run proves the **prompt-level** defense works well, but says nothing on its own about whether the **code-level guard** would catch a payload if a future/different LLM response ever did try to slip one through.

**Supplementary check, run directly against `app.sql_safety.validate_readonly_sql` (bypassing the LLM, same pattern `tests/double_count_guard_test.py` already uses for its own guard):**

| Payload shape | Verdict |
|---|---|
| Literal `DELETE FROM fact_dcb WHERE fiscal_year='2024-25'` | **BLOCKED** — "Only SELECT / WITH ... SELECT statements are allowed." |
| Literal `DROP TABLE dim_subdivision` | **BLOCKED** — same reason |
| Literal `UPDATE fact_dcb SET demand_total = 0 WHERE subdivision_id = 1` | **BLOCKED** — same reason |
| Stacked-statement injection (`; SELECT * FROM fact_dcb; --`) | **BLOCKED** — "Multiple statements are not allowed." |
| Injection appended to an otherwise-real SELECT (`SELECT ...; DROP TABLE fact_dcb; --`) | **BLOCKED** — same reason |
| `SELECT * FROM sqlite_master` (schema enumeration) | **Allowed** — by design, not a bug: the guard's job is read-only enforcement, not restricting which tables can be read, and `sqlite_master` only exposes table/index DDL already partly implied by the app's own published schema, not credentials. Flagged here only so it isn't mistaken for an untested gap. |
| `SELECT load_extension('/etc/passwd')` | **Allowed by the text-level guard** (no forbidden keyword/multi-statement match) — **but independently blocked at the DB layer**: tried directly against the real `ReadOnlyDB` connection, it fails with `OperationalError: not authorized` (Python's `sqlite3` has extension loading disabled by default, and this app never enables it). Not a live vulnerability, but a real gap in `validate_readonly_sql`'s own keyword list worth closing for defense-in-depth (the DB-layer protection happens to save it here, but the guard shouldn't rely on that alone). |

**Verdict: PASS.** Every actual write/injection shape (DELETE, DROP, UPDATE, stacked statements) is blocked by both the prompt (live) and the code-level guard (direct test). One narrow hardening opportunity was found (`load_extension` isn't in the guard's own keyword blocklist) but it is not currently exploitable — the DB connection layer independently stops it.

---

## 6. Summary

- **Behaved reasonably: 58 of 83 (~70%).**
- **Confirmed new issues: 25**, spanning categories A, B, C, D, G, I, J, K, L, N. Zero were raw crashes — every failure was either a clean refusal/error message or a silently-wrong-looking answer.
- **Category with the most severe failures: J (known data-quirk regressions)** — 3 of 5 questions (60%), two of which (Saligrama post-closure, LT-6 EV-charging category) fabricate a plausible-looking number instead of admitting the entity/category didn't exist for that period, the exact failure shape this app has specifically fixed before for *other* questions.
- **Category with the most questions flagged by raw count: N (code-formatting variants)** — 5 of 6 (83%), all consistency/UX issues (refusing a valid format the router *did* correctly parse, or silently picking an unscoped/arbitrary year) rather than fabricated data.
- **Category F (security): explicit PASS**, both live and via the supplementary code-level guard check (§5).
- **Category M (conversational follow-up): confirms the known stateless-pipeline limit**, not a bug — documented with a concrete before/after example (§4, category M).

### One-line bug list, by category (for prioritization — nothing here has been fixed)

- **A** — Q4: a no-scope question refuses instead of defaulting, unlike 4 sibling questions in the same category.
- **B** — Q10/Q12: `is_definitional_question` doesn't handle bare acronyms in a compound glossary key ("IPP") or "stand for" phrasing ("DCB"), and the LLM's own fallback reasoning is factually wrong when it falls through.
- **C** — Q19: LLM generates SQL referencing a nonexistent column (`fy` instead of `fiscal_year`).
- **D** — Q25/Q29: a nonexistent sub-division silently returns a fabricated-looking "N/A"/"no data" with `answerable: true` instead of refusing like a nonexistent tariff code does.
- **G** — Q45: "versus" phrasing regresses to the pre-fix literal-tariff-code join pattern for LT-3/LT-5, producing mostly-zero results — the same bug class CLAUDE.md §11 already fixed once, for a phrasing its worked examples don't cover.
- **I** — Q50/Q51: "current year" and "last year" silently resolve to the identical year (2025-26); Q53: a wrong join column (`subdivision_name` compared to an ID) silently returns a false "no data."
- **J** — Q58: Saligrama's post-2017-18 closure isn't recognized (fabricates "N/A%" instead of explaining, unlike the sibling Division_Office question which handles this correctly); Q60: LT-6 EV-charging is answered with the whole LT-6 family's total instead of the specific sub-category (or a "didn't exist yet" refusal); Q61: a real sub-slab code (`LT-2(b)(ii)`) is incorrectly queried against the family-level rollup table, producing a false "no data."
- **K** — Q62: "in crores" doesn't actually divide by 10,000,000, just relabels the raw rupee figure.
- **L** — Q69: "top 3 tariff categories" can return the same tariff code twice (grouped by description too) and silently sums across all 11 years without saying so.
- **N** — Q73/Q76/Q77: bare tariff-code formats without a hyphen (`LT1`, `ht2`) or with spaces around the hyphen (`HT - 2`) are refused even when the router did extract the right entity; Q74: no fiscal-year filter at all for `Lt 1`, so the result is effectively a random pick among tied years; Q75: dotted format (`l.t.-1`) isn't recognized by the router at all.

Recommend fixing **one at a time**, as usual. Given severity, category J's two fabrication cases (Q58, Q60) and category G's Q45 regression seem like the highest-value next fixes — they're the ones most likely to make a real user believe a wrong number is correct.

---

## 7. Fix pass — Q45, Q58, Q60 (2026-09-17, same day)

Three fixes applied, all prompt/template content — **no pipeline/router/architecture changes**:

1. **`app/prompts.py`** — the comparison-wording paragraph described a stale version of worked example #5 (`SUM(CASE WHEN...)` over a `tariff_code LIKE` prefix). Corrected to describe example #5's real, current pattern (a per-side correlated subquery against `fact_dcb_rollups.tariff_code_summarized`), and made explicit that a family side must never use the slab-level pattern. **A second, more direct contributor to the same bug was found and fixed**: `DCB_Semantic_Layer/semantic_layer.json`'s `common_metrics` — always included in every prompt, never pruned — still defined `"domestic_demand": "SUM(demand_total) WHERE tariff_code LIKE 'LT-1%'"`, the exact buggy pattern, directly contradicting the rule sitting right above it in the same file. Fixing only `prompts.py` would have left this standing contradiction in place.
2. **`DCB_Semantic_Layer/example_queries.sql`** — added worked example #14 (LT-3 vs LT-5 collection, a different pair from #5's LT-1/HT-2) so the rollup-comparison pattern reads as the general rule, not something LT-1-specific.
3. **`app/pipeline.py::_fill_template`** — a single all-`NULL` row (a valid SQLite result for an aggregate query with zero matching source rows) is now treated the same as zero rows, returning an honest "no matching data" message instead of formatting `None` into a literal "N/A%".
4. **`app/prompts.py`** — the family-vs-slab rule needed **three iterations** to actually change live model behavior for Bug 3, worth recording honestly rather than presenting as a one-shot fix:
   - **Attempt 1**: added a paragraph saying a family code + a qualifying word matching one of that family's own real sub-category descriptions is slab-level, not family-level. Live-tested: **no effect**, Q60 still returned the whole LT-6 family total.
   - **Attempt 2**: found the actual reason — a few lines later in the *same* paragraph, an existing sentence said "ignore `tariff_category_candidates` entirely for a family-level question, even when one is listed," which the model could read as overriding the new rule. Added an explicit exception tying that sentence back to the new check. Live-tested: **still no effect**.
   - **Attempt 3**: rewrote the rule as a front-loaded, imperative "STOP — check this first" block (order/salience matters a lot to this model), explicitly addressing the low `match_score` concern (a source typo — "EVM Vheicle Charging Station" for "Vehicle" — was dragging the candidate's word-overlap score down to 0.5–0.6, which may have read as "not a real match"), and added **worked example #15** (`example_queries.sql`) working the exact LT-6/EV-charging case end to end with verified real numbers. This combination worked — see below. Consistent with this project's own prior finding (`CLAUDE.md` bug 4.7): a concrete worked example moves this model's behavior far more reliably than prose rules alone.

### Live re-verification (real LLM, real DB — same three questions, run fresh after all fixes)

| Q | Question | Before | After |
|---|---|---|---|
| **Q45** | "LT-3 versus LT-5 collection" | `lt3_collection=0` for all 11 years (bug) | `lt3_collection` real and nonzero every year (57.6M → 142.5M), `lt5_collection` correctly 0 for years with no real LT-5 collection and real nonzero for 2024-25/2025-26 — **cross-checked directly against the raw `fact_dcb_rollups` table, matches exactly** |
| **Q58** | "Saligrama collection efficiency in 2020-21" | `"...was N/A%."` (fabricated-looking) | `"No matching data was found for that question — this combination may not exist, or may not have been active, for that period."` |
| **Q60** | "LT-6 EV charging station demand in 2016-17" | `"...was 136,735,112.29 units"` (whole LT-6 family total, mislabeled) | `answerable: false`, correctly explains EV charging wasn't billed yet in 2016-17 (verified against the DB: this tariff_category has zero rows before 2022-23, and 2022-23 itself is 0.0) |

**All three confirmed fixed**, verified against real DB figures, not just "a different answer came back."

**One honest caveat on Q60, not hidden**: asking for the *same* category in a year it does have real data (`"LT-6 EV charging station demand in 2024-25"`) now correctly routes to the slab-level table (the core bug — silently substituting the whole family total — is gone), but landed on a different one of three near-identical, typo'd tariff_category descriptions under LT-6 (`"EVM Vheicle Charging Station"`, no HT/LT suffix, which itself has no 2024-25 row and correctly reports "no data") rather than `"EVM Vheicle Charging Station LT"` (which does, ₹31,200.57 verified). Both are honest, non-fabricated answers — neither invents a number — but a user asking about 2024-25 specifically would likely want the LT-suffixed one. This is a narrower, separate ambiguity (which of 3 near-duplicate slab descriptions to prefer) than the reported bug (fabricating the family total), not fixed in this pass — flagged here for a future one-at-a-time round if wanted.

### Full 83-question re-run — regression check

Re-ran the complete category A–O set after all fixes (`tests/eval_categorized_results.json`, previous run preserved as `tests/eval_categorized_results_before_fix.json`). **Zero crashes, 83/83 completed.** Every one of the 25 originally-flagged questions was individually re-compared before vs. after:

**Two bonus fixes** — not explicitly targeted, resolved as a side effect of the same root-cause fixes:
- **Q25** ("collection in Mysuru sub-division", category D) — previously fabricated `"...is N/A."` for a nonexistent sub-division; now correctly returns the same honest "no matching data" message as Q58, since it hits the exact same single-all-`NULL`-row case the `_fill_template` fix (#3 above) generalizes over, not a name-specific fix.
- **Q61** ("LT-2(b)(ii) demand across all years", category J) — previously misrouted to the family-level rollup table (false "no data"); now correctly identified as slab-level (a real, specific sub-code) and routed to `fact_dcb`/`dim_tariff_category`, returning real per-year figures — the same disambiguation logic added for Bug 3.

**One new issue found, not present before this fix pass** — reported honestly rather than smoothed over:
- **Q21** ("compare curent year and last year collection", category C) — the SQL now uses two per-side correlated subqueries against `fact_dcb_rollups` (the pattern this fix pass reinforced for family comparisons) but **both subqueries are textually identical and never scope to any tariff family at all** — the `tariff_code_summarized` filter that makes the pattern meaningful is simply missing from both sides. The two "current"/"previous" columns come back with the same value in every row, coincidentally matching an arbitrary single tariff code's rollup figure (not any real division-wide total). This looks like the model over-generalizing the reinforced correlated-subquery *shape* from example #5/#14 to a comparison question that was never about tariff families in the first place. Not one of the three reported bugs, and not fixed in this pass — flagged here as a new, real finding from the regression re-run, for a decision on whether to address it next.

All other changed answers among the 25 originally-flagged questions were reviewed individually and are consistent with this project's already-documented LLM non-determinism (no fixed seed) rather than anything caused by this fix pass — none regressed to a worse state than their original finding.
