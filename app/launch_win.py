"""`void` on Windows (void.cmd and the Start-menu shortcut run this). Starts the server and his window in the background
if they aren't running, then passes the command on — the same commands as the Linux launcher:

    void            show / hide him            void see [screen]  print a picture of your window (for Claude)
    void listen     pop up and listen          void doctor        check mic, voice and Claude Code
    void diagnostics  a report to paste when asking for help (nothing is sent)
    void look       screenshot, then listen    void stress        test his voice for stutters
    void live       live view on/off           void log           follow the server log
    void hover      hover mode on/off          void stop | restart
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
import winsys  # noqa: E402
for _s in (sys.stdout, sys.stderr):               # logs go to a file: write UTF-8, not the old Windows code page
    if _s is not None and hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

os.environ["PYTHONUTF8"] = "1"                   # everything Void starts reads/writes its files as UTF-8
DATA = winsys.data_dir()
LOG, PIDS = DATA / "server.log", DATA / "pids.json"
PY = Path(sys.executable).with_name("python.exe")
PYW = Path(sys.executable).with_name("pythonw.exe")
if not PYW.exists():
    PYW = PY


def port():
    try:
        return int(json.loads((winsys.config_dir() / "config.json").read_text(encoding="utf-8-sig")).get("port", 47777))
    except (OSError, ValueError):
        return 47777


URL = f"http://127.0.0.1:{port()}/"


def up():
    """OUR server answers: it knows the token in our runtime.json (another Void on the same port doesn't, so we never
    attach to it)."""
    try:
        tok = json.loads((DATA / "runtime.json").read_text())["token"]
        req = urllib.request.Request(URL + "api/state", headers={"X-Void-Token": tok})
        return "cwd" in json.load(urllib.request.urlopen(req, timeout=1))
    except Exception:  # noqa: BLE001
        return False


def _pids():
    try:
        return json.loads(PIDS.read_text())
    except (OSError, ValueError):
        return {}


def _remember(name, pid):
    p = _pids()
    p[name] = pid
    DATA.mkdir(parents=True, exist_ok=True)
    PIDS.write_text(json.dumps(p))


def _start(name, args):
    """Start a background part of Void with no console window; its output goes to the log."""
    DATA.mkdir(parents=True, exist_ok=True)
    out = open(LOG, "ab")
    p = subprocess.Popen([str(PYW), *args], stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=winsys.DETACHED, close_fds=True, cwd=str(APP))
    _remember(name, p.pid)


def stop():
    import window_win
    window_win.send("quit")
    for name, pid in _pids().items():
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, creationflags=winsys.NO_WINDOW)
    PIDS.unlink(missing_ok=True)


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "toggle"
    if cmd in ("doctor", "diagnostics", "stress", "setup", "server"):   # these run here, in the console
        flag = {"doctor": ["--doctor"], "diagnostics": ["--diagnostics"], "stress": ["--stress-voice"], "server": []}.get(cmd)
        if flag is None:
            print("Run install.ps1 again to update Void's voice and speech models.")
            return 0
        return subprocess.call([str(PY), str(APP / "server.py"), *flag, *args[1:]])
    if cmd == "see":
        return subprocess.call([str(PY), str(APP / "server.py"), "--see", args[1] if len(args) > 1 else "window"])
    if cmd == "log":
        LOG.touch()
        with open(LOG, encoding="utf-8", errors="replace") as f:
            print("".join(f.readlines()[-50:]), end="")
            while True:                                          # follow it, like tail -f
                line = f.readline()
                if line:
                    print(line, end="", flush=True)
                else:
                    time.sleep(0.3)
    if cmd == "stop":
        stop()
        print("Void stopped")
        return 0
    if cmd == "restart":
        stop()
        time.sleep(0.6)
        cmd = "show"
    if cmd == "render":
        print("render modes are a Linux thing — on Windows he always draws with Chromium")
        return 0
    if cmd not in ("toggle", "show", "hide", "quit", "listen", "look", "live", "hover", "hover-on", "hover-off",
                   "ptt-down", "ptt-up"):
        print(__doc__)
        return 2
    if not up():
        _start("server", [str(APP / "server.py")])
        for _ in range(80):
            if up():
                break
            time.sleep(0.1)
        else:
            print(f"the server didn't start — see {LOG}", file=sys.stderr)
            return 1
    import window_win
    if window_win.send(cmd):
        return 0
    if cmd in ("hide", "quit", "ptt-up"):
        return 0
    first = {"toggle": "show", "ptt-down": "listen"}.get(cmd, cmd)      # (not open yet: a push-to-talk press just listens)
    _start("window", [str(APP / "window_win.py"), URL + "?native=1", first])
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
