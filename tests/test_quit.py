"""Quitting Void.app stops the server the app started (and that server's children), and never one it didn't start,
told apart by the run id in runtime.json and /api/state.

    python3 -m unittest discover -s tests
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "app"))

# a stand-in for app/server.py with a child of its own (like the multiprocessing resource tracker). VOID_RUN_ID, when
# set, is its run id (as in the real one).
FAKE_SERVER = r'''
import json, os, secrets, signal, subprocess, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
PORT = int(os.environ["FAKE_PORT"])
if "--print-port" in sys.argv:
    print(PORT); sys.exit()
signal.alarm(40)
data = os.path.join(os.environ["XDG_DATA_HOME"], "void")
rid = os.environ.get("VOID_RUN_ID") or secrets.token_hex(8)
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"version": "0", "id": rid}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
os.makedirs(data, exist_ok=True)
kid = subprocess.Popen(["sleep", "40"])
open(os.path.join(data, "server.pid"), "w").write(str(os.getpid()))
open(os.path.join(data, "child.pid"), "w").write(str(kid.pid))
json.dump({"port": PORT, "token": "t", "tool_token": "t", "id": rid, "pid": os.getpid()}, open(os.path.join(data, "runtime.json"), "w"))
srv.serve_forever()
'''
# the window of the fake app: the real window_win.py's command-line bits are run for "--" arguments, anything else is
# only noted (no Qt window in a test)
FAKE_WINDOW = r'''
import os, runpy, sys
here = os.path.dirname(os.path.abspath(__file__))
if sys.argv[1:2] and sys.argv[1].startswith("--"):
    sys.argv[0] = os.path.join(here, "real_window_win.py")
    runpy.run_path(sys.argv[0], run_name="__main__")
else:
    open(os.path.join(os.environ["XDG_DATA_HOME"], "window.args"), "w").write(" ".join(sys.argv[1:]))
'''


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:                                                   # (a zombie nobody reaped yet is gone too)
        return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()[:1] not in ("", "Z")
    except OSError:
        return True


def wait_for(cond, secs=8):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return cond()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.port = free_port()
        self.pids = []
        self.addCleanup(self.reap)

    def reap(self):
        for pid in self.pids:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def env(self, home, **more):
        return {"HOME": str(home), "XDG_DATA_HOME": str(home / "data"), "PATH": "/usr/bin:/bin",
                "FAKE_PORT": str(self.port), **more}

    def fake_app(self, app):
        app.mkdir(parents=True)
        (app / "server.py").write_text(FAKE_SERVER)
        (app / "window_win.py").write_text(FAKE_WINDOW)
        for f in ("window_win.py", "macsys.py", "winsys.py"):
            if (ROOT / "app" / f).exists():
                shutil.copy(ROOT / "app" / f, app / ("real_" + f if f == "window_win.py" else f))

    def pidfiles(self, home):
        d = home / "data/void"
        out = []
        for n in ("server.pid", "child.pid"):
            if (d / n).exists():
                out.append(int((d / n).read_text()))
        self.pids += out
        return out

    def foreign_server(self):
        """Another Void (its own data folder) serving on our port: its pids, which must survive."""
        home = self.tmp / "other"
        self.fake_app(home / "app")
        p = subprocess.Popen([sys.executable, str(home / "app/server.py")], env=self.env(home),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.pids.append(p.pid)
        self.assertTrue(wait_for(lambda: (home / "data/void/child.pid").exists()), "the foreign server didn't start")
        return self.pidfiles(home)


class LauncherQuit(Base):
    def bundle(self):
        c = self.tmp / "Void.app" / "Contents"
        (c / "MacOS").mkdir(parents=True)
        shutil.copy(ROOT / "packaging/macos/launcher", c / "MacOS/Void")
        (c / "Resources/python/bin").mkdir(parents=True)
        (c / "Resources/python/bin/python3").symlink_to(sys.executable)
        self.fake_app(c / "Resources/app")
        return c / "MacOS/Void"

    def run_launcher(self, launcher, cmd):
        return subprocess.run([str(launcher), cmd], env=self.env(self.tmp / "home"), capture_output=True, text=True,
                              timeout=30)

    def test_quit_stops_the_server_it_started_and_its_children(self):
        launcher = self.bundle()
        r = self.run_launcher(launcher, "show")
        self.assertEqual(r.returncode, 0, r.stderr)
        server, child = self.pidfiles(self.tmp / "home")
        self.assertTrue(alive(server) and alive(child))
        r = self.run_launcher(launcher, "quit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(wait_for(lambda: not alive(server)), "the server it started is still running")
        self.assertTrue(wait_for(lambda: not alive(child)), "the server's child is still running")

    def test_quit_leaves_a_server_it_did_not_start(self):
        launcher = self.bundle()
        r = self.run_launcher(launcher, "show")
        self.assertEqual(r.returncode, 0, r.stderr)
        mine = self.pidfiles(self.tmp / "home")
        for pid in mine:                                   # ours is gone (crashed); another Void now holds the port
            os.killpg(pid, signal.SIGKILL) if pid == mine[0] else None
        self.assertTrue(wait_for(lambda: not alive(mine[0])))
        shutil.rmtree(self.tmp / "home/data/void/runtime.json", True)
        (self.tmp / "home/data/void/runtime.json").unlink(missing_ok=True)
        theirs = self.foreign_server()
        shutil.copy(self.tmp / "other/data/void/runtime.json", self.tmp / "home/data/void/runtime.json")
        self.run_launcher(launcher, "quit")
        time.sleep(1)
        self.assertTrue(all(alive(p) for p in theirs), "it stopped a server it didn't start")


class WindowKeepsTheRecord(Base):
    """window_win.Started: the one record of the server the window started."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            global window_win
            import window_win  # noqa: E402
        except ImportError:
            raise unittest.SkipTest("no Qt here")

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        (self.home / "data/void").mkdir(parents=True)
        p = mock.patch.object(window_win, "DATA", self.home / "data/void")
        p.start()
        self.addCleanup(p.stop)

    def start(self, rid, home=None):
        home = home or self.home
        self.fake_app(home / "app")
        p = subprocess.Popen([sys.executable, str(home / "app/server.py")],
                             env=self.env(home, VOID_RUN_ID=rid), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        self.pids.append(p.pid)
        self.assertTrue(wait_for(lambda: (home / "data/void/child.pid").exists()))
        return self.pidfiles(home)

    def test_stop_ends_the_recorded_server_and_its_children(self):
        server, child = self.start("aaaa")
        window_win.Started.record(server, "aaaa")
        window_win.Started.stop()
        self.assertTrue(wait_for(lambda: not alive(server) and not alive(child)))

    def test_stop_leaves_a_server_with_another_run_id(self):
        server, child = self.start("bbbb")
        window_win.Started.record(server, "aaaa")          # what we started was replaced by someone else's
        window_win.Started.stop()
        time.sleep(1)
        self.assertTrue(alive(server) and alive(child))

    def test_stop_leaves_a_foreign_process_when_the_recorded_pid_is_not_the_servers(self):
        server, child = self.start("eeee")                 # the winner of a launch race
        # the loser died at bind, so the launcher recorded its $! with the winner's run id; its pid has since been
        # reused by someone else's process group
        foreign = subprocess.Popen(["sleep", "40"], start_new_session=True)
        self.pids.append(foreign.pid)
        window_win.Started.record(foreign.pid, "eeee")
        window_win.Started.stop()
        time.sleep(1)
        self.assertTrue(alive(foreign.pid) and alive(server) and alive(child))

    def test_stop_without_a_record_stops_nothing(self):
        server, child = self.start("cccc")
        window_win.Started.stop()
        time.sleep(1)
        self.assertTrue(alive(server) and alive(child))

    def test_the_windows_restart_is_what_gets_recorded(self):
        self.fake_app(self.home / "app")
        me = mock.Mock(spec=window_win.VoidWindow)
        with mock.patch.object(window_win, "APP", self.home / "app"), \
                mock.patch.dict(os.environ, self.env(self.home)):
            window_win.VoidWindow._start_server(me)
        self.assertTrue(wait_for(lambda: (self.home / "data/void/child.pid").exists()))
        server, child = self.pidfiles(self.home)
        window_win.Started.stop()
        self.assertTrue(wait_for(lambda: not alive(server) and not alive(child)))

    def test_quitting_the_app_stops_it(self):
        server, child = self.start("dddd")
        window_win.Started.record(server, "dddd")
        me = mock.Mock(spec=window_win.VoidWindow)
        with mock.patch.object(window_win.QApplication, "quit"):
            window_win.VoidWindow._remote_cmd(me, "quit")
        self.assertTrue(wait_for(lambda: not alive(server)), "quit left the server running")


if __name__ == "__main__":
    unittest.main()
