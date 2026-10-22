"""Windows versions of the parts of Void that talk to the operating system: microphone, speakers, screenshots,
clipboard, notifications, volume / media keys / brightness, opening apps, locking, start at login.

On Linux, server.py and quick.py use command-line tools for these (parecord, paplay, grim, wl-paste, pactl, …);
everything else in Void is the same on both.
"""
import atexit
import contextlib
import ctypes
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

NO_WINDOW = 0x08000000          # CREATE_NO_WINDOW: helper programs never flash a console window
DETACHED = 0x00000008 | 0x00000200 | NO_WINDOW    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def ps(script, timeout=15):
    """Run a PowerShell snippet quietly → (ok, output)."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                            script], capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
        return r.returncode == 0, (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def _q(s):
    """Quote for PowerShell single-quoted strings."""
    return "'" + str(s).replace("'", "''") + "'"


# ---------------------------------------------------------------- where things live
def data_dir():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "void"


def config_dir():
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "void"


def cache_dir():
    return data_dir() / "cache"


def dpi_aware():
    """Screen coordinates in real pixels (otherwise Windows scaling would shrink screenshots / window positions)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- PortAudio's device list
# PortAudio reads the list of speakers and mics once, when it starts, and keeps opening streams on what it read. After a
# USB DAC is switched off and on, a sleep/wake, or a new default (an iPhone's mic turning up), that's a device that isn't
# there any more: he goes quiet, with no error, until Void restarts. So PortAudio starts over before a stream opens when
# the system's devices have changed since it last looked, or a stream has died or never ran — and never with one open.
devices = None      # () → anything that changes when the default output / input or the device list does (macsys sets
#                     it: Core Audio's own). None: can't tell (Windows), so only a dead stream starts PortAudio over
STALL = 1.5         # seconds without a callback: that stream's device has gone
LOCK_WAIT = 0.25    # how long a question about the devices waits for _PA.lock (a stream's open may sit on a permission prompt)
FIRST = 1.0         # how long a new stream gets to call back the first time


class _PA:
    lock = threading.RLock()        # (held while a stream opens or closes, so PortAudio never restarts under one)
    live = set()                    # the Players and Mics open now
    sig = None                      # devices() when PortAudio last started
    stale = False                   # a stream died or never ran: start over before the next one opens
    done = False                    # Void is quitting: PortAudio has stopped for good, nothing opens again
    hooked = False                  # stop() is registered to run at exit
    mics = None                     # the last mic list list_mics() read (what it answers while an open holds the lock)
    plays = True                    # the last answer of can_play()

    @classmethod
    @contextlib.contextmanager
    def brief(cls):
        """Holds the lock for the block, or yields False after LOCK_WAIT without it: a stream open can sit on macOS's
        microphone permission prompt for as long as that's on screen, and nothing that only asks may wait behind it."""
        got = cls.lock.acquire(timeout=LOCK_WAIT)
        try:
            yield got
        finally:
            if got:
                cls.lock.release()


def stop():
    """Void is quitting: close every stream and stop PortAudio, for good. Runs at exit ahead of sounddevice's own
    clean-up — otherwise the wake word sees its mic die under that, opens a new one on a PortAudio started over, and its
    callback then comes in while Python is tearing itself down: it can't get Python back, and macOS reports a crash."""
    import sys
    _PA.done = True                                        # (set first, lock or not: nothing opens after this)
    with _PA.brief() as got:
        if not got:                                        # a stream's open is stuck on the microphone permission
            sd = sys.modules.get("sounddevice")            # prompt: quitting can't wait for it (the process exits anyway),
            if sd is not None and hasattr(sd, "_exit_handler"):
                atexit.unregister(sd._exit_handler)        # nor may sounddevice's own clean-up Pa_Terminate beside it
            return
        for s in list(_PA.live):
            s.kill()
        sd = sys.modules.get("sounddevice")
        try:
            while sd is not None and sd._initialized > 0:
                sd._terminate()
        except Exception:  # noqa: BLE001
            pass


