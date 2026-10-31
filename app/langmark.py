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


# For when Claude forgets the mark: a title or name that's clearly in one language is marked here, just for Kokoro.
# Clearly means a run of capitalized words (lowercase function words and the word after one may join: "Écailles de
# lune"), or a quoted title, with a function word of the language, a second sign (another function word, or a letter
# it uses), and either such a letter or a function word between two content words. So "Le Toit du Monde", "Les
# Misérables" and "Der Himmel über Berlin" are marked; "Die Hard", "La Jolla", "De La Soul" and "Mötley Crüe" aren't.
# A false French is worse than a missed one: when in doubt it stays English. No rōmaji (no way to tell it from English).
FN = {k: set(v.split()) for k, v in {        # English look-alikes (a, in, on, do, no, o, al, con, per, den…) left out
    "fr": "le la les du des de et sur dans au aux un une en est pour par avec sous ce qui ou pas à",
    "es": "el los las del la de en y un una por para que sin",
    "de": "der die das und von zu im dem des ein eine einer eines über unter auf aus für vom zum zur ist nicht",
    "it": "il lo gli la le della delle dello degli dei del di e è che nel nella alla sul un una uno",
    "pt": "da dos das de e os uma em na nas nos com que não à ao aos pela pelo",
    "nl": "het een van de en op te voor uit niet",
    "sv": "och av på för det som är en ett inte över",
    "pl": "nie się że jest dla od przez na",
}.items()}
ACCENT = {"fr": "éèêëàâçîïôûùœÿ", "es": "áéíóúñü", "de": "äöüß", "it": "àèéìòù", "pt": "áâãàçéêíóôõú",
          "nl": "éèëïó", "sv": "åäöé", "pl": "ąćęłńóśźż"}   # any other letter (ø, æ, ð…): none of these, left alone
ARTICLES = set("le la les el los las der die das dem il lo gli het een un une una uno ein eine".split())
ANY_FN = set().union(*FN.values())
ACCENT_ALL = set("".join(ACCENT.values()))
WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*(?:-[^\W\d_]+(?:['’][^\W\d_]+)*)*")
KEEP_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]*`|\{[^{}\n]*(?:\}|$)|\[[^\]\n]*\])")   # code, marks, braces, [tags]
QUOTE_RE = re.compile(r"([\"“«„]\s*)([^\"“”«»„\n]{1,80}?)(\s*[\"”»“])")


def _fns(w):
    """→ the languages word w is a function word of (none for "LE": capitals are an acronym or shouting)"""
    if w.isupper():
        return set()
    w = w.lower()
    return {k for k, s in FN.items() if w in s} | ({"fr"} if re.match(r"(?:[ld]|qu)['’]", w) else set())


def _isfn(w):
    return not w.isupper() and w.lower() in ANY_FN


def _lang(words):
    """→ the code of the one language a run of words is clearly in, or None"""
    low = [w.lower() for w in words]
    fns = [_fns(w) for w in words]
    odd = {c for w in low for c in w if not c.isascii() and c.isalpha()}
    if len(words) < 2 or all(fns) or odd - ACCENT_ALL:
        return None
    best = None
    for k in FN:
        n = sum(k in f for f in fns)
        inner = any(k in fns[i] and not _isfn(words[i - 1]) and not _isfn(words[i + 1]) for i in range(1, len(fns) - 1))
        acc = sum(any(c in ACCENT[k] for c in w) for w in low)
        if n and n + acc >= 2 and (acc or inner) and odd <= set(ACCENT[k]) and (best is None or n + acc > best[0]):
            best = (n + acc, k)                                   # a tie: the first in FN
    return best and best[1]


def _runs(s):
    """→ [(start, end, code)] for the runs of words in s that are clearly foreign"""
    runs, cur = [], []
    for m in WORD_RE.finditer(s):
        w = m.group()
        cap = w[0].isupper() and not (len(w) > 1 and w.isupper())
        last = cur and cur[-1].group()
        if last and re.fullmatch(r"[ \t\u00a0]+", s[cur[-1].end():m.start()]) and not re.search(r"['’]s$", last) \
                and (cap or _isfn(w) or _isfn(last)):
            cur.append(m)
        else:
            runs.append(cur)
            cur = [m] if cap else []
    out = []
    for r in runs + [cur]:
        ws = [m.group() for m in r]
        art = next((i for i, w in enumerate(ws) if w.lower() in ARTICLES and not w.isupper()), 0)
        if not any(_fns(w) for w in ws[:art]):
            r = r[art:]                                          # "Then Le Toit du Monde": the title starts at "Le"
        while r and _isfn(r[-1].group()):
            r = r[:-1]                                           # "… Monde et": not the "et"
        k = _lang([m.group() for m in r])
        cut = k and next((i for i, m in enumerate(r) if m.group().islower() and _fns(m.group())
                          and k not in _fns(m.group())), None)
        if cut:                                                  # "… Monde die hard": the English after it isn't
            r = r[:cut]
            while r and _isfn(r[-1].group()):
                r = r[:-1]
            k = _lang([m.group() for m in r])
        if k:
            out.append((r[0].start(), r[-1].end(), k))
    return out


def _mark_runs(s):
    out, at = [], 0
    for a, b, k in _runs(s):
        out += [s[at:a], "{%s:%s}" % (k, s[a:b])]
        at = b
    return "".join(out) + s[at:]


def _mark_free(s):
    out, at = [], 0
    for m in QUOTE_RE.finditer(s):
        words = m.group(2).split()
        k = len(words) <= 8 and all(WORD_RE.fullmatch(w) for w in words) and _lang(words)
        inner = "{%s:%s}" % (k, m.group(2)) if k else _mark_runs(m.group(2))
        out += [_mark_runs(s[at:m.start()]), m.group(1), inner, m.group(3)]
        at = m.end()
    return "".join(out) + _mark_runs(s[at:])


def guess(t):
    """Marks what's clearly a foreign title that Claude didn't: "I loved Le Toit du Monde." → "I loved {fr:Le Toit du
    Monde}." Code, marks, anything else in braces and [tags] stay as they are."""
    return "".join(p if i % 2 else _mark_free(p) for i, p in enumerate(KEEP_RE.split(t or "")))


def phonemes(t, phonemize, en="en-us"):
    """The whole sentence as one phoneme string, each marked span in its own language and the rest in `en`, for Kokoro
    to say in one call with one voice. `phonemize(text, lang)` → phonemes. → None if nothing is marked in a language
    we know (the usual case: Kokoro just reads the text). Foreign titles left unmarked are marked first (guess)."""
    parts = spans(guess(t))
    if all(lang is None for lang, _ in parts):
        return None
    ph = " ".join(romaji(w) if lang == "ja" else FLAG_RE.sub("", phonemize(w, lang or en)) for lang, w in parts)
    return re.sub(r"\s+([,.!?;:…])", r"\1", " ".join(ph.split()))
