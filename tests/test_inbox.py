"""Inbox alerts use the voice session path, wait for Claude, and never replay old alerts."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import server


class Inbox(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "inbox.log"
        self.messages = []
        self.history = []
        self.replies = []
        self.during = []
        self.lock = threading.Lock()
        for patch in (
            mock.patch.object(server.ST, "lock", self.lock),
            mock.patch.object(server.ST, "auto_pick", ""),
            mock.patch.object(server.ST, "stopped", False),
            mock.patch.object(server.ST, "alerting", False),
            mock.patch.object(server.ST, "compacting", False),
            mock.patch.object(server.ST, "last_worked", False),
            mock.patch.dict(server.CFG, {"long_lived": True, "auto_compact": False}),
            mock.patch.dict(server.LONG, {"ok": True}),
            mock.patch.object(server, "claude_bin", return_value="claude"),
            mock.patch.object(server, "brain_launch", return_value={"session": "existing-session"}),
            mock.patch.object(server.BRAIN, "ask", side_effect=self.ask),
            mock.patch.object(server, "hist_add", side_effect=self.history.append),
            mock.patch.object(server.recall, "add"),
            mock.patch.object(server.BUS, "pub", side_effect=lambda **ev: self.replies.append(ev)),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def ask(self, launch, prompt, emit):
        self.assertEqual(launch["session"], "existing-session")
        self.assertTrue(self.lock.locked())
        self.messages.append(prompt)
        self.during.append((server.ST.alerting, server.lock_wait()))
        emit(t="result", text="Alert received", is_error=False)

    def append(self, text):
        with self.path.open("a", encoding="utf-8") as f:
            f.write(text)

    def start(self):
        follower = server.InboxFollower(self.path)
        follower.start()
        self.addCleanup(follower.close)
        return follower

    def followups(self):
        return [ev for ev in self.replies if ev["t"] == "followup"]

    def wait_for_replies(self, count):
        until = time.monotonic() + 3
        while len(self.followups()) < count and time.monotonic() < until:
            time.sleep(0.01)
        self.assertEqual(len(self.followups()), count)

    def test_appended_alerts_reach_session_in_order_after_busy_and_skip_own_stop(self):
        self.append("2026-10-23 10:00:00 [worker] old alert\n")
        self.lock.acquire()
        try:
            follower = self.start()
            first = "2026-10-23 10:01:00 [builder] tests passed"
            second = "2026-10-23 10:01:01 [claude] other session finished"
            third = "2026-10-23 10:01:02 [worker] atom Stop is not our echo"
            self.append(first + "\n2026-10-23 10:01:00 [claude] atom Stop hook echo\n" + second + "\n" + third + "\n")
            time.sleep(0.35)                  # the watcher sees the file while the session is occupied
            self.assertEqual(self.messages, [])
        finally:
            self.lock.release()
        self.wait_for_replies(3)
        follower.close()
        self.assertEqual(self.messages, [first, second, third])
        self.assertEqual([rec["you"] for rec in self.history], self.messages)
        self.assertTrue(all(ev == {"t": "followup", "text": "Alert received", "ok": True} for ev in self.followups()))

    def test_your_message_waits_for_an_alert_and_he_shows_he_is_on_it(self):
        follower = self.start()
        self.assertEqual(server.lock_wait(), 0)          # his own answer to you: a second message is refused
        self.append("2026-10-23 10:01:00 [worker] done\n")
        self.wait_for_replies(1)
        follower.close()
        alerting, wait = self.during[0]
        self.assertTrue(alerting)
        self.assertGreater(wait, 0)
        self.assertEqual([ev["t"] for ev in self.replies], ["followup_soon", "followup"])
        self.assertFalse(server.ST.alerting)
        self.assertEqual(server.lock_wait(), 0)

    def test_missing_inbox_and_partial_line(self):
        follower = self.start()
        line = "2026-10-23 10:01:00 [worker] ready café"
        self.append(line)
        time.sleep(0.35)
        self.assertEqual(self.messages, [])
        self.append("\n")
        self.wait_for_replies(1)
        follower.close()
        self.assertEqual(self.messages, [line])

    def test_preexisting_partial_line_is_not_delivered(self):
        self.append("2026-10-23 10:00:00 [worker] old partial")
        follower = self.start()
        line = "2026-10-23 10:01:00 [worker] new alert"
        self.append(" completed\n" + line + "\n")
        self.wait_for_replies(1)
        follower.close()
        self.assertEqual(self.messages, [line])

    def test_close_while_busy_does_not_deliver(self):
        self.lock.acquire()
        try:
            follower = self.start()
            self.append("2026-10-23 10:01:00 [worker] waiting\n")
            time.sleep(0.35)
            follower.close()
            self.assertFalse(follower.thread.is_alive())
        finally:
            self.lock.release()
        self.assertEqual(self.messages, [])


if __name__ == "__main__":
    unittest.main()
