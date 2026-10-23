"""Quick memory: a small file of what he did lately, sorted by age, so he rarely has to look anything up.

  today      kept in full: what you asked, what he said, which files he touched. Questions about it ("what did you do
             today?", "what did you say about the caption?", "say that again") are answered straight from the file,
             with no Claude call at all
  this week  one line per day and folder
  older      dropped from this file (the full text stays in history.jsonl; he's told how to grep it)

Everything here is plain string work on a few KB: updating and answering take well under a millisecond.
"""
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

PATH = HIST = None
KEEP_DAYS, TODAY_MAX, TODAY_CHARS, WEEK_CHARS = 7, 40, 1800, 1200
_cache = {"mtime": None, "data": {"entries": [], "hits": 0}}
STOP = set("the and for you that this with have from was were are but not what when how did your can will about into "
           "then than them they there just like want please could would should some make made get got let its has had "
           "his her out all any one two also tell told say said ask asked now too very more".split())
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def init(data_dir, hist_path):
    global PATH, HIST
    PATH, HIST = Path(data_dir) / "recent.json", Path(hist_path)


def _load():
    try:
        m = PATH.stat().st_mtime_ns
    except OSError:
        return _cache["data"]
    if m != _cache["mtime"]:
        try:
            _cache["data"], _cache["mtime"] = json.loads(PATH.read_text()), m
        except (OSError, ValueError):
            pass
    return _cache["data"]


def _save(data):
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.chmod(tmp, 0o600)                              # it has snippets of your chats in it
    os.replace(tmp, PATH)
    _cache["data"], _cache["mtime"] = data, PATH.stat().st_mtime_ns
    PATH.with_suffix(".md").write_text(render(data))


def words(*texts):
    return sorted({w for t in texts for w in re.findall(r"[a-z0-9_.]{3,}", t.lower()) if w not in STOP})[:24]


def _short(text, n=260):
    """The first sentence or two of an answer, no face tags."""
    text = re.sub(r"\[[a-z]+\]\s*", "", " ".join((text or "").split()))
    out = ""
    for s in re.findall(r"[^.!?]+[.!?]*", text):
        if out and len(out) + len(s) > n:
            break
        out += s
        if len(out) > n * 0.6:
            break
    return out.strip()[:n]


def add(rec):
    """Remember one finished exchange (rec as saved in history) and tidy the file."""
    q, a = " ".join((rec.get("you") or "").split()), _short(rec.get("him"))
    if not q or (rec.get("ok") is not False and len(a) < 12):      # nothing worth keeping
        return
    files = list(dict.fromkeys(rec.get("files") or []))[:6]
    e = {"ts": rec.get("ts") or int(time.time()), "cwd": rec.get("cwd_display") or "", "q": q[:140], "a": a,
         "full": " ".join(re.sub(r"\[[a-z]+\]\s*", "", rec.get("him") or "").split())[:500], "files": files,
         "ok": rec.get("ok") is not False, "kw": words(q, a, " ".join(files))}
    data = dict(_load())
    key = re.sub(r"\W+", " ", q.lower()).strip()
    keep = [x for x in data.get("entries", []) if re.sub(r"\W+", " ", x["q"].lower()).strip() != key]   # asked again: newest wins
    keep.append(e)
    cutoff = time.time() - KEEP_DAYS * 86400
    keep = [x for x in keep if x["ts"] >= cutoff and (x["ok"] or x["ts"] >= midnight())]               # old failures are junk
    today = [x for x in keep if x["ts"] >= midnight()]
    drop = {id(x) for x in sorted(today, key=lambda x: x["ts"])[:-TODAY_MAX]}
    data["entries"] = [x for x in keep if id(x) not in drop]
    data.setdefault("hits", 0)
    _save(data)


def midnight():
    n = datetime.now()
    return n.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _when(ts):
    d = datetime.fromtimestamp(ts)
    return "earlier today" if ts >= midnight() else d.strftime("on %A")


