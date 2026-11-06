# DCB Chatbot Regression Report — Holenarasipura Division

**Target:** `dcb-ai-app` (FastAPI + Gemini NL2SQL chatbot), chat UI at `/chat`, live SQLite star schema (`DCB_Star_Schema/dcb_star_schema.sqlite`)
**Test set:** `test_questions.json` (49 questions, 14 categories)
**Driver:** `run_frontend_tests.py` (Playwright, real browser, headless Chromium)

> **Note on the database engine.** The test brief described the backend as running "over Neon Postgres." The actual, running `dcb-ai-app` in this repo connects to a local, read-only SQLite file (`DCB_Star_Schema/dcb_star_schema.sqlite`) via `app/db.py`, not Postgres/Neon. This report tests the app as it actually runs; every number below is against that SQLite backend.

---

## 1. Executive summary

The regression suite was run against the live chat UI twice: once before any changes (**26/49 passing**), and once after four rounds of fixes to the NL2SQL entity-extraction and prompt logic (**47/49 passing**). All 21 fixes were to how the backend grounds a question's tariff category against the real database before handing it to the LLM — no fixes touched the database, the schema, or the test set itself. The two questions still failing after all fixes (#16, #30) are a distinct, narrower problem — the model refusing a *comparison* between two named tariff categories — confirmed to fail consistently across repeated fresh samples, not a one-off. **Verdict: substantially improved and ready for the common case (a single named or paraphrased tariff category), not yet ready for two-way tariff-category comparison questions**, which need a separate, targeted fix (see §6).

