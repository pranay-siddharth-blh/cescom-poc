"""
Voice guardrails. Everything here sits around the unchanged question pipeline (ported from the KPCL voice instance,
which built them on the Meghalaya voice agent, as tuned in the RDPR POC, and adapted to the DCB data's sub-divisions,
tariff codes, fiscal years and units):

  transcript checks   language (English / Kannada script only), length (seconds of audio, characters of text),
                      recordings without real speech (fan, typing, a cough, a chime), fragments, repeats (the same
                      question twice within a few seconds from one session), the misheard-names list
                      (voice_lexicon.json, validated), and the "Did you mean ...?" confirmation for anything the clean-up
                      was unsure about (a loose name match, a repaired year, a single ambiguous year)
  spoken answer rules read back the place and period first; ask again (with what was heard) instead of a flat refusal;
                      speak a model-written answer only if the second check passed; every spoken figure must be a screen
                      figure and carry its unit, or the voice says "the figures are on your screen" instead
  health              the speech services, checked at most every two minutes
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import struct
import time
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

import httpx

from app.config import AI_DIR, settings

_log = logging.getLogger(__name__)
LEXICON_PATH = Path(__file__).resolve().parent / "voice_lexicon.json"
MISHEARINGS_LOG = AI_DIR / "logs" / "voice_mishearings.jsonl"


# ================================================================================================ misheard names
@lru_cache(maxsize=1)
def _place_words() -> set[str]:
    """Every word of every sub-division name / alias (what a lexicon entry may mean, and may never be)."""
    from app.router import _SUB_ALIASES
    return {w for name, aliases in _SUB_ALIASES.items() if name != "Division_Office" for a in [name.lower(), *aliases]
            for w in re.findall(r"[a-z]+", a.lower())}


@lru_cache(maxsize=1)
def lexicon() -> dict:
    """{'mishearings': {heard: means}, 'confusable': [...], 'rejected': [(entry, why)]} -- invalid entries are dropped
    (and listed), so a careless addition can never turn a real place name into another one."""
    from app.spoken import _ORDINARY, _name_vocab, _phon
    raw = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    vocab = _name_vocab()                              # sound key -> name word the data knows
    known = _place_words() - _ORDINARY
    out, rejected = {}, []
    for e in raw.get("mishearings", []):
        heard, means = str(e.get("heard", "")).strip().lower(), str(e.get("means", "")).strip()
        why = None
        if not re.fullmatch(r"[a-z]{3,30}", heard):
            why = "heard must be one word of letters"
        elif heard in known or _phon(heard) in vocab:
            why = "heard is (or sounds exactly like) a real sub-division name"
        elif heard in _ORDINARY:
            why = "heard is an ordinary question word"
        elif not means or means.split()[0].lower() not in _place_words():
            why = "means is not a name the data knows"
        elif not e.get("reviewed_by"):
            why = "not reviewed"
        if why:
            rejected.append((e, why))
            _log.warning("voice_lexicon.json: ignoring %r -> %r: %s", heard, means, why)
            continue
        out[heard] = means
    confusable = [c for c in raw.get("confusable", []) if c.get("reviewed_by")]
    return {"mishearings": out, "confusable": confusable, "rejected": rejected}


def record_mishearing(entry: dict) -> None:
    """Text only (never audio), and only when VOICE_COLLECT_MISHEARINGS is on (with the officers' consent)."""
    if not settings.voice_collect_mishearings:
        return
    try:
        MISHEARINGS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with MISHEARINGS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}, ensure_ascii=False) + "\n")
    except OSError as exc:
        _log.warning("could not record a mishearing: %s", exc)


# ================================================================================================ transcript checks
def wav_seconds(audio: bytes) -> float | None:
    """Duration of a PCM WAV from its header (None if it isn't one)."""
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return None
    pos, rate, block = 12, None, None
    while pos + 8 <= len(audio):
        cid, size = audio[pos:pos + 4], struct.unpack("<I", audio[pos + 4:pos + 8])[0]
        if cid == b"fmt " and pos + 24 <= len(audio):
            _fmt, _ch, rate, _bps, block = struct.unpack("<HHIIH", audio[pos + 8:pos + 22])
        elif cid == b"data":
            n = min(size, len(audio) - pos - 8)
            return n / (rate * block) if rate and block else None
        pos += 8 + size + (size & 1)
    return None


