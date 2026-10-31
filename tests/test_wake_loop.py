"""The "hey Void" loop (Wake.loop) at rest: while it shouldn't listen (hidden, the mic busy, he's quiet and the wake
word is off) it waits without waking up every 0.2 s, and still starts listening right away (within 0.25 s) when it
should again: shown (/api/sleep off), the wake word switched on (/api/settings), the mic let go (/api/listen ends),
he starts talking with barge-in on (/api/speaking), the follow-up window opening after he stops. Switched off
entirely, the loop ends promptly. The real request handler on a local port; the mic session is a stand-in.

    python3 -m unittest discover -s tests
"""
import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

try:
    import server
    from http.server import ThreadingHTTPServer
except Exception as e:  # noqa: BLE001
    server, IMPORT_ERR = None, e
else:
    IMPORT_ERR = None

PROMPT = 0.25            # how soon a session must start (or the loop end) once it should
OFF = {"wake_word": False, "barge_in": False, "follow_up": 0}


@unittest.skipIf(server is None, f"server.py doesn't import here: {IMPORT_ERR}")
class WakeLoop(unittest.TestCase):
    def setUp(self):
        W = server.Wake
        self._saved = {k: W.__dict__[k] for k in ("thread", "speaking_until", "follow_from", "follow_until",
                                                    "follow_on", "cut", "asleep")}
        self.addCleanup(self._restore)
        W.thread, W.asleep, W.cut = None, False, None
        W.speaking_until = W.follow_from = W.follow_until = 0.0
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.orig_should_run = W.__dict__["should_run"].__func__
        self.orig_wanted = W.__dict__["wanted"].__func__
        self.sessions = []                       # monotonic start time of each mic session
        self.counts = {"should_run": 0, "wanted": 0}
        self.counting = False
        self.release = threading.Event()         # lets a stand-in /api/listen recording finish
        test = self

        def should_run(cls):
            if test.counting and threading.current_thread() is cls.thread:
                test.counts["should_run"] += 1
            return test.orig_should_run(cls)

        def wanted(cls):
            if test.counting and threading.current_thread() is cls.thread:
                test.counts["wanted"] += 1
            return test.orig_wanted(cls)

        def session(cls):                        # "the mic is open" until it shouldn't be
            test.sessions.append(time.monotonic())
            while test.orig_should_run(cls):
                time.sleep(0.01)

        def listen_run(ls, emit):                # a push-to-talk recording held until the test lets go
            test.release.wait(5)

        for p in (mock.patch.dict(server.CFG, OFF),
                  mock.patch.object(server, "CONF_PATH", Path(tmp.name) / "config.json"),
                  mock.patch.object(server, "state_info", lambda: {}),
                  mock.patch.object(server, "whisper", lambda: None),
                  mock.patch.object(server.Speaker, "stop", classmethod(lambda cls: None)),
                  mock.patch.object(server.Wake, "should_run", classmethod(should_run)),
                  mock.patch.object(server.Wake, "wanted", classmethod(wanted)),
                  mock.patch.object(server.Wake, "session", classmethod(session)),
                  mock.patch.object(server.Listen, "run", listen_run)):
            p.start()
            self.addCleanup(p.stop)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self._shutdown_loop)     # (runs first)

    def _restore(self):
        for k, v in self._saved.items():
            setattr(server.Wake, k, v)

    def _shutdown_loop(self):
        self.release.set()
        server.CFG.update(OFF)
        getattr(server.Wake, "nudge", lambda: None)()        # (CFG changed behind save_config's back)
        t = server.Wake.thread
        if t:
            t.join(2)

    def post(self, path, body=None, wait=True):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        c.request("POST", path, body=json.dumps(body or {}),
                  headers={"X-Void-Token": server.TOKEN, "Content-Type": "application/json"})
        if not wait:
            return c
        r = c.getresponse()
        data = r.read()
        c.close()
        self.assertEqual(r.status, 200, data)
        return data

    def start(self, **cfg):
        """Settings as given, then the loop started as the server does (Wake.ensure)."""
        self.t_start = time.monotonic()
        server.CFG.update(cfg)
        server.Wake.ensure()
        self.assertTrue(server.Wake.thread and server.Wake.thread.is_alive())

    def idle(self, settle=0.25):
        """Let the loop find it shouldn't run (and any session end)."""
        time.sleep(settle)
        self.assertFalse(server.Wake.should_run())

    def assert_session_soon(self, t0, budget=PROMPT, what=""):
        n = len(self.sessions)
        deadline = t0 + budget + 0.5
        while time.monotonic() < deadline and not [s for s in self.sessions if s >= t0]:  # noqa: B023
            time.sleep(0.005)
        got = [s for s in self.sessions if s >= t0]
        self.assertTrue(got, f"no session after {what}")
        self.assertLessEqual(got[0] - t0, budget, f"session started {got[0] - t0:.3f}s after {what}")
        return n

    # ---- 1. no busy-polling at rest
    def test_waits_without_polling_while_asleep(self):
        self.start(wake_word=True)
        self.post("/api/sleep", {"on": True})
        self.idle()
        self.counting = True
        time.sleep(1.0)
        self.counting = False
        c = self.counts
        self.assertLessEqual(c["should_run"], 2, f"should_run checked {c['should_run']}x in 1 s while asleep")
        self.assertLessEqual(c["wanted"], 2, f"wanted checked {c['wanted']}x in 1 s while asleep")

    def test_waits_without_polling_while_wake_word_off(self):
        self.start(barge_in=True)                # wanted, but nothing to listen for while he's quiet
        self.idle()
        self.counting = True
        time.sleep(1.0)
        self.counting = False
        self.assertLessEqual(self.counts["should_run"], 2, f"should_run checked {self.counts['should_run']}x in 1 s")

    # ---- 2. every way back to "should run" starts a session promptly
    def test_unsleeping_starts_a_session(self):
        self.start(wake_word=True)
        self.assert_session_soon(self.t_start, PROMPT, "the loop starting")
        self.post("/api/sleep", {"on": True})
        self.idle()
        t0 = time.monotonic()
        self.post("/api/sleep", {"on": False})
        self.assert_session_soon(t0, what="/api/sleep off")

    def test_enabling_wake_word_in_settings_starts_a_session(self):
        self.start(barge_in=True)
        self.idle()
        self.assertEqual(self.sessions, [])
        t0 = time.monotonic()
        self.post("/api/settings", {"wake_word": True})
        self.assert_session_soon(t0, what="settings wake_word on")

    def test_enabling_wake_word_from_fully_off_starts_a_session(self):
        t0 = time.monotonic()
        self.post("/api/settings", {"wake_word": True})   # (no loop yet: settings starts it)
        self.assert_session_soon(t0, what="settings wake_word on (loop not running)")

    def test_mic_let_go_starts_a_session(self):
        self.start(wake_word=True)
        self.assert_session_soon(self.t_start, PROMPT, "the loop starting")
        c = self.post("/api/listen", {"mode": "ptt"}, wait=False)
        self.addCleanup(c.close)
        deadline = time.monotonic() + 3
        while not server.Listen.lock.locked() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(server.Listen.lock.locked())
        self.idle()
        t0 = time.monotonic()
        self.release.set()                       # the recording ends: /api/listen lets go of the mic
        r = c.getresponse()
        r.read()
        self.assertFalse(server.Listen.lock.locked())
        self.assert_session_soon(t0, what="the Listen lock released")

    def test_speaking_with_barge_in_starts_a_session(self):
        self.start(barge_in=True)
        self.idle()
        t0 = time.monotonic()
        self.post("/api/speaking", {"on": True})
        self.assert_session_soon(t0, what="/api/speaking on (barge-in)")

    def test_speaking_without_barge_in_does_not(self):
        self.start(follow_up=4)                  # wanted only for the follow-up window
        self.idle()
        self.post("/api/speaking", {"on": True})
        time.sleep(0.4)
        self.assertEqual(self.sessions, [])

    def test_follow_up_window_opening_starts_a_session(self):
        self.start(follow_up=4)
        self.idle()
        self.post("/api/speaking", {"on": True})
        time.sleep(0.2)
        self.post("/api/speaking", {"on": False})        # stops talking: window opens 0.8 s from now
        opens = server.Wake.follow_from
        self.assertGreater(server.Wake.follow_until, opens)
        self.assertEqual(self.sessions, [])
        self.assert_session_soon(opens, what="the follow-up window opening")

    def test_talking_until_sets_follow_window_directly(self):  # (what the server-side Speaker calls per sentence)
        self.start(follow_up=4)
        self.idle()
        server.Wake.talking_until(time.monotonic() + 0.3)
        self.assert_session_soon(server.Wake.follow_from, what="Wake.talking_until's follow-up window")

    # ---- 3. switched off: the loop ends promptly, even while waiting
    def test_switching_everything_off_ends_the_loop(self):
        self.start(barge_in=True)
        self.idle()
        t = server.Wake.thread
        t0 = time.monotonic()
        self.post("/api/settings", {"barge_in": False, "wake_word": False, "follow_up": 0})
        t.join(PROMPT + 1.0)
        self.assertFalse(t.is_alive(), "the wake loop is still running")
        self.assertLessEqual(time.monotonic() - t0, PROMPT, f"took {time.monotonic() - t0:.3f}s to end")

    def test_switching_off_while_asleep_ends_the_loop(self):
        self.start(wake_word=True)
        self.post("/api/sleep", {"on": True})
        self.idle()
        t = server.Wake.thread
        t0 = time.monotonic()
        self.post("/api/settings", {"wake_word": False})
        t.join(PROMPT + 1.0)
        self.assertFalse(t.is_alive(), "the wake loop is still running")
        self.assertLessEqual(time.monotonic() - t0, PROMPT, f"took {time.monotonic() - t0:.3f}s to end")


if __name__ == "__main__":
    unittest.main()
