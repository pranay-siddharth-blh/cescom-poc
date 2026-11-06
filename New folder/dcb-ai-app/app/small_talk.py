"""
Small talk and abusive words (the first step of app.pipeline, before any screen, route or model call). Ported from the
RDPR POC (app/small_talk.py, read-only reference), with the DCB assistant's own wording and place names.

  kind(question)        -> "greeting" | "how_are_you" | "thanks" | "goodbye" | "ack" | "abuse" | None
                           only when the WHOLE message is small talk ("hello", "thank you", "ok great, bye",
                           "you are useless, idiot"); a message that also asks something ("hi, what was the collection
                           efficiency in Arkalgud?") is None and goes through the normal screens and routes.
  REPLIES[kind]         -> the fixed reply shown (bold headline, then one line pointing back to the data).
  strip_abuse(question) -> the question with abusive words taken out, so a real question asked rudely ("what's the
                           bloody demand of Konanur, idiot?") is answered normally and none of those words reaches the
                           model, the answer or the report.

Every reply is friendly: abuse gets the same calm greeting as "hello", never a rebuke and never an echo of the words.
A word that is part of a sub-division name is never treated as abuse. Deterministic, no model call.
"""
from __future__ import annotations

import re
from functools import lru_cache

_EXAMPLE = '"What was the collection efficiency in Arkalgud in 2024-25?"'
_OFFER = ("I can give you demand, collection, arrears and installation figures for Holenarasipura Division, each "
          f"sub-division and each tariff category, FY 2015-16 to 2025-26. For example: {_EXAMPLE}")

REPLIES = {
    "greeting": f"**Hello! I'm the CESC DCB assistant.** {_OFFER}",
    "how_are_you": f"**I'm doing well, thank you for asking!** {_OFFER}",
    "thanks": "**You're welcome! Glad to help.** Ask me anything else about demand, collection or arrears whenever you "
              "need it.",
    "goodbye": "**Goodbye, and thank you!** I'm here whenever you need the DCB figures.",
    "ack": "**Glad to help!** Ask me anything else about demand, collection or arrears whenever you need it.",
    # abuse: the same calm greeting, nothing about the words used
    "abuse": f"**Hello! I'm here to help.** {_OFFER}",
}

# What voice mode says for each kind (short, said aloud; app/spoken.py).
_SPOKEN_OFFER = "Ask me about demand, collection, arrears or installations for the division, a sub-division or a tariff category."
SPOKEN = {
    "greeting": "Hello! I'm the CESC DCB assistant. " + _SPOKEN_OFFER,
    "how_are_you": "I'm doing well, thank you for asking! What would you like to know about the DCB figures?",
    "thanks": "You're welcome! Glad to help.",
    "goodbye": "Goodbye, and thank you!",
    "ack": "Glad to help! Ask me anything else whenever you need it.",
    "abuse": "Hello! I'm here to help. " + _SPOKEN_OFFER,
}

# Whole-phrase small talk, by kind (lower case, punctuation removed). Longest phrases are matched first.
_PHRASES = {
    "how_are_you": ["how are you doing", "how are you", "how r u", "how are u", "how is it going", "hows it going",
                    "how do you do", "what's up", "whats up", "what is up", "wassup", "sup"],
    "greeting": ["good morning", "good afternoon", "good evening", "good day", "hello there", "hi there", "hey there",
                 "namaskara", "namaskaragalu", "namaskaram", "namaskar", "namaste", "greetings", "hello", "hallo",
                 "hiya", "hii", "hi", "hey", "yo"],
    "thanks": ["thank you very much", "thank you so much", "thanks a lot", "thanks so much", "many thanks", "thank you",
               "thankyou", "thanks", "thank u", "thanx", "thx", "ty", "dhanyavadagalu", "dhanyavada", "dhanyavaad",
               "shukriya", "much appreciated", "appreciate it"],
    "goodbye": ["good night", "goodnight", "see you later", "see you", "see ya", "bye bye", "goodbye", "good bye", "bye",
                "take care", "that is all", "thats all", "that's all", "nothing else", "i am done", "im done"],
    "ack": ["got it", "understood", "very good", "well done", "good job", "great job", "nice work", "alright",
            "all right", "okay", "okk", "ok", "fine", "great", "nice", "cool", "good", "awesome", "perfect", "excellent",
            "super", "wonderful", "sure", "noted", "wow", "hmm", "right", "yes", "no problem"],
}
# Abusive words and phrases (English, and the Kannada / Hindi ones officers might type in Roman letters).
_ABUSE = ["shut up", "screw you", "go to hell", "hate you", "f off", "fuck off", "piss off", "get lost", "dumbass",
          "dumb ass", "son of a bitch", "idiot", "idiots", "idiotic", "stupid", "dumb", "fool", "foolish", "moron",
          "morons", "useless", "nonsense", "rubbish", "bullshit", "bull shit", "shit", "shitty", "crap", "crappy", "damn",
          "damned", "dammit", "bloody", "fuck", "fucking", "fucked", "wtf", "stfu", "bastard", "bitch",
          "asshole", "arsehole", "ass", "dick", "suck", "sucks", "loser", "pathetic", "worthless", "retard", "retarded",
          "jerk", "scumbag", "saala", "saale", "kutta", "kutte", "kamina", "kameena", "chutiya", "chutiye", "bhosdike",
          "madarchod", "behenchod", "gandu", "harami", "haramkhor", "bevarsi", "bolimaga", "boli maga", "nayi"]
