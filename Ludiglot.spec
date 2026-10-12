from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_delvewheel_libs_directory

root = Path(SPECPATH)
fuzz_datas, fuzz_binaries = collect_delvewheel_libs_directory('rapidfuzz')
a = Analysis(
    [str(root / "src" / "ludiglot" / "desktop_entry.py")],
    pathex=[str(root / "src")],
    binaries=fuzz_binaries,
    datas=[(str(root / "src" / "ludiglot" / "ui" / "style.qss"), "ludiglot/ui"),
           (str(root / 'models' / 'light_ocr'), 'models/light_ocr')] + collect_data_files('rapidocr', includes=['*.yaml']) + fuzz_datas,
    hiddenimports=collect_submodules("winrt") + ['rapidocr.main','onnxruntime'] + ["win32gui", "win32api", "win32con", "win32process", "pywintypes", "windows_capture"],
    excludes=["torch", "paddle", "paddleocr", "transformers", "tensorflow", "matplotlib", "IPython"],
    noarchive=False,
)
# Qt 6.11 uses Windows' ICU ABI. A Poppler directory in the host PATH can
# otherwise make bindepend collect its incompatible, suffixed ICU 78 DLL.
# Let Windows resolve its own system ICU instead of shipping that foreign DLL.
a.binaries = [item for item in a.binaries if Path(item[0]).name.casefold() not in {"icuuc.dll", "icudt78.dll"}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="OssmocEcho", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=bool(os.environ.get("LUDIGLOT_BUILD_DEBUG")),
          icon=str(root / "artifacts" / "app-icon.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Ludiglot")
