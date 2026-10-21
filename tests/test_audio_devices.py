"""Tests for app/winsys.py's Player and Mic starting PortAudio over when the speakers or mics change, against a fake
sounddevice (no real audio, nothing audible).

    python3 -m unittest discover -s tests
"""
import json
import subprocess
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
        self.inits = self.terms = self.fail = self._initialized = 0
        self.silent = False
        self.open, self.made, self.bad = [], [], []
        self._initialize()                                 # (as sounddevice does when it's imported)

    def _initialize(self):
        self.inits += 1
        self._initialized += 1
        self.seen = {"out": self.system["out"], "in": self.system["in"], "devs": list(self.system["devs"])}

    def _terminate(self):
        if not self._initialized:
            raise self.PortAudioError("PortAudio not initialized")
        if self.open:
            self.bad.append([s.device for s in self.open])  # a restart with a stream open
        self.terms += 1
        self._initialized -= 1

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
        winsys._PA.sig, winsys._PA.stale, winsys._PA.done = None, False, False
        winsys._PA.mics, winsys._PA.plays = None, True
        winsys._PA.hooked = True                           # (not at this process's exit: Quitting does that)
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


class Stopping(Base):
    def test_stop_closes_everything_and_nothing_opens_again(self):
        wake, p = winsys.Mic(16000), winsys.Player(22050)
        winsys.stop()
        self.assertEqual((self.sd.open, self.sd._initialized), ([], 0))
        self.assertEqual(p.poll(), 0)
        with self.assertRaises(EOFError):
            wake.read(0.01)
        with self.assertRaises(FakeSD.PortAudioError):     # (the wake word's next try: an error, not a new mic)
            winsys.Mic(16000)
        self.assertEqual((winsys.list_mics(), winsys.can_play()), ([], False))
        self.assertEqual((self.sd.inits, self.sd._initialized), (1, 0))


# the wake word keeps a mic open and opens a new one whenever it ends; then Python exits with it still going
QUIT = """
import atexit, json, sys, threading, time
sys.path[:0] = sys.argv[1:3]
from test_audio_devices import FakeSD
import winsys
sd = sys.modules["sounddevice"] = FakeSD()
winsys.FIRST = 0.05

def last():                                        # (registered first, runs last: after any thread had its go)
    time.sleep(0.3)
    print(json.dumps({"open": len(sd.open), "running": sd._initialized, "bad": sd.bad}), flush=True)

def sounddevice_exit():                            # (what sounddevice registers when it's imported)
    for s in list(sd.open):
        s.stop(); s.close()
    while sd._initialized:
        sd._terminate()

def wake():
    while True:
        try:
            m = winsys.Mic(16000)
            while True:
                m.read(0.01)
                sd.made[-1].tick()
        except Exception:
            time.sleep(0.01)

atexit.register(last)
atexit.register(sounddevice_exit)
threading.Thread(target=wake, daemon=True).start()
while not winsys._PA.live:
    time.sleep(0.01)
"""


class Quitting(unittest.TestCase):
    def test_no_stream_or_portaudio_left_running_at_exit(self):
        """At exit no callback may come in after Python has started tearing itself down (macOS: 'Python quit
        unexpectedly', in _os_workgroup_tsd_cleanup): every stream is closed and PortAudio stays stopped."""
        r = subprocess.run([sys.executable, "-c", QUIT, str(HERE), str(HERE.parent / "app")],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout.splitlines()[-1]), {"open": 0, "running": 0, "bad": []})


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


