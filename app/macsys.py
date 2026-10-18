"""macOS versions of the parts of Void that talk to the operating system: microphone, speakers, screenshots,
clipboard, notifications, volume / music / opening apps / locking, and the bits of Cocoa his window needs.

The same shape as winsys.py, so server.py and quick.py call either one the same way. The mic and speakers are
sounddevice (PortAudio → Core Audio), exactly as on Windows, so those come straight from winsys. Everything else uses
the tools every Mac has: screencapture, pbpaste, osascript, open, pmset.
"""
import ctypes
import ctypes.util
import os
import subprocess
import threading
import time
from pathlib import Path

from winsys import ChannelPick, Mic, Player, can_play, list_mics  # noqa: F401  (sounddevice: the same on both)

NO_WINDOW = 0                   # (Windows' CREATE_NO_WINDOW: nothing to hide here)


def _run(cmd, timeout=10):
    """→ (ok, stdout)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def osa(script, *args, timeout=10):
    """Run AppleScript; `args` arrive as `item n of argv`, so nothing from outside is ever pasted into the script."""
    lines = ["on run argv", *script.strip().splitlines(), "end run"]
    cmd = ["osascript"]
    for ln in lines:
        cmd += ["-e", ln]
    return _run(cmd + [str(a) for a in args], timeout=timeout)


# ---------------------------------------------------------------- where things live (the same as on Linux)
def data_dir():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "void"


def config_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "void"


def cache_dir():
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "void"


# ---------------------------------------------------------------- microphone level (Core Audio's own: not yet)
def mic_level(hint=""):
    """None: Void boosts a quiet mic himself instead (the same as on Linux)."""
    return None


def set_mic_level(level, hint=""):
    return False


# ---------------------------------------------------------------- screenshots & clipboard
def active_window():
    """(x, y, w, h) of the front window, in screen points — None if there isn't one, it's Void, or macOS won't say
    (System Events needs Accessibility permission for this)."""
    ok, out = osa("""
tell application "System Events"
  tell front window of (first application process whose frontmost is true)
    if name is "Void" then return ""
    set {xy, wh} to {position, size}
  end tell
