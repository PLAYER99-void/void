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

import winsys
from winsys import ChannelPick, Mic, Player, can_play, list_mics  # noqa: F401  (sounddevice: the same on both)

NO_WINDOW = 0                   # (Windows' CREATE_NO_WINDOW: nothing to hide here)


def _run(cmd, timeout=10):
    """→ (ok, stdout)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           env={**os.environ, "LANG": "en_US.UTF-8"})   # (no LANG → pbpaste hands back MacRoman)
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


# ---------------------------------------------------------------- which speakers and mics there are (Core Audio's own)
class _Addr(ctypes.Structure):
    _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("elem", ctypes.c_uint32)]


_ca = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreAudio"))
_ca.AudioObjectGetPropertyDataSize.argtypes = (ctypes.c_uint32, ctypes.POINTER(_Addr), ctypes.c_uint32, ctypes.c_void_p,
                                               ctypes.POINTER(ctypes.c_uint32))
_ca.AudioObjectGetPropertyData.argtypes = (ctypes.c_uint32, ctypes.POINTER(_Addr), ctypes.c_uint32, ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p)


def _ca_ids(sel):
    """A device-list property of the whole audio system ('dOut', 'dIn ', 'dev#') → its device ids."""
    a, n = _Addr(int.from_bytes(sel.encode(), "big"), int.from_bytes(b"glob", "big"), 0), ctypes.c_uint32(0)
    if _ca.AudioObjectGetPropertyDataSize(1, ctypes.byref(a), 0, None, ctypes.byref(n)):
        raise OSError(f"Core Audio won't say ({sel})")
    ids = (ctypes.c_uint32 * (n.value // 4))()
    if _ca.AudioObjectGetPropertyData(1, ctypes.byref(a), 0, None, ctypes.byref(n), ids):
        raise OSError(f"Core Audio won't say ({sel})")
    return tuple(ids[:n.value // 4])


def audio_devices():
    """(default output, default input, every device): it changes whenever a speaker or mic comes, goes or is picked.
    Asked of Core Audio itself (~50 µs), since PortAudio's own list is the one it read when it started."""
    return _ca_ids("dOut"), _ca_ids("dIn "), _ca_ids("dev#")


winsys.devices = audio_devices                         # (so a Player or Mic opens on the list as it is now)


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


def no_resize(win_id):
    """Neither you nor macOS can resize the window: drag it to a screen edge or corner and Sequoia offers to tile it
    into half the screen, which leaves him a box. Resizing and moving it from code (setFrame) still work."""
    w = _nswindow(win_id)
    if not w:
        return
    mask = _send(w, "styleMask", restype=ctypes.c_ulong)
    if mask & 8:                                                          # NSWindowStyleMaskResizable
        _send(w, "setStyleMask:", mask & ~8, restype=None, argtypes=(ctypes.c_ulong,))


def unconstrained(win_id):
    """His window goes exactly where Void puts it, flush with the top of the screen too. AppKit otherwise pushes any
    window that reaches above where the menu bar would be back down under it (constrainFrameRect:toScreen:, whenever
    it's shown or the Space changes), menu bar or not: over a full-screen app he'd hang a bar's height down with no
    bar there. The override AppKit documents for this, for Qt's window class (so his notch's ghost too): it hands
    the frame back as asked. ctypes can't return a CGRect from Python, so the method is NSWindow's own
    frameRectForContentRect:, which takes the same rect first (the screen after it is ignored) and, for a window
    with no title bar, returns it unchanged."""
    w = _nswindow(win_id)
    if not w or _send(w, "styleMask", restype=ctypes.c_ulong) & 1:        # (titled: that method would add a title bar)
        return
    o = _objc
    o.class_getInstanceMethod.restype = o.method_getImplementation.restype = ctypes.c_void_p
    o.class_getInstanceMethod.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    o.method_getImplementation.argtypes = (ctypes.c_void_p,)
    o.class_addMethod.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)
    same = o.method_getImplementation(o.class_getInstanceMethod(o.objc_getClass(b"NSWindow"),
                                                                o.sel_registerName(b"frameRectForContentRect:")))
    rect = b"{CGRect={CGPoint=dd}{CGSize=dd}}"
    o.class_addMethod(_send(w, "class"), o.sel_registerName(b"constrainFrameRect:toScreen:"), same,
                      rect + b"@:" + rect + b"@")                          # (once per class: then it's there already)


