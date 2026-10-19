"""Tests for his window on macOS: the native window can't be resized by the user or by macOS (Sequoia's tiling), and a
resize he didn't ask for is put straight back. Qt's offscreen platform and a fake Cocoa: no window on the desktop.

    python3 -m unittest discover -s tests
"""
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
        self.no_resize, self.spaces = 0, 0

    def keep_on_all_spaces(self, win_id):
        self.spaces += 1

    def no_resize_(self, win_id):
        self.no_resize += 1


@unittest.skipUnless(MAC and window_win, "macOS with PySide6 only")
class Unresize(unittest.TestCase):
    """VoidWindow's own geometry comes back after a resize he didn't ask for; his own resizes are left alone."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.cocoa = FakeCocoa()
        fake = type("Cocoa", (), {"keep_on_all_spaces": self.cocoa.keep_on_all_spaces,
                                  "no_resize": self.cocoa.no_resize_})
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
        self.cocoa.no_resize = 0

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


if __name__ == "__main__":
    unittest.main()
