import os
import shutil
import urllib.request
import sys
from pathlib import Path

class ToolManager:
    """
    Manages external tools like FModelCLI.
    Decoupled from the source code, focuses on binary distribution.
    """
    def __init__(self):
        self.root_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]
        self.tools_dir = self.root_dir / "tools"
        self.fmodel_cli_exe = self.tools_dir / "FModelCLI.exe"
        
        self.dev_fmodel_cli_path = Path("E:/FModelCLI/dist/FModelCLI.exe")

        # GitHub Release Info
        self.repo_owner = "Herselfta"
        self.repo_name = "FModelCLI"
        # Standard release URL pattern for latest
        self.download_url = f"https://github.com/{self.repo_owner}/{self.repo_name}/releases/latest/download/FModelCLI.exe"

    def ensure_fmodel_cli(self, force_update=False):
        """
        Ensures FModelCLI.exe is available by checking local paths or downloading.
        """
        if self.fmodel_cli_exe.exists() and not force_update:
            return True

        self.tools_dir.mkdir(parents=True, exist_ok=True)

        # 1. 优先检查本地开发路径 (解耦后的路径)
        dev_paths = [
            Path("E:/FModelCLI/dist/FModelCLI.exe"),
            Path("E:/FModelCLI/FModelCLI/bin/Release/net8.0/win-x64/publish/FModelCLI.exe")
        ]
        
        for dev_path in dev_paths:
            if dev_path.exists():
                print(f"[ToolManager] Found local dev build at {dev_path}, copying...")
                shutil.copy2(dev_path, self.fmodel_cli_exe)
                return True

        # 2. 如果本地没有，则从 GitHub Release 下载
        print(f"[ToolManager] FModelCLI.exe not found. Downloading from {self.download_url}...")
        return self._download_fmodel_cli()

    def _download_fmodel_cli(self):
        try:
            # Simple download with progress placeholder
            def progress(block_num, block_size, total_size):
                if total_size > 0:
                    percent = min(100, int(block_num * block_size * 100 / total_size))
                    if percent % 10 == 0: # Reduce spam
                         print(f"[ToolManager] Downloading... {percent}%", end='\r')

            urllib.request.urlretrieve(self.download_url, str(self.fmodel_cli_exe), reporthook=progress)
            print("\n[ToolManager] Download complete.")
            return True
        except Exception as e:
            print(f"\n[ToolManager] Download failed: {e}")
            print(f"Please manually download FModelCLI.exe to {self.fmodel_cli_exe}")
            return False

if __name__ == "__main__":
    tm = ToolManager()
    tm.ensure_fmodel_cli(force_update=True)
