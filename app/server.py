#!/usr/bin/env python3
"""Void — a tiny voice-first shell for Claude Code.

Local server (127.0.0.1 only) that:
  * serves the Void UI
  * records the mic itself (parecord / pw-record / arecord — no browser mic permissions needed)
    with a little voice-activity detector, and transcribes it with faster-whisper
  * runs `claude -p --output-format stream-json` and streams events back
  * speaks replies with piper (or espeak-ng as a fallback)
"""
import argparse
import array
import io
import json
import math
import os
import re
import select
import shlex
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
import quick  # noqa: E402
WIN = os.name == "nt"                        # Windows: OS bits go through winsys.py instead of Linux command-line tools
if WIN:
    import winsys  # noqa: E402
    winsys.dpi_aware()
    for _s in (sys.stdout, sys.stderr):               # logs go to a file: write UTF-8, not the old Windows code page
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
MAC = sys.platform == "darwin"               # macOS: OS bits go through macsys.py (the same shape as winsys.py)
if MAC:
    import macsys  # noqa: E402
OSYS = winsys if WIN else macsys if MAC else None   # (None on Linux: command-line tools, below)
STATIC = APP_DIR / "static"
try:                                            # written by the release build (and kept in git for git installs)
    VERSION = (APP_DIR / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
except OSError:
    VERSION = "0.0.0"
if WIN:
    DATA_DIR, CONF_PATH = winsys.data_dir(), winsys.config_dir() / "config.json"
else:
    DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "void"
    CONF_PATH = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "void" / "config.json"
STATE_PATH = DATA_DIR / "state.json"
# Two passwords per run. TOKEN is the window's: it can do everything, so it's never put anywhere other programs can
# see it (not in Claude's environment, not in the page the server hands out); the window reads it from runtime.json,
# which only you can read. TOOL_TOKEN goes to the commands Claude runs (the approval hook, `void see`, the sudo
# password helper) and only works for those: asking you to approve, looking at the screen, and the sudo card in root mode.
TOKEN = secrets.token_urlsafe(24)
TOOL_TOKEN = secrets.token_urlsafe(24)
TOOL_PATHS = {"/api/approve", "/api/capture", "/api/askpass"}

VOICE_PROMPT = (
    "You are being driven from Void, a small voice-first desktop shell for Claude Code. "
    "The user is often speaking, so their message may contain speech-to-text mistakes; interpret them sensibly. "
    "Use your tools normally to get the work done, but keep your final reply short and conversational "
    "(1-4 sentences, no tables) because it is read aloud. If there is long output, put it in a file and say which one. "
    "Talk about files, folders and commands the way a person would (\"your Downloads folder\", \"the install script\", "
    "\"I'll update your packages\"), never by reading out full paths or raw commands. "
    "Whenever you run a Bash command, always fill in its description with a short plain-English summary of what it "
    "does, for someone who isn't technical (e.g. \"Restart Firefox\", \"Clear out old package downloads to free up "
    "space\"); Void reads it aloud when asking the user for permission. "
    "When a task needs you to see the user's screen (they mention something on it, or you need to check what happened), "
    "run `void see` for the window they're using or `void see screen` for everything; it prints the path of a small "
    "picture to open with the Read tool. It only looks, it can't click or type. Don't look when the task doesn't need it. "
    "Void has an expressive face. In your final spoken reply only, start each sentence with one tag in square brackets "
    "saying how you mean it, so the face matches your words: [neutral] [happy] [proud] [excited] [laugh] [warm] "
    "[curious] [unsure] [thinking] [surprised] [sorry] [serious]. Example: \"[proud] Fixed it, all nine tests pass now. "
    "[curious] Want me to commit it?\" You can switch tags mid-sentence if your feeling changes. Never put these tags in "
    "files, code, commands or tool input — only in the words you say to the user."
)
# the face tags Claude puts in its replies (see VOICE_PROMPT) — never spoken, never shown, never saved
FACE_TAG_RE = re.compile(r"\[(?:neutral|happy|proud|excited|laugh|warm|curious|unsure|thinking|surprised|sorry|serious)\]\s*", re.I)


def untag(t):
    return FACE_TAG_RE.sub("", t or "")

DEFAULTS = {
    "port": 47777,
    "cwd": "~",
    "claude_bin": "claude",
    # how much he may do on his own:
    #   ask      – edits files himself, asks you (out loud + a card) before commands / web requests   (default)
    #   trust    – does anything without asking
    #   readonly – looks but doesn't touch
    "trust": "ask",
    # (old setting, still honoured if you set it by hand and "trust" is missing)
    "permission_mode": "acceptEdits",
    "model": "",
    "extra_args": [],
    "partial_messages": True,
    "voice_prompt": VOICE_PROMPT,
    # auto = small.en on an NVIDIA GPU (fast + accurate), base.en on CPU. Or pin one: tiny.en / base.en / small.en / medium.en
    "whisper_model": "auto",
    "whisper_beam": 5,
    # words he should expect (helps with names/jargon)
    "stt_hint": "Hey Void, Claude, Claude Code, Void, git, commit, README, Python, JavaScript, npm, pacman, sudo, Arch Linux, "
                "Hyprland, folder, directory, file, terminal, config, server, function, bug, refactor.",
    "whisper_device": "auto",
    # keep the mic at a level he hears well: turn the Windows mic level up when your voice comes in quiet, down when it
    # clips, and boost it himself past 100%
    "mic_auto": True,
    # ask GitHub twice a day whether a newer Void is out (nothing about you is sent) and offer "Update now"
    "update_check": True,
    # hold this anywhere to talk, let go to send (Windows watches it itself; on Linux bind `void ptt-down` / `ptt-up`)
    "ptt_hotkey": "Ctrl+Alt+Space",
    # how he sits on your desktop: "orb" (the floating blob) or "bar" (his eyes in a notch at the top of the screen)
    "presence": "orb",
    "bar_edge": "top",           # which screen edge the bar is merged into: top | bottom | left | right
    "language": "en",
    "stt_prewarm": True,

    "tts": "auto",          # auto (Kokoro → piper → espeak) | kokoro | piper | espeak | off
    "piper_voice": "",
    "espeak_voice": "en-us",
    "speak_max_chars": 600,
    # voice preset: girl | cute | guy | deep | british | story | robot
    # (engine: Kokoro if installed, else piper — piper voices download on first use)
    "voice": "girl",
    # root mode: Claude may run anything, incl. sudo (you get a password box each time it needs root)
    "root_mode": False,
    "cpu_split": True,       # face (window) and voice/brain (server) on separate CPU cores (4+ cores only)
    # pupil colour: "#rrggbb", or "random" for a new one every time he pops up
    "eye_color": "#3d9b74",
    # eye-white colour: "#rrggbb" or "random" (a soft pastel each time)
    "eye_white": "#f3dccf",
    # resting pupil shape: slit | circle | dot  (they still change shape with his mood)
    "pupil_style": "slit",
    # "hey Void" wake word (keeps the mic open and listens for his name; nothing leaves your machine)
    "wake_word": True,
    # start Void when you log in (Hyprland exec-once + XDG autostart)
    "autostart": True,
    # start listening the moment he pops up (`void`)
    "listen_on_show": False,
    # say "stop" / "hey Void" while he's talking to cut him off
    "barge_in": True,
    # for this many seconds after he stops talking, just talk — no "hey Void" needed (0 = off)
    "follow_up": 4,
    # live view ("watch my screen"): turns itself off after this many minutes without you saying anything
    "live_minutes": 10,
    # little sound effects
    "sfx": True,
    # how he looks: body + each eye + each eye white, a skin (accessory), and whether to roll random colours each time
    "look": {},
    # projects you can switch to by voice: {"delivery app": "/home/you/code/diq"}
    "projects": {},
    # when a long task finishes while he's hidden: desktop notification / pop back up
    "notify_done": True,
    "popup_done": False,
    "code_panel": True,      # the floating code / terminal panel while he works
    "code_face": "him",      # which way the terminal faces: "him" (you see the back, mirrored) or "you" (readable)
    # hover mode: he floats next to your mouse cursor
    "hover": False,
    # clicks on the see-through space around him go to the window behind (can flicker with some GPU drivers)
    "click_through": False,
    # one-tap prompts on the right-click menu
    "quick_actions": [
        {"label": "Explain my clipboard", "prompt": "Explain what's on my clipboard in plain words. If it's an error, tell me how to fix it.", "attach": "clipboard"},
        {"label": "What's on my screen?", "prompt": "Look at my screen and tell me briefly what's going on. Point out anything that looks wrong.", "attach": "screen"},
        {"label": "Commit my changes", "prompt": "Look at the git changes in this folder and commit them with a clear message. Tell me what you committed."},
        {"label": "Run the tests", "prompt": "Run this project's tests. Tell me what passed and what failed, and why."},
        {"label": "What's this folder?", "prompt": "Give me a short tour of this folder: what the project is and the important files."},
    ],
    # mic: leave empty for the system default input. `void doctor` lists inputs.
    "mic_device": "",
    # optional custom recorder; must write raw s16le, 16 kHz, mono to stdout
    "mic_cmd": "",
}


def load_config():
    cfg = dict(DEFAULTS)
    if CONF_PATH.exists():
        try:
            user = json.loads(CONF_PATH.read_text(encoding="utf-8-sig"))   # (-sig: Notepad may add a BOM)
            if user.get("whisper_model") == "base.en":     # the old default → let it pick (GPU gets a better model)
                user["whisper_model"] = "auto"
            cfg.update(user)
            if "trust" not in user and user.get("permission_mode"):
                cfg["trust"] = {"bypassPermissions": "trust", "plan": "readonly"}.get(user["permission_mode"], "ask")
        except Exception as e:  # noqa: BLE001
            print(f"[void] could not parse {CONF_PATH}: {e}", file=sys.stderr)
    else:
        CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONF_PATH.write_text(json.dumps(DEFAULTS, indent=2) + "\n")
    return cfg


CFG = load_config()

SKINS = ["none", "cat", "bunny", "bear", "antenna", "devil", "halo", "crown", "sprout", "party", "headphones", "bow"]
HEX = re.compile(r"#[0-9a-f]{6}")


def look():
    lk = dict(CFG.get("look") or {})
    ec, ew = (CFG.get("eye_color") or "#3d9b74").lower(), (CFG.get("eye_white") or "#f3dccf").lower()
    pup = ec if HEX.fullmatch(ec) else "#3d9b74"
    wht = ew if HEX.fullmatch(ew) else "#f3dccf"
    out = {"body": "#0b0b0b", "pupL": pup, "pupR": pup, "whiteL": wht, "whiteR": wht, "skin": "none",
           "skinColor": "", "link": True, "random": ec == "random" or ew == "random"}
    for k, v in lk.items():
        if k in ("body", "pupL", "pupR", "whiteL", "whiteR", "skinColor") and isinstance(v, str) and (HEX.fullmatch(v.lower()) or (k == "skinColor" and v == "")):
            out[k] = v.lower()
        elif k == "skin" and v in SKINS:
            out[k] = v
        elif k in ("link", "random"):
            out[k] = bool(v)
    return out


# ---------------------------------------------------------------- state
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.stopped = False
        s = {}
        try:
            s = json.loads(STATE_PATH.read_text())
        except Exception:  # noqa: BLE001
            pass
        self.cwd = s.get("cwd") or os.path.expanduser(CFG["cwd"])
        if not os.path.isdir(self.cwd):
            self.cwd = str(Path.home())
        self.session = s.get("session")
        self.auto_pick = ""

    def save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({"cwd": self.cwd, "session": self.session}))


ST = State()


def pretty_path(p):
    home = str(Path.home())
    return "~" + p[len(home):] if p == home or p.startswith(home + "/") else p


# ---------------------------------------------------------------- memory + history
MEM_PATH = DATA_DIR / "memory.json"
HIST_PATH = DATA_DIR / "history.jsonl"
_mem_lock = threading.Lock()


def mem_load():
    try:
        return json.loads(MEM_PATH.read_text())
    except Exception:  # noqa: BLE001
        return []


def mem_save(items):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEM_PATH.write_text(json.dumps(items, indent=1))


def mem_add(text):
    text = " ".join(str(text).split())[:400]
    if not text:
        return None
    with _mem_lock:
        items = mem_load()
        if any(i["text"].lower() == text.lower() for i in items):
            return None
        it = {"id": secrets.token_hex(4), "text": text, "ts": int(time.time())}
        items.append(it)
        mem_save(items[-200:])
        return it


def mem_del(ident=None, match=None):
    """Remove by id, or every note containing `match` (case-insensitive). Returns how many went."""
    with _mem_lock:
        items = mem_load()
        keep = [i for i in items if not (i["id"] == ident or
                                         (match and match.lower().strip(" .") in i["text"].lower()))]
        mem_save(keep)
        return len(items) - len(keep)


def hist_add(rec):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(HIST_PATH, "a") as f:
        f.write(json.dumps(rec) + "\n")


def hist_load(limit=60):
    try:
        lines = HIST_PATH.read_text().splitlines()[-2000:]
    except Exception:  # noqa: BLE001
        return []
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


def memory_prompt(fresh):
    parts = []
    notes = mem_load()
    if notes:
        parts.append("Things the user asked you to remember (keep using them):\n" +
                     "\n".join(f"- {n['text']}" for n in notes[-60:]))
    if fresh:                                   # a new conversation: give him a sense of what happened lately
        recent = [h for h in hist_load(8) if h.get("you")][:6]
        if recent:
            parts.append("Recent conversations with the user (background only — don't bring these up unless the user "
                     "refers to them):\n" + "\n".join(
                f"- [{time.strftime('%b %d %H:%M', time.localtime(h['ts']))}, in {h.get('cwd_display', '?')}] "
                f"they said: {h['you'][:160]!r} — you answered: {(h.get('him') or '')[:200]!r}" for h in recent))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- start with the computer
AUTOSTART_DESKTOP = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "void.desktop"
HYPR_CONF = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "hypr" / "hyprland.conf"
HYPR_MARK = "# added by Void (Settings → More → Start when I log in)"
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "void.plist"


def sync_autostart():
    """Add/remove Void from login startup to match the `autostart` setting."""
    on = bool(CFG.get("autostart"))
    if WIN:                                   # a shortcut in the Startup folder that runs the launcher quietly
        link = winsys.startup_link()
        if on:
            pyw = Path(sys.executable).with_name("pythonw.exe")
            winsys.shortcut(link, str(pyw if pyw.exists() else sys.executable), f'"{APP_DIR / "launch_win.py"}" show',
                            icon=str(STATIC / "icon.ico"))
        else:
            link.unlink(missing_ok=True)
        return
    if MAC:                                   # a LaunchAgent that runs `void show` when you log in
        launcher = shutil.which("void") or str(Path.home() / ".local/bin/void")
        try:
            if on:
                import plistlib
                LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
                LAUNCH_AGENT.write_bytes(plistlib.dumps({"Label": "void", "ProgramArguments": [launcher, "show"],
                                                         "RunAtLoad": True}))
            else:
                LAUNCH_AGENT.unlink(missing_ok=True)
        except OSError as e:
            print(f"[void] launch agent: {e}", file=sys.stderr)
        return
    launcher = shutil.which("void") or str(Path.home() / ".local/bin/void")     # ~/.local/bin, or /usr/bin after ninja install
    try:
        if on:
            AUTOSTART_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
            AUTOSTART_DESKTOP.write_text(
                "[Desktop Entry]\nType=Application\nName=Void\nComment=Voice shell for Claude Code\n"
                f"Exec={launcher} show\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n")
        else:
            AUTOSTART_DESKTOP.unlink(missing_ok=True)
    except OSError as e:
        print(f"[void] autostart file: {e}", file=sys.stderr)
    if HYPR_CONF.exists():                    # Hyprland doesn't read XDG autostart → exec-once line, clearly marked
        try:
            lines = HYPR_CONF.read_text().splitlines()
            keep = [ln for ln in lines if HYPR_MARK not in ln]
            if on:
                keep.append(f"exec-once = {launcher} show   {HYPR_MARK}")
            if keep != lines:
                HYPR_CONF.write_text("\n".join(keep) + "\n")
        except OSError as e:
            print(f"[void] hyprland.conf: {e}", file=sys.stderr)


