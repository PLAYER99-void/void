"""Void's window on Windows: a see-through, always-on-top, frameless Qt window with his page in it (QtWebEngine,
i.e. Chromium). It speaks the same messages with the page as window.py (the GTK/WebKit window on Linux). macOS uses
this window too, with macsys.py for the few things Qt can't do itself.

    window_win.py URL [toggle|show|hide|quit|listen|look|live|hover|hover-on|hover-off]
    window_win.py URL --selftest out.png      (load him, save a picture of the window, quit — for test machines)
    window_win.py URL --selftest-bar EDGE out.png   (the same, docked on a screen edge as the top bar; prints where)

The first instance owns the window; later calls just send it a command over a local socket, so `void listen` on a
hotkey pops him up and listens. Only the parts of the window you can see take clicks (the page says which); the rest
clicks through to whatever is underneath.
"""
import ctypes
import json
import math
import os
import socket
import sys
import threading
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
MAC = sys.platform == "darwin"
if MAC:
    import macsys as winsys  # noqa: E402  (the same shape: data_dir & co., plus the Cocoa bits used below)
else:
    import winsys  # noqa: E402
for _s in (sys.stdout, sys.stderr):               # logs go to a file: write UTF-8, not the old Windows code page
    if _s is not None and hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

DATA = winsys.data_dir()
SELFTEST = False                                                         # --selftest / --selftest-bar run (main() sets it)


class Started:
    """The one record of the server this app started (the launcher's, or the window's own restart of it): its pid
    (the leader of its own process group, so its children share the group) and its run id. Quitting stops that server
    and its children, and only if the server answering on its port still has that run id: one this app didn't
    start is never touched."""

    @staticmethod
    def _file():
        return DATA / "started.json"

    @classmethod
    def record(cls, pid, run_id):
        cls._file().write_text(json.dumps({"pid": int(pid), "id": run_id}))

    @classmethod
    def stop(cls):
        if sys.platform == "win32" or SELFTEST:                          # (a selftest never stops a server)
            return
        try:
            rec = json.loads(cls._file().read_text())
            pid, rid = int(rec["pid"]), rec["id"]
            port = json.loads((DATA / "runtime.json").read_text())
            if not rid or port.get("id") != rid or port.get("pid") != pid:   # (not a racer's dead or reused pid)
                cls._forget()
                return
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{int(port['port'])}/api/id", timeout=2) as r:
                if json.load(r).get("id") != rid:
                    cls._forget()
                    return
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return                                                        # (undecided: the record stays for next time)
        cls._forget()
        import signal
        try:
            group = os.getpgid(pid) == pid                                # (never the group of whoever launched it)
        except OSError:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig) if group else os.kill(pid, sig)
            except OSError:
                return
            for _ in range(20 if sig == signal.SIGTERM else 5):
                time.sleep(0.1)
                if cls._gone(pid):
                    return

    @classmethod
    def _forget(cls):
        try:
            cls._file().unlink()
        except OSError:
            pass

    @staticmethod
    def _gone(pid):
        """Has the server gone? Ours is our own child (a zombie until reaped, which kill(pid, 0) never notices): reap it."""
        try:
            return os.waitpid(pid, os.WNOHANG)[0] == pid
        except ChildProcessError:                                         # not our child: just ask whether it exists
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            return False
        except OSError:
            return True


if __name__ == "__main__" and sys.argv[1:2] in (["--started"], ["--stop-started"]):   # the launcher's way in (no Qt)
    if sys.argv[1] == "--started":
        Started.record(sys.argv[2], sys.argv[3])
    else:
        Started.stop()
    sys.exit(0)

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--autoplay-policy=no-user-gesture-required")
from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, QPoint, Qt, QTimer, QUrl, Signal, Slot  # noqa: E402
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QPainter  # noqa: E402
from PySide6.QtWebChannel import QWebChannel  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript, QWebEngineSettings  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

W, H = 340, 460
WIDE = 470                   # while settings / history / memory are open the window widens to this
CODE_SIDE = 100              # room either side for his terminal
BAR_SIDE = 70                # docked at the top / bottom: room either side of the notch, so his glow fades out in it
ROOM = max(WIDE - W, CODE_SIDE, BAR_SIDE)   # always kept on his left, so he never moves in the window (see set_wide)
GLIDE = 0.26                 # seconds the window takes to glide into the notch and back (quick: it should feel snappy)
MENU_LOOK = 150              # ms between looks for a full-screen app's menu bar sliding down over the notch (macOS)
MENU_STEADY = 3              # looks in a row that must agree before he moves (a flick past the edge mustn't bob him)
MENU_NEAR = 3.0              # s the looks stay quick after the mouse was last up by the top edge; after that, one…
MENU_SLOW = 2.0              # …every this many s (it can stay down with the mouse away: a menu opened from it)
BALL = (170, 165)            # where the ball's centre sits inside the window
POS_FILE, PORT_FILE = DATA / "pos.json", DATA / "window.port"