def mouse_down():
    """The left mouse button is held (anywhere)."""
    return bool(_cg.CGEventSourceButtonState(0, 0))                        # combined session state, left button


# ---------------------------------------------------------------- the menu bar and the Dock, as they are right now
class _Rect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


class _Insets(ctypes.Structure):
    _fields_ = [("top", ctypes.c_double), ("left", ctypes.c_double), ("bottom", ctypes.c_double), ("right", ctypes.c_double)]


_appkit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("AppKit"))
DOCK_HIDDEN = 8                 # a Dock that hides itself still keeps a sliver of the screen (4 pt): that's no Dock


def _screens():
    """Every screen as (frame, visibleFrame, the camera housing's height), Cocoa's way: points, y up from the bottom
    of the main screen. visibleFrame is what the menu bar and the Dock leave, and a Dock that hides itself (nearly)
    doesn't count — but in a full-screen app's Space it still keeps their room, though neither is there."""
    arr = _send(_objc.objc_getClass(b"NSScreen"), "screens")
    out = []
    for i in range(_send(arr, "count", restype=ctypes.c_ulong) if arr else 0):
        s = _send(arr, "objectAtIndex:", i, argtypes=(ctypes.c_ulong,))
        f, v = _send(s, "frame", restype=_Rect), _send(s, "visibleFrame", restype=_Rect)
        safe = 0.0
        if _send(s, "respondsToSelector:", _objc.sel_registerName(b"safeAreaInsets"), restype=ctypes.c_bool,
                 argtypes=(ctypes.c_void_p,)):                            # (macOS 12+)
            safe = _send(s, "safeAreaInsets", restype=_Insets).top
        out.append(((f.x, f.y, f.w, f.h), (v.x, v.y, v.w, v.h), safe))
    return out


_cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
_cg.CGWindowListCopyWindowInfo.argtypes = (ctypes.c_uint32, ctypes.c_uint32)
_cg.CGRectMakeWithDictionaryRepresentation.restype = ctypes.c_bool
_cg.CGRectMakeWithDictionaryRepresentation.argtypes = (ctypes.c_void_p, ctypes.POINTER(_Rect))
_cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
_cf.CFRelease.argtypes = (ctypes.c_void_p,)
MENU_BAR_LAYER, DOCK_LAYER = 24, 20                                       # kCGMainMenuWindowLevel, kCGDockWindowLevel


def _bars():
    """The menu bars and the Dock as they're really drawn right now: [("menu" | "dock", (x, y, w, h))], Qt's way
    (points, y down from the top of the main screen), from the windows on screen; None if those can't be had. In a
    full-screen app's Space neither one's window is on screen (each comes only when you reach for it)."""
    info = _cg.CGWindowListCopyWindowInfo(1, 0)                           # kCGWindowListOptionOnScreenOnly, every window
    if not info:
        return None
    key = lambda n: ctypes.c_void_p.in_dll(_cg, n)                        # noqa: E731  (CoreGraphics' own CFString keys)
    get = lambda d, k: _send(d, "objectForKey:", key(k), argtypes=(ctypes.c_void_p,))   # noqa: E731
    out = []
    try:
        for i in range(_send(info, "count", restype=ctypes.c_ulong)):
            d = _send(info, "objectAtIndex:", i, argtypes=(ctypes.c_ulong,))
            layer = _send(get(d, "kCGWindowLayer"), "intValue", restype=ctypes.c_int)
            if layer not in (MENU_BAR_LAYER, DOCK_LAYER):
                continue
            owner = _send(get(d, "kCGWindowOwnerName"), "UTF8String", restype=ctypes.c_char_p) or b""
            r = _Rect()
            if (layer == MENU_BAR_LAYER or owner == b"Dock") and \
                    _cg.CGRectMakeWithDictionaryRepresentation(get(d, "kCGWindowBounds"), ctypes.byref(r)):
                out.append(("menu" if layer == MENU_BAR_LAYER else "dock", (r.x, r.y, r.w, r.h)))
    finally:
        _cf.CFRelease(info)
    return out


