# PyInstaller spec for the MapleStoryAnalyzer HUD.
#
# Build on Windows, from the repo root, inside the project venv:
#   .venv\Scripts\pyinstaller scripts\maple_analyzer.spec --noconfirm
#
# Output: dist\MapleStoryAnalyzer\MapleStoryAnalyzer.exe (one-folder build --
# faster startup than --onefile, and rapidocr's ONNX models are large enough
# that unpacking them to a temp dir on every launch isn't worth it).

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None
repo_root = Path(SPECPATH).resolve().parent

datas = []
binaries = []
hiddenimports = []

for pkg in ("customtkinter", "rapidocr_onnxruntime"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(
    [str(repo_root / "scripts" / "run_overlay.py")],
    pathex=[str(repo_root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MapleStoryAnalyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MapleStoryAnalyzer",
)