# the page talks to us through window.webkit.messageHandlers.voidui.postMessage (WebKit's API on Linux); on Windows
# that becomes a QWebChannel call. Messages sent before the channel is up are queued.
SHIM = """
window.voidQt = true;   // Chromium through Qt, not WebKitGTK: the page skips its WebKit-only workarounds
(function(){ var q = [], b = null;
  window.webkit = {messageHandlers: {voidui: {postMessage: function(m){ m = String(m); if (b) b.post(m); else q.push(m); }}}};
  (function go(){
    if (typeof QWebChannel === 'undefined' || !window.qt || !qt.webChannelTransport) return setTimeout(go, 30);
    new QWebChannel(qt.webChannelTransport, function(ch){ b = ch.objects.voidui; q.forEach(function(m){ b.post(m); }); q = []; });
  })();
})();
"""


# push-to-talk combos like "Ctrl+Alt+Space" → ({"ctrl", "alt"}, virtual-key code of Space)
MOD_VKS = {"ctrl": (0x11,), "alt": (0x12,), "shift": (0x10,), "win": (0x5B, 0x5C)}
KEY_VKS = {"space": 0x20, "`": 0xC0, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
           "pause": 0x13, **{f"f{n}": 0x6F + n for n in range(1, 25)}}


def parse_combo(text):
    parts = [p.strip().lower() for p in (text or "").split("+") if p.strip()]
    if not parts:
        return None
    key, mods = parts[-1], frozenset(p for p in parts[:-1] if p in MOD_VKS)
    if key in KEY_VKS:
        vk = KEY_VKS[key]
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())                                            # A–Z and 0–9 are their own codes
    else:
        return None
    return mods, vk


def log(msg):
    print(f"[void] {msg}", file=sys.stderr, flush=True)


def send(cmd):
    """Pass a command to the running window. False if there isn't one."""
    try:
        port, secret = PORT_FILE.read_text().split()[:2]                 # "port secret" (see VoidWindow._serve)
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1) as s:
            s.sendall(f"{secret} {cmd}".encode())
        return True
    except (OSError, ValueError):
        return False


class Bridge(QObject):
    msg = Signal(str)

    @Slot(str)
    def post(self, m):
        self.msg.emit(m)


class Remote(QObject):
    """Commands arriving on the socket thread → the UI thread."""
    cmd = Signal(str)


class ScreenWatch(QObject):
    """macOS: the menu bar or the Dock may have come, gone or moved (from Cocoa's notifications)."""
    changed = Signal()