# ---------------------------------------------------------------- event bus (server → page, one long stream)
class Bus:
    def __init__(self):
        self.subs = []
        self.lock = threading.Lock()

    def sub(self):
        import queue
        q = queue.Queue()
        with self.lock:
            self.subs.append(q)
        return q

    def unsub(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def pub(self, **ev):
        with self.lock:
            for q in self.subs:
                q.put(ev)


BUS = Bus()


# ---------------------------------------------------------------- "ask me first" approvals
def summarize_command(cmd):
    """No description from Claude: ask the fast model what the command does, in a few plain words (None if it can't)."""
    binary = claude_bin()
    if not binary or not cmd.strip():
        return None
    ask = ("In 3 to 10 plain words, for someone who isn't technical, say what this shell command does. Start with a "
           "verb, e.g. \"restart Firefox\" or \"delete the old download in your Downloads folder\". Only say what it "
           "does, never guess why. Reply with only that phrase.\n\nCommand:\n" + cmd[:2000])
    try:
        out = subprocess.run([binary, "-p", "--model", "haiku", "--tools", "", "--no-session-persistence"],
                             input=ask, capture_output=True, text=True, timeout=15, cwd=str(DATA_DIR)).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    line = (out.strip().splitlines() or [""])[0].strip(" \"'.`*")
    return line if 0 < len(line) < 120 else None


def approval_say(tool, inp):
    """What he asks out loud: what the thing does, not the raw command (the card still shows the real thing)."""
    inp = inp if isinstance(inp, dict) else {}
    if tool == "Bash":
        # Claude's own one-line summary of the command (it's told to always write one), else a quick summary from the
        # fast model — never the raw command (the card under him still shows that)
        desc = " ".join(str(inp.get("description") or "").split()) or summarize_command(str(inp.get("command") or ""))
        desc = speakable(desc or "").rstrip(".")
        if not desc:
            return "Can I run this command?"
        return "Can I run a command to " + desc[0].lower() + desc[1:] + "?"
    if tool == "WebFetch":
        host = re.sub(r"^https?://(www\.)?", "", str(inp.get("url") or "")).split("/")[0]
        return f"Can I open a page on {host}?" if host else "Can I open this page?"
    if tool == "WebSearch":
        return "Can I search the web?"
    if tool in EDIT_TOOLS:
        p = edit_path(inp)
        return f"Can I change {p.name}?" if p else "Can I change this file?"
    return f"Can I use {tool}?"


# ---------------------------------------------------------------- file edits ("Ask me first")
# Claude edits files in the project on its own, but these places run things later, change how Void or Claude behave,
# or hold keys: a change there gets the approval card, like a command. So does any edit outside the project folder.
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def edit_path(inp):
    p = (inp or {}).get("file_path") or (inp or {}).get("notebook_path") if isinstance(inp, dict) else None
    if not p:
        return None
    try:
        return (Path(ST.cwd) / os.path.expanduser(str(p))).resolve()     # (resolve: a symlink can't hide where it points)
    except (OSError, ValueError):
        return None


def sensitive_paths():
    h = Path.home()
    dirs = [CONF_PATH.parent, DATA_DIR, APP_DIR, h / ".claude", h / ".ssh", h / ".gnupg", h / ".aws", h / ".kube",
            h / ".local" / "bin", h / ".config" / "autostart", h / ".config" / "systemd", h / ".config" / "hypr",
            h / ".config" / "sway", h / ".config" / "i3", h / ".config" / "fish", h / ".config" / "git"]
    files = [h / n for n in (".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".profile", ".zshrc", ".zprofile",
                             ".zshenv", ".zlogin", ".xprofile", ".xinitrc", ".xsession", ".gitconfig", ".npmrc", ".pypirc",
                             ".netrc", ".claude.json")]
    if WIN:
        appdata = Path(os.environ.get("APPDATA") or h / "AppData" / "Roaming")
        docs = h / "Documents"
        dirs += [appdata / "Microsoft" / "Windows" / "Start Menu", docs / "WindowsPowerShell", docs / "PowerShell"]
    if MAC:
        dirs += [h / "Library" / "LaunchAgents"]
    return [d.resolve() for d in dirs], [f.resolve() for f in files]


def edit_needs_card(path):
    """True for edits outside the project, in Void's / Claude's own files, startup places, shell profiles, keys, or a
    git repo's hooks and config."""
    if path is None:
        return True
    norm = (lambda x: str(x).lower()) if WIN or MAC else str     # (both ignore case: ~/.SSH is ~/.ssh)
    dirs, files = sensitive_paths()
    if any(norm(path) == norm(f) for f in files) or ".git" in path.parts:
        return True
    if any(norm(path) == norm(d) or norm(path).startswith(norm(d) + os.sep) for d in dirs):
        return True
    cwd = Path(ST.cwd).resolve()
    return not (norm(path) == norm(cwd) or norm(path).startswith(norm(cwd).rstrip(os.sep) + os.sep))


def is_void_see(cmd):
    """`void see` (look at the screen) runs without the approval card — but only when the command is exactly that:
    `void see`, `void see window` or `void see screen`, optionally with the full path of Void's own launcher. No
    pattern: a shell can do a lot before a `/void see` with no spaces at all."""
    launchers = {"void", "void.cmd", str(Path.home() / ".local" / "bin" / "void"), "/usr/bin/void", "/usr/local/bin/void"}
    if WIN:
        launchers |= {str(DATA_DIR / "bin" / "void"), str(DATA_DIR / "bin" / "void.cmd")}
    launchers |= {p.replace("\\", "/") for p in launchers}
    return cmd in {f"{l} see{w}" for l in launchers for w in ("", " window", " screen")}


# commands that run other commands: "always" for one of these would approve whatever is chained after it
CHAINED_RE = re.compile(r"&&|\|\||[;|&<>`\n]|\$\(")


class Tickets:
    """One-time, short-lived tickets: the browser fallback (no window to hand over the token) opens the page with one
    and trades it for the token once."""
    live, lock = {}, threading.Lock()

    @classmethod
    def new(cls):
        t = secrets.token_urlsafe(18)
        with cls.lock:
            cls.live[t] = time.monotonic() + 60
        return t

    @classmethod
    def claim(cls, t):
        with cls.lock:
            exp = cls.live.pop(t, None) if t else None
        return exp is not None and time.monotonic() < exp


class Approvals:
    def __init__(self):
        self.pending = {}
        self.always = set()                    # exact commands / tools approved for this chat (reset() on a new one)
        self.lock = threading.Lock()

    def reset(self):
        """A new chat, a resumed one, or another folder: "always" answers don't carry over."""
        with self.lock:
            self.always.clear()

    def ask(self, tool, inp, timeout=600):
        # the raw, complete command (summarize_input() shortens it and folds newlines away)
        cmd = str(inp.get("command") or "").strip() if tool == "Bash" and isinstance(inp, dict) else ""
        # what "always" covers: the whole command (never just its first word), the one file, the one website
        if tool == "Bash":
            key = "Bash:" + cmd
        elif tool in EDIT_TOOLS:
            key = "Edit:" + str(edit_path(inp))
        elif tool == "WebFetch":
            key = "WebFetch:" + (re.sub(r"^https?://", "", str((inp or {}).get("url") or "")).split("/")[0].split("@")[-1].lower()
                                 if isinstance(inp, dict) else "")
        else:
            key = tool
        can_always = not (tool == "Bash" and CHAINED_RE.search(cmd))
        if key in self.always:
            return {"ok": True, "why": "the user said yes to this exact command for this chat"}
        ev = threading.Event()
        aid = secrets.token_hex(4)
        item = {"id": aid, "tool": tool, "key": key, "detail": approval_text(tool, inp), "ev": ev, "ans": None}
        with self.lock:
            self.pending[aid] = item
        BUS.pub(t="approve", id=aid, tool=tool, detail=item["detail"], key=key, say=approval_say(tool, inp),
                always=can_always)
        ev.wait(timeout)
        with self.lock:
            self.pending.pop(aid, None)
        ans = item["ans"] or "no"
        if ans == "always" and not can_always:
            ans = "yes"                                           # just this once
        BUS.pub(t="approve_done", id=aid, ans=ans)
        if ans == "always":
            with self.lock:
                self.always.add(key)
        if ans in ("yes", "always"):
            return {"ok": True}
        if item["ans"] is None:
            return {"ok": False, "why": "the user didn't answer in time, so it wasn't run"}
        return {"ok": False}

    def answer(self, aid, ans):
        with self.lock:
            it = self.pending.get(aid)
        if it:
            it["ans"] = ans
            it["ev"].set()
        return bool(it)

    def cancel_all(self):
        with self.lock:
            items = list(self.pending.values())
        for it in items:
            it["ans"] = "no"
            it["ev"].set()


APPROVALS = Approvals()


class AskPass:
    """sudo's password, asked for in a small card right next to Void (askpass.py → here → the page). Every time — it's
    never remembered."""
    lock = threading.Lock()
    pending = {}
    last_given = 0.0

    @classmethod
    def ask(cls, prompt):
        now = time.monotonic()
        with cls.lock:
            aid = secrets.token_hex(4)
            ev = threading.Event()
            cls.pending[aid] = {"ev": ev, "pw": None}
        BUS.pub(t="askpass", id=aid, prompt=prompt[:120], retry=now - cls.last_given < 4)
        ev.wait(300)
        with cls.lock:
            it = cls.pending.pop(aid, {})
        BUS.pub(t="askpass_done", id=aid)
        return {"pw": it.get("pw")}

    @classmethod
    def answer(cls, aid, pw):
        with cls.lock:
            it = cls.pending.get(aid)
            if not it:
                return False
            it["pw"] = pw
            if pw is not None:
                cls.last_given = time.monotonic()
            it["ev"].set()
            return True

    @classmethod
    def forget(cls):
        """Root mode turned off: give up on any password card that's still waiting."""
        with cls.lock:
            for it in cls.pending.values():
                it["ev"].set()


def approval_text(tool, inp):
    if not isinstance(inp, dict):
        return tool
    if tool == "Bash":
        return inp.get("command") or "a command"
    if tool in ("WebFetch",):
        return inp.get("url") or "a web page"
    if tool == "WebSearch":
        return "search: " + (inp.get("query") or "")
    if tool in EDIT_TOOLS:
        return "change: " + str(edit_path(inp) or inp.get("file_path") or "a file")
    return summarize_input(inp) or tool


# ---------------------------------------------------------------- screenshots, clipboard, notifications
SHOTS = (winsys.cache_dir() if WIN else Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "void") / "shots"


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, timeout=kw.pop("timeout", 30), **kw)


def _active_window():
    """Where the window you're using is, as (x, y, w, h) on the whole desktop — or None if we can't tell (or it's Void)."""
    try:
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
            w = json.loads(_run(["hyprctl", "activewindow", "-j"], timeout=3).stdout or b"{}")
            if w.get("size") and w.get("class") != "Void":
                return (*w["at"], *w["size"])
        elif os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
            def find(n):
                if n.get("focused") and n.get("type") in ("con", "floating_con"):
                    return n
                for c in n.get("nodes", []) + n.get("floating_nodes", []):
                    f = find(c)
                    if f:
                        return f
            n = find(json.loads(_run(["swaymsg", "-t", "get_tree"], timeout=3).stdout or b"{}"))
            if n and n.get("app_id") != "Void":
                r = n["rect"]
                return r["x"], r["y"], r["width"], r["height"]
        elif not os.environ.get("WAYLAND_DISPLAY") and shutil.which("xdotool"):
            if _run(["xdotool", "getactivewindow", "getwindowclassname"], timeout=3).stdout.strip() != b"Void":
                g = dict(ln.split("=", 1) for ln in _run(["xdotool", "getactivewindow", "getwindowgeometry", "--shell"],
                                                          timeout=3).stdout.decode().split() if "=" in ln)
                return int(g["X"]), int(g["Y"]), int(g["WIDTH"]), int(g["HEIGHT"])
    except Exception as e:  # noqa: BLE001
        print(f"[void] couldn't find the active window: {e}", file=sys.stderr)
    return None


def _shrink(path):
    """Make a picture cheap for Claude: longest side ≤ 1280 px, JPEG. (Image tokens ≈ width × height / 750.)"""
    try:
        from PIL import Image
        im = Image.open(path)
        im.thumbnail((1280, 1280))
        small = path.with_suffix(".jpg")
        im.convert("RGB").save(small, quality=82)
        if small != path:
            path.unlink(missing_ok=True)
        return small
    except Exception as e:  # noqa: BLE001  (no Pillow / odd file: send it as it is)
        print(f"[void] couldn't shrink the picture: {e}", file=sys.stderr)
        return path


def capture(kind):
    """kind: window | screen | region | clipboard → dict describing the attachment."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    for old in sorted(SHOTS.glob("void-*.*"))[:-30]:        # keep the folder small
        old.unlink(missing_ok=True)
    out = SHOTS / f"void-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}.png"
    if OSYS:
        if kind == "clipboard":
            what, val = OSYS.clipboard(out)
            if what == "image":
                return {"kind": "clipimg", "path": str(_shrink(Path(val))), "label": "copied image"}
            if not val.strip():
                raise RuntimeError("the clipboard is empty")
            return {"kind": "clip", "text": val[:12000], "label": "copied text"}
        got = OSYS.screenshot(kind, out)
        return {"kind": got, "path": str(_shrink(out)),
                "label": {"screen": "screenshot", "window": "your window", "region": "screen area"}[got]}
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    geo = _active_window() if kind == "window" else None
    if kind == "window" and not geo and not (wayland and (shutil.which("spectacle") or shutil.which("gnome-screenshot"))):
        kind = "screen"                                       # can't tell which window: the whole screen it is
    if kind in ("window", "screen", "region"):
        area, win = kind == "region", kind == "window"
        gs = f"{geo[0]},{geo[1]} {geo[2]}x{geo[3]}" if geo else None
        # every desktop has its own screenshot tool: try the ones that are installed until one works
        tries = []
        if wayland:
            if shutil.which("grim") and (not area or shutil.which("slurp")):      # wlroots: Hyprland, sway, river, niri…
                tries.append("grim")
            tries += [t for t in ("spectacle", "gnome-screenshot") if shutil.which(t)]   # KDE · GNOME
        else:
            tries += [t for t in ("maim", "scrot", "import") if shutil.which(t)]
        if not tries:
            raise RuntimeError("no screenshot tool — install grim + slurp (Wayland), spectacle (KDE), "
                               "gnome-screenshot (GNOME) or maim (X11)")
        r = None
        for tool in tries:
            out.unlink(missing_ok=True)
            if tool == "grim":
                cmd = ["grim"]
                if area:
                    g = _run(["slurp"], timeout=120)
                    if g.returncode != 0:
                        raise RuntimeError("cancelled")
                    cmd += ["-g", g.stdout.decode().strip()]
                elif win and gs:
                    cmd += ["-g", gs]
                r = _run(cmd + [str(out)])
            elif tool == "spectacle":
                r = _run(["spectacle", "-b", "-n", "-r" if area else "-a" if win else "-f", "-o", str(out)], timeout=120)
            elif tool == "gnome-screenshot":
                r = _run(["gnome-screenshot"] + (["-a"] if area else ["-w"] if win else []) + ["-f", str(out)], timeout=120)
            elif tool == "maim":
                g = ["-g", f"{geo[2]}x{geo[3]}+{geo[0]}+{geo[1]}"] if win and geo else []
                r = _run(["maim"] + (["-s"] if area else g) + [str(out)], timeout=120)
            elif tool == "scrot":
                g = ["-a", f"{geo[0]},{geo[1]},{geo[2]},{geo[3]}"] if win and geo else []
                r = _run(["scrot"] + (["-s"] if area else g) + ["-o", str(out)], timeout=120)
            else:
                crop = ["-crop", f"{geo[2]}x{geo[3]}+{geo[0]}+{geo[1]}"] if win and geo else []
                r = _run(["import"] + ([] if area else ["-window", "root"] + crop) + [str(out)], timeout=120)
            if r.returncode == 0 and out.exists():
                break
        if r is None or r.returncode != 0 or not out.exists():
            raise RuntimeError("screenshot failed: " + (r.stderr or b"").decode(errors="replace")[-200:])
        return {"kind": kind, "path": str(_shrink(out)),
                "label": {"screen": "screenshot", "window": "your window", "region": "screen area"}[kind]}
    if kind == "clipboard":
        if wayland and shutil.which("wl-paste"):
            types = _run(["wl-paste", "--list-types"]).stdout.decode(errors="replace").split()
            img = next((t for t in types if t.startswith("image/")), None)
            if img:
                out.write_bytes(_run(["wl-paste", "--type", img]).stdout)
                return {"kind": "clipimg", "path": str(_shrink(out)), "label": "copied image"}
            r = _run(["wl-paste", "--no-newline"])
        elif shutil.which("xclip"):
            tg = _run(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"]).stdout.decode(errors="replace")
            if "image/png" in tg:
                out.write_bytes(_run(["xclip", "-selection", "clipboard", "-t", "image/png", "-o"]).stdout)
                return {"kind": "clipimg", "path": str(_shrink(out)), "label": "copied image"}
            r = _run(["xclip", "-selection", "clipboard", "-o"])
        elif shutil.which("xsel"):
            r = _run(["xsel", "-b", "-o"])
        else:
            raise RuntimeError("no clipboard tool — install wl-clipboard (Wayland) or xclip (X11)")
        text = r.stdout.decode(errors="replace").strip()
        if not text:
            raise RuntimeError("the clipboard is empty")
        return {"kind": "clip", "text": text[:12000], "label": f"clipboard ({len(text)} chars)"}
    raise RuntimeError("unknown capture")


def attach_prompt(atts):
    parts = []
    for a in atts or []:
        k = a.get("kind")
        if k in ("window", "screen", "region", "clipimg") and a.get("path") and Path(a["path"]).is_file():
            what = {"window": "a screenshot of the window they're using", "screen": "a screenshot of their whole screen",
                    "region": "a screenshot of part of their screen", "clipimg": "an image they copied"}[k]
            if a.get("live"):          # live view: a fresh picture comes with everything they say
                parts.append(f"[Live view is on — this is what the user is looking at right now: {a['path']}. "
                             "Open it with the Read tool only if their message is about something on screen.]")
            else:
                parts.append(f"[The user attached {what}: {a['path']} — open it with the Read tool to see it.]")
        elif k == "clip" and a.get("text"):
            parts.append("[What's on the user's clipboard:]\n```\n" + a["text"][:12000] + "\n```")
    return "\n".join(parts)


def notify(title, body):
    if OSYS:
        return OSYS.notify(title, body)
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "Void", "-i", str(STATIC / "icon.png"), title, body[:300]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wire_quick():
    quick.notify, quick.publish, quick.cfg, quick.save_config = notify, BUS.pub, CFG, save_config


# ---------------------------------------------------------------- speech to text
_whisper = None
_whisper_err = None
_whisper_lock = threading.Lock()


def _preload_cuda_libs():
    """faster-whisper needs cuBLAS + cuDNN for the GPU. The installer puts them in the venv (pip nvidia-* wheels);
    load them by full path so ctranslate2 finds them without LD_LIBRARY_PATH."""
    if WIN:
        winsys.add_cuda_dirs()
        return 1
    import ctypes
    import glob
    import site
    roots = site.getsitepackages() + [site.getusersitepackages()]
    loaded = 0
    for pat in ("nvidia/cublas/lib/libcublasLt.so.*", "nvidia/cublas/lib/libcublas.so.*",
                "nvidia/cuda_runtime/lib/libcudart.so.*", "nvidia/cudnn/lib/libcudnn*.so.*"):
        for r in roots:
            for f in sorted(glob.glob(os.path.join(r, pat))):
                try:
                    ctypes.CDLL(f, mode=ctypes.RTLD_GLOBAL)
                    loaded += 1
                except OSError:
                    pass
    return loaded


def _gpu_ok():
    if CFG["whisper_device"] == "cpu" or not (OSYS.gpu_ok() if OSYS else os.path.exists("/proc/driver/nvidia/version")):
        return False
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


def _compute_type(device):
    """float16 on GPUs that do it fast; older cards (GTX 10xx and before) can't, so take the best they can do
    instead of falling all the way back to the CPU."""
    if device != "cuda":
        return "int8"
    try:
        import ctranslate2
        have = ctranslate2.get_supported_compute_types("cuda")
    except Exception:  # noqa: BLE001
        return "float16"
    for ct in ("float16", "int8_float16", "int8_float32", "int8", "float32"):
        if ct in have:
            return ct
    return "default"


def _load(device):
    from faster_whisper import WhisperModel
    name = CFG["whisper_model"]
    if name in ("", "auto"):
        name = "small.en" if device == "cuda" else "base.en"
    ct = _compute_type(device)
    m = WhisperModel(name, device=device, compute_type=ct)
    m._void_name, m._void_dev, m._void_ct = name, device, ct
    print(f"[void] speech model {name} on {device} ({ct})", file=sys.stderr)
    return m


def whisper():
    global _whisper, _whisper_err
    with _whisper_lock:
        if _whisper is None and _whisper_err is None:
            try:
                import faster_whisper  # noqa: F401
            except Exception as e:  # noqa: BLE001
                _whisper_err = f"faster-whisper not installed ({e})"
            else:
                dev = "cpu"
                if _gpu_ok():
                    _preload_cuda_libs()
                    dev = "cuda"
                try:
                    _whisper = _load(dev)
                except Exception as e:  # noqa: BLE001
                    print(f"[void] whisper on {dev} failed ({e}); using CPU", file=sys.stderr)
                    try:
                        _whisper = _load("cpu")
                    except Exception as e2:  # noqa: BLE001
                        _whisper_err = str(e2)
        if _whisper is None:
            raise RuntimeError(_whisper_err or "speech model unavailable")
        return _whisper


def run_whisper(audio, beam=None, hint=None) -> str:
    """Transcribe; if the GPU path blows up at run time (missing CUDA libs etc.), drop to CPU for good."""
    global _whisper

    def go():
        segs, _ = whisper().transcribe(
            audio, language=CFG["language"] or None, beam_size=int(beam or CFG.get("whisper_beam") or 5),
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 600, "speech_pad_ms": 300},
            condition_on_previous_text=False, initial_prompt=(hint if hint is not None else CFG.get("stt_hint")) or None,
            no_speech_threshold=0.5)
        out = " ".join(s.text.strip() for s in segs).strip()      # segments are lazy: errors surface here
        # whisper sometimes "hears" the hint or filler on near-silence
        if out.strip(" .").lower() in ("", "you", "thank you", "thanks for watching", "bye"):
            return ""
        return out
    try:
        return go()
    except Exception as e:  # noqa: BLE001
        if not re.search(r"cuda|cublas|cudnn|libcu|gpu", str(e), re.I):
            raise
        print(f"[void] GPU speech-to-text failed ({e}); switching to CPU", file=sys.stderr)
        with _whisper_lock:
            _whisper = _load("cpu")
        return go()


def pcm_audio(pcm: bytes):
    """Raw s16le 16 kHz mono → float32 for Whisper, evened out: DC offset removed, a quiet mic brought up."""
    import numpy as np
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    audio -= float(audio.mean()) if len(audio) else 0.0   # DC offset some cheap mics have
    pk = float(np.abs(audio).max()) if len(audio) else 0.0
    if 0 < pk < 0.4:                                       # quiet mic → bring it up (but don't blow up hiss)
        audio *= min(0.7 / pk, 12.0)
    return audio


def transcribe_pcm(pcm: bytes, **kw) -> str:
    """Raw s16le 16 kHz mono → text."""
    if len(pcm) < 16000 * 2 * 0.3:          # < 0.3 s: nothing worth sending
        return ""
    return run_whisper(pcm_audio(pcm), **kw)


def speech_seconds(pcm: bytes) -> float:
    """How many seconds of `pcm` (s16le 16 kHz mono) Whisper's speech detector thinks are someone talking."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    ts = get_speech_timestamps(pcm_audio(pcm), VadOptions(min_silence_duration_ms=300))
    return sum(t["end"] - t["start"] for t in ts) / 16000


