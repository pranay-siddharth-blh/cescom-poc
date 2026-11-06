"""
Guardrails around the chatbot (pattern ported from the KPCL POC, CESCOM DCB vocabulary):

  1. screen_question  -- before any routing: empty / too long, pasted SQL or code, prompt injection, requests to
     change data, harmful requests, personal data, questions about the system itself, off-topic questions.
  2. REFUSALS         -- fixed, reviewed wording for every refusal.
  3. scrub            -- drops any sentence of model-written text that names an internal object (table or column
     names, SQLite, the model) after rewriting known column names into plain words.
  4. safe_refusal     -- the model's own "can't answer" text is shown only if every sentence is about the data.
  5. result checks    -- implausible_result (a figure that cannot exist) and subject_mismatch ("how many
     installations" answered by SQL that never touches installations).
The database-level guard (read-only files, authorizer) is in app.db; SQL checks are in app.sql_safety.
"""
from __future__ import annotations

import re

MAX_QUESTION_CHARS = 500

EXAMPLE = "\"What was the collection efficiency in Arkalgud in 2024-25?\""
REFUSALS = {
    "empty": "Please type a question about Holenarasipura Division's DCB figures.",
    "too_long": f"That question is too long. Please keep it under {MAX_QUESTION_CHARS} characters and ask one thing at a time.",
    "system": ("I can only answer questions about Holenarasipura Division's demand, collection and balance figures, not "
               "about how this assistant is built. Try asking about demand, collection, arrears, installations or a tariff."),
    "injection": "I can't follow instructions like that. I can answer questions about Holenarasipura Division's DCB figures.",
    "code": f"I can't run queries or code typed into the chat. Please ask in plain words, for example: {EXAMPLE}",
    "off_topic": ("I can only help with Holenarasipura Division's Demand-Collection-Balance data: demand billed, "
                  "collection, collection efficiency, arrears, installations, consumption and tariff categories, by "
                  f"sub-division and fiscal year (2015-16 to 2025-26). For example: {EXAMPLE}"),
    "not_in_data": ("I can't answer that reliably from the data available. Try rephrasing, or name the sub-division, "
                    "tariff category, measure and fiscal year you mean."),
    "mismatch": "I couldn't find a reliable answer to that exact question. Please rephrase it, naming the sub-division, tariff or year you mean.",
    "llm_unavailable": ("I can answer that kind of question only with the language model, which isn't reachable right now "
                        "(it runs on the internal gateway, so the VPN must be connected). Single figures such as "
                        f"{EXAMPLE}, definitions and the standard summaries still work."),
    "error": "Sorry, I couldn't answer that right now. Please try again in a moment.",
    "read_only": ("I can't change, add or delete any data: this assistant only reads the DCB statement. Corrections "
                  "have to be made in the billing system itself."),
    "harmful": ("I can't help with anything that could damage equipment, endanger people or tamper with meters or "
                "billing. I can answer questions about Holenarasipura Division's DCB figures."),
    "personal_data": ("I can't share details about individual people, such as names, phone numbers, addresses or account "
                      "details. This data holds no personal records; it covers totals per sub-division and tariff."),
    "unverified": ("I couldn't confirm a reliable answer to that: a second check of the figures did not agree with the "
                   "question. Please rephrase it, naming the sub-division, tariff, measure and fiscal year you mean."),
    "rate_limited": "Too many questions in a short time. Please wait a moment and try again.",
    "busy": "The assistant is busy answering other questions. Please try again in a moment.",
}

# ---------------------------------------------------------------------------------------------- 1. screening
_SQL_OR_CODE_RE = re.compile(
    r"(\bselect\b[\s\S]{0,200}\bfrom\b|\b(insert\s+into|delete\s+from|drop\s+(table|view|database)|update\s+\w+\s+set|"
    r"alter\s+table|create\s+(table|view|index)|truncate\s+table|attach\s+database|pragma\s+\w+|union\s+(all\s+)?select)\b|"
    r";\s*--|/\*|\*/|<\s*script|\bimport\s+os\b|\bexec\s*\(|\beval\s*\(|\bos\.system\b|\bsubprocess\b)", re.IGNORECASE)
_INJECTION_RE = re.compile(
    r"(ignore|disregard|forget|override|bypass)\b[^.?!]{0,40}\b(previous|prior|above|earlier|all|your|the)\b[^.?!]{0,30}"
    r"\b(instructions?|rules?|prompts?|guidelines?|context)|\b(system|developer|hidden|initial)\s+(prompt|message|instructions?)\b|"
    r"\b(reveal|show|print|repeat|output|display|tell me)\b[^.?!]{0,30}\b(your|the)\s+(prompt|instructions?|rules|configuration|config)\b|"
    r"\byou are now\b|\bact as\b|\bpretend (to be|you)\b|\bjailbreak\b|\bdeveloper mode\b|\bdo anything now\b|\bno restrictions\b",
    re.IGNORECASE)
