"""build.sh's Qt check, run with the bundled Python: QApplication comes up on the cocoa platform plugin and makes the Qt
pieces his window is made of (none shown, Void itself not started), and a page loads in the web view.
   python3 -I packaging/macos/qtcheck.py <Void.app/Contents/Resources/app>"""
import os
import sys
from pathlib import Path

APP = Path(sys.argv[1])
sys.path.insert(0, str(APP))
os.environ["QT_QPA_PLATFORM"] = "cocoa"
import window_win as ww  # noqa: E402
from PySide6.QtCore import QFile, QIODevice, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QImage, QPainter  # noqa: E402
from PySide6.QtWebChannel import QWebChannel  # noqa: E402
from PySide6.QtWebEngineCore import QWebEngineScript  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

app = QApplication(sys.argv[:1])
assert app.platformName() == "cocoa", app.platformName()
assert app.style().name() == "macos", app.style().name()
win = QWidget(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint)
icon = QIcon(str(APP / "static" / "icon.png"))
assert not icon.isNull(), "icon.png didn't load"
win.setWindowIcon(icon)
view = QWebEngineView(win)
QVBoxLayout(win).addWidget(view)
chan = QWebChannel(view.page())
bridge, remote, screens = ww.Bridge(), ww.Remote(), ww.ScreenWatch()
chan.registerObject("voidui", bridge)
view.page().setWebChannel(chan)
f = QFile(":/qtwebchannel/qwebchannel.js")
assert f.open(QIODevice.ReadOnly) and f.size() > 0, "qwebchannel.js missing"
sc = QWebEngineScript()
sc.setSourceCode(bytes(f.readAll()).decode() + ww.SHIM)
sc.setInjectionPoint(QWebEngineScript.DocumentCreation)
sc.setWorldId(QWebEngineScript.MainWorld)
view.page().scripts().insert(sc)
img = QImage(40, 40, QImage.Format_ARGB32)                 # what Ghost paints with
img.fill(0)
qp = QPainter(img)
qp.setBrush(QColor(0, 0, 0))
qp.drawRoundedRect(0, 0, 30, 30, 8, 8)
qp.end()

result = {}
view.page().loadFinished.connect(lambda ok: view.page().runJavaScript(
    "document.title + ' ' + (typeof QWebChannel)", lambda r: (result.update(ok=ok, js=r), app.quit())))
QTimer.singleShot(30000, app.quit)
view.setHtml("<title>ok</title>")
app.exec()
assert result.get("ok") and result.get("js") == "ok function", f"web view didn't load a page: {result}"
print(f"Qt {app.platformName()}: QApplication, Void's window classes and a web page ({result['js']})")
