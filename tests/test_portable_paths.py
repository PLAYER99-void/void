"""Tests for the bits of app/server.py that must not depend on the shell's PATH or the system python (a bundled .app).

    python3 -m unittest discover tests
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))
import server  # noqa: E402


class ClaudeLookup(unittest.TestCase):
    def find(self, existing):
        real = Path.exists
        with mock.patch.object(server.shutil, "which", return_value=None), \
                mock.patch.object(Path, "exists", lambda self: str(self) in existing or (real(self) and False)):
            return server.claude_bin()

    def test_finds_homebrew(self):
        self.assertEqual(self.find({"/opt/homebrew/bin/claude"}), "/opt/homebrew/bin/claude")

    def test_usr_local_still_found(self):
        self.assertEqual(self.find({"/usr/local/bin/claude"}), "/usr/local/bin/claude")

    def test_existing_order_kept(self):
        self.assertEqual(self.find({"/opt/homebrew/bin/claude", "/usr/bin/claude"}), "/usr/bin/claude")


class Askpass(unittest.TestCase):
    def test_launcher_uses_own_interpreter(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(server.askpass_path(d))
            self.assertEqual(p.parent, Path(d))
            self.assertTrue(os.access(p, os.X_OK))
            text = p.read_text()
            self.assertIn(sys.executable, text)
            self.assertIn(str(server.APP_DIR / "askpass.py"), text)
            self.assertEqual(server.askpass_path(d), str(p))      # idempotent

    def test_launcher_runs_script(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"PATH": "/usr/bin:/bin", "HOME": d, "XDG_DATA_HOME": d}      # no runtime.json → script exits 1
            r = subprocess.run([server.askpass_path(d), "Password:"], env=env, capture_output=True, timeout=20)
            self.assertEqual(r.returncode, 1)


class Piper(unittest.TestCase):
    def test_module_entry_when_installed(self):
        with mock.patch("importlib.util.find_spec", return_value=object()):
            self.assertEqual(server.piper_cmd(), [sys.executable, "-m", "piper"])

    def test_console_script_fallback(self):
        with mock.patch("importlib.util.find_spec", return_value=None), \
                mock.patch.object(server.shutil, "which", side_effect=lambda n: "/x/piper" if n == "piper" else None), \
                mock.patch.object(Path, "exists", return_value=False):
            self.assertEqual(server.piper_cmd(), ["/x/piper"])

    def test_none_when_absent(self):
        with mock.patch("importlib.util.find_spec", return_value=None), \
                mock.patch.object(server.shutil, "which", return_value=None), \
                mock.patch.object(Path, "exists", return_value=False):
            self.assertIsNone(server.piper_cmd())

    def test_run_uses_module(self):
        with mock.patch("importlib.util.find_spec", return_value=object()), \
                mock.patch.object(server.subprocess, "run") as run:
            server.piper_run("m.onnx", "hi", "o.wav", 1.0)
            self.assertEqual(run.call_args[0][0][:3], [sys.executable, "-m", "piper"])


if __name__ == "__main__":
    unittest.main()
