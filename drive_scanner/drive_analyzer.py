"""Detect attached drives and their filesystem types."""

import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil

from . import colors


@dataclass
class DriveInfo:
    device: str
    mount_point: str | None
    filesystem: str | None
    label: str | None
    size: str | None
    used: str | None
    free: str | None

    def to_dict(self) -> dict:
        return {
            "device": self.device,
            "mount_point": self.mount_point,
            "filesystem": self.filesystem,
            "label": self.label,
            "size": self.size,
            "used": self.used,
            "free": self.free,
        }


def _format_bytes(n: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _get_label(device: str, mount_point: str) -> str | None:
    """Try to get volume label using platform-specific tools."""
    system = platform.system()
    try:
        if system == "Darwin":
            result = subprocess.run(
                ["diskutil", "info", "-plist", device],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                import plistlib
                info = plistlib.loads(result.stdout.encode())
                name = info.get("VolumeName")
                if name:
                    return name
        elif system == "Linux":
            result = subprocess.run(
                ["lsblk", "-n", "-o", "LABEL", device],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                label = result.stdout.strip()
                if label:
                    return label
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, Exception):
        pass

    # Fallback: use the last component of the mount point
    if mount_point and mount_point != "/":
        return Path(mount_point).name
    return None


def analyze_drives() -> list[DriveInfo]:
    """Detect all mounted partitions using psutil."""
    drives: list[DriveInfo] = []

    for part in psutil.disk_partitions(all=True):
        size_str = None
        used_str = None
        free_str = None

        if part.mountpoint:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                size_str = _format_bytes(usage.total)
                used_str = _format_bytes(usage.used)
                free_str = _format_bytes(usage.free)
            except (PermissionError, OSError):
                pass

        label = _get_label(part.device, part.mountpoint)

        drives.append(DriveInfo(
            device=part.device,
            mount_point=part.mountpoint or None,
            filesystem=part.fstype or None,
            label=label,
            size=size_str,
            used=used_str,
            free=free_str,
        ))

    return drives


def print_drive_table(drives: list[DriveInfo]) -> None:
    if not drives:
        colors.warn("No drives detected.")
        return

    colors.header("Attached Drives")

    headers = ["Device", "Mount Point", "Filesystem", "Label", "Size", "Used", "Free"]
    rows = []
    for d in drives:
        rows.append([
            d.device or "-",
            d.mount_point or "-",
            d.filesystem or "unknown",
            d.label or "-",
            d.size or "-",
            d.used or "-",
            d.free or "-",
        ])

    # Calculate column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    print(colors.bold(fmt_row(headers)))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        row[2] = colors.cyan(row[2])
        print(fmt_row(row))
    print()
