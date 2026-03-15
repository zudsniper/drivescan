"""Core file scanning engine."""

import json
import os
import time
from pathlib import Path

from . import colors
from .filters import BaseFilter


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
    total_files = 0
    total_matches = 0
    total_errors = 0
    start_time = time.time()

    colors.header("Scanning")
    filter_names = ", ".join(f.name for f in filters)
    colors.info(f"Active filters: {filter_names}")
    colors.info(f"Scanning {len(paths)} path(s)...")
    print()

    for scan_path in paths:
        if not scan_path.exists():
            colors.warn(f"Path does not exist: {scan_path}")
            continue
        if not scan_path.is_dir():
            colors.warn(f"Not a directory: {scan_path}")
            continue

        colors.info(f"Scanning: {scan_path}")

        for dirpath, dirnames, filenames in os.walk(scan_path, onerror=lambda e: None):
            current = Path(dirpath)

            # Enforce max depth
            if max_depth is not None:
                depth = len(current.relative_to(scan_path).parts)
                if depth >= max_depth:
                    dirnames.clear()
                    continue

            # Skip system/hidden dirs that are unlikely to contain user files
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".")
                or d.lower() in {
                    ".bitcoin", ".litecoin", ".dogecoin", ".electrum",
                    ".armory", ".multibit", ".multibit-hd",
                    ".ethereum", ".monero",
                }
            ]

            for filename in filenames:
                file_path = current / filename
                total_files += 1

                if verbose and total_files % 10000 == 0:
                    colors.info(f"  ...scanned {total_files} files")

                try:
                    stat = file_path.lstat()
                except OSError:
                    total_errors += 1
                    continue

                # Skip symlinks
                if os.path.islink(file_path):
                    continue

                for filt in filters:
                    try:
                        result = filt.match(file_path, stat)
                    except Exception:
                        total_errors += 1
                        continue

                    if result:
                        total_matches += 1
                        _print_match(filt, result)

    elapsed = time.time() - start_time

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

    # Write JSON output
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
