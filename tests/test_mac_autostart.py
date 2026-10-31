"""The macOS login item must start the Void.app that is running, not whatever `void` is on the PATH.

    python3 -m unittest discover -s tests
"""
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))
import server  # noqa: E402

MAC = sys.platform == "darwin"
if MAC:
    import macsys  # noqa: E402


def make_bundle(root, name="Void.app"):
    """A fake bundle → (launcher, app dir inside it)."""
    launcher = Path(root) / name / "Contents" / "MacOS" / "Void"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")
    app = Path(root) / name / "Contents" / "Resources" / "app"
    app.mkdir(parents=True)
    return launcher, app


@unittest.skipUnless(MAC, "macOS only")
class BundleLauncher(unittest.TestCase):
    def test_found_from_app_dir(self):
        with tempfile.TemporaryDirectory() as d:
            launcher, app = make_bundle(d)
            self.assertEqual(macsys.bundle_launcher(app), str(launcher))

    def test_bundle_anywhere_and_renamed(self):
        with tempfile.TemporaryDirectory() as d:
            launcher, app = make_bundle(Path(d) / "My Apps", "Void 2.app")
            self.assertEqual(macsys.bundle_launcher(app), str(launcher))

    def test_not_a_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "app").mkdir()
            self.assertIsNone(macsys.bundle_launcher(Path(d) / "app"))

    def test_bundle_without_launcher(self):
        with tempfile.TemporaryDirectory() as d:
            launcher, app = make_bundle(d)
            launcher.unlink()
            self.assertIsNone(macsys.bundle_launcher(app))


@unittest.skipUnless(MAC, "macOS only")
class MacAutostart(unittest.TestCase):
    def sync(self, tmp, on=True, app_dir=None, which="/opt/homebrew/bin/void"):
        agent = Path(tmp) / "home" / "Library" / "LaunchAgents" / "void.plist"
        with mock.patch.object(server, "LAUNCH_AGENT", agent), \
                mock.patch.object(server, "MAC", True), mock.patch.object(server, "WIN", False), \
                mock.patch.object(server, "APP_DIR", app_dir or Path(tmp) / "checkout" / "app"), \
                mock.patch.dict(server.CFG, {"autostart": on}), \
                mock.patch.object(server.shutil, "which", return_value=which):
            server.sync_autostart()
        return agent

    def args(self, agent):
        return plistlib.loads(agent.read_bytes())["ProgramArguments"]

    def test_points_at_running_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            launcher, app = make_bundle(Path(d) / "Applications")
            self.assertEqual(self.args(self.sync(d, app_dir=app)), [str(launcher), "show"])

    def test_bundle_beats_void_on_path(self):
        with tempfile.TemporaryDirectory() as d:
            launcher, app = make_bundle(d)
            self.assertEqual(self.args(self.sync(d, app_dir=app, which="/usr/local/bin/void"))[0], str(launcher))

    def test_non_bundle_keeps_void_lookup(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.args(self.sync(d)), ["/opt/homebrew/bin/void", "show"])

    def test_non_bundle_falls_back_to_local_bin(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.args(self.sync(d, which=None)), [str(Path.home() / ".local/bin/void"), "show"])

    def test_off_removes_agent(self):
        with tempfile.TemporaryDirectory() as d:
            agent = self.sync(d)
            self.assertTrue(agent.exists())
            self.sync(d, on=False)
            self.assertFalse(agent.exists())


class StableLaunchAgent(unittest.TestCase):
    """Runs anywhere: macsys is faked, so Void.app running from a DMG or a translocated copy can be pretended."""
    def sync(self, tmp, launcher, on=True, which="/opt/homebrew/bin/void"):
        agent = Path(tmp) / "Library" / "LaunchAgents" / "void.plist"
        fake = mock.Mock(bundle_launcher=mock.Mock(return_value=launcher))
        with mock.patch.object(server, "LAUNCH_AGENT", agent), mock.patch.object(server, "macsys", fake, create=True), \
                mock.patch.object(server, "MAC", True), mock.patch.object(server, "WIN", False), \
                mock.patch.dict(server.CFG, {"autostart": on}), \
                mock.patch.object(server.shutil, "which", return_value=which):
            server.sync_autostart()
        return agent

    def test_stable_path_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            agent = self.sync(d, "/Applications/Void.app/Contents/MacOS/Void")
            self.assertEqual(plistlib.loads(agent.read_bytes())["ProgramArguments"],
                             ["/Applications/Void.app/Contents/MacOS/Void", "show"])

    def test_dmg_mount_is_never_written(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(self.sync(d, "/Volumes/Void/Void.app/Contents/MacOS/Void").exists())

    def test_translocated_path_is_never_written(self):
        with tempfile.TemporaryDirectory() as d:
            t = "/private/var/folders/ab/xyz/T/AppTranslocation/1234-ABCD/d/Void.app/Contents/MacOS/Void"
            self.assertFalse(self.sync(d, t).exists())

    def test_unstable_path_leaves_existing_plist(self):
        with tempfile.TemporaryDirectory() as d:
            agent = self.sync(d, "/Applications/Void.app/Contents/MacOS/Void")
            before = agent.read_bytes()
            self.sync(d, "/Volumes/Void/Void.app/Contents/MacOS/Void")
            self.assertEqual(agent.read_bytes(), before)

    def test_resync_on_start_rewrites_existing_plist(self):
        with tempfile.TemporaryDirectory() as d:
            agent = self.sync(d, "/Applications/Void.app/Contents/MacOS/Void")
            agent.write_bytes(plistlib.dumps({"Label": "void", "ProgramArguments": ["/gone/Void", "show"]}))
            fake = mock.Mock(bundle_launcher=mock.Mock(return_value="/Applications/Void.app/Contents/MacOS/Void"))
            with mock.patch.object(server, "LAUNCH_AGENT", agent), mock.patch.object(server, "macsys", fake, create=True), \
                    mock.patch.object(server, "MAC", True), mock.patch.object(server, "WIN", False), \
                    mock.patch.dict(server.CFG, {"autostart": True}):
                server.resync_launch_agent()
            self.assertEqual(plistlib.loads(agent.read_bytes())["ProgramArguments"][0],
                             "/Applications/Void.app/Contents/MacOS/Void")

    def test_resync_without_plist_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            agent = Path(d) / "void.plist"
            with mock.patch.object(server, "LAUNCH_AGENT", agent), mock.patch.object(server, "MAC", True), \
                    mock.patch.dict(server.CFG, {"autostart": True}):
                server.resync_launch_agent()
            self.assertFalse(agent.exists())


if __name__ == "__main__":
    unittest.main()
