from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys


@dataclass(frozen=True)
class AppPaths:
    resource_root: Path
    data_root: Path
    config_path: Path
    config_base: Path

    @classmethod
    def discover(cls, config_path: Path | None = None) -> "AppPaths":
        frozen = bool(getattr(sys, "frozen", False))
        project = Path(__file__).resolve().parents[3]
        application = Path(sys.executable).resolve().parent if frozen else project
        resource = Path(getattr(sys, "_MEIPASS", project))
        data = application / "userdata"
        default = data / "config" / "settings.json"
        if config_path and (config_path.is_absolute() or not frozen):
            configured = config_path.resolve()
        else:
            configured = default
        base = configured.parent.parent if configured.parent.parent.name == "userdata" else project
        return cls(resource, data, configured, base)

    @property
    def logs(self) -> Path:
        return self.data_root / "logs"

    @property
    def updates(self) -> Path:
        return self.data_root / "translations"