_SYSTEM_RE = re.compile(
    r"\b(databases?|db|sqlite|sql|schemas?|tables?|columns?|queries|query language|backend|back-end|server|source code|"
    r"codebase|api keys?|endpoint|prompt|llm|language model|gpt|qwen|openai|gemini|claude|vllm|which model|what model|"
    r"file ?paths?|filenames?|python|fastapi|etl|semantic layer|star schema|data ?mart|staging|"
    r"how (are|were) you (built|made|trained)|your (code|instructions|training|architecture))\b", re.IGNORECASE)
_SYSTEM_EXCEPTIONS_RE = re.compile(
    r"\b(as a table|in a table|table of|tabular|in (one|a single|the same|a) table|(one|single|summary|comparison) table|"
    r"table (showing|with|for|by|comparing))\b", re.IGNORECASE)
_DOMAIN_WORDS = (
    r"dcb|demand\w*|collect\w*|balance\w*|arrears?|outstanding|dues|due|billed|billing|bills?|revenue|realis\w*|realiz\w*|"
    r"efficien\w*|recover\w*|shortfall|uncollected|paid|payments?|installations?|connections?|consumers?|customers?|live|"
    r"dc|mnr|dead|disconnect\w*|consumption|units?|kwh|energy|load|kw|hp|kva|tariffs?|slabs?|categor\w*|lt|ht|"
    r"lt-?\d\w*|ht-?\d\w*|ipp|auxiliary|misc\w*|fac|fuel|tax|interest|surcharge|fixed charges?|energy charges?|"
    r"deposit|opening|closing|ob|cb|arr|domestic|bhagya\w*|kutira\w*|irrigation|ip ?sets?|pump\w*|commercial|industr\w*|"
    r"water supply|public lighting|street ?lights?|ev|charging|temporary|apartments?|residential|lift irrigation|"
    r"agricultur\w*|horticultur\w*|cescom|cesc|electricity|power|division\w*|sub-?divisions?|section|holenarasipura|"
    r"hn ?pura|arkalgud|arakalagud|hangarahalli|ramanathapura|saligrama|konanur|fy|financial year|fiscal year|years?|"
    r"annual|yearly|trend|growth|increase|decrease|change|compare|comparison|versus|vs|highest|lowest|top|bottom|rank\w*|"
    r"total|how much|how many|average|share|breakdown|split|summary|overview|report|dashboard|data|figures?|kpis?|"
    r"perform\w*|numbers?|areas?|pay\w*|pending|percent\w*|rupees?|rs|crores?|lakhs?|status|position|"
    r"l\.\s*t\.?\s*-?\s*\d|h\.\s*t\.?\s*-?\s*\d|(?:19|20)\d\d\s*-\s*\d\d|20\d\d")


_DOMAIN_RE = re.compile(r"\b(" + _DOMAIN_WORDS + r")\b", re.IGNORECASE)
_GENERAL_KNOWLEDGE_RE = re.compile(
    r"\b(capital|chief minister|governor|prime minister|president|minister|elections?|weather|temperature|tourist|"
    r"tourism|places to visit|cricket|football|films?|movies?|actors?|songs?|recipes?|jokes?|poems?|stories|"
    r"stock price|share price|news|horoscope)\b", re.IGNORECASE)
_DATA_TERMS_RE = re.compile(r"\b(demand|collection|arrears|installations?|tariff|lt-?\d|ht-?\d|consumption|dcb|balance)\b", re.I)

# Requests to change data. The database is opened read-only anyway; this gives a clear answer instead of a figure.
_WRITE_RE = re.compile(
    r"^\s*(?:please\s+|kindly\s+|can you\s+|could you\s+|i want you to\s+|go ahead and\s+|now\s+)?"
    r"(update(?!\s+(me|us)\b)|delete|remove|modify|edit|overwrite|insert|alter|erase|wipe|reset|upload|replace|waive|"
    r"write off|write-off|change(?!\s+(in|of|over|from|between)\b)|set(?!\s+of\b)|add(?!\s+up\b)|correct(?!ly)|fix)\b|"
    r"\b(update|overwrite|modify)\b[^.?!]{0,60}\b(to|=|as)\s+-?[\d₹]", re.IGNORECASE)