_OTHER_SCRIPT = re.compile(r"[^\x00-\u024f\u0c80-\u0cff\u2000-\u206f\u20b9\s\d.,'\u2019\"?!:;()\-\u2013/%&]")


def voiced_seconds(audio: bytes) -> float | None:
    """Seconds of a 16-bit PCM WAV clearly louder than its own background (20 ms frames over 2.5x the quietest tenth,
    and over an absolute floor). Office noise, a fan, a chime or a key press stays well under 0.35 s; a spoken question
    is over a second. None if the body is not a PCM WAV (then the speech model decides alone)."""
    try:
        import wave
        import numpy as np
        with wave.open(io.BytesIO(audio)) as w:
            if w.getsampwidth() != 2:
                return None
            rate, ch, raw = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
        x = np.frombuffer(raw[: len(raw) // (2 * ch) * 2 * ch], dtype="<i2").astype(np.float32) / 32768.0
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)
        n = max(1, int(rate * 0.02))
        frames = x[: len(x) // n * n].reshape(-1, n)
        if not len(frames):
            return 0.0
        rms = np.sqrt((frames ** 2).mean(axis=1))
        floor = float(np.percentile(rms, 10))
        return float((rms > max(2.5 * floor, 0.006)).sum() * n / rate)
    except Exception:  # noqa: BLE001
        return None


MIN_VOICED_SECONDS = 0.35


def too_short(cleaned: str, voiced: float | None, context_words: set[str]) -> bool:
    """A transcript that is not a question: one word ("GP", "Mysuru"), or two or three words that are all from the
    speech model's own hint paragraph out of under a second of speech -- what the model writes for noise."""
    words = re.findall(r"[a-z0-9']+", cleaned.lower())
    if len(words) <= 1:
        return True
    return len(words) <= 3 and voiced is not None and voiced < 1.0 and all(w in context_words for w in words)


_DEVA_CONS = dict(zip("कखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसहळ",
                      "k kh g gh n ch chh j jh n t th d dh n t th d dh n p ph b bh m y r l v sh sh s h l".split()))
_DEVA_VOWEL = dict(zip("अआइईउऊएऐओऔऋ", "a aa i ee u oo e ai o au ri".split()))
_DEVA_MATRA = dict(zip("ािीुूेैोौृंँः", "aa i ee u oo e ai o au ri n n h".split()))


def latinise(text: str) -> str:
    """A short transcript the speech model wrote partly in Devanagari although the officer spoke English and a
    Kannada place name ("and Konanur?" -> "And कौन अनुर?", heard in the spoken follow-up test): the Devanagari words are
    spelt out in Latin letters ("And kaun anur?") so the name matching can find the place. A longer transcript in
    another language is left as it is, for the language check to refuse."""
    words = re.findall(r"[ऀ-ॿ]+", text or "")
    if not words or len(words) > 3:
        return text

    def word(w: str) -> str:
        out, i = [], 0
        while i < len(w):
            c = w[i]
            if c in _DEVA_CONS:
                nxt = w[i + 1] if i + 1 < len(w) else ""
                if nxt in _DEVA_MATRA:
                    out.append(_DEVA_CONS[c] + _DEVA_MATRA[nxt]); i += 2; continue
                if nxt == "्":
                    out.append(_DEVA_CONS[c]); i += 2; continue
                out.append(_DEVA_CONS[c] + ("a" if i + 1 < len(w) else ""))   # the final inherent vowel is not said
            elif c in _DEVA_VOWEL or c in _DEVA_MATRA:
                out.append(_DEVA_VOWEL.get(c) or _DEVA_MATRA[c])
            i += 1
        return "".join(out)
    return re.sub(r"[ऀ-ॿ]+", lambda m: word(m.group(0)), text)


def language_ok(text: str) -> bool:
    """English (Latin script) or Kannada script. A transcript mostly in another script (the model hearing noise as
    Chinese, or a question in another language) is dropped and the officer told so."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    other = len(_OTHER_SCRIPT.findall("".join(letters)))
    return other / len(letters) < 0.3


class RepeatGuard:
    """The same question from the same session within `window` seconds is a double-send or an echo: answer once."""

    def __init__(self, window: float = 4.0, max_sessions: int = 5000):
        self.window, self.max = window, max_sessions
        self.last: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def seen(self, session: str, text: str) -> bool:
        key = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        now = time.monotonic()
        prev = self.last.get(session)
        self.last[session] = (key, now)
        self.last.move_to_end(session)
        while len(self.last) > self.max:
            self.last.popitem(last=False)
        return bool(key) and prev is not None and prev[0] == key and now - prev[1] < self.window


repeats = RepeatGuard()


# ================================================================================================ "Did you mean ...?"
def place_label(word: str) -> str:
    """A sub-division name -> how to say it in a question ("Arkalgud sub-division", "Konanur section")."""
    from app.spoken import _names, place_kind
    name = _names().get(word.lower().replace(" ", ""))
    return f"{name} {place_kind(name)}" if name else word


def _option_label(kind: str, opt: str) -> str:
    if kind == "name":
        return place_label(opt)
    if kind == "year":
        return opt if opt.upper().startswith("FY") else f"FY {opt}"
    if kind == "tariff":
        return opt if "-" in opt else f"all of {opt}"
    return opt


def confirmation(cleaned: str, notes: list[dict]) -> dict | None:
    """The one question to ask before answering, from the clean-up's notes: {'prompt', 'reason', 'options': [{'label',
    'question'}]}, the most likely option first. None when nothing was uncertain."""
    for n in notes:
        span = n.get("span", "")
        if not span or not re.search(rf"(?<![\w-]){re.escape(span)}(?![\w-])", cleaned, re.I):
            continue
        options, seen = [], set()
        for opt in n["options"]:
            label = _option_label(n["kind"], opt)
            if label.lower() in seen:
                continue
            seen.add(label.lower())
            q = cleaned if opt.lower() == span.lower() else re.sub(rf"(?<![\w-]){re.escape(span)}(?![\w-])", opt, cleaned,
                                                                   count=1, flags=re.I)
            options.append({"label": label, "question": q})
        if len(options) >= 2:
            return {"prompt": f"Did you mean {options[0]['label']}, or {options[1]['label']}?", "reason": n["kind"],
                    "options": options[:2]}
    return None


# ================================================================================================ spoken answer rules
def readback(question: str, answer: str, spoken: str = "") -> str:
    """'For LT-4, Arkalgud sub-division, 2024-25: ' -- the tariff, place and period the answer is about, said first so a
    misheard name, code or year is caught by ear. Empty when the question names none of them, or when the spoken answer
    already says them all."""
    from app import router
    from app.spoken import place_kind
    e = router.extract(question)
    codes = list(e.get("slab_codes") or []) + [f for f in (e.get("families") or []) if f != "MISC"]
    places = [f"{n} {place_kind(n)}" for n in (e.get("subdivisions") or [])[:2] if n != "Division_Office"]
    if not places and e.get("scope") == "division":
        places.append("Holenarasipura Division")
    fys = e.get("fiscal_years") or []
    period = f"{min(fys)} to {max(fys)}" if len(fys) > 2 else " and ".join(fys)
    if not period:
        m = re.search(r"No (?:financial |fiscal )?year was named, so (?:the latest year in the data, )?(?:FY )?(\d{4}-\d{2})",
                      answer or "")
        if m:
            period = f"{m.group(1)}, the latest year"
    if not places and not period and not codes:
        return ""
    # Already said: the headline names the tariff, every place and the period ("Demand of LT-4 for Arkalgud
    # sub-division in 2024-25 was ..."), so a read-back would only repeat it.
    said = (spoken or "").lower()
    places_said = all(p.split(" ")[0].lower() in said for p in places)
    codes_said = all(c.lower() in said for c in codes[:2])
    periods_said = all(p.split(",")[0].strip().lower() in said for p in re.split(r" and | to ", period)) if period else True
    if said and places_said and periods_said and codes_said:
        return ""
    parts = [" and ".join(codes[:2])] if codes else []
    parts += [" and ".join(places)] if places else []
    parts += [period] if period else []
    return "For " + ", ".join(parts) + ": "


ASK_AGAIN_ROUTES = ("unknown_name", "llm_unverified", "llm_sql_failed", "llm_declined", "llm_mismatch",
                    "refused:off_topic", "refused:empty", "refused:not_in_data")


def ask_again(question: str, route: str) -> str | None:
    """Instead of a flat refusal: say what was heard and ask once more."""
    if not route.startswith(ASK_AGAIN_ROUTES):
        return None
    heard = re.sub(r"\s+", " ", question).strip().rstrip("?.!")[:160]
    if route.startswith("unknown_name"):
        from app import router
        e = router.extract(question)
        what = ("that tariff category" if e.get("unknown_tariffs") and not e.get("unknown_subdivisions") else
                "that sub-division" if e.get("unknown_subdivisions") else "that name")
        return f"I heard: {heard}. I don't recognise {what}. Could you say it again?"
    if route.startswith("refused:"):
        return f"I heard: {heard}. That doesn't sound like a question about the DCB figures. Could you say it again?"
    return (f"I heard: {heard}. I couldn't confirm an answer to that. Could you say it again, with the sub-division, "
            "the tariff category or the year?")


MODEL_UNCHECKED = "The answer is on your screen. I couldn't double-check it, so I won't read it out; please check it there."
FIGURES_ON_SCREEN = "The figures are on your screen."

_SPOKEN_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3}|\d+)(?:\.(\d+))?\s*(crore|lakh)?")
_DATEY = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b|\b(?:FY ?)?(?:19|20)\d{2}(?:-\d{2})?\b|\b\d{1,2} (?:January|February|March|April|"
                    r"May|June|July|August|September|October|November|December)\b|\b(?:January|February|March|April|May|June|"
                    r"July|August|September|October|November|December) \d{1,2}\b|\b\d{1,2}-[A-Za-z]{3}-\d{4}\b|\bQ[1-4]\b|"
                    r"\b[LH]T\s?-\s?\d{1,2}(?:\s?\([A-Za-z]{1,4}\)|\s[a-c](?=\s?\())*|\bpage \d+\b|\btop \d+\b|"
                    r"\b6[12]\.\d{4}\b")
# what a figure's unit may be, right after it
_UNIT_AFTER = re.compile(
    r"^\s*(?:percent|per cent|percentage points|points|crore|lakh|thousand|rupees|paise|%|installations?|consumers?|"
    r"connections?|units?|kWh|kilowatt hours?|MU|HP|kVA|kW|sub-divisions?|sections?|categories|category|slabs?|"
    r"families|years?|months?|times|meters?|of\s+[\d,]+|out of|per unit)\b", re.I)
# What a whole number can count; named earlier in the sentence, it names the figure ("GPs that closed ... was 20").
_COUNTED = re.compile(r"\b(?:installations?|consumers?|connections?|meters?|sub-divisions?|categories|slabs?|count|number|"
                      r"MNR|DC)\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _values(text: str, scaled: bool) -> list[float]:
    out = []
    for w, f, scale in _SPOKEN_NUM.findall(_DATEY.sub(" ", text)):
        v = float(w.replace(",", "") + ("." + f if f else ""))
        out.append(v * ({"crore": 1e7, "lakh": 1e5}.get(scale, 1) if scaled else 1))
    return out


def figures_match_screen(spoken: str, answer: str) -> bool:
    """Every figure the voice says is a figure on the screen (rounded as said; lakh / crore compared both ways)."""
    screen = _values(answer, scaled=False) + _values(answer, scaled=True)
    return all(any(abs(v - x) <= max(0.0051 * abs(x), 0.5) for x in screen) for v in _values(spoken, scaled=True))


def _masked(text: str) -> str:
    """Dates, years and the like blanked out (same length, so positions still line up)."""
    return _DATEY.sub(lambda m: " " * len(m.group()), text)


def _unit_follows(masked: str, m: re.Match) -> bool:
    tail = masked[m.end():m.end() + 60]
    if m.group(3):
        return True
    tail = re.sub(r"^\s*(?:about|nearly|around|over|under|more than|less than|of the|of|the)\s+", " ", tail)
    return bool(_UNIT_AFTER.match(tail) or re.match(r"^\s*(?:[\w,.'-]+\s+){0,3}(?:installations|consumers|connections|"
                                                     r"units|meters|sub-divisions|categories|slabs|months|years)\b",
                                                     tail, re.I))


def add_units(spoken: str) -> str:
    """No DCB measure is kept in an unstated unit (rupees and percent are always written), so nothing is added;
    kept as the hook the voice pipeline calls, like the KPCL instance's heat-rate / frequency rule."""
    return spoken