# words that may surround small talk or abuse without making it a question
_FILLER = {"you", "u", "ur", "your", "youre", "you're", "are", "r", "a", "an", "the", "so", "very", "such", "this",
           "that", "is", "it", "its", "it's", "thing", "bot", "assistant", "chatbot", "machine", "system", "app", "sir",
           "madam", "mam", "maam", "ji", "anna", "akka", "guru", "and", "again", "also", "really", "too", "just", "all",
           "everyone", "friend", "dear", "there", "for", "help", "me", "my", "i", "am", "be", "please",
           "what", "kind", "of", "much", "lot", "totally", "complete", "completely", "absolute", "absolutely",
           "oh", "ah", "aah", "haha", "lol", "man", "bro", "boss"}

_KIND_ORDER = ["abuse", "thanks", "goodbye", "how_are_you", "greeting", "ack"]


def _norm(text: str) -> str:
    s = (text or "").lower().replace("’", "'")
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return " ".join(s.split())


@lru_cache(maxsize=1)
def _place_words() -> frozenset:
    """Every word of every sub-division name and alias: never abuse, never small talk."""
    from app.router import _SUB_ALIASES
    words = set()
    for name, aliases in _SUB_ALIASES.items():
        for n in [name, *aliases]:
            words.update(n.lower().replace("_", " ").split())
    return frozenset(words)


def _phrase_re(phrase: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9'])" + re.escape(phrase) + r"(?![a-z0-9'])")


@lru_cache(maxsize=1)
def _abuse_res() -> list[re.Pattern]:
    places = _place_words()
    return [_phrase_re(p) for p in sorted(_ABUSE, key=len, reverse=True) if not (" " not in p and p in places)]


def kind(question: str) -> str | None:
    """The kind of small talk when the whole message is small talk (or abuse), else None."""
    q = _norm(question)
    if not q or len(q.split()) > 12:
        return None
    found: set[str] = set()
    rest = q
    for rx in _abuse_res():
        if rx.search(rest):
            found.add("abuse")
            rest = rx.sub(" ", rest)
    places = _place_words()
    for k, phrases in _PHRASES.items():
        for p in sorted(phrases, key=len, reverse=True):
            rx = _phrase_re(p)
            if rx.search(rest) and not (" " not in p and p in places):
                found.add(k)
                rest = rx.sub(" ", rest)
    leftover = [w for w in rest.split() if w not in _FILLER]
    if leftover or not found:
        return None
    return next(k for k in _KIND_ORDER if k in found)


def strip_abuse(question: str) -> str:
    """The question without its abusive words (and the commas or "you" left dangling around them)."""
    text = question or ""
    low = text.lower().replace("’", "'")
    spans = []
    for rx in _abuse_res():
        spans += [m.span() for m in rx.finditer(low)]
    if not spans:
        return text
    keep = [True] * len(text)
    for a, b in spans:
        for i in range(a, b):
            keep[i] = False
    out = "".join(ch for ch, k in zip(text, keep) if k)
    out = re.sub(r"\b(?:you|u)(?:\s+(?:are|r))?(?:\s+(?:a|an|such a|so|very))?\s+(?:bot|assistant|chatbot|machine|app|thing)\b",
                 "", out, flags=re.I)                                     # "... you stupid bot" -> "..."
    out = re.sub(r"\b(?:you|u)\s*(?=[,.!?]|$)", "", out, flags=re.I)        # "..., idiot you?" -> "...?"
    out = re.sub(r"\s+([,.!?])", r"\1", out)
    out = re.sub(r",+\s*([?.!])", r"\1", out)                               # "2024-25,?" -> "2024-25?"
    out = re.sub(r"([,.!?])\s*[,.]+", r"\1", out)
    out = re.sub(r"^[\s,.!-]+|[\s,-]+$", "", out)
    return " ".join(out.split())
