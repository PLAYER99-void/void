"""His window on macOS with a Qt platform that isn't cocoa (QT_QPA_PLATFORM=offscreen, minimal, vnc): winId() is then a
small counter, not an NSView pointer, so a Cocoa call on it dereferences a bogus pointer and Python segfaults. Cocoa is
only touched when QGuiApplication.platformName() == "cocoa". macOS forced on, winsys a recorder: nothing here can crash.

    python3 -m unittest discover -s tests
"""
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

try:
    from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402
    import window_win  # noqa: E402
except ImportError:
    window_win = None


class FakeWinsys:
    """Every attribute is a call that's recorded and returns nothing."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **kw: self.calls.append(name)


@unittest.skipUnless(window_win, "PySide6 only")
class NoCocoaOffCocoa(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fake = FakeWinsys()
        self.saved = window_win.winsys, window_win.MAC
        window_win.winsys, window_win.MAC = self.fake, True              # (the window decides on MAC at each call)

        class Win(window_win.VoidWindow):
            def __init__(self):                                          # just the window: no page, server or threads
                Qt = window_win.Qt
                QWidget.__init__(self, None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                                 | Qt.NoDropShadowWindowHint)
                self.cur_w, self.wide_shift, self.hover, self.bar = window_win.W, 0, False, False
                self.own_pos, self.unresize_due, self.through = None, False, False
                self.save_t = window_win.QTimer(self, singleShot=True, interval=700)
                self.setGeometry(500, 300, window_win.W, window_win.H)

            def _js(self, code):
                pass
        self.win = Win()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.settle()
        window_win.winsys, window_win.MAC = self.saved

    def settle(self):
        for _ in range(5):
            self.app.processEvents()

    def test_not_cocoa(self):
        self.assertEqual(window_win.QGuiApplication.platformName(), "offscreen")

    def test_show_hide_makes_no_cocoa_call(self):
        self.win.show()
        self.settle()
        self.win.hide()
        self.win.show()
        self.settle()
        self.assertEqual(self.fake.calls, [])

    def test_click_through_makes_no_cocoa_call(self):
        self.win.show()
        self.win._set_through(True)
        self.win._set_through(False)
        self.settle()
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
