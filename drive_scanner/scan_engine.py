"""Threaded scan engine with pause/resume support."""

import os
import time
from collections import deque
from pathlib import Path

from .filters import BaseFilter
from .scan_state import ScanState

# Hidden dirs to skip, except known crypto wallet dirs
CRYPTO_DIRS = {
    ".bitcoin", ".litecoin", ".dogecoin", ".electrum",
    ".armory", ".multibit", ".multibit-hd",
    ".ethereum", ".monero",
}


def _estimate_files(paths: list[Path], max_sample_dirs: int = 100) -> int:
    """Quick pre-scan to estimate total file count."""
    total_dirs = 0
    total_files = 0
    sampled_dirs = 0

    for scan_path in paths:
        if not scan_path.is_dir():
            continue
        try:
            for entry in os.scandir(scan_path):
                if entry.is_dir(follow_symlinks=False):
                    total_dirs += 1
        except OSError:
            pass

        for dirpath, dirnames, filenames in os.walk(scan_path, onerror=lambda e: None):
            sampled_dirs += 1
            total_files += len(filenames)
            if sampled_dirs >= max_sample_dirs:
                break
            # Filter dirs same as main scan
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") or d.lower() in CRYPTO_DIRS
            ]
        if sampled_dirs >= max_sample_dirs:
            break

    if sampled_dirs == 0:
        return 0

    avg_files_per_dir = total_files / sampled_dirs
    # Rough estimate: assume similar density across all dirs
    # Use a multiplier based on observed depth
    estimated_total_dirs = max(total_dirs * 10, sampled_dirs * 5)
    return int(avg_files_per_dir * estimated_total_dirs)


def run_scan(
    state: ScanState,
    paths: list[Path],
    filters: list[BaseFilter],
    max_depth: int | None = None,
) -> None:
    """Run the scan in the calling thread. Designed for use as a thread target.

    Updates state.progress throughout. Respects pause_event and cancel_requested.
    """
    state.progress.start_time = time.time()
    state.progress.total_paths = len(paths)

    # Estimate file count in background-friendly way
    if state.progress.files_estimated == 0:
        state.progress.files_estimated = _estimate_files(paths)

    # Rate tracking: rolling window
    rate_window: deque[tuple[float, int]] = deque(maxlen=50)

    for path_idx, scan_path in enumerate(paths):
        state.progress.current_path_index = path_idx

        if not scan_path.exists() or not scan_path.is_dir():
            continue

        if state.cancel_requested:
            break

        for dirpath, dirnames, filenames in os.walk(scan_path, onerror=lambda e: None):
            if state.cancel_requested:
                break

            dir_str = dirpath

            # Skip completed dirs (resume support)
            if dir_str in state.completed_dirs:
                dirnames.clear()
                continue

            current = Path(dirpath)

            # Enforce max depth
            if max_depth is not None:
                try:
                    depth = len(current.relative_to(scan_path).parts)
                    if depth >= max_depth:
                        dirnames.clear()
                        continue
                except ValueError:
                    continue

            # Skip hidden dirs (except crypto)
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") or d.lower() in CRYPTO_DIRS
            ]

            for filename in filenames:
                # Pause support
                state.pause_event.wait()

                if state.cancel_requested:
                    break

                file_path = current / filename

                with state.lock:
                    state.progress.files_scanned += 1
                    state.progress.current_file = str(file_path)

                try:
                    stat = file_path.lstat()
                except OSError:
                    with state.lock:
                        state.progress.errors += 1
                    continue

                # Skip symlinks
                if os.path.islink(file_path):
                    continue

                with state.lock:
                    state.progress.bytes_scanned += stat.st_size

                for filt in filters:
                    try:
                        result = filt.match(file_path, stat)
                    except Exception:
                        with state.lock:
                            state.progress.errors += 1
                        continue

                    if result:
                        with state.lock:
                            filter_name = filt.name
                            if filter_name not in state.matches:
                                state.matches[filter_name] = []
                            state.matches[filter_name].append(result)
                            state.progress.matches_per_filter[filter_name] = (
                                state.progress.matches_per_filter.get(filter_name, 0) + 1
                            )

                # Rate tracking
                now = time.time()
                rate_window.append((now, state.progress.files_scanned))

            # Dir completed
            with state.lock:
                state.completed_dirs.add(dir_str)
                state.current_dir_files_done.clear()
            state.increment_dir_counter()

            # Refine estimate based on actual observed ratio
            if state.progress.files_scanned > 0 and len(state.completed_dirs) > 50:
                avg = state.progress.files_scanned / len(state.completed_dirs)
                # Simple heuristic refinement
                state.progress.files_estimated = max(
                    state.progress.files_scanned,
                    int(avg * len(state.completed_dirs) * 1.5),
                )

            # Checkpoint
            if state.should_checkpoint():
                state_file = state.scan_config.get("state_file")
                if state_file:
                    try:
                        state.save(Path(state_file))
                    except OSError:
                        pass
                state.mark_checkpoint_done()

    state.finished = True

    # Final save
    state_file = state.scan_config.get("state_file")
    if state_file:
        try:
            state.save(Path(state_file))
        except OSError:
            pass