# Anything that could hurt people, damage equipment or defraud billing.
_HARMFUL_RE = re.compile(
    r"\b(sabotag\w*|bombs?|explosives?|terror\w*|weapons?|poison\w*|arson)\b|"
    r"\b(tamper\w*|bypass\w*|hook(ing)?|steal\w*|theft)\b[^.?!]{0,30}\b(meters?|electricity|power|supply|line)\b|"
    r"\b(meters?|electricity|power)\b[^.?!]{0,20}\b(tamper\w*|bypass\w*|steal\w*|theft)\b|"
    r"\bhow\s+(do|can|could|would|should)\s+(i|we|someone|one)\b[^.?!]{0,50}\b(avoid paying|evade|cheat|fake|forge|hack|"
    r"disable|damage|destroy)\b|\bhow to\b[^.?!]{0,50}\b(avoid paying|evade|cheat|fake|forge|hack|damage|destroy)\b",
    re.IGNORECASE)
# Individual people: this data has none, and the assistant must never guess.
_PERSONAL_DATA_RE = re.compile(
    r"\b(phone|mobile|contact|cell|telephone)\s*(numbers?|nos?|details)\b|\be-?mail (ids?|address\w*)\b|"
    r"\b(names?|address\w*|salar\w+|pay|details|personal (data|details|information))\s+of\s+(the\s+|all\s+)?"
    r"(?:[\w-]+\s+){0,4}?(engineers?|staff|employees?|officers?|linem[ae]n|workers?|people|persons?|managers?|"
    r"consumers?|customers?|defaulters?|households?|individuals?)\b|"
    r"\b(aadhaar|aadhar|pan (card|number)|bank account|home address|date of birth)\b|"
    r"\bwho (are|is) the (biggest |top |largest )?defaulters?\b", re.IGNORECASE)


def is_general_knowledge(question: str) -> bool:
    q = question or ""
    return bool(_GENERAL_KNOWLEDGE_RE.search(q) and not _DATA_TERMS_RE.search(q))


def screen_question(question: str) -> str | None:
    """Return a REFUSALS key if the question must not go further, else None. 'system' and 'off_topic' are
    returned too but applied by the pipeline only after the definition / knowledge routes had their turn."""
    q = (question or "").strip()
    if not q or not re.search(r"[A-Za-zಀ-೿]", q):
        return "empty"
    if len(q) > MAX_QUESTION_CHARS:
        return "too_long"
    if _SQL_OR_CODE_RE.search(q):
        return "code"
    if _INJECTION_RE.search(q):
        return "injection"
    if _HARMFUL_RE.search(q):
        return "harmful"
    if _WRITE_RE.search(q):
        return "read_only"
    if _PERSONAL_DATA_RE.search(q):
        return "personal_data"
    if _SYSTEM_RE.search(q) and not _SYSTEM_EXCEPTIONS_RE.search(q):
        return "system"
    if re.search(r"[ಀ-೿]", q):
        return None   # Kannada text: let it through; the model decides scope
    if not _DOMAIN_RE.search(q):
        return "off_topic"
    if _GENERAL_KNOWLEDGE_RE.search(q) and not _DATA_TERMS_RE.search(q):
        return "off_topic"
    return None


# ---------------------------------------------------------------------------------------------- 3. scrubbing
_JARGON = {
    r"\bcollection_grand_total\b": "total collection", r"\bdemand_total\b": "total demand",
    r"\bclosing_balance_total\b": "closing balance", r"\bopening_balance_total\b": "opening balance",
    r"\btotal_installations\b": "installations", r"\blive_installations\b": "live installations",
    r"\bconsumption_total\b": "consumption", r"\bcollection_efficiency_pct\b": "collection efficiency",
    r"\bsubdivision_name\b": "sub-division", r"\bfiscal_year\b": "fiscal year", r"\btariff_code\b": "tariff code",
    r"\bfamily_code\b": "tariff family", r"\bDivision_Total\b": "the whole division", r"\bDivision_Office\b": "the division office",
}
_INTERNAL_RE = re.compile(
    r"\b(fact|dim|mart|stg|semantic|ledger)_[a-z0-9_]+\b|\bdcb_star_schema\b|\.sqlite\b|\bsqlite\b|\bsql\b|"
    r"\bquer(y|ies)\b|\bdatabases?\b|\btables?\b|\bcolumns?\b|\bschema\b|\bjoin(ed|s)?\b|\bprompt\b|\bllm\b|"
    r"\blanguage model\b|\bplaceholder\b|\brollups?\b|\b[a-z]+_[a-z0-9]+(_[a-z0-9]+)*\b|\b\w+\.(py|json|sql|xlsx?|csv)\b|"
    r"\bstar schema\b|\bstaging\b", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(₹*])")


def dejargon(text: str) -> str:
    for pattern, plain in _JARGON.items():
        text = re.sub(pattern, plain, text, flags=re.IGNORECASE)
    return text


