"""Tests for his window on macOS: the native window can't be resized by the user or by macOS (Sequoia's tiling), and a
resize he didn't ask for is put straight back. Qt's offscreen platform and a fake Cocoa: no window on the desktop.

    python3 -m unittest discover -s tests
"""
import ctypes
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))

MAC = sys.platform == "darwin"
if MAC:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import macsys  # noqa: E402
    try:
        from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402
        import window_win  # noqa: E402
    except ImportError:
        window_win = None


@unittest.skipUnless(MAC, "macOS only")
class NoResize(unittest.TestCase):
    """macsys.no_resize clears NSWindowStyleMaskResizable and nothing else."""

    def setUp(self):
        self.calls, self.mask = [], 0xe                                  # borderless | closable | mini | resizable
        self.saved = macsys._nswindow, macsys._send
        macsys._nswindow = lambda win_id: 0x1234 if win_id else None

        def send(obj, sel, *args, **kw):
            self.calls.append((sel, *args))
            if sel == "styleMask":
                return self.mask
            if sel == "setStyleMask:":
                self.mask = args[0]
        macsys._send = send

    def tearDown(self):
        macsys._nswindow, macsys._send = self.saved

    def test_clears_resizable_only(self):
        macsys.no_resize(1)
        self.assertEqual(self.mask, 0x6)

    def test_already_clear_is_left_alone(self):
        self.mask = 0x6
        macsys.no_resize(1)
        self.assertNotIn("setStyleMask:", [c[0] for c in self.calls])

    def test_no_native_window(self):
        macsys.no_resize(0)
        self.assertEqual(self.calls, [])


class FakeCocoa:
    def __init__(self):
        self.no_resize, self.spaces, self.free = 0, 0, 0

    def keep_on_all_spaces(self, win_id):
        self.spaces += 1

    def no_resize_(self, win_id):
        self.no_resize += 1

    def unconstrained(self, win_id):
        self.free += 1


