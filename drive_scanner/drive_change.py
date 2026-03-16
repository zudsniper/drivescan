"""Drive change detection between scan sessions."""

from dataclasses import dataclass, field
from pathlib import Path

from .scan_state import DriveSnapshot


@dataclass
class DriveChangeReport:
    removed: list[DriveSnapshot] = field(default_factory=list)
    added: list[DriveSnapshot] = field(default_factory=list)
    changed: list[tuple[DriveSnapshot, DriveSnapshot]] = field(default_factory=list)
    unchanged: list[DriveSnapshot] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.removed or self.added or self.changed)

    def can_continue(self, scan_paths: list[Path] | None = None) -> bool:
        """True if all scan paths are still accessible."""
        if scan_paths is None:
            return not self.removed
        removed_mounts = {d.mount_point for d in self.removed}
        for p in scan_paths:
            p_str = str(p)
            for mount in removed_mounts:
                if p_str == mount or p_str.startswith(mount + "/"):
                    return False
        return True

    def print_report(self) -> None:
        """Print a Rich-formatted colored report of drive changes."""
        from rich.console import Console
        from rich.table import Table

        console = Console()

        if not self.has_changes:
            console.print("[green]No drive changes detected.[/green]")
            return

        console.print("\n[bold yellow]Drive changes detected since last scan:[/bold yellow]\n")

        if self.removed:
            table = Table(title="[red]Removed Drives[/red]", show_header=True)
            table.add_column("Device")
            table.add_column("Mount Point")
            table.add_column("Filesystem")
            table.add_column("Size")
            for d in self.removed:
                table.add_row(
                    d.device, d.mount_point,
                    d.filesystem or "?",
                    _fmt_bytes(d.size_bytes),
                )
            console.print(table)

        if self.added:
            table = Table(title="[green]New Drives[/green]", show_header=True)
            table.add_column("Device")
            table.add_column("Mount Point")
            table.add_column("Filesystem")
            table.add_column("Size")
            for d in self.added:
                table.add_row(
                    d.device, d.mount_point,
                    d.filesystem or "?",
                    _fmt_bytes(d.size_bytes),
                )
            console.print(table)

        if self.changed:
            table = Table(title="[yellow]Changed Drives[/yellow]", show_header=True)
            table.add_column("Mount Point")
            table.add_column("Old FS")
            table.add_column("New FS")
            table.add_column("Old Size")
            table.add_column("New Size")
            for old, new in self.changed:
                table.add_row(
                    old.mount_point,
                    old.filesystem or "?", new.filesystem or "?",
                    _fmt_bytes(old.size_bytes), _fmt_bytes(new.size_bytes),
                )
            console.print(table)

        console.print()


def detect_drive_changes(
    saved: list[DriveSnapshot], current: list[DriveSnapshot]
) -> DriveChangeReport:
    """Compare saved drive snapshots against current state."""
    saved_by_mount = {d.mount_point: d for d in saved}
    current_by_mount = {d.mount_point: d for d in current}

    report = DriveChangeReport()

    for mount, old_drive in saved_by_mount.items():
        if mount not in current_by_mount:
            report.removed.append(old_drive)
        else:
            new_drive = current_by_mount[mount]
            if old_drive.filesystem != new_drive.filesystem or old_drive.size_bytes != new_drive.size_bytes:
                report.changed.append((old_drive, new_drive))
            else:
                report.unchanged.append(new_drive)

    for mount, new_drive in current_by_mount.items():
        if mount not in saved_by_mount:
            report.added.append(new_drive)

    return report


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
