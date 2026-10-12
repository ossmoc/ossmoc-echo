# Build on Windows x64

Tested with Python 3.12, PyInstaller onedir/windowed, Windows OCR and CPU
ONNX inference. No GPU/Torch/Paddle runtime is required.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe tools\download_light_models.py
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe tools\build_public.py
```

The public build is written to artifacts/public/OssmocEcho; ZIP it as a
folder, keeping _internal and licenses. It never reads private settings or
copies game/WAHU data. Runtime data/config/logs are generated in userdata
next to the EXE. Models are fetched only by the explicit build helper from
pinned upstream URLs and verified with SHA-256; runtime does not download
models or send screenshots off the machine.

To run from source:

```powershell
.\.venv\Scripts\python.exe -m ludiglot.desktop_entry
```

Data is not supplied in this repository. Import lawful offline data or
read your own game data, and obtain the corresponding WAHU translation.
Do not install translation PAKs or loader DLLs into the game for Echo.

The older tools/build_portable.py is retained only for its icon generator
and optional local developer provisioning; the supported empty-data build
is tools/build_public.py. Building source does not require a private
config/settings.json, personal screenshots, or a previous portable bundle.
