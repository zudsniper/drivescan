"""Rich-based TUI for live scan progress visualization."""

import atexit
import sys
import termios
import threading
import time
import tty
from collections import deque

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from .filters import BaseFilter
from .scan_state import ScanState


class ScanTUI:
    """Live TUI display for scan progress."""

    def __init__(self, state: ScanState, filters: list[BaseFilter]) -> None:
        self.state = state
        self.filters = filters
        self.console = Console()
        self.recent_matches: deque[dict] = deque(maxlen=8)
        self._ctrl_c_count = 0
        self._ctrl_c_first_time = 0.0
        self._input_thread: threading.Thread | None = None
        self._old_term_settings = None
        self._rate_samples: deque[tuple[float, int]] = deque(maxlen=20)

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
        while not self.state.finished and not self.state.cancel_requested:
            try:
                ch = sys.stdin.read(1)
            except (OSError, ValueError):
                break

            if ch in ("p", "P", " "):
                self.state.toggle_pause()
            elif ch == "\x03":  # Ctrl+C
                self._handle_ctrl_c()

    def _handle_ctrl_c(self) -> None:
        now = time.time()
        if now - self._ctrl_c_first_time > 10:
            self._ctrl_c_count = 0
            self._ctrl_c_first_time = now

        self._ctrl_c_count += 1

        if self._ctrl_c_count >= 4:
            self.state.cancel_requested = True
            # Save state before exit
            state_file = self.state.scan_config.get("state_file")
            if state_file:
                try:
                    from pathlib import Path
                    self.state.save(Path(state_file))
                except OSError:
                    pass
        elif self._ctrl_c_count == 1:
            self._ctrl_c_first_time = now

    def _build_display(self) -> Panel:
        """Build the full TUI layout from current state."""
        state = self.state
        progress = state.progress

        sections = []

        # Header with pause/quit hints
        status = "[bold red]PAUSED[/bold red]" if state.is_paused else "[bold green]SCANNING[/bold green]"
        if self._ctrl_c_count > 0 and self._ctrl_c_count < 4:
            remaining = 4 - self._ctrl_c_count
            status += f"  [yellow]Ctrl+C {remaining} more to quit[/yellow]"

        header = Text.from_markup(
            f" {status}    [dim][P]ause  Ctrl+C x4 quit[/dim]"
        )
        sections.append(header)

        # Drive progress
        drive_table = Table(show_header=False, box=None, padding=(0, 1))
        drive_table.add_column("Info", no_wrap=True, max_width=80)

        path_idx = progress.current_path_index
        total_paths = progress.total_paths
        if total_paths > 0:
            drive_table.add_row(
                f"[cyan]Path {path_idx + 1}/{total_paths}[/cyan]"
            )

        # Bytes progress
        bytes_scanned = progress.bytes_scanned
        if bytes_scanned > 0:
            drive_table.add_row(f"[dim]Scanned: {_fmt_bytes(bytes_scanned)}[/dim]")

        sections.append(Panel(drive_table, title="[bold]Drives[/bold]", border_style="blue"))

        # Progress section
        now = time.time()
        elapsed = (now - progress.start_time - progress.elapsed_paused) if progress.start_time else 0
        files = progress.files_scanned
        estimated = progress.files_estimated

        # Calculate rate
        self._rate_samples.append((now, files))
        rate = 0.0
        if len(self._rate_samples) >= 2:
            t0, f0 = self._rate_samples[0]
            t1, f1 = self._rate_samples[-1]
            dt = t1 - t0
            if dt > 0:
                rate = (f1 - f0) / dt

        # ETA
        eta_str = "?"
        if rate > 0 and estimated > files:
            eta_secs = (estimated - files) / rate
            if eta_secs < 60:
                eta_str = f"~{int(eta_secs)}s"
            elif eta_secs < 3600:
                eta_str = f"~{int(eta_secs / 60)}m {int(eta_secs % 60)}s"
            else:
                eta_str = f"~{int(eta_secs / 3600)}h {int((eta_secs % 3600) / 60)}m"

        pct = min(100, int(files / estimated * 100)) if estimated > 0 else 0

        progress_lines = []
        if estimated > 0:
            progress_lines.append(
                f"Files: [bold]{files:,}[/bold] / ~{estimated:,}  "
                f"Rate: [bold]{rate:,.0f}[/bold]/s  ETA: {eta_str}"
            )
        else:
            progress_lines.append(
                f"Files: [bold]{files:,}[/bold]  Rate: [bold]{rate:,.0f}[/bold]/s"
            )

        elapsed_str = _fmt_duration(elapsed)
        paused_str = _fmt_duration(progress.elapsed_paused) if progress.elapsed_paused > 0 else "0s"
        progress_lines.append(
            f"Elapsed: {elapsed_str}  Paused: {paused_str}  Errors: {progress.errors}"
        )

        # Current file (truncated)
        current = progress.current_file
        if len(current) > 70:
            current = "..." + current[-67:]
        progress_lines.append(f"[dim]{current}[/dim]")

        progress_text = "\n".join(progress_lines)

        # Add progress bar
        if estimated > 0:
            bar = ProgressBar(total=100, completed=pct, width=40)
            progress_panel_content = Text.from_markup(progress_text)
        else:
            progress_panel_content = Text.from_markup(progress_text)

        sections.append(Panel(progress_panel_content, title="[bold]Progress[/bold]", border_style="green"))

        # Matches section
        match_lines = []
        with state.lock:
            for filt in self.filters:
                count = progress.matches_per_filter.get(filt.name, 0)
                summary = filt.summary() if count > 0 else "no matches yet"
                match_lines.append(f"[bold]{filt.name}[/bold]: {count:>5}  ({summary})")

        sections.append(
            Panel(
                Text.from_markup("\n".join(match_lines) if match_lines else "[dim]waiting...[/dim]"),
                title="[bold]Matches[/bold]",
                border_style="yellow",
            )
        )

        # Recent matches section
        with state.lock:
            # Collect new matches into recent deque
            for filter_name, matches in state.matches.items():
                for m in matches[-8:]:
                    key = m.get("path", "")
                    # Only add if not already in recent
                    if not any(r.get("path") == key for r in self.recent_matches):
                        self.recent_matches.append({**m, "_filter": filter_name})

        recent_lines = []
        for m in list(self.recent_matches)[-8:]:
            fname = m.get("_filter", "?")
            path = m.get("path", "?")
            mtype = m.get("type") or m.get("wallet_type", "?")
            confidence = m.get("confidence", "")
            conf_color = {"high": "green", "medium": "yellow", "low": "red"}.get(confidence, "white")

            # Truncate path
            if len(path) > 55:
                path = "..." + path[-52:]

            line = f"[cyan][{fname}][/cyan] {path}  [bold]{mtype}[/bold]"
            if confidence:
                line += f"  [{conf_color}]{confidence.upper()}[/{conf_color}]"
            recent_lines.append(line)

        sections.append(
            Panel(
                Text.from_markup("\n".join(recent_lines) if recent_lines else "[dim]no matches yet[/dim]"),
                title="[bold]Recent[/bold]",
                border_style="magenta",
            )
        )

        # Combine into main panel
        from rich.console import Group
        return Panel(
            Group(*sections),
            title="[bold white] DRIVESCAN [/bold white]",
            border_style="bold white",
        )

    def run(self, scanner_thread: threading.Thread) -> None:
        """Run the TUI until scan completes or is cancelled."""
        self._setup_input()

        try:
            with Live(self._build_display(), console=self.console, refresh_per_second=4) as live:
                while scanner_thread.is_alive() and not self.state.cancel_requested:
                    live.update(self._build_display())
                    time.sleep(0.25)

                # Final update
                live.update(self._build_display())
        except KeyboardInterrupt:
            self.state.cancel_requested = True
        finally:
            self._restore_terminal()

        # Print final summary
        self._print_summary()

    def _print_summary(self) -> None:
        """Print final scan results."""
        state = self.state
        progress = state.progress

        self.console.print()
        self.console.rule("[bold]Scan Complete[/bold]" if state.finished else "[bold yellow]Scan Interrupted[/bold yellow]")
        self.console.print()

        for filt in self.filters:
            count = progress.matches_per_filter.get(filt.name, 0)
            self.console.print(f"  [bold]{filt.name}[/bold]: {filt.summary()}")
        self.console.print()

        elapsed = 0.0
        if progress.start_time:
            elapsed = time.time() - progress.start_time - progress.elapsed_paused
        self.console.print(f"  Files scanned: [bold]{progress.files_scanned:,}[/bold]")
        self.console.print(f"  Elapsed: {_fmt_duration(elapsed)}")
        if progress.errors:
            self.console.print(f"  Errors: [yellow]{progress.errors:,}[/yellow]")

        if not state.finished:
            state_file = state.scan_config.get("state_file")
            if state_file:
                self.console.print(f"\n  [dim]State saved to {state_file}[/dim]")
                self.console.print("  [dim]Resume with: drivescan scan --resume[/dim]")
        self.console.print()


def _fmt_bytes(n: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _fmt_duration(secs: float) -> str:
    if secs < 60:
        return f"{int(secs)}s"
    elif secs < 3600:
        return f"{int(secs / 60)}m {int(secs % 60)}s"
    else:
        return f"{int(secs / 3600)}h {int((secs % 3600) / 60)}m"
