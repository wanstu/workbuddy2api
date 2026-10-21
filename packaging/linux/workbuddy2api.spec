# Build with scripts/build_linux_deb.py on Linux x86_64.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parents[1]

datas = [
    (str(root / "admin/static"), "admin/static"),
    (str(root / "admin/data"), "admin/data"),
    (str(root / "turing_helper.js"), "."),
    (str(root / "desktop/version.json"), "desktop"),
    (str(root / "desktop/assets/app.svg"), "desktop/assets"),
    (str(root / "LICENSE"), "."),
]

hidden = collect_submodules("admin")
hidden += [
    "converter",
    "anthropic_adapter",
    "responses_adapter",
    "responses_projection",
    "desensitize",
    "upstream_compat",
    "wb_install",
    "wb_asar",
    "wb_envcheck",
    "desktop.common",
    "desktop.updates",
    "desktop.runtime",
    "uvicorn.logging",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.lifespan.on",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.mysql.pymysql",
]

analysis = Analysis(
    [str(root / "linux_entry.py")],
    pathex=[str(root)],
    datas=datas,
    hiddenimports=hidden,
    excludes=[
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "cefpython3",
        "cairosvg",
        "resvg_py",
        "numpy",
        "scipy",
        "matplotlib",
        "pytest",
        "IPython",
        "pythonnet",
        "clr",
        "winreg",
        "webview",
        "pystray",
    ],
    noarchive=False,
)

pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="workbuddy2api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