def _sig():
    try:
        return devices() if devices else None
    except Exception:  # noqa: BLE001
        return _PA.sig                                     # (can't ask: call it unchanged)


def _fresh(sd, close=True, force=False):
    """Start PortAudio over if it's out of date. Anything still open is closed first (its owner sees it end and opens
    a new one, on the new list) — or, close=False, left alone and PortAudio left as it is for now."""
    with _PA.lock:
        if _PA.done:
            raise sd.PortAudioError("PortAudio has stopped: Void is quitting")
        if not _PA.hooked:                                 # (sounddevice is imported by now, its clean-up registered:
            _PA.hooked = True                              # this one, registered after, runs before it)
            atexit.register(stop)
        sig = _sig()
        if not (force or _PA.stale or (sig is not None and sig != _PA.sig)) or (_PA.live and not close):
            return
        for s in list(_PA.live):
            s.kill()
        try:
            sd._terminate()
        except Exception:  # noqa: BLE001
            pass                                           # (it wasn't running: an earlier restart failed halfway)
        sd._initialize()
        _PA.sig, _PA.stale = sig, False


def _shut(stream):
    try:
        stream.abort()
        stream.close()
    except Exception:  # noqa: BLE001
        pass


def _open(sd, owner, make):
    """make() → owner's stream, started on an up-to-date PortAudio. One that won't start, or starts but never calls back
    (its device has gone), gets one more go on a fresh PortAudio."""
    with _PA.lock:
        for again in (False, True):
            _fresh(sd, force=again)
            owner.first.clear()
            s = None
            try:
                s = make()
                s.start()
            except sd.PortAudioError:
                _shut(s)
                if again:
                    _PA.stale = True
                    raise
                continue
            except Exception:  # noqa: BLE001
                _shut(s)
                raise                                      # (no such device, say: a fresh list won't help)
            if owner.first.wait(FIRST):
                break
            if again:
                _PA.stale = True                           # (still silent: hand it over anyway, try again next open)
                break
            _shut(s)
        owner.stream = s
        _PA.live.add(owner)


def _ok(owner):
    """Its stream is still running and called back lately. If not, its device has gone: PortAudio starts over before
    the next stream opens."""
    if not owner.alive:
        return False
    try:
        ok = owner.stream.active and time.monotonic() - owner.ran < STALL
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        _PA.stale = True
    return ok


# ---------------------------------------------------------------- microphone
class ChannelPick:
    """Two-channel mics: keep the channel your voice is on. Some headsets send it on the second channel only and the
    first is just noise; recording channel 1 (what a mono stream gets) then hears nothing. Switches to the other channel
    only when it's clearly louder for half a second, so a single bump doesn't flip it."""
    SWITCH_BLOCKS = 25                               # 25 × 20 ms

    def __init__(self):
        self.ch, self.e, self.since, self.switched = 0, [0.0, 0.0], 0, False

    def __call__(self, data):
        import numpy as np
        a = np.frombuffer(data, dtype=np.int16).reshape(-1, 2).astype(np.int32)
        for c in (0, 1):
            self.e[c] = self.e[c] * 0.9 + 0.1 * float(np.abs(a[:, c]).mean())
        other = 1 - self.ch
        self.since = self.since + 1 if self.e[other] > 2 * self.e[self.ch] + 30 else 0
        if self.since >= self.SWITCH_BLOCKS:
            self.ch, self.since, self.switched = other, 0, True
            import sys
            print(f"[void] mic: recording channel {self.ch + 1} of 2 (the other one is near silent)", file=sys.stderr, flush=True)
        return np.ascontiguousarray(a[:, self.ch], dtype=np.int16).tobytes()


