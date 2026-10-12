# Third-party components

Public Windows builds contain the license texts in `licenses/`.
Do not remove notices when redistributing.

| Component | License / source |
|---|---|
| Ludiglot | MIT; LICENSES/Ludiglot-MIT.txt; https://github.com/Herselfta/Ludiglot |
| PyQt6 | GPL-3.0-only / commercial; this release uses GPL; https://riverbankcomputing.com/software/pyqt/ |
| Qt | Qt's LGPL/GPL and third-party component licenses; https://download.qt.io/ |
| PyInstaller | GPL with bootloader exception; https://pyinstaller.org/ |
| RapidFuzz | MIT; https://github.com/rapidfuzz/RapidFuzz |
| RapidOCR / PP-OCR models | Apache-2.0; models/light_ocr/NOTICE.md |
| ONNX Runtime | MIT; https://github.com/microsoft/onnxruntime |
| windows-capture | MIT; https://github.com/NiiightmareXD/windows-capture |
| Pillow, NumPy, OpenCV, Shapely and other runtime packages | Their respective notices are preserved in the binary package |
| Python | PSF license and notices; https://www.python.org/ |

FModelCLI/FModel is a separate optional external program, not bundled in
the public release. Obtain it from its own publisher and respect its own
license. No claim is made that an MIT license is inherently incompatible
with GPL; MIT components can be incorporated into a GPL application.

Exact build package versions are recorded in requirements-desktop-lock.txt.
See SOURCES.md for corresponding-source locations and build instructions.