def edge_insets(screens, x, y, w, h, bars=None):
    """How far in from each edge of the screen whose whole frame is Qt's (x, y, w, h) (points, y down from the top of
    the main screen) the menu bar, the Dock and a camera housing reach → {"top", "bottom", "left", "right"}, plus
    "menu": the room the menu bar takes when it's there (it may slide down over a full-screen app), or None when no
    screen has that frame. bars (from _bars()): the menu bar only counts while one is on screen at this screen's top,
    the Dock only while its window is on this screen (visibleFrame keeps both over a full-screen app); None →
    visibleFrame alone. A notch never hangs behind the camera, even where there's no menu bar."""
    if not screens:
        return None
    top0 = screens[0][0][1] + screens[0][0][3]                            # (the main screen's top: Qt's y = 0)
    for (fx, fy, fw, fh), (vx, vy, vw, vh), safe in screens:
        if max(abs(fx - x), abs(top0 - fy - fh - y), abs(fw - w), abs(fh - h)) >= 1:
            continue
        menu = has_dock = True
        if bars is not None:
            on = lambda bx, by, bw, bh: bx < x + w and bx + bw > x and by < y + h and by + bh > y   # noqa: E731
            menu = any(k == "menu" and abs(r[1] - y) < 1 and on(*r) for k, r in bars)
            has_dock = any(k == "dock" and on(*r) for k, r in bars)
        dock = lambda d: 0 if not has_dock or d < DOCK_HIDDEN else round(d)   # noqa: E731
        room = round(max(fy + fh - vy - vh, 0))
        return {"top": round(max(room if menu else 0, safe)), "bottom": dock(vy - fy),
                "left": dock(vx - fx), "right": dock(fx + fw - vx - vw), "menu": room}
    return None


def edge_insets_now(x, y, w, h):
    return edge_insets(_screens(), x, y, w, h, _bars())


_watch = {}


def watch_screens(cb):
    """Call cb() (on the main thread) when where the menu bar and the Dock are may have changed: another Space became
    active (a full-screen app's, say), or the screens' usable areas changed (the Dock moved, or began or stopped
    hiding itself; the menu bar did). → False if the observer couldn't be set up (then poll instead)."""
    if _watch:
        return True
    try:
        o = _objc
        o.objc_allocateClassPair.restype = ctypes.c_void_p
        o.objc_allocateClassPair.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t)
        o.class_addMethod.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)
        o.objc_registerClassPair.argtypes = (ctypes.c_void_p,)
        imp = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(lambda me, sel, note: _fire())
        cls = o.objc_allocateClassPair(o.objc_getClass(b"NSObject"), b"VoidScreenWatch", 0)
        if not cls:
            return False
        sel = o.sel_registerName(b"screensChanged:")
        o.class_addMethod(cls, sel, ctypes.cast(imp, ctypes.c_void_p), b"v@:@")
        o.objc_registerClassPair(cls)
        obs = _send(_send(cls, "alloc"), "init")
        name = lambda n: ctypes.c_void_p.in_dll(_appkit, n)                # noqa: E731  (AppKit's own NSString constants)
        ws = _send(_send(o.objc_getClass(b"NSWorkspace"), "sharedWorkspace"), "notificationCenter")
        nc = _send(o.objc_getClass(b"NSNotificationCenter"), "defaultCenter")
        add = ("addObserver:selector:name:object:", (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p))
        _send(ws, add[0], obs, sel, name("NSWorkspaceActiveSpaceDidChangeNotification"), None, restype=None, argtypes=add[1])
        _send(nc, add[0], obs, sel, name("NSApplicationDidChangeScreenParametersNotification"), None, restype=None,
              argtypes=add[1])
    except (OSError, ValueError, AttributeError):
        return False
    _watch.update(imp=imp, obs=obs, cb=cb)                                 # (kept: Cocoa calls these for good)
    return True


def _fire():
    try:
        _watch["cb"]()
    except Exception:  # noqa: BLE001  (never into Cocoa)
        pass
