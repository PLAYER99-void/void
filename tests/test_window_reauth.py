"""After the server restarts, the page says "reauth" and his window loads it again with the new token. That has to be
a real reload: the page wipes '#t=…' from its address, so an address that differs only after '#' is an in-page jump,
the old page stays, and every mic, screen and ask request is turned away as "forbidden".

    python3 -m unittest discover -s tests
"""
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QUrl  # noqa: E402
    import window_win  # noqa: E402
except (ImportError, OSError):
    window_win = None


class FakeView:
    def __init__(self):
        self.loaded = []

    def load(self, url):
        self.loaded.append(url.toString())


class FakeWindow:
    """Just what _on_msg("reauth") touches: the page's address and the view it loads into."""

    def __init__(self, token):
        self.url, self.token, self.view = "http://127.0.0.1:47777/?native=1", token, FakeView()

    def _authed_url(self):
        return self.url + "#t=" + self.token


@unittest.skipUnless(window_win, "PySide6 (and window_win) only")
class Reauth(unittest.TestCase):
    def reauth(self, w):
        window_win.VoidWindow._on_msg(w, "reauth")
        return QUrl(w.view.loaded[-1])

    def test_new_token_loads_a_new_page_not_an_in_page_jump(self):
        w = FakeWindow("NEW")
        u = self.reauth(w)
        self.assertEqual(u.fragment(), "t=NEW")
        page_now = w.url                     # (what the page's address is once it has wiped '#t=')
        self.assertNotEqual(u.toString().partition("#")[0], page_now)
        self.assertIn("native=1", u.query())

    def test_every_reauth_is_a_different_address(self):
        w = FakeWindow("A")
        first = self.reauth(w).toString().partition("#")[0]
        w.token = "B"
        self.assertNotEqual(self.reauth(w).toString().partition("#")[0], first)


if __name__ == "__main__":
    unittest.main()
