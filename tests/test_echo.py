"""Void must not hear his own voice as yours: a burst heard while he talks (or just after) is dropped, and so is
any transcript that mostly repeats what he said in the last minute. Barge-in and push-to-talk still work.

    python3 -m unittest discover -s tests
"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

try:
    import server
except Exception as e:  # noqa: BLE001
    server, IMPORT_ERR = None, e
else:
    IMPORT_ERR = None

REPLY = "Sure, I moved the meeting to Thursday at three and told Sam about it."
PCM = bytes(32000)


@unittest.skipIf(server is None, f"server.py doesn't import here: {IMPORT_ERR}")
class Echo(unittest.TestCase):
    def setUp(self):
        W = server.Wake
        self._saved = {k: W.__dict__[k] for k in ("speaking_until", "follow_from", "follow_until")}
        self.addCleanup(self._restore)
        W.speaking_until = W.follow_from = W.follow_until = 0.0
        server.SPOKEN.clear()
        self.addCleanup(server.SPOKEN.clear)
        self.pub = mock.Mock()
        self.text = ""
        for p in (mock.patch.object(server, "speech_seconds", lambda pcm: 2.0),
                  mock.patch.object(server, "transcribe_pcm", lambda pcm, **kw: self.text),
                  mock.patch.object(server.MicAuto, "learn", lambda pcm: None),
                  mock.patch.object(server.BUS, "pub", self.pub),
                  mock.patch.object(server.Speaker, "idle", classmethod(lambda cls: True))):
            p.start()
            self.addCleanup(p.stop)

    def _restore(self):
        for k, v in self._saved.items():
            setattr(server.Wake, k, v)

    def burst(self, text, started=None):
        self.text = text
        server.Wake.check(PCM, time.monotonic() if started is None else started)
        return [c.kwargs for c in self.pub.call_args_list]

    def after_speech(self, ago):             # he finished talking `ago` seconds ago, follow window open
        now = time.monotonic()
        server.Wake.talking_until(now - ago)
        server.Wake.follow_until = now + 30

    # the echo match
    def test_match_repeat_and_partial(self):
        server.note_spoken(REPLY)
        self.assertTrue(server.is_echo("I moved the meeting to Thursday at three"))
        self.assertTrue(server.is_echo("Sure I moved the meeting to thursday, at 3 and told Sam"))

    def test_match_ignores_real_questions(self):
        server.note_spoken(REPLY)
        self.assertFalse(server.is_echo("Can you move the meeting to Friday instead"))
        self.assertFalse(server.is_echo("meeting Thursday"))          # too short to judge
        self.assertFalse(server.is_echo("what is the weather like today"))

    def test_match_in_order_and_expires(self):
        server.note_spoken(REPLY)
        self.assertFalse(server.is_echo("Sam about told and three at Thursday to meeting the moved"))
        server.SPOKEN.clear()
        server.SPOKEN.append((time.monotonic() - 120, REPLY))
        self.assertFalse(server.is_echo("I moved the meeting to Thursday at three"))

    def test_speaker_say_records(self):
        with mock.patch.object(server.Speaker, "_start", classmethod(lambda cls: None)):
            server.Speaker.say(REPLY, 1)
        self.addCleanup(server.Speaker.todo.clear)
        self.assertTrue(server.is_echo("I moved the meeting to Thursday at three"))

    # the time gate
    def test_follow_window_waits_for_tail(self):
        server.Wake.talking_until(time.monotonic())
        self.assertGreaterEqual(server.Wake.follow_from, server.Wake.speaking_until + server.ECHO_TAIL)

    def test_follow_up_in_tail_dropped(self):
        self.after_speech(0.3)               # 0.3 s past the end of his speech: still his room echo
        self.assertEqual(self.burst("please add milk to the shopping list", time.monotonic() - 0.5), [])

    def test_burst_started_before_tail_end_dropped(self):
        self.after_speech(1.6)
        started = server.Wake.speaking_until + server.ECHO_TAIL - 0.2
        server.Wake.follow_from = 0.0
        self.assertEqual(self.burst("please add milk to the shopping list", started), [])

    def test_follow_up_after_tail_accepted(self):
        self.after_speech(server.ECHO_TAIL + 0.6)
        server.Wake.follow_from = 0.0
        ev = self.burst("please add milk to the shopping list")
        self.assertEqual([e.get("t") for e in ev], ["wake"])
        self.assertTrue(ev[0]["follow"])

    def test_follow_up_echo_dropped_after_tail(self):
        self.after_speech(server.ECHO_TAIL + 0.6)
        server.note_spoken(REPLY)
        self.assertEqual(self.burst("I moved the meeting to Thursday at three"), [])

    def test_wake_word_echo_dropped(self):
        server.note_spoken("Okay. Hey Void, you can ask me anything about the calendar this week.")
        self.assertEqual(self.burst("Hey Void, you can ask me anything about the calendar"), [])

    def test_wake_word_still_works(self):
        ev = self.burst("Hey Void, what is the weather like")
        self.assertEqual([e.get("t") for e in ev], ["wake"])

    # barge-in
    def test_barge_in_while_talking(self):
        server.Wake.talking_until(time.monotonic() + 5)
        ev = self.burst("stop")
        self.assertEqual([e.get("t") for e in ev], ["barge"])

    def test_wake_word_barge_in_while_talking(self):
        server.Wake.talking_until(time.monotonic() + 5)
        ev = self.burst("Hey Void, never mind that")
        self.assertEqual([e.get("t") for e in ev], ["barge"])

    def test_barge_in_echo_dropped(self):
        server.note_spoken("Hey Void, you can ask me anything about the calendar this week.")
        server.Wake.talking_until(time.monotonic() + 5)
        self.assertEqual(self.burst("Hey Void, you can ask me anything about the calendar"), [])

    def test_other_speech_while_talking_ignored(self):
        server.Wake.talking_until(time.monotonic() + 5)
        self.assertEqual(self.burst("please add milk to the shopping list"), [])


if __name__ == "__main__":
    unittest.main()