@unittest.skipUnless(MAC and window_win, "macOS with PySide6 only")
class Unresize(unittest.TestCase):
    """VoidWindow's own geometry comes back after a resize he didn't ask for; his own resizes are left alone."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.cocoa = FakeCocoa()
        fake = type("Cocoa", (), {"keep_on_all_spaces": self.cocoa.keep_on_all_spaces,
                                  "no_resize": self.cocoa.no_resize_, "unconstrained": self.cocoa.unconstrained})
        self.saved = window_win.winsys
        window_win.winsys = fake
        js = self.js = []

        class Win(window_win.VoidWindow):
            def __init__(self):                                          # just the window: no page, server or threads
                Qt = window_win.Qt
                QWidget.__init__(self, None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                                 | Qt.NoDropShadowWindowHint)
                self.cur_w, self.wide_shift, self.hover, self.bar = window_win.W, 0, False, False
                self.own_pos, self.unresize_due = None, False
                self.save_t = window_win.QTimer(self, singleShot=True, interval=700)
                self.setGeometry(500, 300, window_win.W, window_win.H)

            def _js(self, code):
                js.append(code)
        self.win = Win()
        self.win.show()
        self.settle()
        self.cocoa.no_resize = self.cocoa.free = 0

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.settle()
        window_win.winsys = self.saved

    def settle(self):
        for _ in range(5):
            self.app.processEvents()

    def test_tiled_comes_back(self):
        self.win.setGeometry(0, 30, 1920, 2038)                          # what Sequoia's "fill the left half" does
        self.settle()
        g = self.win.geometry()
        self.assertEqual((g.x(), g.y(), g.width(), g.height()), (500, 300, window_win.W, window_win.H))
        self.assertGreaterEqual(self.cocoa.no_resize, 1)                 # the style mask is cleared again
        self.assertIn("window.voidLayout&&voidLayout(0)", self.js)       # the page refits at full size

    def test_own_widening_stays(self):
        w = self.win
        w.wide_shift, w.cur_w = 130, window_win.W + 130                  # as set_wide does: cur_w first, then the window
        w.setGeometry(370, 300, w.cur_w, window_win.H)
        self.settle()
        self.assertEqual((w.x(), w.width()), (370, window_win.W + 130))
        self.assertEqual(self.js, [])

    def test_moves_keep_his_spot(self):
        self.win.move(800, 400)
        self.settle()
        self.win.setGeometry(1920, 30, 1920, 1019)                       # tiled into the top-right quarter
        self.settle()
        self.assertEqual((self.win.x(), self.win.y(), self.win.width()), (800, 400, window_win.W))

    def test_show_reapplies(self):
        self.win.hide()
        self.win.show()
        self.settle()
        self.assertGreaterEqual(self.cocoa.no_resize, 1)
        self.assertGreaterEqual(self.cocoa.free, 1)                      # (and AppKit mayn't push him off the top)


@unittest.skipUnless(MAC, "macOS only")
class EdgeInsets(unittest.TestCase):
    """macsys.edge_insets: AppKit's screens (Cocoa's points, y up) → how far in the notch hangs on each edge."""
    MAIN = (0, 0, 3840, 2160)

    def test_menu_bar_and_dock_there(self):
        self.assertEqual(macsys.edge_insets([(self.MAIN, (0, 92, 3840, 2038), 0.0)], 0, 0, 3840, 2160),
                         {"top": 30, "bottom": 92, "left": 0, "right": 0})

    def test_full_screen_app_flush(self):
        self.assertEqual(macsys.edge_insets([(self.MAIN, self.MAIN, 0.0)], 0, 0, 3840, 2160),
                         {"top": 0, "bottom": 0, "left": 0, "right": 0})

    def test_dock_hiding_itself_is_no_dock(self):
        self.assertEqual(macsys.edge_insets([(self.MAIN, (0, 4, 3840, 2126), 0.0)], 0, 0, 3840, 2160)["bottom"], 0)

    def test_dock_on_the_left_or_right(self):
        left = macsys.edge_insets([(self.MAIN, (80, 0, 3760, 2130), 0.0)], 0, 0, 3840, 2160)
        right = macsys.edge_insets([(self.MAIN, (0, 0, 3760, 2130), 0.0)], 0, 0, 3840, 2160)
        self.assertEqual((left["left"], left["right"], left["bottom"]), (80, 0, 0))
        self.assertEqual((right["left"], right["right"]), (0, 80))

    def test_never_behind_the_camera(self):
        built_in = (0, 0, 1512, 982)
        full = macsys.edge_insets([(built_in, built_in, 32.0)], 0, 0, 1512, 982)
        menu = macsys.edge_insets([(built_in, (0, 0, 1512, 945), 32.0)], 0, 0, 1512, 982)
        self.assertEqual((full["top"], menu["top"]), (32, 37))

    def test_second_screen_found_by_its_qt_frame(self):
        # a 1920x1080 screen above the main one, left of centre: Cocoa y 2160…3240 is Qt's y -1080…0
        upper = ((-500, 2160, 1920, 1080), (-500, 2160, 1920, 1055), 0.0)
        scr = [(self.MAIN, (0, 92, 3840, 2038), 0.0), upper]
        self.assertEqual(macsys.edge_insets(scr, -500, -1080, 1920, 1080)["top"], 25)
        self.assertIsNone(macsys.edge_insets(scr, 5000, 0, 1920, 1080))
        self.assertIsNone(macsys.edge_insets([], 0, 0, 3840, 2160))

    # what an Odyssey G8 (3840x2160 at 1x) really read: visibleFrame keeps the menu bar's and the Dock's room in a
    # full-screen app's Space too, where neither one's window is on screen; on a desktop both are
    DESKTOP = [("menu", (0, 0, 3840, 30)), ("dock", (0, 0, 3840, 2160))]

    def test_full_screen_space_flush_though_visible_frame_keeps_room(self):
        self.assertEqual(macsys.edge_insets([(self.MAIN, (0, 92, 3840, 2038), 0.0)], 0, 0, 3840, 2160, []),
                         {"top": 0, "bottom": 0, "left": 0, "right": 0})

    def test_desktop_space_under_the_menu_bar_and_beside_the_dock(self):
        self.assertEqual(macsys.edge_insets([(self.MAIN, (0, 92, 3840, 2038), 0.0)], 0, 0, 3840, 2160, self.DESKTOP),
                         {"top": 30, "bottom": 92, "left": 0, "right": 0})

    def test_menu_bar_auto_hidden_dock_still_there(self):
        self.assertEqual(macsys.edge_insets([(self.MAIN, (0, 92, 3840, 2038), 0.0)], 0, 0, 3840, 2160,
                                            self.DESKTOP[1:]), {"top": 0, "bottom": 92, "left": 0, "right": 0})

    def test_bars_on_another_screen_dont_count(self):
        upper = ((-500, 2160, 1920, 1080), (-500, 2160, 1920, 1055), 0.0)
        scr = [(self.MAIN, (0, 92, 3840, 2038), 0.0), upper]
        self.assertEqual(macsys.edge_insets(scr, -500, -1080, 1920, 1080, self.DESKTOP)["top"], 0)
        here = self.DESKTOP + [("menu", (-500, -1080, 1920, 25))]
        self.assertEqual(macsys.edge_insets(scr, -500, -1080, 1920, 1080, here)["top"], 25)
        self.assertEqual(macsys.edge_insets(scr, 0, 0, 3840, 2160, [("menu", (-500, -1080, 1920, 25))])["top"], 0)

    def test_full_screen_space_still_never_behind_the_camera(self):
        built_in = (0, 0, 1512, 982)
        self.assertEqual(macsys.edge_insets([(built_in, (0, 0, 1512, 945), 32.0)], 0, 0, 1512, 982, [])["top"], 32)

    def test_bars_read_from_the_window_list(self):
        bars = macsys._bars()                                            # (whatever this Mac shows: the shape of it)
        self.assertIsInstance(bars, list)
        for kind, r in bars:
            self.assertIn(kind, ("menu", "dock"))
            self.assertEqual(len(r), 4)


@unittest.skipUnless(MAC, "macOS only")
class Unconstrained(unittest.TestCase):
    """macsys.unconstrained: AppKit hands a window's frame back as asked (flush with the top, or off the screen), not
    pushed down under where the menu bar would be. On a panel class of its own that's never shown."""

    @classmethod
    def setUpClass(cls):
        o, send = macsys._objc, macsys._send
        o.objc_allocateClassPair.restype = ctypes.c_void_p
        o.objc_allocateClassPair.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t)
        o.objc_registerClassPair.argtypes = (ctypes.c_void_p,)
        send(o.objc_getClass(b"NSApplication"), "sharedApplication")
        cls.made = []
        for name, mask in ((b"VoidTestPanel", 0x6), (b"VoidTestTitled", 0x7)):   # (no_resize's mask; and titled)
            k = o.objc_getClass(name)
            if not k:
                k = o.objc_allocateClassPair(o.objc_getClass(b"NSPanel"), name, 0)
                o.objc_registerClassPair(k)
            init = "initWithContentRect:styleMask:backing:defer:"
            cls.made.append(send(send(k, "alloc"), init, macsys._Rect(100, 100, 340, 460), mask, 2, True,
                                 argtypes=(macsys._Rect, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_bool)))
        cls.panel, cls.titled = cls.made
        cls.scr = send(o.objc_getClass(b"NSScreen"), "mainScreen")
        cls.saved = macsys._nswindow
        macsys._nswindow = lambda win_id: ctypes.c_void_p(win_id) if win_id else None

    @classmethod
    def tearDownClass(cls):
        macsys._nswindow = cls.saved
        for w in cls.made:
            macsys._send(w, "close", restype=None)

    def constrain(self, w, r):
        r = macsys._send(w, "constrainFrameRect:toScreen:", macsys._Rect(*r), self.scr, restype=macsys._Rect,
                         argtypes=(macsys._Rect, ctypes.c_void_p))
        return r.x, r.y, r.w, r.h

    def test_flush_with_the_top_and_off_screen_stay_put(self):
        if not self.scr:
            self.skipTest("no screen")
        f = macsys._send(self.scr, "frame", restype=macsys._Rect)
        top = (f.x + 100, f.y + f.h - 460, 340, 460)
        macsys.unconstrained(self.panel)
        self.assertEqual(self.constrain(self.panel, top), top)
        self.assertEqual(self.constrain(self.panel, (f.x - 200, f.y + f.h - 100, 340, 460)),
                         (f.x - 200, f.y + f.h - 100, 340, 460))

    def test_titled_left_alone(self):
        macsys.unconstrained(self.titled)
        sel = macsys._objc.sel_registerName(b"constrainFrameRect:toScreen:")
        o = macsys._objc
        o.class_getInstanceMethod.restype = ctypes.c_void_p
        o.class_getInstanceMethod.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        own = o.class_getInstanceMethod(o.objc_getClass(b"VoidTestTitled"), sel)
        self.assertEqual(own, o.class_getInstanceMethod(o.objc_getClass(b"NSPanel"), sel))   # (only inherited)

    def test_no_native_window(self):
        macsys.unconstrained(0)                                          # (nothing to do, and no crash)


