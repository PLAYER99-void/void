"""Tests for app/winsys.py's Player and Mic starting PortAudio over when the speakers or mics change, against a fake
sounddevice (no real audio, nothing audible).

    python3 -m unittest discover -s tests
"""
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))
import winsys  # noqa: E402


class FakeStream:
    def __init__(self, sd, kind, kw):
        self.sd, self.kind, self.kw, self.active, self.closed = sd, kind, kw, False, False
        dev, devs = kw.get("device"), sd.seen["devs"]
        if dev is None:
            self.device = sd.seen[kind]
        elif isinstance(dev, int):
            self.device = devs[dev]
        elif dev in devs:
            self.device = dev
        else:
            raise ValueError(f"No {kind}put device matching {dev!r}")
        sd.made.append(self)

    def start(self):
        if self.sd.fail:
            self.sd.fail -= 1
            raise self.sd.PortAudioError("Internal PortAudio error")
        self.active = True
        self.sd.open.append(self)
        self.tick()
        self.tick()

    def tick(self):
        """One callback — only if its device is really still there (PortAudio's list may be out of date)."""
        if not self.active or self.device not in self.sd.system["devs"] or self.sd.silent:
            return
        frames = 160
        if self.kind == "out":
            self.kw["callback"](bytearray(frames * 2), frames, None, None)
        else:
            self.kw["callback"](b"\1\0" * frames, frames, None, None)

    def stop(self):
        self.active = False

    abort = stop

    def close(self):
        self.active, self.closed = False, True
        if self in self.sd.open:
            self.sd.open.remove(self)


class FakeSD(types.ModuleType):
    class PortAudioError(Exception):
        pass

    def __init__(self):
        super().__init__("sounddevice")
        self.system = {"out": "DAC", "in": "Built-in Mic", "devs": ["DAC", "Built-in Mic", "Speakers"]}
        self.inits = self.terms = self.fail = 0
        self.silent = False
        self.open, self.made, self.bad = [], [], []
        self._initialize()                                 # (as sounddevice does when it's imported)

    def _initialize(self):
        self.inits += 1
        self.seen = {"out": self.system["out"], "in": self.system["in"], "devs": list(self.system["devs"])}

    def _terminate(self):
        if self.open:
            self.bad.append([s.device for s in self.open])  # a restart with a stream open
        self.terms += 1

    def _dev(self, name):
        mic = "Mic" in name
        return {"name": name, "hostapi": 0, "max_input_channels": 1 if mic else 0,
                "max_output_channels": 0 if mic else 2, "default_samplerate": 48000.0}

    def query_devices(self, device=None, kind=None):
        devs = [self._dev(n) for n in self.seen["devs"]]
        if device is not None:
            return devs[device]
        if kind:
            return self._dev(self.seen["in" if kind == "input" else "out"])
        return devs

    def query_hostapis(self, i):
        return {"name": "Core Audio"}

    def RawOutputStream(self, **kw):
        return FakeStream(self, "out", kw)

    def RawInputStream(self, **kw):
        return FakeStream(self, "in", kw)


class Base(unittest.TestCase):
    mac = False                                            # True: the system's devices can be asked (macsys)

    def setUp(self):
        self.sd = FakeSD()
        self._old = sys.modules.get("sounddevice")
        sys.modules["sounddevice"] = self.sd
        self._first, winsys.FIRST = winsys.FIRST, 0.05
        winsys._PA.live.clear()
        winsys._PA.sig, winsys._PA.stale = None, False
        s = self.sd.system
        winsys.devices = (lambda: (s["out"], s["in"], tuple(s["devs"]))) if self.mac else None

    def tearDown(self):
        for o in list(winsys._PA.live):
            o.kill()
        self.assertEqual(self.sd.bad, [], "PortAudio restarted with a stream open")
        winsys.FIRST, winsys.devices = self._first, None
        if self._old is None:
            sys.modules.pop("sounddevice", None)
        else:
            sys.modules["sounddevice"] = self._old

    def plug(self, name, default=None):
        self.sd.system["devs"].append(name)
        if default:
            self.sd.system[default] = name

    def unplug(self, name):
        self.sd.system["devs"].remove(name)


