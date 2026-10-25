"""Cuts the Python inside Void.app down to what Void runs (build.sh calls it, with that Python):
  - Qt: the PySide6 modules app/*.py import, every Qt framework and pyside lib they link (followed all the way down),
    and the plugins those need (cocoa, the mac style, image formats, TLS); the rest of PySide6 (designer, QML, 3D,
    multimedia, the tools, translations, headers) goes
  - every Mach-O thinned to arm64
  - what a bundled interpreter never uses: pip, Tk / IDLE, 2to3, headers, the console scripts, packages' tests
   python3 -I packaging/macos/slim.py <Void.app/Contents/Resources>"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

RES = Path(sys.argv[1])
PYH = RES / "python"
SITE = PYH / "lib/python3.12/site-packages"
PS = SITE / "PySide6"
QT = PS / "Qt"
PLUGIN_DIRS = ("imageformats", "iconengines", "styles", "tls", "networkinformation")   # kept when all they link is kept
PLATFORM = "platforms/libqcocoa.dylib"


def rm(p):
    if p.is_symlink() or p.is_file():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)


def links(f):
    """@rpath names a Mach-O loads: 'QtGui' for a framework, 'libpyside6.abi3.6.12.dylib' for a plain library."""
    out = subprocess.run(["otool", "-L", str(f)], capture_output=True, text=True, check=True).stdout
    return set(re.findall(r"@rpath/(\w+)\.framework/", out)), set(re.findall(r"@rpath/(lib[\w.]+\.dylib)", out))


def lib_path(name):
    return next(p for p in (PS / name, SITE / "shiboken6" / name, QT / "lib" / name) if p.exists())


def closure(roots):
    """Frameworks and plain libraries the roots load, transitively. A framework kept keeps its Python binding too
    (a binding imports the bindings of what it links), and its helper apps (QtWebEngineProcess)."""
    fws, libs, todo, seen = set(), set(), list(roots), set()
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        fw_names, lib_names = links(f)
        for n in fw_names - fws:
            fws.add(n)
            d = QT / "lib" / f"{n}.framework"
            todo.append(d / "Versions/A" / n)
            todo += [b for b in (d / "Versions/A/Helpers").glob("*.app/Contents/MacOS/*")]
            if (PS / f"{n}.abi3.so").exists():
                todo.append(PS / f"{n}.abi3.so")
        for n in lib_names - libs:
            libs.add(n)
            todo.append(lib_path(n))
    return fws, libs


def prune_qt():
    used = set()
    for py in (RES / "app").glob("*.py"):
        used |= set(re.findall(r"PySide6\.(Qt\w+)", py.read_text(encoding="utf-8")))
    fws, libs = closure([PS / f"{m}.abi3.so" for m in sorted(used)] + [QT / "plugins" / PLATFORM])
    plugins = {QT / "plugins" / PLATFORM}
    for d in PLUGIN_DIRS:
        for p in (QT / "plugins" / d).glob("*.dylib"):
            f, lb = links(p)
            if f <= fws and lb <= libs:
                plugins.add(p)
    for p in (QT / "plugins").rglob("*"):
        if p.is_file() and p not in plugins:
            rm(p)
    for d in sorted((QT / "plugins").iterdir()):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    for p in (QT / "lib").iterdir():
        keep = p.name[:-len(".framework")] in fws if p.suffix == ".framework" else p.name in libs
        if not keep:
            rm(p)
    for p in PS.iterdir():
        mod = p.name.split(".")[0]
        if p.suffix in (".so", ".pyi") and p.name.startswith("Qt") and mod not in fws:
            rm(p)
        elif p.suffix == ".dylib" and p.name not in libs:
            rm(p)
        elif p.suffix == ".app" or (p.is_file() and not p.suffix):          # Designer, Linguist, lupdate, qmlls, …
            rm(p)
    for p in ("include", "lib", "glue", "typesystems", "doc", "scripts", "QtAsyncio",
              "Qt/qml", "Qt/translations", "Qt/metatypes", "Qt/libexec", "../shiboken6/include", "../shiboken6/lib"):
        rm(PS / p)
    web = QT / "lib/QtWebEngineCore.framework/Versions/A/Resources"
    rm(web / "qtwebengine_devtools_resources.pak")                       # the inspector, which Void never opens
    for p in web.glob("v8_context_snapshot.*.bin"):
        if p.name != "v8_context_snapshot.arm64.bin":
            rm(p)
    return sorted(used), fws


def prune_python():
    std = PYH / "lib/python3.12"
    for p in ("include", "share", "lib/pkgconfig", "lib/tcl9", "lib/tcl9.0", "lib/tk9.0", "lib/libtcl9.0.dylib",
              "lib/libtcl9tk9.0.dylib"):
        rm(PYH / p)
    for p in (PYH / "lib").glob("itcl*"):
        rm(p)
    for p in (PYH / "lib").glob("thread*"):
        rm(p)
    for p in ("idlelib", "tkinter", "turtledemo", "turtle.py", "ensurepip", "lib2to3"):
        rm(std / p)
    for p in (std / "lib-dynload").glob("_tkinter*"):
        rm(p)
    for p in [SITE / "pip", *SITE.glob("pip-*.dist-info")]:
        rm(p)
    for p in (PYH / "bin").iterdir():                                     # the console scripts' shebangs point at the
        if not re.fullmatch(r"python3?(\.12)?", p.name):                   # build folder; Void runs `python -m …`
            rm(p)
    for p in sorted(SITE.rglob("tests"), reverse=True):
        if p.is_dir():
            rm(p)


def thin():
    n = 0
    for f in RES.rglob("*"):
        if f.is_symlink() or not f.is_file():
            continue
        with f.open("rb") as h:
            if h.read(4) != b"\xca\xfe\xba\xbe":                           # fat Mach-O (or a Java class: lipo says)
                continue
        archs = subprocess.run(["lipo", "-archs", str(f)], capture_output=True, text=True).stdout.split()
        if "arm64" in archs and len(archs) > 1:
            tmp = f.with_name(f.name + ".thin")
            subprocess.run(["lipo", "-thin", "arm64", str(f), "-output", str(tmp)], check=True)
            shutil.copymode(f, tmp)
            tmp.replace(f)
            n += 1
    return n


used, fws = prune_qt()
prune_python()
print(f"Qt: {', '.join(used)} → {len(fws)} frameworks kept; {thin()} Mach-O files thinned to arm64")