def scrub(text: str) -> str:
    """Rewrite known column names, then drop every sentence that still names an internal object."""
    if not text:
        return ""
    text = re.sub(r"\s*\([^()]*\)", lambda m: "" if _INTERNAL_RE.search(m.group(0)) else m.group(0), dejargon(text))
    paras = []
    for para in text.split("\n\n"):
        lines = []
        for line in para.split("\n"):
            sentences = [s for s in _SENTENCE_SPLIT_RE.split(line) if s.strip()]
            clean = [s for s in sentences if not _INTERNAL_RE.search(s)]
            if clean:
                lines.append(" ".join(clean))
        if lines:
            paras.append("\n".join(lines))
    return "\n\n".join(paras).strip()


_REFUSAL_SENTENCE_RE = re.compile(
    r"\b(data|records?|information|details?|available|contain\w*|cover\w*|includ\w*|only|not|no|cannot|can't|couldn't|"
    r"unable|limited|missing|exist\w*|billed|tariff|categor\w*|codes?|sub-?divisions?|division|section|years?|fiscal|"
    r"period|please|try|ask|rephras\w*|specify|which|reports?|recorded|found|match\w*)\b", re.IGNORECASE)


def safe_refusal(llm_message: str) -> str:
    """The model's reason for not answering, if short and clean and entirely about the data; else a fixed text."""
    raw = dejargon(llm_message or "").strip()
    sentences = [x for x in _SENTENCE_SPLIT_RE.split(raw) if x.strip()]
    if not sentences or any(not _REFUSAL_SENTENCE_RE.search(x) for x in sentences):
        return REFUSALS["not_in_data"]
    if len(raw) <= 400 and not _INTERNAL_RE.search(raw):
        return raw
    return REFUSALS["not_in_data"]


# ---------------------------------------------------------------------------------------------- 5. result checks
_COVERAGE_CLAIM_RE = re.compile(
    r"\b(outside|out of range|beyond|not (include|contain|cover|available|present)\w*|only (contains?|covers?|has|have|"
    r"includes?|holds?|from)|no (historical |such )?(data|records?) (for|before|after)|does(n't| not) (include|contain|"
    r"cover|have|go back))\b[^.]{0,80}\b(20\d\d|fiscal year|year|period|range)", re.IGNORECASE)


def false_coverage_claim(message: str, entities: dict, fiscal_years: list[str]) -> bool:
    """The model declined because a year is 'outside the data' although every year named is in it."""
    if not message or not _COVERAGE_CLAIM_RE.search(message):
        return False
    named = entities.get("fiscal_years") or ([entities["fiscal_year"]] if entities.get("fiscal_year") else [])
    return bool(named) and all(fy in fiscal_years for fy in named)


# A figure that cannot exist: installations, consumption and demand are never negative in this data (collections,
# balances and "uncollected" can be, at slab level, because of adjustments; efficiency can exceed 100%).
_NONNEG_RE = re.compile(r"(installation|consumption|units_billed|^demand_total$|_demand$|^demand$|connected_load)", re.I)
_CHANGE_RE = re.compile(r"(diff|change|growth|delta|yoy|vs|gap|shortfall|variance|minus|increase|decrease|uncollected)", re.I)


def implausible_result(columns: list[str], rows: list[tuple]) -> str | None:
    for i, c in enumerate(columns):
        if _CHANGE_RE.search(c) or not _NONNEG_RE.search(c):
            continue
        vals = [r[i] for r in rows if isinstance(r[i], (int, float)) and not isinstance(r[i], bool)]
        if vals and min(vals) < 0:
            return f"column {c} has a negative value ({min(vals)}), which is impossible for this measure"
    return None


_COUNT_SUBJECT_RE = re.compile(
    r"\b(how many|number of|count of|total number of|no\.? of)\s+(distinct\s+|unique\s+|different\s+)?"
    r"(installations|connections|consumers|sub-?divisions|tariff (codes|categories|slabs)|slabs|years)\b", re.I)
_SUBJECT_SQL = {"installations": r"installation", "connections": r"installation", "consumers": r"installation",
                "subdivisions": r"subdivision", "sub-divisions": r"subdivision", "tariff codes": r"tariff",
                "tariff categories": r"tariff|family", "tariff slabs": r"tariff|slab", "slabs": r"tariff|slab",
                "years": r"fiscal_year"}


def subject_mismatch(question: str, sql: str) -> bool:
    m = _COUNT_SUBJECT_RE.search(question or "")
    if not m:
        return False
    key = m.group(3).lower()
    return not re.search(_SUBJECT_SQL.get(key, re.escape(key)), sql or "", re.IGNORECASE)
