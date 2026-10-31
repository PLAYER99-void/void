"""Void must finish the sentence on the new speakers when you pick others while he talks: Speaker._write opens a new
player and carries on, including the part the old one had queued but never played.

    python3 -m unittest discover -s tests
"""
import sys
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

RATE = 8000
STEP = 320                                                 # bytes per write: 20 ms at RATE
DATA = bytes(range(256)) * 19                              # 4864 bytes, 15.2 steps; no two chunks alike


class FakeProc:
    """A player that plays in real time (the clock moves 20 ms per step) and dies after `dies` writes."""
    def __init__(self, clock, dies=None, on_die=None):
        self.clock, self.dies, self.on_die, self.got, self.dead = clock, dies, on_die, b"", False
        self.stdin = self

    def write(self, b):
        if self.dies is not None and len(self.got) // STEP >= self.dies:
            self.dead = True
            if self.on_die:
                self.on_die()
            raise BrokenPipeError("the speakers have changed")
        self.got += b
        self.clock[0] += STEP / 2 / RATE

    def flush(self):
        pass

    def poll(self):
        return 0 if self.dead else None

    def kill(self):
        pass


@unittest.skipIf(server is None, f"server.py doesn't import here: {IMPORT_ERR}")
class SwitchSpeakers(unittest.TestCase):
    def setUp(self):
        S = server.Speaker
        self.clock = [100.0]
        self.made = []
        for k, v in (("gen", 7), ("proc", None), ("prate", RATE), ("written", 1000), ("t0", 100.0), ("filled", False),
                     ("stats", {})):
            p = mock.patch.object(S, k, v)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(server.time, "monotonic", lambda: self.clock[0])
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(server, "make_player", self.make_player)
        p.start()
        self.addCleanup(p.stop)
        self.old = FakeProc(self.clock, dies=10)           # 1000 samples (125 ms) queued, then it dies
        S.proc = self.old
        self.spare = []                                    # the players the Speaker will be given next

    def make_player(self, rate):
        proc = self.spare.pop(0) if self.spare else FakeProc(self.clock)
        self.made.append(proc)
        return proc

    def test_carries_on_from_what_the_old_player_still_had_queued(self):
        self.assertTrue(server.Speaker._write(DATA, 7))
        self.assertEqual(len(self.made), 1)
        new = self.made[0]
        self.assertIs(server.Speaker.proc, new)
        self.assertEqual(len(self.old.got), 10 * STEP)
        self.assertEqual(new.got, DATA[len(DATA) - len(new.got):])      # the end of the sentence, whole
        back = len(new.got) - (len(DATA) - 10 * STEP)
        self.assertAlmostEqual(back, 1000 * 2, delta=4)                 # replays the queue, nothing more
        self.assertEqual(server.Speaker.written, len(new.got) // 2)     # the clock counts from the new player

    def test_cut_off_meanwhile_returns_false(self):
        self.old.on_die = lambda: setattr(server.Speaker, "gen", 8)
        self.assertFalse(server.Speaker._write(DATA, 7))
        self.assertEqual(self.made, [])

    def test_stopped_returns_false(self):
        self.old.on_die = lambda: setattr(server.Speaker, "proc", None)
        self.assertFalse(server.Speaker._write(DATA, 7))
        self.assertEqual(self.made, [])

    def test_gives_up_after_one_new_player(self):
        self.spare = [FakeProc(self.clock, dies=3)]
        self.assertFalse(server.Speaker._write(DATA, 7))
        self.assertEqual(len(self.made), 1)

    def test_no_new_player_returns_false(self):
        with mock.patch.object(server, "make_player", side_effect=OSError("no speakers")), \
                mock.patch.object(sys, "stderr"):
            self.assertFalse(server.Speaker._write(DATA, 7))


if __name__ == "__main__":
    unittest.main()
