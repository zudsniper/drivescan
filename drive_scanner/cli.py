"""Typer CLI entry point for drive-scanner."""

import sys
import threading
from pathlib import Path
from typing import Annotated, Optional

import typer
import yaml

from . import colors
from .drive_analyzer import analyze_drives, get_root_device_prefix, print_drive_table, snapshot_drives
from .filters import discover_filters
from .scanner import scan_paths

def _is_interactive() -> bool:
    import os
    try:
        return os.isatty(sys.stdin.fileno()) and os.isatty(sys.stdout.fileno())
    except (AttributeError, OSError):
        return sys.stdout.isatty()


app = typer.Typer(
    name="drive-scanner",
    help="Analyze drives and scan for files matching configurable filter rules.",
    no_args_is_help=False,
    invoke_without_command=True,
)

DEFAULT_CONFIG_DIR = Path(__file__).parent.parent / "config"


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context):
    """Launch interactive dashboard when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return
    if _is_interactive():
        from .dashboard import DashboardTUI
        dashboard = DashboardTUI()
        action = dashboard.run()
        if action == "scan":
            # Invoke the scan command
            ctx.invoke(scan)
    else:
        # Non-interactive: show help
        print(ctx.get_help())


@app.command()
def drives():
    """Analyze and list all attached drives with filesystem info."""
    drive_list = analyze_drives()
    print_drive_table(drive_list)


@app.command()
def scan(
    path: Annotated[
        Optional[list[Path]], typer.Option("--path", "-p", help="Path(s) to scan")
    ] = None,
    filter_name: Annotated[
        Optional[list[str]], typer.Option("--filter", "-f", help="Filter(s) to use")
    ] = None,
    config_dir: Annotated[
        Path, typer.Option("--config-dir", help="Config directory")
    ] = DEFAULT_CONFIG_DIR,
    output: Annotated[
        Optional[Path], typer.Option("--output", "-o", help="Save results to JSON file")
    ] = None,
    max_depth: Annotated[
        Optional[int], typer.Option("--max-depth", help="Max directory recursion depth")
    ] = None,
    no_color: Annotated[
        bool, typer.Option("--no-color", help="Disable colored output")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show progress during scan")
    ] = False,
    no_tui: Annotated[
        bool, typer.Option("--no-tui", help="Disable TUI, use legacy text output")
    ] = False,
    resume: Annotated[
        bool, typer.Option("--resume", "-r", help="Resume previous scan from checkpoint")
    ] = False,
    include_boot: Annotated[
        bool, typer.Option("--include-boot", help="Include boot disk in auto-detected scan targets")
    ] = False,
    state_dir: Annotated[
        Optional[Path], typer.Option("--state-dir", help="Override state directory")
    ] = None,
):
    """Scan paths with selected filters to find matching files."""
    if no_color:
        colors.disable()

    # Auto-disable TUI if not a TTY
    use_tui = not no_tui and _is_interactive()

    # Handle resume
    if resume:
        _handle_resume(config_dir, output, max_depth, use_tui, state_dir)
        return

    # Discover and select filters
    available = discover_filters()
    if not available:
        colors.error("No filters found.")
        raise typer.Exit(1)

    if filter_name:
        selected_names = []
        for name in filter_name:
            if name not in available:
                colors.error(f"Unknown filter: {name}")
                colors.info(f"Available: {', '.join(available.keys())}")
                raise typer.Exit(1)
            selected_names.append(name)
    else:
        selected_names = list(available.keys())

    # Instantiate and configure filters
    active_filters = []
    for name in selected_names:
        filt = available[name]()
        filt.load_config(config_dir)
        if filt.enabled:
            active_filters.append(filt)
        else:
            colors.info(f"Filter '{name}' is disabled in config, skipping.")

    if not active_filters:
        colors.error("No active filters.")
        raise typer.Exit(1)

    # Determine scan paths
    if path:
        scan_targets = path
    else:
        # Default: scan all mounted volumes
        colors.info("No paths specified, detecting mounted drives...")
        drive_list = analyze_drives()
        print_drive_table(drive_list)
        root_prefix = None if include_boot else get_root_device_prefix()
        scan_targets = [
            Path(d.mount_point)
            for d in drive_list
            if d.mount_point
            and d.mount_point != "/"
            and (root_prefix is None or not d.device.startswith(root_prefix))
        ]
        if not scan_targets:
            colors.warn("No non-root mount points found. Use --path to specify a scan target.")
            raise typer.Exit(1)

    if use_tui:
        _run_with_tui(scan_targets, active_filters, max_depth, output, state_dir)
    else:
        scan_paths(scan_targets, active_filters, max_depth=max_depth, verbose=verbose, output_file=output)


def _run_with_tui(
    scan_targets: list[Path],
    active_filters: list,
    max_depth: int | None,
    output: Path | None,
    state_dir: Path | None,
) -> None:
    """Run scan with Rich TUI display."""
    import json

    from .scan_engine import run_scan
    from .scan_state import ScanState
    from .tui import ScanTUI

    state = ScanState()
    state_file = ScanState.state_file_for_paths(scan_targets, state_dir)
    state.scan_config = {
        "paths": [str(p) for p in scan_targets],
        "max_depth": max_depth,
        "state_file": str(state_file),
    }

    # Take drive snapshot
    state.drive_snapshots = snapshot_drives()

    scanner_thread = threading.Thread(
        target=run_scan,
        args=(state, scan_targets, active_filters, max_depth),
        daemon=True,
    )
    scanner_thread.start()

    tui = ScanTUI(state, active_filters)
    tui.run(scanner_thread)

    # Write JSON output if requested
    if output:
        all_results = {}
        for filt in active_filters:
            all_results[filt.name] = filt.matches

        output_data = {
            "scan_paths": [str(p) for p in scan_targets],
            "filters": [f.name for f in active_filters],
            "total_files_scanned": state.progress.files_scanned,
            "total_matches": sum(state.progress.matches_per_filter.values()),
            "elapsed_seconds": round(
                ((__import__("time").time() - state.progress.start_time - state.progress.elapsed_paused)
                 if state.progress.start_time else 0), 2
            ),
            "results": all_results,
        }
        with open(output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"Results saved to {output}")


def _handle_resume(
    config_dir: Path,
    output: Path | None,
    max_depth: int | None,
    use_tui: bool,
    state_dir: Path | None,
) -> None:
    """Handle --resume flag: load state, detect drive changes, continue scan."""
    from .drive_change import detect_drive_changes
    from .scan_engine import run_scan
    from .scan_state import ScanState

    # Find latest state file
    state_file = ScanState.find_latest_state(state_dir)
    if state_file is None:
        colors.error("No saved scan state found. Run a scan first.")
        raise typer.Exit(1)

    colors.info(f"Loading state from {state_file}")
    state = ScanState.load(state_file)
    state.scan_config["state_file"] = str(state_file)

    # Detect drive changes
    current_drives = snapshot_drives()
    if state.drive_snapshots:
        from .drive_change import detect_drive_changes
        report = detect_drive_changes(state.drive_snapshots, current_drives)
        if report.has_changes:
            report.print_report()
            scan_paths_list = [Path(p) for p in state.scan_config.get("paths", [])]
            if not report.can_continue(scan_paths_list):
                colors.error("Some scan paths are no longer accessible.")
                if not typer.confirm("Continue anyway?"):
                    raise typer.Exit(1)
    state.drive_snapshots = current_drives

    # Reconstruct filters and restore matches
    available = discover_filters()
    active_filters = []
    for name in available:
        filt = available[name]()
        filt.load_config(config_dir)
        if filt.enabled:
            if name in state.matches:
                filt.restore_matches(state.matches[name])
            active_filters.append(filt)

    if not active_filters:
        colors.error("No active filters.")
        raise typer.Exit(1)

    scan_targets = [Path(p) for p in state.scan_config.get("paths", [])]
    resolved_max_depth = max_depth or state.scan_config.get("max_depth")

    # Reset runtime state for continuation
    state.pause_event.set()
    state.cancel_requested = False
    state.finished = False

    colors.info(f"Resuming scan: {state.progress.files_scanned:,} files already scanned, "
                f"{len(state.completed_dirs):,} dirs completed")

    if use_tui:
        import threading
        from .tui import ScanTUI

        scanner_thread = threading.Thread(
            target=run_scan,
            args=(state, scan_targets, active_filters, resolved_max_depth),
            daemon=True,
        )
        scanner_thread.start()

        tui = ScanTUI(state, active_filters)
        tui.run(scanner_thread)
    else:
        run_scan(state, scan_targets, active_filters, max_depth=resolved_max_depth)

        # Print summary
        colors.header("Results")
        for filt in active_filters:
            label = colors.bold(filt.name)
            print(f"  {label}: {filt.summary()}")
        print()
        colors.info(f"Scanned {state.progress.files_scanned:,} files")
        total_matches = sum(state.progress.matches_per_filter.values())
        colors.info(f"Total matches: {total_matches:,}")
        if state.progress.errors:
            colors.warn(f"Errors: {state.progress.errors:,}")


@app.command("resume")
def resume_scan(
    state_dir: Annotated[
        Optional[Path], typer.Option("--state-dir", help="Override state directory")
    ] = None,
    config_dir: Annotated[
        Path, typer.Option("--config-dir", help="Config directory")
    ] = DEFAULT_CONFIG_DIR,
    no_tui: Annotated[
        bool, typer.Option("--no-tui", help="Disable TUI, use legacy text output")
    ] = False,
):
    """Resume the most recent interrupted scan."""
    use_tui = not no_tui and _is_interactive()
    _handle_resume(config_dir, None, None, use_tui, state_dir)


@app.command("filters")
def list_filters(
    config_dir: Annotated[
        Path, typer.Option("--config-dir", help="Config directory")
    ] = DEFAULT_CONFIG_DIR,
):
    """List all available filters and their status."""
    available = discover_filters()
    if not available:
        colors.warn("No filters found.")
        return

    colors.header("Available Filters")
    for name, cls in sorted(available.items()):
        instance = cls()
        instance.load_config(config_dir)
        status = colors.green("enabled") if instance.enabled else colors.red("disabled")
        print(f"  {colors.bold(name):30s}  {status}  — {cls.description}")
    print()


@app.command()
def config(
    filter_name: Annotated[str, typer.Argument(help="Filter name to show config for")],
    config_dir: Annotated[
        Path, typer.Option("--config-dir", help="Config directory")
    ] = DEFAULT_CONFIG_DIR,
):
    """Show current config for a given filter."""
    config_file = config_dir / f"{filter_name}.yaml"
    if not config_file.exists():
        colors.warn(f"No config file found at {config_file}")
        colors.info("Using built-in defaults for this filter.")
        return

    colors.header(f"Config: {filter_name}")
    with open(config_file) as f:
        content = f.read()
    print(content)


@app.command()
def raid():
    """Show detected RAID arrays and external enclosures."""
    from rich.console import Console
    from rich.table import Table

    from .enclosure_detector import detect_enclosures
    from .raid_detector import detect_raid_arrays

    console = Console()

    arrays = detect_raid_arrays()
    enclosures = detect_enclosures()

    if arrays:
        table = Table(title="RAID Arrays")
        table.add_column("Array", style="bold")
        table.add_column("Level", style="cyan")
        table.add_column("Status")
        table.add_column("Members", justify="right")
        table.add_column("Size")
        table.add_column("Source", style="dim")

        for arr in arrays:
            status_style = {
                "healthy": "green", "degraded": "yellow",
                "failed": "red", "rebuilding": "yellow",
            }.get(arr.status, "white")
            table.add_row(
                arr.name,
                arr.raid_level,
                f"[{status_style}]{arr.status.upper()}[/{status_style}]",
                str(len(arr.members)),
                arr.usable_capacity or arr.total_size or "-",
                arr.source,
            )

        console.print(table)
        console.print()

        # Show members for each array
        for arr in arrays:
            if arr.members:
                mtable = Table(title=f"{arr.name} Members")
                mtable.add_column("Device", style="cyan")
                mtable.add_column("Role")
                mtable.add_column("Status")
                for m in arr.members:
                    status_style = "green" if m.status == "online" else "red"
                    mtable.add_row(m.device, m.role, f"[{status_style}]{m.status}[/{status_style}]")
                console.print(mtable)
                console.print()
    else:
        console.print("[dim]No RAID arrays detected.[/dim]")
        console.print()

    if enclosures:
        etable = Table(title="External Enclosures")
        etable.add_column("Vendor", style="bold")
        etable.add_column("Model")
        etable.add_column("Connection", style="cyan")
        etable.add_column("Inferred RAID")
        etable.add_column("Confidence")
        etable.add_column("Reasoning", style="dim")

        for enc in enclosures:
            conf_style = {"high": "green", "medium": "yellow", "low": "red"}.get(enc.confidence, "white")
            etable.add_row(
                enc.vendor,
                enc.model,
                enc.connection,
                enc.inferred_raid or "N/A",
                f"[{conf_style}]{enc.confidence}[/{conf_style}]",
                enc.reasoning,
            )
        console.print(etable)
    else:
        console.print("[dim]No external enclosures detected.[/dim]")
    console.print()


@app.command()
def recovery():
    """Show data recovery guidance based on detected RAID and enclosures."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    from .drive_analyzer import analyze_drives
    from .enclosure_detector import detect_enclosures
    from .raid_detector import detect_raid_arrays
    from .recovery import generate_recovery_plans

    console = Console()
    console.print("[dim]Analyzing system...[/dim]")

    arrays = detect_raid_arrays()
    enclosures = detect_enclosures()
    drives = analyze_drives()
    plans = generate_recovery_plans(arrays, enclosures, drives)

    for plan in plans:
        severity_style = {
            "info": "blue", "warning": "yellow", "critical": "red",
        }.get(plan.severity, "white")

        lines = []
        for step in plan.steps:
            lines.append(f"  [bold]{step.order}.[/bold] {step.title}")
            lines.append(f"     {step.description}")
            if step.command:
                lines.append(f"     [cyan]$ {step.command}[/cyan]")
            if step.warning:
                lines.append(f"     [yellow]Warning: {step.warning}[/yellow]")
            lines.append("")

        if plan.warnings:
            lines.append("[bold red]Warnings:[/bold red]")
            for w in plan.warnings:
                lines.append(f"  [red]! {w}[/red]")

        if plan.recommended_tools:
            lines.append(f"\n[dim]Recommended tools: {', '.join(plan.recommended_tools)}[/dim]")

        console.print(Panel(
            Text.from_markup("\n".join(lines)),
            title=f"[bold]{plan.scenario}[/bold]",
            border_style=severity_style,
        ))
        console.print()


def main():
    app()


if __name__ == "__main__":
    main()