@unittest.skipUnless(MAC and window_win, "macOS with PySide6 only")
class BarInset(unittest.TestCase):
    """Docked, his window sits flush on the screen's real edge and the page is told how far in the notch hangs: again
    whenever the menu bar or the Dock change, oozing there, without the window moving."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        test = self
        self.insets = {"top": 30, "bottom": 92, "left": 0, "right": 0}
        self.watching = []
        fake = type("Cocoa", (), {"keep_on_all_spaces": staticmethod(lambda w: None), "no_resize": staticmethod(lambda w: None),
                                  "unconstrained": staticmethod(lambda w: None),
                                  "edge_insets_now": staticmethod(lambda x, y, w, h: dict(test.insets)),
                                  "watch_screens": staticmethod(lambda cb: test.watching.append(cb) or True)})
        self.saved = window_win.winsys
        window_win.winsys = fake
        js = self.js = []

        class Win(window_win.VoidWindow):
            def __init__(self):                                          # just the window: no page, server or threads
                Qt = window_win.Qt
                QWidget.__init__(self, None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                                 | Qt.NoDropShadowWindowHint)
                self.cur_w, self.wide_shift, self.hover, self.bar, self.bar_home = window_win.W, 0, False, False, None
                self.own_pos, self.unresize_due, self.want_panel, self.want_code, self.want_bar = None, False, False, False, False
                self.glide_n, self.bar_inset = 0, 0
                self.save_t = window_win.QTimer(self, singleShot=True, interval=700)
                self.setGeometry(300, 200, window_win.W, window_win.H)
                self._watch_screens()

            def _js(self, code):
                js.append(code)
        self.win = Win()
        self.win.show()
        self.settle()
        self.scr = window_win.QGuiApplication.primaryScreen().geometry()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.settle()
        window_win.winsys = self.saved

    def settle(self, s=0.0):
        import time
        end = time.monotonic() + s
        while True:
            self.app.processEvents()
            if time.monotonic() >= end:
                return
            time.sleep(0.01)

    def dock(self, edge):
        self.win.set_bar(edge)
        self.settle(window_win.GLIDE + 0.15)

    def test_top_flush_with_the_screen_and_the_page_hangs_it_under_the_menu_bar(self):
        self.dock("top")
        self.assertEqual(self.win.y(), self.scr.top())                   # not availableGeometry's menu-bar-high gap
        i = [n for n, c in enumerate(self.js) if "voidBarInset" in c or "voidDock" in c]
        self.assertEqual([self.js[n] for n in i], ['window.voidBarInset&&voidBarInset(30,false)',
                                                   'window.voidDock&&voidDock("top","")'])   # (told before it docks)

    def test_menu_bar_goes_and_comes_back_while_docked(self):
        self.dock("top")
        y, n = self.win.y(), len(self.js)
        self.insets["top"] = 0                                           # a full-screen app's Space
        self.watching[0]()
        self.settle(0.05)
        self.assertEqual(self.js[n:], ["window.voidBarInset&&voidBarInset(0,true)"])
        self.assertEqual(self.win.y(), y)                                # the window stays: the page oozes
        self.insets["top"] = 30
        self.watching[0]()
        self.settle(0.8)                                                 # (and the look once the Space has settled)
        self.assertEqual(self.js[n + 1:], ["window.voidBarInset&&voidBarInset(30,true)"])

    def test_bottom_beside_the_dock_or_flush_when_it_hides(self):
        self.dock("bottom")
        self.assertEqual(self.win.y(), self.scr.bottom() - window_win.H + 1)
        self.assertIn("window.voidBarInset&&voidBarInset(92,false)", self.js)
        self.insets["bottom"] = 0                                        # Dock set to hide itself
        self.win._screens_changed()
        self.assertEqual(self.js[-1], "window.voidBarInset&&voidBarInset(0,true)")

    def test_dock_on_the_right(self):
        self.insets.update(bottom=0, right=80)
        self.dock("right")
        self.assertEqual(self.win.x(), self.scr.right() - window_win.W + 1)
        self.assertIn("window.voidBarInset&&voidBarInset(80,false)", self.js)

    def test_not_docked_nothing_to_say(self):
        n = len(self.js)
        self.watching[0]()
        self.settle(0.05)
        self.assertEqual(self.js[n:], [])

    def test_the_notch_left_on_the_wall_is_where_it_hung(self):
        self.dock("top")
        x, y, w, h = self.win._ghost_rect()
        self.assertEqual((y, h), (self.win.y() + 30, 52))
        self.insets["bottom"] = 92
        self.dock("bottom")
        self.assertEqual(self.win._ghost_rect()[1], self.win.y() + window_win.H - 52 - 92)


if __name__ == "__main__":
    unittest.main()