class WindowsLike(Base):
    """No way to ask the system: only a stream that dies or never runs starts PortAudio over."""

    def test_plays_without_restarting(self):
        p = winsys.Player(22050)
        p.stdin.write(b"\5\0" * 100)
        self.sd.made[-1].tick()
        self.assertEqual(len(p.buf), 0)                    # (the callback took it)
        self.assertIsNone(p.poll())
        p.kill()
        self.assertEqual(p.poll(), 0)
        winsys.Player(22050)
        self.assertEqual((self.sd.inits, self.sd.terms), (1, 0))

    def test_stalled_stream_ends_and_next_one_restarts(self):
        p = winsys.Player(22050)
        self.unplug("DAC")                                 # USB DAC switched off: no more callbacks
        p.ran -= 10
        with self.assertRaises(BrokenPipeError):
            p.stdin.write(b"\0\0")
        self.assertEqual(p.poll(), 0)
        self.assertTrue(self.sd.made[-1].closed)
        self.plug("DAC 2", "out")                          # back on, under a new id
        winsys.Player(22050)
        self.assertEqual(self.sd.terms, 1)
        self.assertEqual(self.sd.made[-1].device, "DAC 2")

    def test_silent_stream_is_retried_on_a_fresh_list(self):
        self.unplug("DAC")
        self.plug("DAC 2", "out")                          # PortAudio still thinks the default is "DAC"
        p = winsys.Player(22050)
        self.assertEqual(self.sd.terms, 1)
        self.assertEqual([s.device for s in self.sd.made], ["DAC", "DAC 2"])
        self.assertTrue(self.sd.made[0].closed)
        self.assertIsNone(p.poll())
        self.assertFalse(winsys._PA.stale)

    def test_never_calls_back_at_all(self):
        self.sd.silent = True
        p = winsys.Player(22050)                           # handed over anyway, after one fresh try …
        self.assertEqual(self.sd.terms, 1)
        self.assertTrue(winsys._PA.stale)
        with self.assertRaises(BrokenPipeError):           # … but it says it's dead, like a paplay that exited
            p.stdin.write(b"\0\0")
        self.sd.silent = False
        winsys.Player(22050)                               # and the next one starts over again
        self.assertEqual(self.sd.terms, 2)
        self.assertFalse(winsys._PA.stale)

    def test_portaudio_error_is_retried_once(self):
        self.sd.fail = 1
        p = winsys.Player(22050)
        self.assertEqual(self.sd.terms, 1)
        self.assertIsNone(p.poll())
        self.sd.fail = 2
        with self.assertRaises(FakeSD.PortAudioError):
            winsys.Player(22050)
        self.assertTrue(winsys._PA.stale)

    def test_unknown_mic_is_not_retried(self):
        with self.assertRaises(ValueError):
            winsys.Mic(16000, "nonexistent")
        self.assertEqual(self.sd.terms, 0)
        self.assertFalse(winsys._PA.stale)

    def test_mic_stream_that_stops_ends_reading(self):
        m = winsys.Mic(16000)
        self.assertTrue(m.read(0.01))
        self.sd.made[-1].active = False                    # (the stream stopped by itself)
        with self.assertRaises(EOFError):
            m.read(0.01)
        m.close()
        winsys.Mic(16000)
        self.assertEqual(self.sd.terms, 1)

    def test_mic_that_never_calls_back(self):
        self.sd.silent = True
        m = winsys.Mic(16000)
        with self.assertRaises(EOFError):                  # (server.open_mic: "couldn't open the mic")
            m.read(0.01)


class MacLike(Base):
    """The system's devices are asked (Core Audio, on a Mac) whenever a stream opens or a Player is reused."""
    mac = True

    def test_restarts_only_when_devices_changed(self):
        winsys.Player(22050).kill()
        self.assertEqual(self.sd.terms, 1)                 # (the first stream: whatever was read at import)
        winsys.Player(22050).kill()
        winsys.Mic(16000).close()
        self.assertEqual(self.sd.terms, 1)
        self.plug("iPhone Mic", "in")
        winsys.Mic(16000)
        self.assertEqual(self.sd.terms, 2)
        self.assertEqual(self.sd.made[-1].device, "iPhone Mic")

    def test_new_default_output_ends_the_player(self):
        p = winsys.Player(22050)
        self.assertIsNone(p.poll())
        self.plug("AirPods", "out")
        self.assertEqual(p.poll(), 0)                      # the Speaker opens a new one for the next sentence
        winsys.Player(22050)
        self.assertEqual(self.sd.terms, 2)
        self.assertEqual(self.sd.made[-1].device, "AirPods")

    def test_restart_closes_open_streams_first(self):
        wake, p = winsys.Mic(16000), winsys.Player(22050)
        self.unplug("DAC")
        self.plug("DAC 2", "out")
        rec = winsys.Mic(16000)                            # tearDown: no stream open across the restart
        self.assertEqual(self.sd.terms, 2)
        self.assertEqual(p.poll(), 0)
        with self.assertRaises(BrokenPipeError):
            p.stdin.write(b"\0\0")
        with self.assertRaises(EOFError):
            wake.read(0.01)
        self.assertTrue(rec.read(0.01))
        self.assertEqual(winsys._PA.live, {rec})

    def test_list_mics_waits_while_a_stream_is_open(self):
        p = winsys.Player(22050)
        self.plug("iPhone Mic")
        self.assertNotIn("iPhone Mic", [m["id"] for m in winsys.list_mics()])
        self.assertTrue(p.alive)                           # (a list for Settings never cuts him off)
        p.kill()
        self.assertIn("iPhone Mic", [m["id"] for m in winsys.list_mics()])

    def test_cant_ask_counts_as_unchanged(self):
        p = winsys.Player(22050)

        def broken():
            raise OSError("Core Audio won't say")
        winsys.devices = broken
        self.assertIsNone(p.poll())
        winsys.Player(22050)
        self.assertEqual(self.sd.terms, 1)


@unittest.skipUnless(sys.platform == "darwin", "Core Audio")
class CoreAudio(unittest.TestCase):
    def test_audio_devices(self):
        import macsys
        try:
            self.assertIs(winsys.devices, macsys.audio_devices)
            out, inp, devs = macsys.audio_devices()
            for ids in (out, inp, devs):
                self.assertIsInstance(ids, tuple)
                self.assertTrue(all(isinstance(i, int) for i in ids))
            self.assertTrue(set(out + inp) - {0} <= set(devs))   # (0: no such device right now)
        finally:
            winsys.devices = None


if __name__ == "__main__":
    unittest.main()
