"""Core file scanning engine — backwards-compatible wrapper around scan_engine."""

import json
import time
from pathlib import Path

from . import colors
from .filters import BaseFilter
from .scan_engine import run_scan
from .scan_state import ScanState


def scan_paths(
    paths: list[Path],
    filters: list[BaseFilter],
    max_depth: int | None = None,
    verbose: bool = False,
    output_file: Path | None = None,
) -> dict:
    """Scan given paths with the provided filters.

    Returns a summary dict with scan statistics and all matches.
    """
    colors.header("Scanning")
    filter_names = ", ".join(f.name for f in filters)
    colors.info(f"Active filters: {filter_names}")
    colors.info(f"Scanning {len(paths)} path(s)...")
    print()

    # Validate paths
    valid_paths = []
    for scan_path in paths:
        if not scan_path.exists():
            colors.warn(f"Path does not exist: {scan_path}")
        elif not scan_path.is_dir():
            colors.warn(f"Not a directory: {scan_path}")
        else:
            colors.info(f"Scanning: {scan_path}")
            valid_paths.append(scan_path)

    # Create state and run scan synchronously
    state = ScanState()
    run_scan(state, valid_paths, filters, max_depth=max_depth)

    elapsed = time.time() - state.progress.start_time if state.progress.start_time else 0.0
    total_files = state.progress.files_scanned
    total_errors = state.progress.errors
    total_matches = sum(state.progress.matches_per_filter.values())

    # Print summaries
    colors.header("Results")
    for filt in filters:
        label = colors.bold(filt.name)
        print(f"  {label}: {filt.summary()}")
    print()
    colors.info(f"Scanned {total_files:,} files in {elapsed:.1f}s")
    colors.info(f"Total matches: {total_matches:,}")
    if total_errors:
        colors.warn(f"Errors (permission/read): {total_errors:,}")

    # Collect results
    all_results = {}
    for filt in filters:
        all_results[filt.name] = filt.matches

    if output_file:
        output_data = {
            "scan_paths": [str(p) for p in paths],
            "filters": [f.name for f in filters],
            "total_files_scanned": total_files,
            "total_matches": total_matches,
            "elapsed_seconds": round(elapsed, 2),
            "results": all_results,
        }
        with open(output_file, "w") as f:
            json.dump(output_data, f, indent=2)
        colors.success(f"Results saved to {output_file}")

    return {
        "total_files": total_files,
        "total_matches": total_matches,
        "total_errors": total_errors,
        "elapsed": elapsed,
        "results": all_results,
    }


def _print_match(filt: BaseFilter, result: dict) -> None:
    path = result.get("path", "?")
    match_type = result.get("type") or result.get("wallet_type", "?")
    confidence = result.get("confidence")

    prefix = colors.green(f"  [{filt.name}]")
    detail = colors.bold(match_type)

    extra = ""
    if confidence:
        conf_color = {"high": colors.green, "medium": colors.yellow, "low": colors.red}.get(
            confidence, str
        )
        extra = f" confidence={conf_color(confidence)}"

    reason = result.get("match_reason", "")
    if reason:
        extra += f" ({reason})"

    print(f"{prefix} {path}  {detail}{extra}")
