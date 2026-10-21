"""The launchers start their own server and never attach to another Void's that happens to hold their port.

    python3 -m unittest discover tests
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "app"))
import launch_win  # noqa: E402

# a stand-in for app/server.py: the same port rule, runtime.json and /api/state answers, nothing else
FAKE_SERVER = r'''
import json, os, secrets, signal, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
PORT = int(os.environ["FAKE_PORT"])
if "--print-port" in sys.argv:
    print(PORT); sys.exit()
signal.alarm(30)                                   # never outlives the test, whatever happens to it
data = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "void")
tok = secrets.token_urlsafe(24)

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        mine = self.headers.get("X-Void-Token") == tok
        body = json.dumps({"version": "0", "cwd": "/"} if mine else {"version": "0"}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass

srv = HTTPServer(("127.0.0.1", PORT), H)            # (port taken → dies here, like the real one)
os.makedirs(data, exist_ok=True)
open(os.path.join(data, "server.pid"), "w").write(str(os.getpid()))
json.dump({"port": PORT, "token": tok, "tool_token": "t"}, open(os.path.join(data, "runtime.json"), "w"))
srv.serve_forever()
'''
FAKE_WINDOW = r'''
import os, sys
open(os.path.join(os.environ["XDG_DATA_HOME"], "window.args"), "w").write(" ".join(sys.argv[1:]))
'''


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Launchers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.port = free_port()
        self.pids = []
        self.addCleanup(self.kill_servers)

    def kill_servers(self):
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def env(self, home):
        return {"HOME": str(home), "XDG_DATA_HOME": str(home / "data"), "PATH": "/usr/bin:/bin",
                "FAKE_PORT": str(self.port)}

    def fake_app(self, app):
        app.mkdir(parents=True)
        (app / "server.py").write_text(FAKE_SERVER)
        (app / "window_win.py").write_text(FAKE_WINDOW)

    def mac_bundle(self):
        """A Void.app with the real launcher, a link to this python and the fake app."""
        c = self.tmp / "Void.app" / "Contents"
        (c / "MacOS").mkdir(parents=True)
        shutil.copy(ROOT / "packaging/macos/launcher", c / "MacOS/Void")
        (c / "Resources/python/bin").mkdir(parents=True)
        (c / "Resources/python/bin/python3").symlink_to(sys.executable)
        self.fake_app(c / "Resources/app")
        return c / "MacOS/Void"

    def home_install(self):
        """bin/void with its app and venv in ~/.local/share/void, as install.sh lays them out."""
        void = self.tmp / "home/data/void"
        self.fake_app(void / "app")
        (void / "venv/bin").mkdir(parents=True)
        (void / "venv/bin/python").symlink_to(sys.executable)
        (self.tmp / "bin").mkdir()
        shutil.copy(ROOT / "bin/void", self.tmp / "bin/void")
        return self.tmp / "bin/void"

    def foreign_server(self):
        """Another Void (its own data folder) already serving on our port."""
        home = self.tmp / "other"
        self.fake_app(home / "app")
        p = subprocess.Popen([sys.executable, str(home / "app/server.py")], env=self.env(home),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.pids.append(p.pid)
        rt = home / "data/void/runtime.json"
        for _ in range(50):
            if rt.exists():
                return
            time.sleep(0.1)
        self.fail("the foreign fake server didn't start")

    def launch(self, launcher):
        r = subprocess.run([str(launcher), "show"], env=self.env(self.tmp / "home"), capture_output=True, text=True,
                           timeout=30)
        pidf = self.tmp / "home/data/void/server.pid"
        if pidf.exists():
            self.pids.append(int(pidf.read_text()))
        args = self.tmp / "home/data/window.args"
        for _ in range(30):                                  # (bin/void hands the window off in the background)
            if args.exists():
                break
            time.sleep(0.1)
        return r, args

    def check_own_server(self, launcher):
        r, args = self.launch(launcher)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.tmp / "home/data/void/server.pid").exists(), "it didn't start its own server")
        self.assertIn(f"http://127.0.0.1:{self.port}/?native=1", args.read_text())

    def check_never_attaches(self, launcher):
        self.foreign_server()
        r, args = self.launch(launcher)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("server didn't start", r.stderr)
        self.assertFalse(args.exists(), "it opened a window on another Void's server")

    def test_mac_bundle_starts_its_own_server(self):
        self.check_own_server(self.mac_bundle())

    def test_mac_bundle_never_attaches_to_another_void(self):
        self.check_never_attaches(self.mac_bundle())

    def test_bin_void_starts_its_own_server(self):
        self.check_own_server(self.home_install())

    def test_bin_void_never_attaches_to_another_void(self):
        self.check_never_attaches(self.home_install())


class WindowsLauncherUp(unittest.TestCase):
    """launch_win.up(): the same rule as the shell launchers, in Python."""

    def setUp(self):
        tok = "ours"

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"version": "0", "cwd": "/"} if self.headers.get("X-Void-Token") == tok
                                  else {"version": "0"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        url = f"http://127.0.0.1:{srv.server_address[1]}/"
        for p in (mock.patch.object(launch_win, "URL", url), mock.patch.object(launch_win, "DATA", self.tmp)):
            p.start()
            self.addCleanup(p.stop)

    def runtime(self, tok):
        (self.tmp / "runtime.json").write_text(json.dumps({"port": 0, "token": tok, "tool_token": "t"}))

    def test_up_when_its_own_server_answers(self):
        self.runtime("ours")
        self.assertTrue(launch_win.up())

    def test_not_up_when_another_void_answers(self):
        self.runtime("someone else's")
        self.assertFalse(launch_win.up())

    def test_not_up_without_runtime_json(self):
        self.assertFalse(launch_win.up())


if __name__ == "__main__":
    unittest.main()