*(The automated harness's own keyword-based pass/fail heuristic reported 28/49 before and 49/49 after; both of those numbers are wrong in one direction each — see the methodology note in §2 for why, and how the 26/49 and 47/49 figures above were verified by hand instead.)*

---

## 2. Methodology

**What was tested.** 49 natural-language questions against the Holenarasipura DCB dataset, spanning 14 categories: abbreviated tariff-category names, Free-Lighting/Office-Lighting suffix variants, tariff code vs. free-text description phrasing, IP-set metered/rural/urban splits, a deliberate tariff-code collision (LT-6 reused for both "Water Supply" and "Public Lighting"), miscellaneous LT categories, the full HT-category family, miscellaneous non-tariff revenue heads, Division-vs-Sub-Division name ambiguity, section-level (no-sub-division) queries, metric variety (KW/KVA/consumption/arrears), typo tolerance, year-trend queries, and cross-subdivision aggregation/ranking.

**How it was tested.** `run_frontend_tests.py` drives a real, headless Chromium browser via Playwright against the actual running chat UI at `http://127.0.0.1:8000/chat` — it types into the real `<input id="box">`, clicks the real `<button id="send">`, and reads the real rendered `.msg.bot .bubble` reply out of the DOM. This is not a mocked API call; every question went through the full stack: browser → `POST /api/ask` (SSE) → `app/pipeline.py` → live Gemini call → SQLite.

**Two things about the test harness itself had to be fixed/accounted for before its numbers could be trusted:**

1. **Original `CONFIG` selectors didn't match this app's DOM at all.** `chat_input_selector` looked for `input[type='text']`, but this app's `<input id="box">` has no `type` attribute; `response_bubble_selector` looked for `[data-role='assistant'], .assistant-message, .bot-message`, none of which exist in `app/static/chat.html`. Fixed by reading the actual template and setting `chat_input_selector: "#box"`, `send_button_selector: "#send"`, `response_bubble_selector: ".msg.bot .bubble"`.
2. **The original wait strategy resolved almost instantly, before the answer had streamed in.** `send()` in `chat.html` creates the empty bot-reply bubble *synchronously*, before the network request even starts, so the original "wait until the response-bubble count increases" check would fire while the SSE answer was still streaming in (sometimes 2-6+ seconds later), capturing a blank or half-written answer. Replaced with a wait on the send button's own `disabled` state, which `chat.html` only clears once the SSE stream has fully ended and the meta/technical-details block has been rendered — the one DOM signal that actually means "this answer is complete." Both fixes are in `run_frontend_tests.py`, not in the app itself.
3. **The harness's `looks_like_failure()` keyword list has real blind spots**, on both sides:
   - It missed genuine refusals that don't happen to contain one of its trigger phrases — e.g. *"those exact sub-slabs do not appear as distinct codes... Please clarify"* and *"could not be uniquely matched or does not exist"* were both marked **OK** by the harness despite the app's own UI showing a **"not answerable from this data"** badge on them.
   - Conversely, on two genuinely-correct refusals (#40 "section number 8" doesn't exist; #46 no monthly granularity in an annual dataset) the same wording pattern would *also* have been misread as a failure if it had matched a trigger phrase — it happened not to, but the point stands: keyword matching on free-text LLM output is not a reliable pass/fail oracle either direction.

   Every FAIL/OK boundary in this report was therefore **re-verified by reading the actual captured answer text** (and, for the two still-failing questions, by re-querying the live API three additional times each to rule out a one-off sampling fluke) rather than trusted from the harness's own `status` field. This is why the headline numbers here (26/49 → 47/49) differ from the harness's own summary line (28/49 → 49/49).

**Non-determinism.** Every question is answered by a live Gemini call with no fixed seed — the same question can occasionally generate different SQL on different runs. Where that mattered to a finding below, it's called out explicitly.

---

## 3. Results by category

| Category | # Questions | Passed Before | Passed After | Notes |
|---|---|---|---|---|
| abbreviated-category | 8 | 3 | 8 | Paraphrased/abbreviated slab names (Root Cause B) |
| fl-ol-suffix | 4 | 3 | 4 | Free/Office-Lighting suffix variants (Root Cause B) |
| code-vs-description | 4 | 0 | 3 | Direct code truncation (Root Cause A) **+ #16 still fails** |
| ip-sets-splits | 4 | 1 | 4 | Metered/rural/urban slab paraphrases (Root Cause B) |
| code-collision | 2 | 2 | 2 | Already passing before and after |
| misc-lt | 2 | 0 | 2 | Obscure LT-7/LT-7b description paraphrases (Root Cause B) |
| ht-category | 9 | 4 | 8 | Mix of A/B causes **+ #30 still fails** |
| misc-receipts | 3 | 2 | 3 | Numeric misc-revenue codes only reachable by description (Root Cause B) |
| location-ambiguity | 2 | 2 | 2 | Already passing before and after |
| section-level | 2 | 2 | 2 | Already passing before and after |
| metric-variety | 3 | 2 | 3 | #41 was a harness false-negative (Root Cause A) |
| typo-tolerance | 1 | 1 | 1 | Already passing before and after |
| time-trend | 2 | 1 | 2 | #45 direct-code truncation (Root Cause A) |
| aggregation-ranking | 3 | 3 | 3 | Already passing before and after |
| **TOTAL** | **49** | **26** | **47** | |

---

## 4. Detailed findings

### 4.1 Root causes and fixes

Every one of the 21 questions that got fixed traces back to one of four root causes, all inside the router that runs *before* the LLM call (`app/semantic_layer.py::extract_entities`) or the system prompt that consumes its output (`app/prompts.py::SYSTEM_INSTRUCTIONS`). Nothing outside these two files changed.

#### Root Cause A — tariff-*code* entity extraction only recognized the 14 generic family codes

`extract_entities()` matched a question's tariff code against a hardcoded tuple of 14 family codes (`LT-1`..`LT-7`, `HT-1`..`HT-7`) via plain substring containment. A question naming a real, more specific sub-code — `LT-3(i)`, `HT-2(b)(ii)`, `LT-5(a)` — still contains its family prefix as a substring (`"LT-3"` *is* a substring of `"LT-3(i)"`), so the router silently truncated it to the family code and handed the LLM the *wrong* value as if it were verified ground truth. The LLM trusted it (per its own instructions to reuse pre-extracted entities), producing SQL against a family code that sometimes doesn't exist as its own row at all.

Example, live before the fix:
```
Q: "LT-3(i) collection efficiency in Arkalgud"
entities = {"subdivision_name": "Arkalgud", "tariff_code": "LT-3"}   # truncated!
SQL:  ... WHERE t.tariff_code = 'LT-3' ...   -- no such row; there is no bare 'LT-3'
rows: 0  ->  "No matching data was found for that question."
```

**Fix** — `app/semantic_layer.py::extract_entities`, replaced the 14-code hardcoded list with a scan against **every real, distinct `tariff_code` in `dim_tariff_category`** (already loaded into `RouterIndex.tariff_categories` at startup), keeping only the most specific match when a shorter and a longer real code both appear in the question:

```python
# before
tariff_codes_found = [
    code for code in ("LT-1","LT-2",...,"HT-7")
    if code.lower() in q_lower
]

# after
all_codes = {code for code, _ in index.tariff_categories}
raw_code_matches = [code for code in all_codes if _code_in_question(code)]
tariff_codes_found = [
    code for code in raw_code_matches
    if not any(other != code and other.lower().startswith(code.lower())
               for other in raw_code_matches)
]
```
`_code_in_question` also does a whitespace-collapsed comparison, which incidentally fixed a real DB spacing quirk too — the row literally coded `HT - 3b` now matches a question typed as `HT-3b`.

Fixed: **#13, #14, #41, #45**, and resolved the code-collision half of **#16** (both `LT-5(a)` and `LT-5(b)` are now correctly extracted as distinct codes instead of both collapsing to `LT-5`) — though #16 still fails for an unrelated reason (§4.2).

#### Root Cause B — no fuzzy matching for paraphrased tariff *descriptions*

A slab-level tariff category (e.g. `LT-2(b)(i)` = *"Ltng. ,heating & Motive Power , Pvt. Institu. in ULB's & CC"*) can only be identified by its free-text `description` — there's no shorter code a user would know to type. A prior fix in this session already grounded the LLM when a question quotes that description **verbatim**, but the large majority of real questions *paraphrase or abbreviate* it instead (*"Pvt institutions lighting in ULB's and CC"*), which the verbatim check can never catch. With zero grounding, the LLM either refused outright or guessed its own `LIKE`/equality pattern from the question's wording — which essentially never matches the DB's actual (often abbreviated or typo'd) text.

**Fix** — `app/semantic_layer.py::extract_entities`, added a second, fallback stage: a deterministic bag-of-words overlap scorer (no LLM, no embeddings, in keeping with this router's existing design) that ranks every one of the 106 distinct descriptions by what fraction of *its own* words appear in the question, after normalizing case/punctuation and mapping known DB abbreviations/typos to a shared spelling:

```python
_SYNONYMS = {
    "ltng": "lighting", "comme": "commercial", "pvt": "private",
    "govt": "government", "institu": "institution", "univercity": "university",
    "chartable": "charitable", "then": "than", "basses": "basis",
    "pan": "panchayat", "panchayats": "panchayat",
}
_LOW_SIGNAL_WORDS = {"lighting", "heating", "motive", "power"}  # see below

def _content_tokens(text):
    words = _normalize(text).split()
    mapped = (_SYNONYMS.get(w, w) for w in words)
    return {w for w in mapped if w not in _STOPWORDS and w not in _LOW_SIGNAL_WORDS}

# score = |description tokens ∩ question tokens| / |description tokens|,  keep >= 0.5,
# top 6, entities["tariff_category_candidates"] = [{tariff_code, description, match_score}, ...]
```

Two refinements were needed once real questions were run through it:

- **DB-typo/abbreviation synonyms.** `Ltng.`→lighting, `Comme.`→commercial, `Pvt.`→private, `Govt.`→government, `Institu.`→institution are the sheet's own abbreviations; `Univercity`, `Chartable`, `Less then one week`, `permanent connection Basses` are the sheet's own **misspellings** (verified against the live DB, not guessed) — without mapping these, a correctly-spelled question would never overlap enough with the DB's own broken spelling to pass the score threshold.
- **Low-signal boilerplate.** 17-19 of the 106 descriptions share the same "Ltng ,heating & Motive Power" boilerplate (exactly the LT-1/LT-2/LT-3/LT-5 family that's hardest to disambiguate). Counting those words toward every sibling's score diluted the ratio enough that a natural question omitting them (nobody says *"lighting heating motive power"* when they mean *"private institutions in village panchayats"*) fell under threshold — found live: **#4** returned *zero* candidates and the LLM hallucinated a plausible-but-wrong description (*"...in ULB's & Village Pan."*, blending two different real rows' wording) rather than admitting it didn't know. Excluding these four words from the *scoring* token set (never from the exact-match check, which still requires the complete real string) fixed it.

Fixed: **#3, #4, #5, #6, #8, #12, #18, #19, #20, #23, #24, #25, #26, #28, #31, #35**.

#### Root Cause C — SQL apostrophe escaping

Several real descriptions and one sub-division-adjacent phrase contain a literal apostrophe (`...in ULB's & CC`). SQLite requires doubling an embedded apostrophe inside a single-quoted string literal (`'ULB''s'`); on one live sample the model instead substituted an underscore (`'...in ULB_s & CC'`), which obviously matches no row.

**Fix** — `app/prompts.py::SYSTEM_INSTRUCTIONS`, added an explicit hard rule spelling out SQLite's actual escaping convention with a worked example, so the model doesn't have to infer it:
```
- Several real dim_tariff_category.description values ... contain a literal
apostrophe... SQLite's escape for an apostrophe INSIDE a single-quoted string
literal is to DOUBLE it (''), not to drop it or substitute an underscore/space
— e.g. WHERE description = 'Comme. Ltng ,heating & Motive Power , in ULB''s & CC'.
```
Fixed: **#5** (this was also blocking it independently of Root Cause B).

#### Root Cause D — no guidance on family-level vs. slab-level questions

Introducing `tariff_category_candidates` (Root Cause B's fix) created a new failure mode: for a question naming the whole tariff *family* in general terms (*"Residential apartments HT category billing efficiency"* — no HP band, no metered/unmetered split, no suffix), the fuzzy matcher still surfaces its single best-matching *slab* as a candidate, and the model narrowed to that one slab instead of the whole `HT-4` family — even though `dim_tariff_category`'s own semantic-layer note already says family-level questions should `GROUP BY tariff_code` instead. On live data, the one slab the matcher offered (*"Residential Apartments Colonies..."*) happens to have zero demand in the latest year for every sub-division, so narrowing to it turned a real, non-empty question into an all-`N/A` answer.

**Fix** — `app/prompts.py::SYSTEM_INSTRUCTIONS`, added guidance to check for family-level phrasing *first* and prefer `GROUP BY tariff_code` over any listed candidate in that case, reserving `tariff_category_candidates` for when the question names something more specific than the bare family (a slab, tier, or suffix).

Fixed: **#32**.

### 4.2 Per-question findings

Each subsection below is one of the 23 questions that failed (by manually-verified status, not the harness's raw label) in the before-run. "Fix" references the root cause in §4.1 that resolved it; the code snippet isn't repeated per-question since, in every case, it's the same fix already shown above.

---

**#3 — abbreviated-category** · *"Pvt institutions lighting in ULB's and CC — connected load in Hangarahalli"* · target `LT-2(b)(i)` / Hangarahalli (Sub-Division)
Before: `No matching data was found for that question.`
Root cause: B (no candidate returned; LLM's own `LIKE` guess never matched real text).
Fix: Root Cause B. After (verified): 11 rows of real `connected_load_kw` data, 2015-16→2025-26. **FIXED.**

**#4 — abbreviated-category** · *"Private institutions in Village Panchayats — collection efficiency in Arkalgud"* · target `LT-2(b)(ii)` / Arkalgud
Before: `No matching data was found for that question.`
Root cause: B, specifically the low-signal-boilerplate + `Pan.`→panchayat gap (this was the exact case that motivated the second B refinement — the description's discriminating words were `Village`/`Pan.`, both diluted/missed before the fix).
Fix: Root Cause B (both refinements). After (verified, and re-sampled 3× to confirm stability): 11 rows of real `collection_efficiency_pct` data. **FIXED.**

**#5 — abbreviated-category** · *"Comme. Ltng heating and motive power in ULB's and CC — closing balance in Holenarasipura"* · target `LT-3(i)` / Holenarasipura (Division)
Before: `No matching data was found for that question.`
Root cause: B and C together — even once grounded with the right candidate description, the LLM's first attempt mis-escaped the apostrophe in it (`ULB_s`), independently producing zero rows.
Fix: Root Causes B + C. After (verified): real `closing_balance` figure returned, generated SQL now correctly reads `'...ULB''s & CC'`. **FIXED.**

**#6 — abbreviated-category** · *"Commercial lighting heating motive power in Village Panchayats, Saligrama"* · target `LT-3(ii)` / Saligrama
Before: `No matching data was found for that question.`
Root cause: B.
Fix: Root Cause B. After (verified): 3 rows (Saligrama's real data range is FY2015-16→2017-18 only — it closed after that, per the semantic layer's own documented note, not a bug). **FIXED.**

**#8 — abbreviated-category** · *"Bhagyajyothi Kutirajyothi metered 6 to 18 units installations in Holenarasipura division"* · target `LT-1`
Before: `No matching data was found for that question.`
Root cause: B — the LLM had guessed an exact-equality string reconstructed from the question's own wording, which didn't match the DB's actual `"Bhagyajyothi & Kutirajyothi (BV/KJ) ( Metered) 6 to 18 Units"`.
Fix: Root Cause B. After (verified): real `billed_installations` figures. **FIXED.**

**#12 — fl-ol-suffix** · *"Office lighting for commercial Village Panchayat category in Ramanathapura"* · target `LT-3(ii)(OL)`
Before: `No matching data was found for that question.`
Root cause: B.
Fix: Root Cause B. After (verified): 11 rows of real `demand_total`/`collection_grand_total`. **FIXED.**

**#13 — code-vs-description** · *"LT-3(i) collection efficiency in Arkalgud"* · target `LT-3(i)`
Before: `No matching data was found for that question.` — entities extracted `tariff_code: "LT-3"` (truncated).
Root cause: A, the canonical case.
Fix: Root Cause A. After (verified): correctly filters on `LT-3(i)` (answer is `N/A%` because the latest-year demand for this slab happens to be 0 in Arkalgud — a genuine data fact, not a bug; earlier years have real non-null values). **FIXED** (query correctness fixed; the specific `N/A` value is correct given the underlying data).

**#14 — code-vs-description** · *"HT-2(b)(ii) demand for FY 2020-21"* · target `HT-2(b)(ii)`
Before: `No matching data was found for that question.` — entities extracted `tariff_code: "HT-2"`, and the LLM tried to stuff the rest of the code into `description` as a workaround.
Root cause: A.
Fix: Root Cause A. After (verified): correct scalar demand figure for FY2020-21. **FIXED.**

**#15 — code-vs-description** · *"What is LT-5(b) heating and motive power, 5 to 40 HP slab, in Saligrama"* · target `LT-5(b)`
Before: `No matching data was found for that question.`
Root cause: A (code truncated to `LT-5`, which collapsed both `LT-5(a)` and `LT-5(b)` together).
Fix: Root Cause A. After (verified): 12 rows, correctly scoped to `LT-5(b)`'s four HP-band descriptions. **FIXED.**

**#16 — code-vs-description** · *"Same heating and motive power 5 to 40 HP slab but under LT-5(a) instead of LT-5(b), Holenarasipura"* · target `LT-5(a)`, *"tests whether the code, not just the text, drives the match"*
Before: refused — *"those exact sub-slabs do not appear as distinct codes... Please clarify"* (harness mislabeled this **OK**; the UI's own "not answerable from this data" badge says otherwise).
Root cause: A is fixed — `extract_entities` now correctly returns `tariff_codes: ["LT-5(b)", "LT-5(a)"]`, both real, distinct codes. But the question's phrasing ("X instead of Y") reads to the model as a *two-way comparison request*, and it refuses rather than treating it as "just show me the LT-5(a) version."
**After: still refuses.** Re-sampled 3 additional times against the live API post-fix — refused all 3, with different wording each time but the same "please specify/clarify" shape. **NOT FIXED** — see §6 for the recommended follow-up.

**#18 — ip-sets-splits** · *"IP sets up to 10HP on urban feeders, metered — connected load in Konanur"* · target `LT-4(a)`
Before: *"I could not find a tariff category matching..."*
Root cause: B.
Fix: Root Cause B. After (verified): 11 rows returned (all `connected_load_hp=0` — Konanur genuinely has no connected load recorded against this specific metered/urban slab; a real, correctly-empty result, not a refusal). **FIXED** (the app now runs the right query; the data itself is legitimately zero).

**#19 — ip-sets-splits** · *"IP sets for private horticulture nurseries, coffee and tea, 10HP and below, in Saligrama"* · target `LT-4(c)(i)`
Before: *"I could not find a tariff code or description matching..."*
Root cause: B.
Fix: Root Cause B. After (verified): 3 rows returned (Saligrama's real data window). **FIXED.**

**#20 — ip-sets-splits** · *"Above 10HP horticulture nurseries coffee tea IP sets demand in Ramanathapura"* · target `LT-4(c)(ii)`
Before: `No matching data was found for that question.`
Root cause: B.
Fix: Root Cause B. After (verified): 11 rows returned. **FIXED.**

**#23 — misc-lt** · *"Temporary supply less than one week — revenue in Konanur"* · target `LT-7`
Before: `No matching data was found for that question.`
Root cause: B — plus this description contains the DB's own typo, *"Less then one week"*, requiring the `then→than` synonym.
Fix: Root Cause B. After (verified): 11 rows returned. **FIXED.**

**#24 — misc-lt** · *"Power supply for hoardings and advertisement boards on permanent connection basis, Ramanathapura"* · target `LT-7 b`
Before: `No matching data was found for that question.`
Root cause: B — plus the DB's own typo *"permanent connection Basses"*, requiring the `basses→basis` synonym.
Fix: Root Cause B. After (verified): 11 rows returned. **FIXED.**

**#25 — ht-category** · *"Industrial other than BMA at 11 KV — connected load in Hangarahalli"* · target `HT-2(a)(ii)`
Before: *"I could not find a matching tariff code or description..."*
Root cause: B — this is also the case that best demonstrates the scorer correctly discriminating between 6 near-identical HT-2(a)(ii) description rows (11 KV / 33 KV / 33 KV EHT / 66 KV / 66 KV EHT / Railway Traction) by KV tier.
Fix: Root Cause B. After (verified): 11 rows returned. **FIXED.**

**#26 — ht-category** · *"Industrial other than BMA — Railway Traction and ET Plant billing in Holenarasipura division"* · target `HT-2(a)(ii)`
Before: *"I could not find a tariff code or description matching..."*
Root cause: B.
Fix: Root Cause B. After (verified): 11 rows returned. **FIXED.**

**#28 — ht-category** · *"Govt charitable university and ESI hospital hostels — demand in Saligrama"* · target `HT-2(c)(i)`
Before: `No matching data was found for that question.`
Root cause: B — the real description contains two of the sheet's own typos at once (*"Univercity"*, *"Chartable"*), both requiring synonym entries.
Fix: Root Cause B. After (verified): 3 rows returned, correctly matched against the misspelled real description. **FIXED.**

**#30 — ht-category** · *"Private LI schemes connected to urban express feeders versus other private LI schemes — compare demand in Hangarahalli"* · target `HT-3(a)(ii) vs HT-3(a)(iii)`
Before: *"I could not find tariff categories matching... Please specify the exact tariff codes or descriptions."*
Root cause: B gets the model to the *right single candidate* (`HT-3(a)(ii)`, confirmed in the after-run's own reasoning: *"I see the candidate for private LI schemes connected to urban/express feeders (HT-3(a)(ii))..."*), but the question asks for a comparison against **"other" schemes** — an open-ended complement, not a second named category the candidate-matcher (or anything else grounded) can point at.
**After: still refuses**, consistently — re-sampled 3 additional times, refused all 3 with materially the same reasoning each time. **NOT FIXED** — see §6.

**#31 — ht-category** · *"Irrigation and agricultural farms and horticulture HT category consumption in Saligrama"* · target `HT-3b`
Before: `No matching data was found for that question.`
Root cause: B and, incidentally, the same DB-spacing quirk noted under Root Cause A (`HT-3b` vs. the DB's own `HT - 3b`) — the fuzzy scorer isn't sensitive to that spacing since it tokenizes on non-alphanumerics anyway, so this one scored a clean 1.0 candidate match.
Fix: Root Cause B. After (verified): 3 rows returned. **FIXED.**

**#32 — ht-category** · *"Residential apartments HT category billing efficiency across all sub-divisions"* · target `HT-4`
Before (harness said OK — a genuine pass at the time): real per-sub-division percentages.
**Regression introduced, then fixed, within this same work:** once Root Cause B's candidate matcher existed, it started narrowing this family-level question to one specific (currently zero-demand) slab, turning it into an all-`N/A` answer. This was caught before being reported as "fixed" by diffing against the original passing run.
Root cause: D.
Fix: Root Cause D. After (verified): real per-sub-division percentages restored, generated SQL now uses `t.tariff_code LIKE 'HT-4%'` with no `description` filter. **FIXED** (back to its original correct behavior).

**#35 — misc-receipts** · *"Rental income from staff quarters across all sub-divisions combined"* · target `62.9017`
Before: `No matching data was found for that question.`
Root cause: B — this is the case for the numeric misc-revenue codes generally: a user has no way to know `62.9017` exists, so the *only* path in is the description (*"Rental from Staff Qtrs"*), which the paraphrase matcher now handles.
Fix: Root Cause B. After (verified): 11 rows returned. **FIXED.**

**#41 — metric-variety** · *"Connected load in KVA for HT-2(a)(ii) industrial category in Hangarahalli"* · target `HT-2(a)(ii)`
Before (harness said OK — false negative): *"The tariff code HT-2(a)(ii) with description slab could not be uniquely matched or does not exist... "* with a "not answerable" badge — same Root Cause A truncation as #13/#14 (`HT-2(a)(ii)` → `HT-2`).
Fix: Root Cause A. After (verified): real scalar KVA figure returned. **FIXED** (previously mis-reported as passing by the harness; genuinely broken before, genuinely fixed now).

**#45 — time-trend** · *"Show the year-on-year demand trend for LT-3(i) in Holenarasipura from FY2015-16 to FY2025-26"* · target `LT-3(i)`
Before: `No matching data was found for that question.`
Root cause: A (same `LT-3(i)`→`LT-3` truncation as #13).
Fix: Root Cause A. After (verified): multi-year trend data returned with a line chart. **FIXED.**

---

## 5. Full results appendix

| ID | Question | Category | Status (before → after) |
|---|---|---|---|
| 1 | Ltng heating and motive power BMA and Municipal Corp collections in Arkalgud | abbreviated-category | OK → OK |
| 2 | Urban Local Bodies lighting heating motive power collections in Saligrama | abbreviated-category | OK → OK |
| 3 | Pvt institutions lighting in ULB's and CC — connected load in Hangarahalli | abbreviated-category | FAIL → OK |
| 4 | Private institutions in Village Panchayats — collection efficiency in Arkalgud | abbreviated-category | FAIL → OK |
| 5 | Comme. Ltng heating and motive power in ULB's and CC — closing balance in Holenarasipura | abbreviated-category | FAIL → OK |
| 6 | Commercial lighting heating motive power in Village Panchayats, Saligrama | abbreviated-category | FAIL → OK |
| 7 | IP sets above 10 HP total consumption in Hangarahalli | abbreviated-category | OK → OK |
| 8 | Bhagyajyothi Kutirajyothi metered 6 to 18 units installations in Holenarasipura division | abbreviated-category | FAIL → OK |
| 9 | Free lighting demand under BMA and Municipal Corporation category in Hangarahalli | fl-ol-suffix | OK → OK |
| 10 | Free lighting for Urban Local Bodies category in Ramanathapura | fl-ol-suffix | OK → OK |
| 11 | Office lighting under commercial ULB's and CC category in Hangarahalli | fl-ol-suffix | OK → OK |
| 12 | Office lighting for commercial Village Panchayat category in Ramanathapura | fl-ol-suffix | FAIL → OK |
| 13 | LT-3(i) collection efficiency in Arkalgud | code-vs-description | FAIL → OK |
| 14 | HT-2(b)(ii) demand for FY 2020-21 | code-vs-description | FAIL → OK |
| 15 | What is LT-5(b) heating and motive power, 5 to 40 HP slab, in Saligrama | code-vs-description | FAIL → OK |
| 16 | Same heating and motive power 5 to 40 HP slab but under LT-5(a) instead of LT-5(b), Holenarasipura | code-vs-description | **FAIL → FAIL** |
| 17 | IP sets up to 10 HP on rural feeders — compare metered vs unmetered installations in Arkalgud | ip-sets-splits | OK → OK |
| 18 | IP sets up to 10HP on urban feeders, metered — connected load in Konanur | ip-sets-splits | FAIL → OK |
| 19 | IP sets for private horticulture nurseries, coffee and tea, 10HP and below, in Saligrama | ip-sets-splits | FAIL → OK |
| 20 | Above 10HP horticulture nurseries coffee tea IP sets demand in Ramanathapura | ip-sets-splits | FAIL → OK |
| 21 | Water supply CMC TMC TP category collections in Arkalgud | code-collision | OK → OK |
| 22 | Public lighting Grampanchayath collection efficiency in Saligrama | code-collision | OK → OK |
| 23 | Temporary supply less than one week — revenue in Konanur | misc-lt | FAIL → OK |
| 24 | Power supply for hoardings and advertisement boards on permanent connection basis, Ramanathapura | misc-lt | FAIL → OK |
| 25 | Industrial other than BMA at 11 KV — connected load in Hangarahalli | ht-category | FAIL → OK |
| 26 | Industrial other than BMA — Railway Traction and ET Plant billing in Holenarasipura division | ht-category | FAIL → OK |
| 27 | Commercial other than under BMA — collection efficiency in Arkalgud | ht-category | OK → OK |
| 28 | Govt charitable university and ESI hospital hostels — demand in Saligrama | ht-category | FAIL → OK |
| 29 | Lift irrigation government department at 33KV — arrears in Konanur | ht-category | OK → OK |
| 30 | Private LI schemes connected to urban express feeders versus other private LI schemes — compare demand in Hangarahalli | ht-category | **FAIL → FAIL** |
| 31 | Irrigation and agricultural farms and horticulture HT category consumption in Saligrama | ht-category | FAIL → OK |
| 32 | Residential apartments HT category billing efficiency across all sub-divisions | ht-category | OK → OK |
| 33 | Temporary supply 67 HP and above — revenue in Ramanathapura | ht-category | OK → OK |
| 34 | Reconnection fee D&R total for Holenarasipura division | misc-receipts | OK → OK |
| 35 | Rental income from staff quarters across all sub-divisions combined | misc-receipts | FAIL → OK |
| 36 | Delayed payment charges collected in Ramanathapura | misc-receipts | OK → OK |
| 37 | Total demand for Holenarasipura — I mean the division level, not the sub-division | location-ambiguity | OK → OK |
| 38 | Compare commercial lighting demand between Holenarasipura sub-division and Holenarasipura division | location-ambiguity | OK → OK |
| 39 | Konanur section water supply collections | section-level | OK → OK |
| 40 | Show DCB data for section number 8 | section-level | OK → OK *(correct refusal both times — no such section)* |
| 41 | Connected load in KVA for HT-2(a)(ii) industrial category in Hangarahalli | metric-variety | FAIL → OK |
| 42 | Assessed vs metered consumption for Water Supply CMC/TMC/TP in Saligrama | metric-variety | OK → OK |
| 43 | Closing balance arrears for Public Lighting Grampanchayath in Arkalgud | metric-variety | OK → OK |
| 44 | commercial lighting in ulb and cc for holenarsipura | typo-tolerance | OK → OK |
| 45 | Show the year-on-year demand trend for LT-3(i) in Holenarasipura from FY2015-16 to FY2025-26 | time-trend | FAIL → OK |
| 46 | What was the collection efficiency for HT-4 residential apartments in March 2016? | time-trend | OK → OK *(correct refusal both times — no monthly data)* |
| 47 | Total connected load in KW across all LT categories for Konanur section | aggregation-ranking | OK → OK |
| 48 | Which of the five sub-divisions has the lowest percentage collection efficiency with adjustment? | aggregation-ranking | OK → OK |
| 49 | Sum of HT category collections across all five sub-divisions | aggregation-ranking | OK → OK |

**26/49 → 47/49.**

---

## 6. Remaining risks / recommendations

**Still broken — two-way tariff-category comparison (#16, #30).** Both failures share a shape: the question asks to compare/substitute between two specific tariff categories, and the model refuses even when one or both categories are correctly grounded. This is distinct from Root Causes A-D and wasn't addressed by any of this session's fixes. `example_queries.sql` already has one worked comparison example (#5, domestic LT-1 vs industrial HT-2 demand, at the *family-code* level with a `SUM(CASE WHEN...)` pattern) — the model generalizes that pattern fine for family-level and location-level comparisons (#38 passes reliably), just not for slab/description-level ones. Recommended next step: add a worked example demonstrating the same `SUM(CASE WHEN description = 'A' ...)` pattern at the *description* level, and add explicit prompt guidance that "X instead of Y" phrasing (a substitution) is different from "X vs Y" phrasing (a genuine two-way comparison) — the former should just answer for X.

**Thin coverage.** Every LT/HT family got at least one question, but several rare corners have zero direct coverage: the `AUXILIARY CONSUMPTION` / `HT IPP` / `LT IPP` "special accounting categories" the semantic layer explicitly flags as near-zero-installation and worth treating with caution; the FY2025-26-only P&G Surcharge columns; and the pre-/post-FY2018-19 DC/MNR installation-column split (covered by this app's automated `tests/double_count_guard_test.py` and worked example #8, but not by this 49-question set).

**Data-quality issues in the source data itself** (not app bugs, but real landmines for future questions outside this test set):
- **`LT-5(a)` and `LT-5(b)` share byte-for-byte identical description text** for every HP-band slab. Description-only matching can never tell them apart; only the `tariff_code` column can. This is exactly what surfaced Root Cause A.
- **`LT-6` is reused across two semantically unrelated row groups** — "Water Supply (CMC/TMC/TP or Grampanchayath)" and "Public Lighting" — same code, different business meaning. `#21`/`#22` in this test set cover both sides and both already passed even before this session's fixes.
- **The same description text repeats under genuinely different codes** beyond LT-5(a)/(b) — e.g. `"Ltng.,heating & Motive Power , BMA & Municipal Corp ( Free Lighting)"` under both `LT-1` and `LT-2(a)(i)(FL)`; `"Comme. Ltng ,heating & Motive Power , in ULB's & CC"` under both `LT-3(i)` and `LT-3 a (i)`; `"Power supply on permanent connection Basses..."` under both `LT-3 b` and `LT-7 b`. The app's exact-match and fuzzy-match grounding both surface every matching row rather than silently picking one, which is the safe behavior, but a question naming only one such description is inherently ambiguous about which code it means unless the code itself is also stated.
- **At least one section row (`section number 8`) has no name in the source data at all** (`#40` in this set exercises it) — the app currently refuses gracefully rather than guessing, which is correct, but it's worth knowing that section-level questions for that row can never resolve by name.
- **Several descriptions carry typos from the original spreadsheet**, now baked into the database: `Univercity`, `Chartable`, `Less then one week`, `Basses` (for "Basis"). These are now specifically synonym-mapped in the fuzzy matcher (§4.1, Root Cause B), but any *new* abbreviation or typo pattern introduced by a future data refresh won't be covered until it's found and added the same way — this is a hand-maintained list, not a general typo-correction system.
- **Case-variant duplicate codes** exist too (`LT-6 C` vs `LT-6 c` as distinct rows) — not exercised by this test set, but the same category of risk as the `HT - 3b` spacing quirk this session already fixed for.

**Non-determinism is inherent, not a bug to chase to zero.** Every answer comes from one live, unseeded Gemini call. §4.2's #16/#30 findings were confirmed by re-sampling 3× each specifically to distinguish "consistently broken" from "occasionally unlucky" — that distinction matters more than a single pass/fail run ever can, and is worth keeping in mind for any future regression pass over this app.
