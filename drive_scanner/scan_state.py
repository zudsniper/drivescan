"""Shared scan state and persistence for pause/resume support."""

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DriveSnapshot:
    device: str
    mount_point: str
    filesystem: str | None
    label: str | None
    size_bytes: int

    def to_dict(self) -> dict:
        return {
            "device": self.device,
            "mount_point": self.mount_point,
            "filesystem": self.filesystem,
            "label": self.label,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DriveSnapshot":
        return cls(
            device=d["device"],
            mount_point=d["mount_point"],
            filesystem=d.get("filesystem"),
            label=d.get("label"),
            size_bytes=d.get("size_bytes", 0),
        )


@dataclass
class ScanProgress:
    files_scanned: int = 0
    files_estimated: int = 0
    matches_per_filter: dict[str, int] = field(default_factory=dict)
    errors: int = 0
    current_file: str = ""
    current_path_index: int = 0
    total_paths: int = 0
    bytes_scanned: int = 0
    start_time: float = 0.0
    elapsed_paused: float = 0.0

    def to_dict(self) -> dict:
        return {
            "files_scanned": self.files_scanned,
            "files_estimated": self.files_estimated,
            "matches_per_filter": dict(self.matches_per_filter),
            "errors": self.errors,
            "current_path_index": self.current_path_index,
            "total_paths": self.total_paths,
            "bytes_scanned": self.bytes_scanned,
            "elapsed_paused": self.elapsed_paused,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScanProgress":
        return cls(
            files_scanned=d.get("files_scanned", 0),
            files_estimated=d.get("files_estimated", 0),
            matches_per_filter=d.get("matches_per_filter", {}),
            errors=d.get("errors", 0),
            current_path_index=d.get("current_path_index", 0),
            total_paths=d.get("total_paths", 0),
            bytes_scanned=d.get("bytes_scanned", 0),
            elapsed_paused=d.get("elapsed_paused", 0.0),
        )


class ScanState:
    """Thread-safe shared state between scanner, TUI, and input threads."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.progress = ScanProgress()
        self.completed_dirs: set[str] = set()
        self.current_dir_files_done: set[str] = set()
        self.matches: dict[str, list[dict]] = {}
        self.scan_config: dict[str, Any] = {}
        self.drive_snapshots: list[DriveSnapshot] = []
        self.pause_event = threading.Event()
        self.pause_event.set()  # starts running (not paused)
        self.cancel_requested = False
        self.finished = False
        self._pause_start: float | None = None
        self._checkpoint_counter = 0
        self._last_checkpoint_time = 0.0

    @property
    def is_paused(self) -> bool:
        return not self.pause_event.is_set()

    def toggle_pause(self) -> bool:
        """Toggle pause state. Returns True if now paused."""
        if self.pause_event.is_set():
            self._pause_start = time.time()
            self.pause_event.clear()
            return True
        else:
            if self._pause_start is not None:
                with self.lock:
                    self.progress.elapsed_paused += time.time() - self._pause_start
                self._pause_start = None
            self.pause_event.set()
            return False

    def should_checkpoint(self) -> bool:
        """Check if it's time to save a checkpoint (every 30s or 1000 dirs)."""
        now = time.time()
        if now - self._last_checkpoint_time >= 30:
            return True
        if self._checkpoint_counter >= 1000:
            return True
        return False

    def mark_checkpoint_done(self) -> None:
        self._last_checkpoint_time = time.time()
        self._checkpoint_counter = 0

    def increment_dir_counter(self) -> None:
        self._checkpoint_counter += 1

    def save(self, path: Path) -> None:
        """Serialize state to JSON for resume."""
        with self.lock:
            data = {
                "progress": self.progress.to_dict(),
                "completed_dirs": sorted(self.completed_dirs),
                "matches": self.matches,
                "scan_config": self.scan_config,
                "drive_snapshots": [ds.to_dict() for ds in self.drive_snapshots],
            }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        tmp.rename(path)

    @classmethod
    def load(cls, path: Path) -> "ScanState":
        """Deserialize state from JSON."""
        with open(path) as f:
            data = json.load(f)

        state = cls()
        state.progress = ScanProgress.from_dict(data.get("progress", {}))
        state.completed_dirs = set(data.get("completed_dirs", []))
        state.matches = data.get("matches", {})
        state.scan_config = data.get("scan_config", {})
        state.drive_snapshots = [
            DriveSnapshot.from_dict(d) for d in data.get("drive_snapshots", [])
        ]
        return state

    @staticmethod
    def state_file_for_paths(paths: list[Path], state_dir: Path | None = None) -> Path:
        """Compute the state file path for a set of scan paths."""
        if state_dir is None:
            state_dir = Path.home() / ".local" / "share" / "drivescan" / "scans"
        key = "|".join(sorted(str(p.resolve()) for p in paths))
        h = hashlib.sha256(key.encode()).hexdigest()[:16]
        return state_dir / f"scan_{h}.json"

    @staticmethod
    def find_latest_state(state_dir: Path | None = None) -> Path | None:
        """Find the most recently modified state file."""
        if state_dir is None:
            state_dir = Path.home() / ".local" / "share" / "drivescan" / "scans"
        if not state_dir.exists():
            return None
        state_files = sorted(state_dir.glob("scan_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return state_files[0] if state_files else None