def level_db(pcm: bytes) -> float:
    """The loud bits of `pcm` (99.5th percentile), in dB below full scale."""
    import numpy as np
    a = np.abs(np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0)
    return 20 * float(np.log10(max(float(np.percentile(a, 99.5)) if len(a) else 0.0, 1e-5)))


# ---------------------------------------------------------------- microphone
RATE = 16000
FRAME = RATE * 2 * 40 // 1000               # 40 ms of s16le mono = 1280 bytes


class MicAuto:
    """Keeps your voice at a level he hears well. After you speak: if it came in quiet, turn the mic's Windows level up;
    if it clipped, turn it down. Once Windows is at 100% and you're still quiet, `soft` boosts what the speech detector
    sees (transcribe_pcm already evens out what Whisper gets)."""
    soft = 1.0
    last = 0.0
    last_peak = None                              # loudest moments of the last thing you said (for diagnostics)
    TARGET, QUIET, LOUD = 0.45, 0.18, 0.9         # loudest moments of your speech (0–1)

    @classmethod
    def learn(cls, pcm: bytes):
        if not CFG.get("mic_auto", True) or time.monotonic() - cls.last < 2 or len(pcm) < RATE * 2 * 0.3:
            return
        import numpy as np
        a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        a = np.abs(a - a.mean())
        pk = float(np.percentile(a, 99.9))        # loudest bits of your voice; a single click doesn't count
        clipped = float((a > 0.97).mean())
        if pk < 0.003:                            # dead or muted input: nothing to learn (Listen says so)
            return
        cls.last, cls.last_peak = time.monotonic(), pk
        hint = CFG.get("mic_device") or ""
        hw = OSYS.mic_level(hint) if OSYS else None
        if clipped > 0.002 or pk > cls.LOUD:
            if hw is not None and hw > 0.15:
                OSYS.set_mic_level(hw * 0.8, hint)
                print(f"[void] mic: your voice clipped — Windows mic level {hw:.0%} → {hw * 0.8:.0%}", file=sys.stderr)
            cls.soft = 1.0
        elif pk * cls.soft < cls.QUIET:
            want = min(2.0, cls.TARGET / pk)      # how much louder it should be, at most double per step
            if hw is not None and hw < 0.99:
                new = min(1.0, hw * want)
                OSYS.set_mic_level(new, hint)
                print(f"[void] mic: your voice came in quiet — Windows mic level {hw:.0%} → {new:.0%}", file=sys.stderr)
            else:
                cls.soft = min(8.0, cls.TARGET / pk)
                print(f"[void] mic: still quiet at full level — boosting it {cls.soft:.1f}×", file=sys.stderr)
        elif cls.soft > 1.0 and pk * cls.soft > 0.7:
            cls.soft = max(1.0, 0.5 / pk)