class StuckOpen(Base):
    """A mic stream's open is stuck (macOS's microphone permission prompt) holding _PA.lock: asking about devices
    must still answer at once."""

    def setUp(self):
        super().setUp()
        import threading
        self.threading = threading
        self._wait, winsys.LOCK_WAIT = getattr(winsys, "LOCK_WAIT", 0.25), 0.05
        self.release, self.inside = threading.Event(), threading.Event()
        owner = types.SimpleNamespace(first=threading.Event(), stream=None)

        def make():
            self.inside.set()
            self.release.wait(10)                          # (the permission prompt, still on screen)
            raise ValueError("denied")

        def stuck():
            try:
                winsys._open(self.sd, owner, make)
            except ValueError:
                pass

        self.t = threading.Thread(target=stuck, daemon=True)
        self.t.start()
        self.assertTrue(self.inside.wait(5))

    def tearDown(self):
        self.release.set()
        self.t.join(5)
        winsys.LOCK_WAIT = self._wait
        super().tearDown()

    def answer(self, fn):
        out = []
        t = self.threading.Thread(target=lambda: out.append(fn()), daemon=True)
        t.start()
        t.join(2)
        self.assertFalse(t.is_alive(), "blocked behind the stuck open")
        return out[0]

    def test_stop_does_not_wait_for_the_stuck_open(self):
        self.answer(winsys.stop)                           # (returns while the open still holds the lock)
        self.assertTrue(winsys._PA.done)                   # (and nothing opens again)

    def test_sigterm_runs_every_atexit_callback(self):
        """SIGTERM → SystemExit → atexit: winsys.stop must not hold the callbacks behind the stuck open."""
        r = subprocess.run([sys.executable, "-c", STUCK_QUIT, str(HERE), str(HERE.parent / "app")],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.stdout.split(), ["last"], r.stderr)

    def test_sigterm_leaves_sounddevices_exit_handler_unrun(self):
        """sounddevice registers _exit_handler (Pa_Terminate) at import: with the open stuck, it must not run."""
        r = subprocess.run([sys.executable, "-c", STUCK_QUIT_REAL_HANDLER, str(HERE), str(HERE.parent / "app")],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.stdout.split(), ["last"], r.stderr)

    def test_can_play_answers(self):
        self.assertTrue(self.answer(winsys.can_play))

    def test_list_mics_answers(self):
        self.assertIsInstance(self.answer(winsys.list_mics), list)

    def test_list_mics_keeps_the_last_list(self):
        self.release.set()
        self.t.join(5)
        known = winsys.list_mics()
        self.assertEqual([m["id"] for m in known], ["Built-in Mic"])
        self.release.clear()
        self.inside.clear()
        self.test_list_mics_answers_after(known)

    def test_list_mics_answers_after(self, known=None):
        if known is None:
            return
        owner = types.SimpleNamespace(first=self.threading.Event(), stream=None)
        def make():
            self.inside.set()
            self.release.wait(10)
            raise ValueError("denied")
        t = self.threading.Thread(target=lambda: self._try(owner, make), daemon=True)
        t.start()
        self.assertTrue(self.inside.wait(5))
        self.assertEqual(self.answer(winsys.list_mics), known)
        self.release.set()
        t.join(5)

    def _try(self, owner, make):
        try:
            winsys._open(self.sd, owner, make)
        except ValueError:
            pass

    def test_state_info_answers(self):
        try:
            import server
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"server.py doesn't import here: {e}")
        from unittest import mock
        with mock.patch.object(server, "OSYS", winsys), mock.patch.object(server.quick, "projects", lambda: {}):
            st = self.answer(server.state_info)
        self.assertIn("voice_native", st)


if __name__ == "__main__":
    unittest.main()


STUCK_QUIT = """
import atexit, os, signal, sys, threading, time
sys.path[:0] = sys.argv[1:3]
from test_audio_devices import FakeSD
import winsys
sd = sys.modules["sounddevice"] = FakeSD()
winsys.LOCK_WAIT = 0.05
atexit.register(lambda: print("last", flush=True))     # (registered first, runs last)
winsys._PA.hooked = True
atexit.register(winsys.stop)
def bye(*_):
    raise SystemExit(0)
signal.signal(signal.SIGTERM, bye)
inside = threading.Event()
def make():
    inside.set()
    time.sleep(60)
owner = type("O", (), {"first": threading.Event(), "stream": None})()
threading.Thread(target=lambda: winsys._open(sd, owner, make), daemon=True).start()
inside.wait(5)
os.kill(os.getpid(), signal.SIGTERM)
time.sleep(30)
"""

STUCK_QUIT_REAL_HANDLER = STUCK_QUIT.replace(
    "sd = sys.modules[\"sounddevice\"] = FakeSD()",
    "sd = sys.modules[\"sounddevice\"] = FakeSD()\n"
    "sd._exit_handler = lambda: print(\"pa_terminate\", flush=True)   # (as sounddevice's, registered at its import)\n"
    "atexit.register(sd._exit_handler)")