class Mic:
    """Records 16-bit mono at `rate`. Same shape as the Linux recorder: read(timeout) → bytes, close().
    keep_raw: also keep what came in on every channel (`raw`, interleaved), for `void doctor`."""

    def __init__(self, rate, device=None, keep_raw=False):
        import sounddevice as sd
        self.q, self.raw = queue.Queue(), bytearray() if keep_raw else None
        self.pick, self.alive, self.ran, self.first = None, True, 0.0, threading.Event()

        def cb(data, frames, t, status):
            self.ran = time.monotonic()
            b = bytes(data)
            if self.raw is not None:
                self.raw += b
            self.q.put(self.pick(b) if self.pick else b)
            if not self.first.is_set():
                self.first.set()

        def make():                                        # (after any restart: device numbers change with it)
            dev = _mic_index(sd, (device or "").strip())
            try:
                info = sd.query_devices(dev, "input") if dev is not None and not isinstance(dev, str) else sd.query_devices(kind="input")
                self.info = {"name": info["name"], "driver": sd.query_hostapis(info["hostapi"])["name"],
                             "channels": int(info["max_input_channels"]), "rate": int(info["default_samplerate"])}
            except Exception:  # noqa: BLE001
                self.info = {"name": str(device or "default"), "driver": "?", "channels": 1, "rate": 0}
            for ch in ((2, 1) if self.info["channels"] >= 2 else (1,)):
                self.pick, self.channels = ChannelPick() if ch == 2 else None, ch
                try:
                    return sd.RawInputStream(samplerate=rate, channels=ch, dtype="int16", blocksize=int(rate * 0.02),
                                             device=dev, callback=cb)
                except Exception:  # noqa: BLE001
                    if ch == 1:
                        raise                              # (2 channels refused: try mono, exactly as before)
        _open(sd, self, make)
        self.name = "sounddevice"
        Mic.last = self                                    # (for diagnostics: how the last recording was made)

    @property
    def channel(self):
        """The channel being recorded (1-based) and whether it switched to it because the other was near silent."""
        return (self.pick.ch + 1, self.pick.switched) if self.pick else (1, False)

    def read(self, timeout):
        if not _ok(self):
            raise EOFError                                 # (closed under it, or its mic has gone: open a new one)
        out = bytearray()
        try:
            out += self.q.get(timeout=timeout)
            while True:
                out += self.q.get_nowait()
        except queue.Empty:
            pass
        return bytes(out)

    def close(self):
        with _PA.lock:
            if not self.alive:
                return
            self.alive = False
            _PA.live.discard(self)
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:  # noqa: BLE001
                pass

    kill = close

    def err(self):
        return ""


def _mic_index(sd, name):
    """mic_device → one device number. Windows lists each mic once per driver (MME, DirectSound, WASAPI…), so a name
    alone is ambiguous and sounddevice refuses it; prefer the default driver's copy (the list Settings shows)."""
    if not name:
        return None
    if name.isdigit():
        return int(name)
    want = name.lower()
    api = sd.query_devices(kind="input")["hostapi"]
    hits = [(i, d) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0 and want in d["name"].lower()]
    if not hits:
        return name                                       # let sounddevice say it can't find it
    hits.sort(key=lambda h: (h[1]["hostapi"] != api, h[1]["name"].lower() != want))
    return hits[0][0]


