"""His Qt window's mouse poll (click-through ~30×/s, hover ~120×/s) only runs while he's on screen: hiding him stops
it, showing him starts it again at the rate it had, and brings him up to date at once — in hover mode he's on the
pointer the moment he appears (no stale spot, then a jump), and which parts take clicks is right straight away.
Qt's offscreen platform and just the window (no page, server or threads): nothing on the desktop.

    python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402
    import window_win  # noqa: E402
except (ImportError, OSError):
    window_win = None


def setUpModule():
    """His saved spot goes to a folder of the tests' own, not the real one (hover-off glides home and saves)."""
    global _data
    if window_win:
        _data = tempfile.TemporaryDirectory()
        window_win.DATA, window_win.POS_FILE = Path(_data.name), Path(_data.name) / "pos.json"


def tearDownModule():
    if window_win:
        _data.cleanup()


@unittest.skipUnless(window_win, "PySide6 (and window_win) only")
class Poll(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        calls = self.calls = []                                          # ("poll" | "follow", visible at the time)
        throughs = self.throughs = []

        class Win(window_win.VoidWindow):
            def __init__(self):                                          # just the window and its poll, as __init__ makes it
                Qt, QTimer = window_win.Qt, window_win.QTimer
                QWidget.__init__(self, None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                                 | Qt.NoDropShadowWindowHint)
                self.cur_w, self.wide_shift, self.box_l = window_win.W + window_win.ROOM, window_win.ROOM, 0
                self.want_panel = self.want_code = False
                self.own_pos, self.unresize_due = None, False
                self.hover, self.hpos, self.home, self.rects, self.through = False, None, None, None, None
                self.bar, self.bar_home, self.want_bar, self.glide_n, self.bar_inset = False, None, False, 0, 0
                self.shown = True
                self.save_t = QTimer(self, singleShot=True, interval=700)
                self.setGeometry(500, 300, self.cur_w, window_win.H)
                self.show()
                self.poll = QTimer(self, interval=33, timeout=self._poll_mouse)
                self.poll.setTimerType(Qt.PreciseTimer)
                self.poll.start()
                self.look_t = 0.0

            def _js(self, code):
                pass

            def _save_pos(self):
                pass

            def _set_through(self, on):                                  # (the real one is Win32 / Cocoa)
                throughs.append((on, self.isVisible()))
                self.through = on

            def _poll_mouse(self):
                calls.append(("poll", self.isVisible()))
                return super()._poll_mouse()

            def _follow(self):
                calls.append(("follow", self.isVisible()))
                return super()._follow()

        self.win = Win()
        self.settle()

    def tearDown(self):
        self.win.poll.stop()
        self.win.close()
        self.win.deleteLater()
        self.settle()

    def settle(self):
        for _ in range(5):
            self.app.processEvents()

    def timers(self):
        return [t for t in self.win.findChildren(window_win.QTimer) if t is not self.win.save_t]

    # 1
    def test_hide_stops_the_poll(self):
        self.assertTrue(self.win.poll.isActive())
        self.win.hide_me()
        self.assertFalse(self.win.poll.isActive())

    def test_hide_in_hover_stops_the_poll(self):
        self.win.set_hover(True)
        self.win.hide_me()
        self.assertFalse(self.win.poll.isActive())

    # 2
    def test_show_starts_it_at_the_normal_rate(self):
        self.win.hide_me()
        self.win.show_me()
        self.assertTrue(self.win.poll.isActive())
        self.assertEqual(self.win.poll.interval(), 33)

    def test_show_starts_it_at_the_hover_rate(self):
        self.win.set_hover(True)
        self.win.hide_me()
        self.win.show_me()
        self.assertTrue(self.win.poll.isActive())
        self.assertEqual(self.win.poll.interval(), 8)

    def test_show_when_already_shown_keeps_it_running(self):
        self.win.show_me()
        self.assertTrue(self.win.poll.isActive())
        self.assertEqual(self.win.poll.interval(), 33)

    # 3
    def test_hide_show_over_and_over_keeps_one_timer(self):
        poll = self.win.poll
        before = len(self.timers())
        for i in range(5):
            self.win.hide_me()
            self.assertFalse(self.win.poll.isActive(), i)
            self.win.show_me()
            self.assertTrue(self.win.poll.isActive(), i)
        self.assertIs(self.win.poll, poll)
        self.assertEqual(len(self.timers()), before)
        self.assertEqual(self.win.poll.interval(), 33)
        self.win.set_hover(True)
        for i in range(3):
            self.win.hide_me()
            self.win.show_me()
        self.assertIs(self.win.poll, poll)
        self.assertEqual(len(self.timers()), before)
        self.assertEqual(self.win.poll.interval(), 8)

    def test_one_tick_per_interval_after_reshows(self):
        """(no second connection to _poll_mouse piles up: one tick runs it once)"""
        for _ in range(3):
            self.win.hide_me()
            self.win.show_me()
        del self.calls[:]
        self.win.poll.timeout.emit()
        self.assertEqual([c for c in self.calls if c[0] == "poll"], [("poll", True)])

    # 4
    def test_show_brings_click_through_up_to_date_at_once(self):
        self.win.rects = [(0, 0, 1, 1)]
        self.win.hide_me()
        del self.calls[:], self.throughs[:]
        self.win.show_me()                                               # (no event loop: no tick can run meanwhile)
        self.assertIn(("poll", True), self.calls)
        self.assertTrue(self.throughs and self.throughs[-1][1], self.throughs)   # set while he's visible
        self.assertIsNotNone(self.win.through)

    def test_show_in_hover_puts_him_on_the_pointer_at_once(self):
        self.win.set_hover(True)
        self.win.hide_me()
        del self.calls[:]
        self.win.show_me()
        self.assertIn(("follow", True), self.calls)
        self.assertIsNotNone(self.win.hpos)
        self.assertEqual([self.win.x(), self.win.y()], self.win.hpos)

    # 5
    def test_hover_on_while_hidden_leaves_the_poll_off(self):
        self.win.hide_me()
        self.win.set_hover(True)
        self.assertFalse(self.win.poll.isActive())
        self.win.show_me()
        self.assertTrue(self.win.poll.isActive())
        self.assertEqual(self.win.poll.interval(), 8)

    def test_hover_off_while_hidden_leaves_the_poll_off(self):
        self.win.set_hover(True)
        self.win.hide_me()
        self.win.set_hover(False)
        self.assertFalse(self.win.poll.isActive())
        self.win.show_me()
        self.assertTrue(self.win.poll.isActive())
        self.assertEqual(self.win.poll.interval(), 33)

    def test_hover_while_shown_sets_the_rate(self):
        self.win.set_hover(True)
        self.assertTrue(self.win.poll.isActive())
        self.assertEqual(self.win.poll.interval(), 8)
        self.win.set_hover(False)
        self.assertTrue(self.win.poll.isActive())
        self.assertEqual(self.win.poll.interval(), 33)


if __name__ == "__main__":
    unittest.main()
