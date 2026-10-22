"""Void's Qt window names itself Void and keeps Qt's own cache (the pipeline cache) in a folder of its own under
winsys.cache_dir(), not in ~/Library/Caches/window_win.py, and leaves the page on its off-the-record profile.

    python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))

MAC = sys.platform == "darwin"
if MAC:
    try:
        from PySide6.QtCore import QCoreApplication  # noqa: E402
        from PySide6.QtWidgets import QApplication  # noqa: E402
        import window_win  # noqa: E402
    except ImportError:
        window_win = None


@unittest.skipUnless(MAC and window_win, "macOS with Qt only")
class QtIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        for k in ("QSG_RHI_PIPELINE_CACHE_SAVE", "QSG_RHI_PIPELINE_CACHE_LOAD"):
            os.environ.pop(k, None)
        self.cache = Path(self.tmp.name) / "cache"
        p = mock.patch.object(window_win.winsys, "cache_dir", lambda: self.cache)
        p.start()
        self.addCleanup(p.stop)
        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        name = QCoreApplication.applicationName()
        self.addCleanup(QCoreApplication.setApplicationName, name)

    def test_names_itself_void(self):
        window_win.name_qt_app(self.app)
        self.assertEqual(QCoreApplication.applicationName(), "Void")

    def test_pipeline_cache_goes_under_cache_dir_in_a_void_folder(self):
        window_win.name_qt_app(self.app)
        folder = self.cache / "Void"
        self.assertTrue(folder.is_dir())
        for k in ("QSG_RHI_PIPELINE_CACHE_SAVE", "QSG_RHI_PIPELINE_CACHE_LOAD"):
            self.assertEqual(Path(os.environ[k]).parent, folder, k)

    def test_does_not_override_an_explicit_choice(self):
        os.environ["QSG_RHI_PIPELINE_CACHE_SAVE"] = "/elsewhere/x"
        window_win.name_qt_app(self.app)
        self.assertEqual(os.environ["QSG_RHI_PIPELINE_CACHE_SAVE"], "/elsewhere/x")

    def test_page_stays_off_the_record(self):
        from PySide6.QtWebEngineCore import QWebEngineProfile
        window_win.name_qt_app(self.app)
        self.assertTrue(QWebEngineProfile.defaultProfile().isOffTheRecord())


if __name__ == "__main__":
    unittest.main()