def render(data=None):
    """The file as text: today in full, the week in one line per day and folder."""
    es = sorted((data or _load()).get("entries", []), key=lambda x: x["ts"])
    m, lines, used = midnight(), [], 0
    for e in reversed([x for x in es if x["ts"] >= m]):            # newest first, until the budget runs out
        f = f" [touched {', '.join(e['files'])}]" if e["files"] else ""
        ln = f"- {time.strftime('%H:%M', time.localtime(e['ts']))} {e['cwd']} · asked: {e['q'][:90]!r} → {e['a'][:200]}{f}"
        if used + len(ln) > TODAY_CHARS:
            break
        lines.append(ln)
        used += len(ln)
    today = list(reversed(lines))
    days = {}
    for e in es:
        if e["ts"] < m:
            days.setdefault((time.strftime("%a %b %d", time.localtime(e["ts"])), e["cwd"]), []).append(e)
    week, used = [], 0
    for (day, cwd), xs in reversed(list(days.items())):
        fs = list(dict.fromkeys(f for x in xs for f in x["files"]))[:5]
        ln = f"- {day} {cwd} ({len(xs)}): " + "; ".join(x["q"][:50] for x in xs[-4:]) + (f" · files: {', '.join(fs)}" if fs else "")
        if used + len(ln) > WEEK_CHARS:
            break
        week.append(ln)
        used += len(ln)
    out = []
    if today:
        out.append("Today:\n" + "\n".join(today))
    if week:
        out.append("Earlier this week:\n" + "\n".join(week))
    return "\n".join(out)


def prompt():
    """What goes in the system prompt of a new chat (empty if there's nothing yet)."""
    body = render()
    if not body:
        return ""
    return ("Quick memory — what you and the user did lately. Trust it before searching the project; it's kept up to "
            "date. For anything older, grep -i <word> in " + str(HIST) + " (one JSON line per chat).\n" + body)


# ---- answering without Claude
RE_AGAIN = re.compile(r"^(?:say that again|say it again|repeat that|repeat it|what did you (?:just )?say|come again)$")
RE_RECAP = re.compile(r"^(?:what (?:did|have) (?:we|you) (?:do|done|work(?:ed)? on|change[d]?|edit(?:ed)?|made?|build|built|fix(?:ed)?)"
                      r"(?: so far| today| earlier| lately| recently| just now)?|what (?:are|were) we (?:working on|doing)|"
                      r"what files did you (?:change|edit|touch|modify)(?: today| earlier)?)$")
RE_LAST_Q = re.compile(r"^what was (?:my|the) (?:last|previous) (?:question|thing i asked|request)$")
RE_TOPIC = re.compile(r"^(?:what|remind me what) (?:did|was it that) (?:you|we) (?:say|tell me|do|decide|find|change|fix|write)"
                      r"(?: to me)? (?:about|with|to|on|regarding) (.+)$")


def answer(text):
    """A spoken answer taken from the file, or None (then it goes to Claude as usual)."""
    if PATH is None:
        return None
    t = " ".join((text or "").lower().replace("’", "'").split()).strip(" .!?")
    t = re.sub(r"^(?:hey void|void|ok void|okay void)[,\s]+", "", t)
    t = re.sub(r"^(?:please|can you|could you|would you|will you)\s+", "", t)
    if not t or len(t) > 120:
        return None
    es = sorted(_load().get("entries", []), key=lambda x: x["ts"])
    if RE_AGAIN.match(t):
        say = es[-1]["full"] if es else None
    elif RE_LAST_Q.match(t):
        say = f"You asked: {es[-1]['q']}" if es else None
    elif RE_RECAP.match(t):
        say = _recap(es)
    elif (m := RE_TOPIC.match(t)):
        say = _topic(es, m.group(1))
    else:
        return None
    if say:
        data = dict(_load())
        data["hits"] = data.get("hits", 0) + 1
        _save(data)
    return say


def _recap(es):
    today = [e for e in es if e["ts"] >= midnight()]
    if not today:
        return "Nothing yet today."
    steps = "; ".join(e["a"].split(".")[0][:90] for e in today[-4:] if e["ok"])
    fs = list(dict.fromkeys(f for e in today for f in e["files"]))[:5]
    n = len(today)
    return (f"Today we've done {n} thing{'s' if n > 1 else ''}. {steps}." + (f" I touched {', '.join(fs)}." if fs else "")).strip()


def _topic(es, topic):
    want = set(words(topic))
    if not want:
        return None
    best, score = None, 0
    for e in es:
        s = len(want & set(e["kw"])) + 0.01 * e["ts"] / 1e9        # (newest wins a tie)
        if s > score:
            best, score = e, s
    if not best or len(want & set(best["kw"])) < max(1, round(len(want) * 0.6)):
        return None                                               # not sure: let Claude answer properly
    return f"{_when(best['ts']).capitalize()} you asked: {best['q']}. I said: {best['full'] or best['a']}"


def stats():
    return {"answered": _load().get("hits", 0), "entries": len(_load().get("entries", []))}
