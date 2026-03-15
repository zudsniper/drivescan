"""Typer CLI entry point for drive-scanner."""

import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
import yaml

from . import colors
from .drive_analyzer import analyze_drives, print_drive_table
from .filters import discover_filters
from .scanner import scan_paths

app = typer.Typer(
    name="drive-scanner",
    help="Analyze drives and scan for files matching configurable filter rules.",
    no_args_is_help=True,
)

DEFAULT_CONFIG_DIR = Path(__file__).parent.parent / "config"


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
):
    """Scan paths with selected filters to find matching files."""
    if no_color:
        colors.disable()

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
        scan_targets = [
            Path(d.mount_point)
            for d in drive_list
            if d.mount_point and d.mount_point != "/"
        ]
        if not scan_targets:
            colors.warn("No non-root mount points found. Use --path to specify a scan target.")
            raise typer.Exit(1)

    scan_paths(scan_targets, active_filters, max_depth=max_depth, verbose=verbose, output_file=output)


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


def main():
    app()


if __name__ == "__main__":
    main()