def list_mics():
    """Inputs for Settings → Voice (the id is the device name; any part of it works)."""
    try:
        import sounddevice as sd
        with _PA.brief() as got:
            if not got:                                    # (a stream is opening, maybe stuck on the permission prompt)
                return list(_PA.mics or [])
            _fresh(sd, close=False)                        # (a fresh list if nothing's open: a mic may have come)
            api = sd.query_devices(kind="input")["hostapi"]  # just the default driver's list (no MME/WASAPI repeats)
            _PA.mics = [{"id": d["name"], "label": d["name"]} for d in sd.query_devices()
                        if d["max_input_channels"] > 0 and d["hostapi"] == api]
            return list(_PA.mics)
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------- speakers
class Player:
    """Looks like a `paplay` process to the Speaker: .stdin.write(raw 16-bit mono), .poll(), .kill(). Playback runs on
    sounddevice's own audio thread; the Speaker keeps feeding it ~0.8 s ahead, exactly as on Linux. If its speakers go
    (or another output becomes the default) it ends, like a paplay that died, and the Speaker opens a new one."""

    def __init__(self, rate):
        import sounddevice as sd
        self.buf, self.lock, self.alive = bytearray(), threading.Lock(), True
        self.ran, self.first = 0.0, threading.Event()

        def cb(outdata, frames, t, status):
            self.ran = time.monotonic()
            n = frames * 2
            with self.lock:
                chunk = bytes(self.buf[:n])
                del self.buf[:n]
            outdata[:len(chunk)] = chunk
            if len(chunk) < n:
                outdata[len(chunk):] = b"\0" * (n - len(chunk))
            if not self.first.is_set():
                self.first.set()

        _open(sd, self, lambda: sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16", latency=0.1, callback=cb))
        self.stdin = self

    def write(self, data):
        if not _ok(self):
            self.kill()
            raise BrokenPipeError("the speakers have gone")
        with self.lock:
            self.buf += data

    def flush(self):
        pass

    def poll(self):
        """Checked before each sentence: the devices are asked again here (not in between), so it's cheap."""
        if self.alive and (not _ok(self) or _sig() != _PA.sig):
            self.kill()
        return None if self.alive else 0

    def kill(self):
        with _PA.lock:
            if not self.alive:
                return
            self.alive = False
            _PA.live.discard(self)
            _shut(self.stream)


def can_play():
    try:
        import sounddevice as sd
        with _PA.brief() as got:
            if not got:
                return _PA.plays                           # (as last answered: a stream is opening)
            _fresh(sd, close=False)
            sd.query_devices(kind="output")
            _PA.plays = True
            return True
    except Exception:  # noqa: BLE001
        _PA.plays = False
        return False


# ---------------------------------------------------------------- screenshots & clipboard
def active_window():
    """(x, y, w, h) of the window you're using, in screen pixels — None if there isn't one (or it's Void)."""
    from ctypes import wintypes
    u = ctypes.windll.user32
    h = u.GetForegroundWindow()
    if not h:
        return None
    title = ctypes.create_unicode_buffer(256)
    u.GetWindowTextW(h, title, 256)
    if title.value == "Void":
        return None
    r = wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(h, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:   # frame w/o shadow
        u.GetWindowRect(h, ctypes.byref(r))
    w, ht = r.right - r.left, r.bottom - r.top
    return (r.left, r.top, w, ht) if w > 20 and ht > 20 else None


def _clip_image():
    from PIL import Image, ImageGrab
    img = ImageGrab.grabclipboard()
    return img if isinstance(img, Image.Image) else None


def screenshot(kind, out):
    """kind: window | screen | region → saves a PNG at `out`, returns the kind it really got."""
    from PIL import ImageGrab
    if kind == "region":                              # Windows' own snipping tool → the clipboard
        before = _clip_image()
        os.startfile("ms-screenclip:")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 90:
            time.sleep(0.4)
            img = _clip_image()
            if img is not None and (before is None or img.tobytes()[:4096] != before.tobytes()[:4096]):
                img.save(out)
                return "region"
        raise RuntimeError("cancelled")
    box = None
    if kind == "window":
        g = active_window()
        if g:
            box = (g[0], g[1], g[0] + g[2], g[1] + g[3])
    ImageGrab.grab(bbox=box, all_screens=box is None).save(out)
    return "window" if box else "screen"


def clipboard(out):
    """→ ("image", path) or ("text", text)."""
    img = _clip_image()
    if img is not None:
        img.save(out)
        return "image", str(out)
    ok, text = ps("Get-Clipboard -Raw")
    return "text", text if ok else ""


def set_clipboard(text):
    """Put text on the clipboard (works when the window isn't focused, unlike the web clipboard) → ok."""
    return ps("Set-Clipboard -Value " + _q(text))[0]


# ---------------------------------------------------------------- notifications
def notify(title, body):
    """A Windows notification (toast), shown through PowerShell so nothing extra needs installing."""
    from xml.sax.saxutils import escape
    xml = (f"<toast><visual><binding template='ToastGeneric'><text>{escape(title)}</text>"
           f"<text>{escape(body[:300])}</text></binding></visual></toast>")
    script = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]"
              " > $null; [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] > $null;"
              f" $x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml({_q(xml)});"
              " $id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
              " [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show("
              "[Windows.UI.Notifications.ToastNotification]::new($x))")
    threading.Thread(target=ps, args=(script,), daemon=True).start()


