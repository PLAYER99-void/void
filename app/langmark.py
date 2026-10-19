"""Foreign words in what he says. Claude marks a name or title with its language — {fr:Les Jardins de Minuit} (see
VOICE_PROMPT in server.py) — and Kokoro says those words with French phonemes, in the same English voice, in one go.
Captions, history, piper and espeak-ng just get the plain words. Nothing here makes sound or needs the network: the
phonemizer (Kokoro's own, espeak-ng) is passed in.
"""
import re

# {xx:words} or {xx-yy:words}. Braces aren't markdown and the face tags are [square], so neither can clash; code is
# matched first and left as it is, so `{id:5}` in code stays code. An unclosed one at the very end (a reply cut short,
# or still streaming in) runs to the end.
MARK_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)|\{([a-z]{2}(?:-[a-z]{2})?):([^{}\n]+)(?:\}|$)", re.I)

# Claude's language code → espeak-ng's. Any other code is said in English.
ESPEAK = {"fr": "fr-fr", "es": "es", "de": "de", "it": "it", "pt": "pt", "pt-br": "pt-br", "nl": "nl", "sv": "sv",
          "pl": "pl", "ja": "ja"}
FLAG_RE = re.compile(r"\([a-z]{2,3}(?:-[a-z0-9]+)?\)")   # espeak's "(en)…(fr)" around words it thinks are English


def unmark(t):
    """The words without the markup: "{fr:Les Jardins} tonight" → "Les Jardins tonight"."""
    return MARK_RE.sub(lambda m: m.group(1) or m.group(3), t or "")


def lang_of(code):
    code = (code or "").lower()
    return ESPEAK.get(code) or ESPEAK.get(code[:2])


def spans(t):
    """→ [(espeak language, or None for English, words), …] in order, unmarked; empty pieces dropped."""
    out, at, t = [], 0, t or ""
    for m in MARK_RE.finditer(t):
        lang = None if m.group(1) else lang_of(m.group(2))
        if lang:
            out += [(None, unmark(t[at:m.start()])), (lang, m.group(3))]
            at = m.end()
    out.append((None, unmark(t[at:])))
    return [(lang, w) for lang, w in out if w.strip()]


def romaji(t):
    """Japanese in rōmaji → IPA (espeak-ng only reads kana, and spells rōmaji out letter by letter)."""
    t = t.lower()
    for a, b in (("ā", "aː"), ("ī", "iː"), ("ū", "uː"), ("ē", "eː"), ("ō", "oː"), ("ou", "oː"), ("oo", "oː"),
                 ("tch", "tʨ"), ("sh", "ɕ"), ("ch", "ʨ"), ("ts", "ʦ"), ("j", "ʥ"), ("fu", "ɸu"), ("hi", "çi"),
                 ("u", "ɯ"), ("r", "ɾ"), ("y", "j"), ("g", "ɡ")):
        t = t.replace(a, b)
    return re.sub(r"n'|n(?![aiueoɯj])", "ɴ", t)          # n before a consonant or at the end of a word


def phonemes(t, phonemize, en="en-us"):
    """The whole sentence as one phoneme string, each marked span in its own language and the rest in `en`, for Kokoro
    to say in one call with one voice. `phonemize(text, lang)` → phonemes. → None if nothing is marked in a language
    we know (the usual case: Kokoro just reads the text)."""
    parts = spans(t)
    if all(lang is None for lang, _ in parts):
        return None
    ph = " ".join(romaji(w) if lang == "ja" else FLAG_RE.sub("", phonemize(w, lang or en)) for lang, w in parts)
    return re.sub(r"\s+([,.!?;:…])", r"\1", " ".join(ph.split()))
