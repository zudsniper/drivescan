# drivescan

CLI tool to analyze attached drives and scan filesystems for files matching configurable filter rules. Features a Rich-based TUI with live progress, pause/resume support, and drive change detection.

## Quick Install

```bash
curl -fsSL https://raw.githubusercontent.com/zudsniper/drivescan/main/install.sh | bash
```

Or clone and install manually:

```bash
git clone https://github.com/zudsniper/drivescan.git
cd drivescan
pip install .
```

## What it does

`drivescan` enumerates all attached drives (mount points, filesystem types, usage stats) and scans them for files matching modular filter rules. Filters are defined as Python classes backed by YAML config files, making it straightforward to add new scan targets without touching core logic.

## Usage

```bash
# List all attached drives
drivescan drives

# Scan specific paths with all enabled filters (launches TUI)
drivescan scan -p /Volumes/USB -p /mnt/backup

# Scan with a specific filter only
drivescan scan -p /Volumes/USB -f crypto_wallets

# Auto-detect mounted drives and scan everything
drivescan scan

# Save results to JSON
drivescan scan -p /Volumes/USB -o results.json

# Disable TUI, use legacy text output
drivescan scan -p /Volumes/USB --no-tui

# Show verbose progress (legacy mode)
drivescan scan -p /Volumes/USB --no-tui -v

# Show config for a filter
drivescan config documents
```

## TUI

When run in an interactive terminal, `drivescan scan` launches a Rich-based TUI showing:

- **Live progress**: files scanned, scan rate, ETA, elapsed/paused time
- **Per-drive status**: which path is currently being scanned
- **Match summary**: counts per filter with type breakdown
- **Recent matches**: last 8 matches with file type and confidence

### Keyboard Controls

| Key | Action |
|-----|--------|
| `p` / `Space` | Toggle pause/resume |
| `Ctrl+C` x4 | Quit (saves state for resume) |

The TUI auto-disables when output is piped (non-TTY). Use `--no-tui` to force legacy text mode.

## Pause / Resume

Scans automatically checkpoint every 30 seconds. If interrupted (Ctrl+C x4), state is saved to `~/.local/share/drivescan/scans/`.

```bash
# Resume the most recent interrupted scan
drivescan resume

# Or use the --resume flag on scan
drivescan scan --resume

# Custom state directory
drivescan resume --state-dir /path/to/state
```

On resume, drivescan detects drive changes (added, removed, or modified drives) and warns before continuing.

## Filters

Built-in filters:

- **documents** -- common document formats (.pdf, .doc, .xlsx, .csv, etc.) with optional magic-byte verification
- **crypto_wallets** -- wallet files, key files, and directory indicators for Bitcoin, Ethereum, Monero, and other chains; optionally scans file contents for private key patterns

### Adding a custom filter

1. Create a new Python file in `drive_scanner/filters/` (e.g., `images.py`)
2. Subclass the base filter and set `name`, `description`, and implement `match(path) -> bool`
3. Optionally add a YAML config file in `config/` with the same name

The filter is auto-discovered at runtime -- no registration step needed.

## Configuration

Filter behavior is controlled via YAML files in `config/`. Each filter looks for `config/<filter_name>.yaml`.

Example (`config/documents.yaml`):
```yaml
enabled: true
extensions:
  - .pdf
  - .doc
  - .docx
min_size_bytes: 100
max_size_bytes: null
verify_magic_bytes: true
```

## Discord Webhooks

Get notified in Discord when scans start, complete, hit error thresholds, or get interrupted.

```bash
# Via command line flag
drivescan scan -p /mnt/drive1 --webhook-url "https://discord.com/api/webhooks/YOUR/URL"

# Via environment variable
export DRIVESCAN_WEBHOOK_URL="https://discord.com/api/webhooks/YOUR/URL"
drivescan scan -p /mnt/drive1

# Works with resume too
drivescan resume --webhook-url "$DRIVESCAN_WEBHOOK_URL"
```

Notifications sent:
- **Scan Started** (blue) — paths and filters being used
- **Scan Complete** (green) — files scanned, matches, errors, elapsed time, per-filter breakdown
- **Error Threshold** (red) — at 100, 500, 1000, then every 1000 errors
- **Scan Interrupted** (yellow) — progress so far + how to resume

To create a Discord webhook: Server Settings → Integrations → Webhooks → New Webhook → Copy URL.

## Headless / Remote Usage

For running unattended on a remote machine (e.g. over SSH):

```bash
# Start inside tmux so it survives SSH disconnect
tmux new -s scan
export DRIVESCAN_WEBHOOK_URL="https://discord.com/api/webhooks/YOUR/URL"
drivescan scan -p /mnt/drive1 --no-tui 2>&1 | tee /tmp/drivescan.log
# Detach: Ctrl+B, then D

# Reconnect later
tmux attach -t scan
```

The `--no-tui` flag is recommended for headless use. Combined with `--webhook-url`, you get Discord notifications without needing to watch the terminal.

## Linux / NTFS Support

drivescan works on Linux. For scanning NTFS drives:

```bash
# Install NTFS support
sudo apt install ntfs-3g

# Find and mount NTFS drives
lsblk -f
sudo mkdir -p /mnt/drive1
sudo mount -t ntfs-3g /dev/sdX1 /mnt/drive1

# Scan
drivescan scan -p /mnt/drive1
```

The installer (`install.sh`) will remind you to install `ntfs-3g` on Linux.

## Requirements

- Python 3.10+
- macOS or Linux
- Dependencies: `typer`, `pyyaml`, `psutil`, `rich` (installed automatically)

## License

MIT
