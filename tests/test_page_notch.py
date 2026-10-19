"""Tests for the notch on the page: docked, when it moves to hang at another depth (the menu bar slid down over a
full-screen app, or went), the liquid starts and ends exactly where the real notch is, so nothing snaps when the real
notch takes over again. The real page in Qt's web view, offscreen: no window on the desktop.

    python3 -m unittest discover -s tests
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "app" / "static" / "index.html"
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --num-raster-threads=1 --renderer-process-limit=1")
try:
    from PySide6.QtCore import QEvent, QUrl
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication
except ImportError:
    QWebEngineView = None

# every frame while the liquid stands in for the notch: where its bar reaches and where the real notch is
WATCH = """(()=>{ window.__seen=[]; const bar=$('#gooBar'), pill=$('#barPill');
  (function f(){ const b=document.body.classList; if(b.contains('goohide')){ const r=bar.getBoundingClientRect();
      window.__seen.push([r.top,r.bottom,r.left,r.right]); }
    if(window.__seen) requestAnimationFrame(f); })();
  return 1; })()"""
PILL = "JSON.stringify((r=>[r.top,r.bottom,r.left,r.right])($('#barPill').getBoundingClientRect()))"


@unittest.skipUnless(QWebEngineView and sys.platform == "darwin", "macOS with PySide6's web view only")
class NotchShift(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.view = QWebEngineView()
        cls.view.resize(340, 460)
        cls.view.show()                                                  # (offscreen: it only animates shown)
        cls.view.load(QUrl.fromLocalFile(str(PAGE)))
        cls.wait_for(lambda: cls.js("typeof voidBarInset==='function'&&typeof voidDock==='function'"))

    @classmethod
    def tearDownClass(cls):
        page = cls.view.page()
        cls.view.close()
        page.deleteLater()                                               # (the page before the profile it uses)
        cls.view.deleteLater()
        cls.app.sendPostedEvents(None, QEvent.DeferredDelete)

    @classmethod
    def js(cls, code, timeout=5.0):
        got = []
        cls.view.page().runJavaScript(code, 0, got.append)
        end = time.monotonic() + timeout
        while not got and time.monotonic() < end:
            cls.app.processEvents()
            time.sleep(0.005)
        return got[0] if got else None

    @classmethod
    def wait_for(cls, cond, timeout=15.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            cls.app.processEvents()
            time.sleep(0.02)
        raise AssertionError("the page never got there")

    def pill(self):
        return json.loads(self.js(PILL))

    def shift(self, px):
        """Hang the docked notch px in, as the window says when the menu bar comes or goes: → (the real notch before,
        every frame of the liquid, the real notch after)."""
        before = self.pill()
        self.js(WATCH)
        self.js(f"voidBarInset({px},true)")
        self.wait_for(lambda: self.js("document.body.classList.contains('goohide')") is False)
        seen = self.js("JSON.stringify(window.__seen)")
        self.js("window.__seen=null")
        return before, json.loads(seen), self.pill()

    def test_drip_down_and_slurp_up_start_and_end_on_the_notch(self):
        self.js("voidBarInset(0,false); voidDock('top','')")
        self.wait_for(lambda: self.js("document.body.classList.contains('bar-done')"))
        for px in (30, 0):                                               # under the menu bar, and back to the edge
            before, seen, after = self.shift(px)
            self.assertGreater(len(seen), 3)
            self.assertAlmostEqual(after[1], 52 + px, delta=1)           # (the real notch: at the new depth)
            # the liquid's bar: its first frame no longer than the notch was, its last exactly the notch now (a
            # longer one is cut back in one frame when the real notch takes over: a snap)
            self.assertAlmostEqual(seen[0][1], before[1], delta=2)
            self.assertAlmostEqual(seen[-1][1], after[1], delta=1)
            self.assertLessEqual(seen[-1][0], after[0] + 1)              # (and hangs from at least as high)


if __name__ == "__main__":
    unittest.main()