end tell
return (item 1 of xy as text) & "," & (item 2 of xy as text) & "," & (item 1 of wh as text) & "," & (item 2 of wh as text)""",
                  timeout=3)
    try:
        x, y, w, h = (int(float(v)) for v in out.split(","))
    except ValueError:
        return None
    return (x, y, w, h) if ok and w > 20 and h > 20 else None


def screenshot(kind, out):
    """kind: window | screen | region → saves a PNG at `out`, returns the kind it really got. (The first time, macOS
    asks whether the app running Void may record the screen.)"""
    out = str(out)
    if kind == "region":
        ok, _ = _run(["screencapture", "-x", "-i", out], timeout=120)       # drag out an area; Esc cancels
        if not os.path.exists(out):
            raise RuntimeError("cancelled")
        return "region"
    g = active_window() if kind == "window" else None
    cmd = ["screencapture", "-x"] + ([f"-R{g[0]},{g[1]},{g[2]},{g[3]}"] if g else ["-m"]) + [out]
    ok, err = _run(cmd, timeout=30)
    if not ok or not os.path.exists(out):
        raise RuntimeError("screenshot failed (System Settings → Privacy & Security → Screen Recording?) " + err[-160:])
    return "window" if g else "screen"


def clipboard(out):
    """→ ("image", path) or ("text", text)."""
    try:
        from PIL import Image, ImageGrab
        img = ImageGrab.grabclipboard()
        if isinstance(img, Image.Image):
            img.save(out)
            return "image", str(out)
    except Exception:  # noqa: BLE001
        pass
    ok, text = _run(["pbpaste"])
    return "text", text if ok else ""


# ---------------------------------------------------------------- notifications
def notify(title, body):
    threading.Thread(target=osa, args=("display notification (item 2 of argv) with title (item 1 of argv)",
                                       title, body[:300]), daemon=True).start()


# ---------------------------------------------------------------- volume, music, brightness, lock, apps
def volume_get():
    """→ (percent, muted) or (None, False)."""
    ok, out = osa("set v to get volume settings\nreturn (output volume of v as text) & \",\" & (output muted of v as text)")
    try:
        pct, muted = out.split(",")
        return int(pct), muted.strip() == "true"
    except ValueError:
        return None, False


def volume_set(op, val=None):
    """op: set | up | down | mute | unmute"""
    if op in ("mute", "unmute"):
        return osa(f"set volume output muted {'true' if op == 'mute' else 'false'}")[0]
    if op == "set":
        new = val
    else:
        cur, _ = volume_get()
        if cur is None:
            return False
        new = cur + (val or 10) * (1 if op == "up" else -1)
    return osa("set volume output volume (item 1 of argv as integer)", max(0, min(100, int(new))))[0]


PLAYERS = ("Spotify", "Music")


def _player():
    """Spotify or Music, whichever is open. (Only ever named once it's known to be installed: AppleScript asks
    "Where is Spotify?" in a dialog when a script names an app that isn't there.)"""
    installed = {nm for nm, _ in _apps()}
    return next((a for a in PLAYERS if a in installed and _run(["pgrep", "-xq", a])[0]), None)


def media(op):
    """op: pause | play | toggle | next | previous. False: no player is open."""
    app = _player()
    if not app:
        return False
    verb = {"pause": "pause", "play": "play", "toggle": "playpause", "next": "next track",
            "previous": "previous track"}[op]
    return osa(f'tell application "{app}" to {verb}')[0]


def now_playing():
    """'title by artist', or ''."""
    app = _player()
    if not app:
        return ""
    ok, out = osa(f'tell application "{app}"\nif player state is playing then return (name of current track) & " by " & '
                  '(artist of current track)\nend tell\nreturn ""')
    return out if ok else ""


def brightness(op, val=None):
    """Not on macOS yet → None."""
    return None


def lock():
    """Sleep the display: with "Require password … immediately" (the default) that locks the Mac."""
    return _run(["pmset", "displaysleepnow"])[0]


APP_DIRS = (Path("/Applications"), Path("/Applications/Utilities"), Path("/System/Applications"),
            Path("/System/Applications/Utilities"), Path.home() / "Applications")
_apps_cache = {"t": 0, "apps": []}


def _apps():
    """Every .app in the usual folders → [(name, path)]"""
    if time.monotonic() - _apps_cache["t"] > 300:
        apps = []
        for d in APP_DIRS:
            try:
                apps += [(p.stem, p) for p in d.iterdir() if p.suffix == ".app"]
            except OSError:
                pass
        _apps_cache.update(t=time.monotonic(), apps=apps)
    return _apps_cache["apps"]


def open_app(name):
    """Start an installed app by (part of) its name → its proper name, or None if nothing matches."""
    name = name.lower().strip(" .")
    best = None
    for nm, path in _apps():
        low = nm.lower()
        score = 3 if low == name else 2 if low.startswith(name) or name in low.split() else 1 if name in low else 0
        if score and (not best or score > best[0]):
            best = (score, nm, path)
    if not best:
        return None
    subprocess.Popen(["open", str(best[2])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return best[1]


def gpu_ok():
    """No CUDA on a Mac: speech recognition runs on the CPU."""
    return False


# ---------------------------------------------------------------- Cocoa, for his window (plain ctypes: no PyObjC)
_objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
_objc.objc_getClass.restype = ctypes.c_void_p
_objc.sel_registerName.restype = ctypes.c_void_p
_cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
_cg.CGEventSourceButtonState.argtypes = (ctypes.c_int32, ctypes.c_uint32)
_cg.CGEventSourceButtonState.restype = ctypes.c_bool


def _send(obj, sel, *args, restype=ctypes.c_void_p, argtypes=()):
    f = ctypes.cast(_objc.objc_msgSend, ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes))
    return f(obj, _objc.sel_registerName(sel.encode()), *args)


def no_dock_icon():
    """An accessory app: no Dock icon, no menu bar of its own (like a Windows tool window). Call after QApplication."""
    app = _send(_objc.objc_getClass(b"NSApplication"), "sharedApplication")
    _send(app, "setActivationPolicy:", 1, restype=ctypes.c_bool, argtypes=(ctypes.c_long,))   # Accessory


def _nswindow(win_id):
    return _send(ctypes.c_void_p(int(win_id)), "window")                  # Qt's winId() is the NSView


def set_click_through(win_id, on):
    """The whole window lets the mouse through to what's under it (on) or takes it (off)."""
    w = _nswindow(win_id)
    if w:
        _send(w, "setIgnoresMouseEvents:", bool(on), restype=None, argtypes=(ctypes.c_bool,))


def keep_on_all_spaces(win_id):
    """Stay up on every desktop (Space) and over full-screen apps, and don't hide when another app is in front."""
    w = _nswindow(win_id)
    if not w:
        return
    # canJoinAllSpaces | stationary | fullScreenAuxiliary | ignoresCycle
    _send(w, "setCollectionBehavior:", (1 << 0) | (1 << 4) | (1 << 8) | (1 << 6), restype=None,
          argtypes=(ctypes.c_ulong,))
    _send(w, "setHidesOnDeactivate:", False, restype=None, argtypes=(ctypes.c_bool,))


def mouse_down():
    """The left mouse button is held (anywhere)."""
    return bool(_cg.CGEventSourceButtonState(0, 0))                        # combined session state, left button