# ---------------------------------------------------------------- shortcuts & start at login
def shortcut(path, target, args="", icon=None, workdir=None):
    """Create a .lnk (Start menu, Startup folder)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_q(path)}); $s.TargetPath = {_q(target)};"
              f" $s.Arguments = {_q(args)}; $s.WorkingDirectory = {_q(workdir or Path(target).parent)};"
              + (f" $s.IconLocation = {_q(icon)};" if icon else "") + " $s.Save()")
    return ps(script)[0]


def startup_link():
    return Path(os.environ.get("APPDATA") or "") / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Void.lnk"


# ---------------------------------------------------------------- volume, media keys, brightness, lock, apps
# Windows' audio controls are COM objects: they must be made, used and released on one thread. Released on another
# (Python frees them wherever the last reference dies) they can crash the whole server with no traceback, so every
# volume / mic-level call runs on this one thread and only plain values come back.
_com_jobs, _com_lock = None, threading.Lock()


def _com_loop():
    import gc
    import comtypes
    comtypes.CoInitialize()
    while True:
        fn, args, done = _com_jobs.get()
        try:
            out = (True, fn(*args))
        except Exception as e:  # noqa: BLE001
            out = (False, RuntimeError(str(e)))       # a fresh error: the original's traceback holds COM objects
        # pycaw's objects reference each other, so some only go when the garbage collector runs: run it here, now,
        # before anyone else's collection can free them on the wrong thread
        fn = args = None
        gc.collect()
        done.put(out)


def _on_com_thread(fn, *args):
    global _com_jobs
    with _com_lock:
        if _com_jobs is None:
            _com_jobs = queue.Queue()
            threading.Thread(target=_com_loop, daemon=True, name="void-com").start()
    done = queue.Queue(maxsize=1)
    _com_jobs.put((fn, args, done))
    ok, val = done.get(timeout=15)
    if not ok:
        raise val
    return val


def _endpoint_volume():
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    spk = AudioUtilities.GetSpeakers()
    if hasattr(spk, "EndpointVolume"):                         # newer pycaw
        return spk.EndpointVolume
    from comtypes import CLSCTX_ALL
    return spk.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)  # (not a cast)


def volume_get():
    """→ (percent, muted) or (None, False)."""
    def get():
        v = _endpoint_volume()
        return round(v.GetMasterVolumeLevelScalar() * 100), bool(v.GetMute())
    try:
        return _on_com_thread(get)
    except Exception:  # noqa: BLE001
        return None, False


def _mic_endpoint(hint=""):
    """The Windows volume control of the mic Void records from: the one whose name matches mic_device (sounddevice
    cuts names to 31 characters, so it may be just the start), else the default recording device. COM thread only."""
    import comtypes
    from comtypes import CLSCTX_ALL
    from pycaw.constants import CLSID_MMDeviceEnumerator
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, IMMDeviceEnumerator
    en = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
    dev, hint = None, (hint or "").strip().lower()
    if hint and not hint.isdigit():
        import warnings
        with warnings.catch_warnings():                        # pycaw warns for every property some drivers lack
            warnings.simplefilter("ignore")
            devices = AudioUtilities.GetAllDevices()
        for d in devices:
            if (d.id or "").startswith("{0.0.1.") and str(d.state).endswith("Active") and hint in (d.FriendlyName or "").lower():
                dev = en.GetDevice(d.id)                       # {0.0.1.…} = a recording device
                break
    dev = dev or en.GetDefaultAudioEndpoint(1, 0)              # eCapture, eConsole
    # QueryInterface, not ctypes.cast: a cast makes a second pointer without a reference of its own, so the object is
    # released twice and the process eventually crashes
    return dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)


def mic_level(hint=""):
    """→ the mic's Windows level, 0.0–1.0, or None."""
    try:
        return _on_com_thread(lambda: float(_mic_endpoint(hint).GetMasterVolumeLevelScalar()))
    except Exception:  # noqa: BLE001
        return None