def mic_cmds():
    dev = (CFG.get("mic_device") or "").strip()
    cmds = []
    if CFG.get("mic_cmd"):
        cmds.append(shlex.split(CFG["mic_cmd"]))
    if shutil.which("parecord"):            # PulseAudio or PipeWire (pipewire-pulse)
        cmds.append(["parecord", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1", "--latency-msec=30"]
                    + ([f"--device={dev}"] if dev else []))
    if shutil.which("pw-record"):           # native PipeWire
        cmds.append(["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16"]
                    + (["--target", dev] if dev else []) + ["-"])
    if shutil.which("arecord"):             # plain ALSA
        cmds.append(["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "1"]
                    + (["-D", dev] if dev else []))
    return cmds


class LinuxMic:
    """A recorder program's output: read(timeout) → bytes (b"" = nothing yet; EOFError if it stopped), close(). On
    Windows and macOS, winsys.Mic has the same shape."""

    def __init__(self, proc, name):
        self.proc, self.name, self.fd = proc, name, proc.stdout.fileno()

    def read(self, timeout):
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return b""
        chunk = os.read(self.fd, 65536)
        if not chunk:
            raise EOFError
        return chunk

    def close(self):
        kill(self.proc)

    def err(self):
        return (self.proc.stderr.read() or b"").decode(errors="replace").strip()


def open_mic():
    """Start the first recorder that actually produces audio. Returns (mic, name, first_bytes)."""
    if OSYS:
        mic = OSYS.Mic(RATE, CFG.get("mic_device"))
        first = mic.read(2.5)
        if not first:
            mic.close()
            raise RuntimeError("couldn't open the mic — no sound came in (Settings → Privacy → Microphone?)")
        return mic, mic.name, first
    errs = []
    for c in mic_cmds():
        try:
            p = subprocess.Popen(c, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as e:
            errs.append(f"{c[0]}: {e}")
            continue
        r, _, _ = select.select([p.stdout], [], [], 2.5)
        first = os.read(p.stdout.fileno(), 65536) if r else b""
        if first:
            return LinuxMic(p, c[0]), c[0], first
        kill(p)
        err = (p.stderr.read() or b"").decode(errors="replace").strip().splitlines()
        errs.append(f"{c[0]}: {err[-1] if err else 'no audio came out'}")
    if not errs:
        raise RuntimeError("no recorder found — install libpulse (parecord), pipewire or alsa-utils")
    raise RuntimeError("couldn't open the mic — " + "; ".join(errs))


def spawn_kw():
    """Start a child in its own group, so stopping it also stops whatever it started."""
    return {"creationflags": 0x00000200 | winsys.NO_WINDOW} if WIN else {"start_new_session": True}


def kill(p):
    if p and p.poll() is None and WIN:
        p.kill()
        return
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            p.wait(timeout=1)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)


class Listen:
    """One mic session. mode 'auto' stops by itself after you stop talking; 'ptt' waits for stop()."""
    current = None
    lock = threading.Lock()

    def __init__(self, mode):
        self.mode = mode
        self.stop_ev = threading.Event()
        self.cancel = False

    def stop(self, cancel=False):
        self.cancel = self.cancel or cancel
        self.stop_ev.set()

    def run(self, emit):
        mic, name, buf = open_mic()
        emit(t="open", rec=name)
        pcm = bytearray()
        t0 = time.monotonic()
        floor, spoke, voiced, last_voice, peak, clipped, clip_run = None, False, 0, t0, 0.0, 0, 0
        reason = "stop"
        try:
            while True:
                while len(buf) >= FRAME:
                    fr, buf = buf[:FRAME], buf[FRAME:]
                    pcm += fr
                    n = len(fr) // 2
                    s = memoryview(fr).cast("h")
                    rms = (sum(x * x for x in s) / n) ** 0.5 / 32768.0 * MicAuto.soft
                    peak = max(peak, rms)
                    # only sustained clipping counts (≥3 frames in a row): a key click or a bump is a one-frame spike
                    c = sum(1 for x in s if x > 32500 or x < -32500)
                    clip_run = clip_run + 1 if c else 0
                    if clip_run >= 3:
                        clipped += c
                    now = time.monotonic()
                    # noise floor: settles over the first ~quarter second, then drifts slowly toward quiet
                    if floor is None:
                        floor = rms
                    elif now - t0 < 0.3 or rms < floor:
                        floor = floor * 0.8 + rms * 0.2
                    else:
                        floor = floor * 0.998 + rms * 0.002
                    thr = max(0.012, floor * 3.0)
                    voiced = min(4, voiced + 1) if rms > thr else max(0, voiced - 1)
                    if voiced >= 3:
                        if not spoke:
                            emit(t="speech")
                        spoke, last_voice = True, now
                    elif spoke and rms > thr * 0.6:
                        last_voice = now
                    emit(t="lvl", v=round(min(1.0, max(0.0, (rms - floor) * 12)), 3))
                    if self.mode == "auto":
                        if spoke and now - last_voice > 1.2:
                            reason = "done"
                        elif not spoke and now - t0 > 8:
                            reason, self.cancel = "nospeech", True
                    if now - t0 > 1.5 and peak < 0.0005:
                        reason = "silent"          # dead input: say so right away instead of waiting
                    if now - t0 > 120:
                        reason = "toolong"
                    if reason != "stop":
                        self.stop_ev.set()
                if self.stop_ev.is_set():
                    break
                try:
                    buf += mic.read(0.1)
                except EOFError:
                    if len(pcm) < FRAME * 10:
                        raise RuntimeError(f"{name} stopped unexpectedly {mic.err()[-200:]}".strip())
                    break
        finally:
            mic.close()
        if peak < 0.0005:
            emit(t="error", text="the mic is completely silent — is the right input selected/unmuted? "
                                 "(`void doctor` lists inputs; set mic_device in the config)")
            return
        if spoke or (floor is not None and peak > max(0.001, floor * 2.5)):   # you said something, maybe too quietly
            MicAuto.learn(bytes(pcm))
        if self.cancel:
            emit(t="cancel", why=reason)
            return
        if clipped > RATE * 0.05:
            emit(t="note", text="your mic is clipping (too loud) — turn the input down to ~65% for better recognition")
        emit(t="transcribing")
        emit(t="text", text=transcribe_pcm(bytes(pcm)))


def reset_whisper():
    global _whisper, _whisper_err
    with _whisper_lock:
        _whisper, _whisper_err = None, None
    threading.Thread(target=lambda: _try(whisper), daemon=True).start()


def list_mics():
    if OSYS:
        return OSYS.list_mics()
    if not shutil.which("pactl"):
        return []
    out = subprocess.run(["pactl", "-f", "json", "list", "sources"], capture_output=True, text=True).stdout
    try:
        return [{"id": s["name"], "label": s.get("description") or s["name"]} for s in json.loads(out)
                if ".monitor" not in s.get("name", "")]
    except ValueError:
        out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True).stdout
        return [{"id": ln.split("\t")[1], "label": ln.split("\t")[1]} for ln in out.splitlines()
                if "\t" in ln and ".monitor" not in ln]


STOP_RE = re.compile(r"(?:ok(?:ay)?\W+|void\W+|hey\W+void\W+)?(?:stop|wait|shush|shh+|quiet|be quiet|shut up|hold on|hang on|"
                     r"enough|stop talking|that's enough|thanks?(?: you)?|got it)(?:\W+(?:void|please|now))?")
# sent ahead of a follow-up (said without "hey Void") so Claude can tell if it was even meant for him
FOLLOW_NOTE = ("(Voice note from Void: the mic picked this up a few seconds after your last reply, without \"hey Void\". "
               "If it is clearly not meant for you, e.g. the user thinking out loud, talking to themselves or to someone "
               "else, reply with exactly [ignore] and nothing else, and use no tools. Otherwise just answer it normally.)\n\n")
IGNORE = "[ignore]"

# what the speech model "hears" in background noise — never treat these as a follow-up request
NOISE_WORDS = {"you", "thank you", "thanks", "bye", "thanks for watching", "thank you for watching", "uh", "um", "hmm"}
WAKE_RE = re.compile(r"^\W*(?:(?:hey|hi|hay|okay|ok|yo|oi)\W+)?(void|voyd|boyd|voi|vod)\b\W*(.*)$", re.I | re.S)


class Wake:
    """Optional "hey Void": keeps a recorder open, cuts out short bursts of speech, and checks them for his name.
    Steps aside whenever the normal mic session runs (or while he's talking, so he can't wake himself)."""
    thread = None
    speaking_until = 0.0
    follow_from = follow_until = 0.0         # the "just talk, no 'hey Void'" window right after he stops talking
    follow_on = False                        # what the page was last told (it shows him listening while it's open)
    cut = None                               # you clicked him ("finish") or pressed Space/Esc ("cancel") while he heard you
    asleep = False                           # he's hidden: the mic stays closed until he's shown again

    @classmethod
    def wanted(cls):
        return bool(CFG.get("wake_word") or CFG.get("barge_in") or CFG.get("follow_up"))

    @classmethod
    def speaking(cls):
        return time.monotonic() < cls.speaking_until

    @classmethod
    def talking_until(cls, end):
        """He's talking until `end`; the follow-up window opens then."""
        cls.speaking_until = cls.follow_from = end
        cls.follow_until = end + float(CFG.get("follow_up") or 0)

    @classmethod
    def end_follow(cls, how):
        """Close the follow-up window. "finish": take what you've said so far · "cancel": throw it away."""
        cls.cut = how
        if how != "finish":
            cls.follow_until = 0.0

    @classmethod
    def hush(cls):
        """He was cut off: no follow-up window."""
        cls.speaking_until = cls.follow_until = 0.0

    @classmethod
    def ensure(cls):
        if cls.wanted() and not (cls.thread and cls.thread.is_alive()):
            cls.thread = threading.Thread(target=cls.loop, daemon=True)
            cls.thread.start()

    @classmethod
    def should_run(cls):
        if cls.asleep or Listen.lock.locked():
            return False
        if cls.speaking():                       # while he talks: only to catch "stop" / "hey Void" (barge-in)
            return bool(CFG.get("barge_in"))
        return bool(CFG.get("wake_word")) or time.monotonic() < cls.follow_until

    @classmethod
    def loop(cls):
        last = None
        while cls.wanted():
            if not cls.should_run():
                time.sleep(0.2)
                continue
            try:
                cls.session()
                last = None
            except Exception as e:  # noqa: BLE001
                if str(e) != last:                   # (no mic plugged in: say so once, then check now and then)
                    print(f"[void] wake word: {e}", file=sys.stderr)
                time.sleep(5 if str(e) != last else 30)
                last = str(e)

    @classmethod
    def session(cls):
        mic, _name, buf = open_mic()
        floor, voiced, seg, in_speech, last_voice, started = None, 0, bytearray(), False, 0.0, 0.0
        pre, n = bytearray(), 0
        cls.cut = None
        try:
            while cls.should_run():
                try:
                    buf += mic.read(0.2)
                except EOFError:
                    return
                while len(buf) >= FRAME:
                    fr, buf = buf[:FRAME], buf[FRAME:]
                    s = memoryview(fr).cast("h")
                    rms = (sum(x * x for x in s) / (len(fr) // 2)) ** 0.5 / 32768.0 * MicAuto.soft
                    floor = rms if floor is None else (floor * 0.9 + rms * 0.1 if rms < floor else floor * 0.999 + rms * 0.001)
                    thr = max(0.015, floor * 3.2)
                    voiced = min(4, voiced + 1) if rms > thr else max(0, voiced - 1)
                    now = time.monotonic()
                    if cls.cut:                          # clicked (finish now) / Space or Esc (drop it)
                        how, cls.cut = cls.cut, None
                        if in_speech and how == "finish" and len(seg) > RATE * 2 * 0.35:
                            cls.check(bytes(seg), started)
                        in_speech, seg, cls.follow_until = False, bytearray(), 0.0
                    # you started talking inside the follow-up window: show him hearing you, ring/eyes move with your voice
                    fol = in_speech and not cls.speaking() and cls.follow_from <= started < cls.follow_until
                    if fol != cls.follow_on:
                        cls.follow_on = fol
                        BUS.pub(t="follow", on=fol)
                    n += 1
                    if fol and n % 2 == 0:
                        BUS.pub(t="hear", v=round(min(1.0, max(0.0, (rms - floor) * 12)), 3))
                    if in_speech:
                        seg += fr
                        if rms > thr * 0.6:
                            last_voice = now
                        if now - last_voice > 0.7 or len(seg) > RATE * 2 * 12:
                            in_speech = False
                            if len(seg) > RATE * 2 * 0.35:
                                cls.check(bytes(seg), started)
                            seg = bytearray()
                    else:
                        pre = (pre + fr)[-FRAME * 8:]
                        if voiced >= 3:
                            in_speech, last_voice, seg, started = True, now, bytearray(pre), now
        finally:
            if cls.follow_on:
                cls.follow_on = False
                BUS.pub(t="follow", on=False)
            mic.close()

    @classmethod
    def check(cls, pcm, started):
        # started after he finished talking, inside the follow-up window → it's a request, no "hey Void" needed
        follow = not cls.speaking() and cls.follow_from <= started < cls.follow_until
        if not cls.speaking():                   # (while he talks the mic also hears him: don't tune to his voice)
            MicAuto.learn(pcm)
        try:
            text = transcribe_pcm(pcm) if follow else transcribe_pcm(pcm, beam=1, hint="Hey Void.")
        except Exception as e:  # noqa: BLE001
            print(f"[void] wake word stt: {e}", file=sys.stderr)
            return
        m = WAKE_RE.match(text or "")
        if cls.speaking():
            if STOP_RE.fullmatch((text or "").strip(" .,!?").lower()) or m:
                BUS.pub(t="barge", listen=bool(m), text=(m.group(2).strip(" .,!?") if m else ""))
                cls.hush()
            return
        if m:
            rest = m.group(2).strip(" .,!?")
            BUS.pub(t="wake", text=rest)
        elif follow:
            said = (text or "").strip(" .,!?")
            if said and said.lower() not in NOISE_WORDS:
                cls.follow_until = 0.0
                BUS.pub(t="wake", text=said, follow=True)


def doctor():
    print(f"Void doctor (version {VERSION})\n")
    if OSYS:
        print("inputs (any part of a name works as mic_device):")
        for m in OSYS.list_mics():
            print("  " + m["label"])
        print()
    elif shutil.which("pactl"):
        out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True).stdout
        dflt = subprocess.run(["pactl", "get-default-source"], capture_output=True, text=True).stdout.strip()
        print("inputs (use the 2nd column as mic_device):")
        for ln in out.splitlines():
            if ".monitor" not in ln:
                print("  " + ln + ("   <- default" if dflt and dflt in ln else ""))
        print()
    print("recorders tried in order:", "sounddevice" if OSYS else (", ".join(c[0] for c in mic_cmds()) or "NONE"))
    print("recording 3 seconds — keep talking the whole time…", flush=True)
    try:
        if OSYS:                                    # (keep every channel, to see which one your voice is on)
            mic = OSYS.Mic(RATE, CFG.get("mic_device"), keep_raw=True)
            name, buf = mic.name, b""
        else:
            mic, name, buf = open_mic()
        t0, data = time.monotonic(), bytearray(buf)
        while time.monotonic() - t0 < 3:
            try:
                data += mic.read(0.2)
            except EOFError:
                break
        mic.close()
        data = bytes(data[: len(data) // 2 * 2])
        if OSYS:
            i = mic.info
            ch, switched = mic.channel
            print(f"  device: {i['name']} · {i['driver']} · {i['channels']} channel(s) · {i['rate']} Hz"
                  + (f" · recording channel {ch}" + (" (the other is near silent)" if switched else "")
                     if mic.channels == 2 else ""))
        s = memoryview(data).cast("h")
        peak = max((abs(x) for x in s), default=0) / 32768
        bar = "#" * int(min(1, peak * 3) * 30)
        print(f"  {name}: {len(data) / 32000:.1f}s captured, loudest {peak:.3f}  [{bar:<30}]")
        # what each channel carries: how loud, and whether there's any talking in it at all
        chans = {"": data}
        if OSYS and mic.channels == 2 and mic.raw:
            import numpy as np
            st = np.frombuffer(bytes(mic.raw[: len(mic.raw) // 4 * 4]), dtype=np.int16).reshape(-1, 2)
            chans = {f"channel {c + 1}": np.ascontiguousarray(st[:, c]).tobytes() for c in (0, 1)}
        talk = {}
        for k, pcm in chans.items():
            try:
                talk[k] = speech_seconds(pcm)
            except Exception:  # noqa: BLE001
                talk[k] = None
            if k:
                print(f"  {k}: level {level_db(pcm):.0f} dB · talking for "
                      + ("?" if talk[k] is None else f"{talk[k]:.1f}s"))
        heard_any = any(v for v in talk.values())
        wav = DATA_DIR / "doctor.wav"                 # what he heard, so you can listen to it yourself
        try:
            import wave as _w
            with _w.open(str(wav), "wb") as w:
                raw = bytes(mic.raw) if OSYS and mic.channels == 2 and mic.raw else data
                w.setnchannels(2 if raw is not data else 1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(raw)
        except Exception:  # noqa: BLE001
            wav = None
        if peak > 0.98:
            print("  -> CLIPPING: input is too loud — turn the mic down to ~65% in Sound settings" if OSYS else
                  "  -> CLIPPING: input is too loud — try: wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 0.65")
        elif talk.get("") == 0 or (len(talk) == 2 and not heard_any):
            print("  -> NO VOICE in what this mic sends, only background noise. Is the mic muted on the headset "
                  "(a button, or its own app, e.g. INZONE Hub / Logitech G HUB)? Is it the mic you're talking into? "
                  "Pick another in Settings → Voice.")
        elif len(talk) == 2 and talk.get("channel 2") and not talk.get("channel 1"):
            print("  -> your voice is on channel 2 only — Void records that one (since 1.5.4)")
        elif peak <= 0.02:
            print("  -> very quiet: wrong input, muted, or gain too low")
        else:
            print("  -> looks good")
        if wav:
            print(f"  (what he heard is saved in {wav} — play it to hear it yourself)")
        if peak > 0.002:
            try:
                heard = transcribe_pcm(data)
                print("  heard:", repr(heard))
                if not heard and heard_any:
                    print("  -> there's talking but no words came out: speak a bit closer to the mic, in English "
                          "(or set language in Settings)")
                print(f"  (speech model: {_whisper._void_name} on {_whisper._void_dev}, {_whisper._void_ct})")
            except Exception as e:  # noqa: BLE001
                print("  speech-to-text failed:", e)
    except Exception as e:  # noqa: BLE001
        print("  MIC FAILED:", e)
    if WIN:
        lv = winsys.mic_level(CFG.get("mic_device") or "")
        print(f"  Windows mic level: {'?' if lv is None else f'{lv:.0%}'}"
              + ("  (Void adjusts it as you talk: mic_auto)" if CFG.get("mic_auto", True) else ""))
    print("\nvoice:", tts_engine() or "none (install espeak-ng / piper)")
    print("claude:", claude_bin() or "NOT FOUND")


# ---------------------------------------------------------------- text to speech
def piper_bin():
    local = Path(sys.executable).parent / ("piper.exe" if WIN else "piper")
    if local.exists():
        return str(local)
    return shutil.which("piper") or shutil.which("piper-tts")


def piper_voice():
    v = CFG["piper_voice"]
    if v:
        v = os.path.expanduser(v)
        return v if os.path.exists(v) else None
    voices = sorted((DATA_DIR / "voices").glob("*.onnx"))
    return str(voices[0]) if voices else None


def tts_engine():
    mode = CFG["tts"]
    if mode == "off":
        return None
    if mode in ("auto", "kokoro") and kokoro_ready():
        return "kokoro"
    if mode in ("auto", "piper") and piper_bin() and piper_voice():
        return "piper"
    if mode in ("auto", "espeak") and shutil.which("espeak-ng"):
        return "espeak"
    return None


PATH_RE = re.compile(r"(?<![\w.])(?:~|/)[\w.\-]*(?:/[\w.\-]+)+/?|(?<![\w.])/(?:home|etc|usr|tmp|opt|var)\b")


def say_path(p):
    """/home/you/Downloads → "your Downloads folder" · ~/code/void/app/server.py → "server.py" (how a person says it)."""
    parts = [x for x in p.split("/") if x]
    home = parts[:1] == ["~"] or parts[:1] == ["home"]
    parts = parts[1:] if parts[:1] == ["~"] else parts[2:] if parts[:1] == ["home"] else parts
    if not parts:
        return "your home folder" if home else "the main folder"
    last = parts[-1]
    if "." in last.strip("."):                       # a file: just its name
        return last
    return f"your {last} folder" if home and len(parts) == 1 else f"the {last} folder"


def speakable(t: str) -> str:
    t = untag(t)
    t = re.sub(r"```.*?```", " I put some code on screen. ", t, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = PATH_RE.sub(lambda m: say_path(m.group(0).rstrip(".")) + "." * (len(m.group(0)) - len(m.group(0).rstrip("."))), t)
    t = re.sub(r"^\s*(?:[#>*+\-]+|\d+\.)\s*", "", t, flags=re.M)
    t = re.sub(r"[*_#|~]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    lim = int(CFG["speak_max_chars"])
    if len(t) > lim:
        cut = t[:lim]
        i = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        t = (cut[: i + 1] if i > lim // 3 else cut) + " The rest is on screen."
    return t


# Voice presets. `pitch` is applied on playback (played faster/slower);
# piper is asked to speak slower/faster by the same factor so the talking speed stays natural.
# With Kokoro (the default when it's installed) each preset is one of its voices; `kpitch` is the pitch nudge for it.
VOICES = {
    "girl":     {"label": "Girl",        "kokoro": "af_heart",   "piper": "en_US-amy-medium",  "pitch": 1.0},
    "cute":     {"label": "Cute",        "kokoro": "af_bella",   "piper": "en_US-amy-medium",  "pitch": 1.28, "kpitch": 1.12},
    "guy":      {"label": "Guy",         "kokoro": "am_michael", "piper": "en_US-ryan-medium", "pitch": 1.0},
    "deep":     {"label": "Deep",        "kokoro": "am_onyx",    "piper": "en_US-ryan-medium", "pitch": 0.82, "kpitch": 0.94},
    "british":  {"label": "British",     "kokoro": "bm_george",  "piper": "en_GB-alan-medium", "pitch": 1.0},
    "story":    {"label": "Storyteller", "kokoro": "bm_fable",   "piper": "en_GB-alan-medium", "pitch": 1.0},
    "robot":    {"label": "Robot",       "espeak": "en-us+m3",   "pitch": 0.9},
}

# ---- Kokoro (kokoro-onnx): a much more natural voice than piper, still fully local. Small int8 model (~120 MB total).
KOKORO_DIR = DATA_DIR / "voices" / "kokoro"
KOKORO_FILES = {"kokoro-v1.0.int8.onnx": "model", "voices-v1.0.bin": "voices"}
_KOKORO = {}
_downloading = set()


def kokoro_installed():
    import importlib.util
    return importlib.util.find_spec("kokoro_onnx") is not None


def kokoro_ready():
    return all((KOKORO_DIR / f).exists() for f in KOKORO_FILES) and kokoro_installed()


def fetch_kokoro():
    """Download Kokoro's model files in the background (the installer normally does this)."""
    if "kokoro" in _downloading or not kokoro_installed() or all((KOKORO_DIR / f).exists() for f in KOKORO_FILES):
        return
    _downloading.add("kokoro")

    def go():
        import kokoro_fetch   # GitHub first, Hugging Face if that's blocked
        try:
            src = kokoro_fetch.fetch(KOKORO_DIR)
            print(f"[void] kokoro voice ready (from {src})", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[void] kokoro download failed: {e}", file=sys.stderr)
        finally:
            _downloading.discard("kokoro")
    threading.Thread(target=go, daemon=True).start()


def kokoro_synth(text, voice, speed):
    """→ wav bytes (16-bit mono, 24 kHz)"""
    from kokoro_onnx import Kokoro
    with _TTS_LOCK:
        k = _KOKORO.get("k")
        if k is None:
            k = _KOKORO["k"] = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.int8.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
        lang = "en-gb" if voice[:1] == "b" else "en-us"
        samples, sr = k.create(text, voice=voice, speed=max(0.5, min(2.0, speed)), lang=lang)
    import numpy as np
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm)
    return buf.getvalue()


def voice_preset():
    return VOICES.get(CFG.get("voice") or "girl", VOICES["girl"])


def voice_file(name):
    return DATA_DIR / "voices" / f"{name}.onnx"


def fetch_voice(name):
    """Download a piper voice in the background (same source the installer uses)."""
    if name in _downloading or voice_file(name).exists():
        return
    _downloading.add(name)

    def go():
        import urllib.request
        try:
            loc, spk, q = name.split("-")
            base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{loc.split('_')[0]}/{loc}/{spk}/{q}/{name}"
            d = DATA_DIR / "voices"
            d.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(base + ".onnx.json", d / f"{name}.onnx.json")
            urllib.request.urlretrieve(base + ".onnx", d / f"{name}.onnx.part")
            (d / f"{name}.onnx.part").rename(d / f"{name}.onnx")
            print(f"[void] voice {name} ready", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[void] voice download {name} failed: {e}", file=sys.stderr)
        finally:
            _downloading.discard(name)
    threading.Thread(target=go, daemon=True).start()


_piper_speed_flag = None


def piper_run(model, text, out, length_scale):
    global _piper_speed_flag
    base = [piper_bin(), "-m", model, "-f", out]
    flags = [_piper_speed_flag] if _piper_speed_flag else ["--length-scale", "--length_scale"]
    if abs(length_scale - 1) > 0.01:
        for f in flags:
            r = subprocess.run(base + [f, f"{length_scale:.3f}"], input=text, text=True, capture_output=True, timeout=90)
            if r.returncode == 0:
                _piper_speed_flag = f
                return
        print("[void] piper doesn't take a speed flag; pitch preset will also change speed", file=sys.stderr)
    subprocess.run(base, input=text, text=True, capture_output=True, timeout=90, check=True)


# ---- piper, loaded once and kept in memory. Running the piper CLI for every sentence reloads the voice model each
# time (~1 s); the next sentence then isn't ready when the last one ends and his speech stutters.
_PIPER = {}
_TTS_LOCK = threading.Lock()


def piper_inproc(model, text, length_scale):
    """Synthesize with the piper Python package (1.2 or 1.3 API). → wav bytes, or None if it isn't usable."""
    try:
        import piper as _pp
        v = _PIPER.get(model)
        if v is None:
            v = _PIPER[model] = _pp.PiperVoice.load(model)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            if hasattr(v, "synthesize_wav"):                      # piper-tts 1.3+
                cfg = None
                if abs(length_scale - 1) > 0.01:                    # otherwise keep the voice's own pacing
                    try:
                        cfg = _pp.SynthesisConfig(length_scale=length_scale)
                    except Exception:  # noqa: BLE001
                        pass
                v.synthesize_wav(text, wf, syn_config=cfg) if cfg else v.synthesize_wav(text, wf)
            else:                                                  # piper-tts 1.2
                v.synthesize(text, wf, **({"length_scale": length_scale} if abs(length_scale - 1) > 0.01 else {}))
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        if not _PIPER.get("_warned"):
            _PIPER["_warned"] = True
            print(f"[void] piper in-process unavailable ({e}); using the piper command instead", file=sys.stderr)
        return None


def warm_tts():
    """Load the voice in the background at start-up so his first sentence isn't slow."""
    try:
        if CFG.get("tts", "auto") in ("auto", "kokoro"):
            fetch_kokoro()
        if tts_engine() in ("piper", "kokoro"):
            synth("Hi.")
    except Exception as e:  # noqa: BLE001
        print(f"[void] couldn't warm up the voice: {e}", file=sys.stderr)


def _syllables(w):
    w = w.lower()
    digits = sum(c.isdigit() for c in w)
    letters = re.sub(r"[^a-z]", "", w)
    v = len(re.findall(r"[aeiouy]+", letters))
    if letters.endswith("e") and v > 1 and not letters.endswith(("le", "ee")):
        v -= 1
    return max(1 if letters else 0, v) + digits * 1.1 + (0.4 if len(letters) > 7 else 0) + (0.3 if not letters and not digits else 0)


def tidy_and_time(wav, text):
    w2, times, _env = tidy_full(wav, text)[:3]
    return w2, times


def tidy_full(wav, text):
    """Trim the silence piper puts before/after each sentence (that's dead air between sentences) and work out when
    each word is spoken: find where the voice actually starts and stops and the pauses at commas/full stops, then
    spread the words between them by syllables. → (wav bytes, [[start, end], …] seconds, or None)"""
    try:
        with wave.open(io.BytesIO(wav)) as w:
            sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            raw = w.readframes(n)
        if sw != 2 or not n:
            return wav, None, None
        a = array.array("h", raw)
        if sys.byteorder == "big":
            a.byteswap()
        if ch > 1:
            a = a[::ch]
        hop = max(1, sr // 100)                                   # 10 ms frames
        env = []
        try:
            import numpy as _np
            arr = _np.frombuffer(a.tobytes(), dtype=_np.int16).astype(_np.float32)
            nfr = len(arr) // hop
            if nfr:
                env = list(_np.sqrt((arr[:nfr * hop].reshape(nfr, hop) ** 2).mean(axis=1)))
        except ImportError:
            for i in range(0, len(a) - hop + 1, hop):
                seg = a[i:i + hop:4]
                env.append(math.sqrt(sum(x * x for x in seg) / len(seg)) if len(seg) else 0)
        if not env:
            return wav, None, None
        thr = max(max(env) * 0.06, 90)
        on = [e > thr for e in env]
        if not any(on):
            return wav, None, None
        f0 = on.index(True)
        f1 = len(on) - on[::-1].index(True)
        k0, k1 = max(0, f0 - 3), min(len(on), f1 + 5)             # keep 30 ms before, 50 ms after
        out = a[k0 * hop:k1 * hop]
        if sys.byteorder == "big":
            out.byteswap()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(out.tobytes())
        # pauses (≥ 80 ms of quiet) inside the speech
        gaps, i = [], f0
        while i < f1:
            if not on[i]:
                j = i
                while j < f1 and not on[j]:
                    j += 1
                if j - i >= 8:
                    gaps.append([(i - k0) / 100, (j - k0) / 100])
                i = j
            else:
                i += 1
        a0, a1 = (f0 - k0) / 100, (f1 - k0) / 100
        lv = env[k0:k1]                                           # loudness every 40 ms, 0-100 (drives his "mouth")
        level = [min(100, round(max(lv[i:i + 4]) / 32768 * 550)) for i in range(0, len(lv), 4)]
        words = text.split()
        if not words:
            return buf.getvalue(), None, level
        wt = [_syllables(w) + 0.35 for w in words]
        pz = [2.2 if re.search(r"[.!?]['\")]*$", w) else 1.2 if re.search(r"[,;:—–]['\")]*$", w) else 0 for w in words]
        tot = sum(wt) + sum(pz[:-1]) or 1
        # anchor phrase ends to real pauses near where we'd expect them
        anchors, used, acc = [], set(), 0.0
        for i in range(len(words) - 1):
            acc += wt[i]
            if pz[i]:
                exp = a0 + (a1 - a0) * acc / tot
                best = min((g for gi, g in enumerate(gaps) if gi not in used), key=lambda g: abs((g[0] + g[1]) / 2 - exp), default=None)
                if best and abs((best[0] + best[1]) / 2 - exp) < 0.5:
                    used.add(gaps.index(best))
                    anchors.append((i, best[0], best[1]))
            acc += pz[i]
        anchors.sort(key=lambda x: x[1])
        times, start_i, t_start = [None] * len(words), 0, a0
        for idx, g0, g1 in anchors + [(len(words) - 1, a1, a1)]:
            if idx < start_i or g0 <= t_start:
                continue
            seg = range(start_i, idx + 1)
            sw_ = sum(wt[k] for k in seg) or 1
            t = t_start
            for k in seg:
                d = (g0 - t_start) * wt[k] / sw_
                times[k] = [round(t, 3), round(t + d, 3)]
                t += d
            start_i, t_start = idx + 1, g1
        if any(x is None for x in times):                         # anchors out of order: plain spread instead
            t, times = a0, []
            for k in range(len(words)):
                d = (a1 - a0) * (wt[k] + (pz[k] if k < len(words) - 1 else 0)) / tot
                times.append([round(t, 3), round(t + d * wt[k] / (wt[k] + (pz[k] if k < len(words) - 1 else 0)), 3)])
                t += d
        return buf.getvalue(), times, level
    except Exception as e:  # noqa: BLE001
        print(f"[void] word timing failed: {e}", file=sys.stderr)
        return wav, None, None


def audio_player_cmd(rate):
    """A plain audio player that takes raw 16-bit mono on stdin."""
    if shutil.which("paplay"):
        return ["paplay", "--raw", f"--rate={rate}", "--channels=1", "--format=s16le", "--latency-msec=100",
                "--client-name=Void", "--stream-name=Void voice"]
    if shutil.which("aplay"):
        return ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(rate), "-c", "1", "--buffer-time=120000", "-"]
    return None


def make_player(rate):
    """Something that takes raw 16-bit mono on .stdin: paplay/aplay on Linux, sounddevice on Windows and macOS."""
    if OSYS:
        return OSYS.Player(rate)
    return subprocess.Popen(audio_player_cmd(rate), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


class Speaker:
    """Plays his voice straight to PipeWire/Pulse, not through the window (the window's drawing can starve audio).

    Two workers, so nothing ever waits on anything else:
      * the maker turns upcoming sentences into audio ahead of time (up to 3 ready), in order;
      * the player streams finished audio to the speakers, keeping ~0.8 s buffered — so even if Void (or the whole
        computer) is busy for a moment, there's still sound queued up and nothing stutters.
    It always knows when each sentence will start playing and tells the page, which times the captions and his face
    from that. If it ever does run dry, it counts it (see `void stress`) and shifts its clock so sync stays right."""
    cv = threading.Condition()
    todo = []            # (gen, id, text) waiting to be made
    ready = []           # (gen, id, text, pcm, prate, words, level) waiting to be played
    making = False
    gen = 0
    proc = None
    prate = None
    t0 = 0.0
    written = 0
    threads = []
    LEAD = 0.8           # seconds of audio kept buffered ahead of the speakers
    filled = False
    LAT = 0.12           # roughly how long the audio server takes to get it out of the speakers
    stats = {}

    @classmethod
    def reset_stats(cls):
        cls.stats = {"underruns": 0, "worst_gap_ms": 0.0, "min_ahead_ms": None, "sentences": 0, "audio_s": 0.0,
                     "worst_rtf": 0.0, "slowest_make_s": 0.0}

    @classmethod
    def available(cls):
        return CFG.get("voice_out", "auto") != "page" and (OSYS.can_play() if OSYS else audio_player_cmd(22050) is not None)

    @classmethod
    def _start(cls):
        if not cls.stats:
            cls.reset_stats()
        if not cls.threads or not all(t.is_alive() for t in cls.threads):
            cls.threads = [threading.Thread(target=cls._maker, daemon=True), threading.Thread(target=cls._player_loop, daemon=True)]
            for t in cls.threads:
                t.start()

    @classmethod
    def say(cls, text, sid):
        with cls.cv:
            cls.todo.append((cls.gen, sid, text))
            if all(isinstance(x[1], int) for x in cls.todo):     # sentences can arrive out of order: play them in order
                cls.todo.sort(key=lambda x: (x[0], x[1]))
            cls.cv.notify_all()
        cls._start()

    @classmethod
    def idle(cls):
        with cls.cv:
            return not cls.todo and not cls.ready and not cls.making and cls.written / (cls.prate or 1) < time.monotonic() - cls.t0

    @classmethod
    def stop(cls):
        with cls.cv:
            cls.gen += 1
            cls.todo.clear()
            cls.ready.clear()
            p, cls.proc = cls.proc, None
            cls.written, cls.t0 = 0, 0.0
            cls.cv.notify_all()
        if p:
            try:
                p.kill()
            except OSError:
                pass
        Wake.hush()

    # ---- worker 1: make the audio ahead of time
    @classmethod
    def _maker(cls):
        while True:
            with cls.cv:
                while not cls.todo or len(cls.ready) >= 3:
                    cls.cv.wait()
                g, sid, text = cls.todo.pop(0)
                cls.making = True
            try:
                if g != cls.gen:
                    continue
                t = time.monotonic()
                wav, rate = synth(text)
                wav, times, level = tidy_full(wav, text)
                with wave.open(io.BytesIO(wav)) as w:
                    sr, pcm = w.getframerate(), w.readframes(w.getnframes())
                took = time.monotonic() - t
                prate = int(round(sr * rate))                     # the pitch preset: play it a touch faster/slower
                dur = len(pcm) / 2 / prate
                st = cls.stats
                st["slowest_make_s"] = max(st.get("slowest_make_s", 0), round(took, 3))
                if dur > 0.3:
                    st["worst_rtf"] = max(st.get("worst_rtf", 0), round(took / dur, 3))
                words = [[round(a * sr / prate, 3), round(b * sr / prate, 3)] for a, b in times] if times else None
                with cls.cv:
                    if g == cls.gen:
                        cls.ready.append((g, sid, text, pcm, prate, words, level, round(40 * sr / prate, 2)))
                        cls.cv.notify_all()
            except Exception as e:  # noqa: BLE001
                print(f"[void] speech: {e}", file=sys.stderr)
                BUS.pub(t="spk_skip", id=sid)
            finally:
                with cls.cv:
                    cls.making = False
                    cls.cv.notify_all()

    # ---- worker 2: play it
    @classmethod
    def _player(cls, rate):
        if cls.proc and cls.proc.poll() is None and cls.prate == rate:
            return cls.proc
        if cls.proc:
            try:
                cls.proc.kill()
            except OSError:
                pass
        cls.proc = make_player(rate)
        cls.prate, cls.written, cls.t0 = rate, 0, time.monotonic()
        return cls.proc

    @classmethod
    def _write(cls, data, g):
        """Stream raw samples, keeping LEAD seconds buffered. If we were ever late (the speakers ran dry) count it and
        move the clock on, so the start times we tell the page stay true."""
        step = max(2, int(cls.prate * 0.02)) * 2
        for i in range(0, len(data), step):
            while True:
                if g != cls.gen:
                    return False
                ahead = cls.written / cls.prate - (time.monotonic() - cls.t0)
                if ahead <= cls.LEAD:
                    break
                time.sleep(min(0.05, ahead - cls.LEAD + 0.005))
            if ahead < 0 and cls.written:                         # ran dry: an audible gap
                gap = -ahead
                cls.t0 += gap
                cls.stats["underruns"] = cls.stats.get("underruns", 0) + 1
                cls.stats["worst_gap_ms"] = max(cls.stats.get("worst_gap_ms", 0), round(gap * 1000, 1))
                ahead = 0.0
            if ahead >= cls.LEAD * 0.9:
                cls.filled = True                                 # (only judge the buffer once it has filled up)
            if cls.written and cls.filled:
                m = cls.stats.get("min_ahead_ms")
                cls.stats["min_ahead_ms"] = round(ahead * 1000) if m is None else min(m, round(ahead * 1000))
            try:
                cls.proc.stdin.write(data[i:i + step])
                cls.proc.stdin.flush()
            except (BrokenPipeError, OSError, AttributeError):
                return False
            cls.written += len(data[i:i + step]) // 2
        return True

    @classmethod
    def _player_loop(cls):
        while True:
            with cls.cv:
                while not cls.ready:
                    if not cls.todo and not cls.making and cls.prate and cls.written:
                        end = cls.t0 + cls.written / cls.prate + cls.LAT  # nothing left anywhere: say when he's done
                        g = cls.gen
                        cls.cv.wait(timeout=max(0.0, end - time.monotonic()))
                        if not cls.ready and not cls.todo and not cls.making and g == cls.gen and time.monotonic() >= end - 0.01:
                            BUS.pub(t="spk_done")
                            cls.written, cls.t0 = 0, time.monotonic()
                        continue
                    cls.cv.wait()
                g, sid, text, pcm, prate, words, level, lstep = cls.ready.pop(0)
                cls.cv.notify_all()
            if g != cls.gen:
                continue
            try:
                cls._player(prate)
            except Exception as e:  # noqa: BLE001
                print(f"[void] audio player: {e}", file=sys.stderr)
                BUS.pub(t="spk_skip", id=sid)
                continue
            now = time.monotonic()
            if cls.written / cls.prate < now - cls.t0:           # it had gone quiet: restart the clock
                cls.t0, cls.written, cls.filled = now, 0, False
            start = cls.t0 + cls.written / cls.prate
            dur = len(pcm) / 2 / prate
            if g == cls.gen:                                     # (not if he was cut off meanwhile)
                Wake.talking_until(start + dur + 0.8)
            cls.stats["sentences"] = cls.stats.get("sentences", 0) + 1
            cls.stats["audio_s"] = round(cls.stats.get("audio_s", 0) + dur, 2)
            BUS.pub(t="spk", id=sid, at=round((start - now + cls.LAT) * 1000), dur=round(dur, 3), words=words,
                    level=level, step=lstep)
            if not cls._write(pcm, g):
                continue
            pause = 0.3 if re.search(r"[.!?]['\")]*$", text) else 0.14 if re.search(r"[,;:—–]$", text) else 0.06
            cls._write(b"\0\0" * int(prate * pause), g)


STRESS_LINES = [
    "Okay, I had a look through the whole project.",
    "The crash happens when the list of items comes back empty, so the loop never runs and the next step falls over.",
    "I added a check before the loop, and a test that covers the empty case.",
    "All nine tests pass now.",
    "I also noticed the deploy script still points at the old server, which might cause trouble later.",
    "Want me to update that too, or leave it for now?",
]


def _cpu_burn(stop):
    x = 1
    while not stop.is_set():
        for _ in range(20000):
            x = (x * x + 1) % 1000003


def stress_voice(silent=False):
    """`void stress`: speak a few sentences three times — on a quiet computer, with every CPU core flat out, and with Void
    itself busy — and report whether the voice ever ran dry (a stutter), and how close it came."""
    import multiprocessing as mp
    eng = tts_engine()
    print(f"voice engine: {eng or 'none'}" + (f" ({Path(piper_voice()).name})" if eng == 'piper' and piper_voice() else ""))
    if not eng:
        print("no voice engine installed — nothing to test"); return 1
    if silent:
        globals()["audio_player_cmd"] = lambda rate: [sys.executable, "-c", "import sys\nwhile sys.stdin.buffer.read(65536): pass"]
        globals()["make_player"] = lambda rate: subprocess.Popen(audio_player_cmd(rate), stdin=subprocess.PIPE)
    else:
        cmd = True if OSYS and OSYS.can_play() else audio_player_cmd(22050)
        if not cmd:
            print("no audio player (paplay / aplay) — install libpulse or alsa-utils"); return 1
        print(f"player: {cmd[0]}   (you'll hear him say the same few sentences three times)")
    print("plays his voice four times: on a quiet computer, then with your CPU and Void itself as busy as they can be")
    print("warming up the voice…", flush=True)
    synth("Hi.")
    results, ok = [], True
    phases = [("quiet computer", ""), ("every CPU core busy", "cpu"), ("Void itself busy", "gil"),
              ("everything busy at once", "cpu+gil")]
    for n, (name, load) in enumerate(phases):
        stop, workers = mp.Event(), []
        if "cpu" in load:
            workers += [mp.Process(target=_cpu_burn, args=(stop,), daemon=True) for _ in range(os.cpu_count() or 2)]
        if "gil" in load:
            workers += [threading.Thread(target=_cpu_burn, args=(stop,), daemon=True) for _ in range(2)]
        for w in workers:
            w.start()
        Speaker.reset_stats()
        print(f"\n▶ {name}…", flush=True)
        for i, line in enumerate(STRESS_LINES):
            Speaker.say(line, n * 100 + i + 1)
        t = time.monotonic()
        time.sleep(0.5)
        while not Speaker.idle() and time.monotonic() - t < 180:
            time.sleep(0.1)
        stop.set()
        for w in workers:
            w.join(timeout=2)
        st = dict(Speaker.stats)
        verdict = "smooth — no stutter" if not st["underruns"] else f"STUTTERED {st['underruns']}× (worst gap {st['worst_gap_ms']:.0f} ms)"
        ok &= not st["underruns"]
        print(f"  {verdict}")
        if st["min_ahead_ms"] is not None and st["min_ahead_ms"] < 150 and not st["underruns"]:
            print("  (it got close, though)")
        print(f"  {st['sentences']} sentences, {st['audio_s']:.1f}s of speech · slowest sentence took {st['slowest_make_s']:.2f}s to make"
              f" (that's {st['worst_rtf']:.2f}× its own length) · closest it came to running dry: "
              f"{st['min_ahead_ms'] if st['min_ahead_ms'] is not None else '—'} ms of sound left")
        results.append((name, st))
    worst_rtf = max(r[1]["worst_rtf"] for r in results)
    print()
    if ok:
        print("✓ PASS — the voice never ran dry, even with your computer flat out.")
    elif worst_rtf > 0.8:
        print("✗ The voice takes almost as long to make as to say on this computer. Pick a lighter voice "
              "(Settings → Voice), or a '-low' piper voice, and run `void stress` again.")
    else:
        print("✗ It ran dry under load. Please send the numbers above.")
    return 0 if ok else 2


def synth(text: str):
    """→ (wav bytes, playback rate)"""
    eng = tts_engine()
    if not eng:
        raise RuntimeError("no TTS engine (install espeak-ng or piper)")
    vp = voice_preset()
    if eng == "kokoro" and vp.get("kokoro"):
        # pitch: play it back a touch faster/slower, and have Kokoro speak slower/faster by the same factor
        kp = float(vp.get("kpitch", 1.0))
        return kokoro_synth(text, CFG.get("kokoro_voice") or vp["kokoro"], 1.0 / kp), kp
    if eng == "kokoro":                                       # (the robot preset is espeak on purpose)
        eng = "piper" if piper_bin() and piper_voice() else "espeak"
    pitch = float(vp.get("pitch", 1.0))
    fd, out = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        model = None
        if eng == "piper" and not vp.get("espeak") and not CFG["piper_voice"]:
            if voice_file(vp["piper"]).exists():
                model = str(voice_file(vp["piper"]))
            else:
                fetch_voice(vp["piper"])          # use whatever we have until it lands
        if eng == "piper" and not vp.get("espeak") and model is None:
            model = piper_voice()
        if model:
            with _TTS_LOCK:                                         # one at a time, in order: no CPU fight between sentences
                audio = piper_inproc(model, text, pitch)
            if audio:
                return audio, pitch
            piper_run(model, text, out, pitch)
            rate = pitch
        elif shutil.which("espeak-ng"):
            ev = vp.get("espeak") or CFG["espeak_voice"]
            subprocess.run(["espeak-ng", "-v", ev, "-s", "160", "-p", str(int(clamp01(pitch / 2) * 99)), "-w", out, text],
                           capture_output=True, timeout=90, check=True)
            rate = 1.0
        else:
            raise RuntimeError("no usable voice")
        return Path(out).read_bytes(), rate
    finally:
        os.unlink(out)


def clamp01(x):
    return max(0.0, min(1.0, x))


def save_config(**kw):
    """Persist a few settings into config.json without clobbering the user's other edits."""
    try:
        cur = json.loads(CONF_PATH.read_text(encoding="utf-8-sig")) if CONF_PATH.exists() else {}
    except Exception:  # noqa: BLE001
        cur = {}
    cur.update(kw)
    CFG.update(kw)
    CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONF_PATH.write_text(json.dumps(cur, indent=2) + "\n")


# ---------------------------------------------------------------- claude code
def claude_bin():
    b = CFG["claude_bin"]
    found = shutil.which(b)
    if found:
        return found
    for c in (Path.home() / ".local/bin/claude", Path.home() / ".local/bin/claude.exe", Path.home() / ".claude/local/claude",
              Path("/usr/bin/claude"), Path("/usr/local/bin/claude")):
        if c.exists():
            return str(c)
    return None


def summarize_input(inp):
    if not isinstance(inp, dict):
        return ""
    s = ""
    for k in ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt"):
        if isinstance(inp.get(k), str):
            s = inp[k]
            break
    s = " ".join(s.split())
    return s[:90] + ("…" if len(s) > 90 else "")


def _cut(v, n):
    v = v if isinstance(v, str) else json.dumps(v)
    return v if len(v) <= n else v[:n] + "\n…"


def panel_input(name, inp):
    """What the code panel shows for a tool call: the real code / command / query, trimmed to a sane size."""
    if not isinstance(inp, dict):
        return {}
    out = {}
    for k in ("file_path", "path", "command", "description", "pattern", "glob", "url", "query", "notebook_path"):
        if isinstance(inp.get(k), str):
            out[k] = _cut(inp[k], 600)
    for k in ("old_string", "new_string", "content", "new_source"):
        if isinstance(inp.get(k), str):
            out[k] = _cut(inp[k], 4000)
    if isinstance(inp.get("edits"), list):
        out["edits"] = [{"old_string": _cut(e.get("old_string", ""), 1500), "new_string": _cut(e.get("new_string", ""), 1500)}
                        for e in inp["edits"][:6] if isinstance(e, dict)]
    if isinstance(inp.get("todos"), list):
        out["todos"] = [{"content": _cut(t.get("content", ""), 120), "status": t.get("status", "")}
                        for t in inp["todos"][:12] if isinstance(t, dict)]
    # which line an edit lands on, so the panel shows real line numbers (the file isn't changed yet at this point)
    if name in ("Edit", "MultiEdit") and isinstance(inp.get("file_path"), str):
        try:
            fp = Path(inp["file_path"]) if os.path.isabs(inp["file_path"]) else Path(ST.cwd) / inp["file_path"]
            if fp.is_file() and fp.stat().st_size < 3_000_000:
                txt = fp.read_text(errors="replace")
                old = inp.get("old_string") or ((inp.get("edits") or [{}])[0] or {}).get("old_string") or ""
                i = txt.find(old) if old else -1
                if i >= 0:
                    out["line"] = txt.count("\n", 0, i) + 1
        except Exception:  # noqa: BLE001
            pass
    if name == "Write" and isinstance(inp.get("file_path"), str):
        try:
            fp = Path(inp["file_path"]) if os.path.isabs(inp["file_path"]) else Path(ST.cwd) / inp["file_path"]
            out["exists"] = fp.exists()
        except Exception:  # noqa: BLE001
            pass
    return out


def result_text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) and b.get("type") == "text" else
                         ("[image]" if isinstance(b, dict) and b.get("type") == "image" else "") for b in c)
    return ""


def trust():
    return "root" if CFG.get("root_mode") and not WIN else (CFG.get("trust") if CFG.get("trust") in ("ask", "trust", "readonly") else "ask")


HOOKS_PATH = DATA_DIR / "hooks.json"


def hooks_file():
    """Claude Code settings that route commands / web requests through approve.py (→ a yes/no card under Void)."""
    cmd = (f'"{sys.executable}" "{APP_DIR / "approve.py"}"' if WIN else
           f"{shlex.quote(sys.executable)} {shlex.quote(str(APP_DIR / 'approve.py'))}")
    data = {"hooks": {"PreToolUse": [{"matcher": "Bash|WebFetch|WebSearch|Edit|Write|MultiEdit|NotebookEdit|mcp__.*",
                                      "hooks": [{"type": "command", "command": cmd, "timeout": 900}]}]}}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HOOKS_PATH.write_text(json.dumps(data, indent=1))
    return str(HOOKS_PATH)


def build_cmd(binary, partial):
    c = [binary, "-p", "--output-format", "stream-json", "--verbose"]
    if partial:
        c.append("--include-partial-messages")
    tr = trust()
    if tr in ("root", "trust"):
        c += ["--permission-mode", "bypassPermissions"]
    elif tr == "readonly":
        c += ["--permission-mode", "plan"]
    else:
        c += ["--permission-mode", "acceptEdits", "--settings", hooks_file()]
    mdl = CFG["model"]
    if mdl == "auto":                            # quick chit-chat → the fast model; real work → the default one
        mdl = ST.auto_pick or ""
    if mdl:
        c += ["--model", mdl]
    sp = CFG["voice_prompt"] or ""
    if CFG.get("root_mode"):
        sp += ("\n\nRoot mode is ON: the user allows you to run any command, including ones that need root. "
               "When you need root, use `sudo -A ...` (Void asks the user for the password itself, or fills it in "
               "automatically; never ask for the password in chat). Still be careful: say what you're about to change before destructive or "
               "system-wide actions.")
    if tr == "ask":
        sp += ("\n\nThe user approves commands and web requests one at a time (Void asks them out loud). "
               "Prefer one clear command over many small ones, and don't retry a command the user said no to.")
    mp = memory_prompt(fresh=not ST.session)
    if mp:
        sp += "\n\n" + mp
    if sp:
        c += ["--append-system-prompt", sp]
    if ST.session:
        c += ["--resume", ST.session]
    c += list(CFG["extra_args"])
    return c


def run_claude(prompt, emit, partial):
    binary = claude_bin()
    if not binary:
        emit(t="error", text=f"Couldn't find Claude Code (`{CFG['claude_bin']}`). Install it, run `claude` once to "
                             f"log in, or set claude_bin in {CONF_PATH}.")
        return "done"
    env = dict(os.environ)
    env["PATH"] = str(Path.home() / ".local" / "bin") + os.pathsep + env.get("PATH", "")
    if CFG.get("root_mode") and not WIN:
        env["SUDO_ASKPASS"] = str(APP_DIR / "askpass.py")
    env["VOID_PORT"], env["VOID_TOKEN"] = str(CFG["port"]), TOOL_TOKEN   # (the limited one: every command Claude runs sees it)
    proc = subprocess.Popen(build_cmd(binary, partial), cwd=ST.cwd, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
                            encoding="utf-8", errors="replace", **spawn_kw())
    ST.proc = proc
    err = []
    t_err = threading.Thread(target=lambda: err.extend(proc.stderr), daemon=True)
    t_err.start()
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except BrokenPipeError:
        pass

    got_result = streamed = emitted = False
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        ty = o.get("type")
        if ty == "system" and o.get("subtype") == "init":
            if o.get("session_id"):
                ST.session = o["session_id"]
                ST.save()
            emit(t="init", model=o.get("model", ""))
        elif ty == "stream_event":
            ev = o.get("event") or {}
            d = ev.get("delta") or {}
            if ev.get("type") == "content_block_delta" and d.get("type") == "text_delta":
                streamed = emitted = True
                emit(t="delta", text=d.get("text", ""))
            elif ev.get("type") == "message_start":
                emit(t="break")
        elif ty == "assistant":
            for b in (o.get("message") or {}).get("content") or []:
                if b.get("type") == "text" and not streamed and b.get("text"):
                    emitted = True
                    emit(t="break")
                    emit(t="delta", text=b["text"])
                elif b.get("type") == "tool_use":
                    emitted = True
                    emit(t="tool", name=b.get("name", "tool"), detail=summarize_input(b.get("input")), id=b.get("id", ""),
                         input=panel_input(b.get("name", ""), b.get("input")))
        elif ty == "user":
            # tool output comes back as a "user" message — the code panel shows it (terminal output, file contents, hits)
            content = (o.get("message") or {}).get("content")
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    txt = result_text(b.get("content"))
                    lines = txt.splitlines()
                    if len(lines) > 80:
                        lines = lines[:40] + [f"… {len(lines) - 70} more lines …"] + lines[-30:]
                    emit(t="toolres", id=b.get("tool_use_id", ""), text=_cut("\n".join(lines), 6000),
                         is_error=bool(b.get("is_error")))
        elif ty == "result":
            got_result = True
            if o.get("session_id"):
                ST.session = o["session_id"]
                ST.save()
            denied = o.get("permission_denials") or []
            if denied:
                emit(t="denied", tools=sorted({d.get("tool_name", "?") for d in denied}))
            emit(t="result", text=o.get("result") or "", is_error=bool(o.get("is_error")),
                 cost=o.get("total_cost_usd"), turns=o.get("num_turns"))
    proc.wait()
    t_err.join(timeout=2)
    ST.proc = None
    if got_result:
        return "done"
    e = "".join(err).strip()
    if ST.stopped:
        emit(t="stopped")
        return "done"
    low = e.lower()
    if partial and not emitted and ("include-partial-messages" in low or "unknown option" in low):
        return "retry_nopartial"
    if ST.session and not emitted and ("session" in low or "conversation" in low):
        return "retry_fresh"
    emit(t="error", text=e[-1200:] or f"claude exited with code {proc.returncode}")
    return "done"


def stop_proc():
    APPROVALS.cancel_all()
    p = ST.proc
    if p and p.poll() is None:
        ST.stopped = True
        if WIN:                               # no process groups: take down Claude and everything it started
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True,
                           creationflags=winsys.NO_WINDOW)
            return
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


# ---------------------------------------------------------------- updates
UPDATE_URL = os.environ.get("VOID_UPDATE_URL") or "https://api.github.com/repos/PLAYER99-void/void/releases/latest"


def vtuple(v):
    """'v1.2.10' → (1, 2, 10), so versions compare as numbers."""
    nums = [int(n) for n in re.findall(r"\d+", str(v or ""))[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


class Updates:
    """Is a newer Void out? Asks GitHub's public "latest release" — at most twice a day; nothing about you is sent —
    and installs it on request: on Windows by running the new VoidSetup.exe (it stops Void, updates and starts him
    again), on a git install of Linux by `git pull` + install.sh + `void show`."""
    info = {}                    # {"latest", "url", "setup", "checked"} — also kept in update.json
    state, error = "idle", ""    # idle | updating | failed

    @classmethod
    def cache(cls):
        return DATA_DIR / "update.json"

    @classmethod
    def start(cls):
        try:
            cls.info = json.loads(cls.cache().read_text())
        except (OSError, ValueError):
            cls.info = {}
        try:                                       # what an update run on Linux left behind (it restarted us)
            res = (DATA_DIR / "update.result").read_text().strip()
            (DATA_DIR / "update.result").unlink()
            if res != "0":
                try:
                    hint = (DATA_DIR / "update.hint").read_text().strip()
                except OSError:
                    hint = "run ./install.sh in a terminal to see why"
                cls.state, cls.error = "failed", f"the update didn't finish — {hint}"
        except OSError:
            pass
        threading.Thread(target=cls.loop, daemon=True).start()

    @classmethod
    def stale(cls, age):
        return time.time() - float(cls.info.get("checked") or 0) > age

    @classmethod
    def loop(cls):
        first = True
        while True:                            # once when he starts (if the last answer is over an hour old), then
            if CFG.get("update_check", True) and cls.stale(3600 if first else 6 * 3600):   # every 6 hours
                cls.check()
            first = False
            time.sleep(600)

    @classmethod
    def check(cls):
        import urllib.request
        try:
            req = urllib.request.Request(UPDATE_URL, headers={"Accept": "application/vnd.github+json",
                                                              "User-Agent": f"Void/{VERSION}"})
            rel = json.load(urllib.request.urlopen(req, timeout=15))
        except Exception as e:  # noqa: BLE001
            print(f"[void] update check: {e}", file=sys.stderr)
            return False
        assets = {a.get("name") or "": a.get("browser_download_url") or "" for a in rel.get("assets") or []}

        def pick(test):
            return next((u for n, u in assets.items() if test(n)), "")
        cls.info = {"latest": str(rel.get("tag_name") or "").lstrip("v"), "url": rel.get("html_url") or "",
                    "setup": assets.get("VoidSetup.exe", ""),
                    "deb": pick(lambda n: n.startswith("void-claude_") and n.endswith(".deb")),
                    "rpm": pick(lambda n: n.startswith("void-claude-") and n.endswith(".noarch.rpm")),
                    "arch": pick(lambda n: n.startswith("void-claude-") and n.endswith(".pkg.tar.zst")),
                    "checked": time.time()}
        try:
            cls.cache().write_text(json.dumps(cls.info))
        except OSError:
            pass
        return True

    @classmethod
    def how(cls):
        """setup (Windows) | git (Linux, installed from a clone) | package (AUR/.deb/meson) | manual"""
        if WIN:
            return "setup"
        try:
            src = (DATA_DIR / "source").read_text().strip()
        except OSError:
            return "manual"
        if src == "package":
            return "package"
        return "git" if (Path(src) / ".git").exists() and (Path(src) / "install.sh").exists() else "manual"

    _kind = None

    @classmethod
    def pkg_kind(cls):
        """Which package manager owns this install: deb | rpm | arch | "" (asked once)."""
        if cls._kind is None:
            cls._kind = ""
            f = str(APP_DIR / "server.py")
            for kind, cmd in (("deb", ["dpkg", "-S", f]), ("rpm", ["rpm", "-qf", f]), ("arch", ["pacman", "-Qo", f])):
                if shutil.which(cmd[0]):
                    try:
                        if subprocess.run(cmd, capture_output=True, timeout=10).returncode == 0:
                            cls._kind = kind
                            break
                    except (OSError, subprocess.SubprocessError):
                        pass
        return cls._kind

    @classmethod
    def pkg_command(cls, path):
        """How to install a downloaded package file, as root."""
        return {"deb": ["apt-get", "install", "-y", str(path)], "rpm": ["dnf", "install", "-y", str(path)],
                "arch": ["pacman", "-U", "--noconfirm", str(path)]}.get(cls.pkg_kind())

    @classmethod
    def can_install(cls):
        how = cls.how()
        if WIN:
            return bool(cls.info.get("setup"))
        if how == "git":
            return True
        return how == "package" and bool(cls.pkg_kind() and cls.info.get(cls.pkg_kind()) and shutil.which("pkexec"))

    @classmethod
    def status(cls):
        latest = cls.info.get("latest") or ""
        return {"current": VERSION, "latest": latest, "newer": bool(latest) and vtuple(latest) > vtuple(VERSION),
                "url": cls.info.get("url") or "", "how": cls.how(), "pkg": "" if WIN else cls.pkg_kind(),
                "can_install": cls.can_install(), "state": cls.state, "error": cls.error}

    @classmethod
    def install(cls):
        if cls.state == "updating":
            return
        cls.state, cls.error = "updating", ""
        threading.Thread(target=cls._install, daemon=True).start()

    @classmethod
    def _install(cls):
        try:
            if WIN:
                import urllib.request
                if not cls.info.get("setup"):
                    raise RuntimeError("this release has no VoidSetup.exe")
                exe = Path(tempfile.gettempdir()) / "VoidSetup-update.exe"
                urllib.request.urlretrieve(cls.info["setup"], exe)
                # A hidden helper runs setup, waits for it, then starts Void again. A short-lived PowerShell starts the
                # helper and exits, so the helper isn't in our process tree and survives setup stopping Void (and this
                # server). The helper's script goes in base64 (-EncodedCommand): no quoting to get wrong.
                import base64

                def q(s):                                              # a PowerShell '...' string
                    return "'" + str(s).replace("'", "''") + "'"
                pyw = Path(sys.executable).with_name("pythonw.exe")
                pyw = pyw if pyw.exists() else Path(sys.executable)
                log = DATA_DIR / "update-setup.log"
                helper = ("Start-Process -Wait -FilePath " + q(exe) + " -ArgumentList '/SILENT','/SUPPRESSMSGBOXES',"
                          "'/NORESTART','/UPDATER'," + q('/LOG="' + str(log) + '"') + "\n"
                          "Start-Process -FilePath " + q(pyw) + " -ArgumentList " + q('"' + str(APP_DIR / "launch_win.py") + '"')
                          + ",'show'\n")
                enc = base64.b64encode(helper.encode("utf-16-le")).decode()
                subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                                  "Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile -EncodedCommand "
                                  + enc + "'"], creationflags=winsys.NO_WINDOW, close_fds=True)
                print(f"[void] update: running {exe}", flush=True)
                return                                 # setup stops us and updates Void; the helper starts him again
            how = cls.how()
            if how == "package":
                return cls._install_package()
            if how != "git":
                raise RuntimeError("update by running install.sh from the folder you installed from")
            src = (DATA_DIR / "source").read_text().strip()
            void_bin = Path.home() / ".local" / "bin" / "void"
            res = DATA_DIR / "update.result"
            # install.sh stops this server and the window at the end, so the whole thing runs on its own and
            # starts Void again afterwards, success or not; the result is read back by the new server (start())
            script = (f'cd {shlex.quote(src)} && git pull --ff-only && ./install.sh </dev/null; '
                      f'echo $? > {shlex.quote(str(res))}; {shlex.quote(str(void_bin))} show')
            with open(DATA_DIR / "update.log", "ab") as log:
                subprocess.Popen(["bash", "-c", script], stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
            print("[void] update: git pull + install.sh running (log: update.log)", flush=True)
        except Exception as e:  # noqa: BLE001
            cls.state, cls.error = "failed", str(e)
            print(f"[void] update failed: {e}", file=sys.stderr)

    @classmethod
    def _install_package(cls):
        """A .deb / .rpm / Arch package install: download the new package from the release and install it as root
        (pkexec: your desktop asks for your password), then start Void again. Runs on its own, like the git path."""
        import urllib.request
        kind, url = cls.pkg_kind(), cls.info.get(cls.pkg_kind() or "-")
        if not kind or not url:
            raise RuntimeError("this release has no package for your system — update with your package manager")
        d = DATA_DIR / "update-pkg"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        f = d / url.rsplit("/", 1)[-1]
        urllib.request.urlretrieve(url, f)
        cmd = " ".join(shlex.quote(c) for c in cls.pkg_command(f))
        (DATA_DIR / "update.hint").write_text(f"install it by hand: sudo {cmd}")
        if not shutil.which("pkexec"):
            raise RuntimeError(f"no pkexec here — run: sudo {cmd}")
        res, void_bin = DATA_DIR / "update.result", shutil.which("void") or "/usr/bin/void"
        script = (f'pkexec {cmd}; echo $? > {shlex.quote(str(res))}; '
                  f'{shlex.quote(void_bin)} restart')
        with open(DATA_DIR / "update.log", "ab") as log:
            subprocess.Popen(["bash", "-c", script], stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        print(f"[void] update: installing {f.name} with pkexec (log: update.log)", flush=True)


# ---------------------------------------------------------------- diagnostics ("Copy diagnostics", `void diagnostics`)
ISSUE_URL = "https://github.com/PLAYER99-void/void/issues/new?template=bug.md"


def redact(text):
    """Blank out what could identify you: your home folder and user name, email addresses, and this run's token."""
    import getpass
    home = str(Path.home())
    for h in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
        text = re.sub(re.escape(h), "~", text, flags=re.I)
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = ""
    if len(user) >= 3:
        text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(user) + r"(?![A-Za-z0-9])", "<user>", text, flags=re.I)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "<email>", text)
    return text.replace(TOKEN, "<token>").replace(TOOL_TOKEN, "<token>")


def diagnostics():
    """A plain-text report for asking for help: versions, what Void found (mic, voice, speech model, Claude Code) and
    the end of the log. Nothing is sent anywhere — you paste it where you want. Memory, history, projects and what
    you said aren't included; your home folder, user name and email addresses are blanked out."""
    import importlib.util
    import platform
    L = []

    def add(k, v):
        L.append(f"{k:<18} {v}")
    add("void", f"{VERSION} ({Updates.how()} install; latest seen: {Updates.info.get('latest') or '?'})")
    add("system", platform.platform())
    if not WIN:
        add("desktop", " / ".join(x for x in (os.environ.get("XDG_CURRENT_DESKTOP"), os.environ.get("XDG_SESSION_TYPE"),
                                              "hyprland" if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") else "") if x) or "?")
    add("python", platform.python_version())
    cb = claude_bin()
    cv = "not found"
    if cb:
        try:
            cv = (subprocess.run([cb, "--version"], capture_output=True, text=True, timeout=10, **spawn_kw()).stdout or "found").strip()
        except Exception:  # noqa: BLE001
            cv = "found (version unknown)"
    add("claude code", cv)
    add("voice engine", f"{tts_engine() or 'none'} (voice: {CFG.get('voice')})")
    if _whisper:
        add("speech model", f"{_whisper._void_name} on {_whisper._void_dev} ({_whisper._void_ct})")
    else:
        add("speech model", _whisper_err or "not loaded yet")
    try:                                         # (find_spec raises when there's no nvidia package at all)
        cuda = importlib.util.find_spec("nvidia.cublas") is not None
    except (ImportError, ValueError):
        cuda = False
    add("cuda libraries", "yes" if cuda else "no")
    add("mic", f"device: {CFG.get('mic_device') or 'system default'}")
    if WIN:
        lv = winsys.mic_level(CFG.get("mic_device") or "")
        add("mic level", "?" if lv is None else f"{lv:.0%} (Windows)")
        m = getattr(winsys.Mic, "last", None)
        if m:
            add("mic stream", f"{m.info['driver']} · {m.channels} of {m.info['channels']} channel(s) · {m.info['rate']} Hz"
                              f" · recording channel {m.channel[0]}" + (" (switched: the other is near silent)" if m.channel[1] else ""))
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64", 0,
                               winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
            add("vc++ runtime", winreg.QueryValueEx(k, "Version")[0])
        except OSError:
            add("vc++ runtime", "not found")
    add("mic auto-level", f"{'on' if CFG.get('mic_auto', True) else 'off'}, boost {MicAuto.soft:.1f}x, last voice peak "
                          + ("-" if MicAuto.last_peak is None else f"{MicAuto.last_peak:.3f}"))
    add("settings", ", ".join(f"{k}={CFG.get(k)}" for k in ("trust", "wake_word", "barge_in", "follow_up", "tts",
                                                             "whisper_model", "model", "hover", "update_check")))
    try:
        log = (DATA_DIR / "server.log").read_text(encoding="utf-8", errors="replace").splitlines()[-60:]
    except OSError:
        log = ["(no server.log)"]
    return redact("Void diagnostics\n\n" + "\n".join(L) + "\n\n--- last lines of server.log ---\n" + "\n".join(log) + "\n")


def state_info():
    return {
        "version": VERSION, "update": Updates.status(), "update_check": bool(CFG.get("update_check", True)),
        "platform": "windows" if WIN else "macos" if MAC else "linux", "ptt_hotkey": CFG.get("ptt_hotkey", "") or "",
        "presence": "bar" if CFG.get("presence") == "bar" else "orb",
        "bar_edge": CFG.get("bar_edge") if CFG.get("bar_edge") in ("top", "bottom", "left", "right") else "top",
        "cwd": ST.cwd, "cwd_display": pretty_path(ST.cwd), "session": bool(ST.session),
        "tts": tts_engine(), "mic": bool(OSYS) or bool(mic_cmds()), "stt": "ready" if _whisper else (_whisper_err or "loading"),
        "trust": trust(), "model": CFG["model"] or "default", "root": bool(CFG.get("root_mode")) and not WIN, "root_ok": not WIN,

        "wake_word": bool(CFG.get("wake_word")), "notify_done": bool(CFG.get("notify_done")),
        "popup_done": bool(CFG.get("popup_done")), "hover": bool(CFG.get("hover")), "code_panel": CFG.get("code_panel", True) is not False, "live_minutes": int(CFG.get("live_minutes") or 10), "voice_native": Speaker.available(), "code_face": "you" if CFG.get("code_face") == "you" else "him",
        "click_through": bool(CFG.get("click_through")),
        "autostart": bool(CFG.get("autostart")), "listen_on_show": bool(CFG.get("listen_on_show")),
        "barge_in": bool(CFG.get("barge_in")), "sfx": bool(CFG.get("sfx")), "look": look(), "skins": SKINS,
        "projects": quick.projects(),
        "quick_actions": CFG.get("quick_actions") or [], "memory_count": len(mem_load()),
        "mic_device": CFG.get("mic_device") or "", "whisper_model": CFG.get("whisper_model") or "auto",
        "can_shoot": bool(OSYS) or any(shutil.which(t) for t in ("grim", "spectacle", "gnome-screenshot", "maim", "scrot", "import")),
        "eye_color": CFG.get("eye_color") or "#3d9b74", "eye_white": CFG.get("eye_white") or "#f3dccf",
        "pupil_style": CFG.get("pupil_style") or "slit",
        "voice": CFG.get("voice") or "girl", "voice_pitch": voice_preset().get("pitch", 1.0),
        "voices": [{"id": k, "label": v["label"],
                    "ready": bool(v.get("espeak")) or (bool(v.get("kokoro")) and tts_engine() == "kokoro")
                             or voice_file(v.get("piper", "")).exists(),
                    "downloading": v.get("piper") in _downloading or (bool(v.get("kokoro")) and "kokoro" in _downloading)}
                   for k, v in VOICES.items()],
        "voice_engine": tts_engine() or "none",
        "claude": bool(claude_bin()), "busy": ST.lock.locked(), "config": str(CONF_PATH),
    }


# ---------------------------------------------------------------- http
CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
          ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json"}


class Handler(BaseHTTPRequestHandler):
    server_version = "Void"

    def log_message(self, *a):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "bad host"}, 403)
        p = urlparse(self.path).path
        if p == "/api/state":                      # without the window's token: just "it's running" and the version
            if secrets.compare_digest(self.headers.get("X-Void-Token") or "", TOKEN):
                return self._json(state_info())
            return self._json({"version": VERSION})
        if p == "/":
            p = "/index.html"
        f = (STATIC / p.lstrip("/")).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            return self._json({"error": "not found"}, 404)
        data = f.read_bytes()                      # (the page has no token in it: the window hands it over itself)
        self.send_response(200)
        self.send_header("Content-Type", CTYPES.get(f.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        p = urlparse(self.path).path
        if not self._host_ok():
            return self._json({"error": "forbidden"}, 403)
        if p == "/api/claim":                      # the browser fallback trades a one-time ticket for the token
            if Tickets.claim(str(json.loads(self._body() or b"{}").get("ticket") or "")):
                return self._json({"token": TOKEN})
            return self._json({"error": "forbidden"}, 403)
        tok = self.headers.get("X-Void-Token") or ""
        full = secrets.compare_digest(tok, TOKEN)
        if not full and not (secrets.compare_digest(tok, TOOL_TOKEN) and p in TOOL_PATHS):
            return self._json({"error": "forbidden"}, 403)
        try:
            if p == "/api/ticket":                 # (window token only) a one-time ticket for the browser fallback
                return self._json({"ticket": Tickets.new()})
            if p == "/api/ask":
                return self.ask()
            if p == "/api/tts":
                text = speakable(json.loads(self._body() or b"{}").get("text", ""))
                if not text or not tts_engine():
                    self.send_response(204)
                    self.end_headers()
                    return None
                audio, rate = synth(text)
                audio, words = tidy_and_time(audio, text)
                pause = 0.3 if re.search(r"[.!?]['\")]*$", text) else 0.14 if re.search(r"[,;:—–]$", text) else 0.06
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("X-Void-Rate", f"{rate:.3f}")
                self.send_header("X-Void-Pause", f"{pause:.2f}")
                if words:
                    self.send_header("X-Void-Words", json.dumps(words, separators=(",", ":")))
                self.send_header("Access-Control-Expose-Headers", "X-Void-Rate, X-Void-Pause, X-Void-Words")
                self.send_header("Content-Length", str(len(audio)))
                self.end_headers()
                self.wfile.write(audio)
                return None
            if p == "/api/say":                       # speak this (played by the server, see Speaker)
                b = json.loads(self._body() or b"{}")
                text = speakable(b.get("text", ""))
                if not text or not tts_engine():
                    BUS.pub(t="spk_skip", id=b.get("id"))
                else:
                    Speaker.say(text, b.get("id"))
                return self._json({"ok": True})
            if p == "/api/say/stop":
                Speaker.stop()
                return self._json({"ok": True})
            if p == "/api/listen":
                Wake.end_follow("cancel")                # a normal recording takes over from the follow-up window
                return self.listen()
            if p == "/api/sleep":                  # hidden = fully off: mic closed, no "hey Void", no voice
                Wake.asleep = bool(json.loads(self._body() or b"{}").get("on"))
                if Wake.asleep:
                    Speaker.stop()
                    Wake.hush()
                    if Listen.current:
                        Listen.current.stop(cancel=True)
                return self._json({"ok": True, "asleep": Wake.asleep})
            if p == "/api/follow":
                Wake.end_follow("finish" if json.loads(self._body() or b"{}").get("act") == "finish" else "cancel")
                return self._json({"ok": True})
            if p == "/api/listen/stop":
                cur = Listen.current
                if cur:
                    cur.stop(bool(json.loads(self._body() or b"{}").get("cancel")))
                return self._json({"ok": True})
            if p == "/api/stop":
                stop_proc()
                return self._json({"ok": True})
            if p == "/api/diagnostics":
                return self._json({"text": diagnostics()})
            if p == "/api/report":                 # opens GitHub's "new issue" page — only ever this one address
                import webbrowser
                webbrowser.open(ISSUE_URL)
                return self._json({"ok": True})
            if p == "/api/update/check":                     # {"stale": s}: only if the last check is older than s
                st = json.loads(self._body() or b"{}").get("stale")
                if not isinstance(st, (int, float)) or Updates.stale(st):
                    Updates.check()
                return self._json(Updates.status())
            if p == "/api/update":
                Updates.install()
                return self._json(Updates.status())
            if p == "/api/settings":
                b = json.loads(self._body() or b"{}")
                upd = {}
                if "model" in b:
                    m = str(b["model"] or "").strip()
                    upd["model"] = "" if m in ("", "default") else m
                if "voice" in b and b["voice"] in VOICES:
                    upd["voice"] = b["voice"]
                    if VOICES[b["voice"]].get("piper"):
                        fetch_voice(VOICES[b["voice"]]["piper"])
                if "root" in b:
                    upd["root_mode"] = bool(b["root"]) and not WIN
                if upd.get("root_mode") is False:
                    AskPass.forget()
                for key in ("eye_color", "eye_white"):
                    if key in b:
                        ec = str(b[key] or "").strip().lower()
                        if ec == "random" or re.fullmatch(r"#[0-9a-f]{6}", ec):
                            upd[key] = ec
                if isinstance(b.get("look"), dict):
                    cur = dict(CFG.get("look") or {})
                    cur.update(b["look"])
                    CFG["look"] = cur
                    upd["look"] = look()
                if b.get("pupil_style") in ("slit", "circle", "dot"):
                    upd["pupil_style"] = b["pupil_style"]
                if b.get("trust") in ("ask", "trust", "readonly"):
                    upd["trust"] = b["trust"]
                    upd["root_mode"] = False
                    AskPass.forget()
                if "follow_up" in b:
                    upd["follow_up"] = max(0, min(60, int(b.get("follow_up") or 0)))
                for key in ("wake_word", "notify_done", "popup_done", "code_panel", "hover", "click_through", "autostart", "listen_on_show",
                            "barge_in", "sfx", "update_check"):
                    if key in b:
                        upd[key] = bool(b[key])
                if "ptt_hotkey" in b:
                    k = str(b["ptt_hotkey"] or "").strip()
                    if k == "" or re.fullmatch(r"((Ctrl|Alt|Shift|Win)\+){0,4}[A-Za-z0-9`]+", k):
                        upd["ptt_hotkey"] = k[:40]
                if b.get("presence") in ("orb", "bar"):
                    upd["presence"] = b["presence"]
                if b.get("bar_edge") in ("top", "bottom", "left", "right"):
                    upd["bar_edge"] = b["bar_edge"]
                if b.get("code_face") in ("him", "you"):
                    upd["code_face"] = b["code_face"]
                if "mic_device" in b:
                    upd["mic_device"] = str(b["mic_device"] or "")
                if b.get("whisper_model") in ("auto", "tiny.en", "base.en", "small.en", "medium.en"):
                    upd["whisper_model"] = b["whisper_model"]
                if isinstance(b.get("quick_actions"), list):
                    qa = []
                    for a in b["quick_actions"][:12]:
                        if isinstance(a, dict) and str(a.get("label", "")).strip() and str(a.get("prompt", "")).strip():
                            qa.append({"label": str(a["label"]).strip()[:40], "prompt": str(a["prompt"]).strip()[:1000],
                                       **({"attach": a["attach"]} if a.get("attach") in ("screen", "clipboard") else {})})
                    upd["quick_actions"] = qa
                if upd:
                    save_config(**upd)
                if "whisper_model" in upd:
                    reset_whisper()
                if "autostart" in upd:
                    sync_autostart()
                if upd.get("wake_word"):
                    threading.Thread(target=lambda: _try(whisper), daemon=True).start()
                Wake.ensure()
                return self._json(state_info())
            if p == "/api/events":
                return self.events()
            if p == "/api/quick":
                r = quick.handle(json.loads(self._body() or b"{}").get("text", ""))
                if r and r.get("cd"):
                    ST.cwd, ST.session = r["cd"], None
                    ST.save()
                    APPROVALS.reset()
                    r["state"] = state_info()
                return self._json({"handled": bool(r), **(r or {})})
            if p == "/api/projects":
                b = json.loads(self._body() or b"{}")
                if b.get("op") == "add":
                    got = quick.add_project(str(b.get("name") or ""), str(b.get("path") or ""))
                    if not got:
                        return self._json({"error": "that folder doesn't exist"}, 400)
                elif b.get("op") == "del":
                    pr = quick.projects()
                    pr.pop(str(b.get("name")), None)
                    save_config(projects=pr)
                return self._json({"projects": quick.projects()})
            if p == "/api/approve":
                b = json.loads(self._body() or b"{}")
                cmd = str((b.get("input") or {}).get("command") or "").strip()
                if b.get("tool") == "Bash" and is_void_see(cmd):          # only looking at the screen: no card
                    return self._json({"ok": True, "why": "just looking at the screen"})
                if b.get("tool") in EDIT_TOOLS and not edit_needs_card(edit_path(b.get("input"))):
                    return self._json({"ok": True})                       # an ordinary edit in the project: no card
                return self._json(APPROVALS.ask(str(b.get("tool") or "?"), b.get("input") or {}))
            if p == "/api/askpass":                   # askpass.py (sudo -A) is waiting for the password
                if WIN or not CFG.get("root_mode"):   # the password card only exists in root mode
                    return self._json({"error": "root mode is off"}, 403)
                b = json.loads(self._body() or b"{}")
                return self._json(AskPass.ask(str(b.get("prompt") or "Password:")))
            if p == "/api/askpass/answer":
                b = json.loads(self._body() or b"{}")
                pw = b.get("pw")
                return self._json({"ok": AskPass.answer(str(b.get("id")), pw if isinstance(pw, str) else None)})
            if p == "/api/approve/answer":
                b = json.loads(self._body() or b"{}")
                ans = b.get("ans") if b.get("ans") in ("yes", "no", "always") else "no"
                return self._json({"ok": APPROVALS.answer(str(b.get("id")), ans)})
            if p == "/api/speaking":                 # the page tells us while he talks (so "hey Void" can't wake himself)
                b = json.loads(self._body() or b"{}")
                Wake.talking_until(time.monotonic() + (60 if b.get("on") else 0.8))
                return self._json({"ok": True})
            if p == "/api/capture":
                b = json.loads(self._body() or b"{}")
                kind = b.get("kind", "screen")
                if not full:                          # Claude's commands may look at the screen, never your clipboard
                    if kind not in ("window", "screen"):
                        return self._json({"error": "forbidden"}, 403)
                    b["by"] = "claude"
                try:
                    shot = capture(kind)
                    if b.get("by") == "claude":          # `void see` during a task: show him looking
                        BUS.pub(t="saw", kind=shot["kind"])
                    return self._json(shot)
                except Exception as e:  # noqa: BLE001
                    return self._json({"error": str(e)}, 400)
            if p == "/api/notify":
                b = json.loads(self._body() or b"{}")
                notify(str(b.get("title") or "Void"), str(b.get("body") or ""))
                return self._json({"ok": True})
            if p == "/api/memory":
                b = json.loads(self._body() or b"{}")
                op = b.get("op", "list")
                if op == "add":
                    it = mem_add(b.get("text", ""))
                    return self._json({"added": it, "items": mem_load()})
                if op == "del":
                    n = mem_del(ident=b.get("id"), match=b.get("match"))
                    return self._json({"removed": n, "items": mem_load()})
                if op == "clear":
                    mem_save([])
                return self._json({"items": mem_load()})
            if p == "/api/history":
                b = json.loads(self._body() or b"{}")
                if b.get("op") == "clear":
                    HIST_PATH.unlink(missing_ok=True)
                return self._json({"items": hist_load(int(b.get("limit") or 60))})
            if p == "/api/resume":
                b = json.loads(self._body() or b"{}")
                d = b.get("cwd") or ST.cwd
                try:
                    d_path = Path(d).expanduser().resolve()
                except Exception:
                    return self._json({"error": f"that folder is gone: {pretty_path(d)}"}, 400)
                if not d_path.is_dir():
                    return self._json({"error": f"that folder is gone: {pretty_path(str(d_path))}"}, 400)
                ST.cwd, ST.session = str(d_path), b.get("session") or None
                ST.save()
                APPROVALS.reset()
                return self._json(state_info())
            if p == "/api/devices":
                return self._json({"mics": list_mics()})
            if p == "/api/new":
                ST.session = None
                ST.save()
                APPROVALS.reset()
                return self._json(state_info())
            if p == "/api/cd":
                raw = json.loads(self._body() or b"{}").get("path", "~")
                path = os.path.abspath(os.path.join(ST.cwd, os.path.expanduser(raw.strip() or "~")))
                if not os.path.isdir(path):
                    return self._json({"error": f"not a directory: {pretty_path(path)}"}, 400)
                ST.cwd, ST.session = path, None
                ST.save()
                APPROVALS.reset()
                return self._json(state_info())
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 500)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        alive = [True]

        def emit(**kw):
            if not alive[0]:
                return
            try:
                self.wfile.write(b"data: " + json.dumps(kw).encode() + b"\n\n")
                self.wfile.flush()
            except ConnectionError:                 # (broken pipe / reset / Windows' "aborted": the window went away)
                alive[0] = False
        return emit, alive

    def listen(self):
        mode = json.loads(self._body() or b"{}").get("mode", "auto")
        if not Listen.lock.acquire(blocking=False):
            if Listen.current:
                Listen.current.stop(cancel=True)
            if not Listen.lock.acquire(timeout=2):
                return self._json({"error": "mic busy"}, 409)
        try:
            ls = Listen.current = Listen("ptt" if mode == "ptt" else "auto")
            emit, alive = self._sse()

            def emit2(**kw):
                emit(**kw)
                if not alive[0]:
                    ls.stop(cancel=True)        # window closed / request aborted
            try:
                ls.run(emit2)
            except Exception as e:  # noqa: BLE001
                emit(t="error", text=str(e))
            emit(t="done")
        finally:
            Listen.current = None
            Listen.lock.release()
        return None

    def events(self):
        emit, alive = self._sse()
        q = BUS.sub()
        import queue
        try:
            emit(t="hello")
            while alive[0]:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    emit(t="ping")
                    continue
                emit(**ev)
        finally:
            BUS.unsub(q)
        return None

    def ask(self):
        body = json.loads(self._body() or b"{}")
        said = (body.get("prompt") or "").strip()
        extra = attach_prompt(body.get("attach"))
        prompt = (extra + "\n\n" + said).strip() if extra else said
        follow = bool(body.get("follow")) and not extra
        if follow:
            prompt = FOLLOW_NOTE + said
        if not prompt:
            return self._json({"error": "empty prompt"}, 400)
        workish = re.search(r"\b(code|file|files|fix|bug|commit|test|tests|run|build|refactor|install|folder|project|git|"
                            r"error|script|function|write|edit|create|make|deploy|server|repo|update|delete|rename|"
                            r"search|find|install|package|config)\b", said, re.I)
        ST.auto_pick = "haiku" if (len(said) < 160 and not extra and not workish) else ""
        if not ST.lock.acquire(blocking=False):
            return self._json({"error": "still working on the last one"}, 409)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            alive = [True]

            rec = {"ts": int(time.time()), "cwd": ST.cwd, "cwd_display": pretty_path(ST.cwd), "you": said or "(attachment)",
                   "him": "", "tools": [], "ok": True,
                   "attach": [a.get("label") for a in body.get("attach") or [] if isinstance(a, dict)]}

            # a follow-up: hold back his first words until we know it isn't "[ignore]"
            gate = {"open": not follow, "held": [], "text": "", "ignored": False}

            def emit(**kw):
                if not gate["open"]:
                    t = kw.get("t")
                    if t in ("delta", "result"):
                        gate["text"] += kw.get("text", "") if t == "delta" else ("" if gate["text"] else kw.get("text", ""))
                    said_so_far = gate["text"].strip().lower()
                    if said_so_far.startswith(IGNORE):                   # not meant for him: drop the whole reply
                        gate["open"] = gate["ignored"] = True
                        return send_ev({"t": "ignored"})
                    if t == "delta" and IGNORE.startswith(said_so_far):    # could still turn into "[ignore]"
                        gate["held"].append(kw)
                        return
                    if t in ("delta", "tool", "result", "error", "stopped"):  # it's a real answer: let it all through
                        gate["open"] = True
                        for h in gate["held"]:
                            real_emit(**h)
                        gate["held"].clear()
                    else:
                        gate["held"].append(kw)
                        return
                if gate["ignored"]:
                    return
                real_emit(**kw)

            def send_ev(kw):
                try:
                    self.wfile.write(b"data: " + json.dumps(kw).encode() + b"\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    alive[0] = False

            def real_emit(**kw):
                t = kw.get("t")
                if t == "delta":
                    rec["him"] += kw.get("text", "")
                elif t == "break" and rec["him"] and not rec["him"].endswith("\n\n"):
                    rec["him"] += "\n\n"
                elif t == "tool" and len(rec["tools"]) < 30:
                    rec["tools"].append(kw.get("name"))
                elif t == "result":
                    if not rec["him"].strip() and kw.get("text"):
                        rec["him"] = kw["text"]
                    rec["ok"] = not kw.get("is_error")
                elif t == "error":
                    rec["ok"] = False
                    rec["him"] = rec["him"] or kw.get("text", "")
                if not alive[0]:
                    return
                try:
                    self.wfile.write(b"data: " + json.dumps(kw).encode() + b"\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    alive[0] = False
                    stop_proc()

            ST.stopped = False
            partial = bool(CFG["partial_messages"])
            for _ in range(3):
                r = run_claude(prompt, emit, partial)
                if r == "retry_nopartial":
                    partial = False
                    continue
                if r == "retry_fresh":
                    ST.session = None
                    ST.save()
                    APPROVALS.reset()
                    emit(t="note", text="previous session couldn't be resumed — started fresh")
                    continue
                break
            if gate["ignored"]:
                return
            rec["session"] = ST.session
            rec["him"] = untag(rec["him"]).strip()[-6000:]
            try:
                hist_add(rec)
            except OSError:
                pass
            emit(t="done")
        finally:
            ST.lock.release()
        return None


def cpu_split(role):
    """Keep his face (the window) and his voice/brain (this server) on different CPU cores, so drawing can never
    starve the voice. Window → first half of the cores, server → second half. Children (the audio player, Kokoro's
    threads, WebKit's processes) inherit it. Off with "cpu_split": false."""
    try:
        cores = sorted(os.sched_getaffinity(0))
        if len(cores) < 4:                     # too few cores to share out: leave everything free
            return None
        half = max(1, len(cores) // 2)
        mine = set(cores[:half]) if role == "window" else set(cores[half:])
        os.sched_setaffinity(0, mine)
        return sorted(mine)
    except (AttributeError, OSError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-port", action="store_true")
    ap.add_argument("--doctor", action="store_true")
    ap.add_argument("--diagnostics", action="store_true", help="print the report that 'Copy diagnostics' copies")
    ap.add_argument("--warm", action="store_true", help="download/load the speech model and exit")
    ap.add_argument("--sync-autostart", action="store_true", help="add/remove login startup to match the config")
    ap.add_argument("--stress-voice", action="store_true", help="test the voice for stutters under heavy load")
    ap.add_argument("--silent", action="store_true", help="with --stress-voice: don't actually play the sound")
    ap.add_argument("--see", choices=("window", "screen"), help="`void see`: ask the running Void for a picture, print its path")
    args = ap.parse_args()
    if args.see:                                 # view-only: no clicking or typing, ever
        try:
            rt = json.loads((DATA_DIR / "runtime.json").read_text())
            port, tok = os.environ.get("VOID_PORT") or rt["port"], os.environ.get("VOID_TOKEN") or rt["tool_token"]
            import urllib.request
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/capture",
                                         data=json.dumps({"kind": args.see, "by": "claude"}).encode(),
                                         headers={"Content-Type": "application/json", "X-Void-Token": tok})
            j = json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:  # noqa: BLE001
            print(f"couldn't look: {getattr(e, 'read', lambda: b'')().decode() or e}", file=sys.stderr)
            sys.exit(1)
        print(f"{j['path']}  ({j['label']} — open it with the Read tool)")
        sys.exit(0)
    if args.sync_autostart:
        sync_autostart()
        print("autostart:", "on" if CFG.get("autostart") else "off")
        return None
    if args.doctor:
        return doctor()
    if args.diagnostics:
        try:                                     # just the last check's result (start() would also consume update.result)
            Updates.info = json.loads(Updates.cache().read_text())
        except (OSError, ValueError):
            pass
        print(diagnostics())
        return None
    if args.warm:
        m = whisper()
        print(f"speech model ready: {m._void_name} on {m._void_dev}")
        return None
    if CFG.get("cpu_split", True) and not args.print_port:
        got = cpu_split("server")
        if got:
            print(f"[void] voice & brain on CPU cores {got[0]}-{got[-1]} (the window gets the others)", flush=True)
    if args.stress_voice:
        sys.exit(stress_voice(silent=args.silent))
    if args.print_port:
        print(CFG["port"])
        return
    if CFG["stt_prewarm"] or CFG.get("wake_word"):
        threading.Thread(target=lambda: _try(whisper), daemon=True).start()
    _wire_quick()
    threading.Thread(target=warm_tts, daemon=True).start()
    Wake.ensure()
    Updates.start()
    srv = ThreadingHTTPServer(("127.0.0.1", int(CFG["port"])), Handler)
    try:                                       # where askpass.py finds us if sudo cleaned its environment (owner-only)
        rt = DATA_DIR / "runtime.json"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(rt, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"port": CFG["port"], "token": TOKEN, "tool_token": TOOL_TOKEN}, f)
    except OSError:
        pass
    srv.daemon_threads = True
    print(f"[void] http://127.0.0.1:{CFG['port']}/  cwd={ST.cwd}  tts={tts_engine()}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def _try(fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        print(f"[void] {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
