import subprocess
import os
import tempfile
import sys
import queue
import threading
import time
from pathlib import Path

class NativeExtractor:
    def __init__(self, tool_path="./tools/FModelCLI.exe"):
        # Resolve absolute path relative to CWD (usually project root)
        # If tool_path is relative, make it absolute
        self.tool_path = Path(tool_path).resolve()
        
        # If not found, try to look relative to this file? 
        # But usually running from root is standard.
        if not self.tool_path.exists():
            # Fallback: check if we are in src/ludiglot/infrastructure and tools is up 3 levels
            # rooted at e:\Ludiglot
            application_root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parents[3]
            fallback = application_root / "tools" / "FModelCLI.exe"
            if fallback.exists():
                self.tool_path = fallback

    def run_extraction(self, game_dir, aes_key, output_dir, filter_keyword, *, progress=None, timeout=300):
        """
        Calls the C# core for precise extraction
        """
        if not self.tool_path.exists():
            print(f"[!] Critical: FModelCLI.exe not found at {self.tool_path}")
            return False

        # Ensure output directory exists
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        all_keys = list(dict.fromkeys(k.strip() for k in aes_key.split(";") if k.strip()))
        if not all_keys:
            print("[Native] No AES keys provided.")
            return False

        overall_success = True
        
        # Windows command line length limit is 32767. 400+ keys can exceed this limit.
        # FModelCLI now supports `@filepath` argument for keys to bypass it.
        # Use project cache directory for temporary key storage if available
        cache_dir = self.tool_path.parent.parent / "userdata" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
            
        tmp_path = None
        process = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", 
                suffix=".txt", 
                prefix="native_aes_keys_",
                dir=cache_dir,
                delete=False, 
                encoding="utf-8"
            ) as tmp_file:
                tmp_file.write("\n".join(all_keys))
                tmp_path = tmp_file.name

            cmd = [
                str(self.tool_path),
                str(game_dir),
                f"@{tmp_path}",
                str(output_dir),
                filter_keyword  # e.g. "ConfigDB" or "Audio"
            ]

            print(f"[Native] Calling FModelCLI with filter: {filter_keyword} and {len(all_keys)} keys...")
            
            # Live log output
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT,
                text=True, 
                encoding='utf-8',
                errors='replace' # Prevent crashes on Chinese paths
                ,creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )

            messages = queue.Queue(maxsize=256)
            def read_output():
                try:
                    while line := process.stdout.readline(65536):
                        try:
                            messages.put_nowait(line.strip())
                        except queue.Full:
                            pass  # Keep a noisy tool from blocking or filling RAM.
                except (OSError, ValueError):
                    pass
            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            started = last_report = time.monotonic()
            while True:
                try:
                    line = messages.get(timeout=.25)
                except queue.Empty:
                    line = ''
                if line:
                    print(f"    | {line}")
                    if progress:
                        progress(f'[PAK] {filter_keyword}: {line[:400]}')
                now = time.monotonic()
                if process.poll() is not None and not reader.is_alive() and messages.empty():
                    break
                if now - started >= timeout:
                    raise TimeoutError(f'Đọc PAK ({filter_keyword}) quá {timeout:g} giây. '
                                       'Hãy thoát game và thử lại; giữ nguyên dữ liệu cũ.')
                if progress and now - last_report >= 5:
                    progress(f'Đang đọc PAK {filter_keyword} — {int(now-started)} giây '
                             f'(giới hạn {timeout:g} giây). Không mở game trong lúc quét.')
                    last_report = now
            
            if process.returncode == 0:
                print(f"[Native] Extraction success for {filter_keyword}")
            else:
                print(f"[Native] Extraction failed with code {process.returncode}")
                overall_success = False
                
        except OSError as e:
            if isinstance(e, TimeoutError):
                raise
            print(f"[Native] Exception during execution: {e}")
            overall_success = False
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

        return overall_success
