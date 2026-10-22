"""Instant local commands: things Void can do himself in a blink instead of asking Claude.

    handle(text) -> None (not a quick command → goes to Claude) or a dict:
        {"say": "...", "mood": "happy", "cd": "/path" (switch folder), "sfx": "ok"}

Volume / music / brightness / timers & reminders / time & date / lock screen / open an app / switch project.
Anything that doesn't clearly match falls through to Claude, so false positives are kept to a minimum.
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
WIN = os.name == "nt"
MAC = sys.platform == "darwin"
if WIN:
    import winsys   # Windows versions of volume / media keys / brightness / apps / lock
if MAC:
    import macsys   # the macOS ones, the same shape
OSYS = winsys if WIN else macsys if MAC else None
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

# filled in by server.py
notify = None          # notify(title, body)
publish = None         # publish(**event) → page
cfg = None             # the live config dict
save_config = None     # save_config(**kw)

NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
       "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
       "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "half an": 0.5, "a half": 0.5, "couple": 2, "a couple": 2,
       "a few": 3, "few": 3}


def _run(cmd, timeout=4):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


def _num(s):
    s = (s or "").strip().lower()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return NUM.get(s)


# ---------------------------------------------------------------- volume
def _volume_pct():
    if OSYS:
        return OSYS.volume_get()
    if shutil.which("wpctl"):
        ok, out = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        m = re.search(r"([\d.]+)", out)
        if ok and m:
            return round(float(m.group(1)) * 100), "MUTED" in out
    if shutil.which("pactl"):
        ok, out = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        m = re.search(r"(\d+)%", out)
        mu = _run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])[1]
        if ok and m:
            return int(m.group(1)), "yes" in mu
    return None, False


def _volume(op, val=None):
    if OSYS:
        if not OSYS.volume_set(op, val):
            return None
        if op in ("mute", "unmute"):
            return {"say": "Muted." if op == "mute" else "Sound's back on.", "mood": "wink", "sfx": "ok"}
        pct, _ = _volume_pct()
        return {"say": f"Volume {pct} percent." if pct is not None else "Done.", "mood": "happy", "sfx": "ok"}
    w, p = shutil.which("wpctl"), shutil.which("pactl")
    if not (w or p):
        return None
    if op == "set":
        val = max(0, min(150, val))
        _run(["wpctl", "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", f"{val / 100:.2f}"] if w else
             ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{int(val)}%"])
    elif op in ("up", "down"):
        step = val or 10
        _run(["wpctl", "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", f"{step}%{'+' if op == 'up' else '-'}"] if w else
             ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{'+' if op == 'up' else '-'}{step}%"])
    elif op in ("mute", "unmute"):
        _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if op == "mute" else "0"] if w else
             ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if op == "mute" else "0"])
        return {"say": "Muted." if op == "mute" else "Sound's back on.", "mood": "wink", "sfx": "ok"}
    pct, _ = _volume_pct()
    return {"say": f"Volume {pct} percent." if pct is not None else "Done.", "mood": "happy", "sfx": "ok"}


# ---------------------------------------------------------------- media
def _media(op):
    if MAC and op == "what":
        now = macsys.now_playing()
        return {"say": f"That's {now}." if now else "Nothing's playing.", "mood": "curious"}
    if OSYS:
        if op == "what":
            return {"say": "I can't see what's playing on Windows yet.", "mood": "unsure"}
        if not OSYS.media(op):
            return {"say": "I can't find anything playing.", "mood": "confused"}
        return {"say": {"pause": "Okay.", "play": "Okay.", "toggle": "Okay.", "next": "Next one.",
                        "previous": "Going back."}[op], "mood": "happy", "sfx": "ok"}
    if not shutil.which("playerctl"):
        return {"say": "I need playerctl for that — pacman -S playerctl.", "mood": "confused"}
    if op == "what":
        ok, out = _run(["playerctl", "metadata", "--format", "{{title}} by {{artist}}"])
        return {"say": f"That's {out}." if ok and out.strip(" by") else "Nothing's playing.", "mood": "curious"}
    ok, _ = _run(["playerctl", {"pause": "pause", "play": "play", "toggle": "play-pause", "next": "next",
                                "previous": "previous"}[op]])
    if not ok:
        return {"say": "I can't find anything playing.", "mood": "confused"}
    return {"say": {"pause": "Paused.", "play": "Playing.", "toggle": "Okay.", "next": "Next one.",
                    "previous": "Going back."}[op], "mood": "happy", "sfx": "ok"}


# ---------------------------------------------------------------- brightness
def _bright(op, val=None):
    if OSYS:
        new = OSYS.brightness(op, val)
        return ({"say": f"Brightness {new} percent.", "mood": "happy"} if new is not None else
                {"say": "I can't change the brightness on a Mac yet." if MAC else
                 "I can only change the brightness of laptop screens on Windows.", "mood": "confused"})
    if not shutil.which("brightnessctl"):
        return {"say": "I need brightnessctl for that — pacman -S brightnessctl.", "mood": "confused"}
    arg = f"{int(val)}%" if op == "set" else f"{int(val or 10)}%{'+' if op == 'up' else '-'}"
    ok, _ = _run(["brightnessctl", "set", arg])
    ok2, cur = _run(["brightnessctl", "-m"])
    m = re.search(r",(\d+)%,", cur)
    return {"say": f"Brightness {m.group(1)} percent." if ok and m else
            ("Done." if ok else "Couldn't change the brightness on this screen."), "mood": "happy" if ok else "confused"}


# ---------------------------------------------------------------- timers & reminders
TIMERS = []          # [{"id", "at", "what", "timer"}]
_tlock = threading.Lock()


def _fire(item):
    with _tlock:
        if item in TIMERS:
            TIMERS.remove(item)
    what = item["what"]
    msg = f"Reminder: {what}" if what else "Time's up!"
    if notify:
        notify("Void", msg)
    if publish:
        publish(t="remind", text=msg)


def _schedule(secs, what):
    item = {"id": f"{time.time():.0f}", "at": time.time() + secs, "what": what}
    item["timer"] = threading.Timer(secs, _fire, args=(item,))
    item["timer"].daemon = True
    with _tlock:
        TIMERS.append(item)
    item["timer"].start()
    return item


def _dur_words(secs):
    if secs < 60:
        return f"{int(secs)} seconds"
    m = round(secs / 60)
    if m < 60:
        return f"{m} minute{'s' if m != 1 else ''}"
    h, m = divmod(m, 60)
    return f"{h} hour{'s' if h != 1 else ''}" + (f" {m} minutes" if m else "")


UNIT = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60, "minute": 60,
        "minutes": 60, "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600}
NUMRE = r"(\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|forty-five|fifty|sixty|ninety|half an|a couple|couple|a few|few)"
DUR_RE = re.compile(NUMRE + r"(?:\s+of)?\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b(?:\s+and\s+(?:a\s+)?half)?")


def _parse_dur(t):
    total, found = 0.0, False
    for m in DUR_RE.finditer(t):
        n = _num(m.group(1))
        if n is None:
            continue
        found = True
        mult = UNIT.get(m.group(2), 60)
        total += n * mult + (0.5 * mult if "half" in m.group(0)[len(m.group(1)):] else 0)
    return total if found else None


def _parse_clock(t):
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", t)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    now = datetime.now()
    at = now.replace(hour=h % 24, minute=mi, second=0, microsecond=0)
    if not ap and at <= now and h < 12:             # "at 5" in the afternoon → 5 pm
        at += timedelta(hours=12)
    if at <= now:
        at += timedelta(days=1)
    return (at - now).total_seconds(), at


def _timer(t):
    what = ""
    m = re.search(r"\bto\s+(.+)$", t)
    if m and "remind" in t:
        what = m.group(1).strip(" .")
    clock = _parse_clock(t)
    secs = clock[0] if clock else _parse_dur(t)
    if not secs or secs <= 0:
        return None
    if secs > 3 * 86400:
        return {"say": "That's a bit far off for me — try a calendar for that one.", "mood": "confused"}
    _schedule(secs, what)
    when = f"at {clock[1].strftime('%-I:%M %p').lower()}" if clock else f"in {_dur_words(secs)}"
    return {"say": f"Okay, I'll remind you {when}{' to ' + what if what else ''}." if what or "remind" in t
            else f"Timer set for {_dur_words(secs)}.", "mood": "wink", "sfx": "ok"}


def _timers_left():
    with _tlock:
        items = sorted(TIMERS, key=lambda i: i["at"])
    if not items:
        return {"say": "No timers running.", "mood": "curious"}
    parts = [f"{_dur_words(i['at'] - time.time())}{' — ' + i['what'] if i['what'] else ''}" for i in items[:3]]
    return {"say": "Left: " + "; ".join(parts) + ".", "mood": "curious"}


def _cancel_timers():
    with _tlock:
        n = len(TIMERS)
        for i in TIMERS:
            i["timer"].cancel()
        TIMERS.clear()
    return {"say": f"Cancelled {n} timer{'s' if n != 1 else ''}." if n else "There weren't any.", "mood": "happy"}


# ---------------------------------------------------------------- apps
def _desktop_files():
    dirs = [Path.home() / ".local/share/applications", Path("/usr/share/applications"),
            Path("/var/lib/flatpak/exports/share/applications"), Path.home() / ".local/share/flatpak/exports/share/applications"]
    for d in dirs:
        if d.is_dir():
            yield from d.glob("*.desktop")


def _find_app(name):
    name = name.lower().strip(" .")
    best = None
    for f in _desktop_files():
        try:
            txt = f.read_text(errors="replace")
        except OSError:
            continue
        if re.search(r"^NoDisplay\s*=\s*true", txt, re.M):
            continue
        nm = re.search(r"^Name\s*=\s*(.+)$", txt, re.M)
        nm = (nm.group(1).strip() if nm else f.stem)
        score = 0
        low = nm.lower()
        if low == name or f.stem.lower() == name:
            score = 3
        elif low.startswith(name) or name in low.split():
            score = 2
        elif name in low or name in f.stem.lower():
            score = 1
        if score and (not best or score > best[0]):
            best = (score, f, nm)
    return best


def _open_app(name):
    if OSYS:
        nm = OSYS.open_app(name)
        return {"say": f"Opening {nm}.", "mood": "happy", "sfx": "ok"} if nm else None
    hit = _find_app(name)
    if not hit:
        return None                                  # unknown → let Claude figure it out
    _, f, nm = hit
    if shutil.which("gtk-launch"):
        cmd = ["gtk-launch", f.stem]
    else:
        ex = re.search(r"^Exec\s*=\s*(.+)$", f.read_text(errors="replace"), re.M)
        if not ex:
            return None
        cmd = [a for a in shlex.split(ex.group(1)) if not a.startswith("%")]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"say": f"Opening {nm}.", "mood": "happy", "sfx": "ok"}


def _lock():
    if OSYS:
        OSYS.lock()
        return {"say": "Locking up. See you soon!", "mood": "wink"}
    for cmd in (["loginctl", "lock-session"], ["hyprlock"], ["swaylock", "-f"], ["xdg-screensaver", "lock"]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return {"say": "Locking up. See you soon!", "mood": "wink"}
    return {"say": "I don't know how to lock this screen.", "mood": "confused"}


# ---------------------------------------------------------------- projects
def projects():
    return dict(cfg.get("projects") or {})


def _project_name(name):
    return " ".join(re.sub(r"\b(my|the|project|folder|repo|directory)\b", " ", name.lower()).split())


def add_project(name, path):
    name = _project_name(name)
    p = os.path.abspath(os.path.expanduser(path.strip().rstrip(".,!")))
    if not name or not os.path.isdir(p):
        return None
    pr = projects()
    pr[name] = p
    save_config(projects=pr)
    return name, p


def _find_project(name):
    name = _project_name(name)
    if not name:
        return None
    pr = projects()
    if name in pr:
        return name, pr[name]
    for k, v in pr.items():
        if name in k or k in name or os.path.basename(v).lower() == name.replace(" ", "-"):
            return k, v
    return None


# ---------------------------------------------------------------- the matcher
def handle(text):
    t = " ".join((text or "").lower().replace("’", "'").split()).strip(" .!?")
    t = re.sub(r"^(hey void|void|ok void|okay void)[,\s]+", "", t)
    t = re.sub(r"^(please|can you|could you|would you|will you)\s+", "", t)
    t = re.sub(r"\s+please$", "", t)
    if not t or len(t) > 120:
        return None

    # switch project / folder
    m = re.match(r"^(?:switch to|go to|jump to|work on|open|cd to|change to)\s+(?:my\s+|the\s+)?(.+?)(?:\s+(?:project|folder|repo|directory))?$", t)
    if m:
        hit = _find_project(m.group(1))
        if hit:
            return {"say": f"Switched to {hit[0]}.", "mood": "happy", "cd": hit[1], "sfx": "ok"}

    # volume
    if re.search(r"\b(volume|sound|audio)\b|\b(louder|quieter|softer)\b|\b(mute|unmute)\b", t) and not re.search(r"\b(yourself|your voice|you)\b", t):
        if re.search(r"\bunmute\b", t):
            return _volume("unmute")
        if re.search(r"\bmute\b", t):
            return _volume("mute")
        m = re.search(r"(?:to|at)\s+(\d{1,3})\s*(?:%|percent)?", t)
        if m:
            return _volume("set", int(m.group(1)))
        by = re.search(r"\bby\s+(\d{1,2})", t)
        if re.search(r"\b(up|louder|higher|raise|increase|more)\b", t):
            return _volume("up", int(by.group(1)) if by else None)
        if re.search(r"\b(down|quieter|softer|lower|decrease|less)\b", t):
            return _volume("down", int(by.group(1)) if by else None)
        if re.search(r"\bwhat'?s the volume|\bhow loud\b", t):
            return _volume("get")

    # music
    if re.fullmatch(r"(pause|stop)( the)?( music| song| video| playback)?( for a (sec|second|bit))?", t):
        return _media("pause")
    if re.fullmatch(r"(play|resume|unpause|continue)( the)?( music| song| playback)?", t):
        return _media("play")
    if re.fullmatch(r"(next|skip)( (song|track|one|this))?|play the next (song|track|one)|skip (this|the) (song|track)", t):
        return _media("next")
    if re.fullmatch(r"(previous|last|go back)( (song|track|one))?|play the (previous|last) (song|track|one)", t):
        return _media("previous")
    if re.search(r"what('s| is) (this song|playing|the song)", t):
        return _media("what")

    # brightness
    if re.search(r"\bbright(ness|er)?\b|\bdimmer\b|\bdim (the )?screen\b", t):
        m = re.search(r"(?:to|at)\s+(\d{1,3})\s*(?:%|percent)?", t)
        if m:
            return _bright("set", int(m.group(1)))
        if re.search(r"\b(up|brighter|raise|increase|more)\b", t):
            return _bright("up")
        if re.search(r"\b(down|dimmer|dim|lower|decrease|less)\b", t):
            return _bright("down")

    # timers & reminders
    if re.search(r"\b(cancel|stop|clear)\b.*\btimers?\b|\b(cancel|clear)\b.*\breminders?\b", t):
        return _cancel_timers()
    if re.search(r"\bhow (long|much time)\b.*\b(left|timer)\b|\bany timers\b", t):
        return _timers_left()
    if re.search(r"\b(timer|remind me|alarm)\b", t) or re.match(r"^" + NUMRE + r"\s+(minute|min|second|hour)s?\s+timer$", t):
        r = _timer(t)
        if r:
            return r

    # time & date
    if re.fullmatch(r"(what'?s the |what is the )?time( is it)?( now)?|what time is it( now)?", t):
        return {"say": f"It's {datetime.now().strftime('%-I:%M %p').lower()}.", "mood": "curious"}
    if re.fullmatch(r"(what'?s|what is) (the )?(date|day)( today)?( is it)?|what day is (it|today)|what'?s today", t):
        return {"say": f"It's {datetime.now().strftime('%A, %B %-d')}.", "mood": "curious"}

    # lock
    if re.fullmatch(r"lock (my |the )?(screen|computer|pc|laptop|desktop|session)( now)?", t):
        return _lock()

    # open an app (only when it's clearly "open <app name>")
    m = re.fullmatch(r"(?:open|launch|start|run)\s+(?:up\s+)?(?:the\s+)?([a-z0-9 .+-]{2,30}?)(?:\s+app)?", t)
    if m and not re.search(r"\b(file|folder|project|repo|this|that|it|my)\b", m.group(1)):
        return _open_app(m.group(1))
    return None
