# Corresponding source — Windows v1.0.0

The complete application source and build scripts are in this repository
and the `OssmocEcho-v1.0.0-source.zip` asset next to the Windows download.
Build instructions: `docs/BUILD.md`. No application source is obfuscated.
Package versions: `requirements-desktop-lock.txt`; Python 3.12.13 x64.

Unmodified dependencies can be obtained without charge from their official
source servers below. These are source downloads, not wheel/binary downloads.
For redistributions, preserve these directions and ensure equivalent source
remains available; upstream links alone are not permission to omit source
obligations. License notices are included in the Windows package.

| Component | Exact corresponding source |
|---|---|
| PyQt6 6.11.0 | https://files.pythonhosted.org/packages/8b/47/b25c13eca5bebc6505394d0223e46d7ebf0c57dcac2ed908d7d19b18ab6b/pyqt6-6.11.0.tar.gz |
| PyQt6-sip 13.12.0 | https://files.pythonhosted.org/packages/d1/23/16c583dbb6b53e0494dfcf7d1a44778c82e324edda62326727b37f1a5b34/pyqt6_sip-13.12.0.tar.gz |
| Qt 6.11.2, including Qt's third-party source/notices | https://download.qt.io/archive/qt/6.11/6.11.2/single/qt-everywhere-src-6.11.2.tar.xz |
| PyInstaller 6.22.3 including bootloader exception | https://files.pythonhosted.org/packages/63/41/f90302845945abd4ed647933ff5ee7c6ac93983187be67f897b6cb613331/pyinstaller-6.22.3.tar.gz |
| Shapely 2.2.0 | https://files.pythonhosted.org/packages/f3/ab/924b6e202f796d270a3041a230151f7908db5ea48c74effe6f8023e9bd05/shapely-2.2.0.tar.gz |
| Python 3.12.13 | https://www.python.org/ftp/python/3.12.13/Python-3.12.13.tar.xz |
| GEOS 3.14.1 (Shapely native DLLs) | https://download.osgeo.org/geos/geos-3.14.1.tar.bz2 |

Other unmodified Python packages: use the exact version in the lock file
on `https://pypi.org/project/<package>/<version>/#files`, selecting its
source distribution. Their upstream project links and bundled native
component notices are retained in `licenses/` and the package metadata.
GEOS DLLs remain separate in `_internal/shapely.libs/`; replacement and
relinking are allowed. Use the exact source above, not the latest version.

Qt libraries remain separate DLLs in `_internal/PyQt6/Qt6/`. This package
does not prohibit replacing/relinking them or reverse engineering for
debugging modifications. It is not signed or locked to a hardware key.
Use Qt's matching build instructions and PyQt's sdist instructions to
rebuild replacements; preserve ABI/version compatibility.

OCR model source/provenance, license and hashes: `models/light_ocr/NOTICE.md`.
`tools/download_light_models.py` fetches the pinned, verified model weights.
Game data and WAHU databases are not part of the application source and
are not distributed in the public package.