def figures_have_units(spoken: str) -> bool:
    """No figure the listener can't place: each has its unit after it, or is a whole-number count of something the same
    sentence, or the sentence just before it, has already named ('MNR installations ... were 7. Down 53.3 percent from
    15 in 2023-24.'). A bare decimal never passes."""
    named_before = False
    for sent in _SENTENCE.split(spoken):
        masked = _masked(sent)
        for m in _SPOKEN_NUM.finditer(masked):
            if _unit_follows(masked, m):
                continue
            if not m.group(2) and (named_before or _COUNTED.search(sent[:m.start()])):
                continue
            if re.search(r"\bratio\b", sent[:m.start()], re.I):
                continue
            return False
        named_before = bool(_COUNTED.search(sent))
    return True


# ================================================================================================ health
_health: dict = {"at": 0.0, "value": None}
_health_lock = asyncio.Lock()


async def speech_health(force: bool = False) -> dict:
    """{'speech_in': {'ok', 'detail'}, 'speech_out': {...}, 'checked': iso}. Speech in: the model list (no audio, no GPU
    work); speech out: one short word, first audio only. Cached for two minutes."""
    async with _health_lock:
        if not force and _health["value"] and time.monotonic() - _health["at"] < 120:
            return _health["value"]

        async def probe(enabled: bool, base: str, key: str, model: str) -> dict:
            if not enabled:
                return {"ok": False, "detail": "not configured"}
            try:
                async with httpx.AsyncClient(verify=settings.ai_model_ca_bundle_path or False, timeout=6) as c:
                    r = await c.get(f"{base.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"})
                if r.status_code != 200:
                    return {"ok": False, "detail": f"gateway answered {r.status_code}"}
                ids = [m.get("id") for m in (r.json().get("data") or []) if isinstance(m, dict)]
                return {"ok": True, "detail": "ready" if model in ids or not ids else f"{model} not listed; gateway reachable"}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "detail": f"unreachable ({type(exc).__name__})"}

        async def probe_tts() -> dict:
            """The voice key may not list models, so ask for one short word and stop at the first audio."""
            if not settings.tts_configured:
                return {"ok": False, "detail": "not configured"}
            from app import voice
            stream = voice.stream_speech("OK.")
            try:
                await asyncio.wait_for(stream.__anext__(), timeout=10)
                return {"ok": True, "detail": "ready"}
            except voice.VoiceBusy:
                return {"ok": True, "detail": "busy"}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "detail": f"unavailable ({type(exc).__name__})"}
            finally:
                try:
                    await stream.aclose()
                except Exception:  # noqa: BLE001
                    pass

        speech_in, speech_out = await asyncio.gather(
            probe(settings.asr_configured, settings.asr_base_url, settings.asr_api_key, settings.asr_model), probe_tts())
        value = {"speech_in": speech_in, "speech_out": speech_out, "checked": time.strftime("%Y-%m-%dT%H:%M:%S")}
        _health.update(at=time.monotonic(), value=value)
        return value
