# drivescan

CLI tool to analyze attached drives and scan filesystems for files matching configurable filter rules.

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

# Scan specific paths with all enabled filters
drivescan scan -p /Volumes/USB -p /mnt/backup

# Scan with a specific filter only
drivescan scan -p /Volumes/USB -f crypto_wallets

# Auto-detect mounted drives and scan everything
drivescan scan

# Save results to JSON
drivescan scan -p /Volumes/USB -o results.json

# Show verbose progress
drivescan scan -p /Volumes/USB -v

# Show config for a filter
drivescan config documents
```

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

## Requirements

- Python 3.10+
- macOS or Linux
- Dependencies: `typer`, `pyyaml`, `psutil` (installed automatically)

## License

MIT