class Ghost(QWidget):
    """The notch left on the wall when he's pulled out of it: it melts back into the screen edge on its own (black,
    shrinking into the edge) while he goes wherever you take him. Click-through, never takes focus, gone in 0.3 s."""
    def __init__(self, x, y, w, h, edge):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.edge, self.k, self.t0 = edge, 1.0, time.monotonic()
        self.setGeometry(int(x), int(y), int(w), int(h))
        self.timer = QTimer(self, interval=10, timeout=self._step)
        self.timer.start()
        self.show()

    def _step(self):
        p = min(1.0, (time.monotonic() - self.t0) / 0.3)
        self.k = 1 - p * p                                            # eases in: it lets go, then is drawn away
        self.update()
        if p >= 1:
            self.timer.stop()
            self.close()
            self.deleteLater()

    def paintEvent(self, _ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setPen(Qt.NoPen)
        qp.setBrush(QColor(0, 0, 0))
        w, h, k, o = self.width(), self.height(), self.k, 24          # o: runs off the edge, so only its open side is round
        if self.edge in ("top", "bottom"):
            rw, rh = w * (0.55 + 0.45 * k), h * k
            x, y = (w - rw) / 2, (-o if self.edge == "top" else h - rh)
            qp.drawRoundedRect(int(x), int(y), int(rw), int(rh + o), min(18, rh), min(18, rh))
        else:
            rw, rh = w * k, h * (0.55 + 0.45 * k)
            x, y = (-o if self.edge == "left" else w - rw), (h - rh) / 2
            qp.drawRoundedRect(int(x), int(y), int(rw + o), int(rh), min(18, rw), min(18, rw))
        qp.end()


class VoidWindow(QWidget):
    def __init__(self, url):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool   # Tool: no taskbar button
                         | Qt.NoDropShadowWindowHint)   # (macOS: its shadow traces a black ring round his glow)
        self.setWindowTitle("Void")
        self.setWindowIcon(QIcon(str(APP / "static" / "icon.png")))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.cur_w, self.wide_shift, self.want_panel, self.want_code = W + ROOM, ROOM, False, False
        self.box_l = 0                                                   # px of the room on his left in use (set_wide)
        self.own_pos, self.unresize_due = None, False                    # his last spot at his own size (see resizeEvent)
        self.resize(self.cur_w, H)
        self.hover, self.hpos, self.home, self.rects, self.through = False, None, None, None, None
        self.bar, self.bar_home = False, None                                # top bar: his eyes in a notch up top
        self.want_bar, self.glide_n = False, 0                               # (room for the notch's glow; current glide)
        self.bar_inset = 0                                                   # px the notch hangs in from the screen edge
        self.shown = True
        self.save_t = QTimer(self, singleShot=True, interval=700, timeout=self._save_pos)   # save after you stop moving him

        v = self.view = QWebEngineView(self)
        page = v.page()
        page.setBackgroundColor(QColor(0, 0, 0, 0))
        st = page.settings()
        st.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
        st.setAttribute(QWebEngineSettings.ShowScrollBars, False)
        v.setContextMenuPolicy(Qt.NoContextMenu)                    # right-click is his own menu
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(v)

        self.bridge = Bridge()
        self.bridge.msg.connect(self._on_msg)
        chan = QWebChannel(page)
        chan.registerObject("voidui", self.bridge)
        page.setWebChannel(chan)
        f = QFile(":/qtwebchannel/qwebchannel.js")
        f.open(QIODevice.ReadOnly)
        sc = QWebEngineScript()
        sc.setSourceCode(bytes(f.readAll()).decode() + SHIM + f"window.voidLayoutX={self.wide_shift};")   # (from frame 1)
        sc.setInjectionPoint(QWebEngineScript.DocumentCreation)
        sc.setWorldId(QWebEngineScript.MainWorld)
        page.scripts().insert(sc)
        page.featurePermissionRequested.connect(                      # the server records the mic, not the page
            lambda origin, feat: page.setFeaturePermission(origin, feat, QWebEnginePage.PermissionDeniedByUser))
        page.renderProcessTerminated.connect(lambda *a: (log("page renderer stopped — reloading"),
                                                         QTimer.singleShot(400, v.reload)))
        page.loadFinished.connect(lambda ok: self._js(f"window.voidRoom&&voidRoom({self.box_l})"))   # (a reload too)
        self.url = url
        lay.activate()                     # (give the view its real size before the page first measures it: else it can
        v.load(QUrl(self._authed_url()))  #  read Qt's 100x30 placeholder, scale him to a speck and never hear otherwise)

        self._load_pos()
        self.show()
        if MAC:
            self._mac_native()
            self._watch_screens()
        self.remote = Remote()
        self.remote.cmd.connect(self._remote_cmd)
        threading.Thread(target=self._serve, daemon=True).start()
        self.base = url.split("?")[0]
        self.quitting = False                                            # (stops the watchdog: no restart while quitting)
        threading.Thread(target=self._watch_server, daemon=True).start()
        QApplication.instance().aboutToQuit.connect(self._about_to_quit)   # (macOS's own Quit too)
        self.ptt, self.ptt_held = None, False                           # the push-to-talk combo (the page sends it)
        threading.Thread(target=self._ptt_hook, daemon=True).start()
        QTimer.singleShot(1500, self._tell_page)
        self.poll = QTimer(self, interval=33, timeout=self._poll_mouse)    # click-through ~30×/s; hover ~120×/s
        self.poll.setTimerType(Qt.PreciseTimer)
        self.poll.start()
        self.look_t = 0.0

    # ---- position (remembered across runs)
    def _screen(self):
        """The screen his own strip is on (the room on his left may hang over onto the next one)."""
        c = QPoint(self.x() + self.wide_shift + W // 2, self.y() + H // 2)
        return QGuiApplication.screenAt(c) or QGuiApplication.primaryScreen()

    def _screen_rect(self):
        return self._screen().availableGeometry()                         # minus the taskbar

    def _load_pos(self):
        try:
            p = json.loads(POS_FILE.read_text())
            x, y = int(p["x"]), int(p["y"])
            if not any(s.availableGeometry().contains(x + W // 2, y + H // 2) for s in QGuiApplication.screens()):
                raise ValueError                                          # that monitor is gone
        except Exception:  # noqa: BLE001
            g = QGuiApplication.primaryScreen().availableGeometry()
            x, y = g.right() - W - 10, g.bottom() - H - 10                # default: bottom-right corner
        self.move(x - self.wide_shift, y)

    def moveEvent(self, ev):
        if (self.width(), self.height()) == (self.cur_w, H):              # (not mid-way through someone else's resize)
            self.own_pos = (self.x(), self.y())
        if not self.hover and not self.bar:
            self.save_t.start()
        super().moveEvent(ev)

    # ---- only Void sizes his window (macOS tiling would squash him into half the screen)
    def _mac_native(self):
        """The Cocoa side of his window: on every Space, not resizable by you or by macOS, and not pushed down off the
        top edge by macOS (docked up there, the notch hangs exactly as far in as the page says)."""
        if not self.internalWinId():                                     # (gone: don't make a new one just for this)
            return
        winsys.keep_on_all_spaces(self.winId())
        winsys.no_resize(self.winId())
        winsys.unconstrained(self.winId())

    def event(self, ev):
        # Qt puts Resizable back when the window's flags change (which needs a show after) or the NSWindow is rebuilt
        # (a new id); a moment later, once the NSWindow is up
        if MAC and ev.type() in (QEvent.Show, QEvent.WinIdChange):
            QTimer.singleShot(0, self._mac_native)
        return super().event(ev)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if MAC and (ev.size().width(), ev.size().height()) != (self.cur_w, H) and not self.unresize_due:
            self.unresize_due = True                                      # every size of his own sets cur_w first
            QTimer.singleShot(0, self._unresize)

    def _unresize(self):
        """Something else resized him (tiling got through anyway): straight back to his own size and spot, the page
        at full size, before he's left a box in half the screen with his page scaled to fit it."""
        self.unresize_due = False
        w, h = self.width(), self.height()
        if (w, h) == (self.cur_w, H):
            return
        x, y = self.own_pos or (self.x(), self.y())
        log(f"window resized to {w}x{h} from outside — back to {self.cur_w}x{H}")
        self.setGeometry(x, y, self.cur_w, H)
        self._mac_native()
        self._js(f"window.voidLayout&&voidLayout({self.wide_shift})")

    def _save_pos(self):
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            POS_FILE.write_text(json.dumps({"x": self.x() + self.wide_shift, "y": self.y()}))
        except OSError:
            pass

    # ---- page plumbing
    def _js(self, code):
        self.view.page().runJavaScript(code)

    def _tell_page(self):
        self._js("window.voidMode&&voidMode(%s)" % json.dumps({"layer": False, "cursor": True, "why": ""}))

    def _on_msg(self, msg):
        if msg.startswith("region:"):
            try:
                self.rects = json.loads(msg[7:])
            except ValueError:
                self.rects = None
            return
        if msg == "reauth":                                              # the server restarted: hand over its new token
            self.view.load(QUrl(self._authed_url()))
            return
        if msg.startswith("ptt:"):
            self.ptt = parse_combo(msg[4:])
            return
        if msg.startswith("dragby:"):
            try:
                dx, dy = (float(x) for x in msg[7:].split(","))
                self.move(self.x() + int(dx), self.y() + int(dy))
            except ValueError:
                pass
            return
        {"drag": self._drag, "pmove": self._pmove, "dragend": lambda: self.bar or self._save_pos(), "hide": self.hide_me, "show": self.show_me,
         "hover:on": lambda: self.set_hover(True), "hover:off": lambda: self.set_hover(False),
         "bar:top": lambda: self.set_bar("top"), "bar:bottom": lambda: self.set_bar("bottom"),
         "bar:left": lambda: self.set_bar("left"), "bar:right": lambda: self.set_bar("right"),
         "bar:off": lambda: self.set_bar(None),
         "wide:on": lambda: self.set_wide(True), "wide:off": lambda: self.set_wide(False),
         "code:on": lambda: self.set_wide(True, "code"), "code:off": lambda: self.set_wide(False, "code"),
         "kbd:grab": self._grab_keys, "kbd:release": lambda: None,
         "peek:hide": self.hide, "peek:show": self.show_me}.get(msg, lambda: None)()

    # ---- the bar: merged into a screen edge as a notch (the page draws it on the matching side of the window)
    def _bar_area(self, scr):
        """Where the bar docks on `scr`: the rect its window sits flush in, and how far in from each of its edges the
        notch hangs. macOS: the whole screen, the notch just under the menu bar or beside the Dock only while they're
        really there (AppKit, asked now: Qt's availableGeometry always keeps room for the menu bar, even over a
        full-screen app, and isn't asked again when you change Space). Elsewhere: what the taskbar leaves, flush."""
        if MAC:
            g = scr.geometry()
            ins = winsys.edge_insets_now(g.x(), g.y(), g.width(), g.height())
            if ins:
                return g, ins
        return scr.availableGeometry(), {"top": 0, "bottom": 0, "left": 0, "right": 0}

    @staticmethod
    def _bar_xy(edge, g):
        return ({"top": g.center().x() - W // 2, "bottom": g.center().x() - W // 2, "left": g.left(), "right": g.right() - W + 1}[edge],
                {"top": g.top(), "bottom": g.bottom() - H + 1, "left": g.center().y() - H // 2, "right": g.center().y() - H // 2}[edge])

    def _set_inset(self, px, glide):
        """Tell the page how far in the notch hangs; glide: it oozes there (the window itself stays put)."""
        if px != self.bar_inset or not glide:
            self.bar_inset = px
            self._js(f"window.voidBarInset&&voidBarInset({px},{'true' if glide else 'false'})")

    def _watch_screens(self):
        """macOS: look again where the notch should hang whenever the menu bar or the Dock may have changed — at once,
        and once more when a Space switch has settled. Without Cocoa's notifications, a cheap look every 2 s."""
        self.scr_watch = ScreenWatch()
        self.scr_watch.changed.connect(lambda: (self._screens_changed(), QTimer.singleShot(700, self._screens_changed)))
        if not winsys.watch_screens(self.scr_watch.changed.emit):
            log("no screen notifications — checking the menu bar and the Dock every 2 s instead")
            self.scr_poll = QTimer(self, interval=2000, timeout=self._screens_changed)
            self.scr_poll.start()
        self.menu_t = QTimer(self, interval=MENU_LOOK, timeout=self._menu_tick)
        self.menu_room, self.menu_want, self.menu_n, self.menu_near, self.menu_look = 0, 0, 0, 0.0, 0.0

    def _watch_menu(self, ins):
        """Docked at the top of a screen with a menu bar: over a full-screen app it slides down over him when the
        mouse reaches the top edge (and back up when it leaves), and nothing says so — no Space or screen change.
        So look (_menu_tick) while he's up there."""
        if self.bar == "top" and ins.get("menu", 0) > 0:
            self.menu_room, self.menu_want, self.menu_n = ins["menu"], ins["top"], MENU_STEADY
            self.menu_look = time.monotonic()
            self.menu_t.start()
        else:
            self.menu_t.stop()

    def _menu_tick(self):
        """Is the menu bar down over him? Looked at every MENU_LOOK ms while the mouse is (or just was) up by the top
        edge, the only thing that brings it down, else every MENU_SLOW s (a look is ~1 ms). Once MENU_STEADY looks
        agree he drips down under it, or is slurped back up when it's gone."""
        if self.bar != "top" or self.hover:
            self.menu_t.stop()
            return
        scr = self._screen()
        g, c, now = scr.geometry(), QCursor.pos(), time.monotonic()
        if g.left() <= c.x() <= g.right() and g.top() <= c.y() < g.top() + self.menu_room + 8:
            self.menu_near = now
        if now - self.menu_near > MENU_NEAR and self.menu_n >= MENU_STEADY and now - self.menu_look < MENU_SLOW:
            return
        self.menu_look = now
        want = self._bar_area(scr)[1]["top"]
        self.menu_n = self.menu_n + 1 if want == self.menu_want else 1
        self.menu_want = want
        if self.menu_n >= MENU_STEADY:
            self._set_inset(want, glide=True)

    def _screens_changed(self):
        if not self.bar or self.hover:
            return
        scr = self._screen()
        g, ins = self._bar_area(scr)
        x, y = self._bar_xy(self.bar, g)
        if (x, y) != (self.x() + self.wide_shift, self.y()):            # (the screen itself changed)
            self._glide_to(x, y)
        self._set_inset(ins[self.bar], glide=True)
        self._watch_menu(ins)

    def _ghost_rect(self):
        """The notch as it is on the wall (where it melts away when he's pulled out of it)."""
        bx, y, e, i = self.x() + self.wide_shift, self.y(), self.bar, self.bar_inset
        return {"top": (bx + 170 - 140, y + i, 280, 52), "bottom": (bx + 170 - 140, y + H - 52 - i, 280, 52),
                "left": (bx + i, y + 230 - 115, 68, 230), "right": (bx + W - 68 - i, y + 230 - 115, 68, 230)}[e]

    def set_bar(self, edge, to=None, glide=True):
        """Dock on `edge` (top / bottom / left / right) of the screen he's on, or undock (None): back to the blob's
        own spot, or to `to` when he's pulled out by the mouse. Tells the page with voidDock()."""
        edge = edge if edge in ("top", "bottom", "left", "right") else None
        if edge:
            if not self.bar:
                self.bar_home = (self.x() + self.wide_shift, self.y())
            self.bar = edge
            scr = self._screen()
            g, ins = self._bar_area(scr)                                 # (the bottom edge sits above the taskbar)
            x, y = self._bar_xy(edge, g)
            self._set_inset(ins[edge], glide=False)                      # (the page eases there as he docks)
            if MAC:
                self._watch_menu(ins)
            # glide into place while the page morphs, growing the window round the notch for its glow on the way
            # (no growing the window round the notch: it flickered; the notch's glow is sized to fit the window)
            self._glide_to(x, y)
        else:
            was, self.bar = self.bar, None
            if to:                                                       # pulled out by the mouse: right under it
                self.set_wide(False, "bar")
                self.glide_n += 1
                self.move(int(to[0]), int(to[1]))
                self._save_pos()
            elif was and self.bar_home:                                  # back to his spot, gliding while he pops out
                if glide:
                    self.set_wide(False, "bar")
                    self._glide_to(self.bar_home[0], self.bar_home[1], done=self._save_pos)
                else:
                    self.set_wide(False, "bar")
                    self.glide_n += 1
                    self.move(self.bar_home[0] - self.wide_shift, self.bar_home[1])
                    self._save_pos()
            else:
                self.set_wide(False, "bar")
        # (pulled out by the mouse: the page just pops him out — the notch's melt happens on the wall, in a Ghost)
        self._js("window.voidDock&&voidDock(%s,%s)" % (json.dumps(edge or ""), json.dumps("pull" if to and not edge else "")))

    def _glide_to(self, bx, by, done=None):
        """Glide his own 340px strip to (bx, by) over GLIDE seconds, easing out; a newer glide (or move) cancels this.
        (The window keeps its size: he never moves inside it, see set_wide.)"""
        self.glide_n += 1
        n, t0 = self.glide_n, time.monotonic()
        sbx, sby = self.x() + self.wide_shift, self.y()

        def step():
            if n != self.glide_n:
                return
            k = min(1.0, (time.monotonic() - t0) / GLIDE)
            e = 1 - (1 - k) ** 3
            self.move(int(round(sbx + (bx - sbx) * e)) - self.wide_shift, int(round(sby + (by - sby) * e)))
            if k < 1:
                QTimer.singleShot(8, step)
            elif done:
                done()
        step()

    def _drag_done(self):
        """After you drop the blob: pushed into a screen edge? He merges into it as the bar. Judged by the mouse, not
        his ball: Windows won't let a window go above the top of the screen, so the ball can never reach the top."""
        c = QCursor.pos()
        scr = QGuiApplication.screenAt(c) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        dist = {"top": c.y() - g.top(), "bottom": g.bottom() - c.y(), "left": c.x() - g.left(), "right": g.right() - c.x()}
        edge = min(dist, key=dist.get)
        fx, fy = getattr(self, "drag_from", (self.x(), self.y()))
        if abs(self.x() - fx) + abs(self.y() - fy) < 12:                 # he didn't actually move: don't jump anywhere
            return
        if dist[edge] < 40:                                              # dropped right at the edge
            self.set_bar(edge)

    def _watch_drag(self):
        """Windows moves the window itself while you drag (startSystemMove); watch for the mouse button coming up.
        Exactly one watcher, and it stops itself: a stray one kept docking him wherever the mouse went near an edge."""
        old = getattr(self, "_drag_t", None)
        if old:
            old.stop()
            old.deleteLater()
        t = QTimer(self, interval=50)
        t0 = time.monotonic()

        def check():
            down = winsys.mouse_down() if MAC else ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000
            if not down or time.monotonic() - t0 > 60:
                t.stop()
                if self._drag_t is t:
                    self._drag_t = None
                    self._drag_done()
                t.deleteLater()
        t.timeout.connect(check)
        self._drag_t = t
        t.start()

    def _drag(self):
        self.glide_n += 1                                                # grabbed: any glide stops right here
        if self.bar:                                                     # pulling the bar away: out pops the blob,
            c = QCursor.pos()                                            # under your mouse, still being dragged
            e = self.bar                                                 # the notch stays on the wall and melts away
            nx, ny, nw, nh = self._ghost_rect()
            self.set_wide(False, "bar")                                  # (first: it may resize him)
            self.set_bar(None, to=(c.x() - BALL[0] - self.wide_shift, c.y() - BALL[1]))
            # (a moment later: a new window appearing right as the drag starts can upset the drag)
            QTimer.singleShot(30, lambda: setattr(self, "ghost", Ghost(nx, ny, nw, nh, e)))
        if not self.hover and self.windowHandle():
            self._set_through(False)
            self.drag_from = (self.x(), self.y())
            self.windowHandle().startSystemMove()
            self._watch_drag()

    def _pmove(self):
        """Dragging a panel's header: move the window as it is (no undocking, no docking on drop; in the bar the
        page sends bar:<edge> when the panel closes, which puts the notch back)."""
        self.glide_n += 1
        if not self.hover and self.windowHandle():
            self._set_through(False)
            self.windowHandle().startSystemMove()

    def _grab_keys(self):
        self.activateWindow()
        self.raise_()
        self.view.setFocus()

    # ---- click-through: only what the page says is solid takes the mouse
    def _set_through(self, on):
        if on == self.through:
            return
        self.through = on
        if MAC:
            winsys.set_click_through(self.winId(), on)
            return
        hwnd = int(self.winId())
        u = ctypes.windll.user32
        ex = u.GetWindowLongW(hwnd, -20)                                  # GWL_EXSTYLE
        ex = (ex | 0x20 | 0x80000) if on else (ex & ~0x20)                # WS_EX_TRANSPARENT (+ WS_EX_LAYERED)
        u.SetWindowLongW(hwnd, -20, ex)

    def _poll_mouse(self):
        if self.hover:
            self._follow()
            return
        if not self.isVisible():
            self._set_through(False)
            return
        p = QCursor.pos() - self.pos()
        if self.rects is None:                                           # all of him takes clicks, not the unused room
            self._set_through(p.x() < self.wide_shift - self.box_l)
            return
        solid = any(x <= p.x() < x + w and y <= p.y() < y + h for x, y, w, h in self.rects)
        self._set_through(not solid)

    # ---- show / hide / commands
    def show_me(self):
        self.shown = True
        self.show()
        self.raise_()
        self._js("window.voidShown&&voidShown()")

    def hide_me(self):
        self.shown = False
        self._js("window.voidHidden&&voidHidden()")
        self.hide()

    def _remote_cmd(self, c):
        if c == "quit":
            quit_app(self)
        elif c == "toggle":
            if self.hover:
                self._js("window.voidCmd&&voidCmd('home')")
            elif self.isVisible():
                self.hide_me()
            else:
                self.show_me()
        elif c == "show":
            self.show_me()
        elif c == "hide":
            self.hide_me()
        elif c in ("ptt-down", "ptt-up"):
            log(f"push-to-talk: {c[4:]}")
            if c == "ptt-down" and not self.isVisible():
                self.show_me()
            self._js("window.voidCmd&&voidCmd(%s)" % json.dumps(c))
        elif c in ("listen", "look", "live", "hover", "hover-on", "hover-off"):
            if not self.isVisible():
                self.show_me()
            self._js("window.voidCmd&&voidCmd(%s)" % json.dumps(c))

    def _authed_url(self):
        """The page's address with the server's token after '#': read from runtime.json (only you can read it) and
        never on a command line or in the page the server hands out; the part after '#' is never sent anywhere."""
        for _ in range(50):                                              # (a just-started server writes it in a moment)
            try:
                return self.url + "#t=" + json.loads((DATA / "runtime.json").read_text())["token"]
            except (OSError, ValueError, KeyError):
                time.sleep(0.1)
        log("couldn't read the server's token (runtime.json)")
        return self.url

    def _server_up(self):
        import urllib.error
        import urllib.request
        try:
            urllib.request.urlopen(self.base + "api/state", timeout=2)
            return True
        except urllib.error.HTTPError:                                    # it answered: it's running
            return True
        except Exception:  # noqa: BLE001
            return False

    def _start_server(self):
        """Start the server in its own process group, with a run id of ours, and record it (see Started)."""
        import secrets
        import subprocess
        rid = secrets.token_hex(8)
        with open(DATA / "server.log", "ab") as out:
            p = subprocess.Popen([sys.executable, str(APP / "server.py")], stdout=out, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True,
                                 env={**os.environ, "VOID_RUN_ID": rid})
        Started.record(p.pid, rid)

    def _about_to_quit(self):
        self.quitting = True
        Started.stop()

    def _watch_server(self):
        """If the server stops (a crash), start it again instead of leaving him deaf with "failed to fetch". The page
        reconnects by itself: a new server means a new token, and it reloads when it sees "forbidden"."""
        misses, last = 0, 0.0
        while not self.quitting:
            time.sleep(3)
            if self.quitting:
                return
            if self._server_up():
                misses = 0
                continue
            misses += 1
            if misses >= 2 and time.monotonic() - last > 20:
                last = time.monotonic()
                log("the server isn't answering — starting it again")
                try:
                    if MAC:
                        self._start_server()
                    else:
                        import launch_win
                        launch_win._start("server", [str(APP / "server.py")])
                except Exception as e:  # noqa: BLE001
                    log(f"couldn't restart the server: {e}")

    def _ptt_hook(self):
        """The push-to-talk hotkey, anywhere in Windows: a low-level keyboard hook (RegisterHotKey can't tell when a
        key is let go). It only compares each key with the combo; everything else passes straight through untouched,
        and nothing is recorded. The combo's own key presses are kept from the app you're in."""
        if MAC:                                                          # (needs Input Monitoring: not yet)
            log("push-to-talk hotkey: not on macOS yet — hold Space in his window, or bind `void listen` to a key")
            return
        import ctypes.wintypes as wt
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
        user32.CallNextHookEx.argtypes = (wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM)
        user32.CallNextHookEx.restype = LRESULT
        user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD)
        user32.SetWindowsHookExW.restype = wt.HHOOK
        user32.GetAsyncKeyState.restype = ctypes.c_short
        kernel32.GetModuleHandleW.restype = wt.HMODULE

        class KBD(ctypes.Structure):
            _fields_ = [("vk", wt.DWORD), ("scan", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                        ("extra", ctypes.c_size_t)]

        def held(vks):
            return any(user32.GetAsyncKeyState(v) & 0x8000 for v in vks)

        def proc(code, wparam, lparam):
            combo = self.ptt
            if code == 0 and combo:
                mods, key = combo
                vk = ctypes.cast(lparam, ctypes.POINTER(KBD)).contents.vk
                if vk == key:
                    down = wparam in (0x100, 0x104)                     # WM_KEYDOWN / WM_SYSKEYDOWN
                    if down and not self.ptt_held and all(held(MOD_VKS[m]) for m in mods):
                        self.ptt_held = True
                        self.remote.cmd.emit("ptt-down")
                        return 1
                    if self.ptt_held:
                        if not down:
                            self.ptt_held = False
                            self.remote.cmd.emit("ptt-up")
                        return 1                                         # (and swallow the key's auto-repeat)
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._ptt_proc = HOOKPROC(proc)                                  # keep it alive: Windows calls it
        if not user32.SetWindowsHookExW(13, self._ptt_proc, kernel32.GetModuleHandleW(None), 0):   # WH_KEYBOARD_LL
            log("push-to-talk hotkey unavailable (couldn't watch the keyboard)")
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:   # low-level hooks need a message loop
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _serve(self):
        """Commands from `void listen` & co. A local port any program (any user) could reach, so each command must
        start with this run's secret, which sits next to the port number in a file only you can read."""
        import hmac
        import secrets
        secret = secrets.token_urlsafe(18)
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        DATA.mkdir(parents=True, exist_ok=True)
        if not SELFTEST:                     # (a test window must not take over the real one's address: `void stop`)
            PORT_FILE.write_text(f"{srv.getsockname()[1]} {secret}")
        while True:
            c, _ = srv.accept()
            try:
                got, _, cmd = c.recv(128).decode(errors="replace").strip().partition(" ")
            finally:
                c.close()
            if cmd and hmac.compare_digest(got, secret):
                self.remote.cmd.emit(cmd.strip())

    # ---- widening for panels / his terminal
    def set_wide(self, on, kind="panel"):
        """Make room: panels on his left, his terminal either side. The room on his left is always there and the page is
        told how much of it to use (voidRoom): moving the window and him in it at once, the page a frame behind showed
        him 100 px off for a frame. Only the right side grows and shrinks (the page is drawn from the left)."""
        if kind == "panel":
            self.want_panel = on
        elif kind == "bar":
            self.want_bar = on
        else:
            self.want_code = on
        L = max(WIDE - W if self.want_panel else 0, CODE_SIDE if self.want_code else 0, BAR_SIDE if self.want_bar else 0)
        R = max(CODE_SIDE if self.want_code else 0, BAR_SIDE if self.want_bar else 0)
        if self.hover:
            L = R = 0
        base = self.x() + self.wide_shift                                 # where his own 340px strip starts
        g = self._screen_rect()
        L, R = min(L, max(0, base - g.left())), min(R, max(0, g.right() - base - W))
        if L != self.box_l:
            self.box_l = L
            self._js(f"window.voidRoom&&voidRoom({L})")
        if self.wide_shift + W + R != self.cur_w:
            self.cur_w = self.wide_shift + W + R
            self.resize(self.cur_w, H)

    # ---- hover mode: he is the mouse pointer
    def set_hover(self, on):
        if self.hover == on:
            return
        if on and self.bar:                                              # out of the notch first (it mustn't fly around)
            self.set_bar(None, glide=False)                              # (at once: the cursor-following starts here)
        if on and (self.box_l or self.cur_w > self.wide_shift + W):
            self.want_code = False
            self.set_wide(False)
        self.hover = on
        self.poll.setInterval(8 if on else 33)                            # as a cursor he has to keep up with the mouse
        if on:
            self.home, self.hpos, self.vel, self.last = (self.x(), self.y()), None, [0.0, 0.0], None
            self._set_through(True)
        else:
            self._glide_home()
        self._js(f"window.voidHover&&voidHover({'true' if on else 'false'})")

    def _follow(self):
        c = QCursor.pos()
        now = time.monotonic()
        if self.last:
            dt = max(1e-3, now - self.last[2])
            self.vel = [self.vel[0] * 0.8 + (c.x() - self.last[0]) / dt * 0.2,
                        self.vel[1] * 0.8 + (c.y() - self.last[1]) / dt * 0.2]
        self.last = (c.x(), c.y(), now)
        self.hpos = [c.x() - BALL[0] - self.wide_shift, c.y() - BALL[1]]  # right on the pointer: he *is* the cursor
        if (self.x(), self.y()) != (self.hpos[0], self.hpos[1]):
            self.move(self.hpos[0], self.hpos[1])
        if now - self.look_t >= 0.033:                                    # his eyes only need ~30 updates a second
            self.look_t = now
            self._js("window.voidLook&&voidLook(%.3f,%.3f)" % (max(-1, min(1, self.vel[0] / 900)),
                                                               max(-1, min(1, self.vel[1] / 900))))

    def _glide_home(self):
        if not self.home:
            return
        (hx, hy), (sx, sy), t0 = self.home, (self.x(), self.y()), time.monotonic()

        def step():
            k = min(1.0, (time.monotonic() - t0) / 0.65)
            e = 1 - (1 - k) ** 3
            self.move(int(sx + (hx - sx) * e), int(sy + (hy - sy) * e + math.sin(math.pi * k) * -40))
            if k < 1:
                QTimer.singleShot(8, step)
            else:
                self._save_pos()
        step()


def quit_app(win):
    """Quit the way the tray's Quit does: stop the watchdog, then our own server (if it is ours), then Qt."""
    win.quitting = True
    Started.stop()
    QApplication.quit()


def install_sigterm_handler(win):
    """SIGTERM (a crash handler, launchctl stop) quits the same way, since the server is in a group of its own and
    would otherwise outlive him. Qt needs the interpreter woken now and then to run the handler: see main()."""
    import signal
    signal.signal(signal.SIGTERM, lambda signum, frame: quit_app(win))


def name_qt_app(app):
    """Qt names itself after the script (window_win.py) and, on macOS, writes its own cache (the Qt Quick pipeline cache)
    to ~/Library/Caches/<that name> whatever HOME says. Call him Void and send that cache to a folder of his own under
    cache_dir(). The page keeps QtWebEngine's default off-the-record profile (nothing of it is stored)."""
    app.setApplicationName("Void")
    folder = winsys.cache_dir() / "Void"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    for var in ("QSG_RHI_PIPELINE_CACHE_SAVE", "QSG_RHI_PIPELINE_CACHE_LOAD"):
        os.environ.setdefault(var, str(folder / "qtpipelinecache"))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    url = sys.argv[1]
    global SELFTEST
    SELFTEST = len(sys.argv) > 2 and sys.argv[2].startswith("--selftest")
    if len(sys.argv) > 3 and sys.argv[2] == "--selftest":
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] += " --disable-gpu"
        app = QApplication(sys.argv[:1])
        name_qt_app(app)
        win = VoidWindow(url)
        out = sys.argv[3]

        def snap():
            win.grab().save(out)
            log(f"selftest: saved {out} ({win.width()}x{win.height()})")
            app.quit()
        QTimer.singleShot(9000, snap)
        sys.exit(app.exec())
    if len(sys.argv) > 4 and sys.argv[2] == "--selftest-bar":            # dock on an edge, report where, save a picture
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] += " --disable-gpu"
        app = QApplication(sys.argv[:1])
        name_qt_app(app)
        win = VoidWindow(url)
        edge, out = sys.argv[3], sys.argv[4]

        def report(cls):
            g = win._screen_rect()
            win.grab().save(out)                                         # (x, w: his own, not the room on his left)
            print(f"selftest-bar: {edge} x={win.x() + win.wide_shift} y={win.y()} w={win.width() - win.wide_shift} h={win.height()} "
                  f"area={g.x()},{g.y()},{g.width()},{g.height()} page={cls}", flush=True)
            app.quit()
        QTimer.singleShot(10000, lambda: win.set_bar(edge))              # (slow test machines: let the page load)
        QTimer.singleShot(13000,lambda: win.view.page().runJavaScript("document.body.className", report))
        sys.exit(app.exec())
    cmd =sys.argv[2] if len(sys.argv) > 2 else "toggle"
    if send(cmd):
        return
    if cmd in ("hide", "quit"):
        return
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    name_qt_app(app)
    app.setQuitOnLastWindowClosed(False)
    if MAC:
        winsys.no_dock_icon()
    win = VoidWindow(url)
    if cmd in ("listen", "look", "live", "hover", "hover-on"):
        QTimer.singleShot(1800, lambda: win._remote_cmd(cmd))         # first launch: let the page load, then do it
    if sys.platform != "win32":
        install_sigterm_handler(win)
        wake = QTimer(app, interval=300, timeout=lambda: None)          # (lets Python see the signal while Qt runs)
        wake.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
