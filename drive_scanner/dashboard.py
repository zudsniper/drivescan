"""Interactive TUI dashboard for drive-scanner."""

import atexit
import platform
import sys
import termios
import threading
import time
import tty

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .drive_analyzer import DriveInfo, analyze_drives
from .enclosure_detector import EnclosureInfo, detect_enclosures
from .raid_detector import RAIDArray, detect_raid_arrays
from .recovery import RecoveryPlan, generate_recovery_plans

VERSION = "0.1.0"


class DashboardTUI:
    """Interactive TUI dashboard for drive scanning overview."""

    def __init__(self) -> None:
        self.console = Console()
        self.view = "main"  # main, drives, raid, recovery, filters
        self._old_term_settings = None
        self._input_thread: threading.Thread | None = None
        self._quit_requested = False
        self._action: str | None = None  # "scan" to return to CLI scan flow

        # Data (loaded lazily)
        self._drives: list[DriveInfo] | None = None
        self._raid_arrays: list[RAIDArray] | None = None
        self._enclosures: list[EnclosureInfo] | None = None
        self._recovery_plans: list[RecoveryPlan] | None = None

    def _load_data(self) -> None:
        """Load drive, RAID, and enclosure data."""
        self._drives = analyze_drives()
        self._raid_arrays = detect_raid_arrays()
        self._enclosures = detect_enclosures()
        self._recovery_plans = generate_recovery_plans(
            self._raid_arrays, self._enclosures, self._drives,
        )

    def _setup_input(self) -> None:
        """Set up non-blocking keyboard input via cbreak mode."""
        try:
            fd = sys.stdin.fileno()
            self._old_term_settings = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            atexit.register(self._restore_terminal)
        except (termios.error, OSError, ValueError):
            self._old_term_settings = None
            return

        self._input_thread = threading.Thread(target=self._input_loop, daemon=True)
        self._input_thread.start()

    def _restore_terminal(self) -> None:
        """Restore terminal settings."""
        if self._old_term_settings is not None:
            try:
                fd = sys.stdin.fileno()
                termios.tcsetattr(fd, termios.TCSADRAIN, self._old_term_settings)
            except (termios.error, OSError, ValueError):
                pass
            self._old_term_settings = None

    def _input_loop(self) -> None:
        """Read keyboard input in a daemon thread."""
        while not self._quit_requested:
            try:
                ch = sys.stdin.read(1)
            except (OSError, ValueError):
                break

            if ch in ("q", "Q", "\x03"):  # q or Ctrl+C
                self._quit_requested = True
            elif ch in ("1",):
                if self.view == "main":
                    self._action = "scan"
                    self._quit_requested = True
                else:
                    self.view = "main"
            elif ch in ("2",):
                self.view = "drives"
            elif ch in ("3",):
                self.view = "raid"
            elif ch in ("4",):
                self.view = "recovery"
            elif ch in ("5",):
                self.view = "filters"
            elif ch in ("\x1b", "b", "B"):  # Escape or b
                if self.view != "main":
                    self.view = "main"

    def _build_main_view(self) -> Panel:
        """Build the main overview dashboard."""
        drives = self._drives or []
        raids = self._raid_arrays or []
        enclosures = self._enclosures or []

        sections = []

        # System overview
        system_info = Table(show_header=False, box=None, padding=(0, 1))
        system_info.add_column("Key", style="bold", no_wrap=True)
        system_info.add_column("Value")
        system_info.add_row("OS", f"{platform.system()} {platform.release()}")
        system_info.add_row("Drives", str(len(drives)))
        system_info.add_row("RAID Arrays", str(len(raids)))
        system_info.add_row("Enclosures", str(len(enclosures)))
        sections.append(Panel(system_info, title="[bold]System Overview[/bold]", border_style="blue"))

        # Drive summary table
        if drives:
            drive_table = Table(box=None, padding=(0, 1))
            drive_table.add_column("Device", style="cyan", no_wrap=True)
            drive_table.add_column("Mount", no_wrap=True)
            drive_table.add_column("FS", style="green")
            drive_table.add_column("Size", justify="right")
            drive_table.add_column("Free", justify="right")
            drive_table.add_column("Type", style="dim")

            for d in drives[:10]:  # Show top 10 in summary
                drive_table.add_row(
                    d.device or "-",
                    d.mount_point or "-",
                    d.filesystem or "-",
                    d.size or "-",
                    d.free or "-",
                    getattr(d, "connection_type", "") or "",
                )
            if len(drives) > 10:
                drive_table.add_row(f"... and {len(drives) - 10} more", "", "", "", "", "")
            sections.append(Panel(drive_table, title="[bold]Drives[/bold]", border_style="cyan"))

        # RAID status summary
        if raids:
            raid_table = Table(box=None, padding=(0, 1))
            raid_table.add_column("Array", style="bold")
            raid_table.add_column("Level", style="cyan")
            raid_table.add_column("Status")
            raid_table.add_column("Members", justify="right")
            raid_table.add_column("Source", style="dim")

            for arr in raids:
                status_style = {
                    "healthy": "green",
                    "degraded": "yellow",
                    "failed": "red",
                    "rebuilding": "yellow",
                }.get(arr.status, "white")
                raid_table.add_row(
                    arr.name,
                    arr.raid_level,
                    f"[{status_style}]{arr.status.upper()}[/{status_style}]",
                    str(len(arr.members)),
                    arr.source,
                )
            sections.append(Panel(raid_table, title="[bold]RAID Status[/bold]", border_style="yellow"))
        else:
            sections.append(Panel(
                Text.from_markup("[dim]No RAID arrays detected[/dim]"),
                title="[bold]RAID Status[/bold]",
                border_style="yellow",
            ))

        # Menu
        menu = Text.from_markup(
            "  [bold][1][/bold] Start Scan    "
            "[bold][2][/bold] Drives    "
            "[bold][3][/bold] RAID Details    "
            "[bold][4][/bold] Recovery    "
            "[bold][5][/bold] Filters    "
            "[bold][Q][/bold] Quit"
        )
        sections.append(Panel(menu, title="[bold]Menu[/bold]", border_style="white"))

        return Panel(
            Group(*sections),
            title=f"[bold white] DRIVESCAN v{VERSION} [/bold white]",
            border_style="bold white",
        )

    def _build_drives_view(self) -> Panel:
        """Build detailed drives view."""
        drives = self._drives or []
        drive_table = Table(box=None, padding=(0, 1))
        drive_table.add_column("Device", style="cyan", no_wrap=True)
        drive_table.add_column("Mount Point", no_wrap=True)
        drive_table.add_column("Filesystem", style="green")
        drive_table.add_column("Label")
        drive_table.add_column("Size", justify="right")
        drive_table.add_column("Used", justify="right")
        drive_table.add_column("Free", justify="right")
        drive_table.add_column("Boot", style="dim")
        drive_table.add_column("Type", style="dim")

        for d in drives:
            boot = "yes" if getattr(d, "is_boot_disk", False) else ""
            conn = getattr(d, "connection_type", "") or ""
            drive_table.add_row(
                d.device or "-",
                d.mount_point or "-",
                d.filesystem or "-",
                d.label or "-",
                d.size or "-",
                d.used or "-",
                d.free or "-",
                boot,
                conn,
            )

        nav = Text.from_markup("[dim][Esc/B] Back    [Q] Quit[/dim]")
        content = Group(drive_table, Text(""), nav)

        return Panel(
            content,
            title=f"[bold white] DRIVESCAN v{VERSION} - Drives ({len(drives)}) [/bold white]",
            border_style="bold cyan",
        )

    def _build_raid_view(self) -> Panel:
        """Build detailed RAID view."""
        raids = self._raid_arrays or []
        enclosures = self._enclosures or []
        sections = []

        if raids:
            for arr in raids:
                member_table = Table(box=None, padding=(0, 1))
                member_table.add_column("Device", style="cyan")
                member_table.add_column("Role")
                member_table.add_column("Status")

                for m in arr.members:
                    status_style = "green" if m.status == "online" else "red"
                    member_table.add_row(
                        m.device,
                        m.role,
                        f"[{status_style}]{m.status}[/{status_style}]",
                    )

                status_style = {
                    "healthy": "green", "degraded": "yellow",
                    "failed": "red", "rebuilding": "yellow",
                }.get(arr.status, "white")

                info_lines = [
                    f"Level: [cyan]{arr.raid_level}[/cyan]    "
                    f"Status: [{status_style}]{arr.status.upper()}[/{status_style}]    "
                    f"Source: [dim]{arr.source}[/dim]",
                ]
                if arr.total_size:
                    info_lines.append(f"Total: {arr.total_size}    Usable: {arr.usable_capacity or 'N/A'}")

                info_text = Text.from_markup("\n".join(info_lines))
                sections.append(Panel(
                    Group(info_text, Text(""), member_table),
                    title=f"[bold]{arr.name}[/bold]",
                    border_style=status_style,
                ))
        else:
            sections.append(Text.from_markup("[dim]No software RAID arrays detected[/dim]"))

        if enclosures:
            enc_table = Table(box=None, padding=(0, 1))
            enc_table.add_column("Vendor", style="bold")
            enc_table.add_column("Model")
            enc_table.add_column("Connection", style="cyan")
            enc_table.add_column("Inferred RAID")
            enc_table.add_column("Confidence")

            for enc in enclosures:
                conf_style = {"high": "green", "medium": "yellow", "low": "red"}.get(enc.confidence, "white")
                enc_table.add_row(
                    enc.vendor,
                    enc.model,
                    enc.connection,
                    enc.inferred_raid or "N/A",
                    f"[{conf_style}]{enc.confidence}[/{conf_style}]",
                )
            sections.append(Panel(enc_table, title="[bold]Enclosures[/bold]", border_style="magenta"))

        nav = Text.from_markup("[dim][Esc/B] Back    [Q] Quit[/dim]")
        sections.append(nav)

        return Panel(
            Group(*sections),
            title=f"[bold white] DRIVESCAN v{VERSION} - RAID & Enclosures [/bold white]",
            border_style="bold yellow",
        )

    def _build_recovery_view(self) -> Panel:
        """Build recovery guidance view."""
        plans = self._recovery_plans or []
        sections = []

        for plan in plans:
            severity_style = {
                "info": "blue", "warning": "yellow", "critical": "red",
            }.get(plan.severity, "white")

            step_lines = []
            for step in plan.steps:
                step_lines.append(f"  [bold]{step.order}.[/bold] {step.title}")
                step_lines.append(f"     {step.description}")
                if step.command:
                    step_lines.append(f"     [cyan]$ {step.command}[/cyan]")
                if step.warning:
                    step_lines.append(f"     [yellow]Warning: {step.warning}[/yellow]")
                step_lines.append("")

            warning_lines = []
            if plan.warnings:
                warning_lines.append("[bold red]Warnings:[/bold red]")
                for w in plan.warnings:
                    warning_lines.append(f"  [red]! {w}[/red]")

            tool_line = ""
            if plan.recommended_tools:
                tool_line = f"\n[dim]Recommended tools: {', '.join(plan.recommended_tools)}[/dim]"

            content = "\n".join(step_lines + warning_lines) + tool_line
            sections.append(Panel(
                Text.from_markup(content),
                title=f"[bold]{plan.scenario}[/bold]",
                border_style=severity_style,
            ))

        if not plans:
            sections.append(Text.from_markup("[dim]No recovery guidance available[/dim]"))

        nav = Text.from_markup("[dim][Esc/B] Back    [Q] Quit[/dim]")
        sections.append(nav)

        return Panel(
            Group(*sections),
            title=f"[bold white] DRIVESCAN v{VERSION} - Recovery Guidance [/bold white]",
            border_style="bold red",
        )

    def _build_filters_view(self) -> Panel:
        """Build filters listing view."""
        from .filters import discover_filters
        from pathlib import Path

        available = discover_filters()
        config_dir = Path(__file__).parent.parent / "config"

        filter_table = Table(box=None, padding=(0, 1))
        filter_table.add_column("Filter", style="bold")
        filter_table.add_column("Status")
        filter_table.add_column("Description")

        for name, cls in sorted(available.items()):
            instance = cls()
            instance.load_config(config_dir)
            status = "[green]enabled[/green]" if instance.enabled else "[red]disabled[/red]"
            filter_table.add_row(name, status, cls.description)

        if not available:
            filter_table.add_row("[dim]No filters found[/dim]", "", "")

        nav = Text.from_markup("[dim][Esc/B] Back    [Q] Quit[/dim]")
        content = Group(filter_table, Text(""), nav)

        return Panel(
            content,
            title=f"[bold white] DRIVESCAN v{VERSION} - Filters [/bold white]",
            border_style="bold green",
        )

    def _build_display(self) -> Panel:
        """Build the current view."""
        if self.view == "drives":
            return self._build_drives_view()
        elif self.view == "raid":
            return self._build_raid_view()
        elif self.view == "recovery":
            return self._build_recovery_view()
        elif self.view == "filters":
            return self._build_filters_view()
        return self._build_main_view()

    def run(self) -> str | None:
        """Run the dashboard TUI. Returns 'scan' if user chose Start Scan, else None."""
        self.console.print("[dim]Loading drive information...[/dim]")
        self._load_data()

        self._setup_input()
        try:
            with Live(self._build_display(), console=self.console, refresh_per_second=4) as live:
                while not self._quit_requested:
                    live.update(self._build_display())
                    time.sleep(0.25)
        except KeyboardInterrupt:
            pass
        finally:
            self._restore_terminal()

        return self._action
