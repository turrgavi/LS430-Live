# PyInstaller recipe for the macOS app. Run from the repo root:
#   pyinstaller packaging/LS430Live.spec
# Expects librmvci_j2534.dylib (built from rmvci) in the repo root.
import os
import re

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VERSION = re.search(r'VERSION = "([^"]+)"', open(os.path.join(ROOT, "ls430live", "version.py")).read()).group(1)

a = Analysis(
    [os.path.join(ROOT, "desktop.py")],
    pathex=[ROOT],
    binaries=[(os.path.join(ROOT, "librmvci_j2534.dylib"), ".")],
    datas=[(os.path.join(ROOT, "ls430live", "dashboard.html"), "ls430live")],
    hiddenimports=["serial", "webview"],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LS430 Live", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="LS430 Live")
app = BUNDLE(
    coll,
    name="LS430 Live.app",
    icon=os.path.join(SPECPATH, "icon.png"),
    bundle_identifier="local.ls430live",
    version=VERSION,
    info_plist={
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHumanReadableCopyright": "Personal diagnostic tool. Not affiliated with Toyota or Lexus.",
    },
)
