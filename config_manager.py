"""Application configuration – persisted as JSON in the platform’s app-data folder.

The configuration file stores user preferences that persist across sessions
and are independent of any specific project:

- Default / last-used project directory
- Last export directory
- Up to 10 recently opened projects
- Custom projects root (optional override)

Storage locations:
    Windows   %LOCALAPPDATA%/PCB-to-KiCad/config.json
    macOS     ~/Library/Application Support/PCB-to-KiCad/config.json
    Linux     ~/.config/PCB-to-KiCad/config.json

On first run, any legacy config.json found in ~/Documents/PCB-to-KiCad/
is automatically migrated to the new location.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Default root for all projects lives in the user's Documents
_APP_DIR_NAME = "PCB-to-KiCad"
_CONFIG_FILE = "config.json"
_MAX_RECENT = 10


def _default_projects_root() -> Path:
    """Return ~/Documents/PCB-to-KiCad (created lazily)."""
    docs = Path.home() / "Documents"
    return docs / _APP_DIR_NAME


def _app_data_dir() -> Path:
    """Return platform-specific application data directory.

    Windows:  %LOCALAPPDATA%/PCB-to-KiCad
    macOS:    ~/Library/Application Support/PCB-to-KiCad
    Linux:    ~/.config/PCB-to-KiCad
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / _APP_DIR_NAME


def _config_path() -> Path:
    """Config file sits in app-data, independent of any project."""
    return _app_data_dir() / _CONFIG_FILE


class ConfigManager:
    """Read / write a small JSON config file for persistent app settings."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._path = _config_path()
        self._migrate_old_config()
        self._load()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def projects_root(self) -> Path:
        """Base directory where new projects are created."""
        custom = self._data.get("projects_root")
        p = Path(custom) if custom else _default_projects_root()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @projects_root.setter
    def projects_root(self, value: str | Path) -> None:
        self._data["projects_root"] = str(value)
        self._save()

    @property
    def last_project_dir(self) -> str:
        return self._data.get("last_project_dir", str(self.projects_root))

    @last_project_dir.setter
    def last_project_dir(self, value: str) -> None:
        self._data["last_project_dir"] = value
        self._save()

    @property
    def last_export_dir(self) -> str:
        return self._data.get("last_export_dir", "")

    @last_export_dir.setter
    def last_export_dir(self, value: str) -> None:
        self._data["last_export_dir"] = value
        self._save()

    @property
    def recent_projects(self) -> list[str]:
        return list(self._data.get("recent_projects", []))

    def add_recent_project(self, path: str) -> None:
        recents = self.recent_projects
        if path in recents:
            recents.remove(path)
        recents.insert(0, path)
        self._data["recent_projects"] = recents[:_MAX_RECENT]
        self._save()

    # ------------------------------------------------------------------
    # Project directory helpers
    # ------------------------------------------------------------------

    def project_dir_for(self, project_name: str) -> Path:
        """Return <projects_root>/<project_name>/, creating it."""
        d = self.projects_root / project_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def images_dir_for(project_dir: str | Path) -> Path:
        """Return <project_dir>/images/, creating it."""
        d = Path(project_dir) / "images"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _migrate_old_config(self) -> None:
        """Move config from old location (Documents/PCB-to-KiCad/) to app-data."""
        old = _default_projects_root() / _CONFIG_FILE
        if old.is_file() and not self._path.is_file():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(str(old), str(self._path))
            old.unlink()

    def _load(self) -> None:
        if self._path.is_file():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
