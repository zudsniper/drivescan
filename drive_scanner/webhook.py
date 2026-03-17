"""Discord webhook notifications using only stdlib."""

import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

# Error notification thresholds
_ERROR_THRESHOLDS = [100, 500, 1000]
_ERROR_REPEAT_INTERVAL = 1000

# Track last notified error count per-process
_last_error_notify = 0
_error_lock = threading.Lock()


def send_webhook(url: str, *, content: str | None = None, embed: dict | None = None) -> None:
    """Fire-and-forget POST to a Discord webhook URL. Runs in a daemon thread."""
    if not url:
        return

    payload: dict[str, Any] = {}
    if content:
        payload["content"] = content
    if embed:
        payload["embeds"] = [embed]

    def _post():
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # fire and forget

    t = threading.Thread(target=_post, daemon=True)
    t.start()


def resolve_url(explicit: str | None = None) -> str | None:
    """Return webhook URL from explicit arg or DRIVESCAN_WEBHOOK_URL env var."""
    if explicit:
        return explicit
    return os.environ.get("DRIVESCAN_WEBHOOK_URL")


def notify_scan_start(url: str | None, paths: list, filters: list[str]) -> None:
    """Send a scan-started notification (blue embed)."""
    url = resolve_url(url)
    if not url:
        return

    path_list = "\n".join(f"• `{p}`" for p in paths[:10])
    if len(paths) > 10:
        path_list += f"\n• ... and {len(paths) - 10} more"

    filter_list = ", ".join(f"`{f}`" for f in filters)

    embed = {
        "title": "🔍 Scan Started",
        "color": 0x3498DB,  # blue
        "fields": [
            {"name": "Paths", "value": path_list, "inline": False},
            {"name": "Filters", "value": filter_list, "inline": False},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    send_webhook(url, embed=embed)


def notify_scan_complete(
    url: str | None,
    files_scanned: int,
    matches: int,
    errors: int,
    elapsed: float,
    matches_per_filter: dict[str, int],
) -> None:
    """Send a scan-complete notification (green embed)."""
    url = resolve_url(url)
    if not url:
        return

    elapsed_str = _format_duration(elapsed)
    filter_breakdown = "\n".join(
        f"• **{name}**: {count:,}" for name, count in matches_per_filter.items()
    ) or "None"

    embed = {
        "title": "✅ Scan Complete",
        "color": 0x2ECC71,  # green
        "fields": [
            {"name": "Files Scanned", "value": f"{files_scanned:,}", "inline": True},
            {"name": "Total Matches", "value": f"{matches:,}", "inline": True},
            {"name": "Errors", "value": f"{errors:,}", "inline": True},
            {"name": "Elapsed", "value": elapsed_str, "inline": True},
            {"name": "Matches by Filter", "value": filter_breakdown, "inline": False},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    send_webhook(url, embed=embed)


def notify_scan_error(
    url: str | None,
    error_count: int,
    files_scanned: int,
    current_file: str,
) -> None:
    """Send an error-threshold notification (red embed). Throttled."""
    url = resolve_url(url)
    if not url:
        return

    embed = {
        "title": "⚠️ Error Threshold Reached",
        "color": 0xE74C3C,  # red
        "fields": [
            {"name": "Errors So Far", "value": f"{error_count:,}", "inline": True},
            {"name": "Files Scanned", "value": f"{files_scanned:,}", "inline": True},
            {"name": "Current File", "value": f"`{current_file[:200]}`", "inline": False},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    send_webhook(url, embed=embed)


def notify_scan_interrupted(
    url: str | None,
    files_scanned: int,
    matches: int,
    state_file: str | None,
) -> None:
    """Send a scan-interrupted notification (yellow embed)."""
    url = resolve_url(url)
    if not url:
        return

    fields = [
        {"name": "Files Scanned", "value": f"{files_scanned:,}", "inline": True},
        {"name": "Matches", "value": f"{matches:,}", "inline": True},
    ]
    if state_file:
        fields.append({"name": "State Saved", "value": f"`{state_file}`", "inline": False})
        fields.append({"name": "Resume", "value": "`drivescan resume`", "inline": False})

    embed = {
        "title": "⏸️ Scan Interrupted",
        "color": 0xF39C12,  # yellow
        "fields": fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    send_webhook(url, embed=embed)


def maybe_notify_errors(state) -> None:
    """Check if error count crossed a threshold and send notification if so.

    Thresholds: 100, 500, 1000, then every 1000 after that.
    """
    global _last_error_notify

    url = state.scan_config.get("webhook_url")
    if not url:
        return

    error_count = state.progress.errors

    with _error_lock:
        should_notify = False

        for threshold in _ERROR_THRESHOLDS:
            if error_count >= threshold > _last_error_notify:
                should_notify = True
                break

        if not should_notify and error_count >= 1000:
            last_milestone = (_last_error_notify // _ERROR_REPEAT_INTERVAL) * _ERROR_REPEAT_INTERVAL
            current_milestone = (error_count // _ERROR_REPEAT_INTERVAL) * _ERROR_REPEAT_INTERVAL
            if current_milestone > last_milestone:
                should_notify = True

        if should_notify:
            _last_error_notify = error_count

    if should_notify:
        notify_scan_error(url, error_count, state.progress.files_scanned, state.progress.current_file)


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"