def set_mic_level(level, hint=""):
    try:
        _on_com_thread(lambda: _mic_endpoint(hint).SetMasterVolumeLevelScalar(max(0.0, min(1.0, level)), None))
        return True
    except Exception:  # noqa: BLE001
        return False


def _key(vk, times=1):
    u = ctypes.windll.user32
    for _ in range(times):
        u.keybd_event(vk, 0, 0, 0)
        u.keybd_event(vk, 0, 2, 0)          # KEYEVENTF_KEYUP


def volume_set(op, val=None):
    """op: set | up | down | mute | unmute. Uses the volume control itself, or the volume keys if that's unavailable."""
    def change():
        v = _endpoint_volume()
        if op == "set":
            v.SetMasterVolumeLevelScalar(max(0, min(100, val)) / 100, None)
        elif op in ("up", "down"):
            cur = v.GetMasterVolumeLevelScalar() * 100
            v.SetMasterVolumeLevelScalar(max(0, min(100, cur + (val or 10) * (1 if op == "up" else -1))) / 100, None)
        else:
            v.SetMute(op == "mute", None)
    try:
        _on_com_thread(change)
        return True
    except Exception:  # noqa: BLE001
        if op in ("up", "down"):
            _key(0xAF if op == "up" else 0xAE, max(1, (val or 10) // 2))   # each key press is 2%
            return True
        if op in ("mute", "unmute"):
            _key(0xAD)
            return True
        return False


def media(op):
    """op: pause | play | toggle | next | previous — the media keys, which every Windows player listens to."""
    _key({"pause": 0xB3, "play": 0xB3, "toggle": 0xB3, "next": 0xB0, "previous": 0xB1}[op])
    return True


def brightness(op, val=None):
    """Laptop screens (WMI). → new percent or None."""
    ok, cur = ps("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness")
    if not ok or not cur.strip().split()[0:1]:
        return None
    now = int(cur.split()[0])
    new = val if op == "set" else now + (val or 10) * (1 if op == "up" else -1)
    new = max(0, min(100, int(new)))
    ok, _ = ps(f"(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)"
               f" | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={new}}}")
    return new if ok else None


def lock():
    return bool(ctypes.windll.user32.LockWorkStation())


_apps_cache = {"t": 0, "apps": []}


def _start_apps():
    """Everything in the Start menu (normal and Store apps) → [{"Name", "AppID"}]."""
    if time.monotonic() - _apps_cache["t"] > 300:
        ok, out = ps("Get-StartApps | ConvertTo-Json -Compress", timeout=20)
        try:
            apps = json.loads(out) if ok and out else []
            _apps_cache.update(t=time.monotonic(), apps=apps if isinstance(apps, list) else [apps])
        except ValueError:
            pass
    return _apps_cache["apps"]


def open_app(name):
    """Start an app by (part of) its Start-menu name → its proper name, or None if nothing matches."""
    name = name.lower().strip(" .")
    best = None
    for a in _start_apps():
        nm = str(a.get("Name") or "")
        low = nm.lower()
        score = 3 if low == name else 2 if low.startswith(name) or name in low.split() else 1 if name in low else 0
        if score and (not best or score > best[0]):
            best = (score, a)
    if not best:
        return None
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{best[1]['AppID']}"], creationflags=NO_WINDOW)
    return best[1]["Name"]


def gpu_ok():
    """An NVIDIA card with drivers (for fast speech recognition)."""
    from shutil import which
    return which("nvidia-smi") is not None


def add_cuda_dirs():
    """faster-whisper on the GPU needs cuBLAS/cuDNN: the installer puts them in the venv (pip nvidia-* wheels)."""
    import glob
    import site
    for r in site.getsitepackages():
        for d in glob.glob(os.path.join(r, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            except OSError:
                pass
