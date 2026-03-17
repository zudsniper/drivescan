#!/usr/bin/env bash
# setup.sh — Plug-and-play Ubuntu setup for drive-scanner
#
# 1-liner usage (store in password manager with your secrets):
#   TS_AUTHKEY="tskey-auth-xxxx" DRIVESCAN_WEBHOOK_URL="https://discord.com/api/webhooks/xxx/xxx" bash setup.sh
#
# Optional env vars:
#   TS_AUTHKEY              Tailscale auth key (from tailscale.com → Settings → Keys)
#                           Enables fully headless Tailscale setup. Without it, you'll
#                           get an interactive auth URL to visit.
#   DRIVESCAN_WEBHOOK_URL   Discord webhook URL — auto-starts scan in tmux when set
#   SCAN_PATHS              Space-separated paths to scan (default: auto-detect NTFS drives)
#   SCAN_SESSION            tmux session name (default: scan)
#   SKIP_SCAN               Set to 1 to skip auto-starting the scan

set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────────────
info()    { printf '\033[1;34m[info]\033[0m  %s\n' "$*"; }
ok()      { printf '\033[1;32m[ ok ]\033[0m  %s\n' "$*"; }
warn()    { printf '\033[1;33m[warn]\033[0m  %s\n' "$*"; }
err()     { printf '\033[1;31m[err ]\033[0m  %s\n' "$*" >&2; }
header()  { printf '\n\033[1;37m━━━ %s\033[0m\n' "$*"; }

SCAN_SESSION="${SCAN_SESSION:-scan}"

# ── sudo check ────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    # Re-exec with sudo, forwarding all env vars the user set
    info "Requesting sudo to continue..."
    exec sudo \
        TS_AUTHKEY="${TS_AUTHKEY:-}" \
        DRIVESCAN_WEBHOOK_URL="${DRIVESCAN_WEBHOOK_URL:-}" \
        SCAN_PATHS="${SCAN_PATHS:-}" \
        SCAN_SESSION="$SCAN_SESSION" \
        SKIP_SCAN="${SKIP_SCAN:-}" \
        SUDO_USER="${SUDO_USER:-$USER}" \
        HOME="${HOME}" \
        bash "$0" "$@"
fi

# Real user (not root) — needed to run user-space installs and tmux correctly
REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(eval echo "~$REAL_USER")
info "Running as root on behalf of: $REAL_USER (home: $REAL_HOME)"

# ── 1. System packages ────────────────────────────────────────────────────────
header "System packages"

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    openssh-server \
    ntfs-3g \
    python3-pip \
    python3-venv \
    python3-full \
    pipx \
    tmux \
    git \
    util-linux \
    curl \
    2>/dev/null

ok "System packages installed."

# ── 2. SSH ────────────────────────────────────────────────────────────────────
header "SSH server"

systemctl enable --now ssh 2>/dev/null || systemctl enable --now openssh-server 2>/dev/null || true
ok "SSH enabled and running."

# ── 3. Prevent sleep ─────────────────────────────────────────────────────────
header "Disable sleep/suspend"

systemctl mask --now \
    sleep.target \
    suspend.target \
    hibernate.target \
    hybrid-sleep.target \
    2>/dev/null || true
ok "Sleep/suspend disabled."

# ── 4. Tailscale ─────────────────────────────────────────────────────────────
header "Tailscale"

if ! command -v tailscale &>/dev/null; then
    info "Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    ok "Tailscale installed."
else
    ok "Tailscale already installed."
fi

# Bring up Tailscale
TAILSCALE_IP=""
if [[ -n "${TS_AUTHKEY:-}" ]]; then
    info "Authenticating Tailscale with auth key (headless)..."
    tailscale up --authkey="$TS_AUTHKEY" --accept-routes 2>/dev/null || \
    tailscale up --authkey="$TS_AUTHKEY" 2>/dev/null || true
    sleep 2
    TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || true)
    if [[ -n "$TAILSCALE_IP" ]]; then
        ok "Tailscale connected: $TAILSCALE_IP"
    else
        warn "Tailscale auth sent but couldn't get IP yet. Run: tailscale ip -4"
    fi
else
    warn "No TS_AUTHKEY set. Starting Tailscale interactively..."
    tailscale up 2>/dev/null || true
    TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || true)
fi

# ── 5. Install drivescan ─────────────────────────────────────────────────────
header "Install drivescan"

# If running from inside the repo, use local dir; otherwise clone
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/pyproject.toml" ]] && grep -q 'drive-scanner' "$SCRIPT_DIR/pyproject.toml" 2>/dev/null; then
    info "Installing from local repo at $SCRIPT_DIR..."
    sudo -u "$REAL_USER" bash "$SCRIPT_DIR/install.sh"
else
    CLONE_DIR="$REAL_HOME/.local/share/drivescan"
    info "Cloning drivescan to $CLONE_DIR..."
    sudo -u "$REAL_USER" git clone --depth 1 https://github.com/zudsniper/drivescan.git "$CLONE_DIR" 2>/dev/null \
        || (cd "$CLONE_DIR" && sudo -u "$REAL_USER" git pull --ff-only 2>/dev/null || true)
    sudo -u "$REAL_USER" bash "$CLONE_DIR/install.sh"
fi

# Resolve drivescan binary
USER_BIN="$(sudo -u "$REAL_USER" python3 -m site --user-base)/bin"
DRIVESCAN_BIN=""
if sudo -u "$REAL_USER" bash -c 'command -v drivescan' &>/dev/null 2>&1; then
    DRIVESCAN_BIN="$(sudo -u "$REAL_USER" bash -c 'command -v drivescan')"
elif [[ -x "$USER_BIN/drivescan" ]]; then
    DRIVESCAN_BIN="$USER_BIN/drivescan"
fi

if [[ -z "$DRIVESCAN_BIN" ]]; then
    err "drivescan binary not found after install. Check install.sh output above."
    exit 1
fi
ok "drivescan at: $DRIVESCAN_BIN"

# ── 6. Auto-mount NTFS drives ────────────────────────────────────────────────
header "NTFS drives"

MOUNTED_PATHS=()

# Find unmounted NTFS partitions: columns NAME FSTYPE LABEL MOUNTPOINT
while IFS= read -r line; do
    DEV=$(echo "$line" | awk '{print $1}')
    LABEL=$(echo "$line" | awk '{print $3}')
    # Sanitize label (replace spaces/slashes with underscores)
    MNTNAME="${LABEL:-$DEV}"
    MNTNAME="${MNTNAME//[[:space:]]/_}"
    MNTNAME="${MNTNAME//\//_}"
    MNTDIR="/mnt/${MNTNAME}"

    mkdir -p "$MNTDIR"
    if mount -t ntfs-3g "/dev/$DEV" "$MNTDIR" 2>/dev/null; then
        ok "Mounted /dev/$DEV → $MNTDIR"
        MOUNTED_PATHS+=("$MNTDIR")
    else
        warn "Could not mount /dev/$DEV (may already be mounted)"
        # Check if it's already mounted somewhere
        EXISTING=$(lsblk -n -o MOUNTPOINT "/dev/$DEV" 2>/dev/null | grep -v '^$' | head -1 || true)
        if [[ -n "$EXISTING" ]]; then
            info "  Already mounted at $EXISTING"
            MOUNTED_PATHS+=("$EXISTING")
        fi
    fi
done < <(lsblk -f -n -o NAME,FSTYPE,LABEL,MOUNTPOINT 2>/dev/null \
    | awk '($2=="ntfs" || $2=="ntfs3") && $4=="" {print}')

if [[ ${#MOUNTED_PATHS[@]} -eq 0 ]]; then
    warn "No unmounted NTFS drives found. Use lsblk -f to inspect drives."
    warn "You can mount manually: sudo mount -t ntfs-3g /dev/sdXN /mnt/drivename"
fi

# ── 7. Determine scan paths ───────────────────────────────────────────────────
# Build -p flags for drivescan
SCAN_CMD_PATHS=()
if [[ -n "${SCAN_PATHS:-}" ]]; then
    # User-specified paths (space-separated env var)
    read -ra SCAN_CMD_PATHS <<< "$SCAN_PATHS"
elif [[ ${#MOUNTED_PATHS[@]} -gt 0 ]]; then
    SCAN_CMD_PATHS=("${MOUNTED_PATHS[@]}")
fi

# ── 8. Launch scan in tmux ───────────────────────────────────────────────────
header "Launch scan"

if [[ -n "${SKIP_SCAN:-}" ]]; then
    info "SKIP_SCAN set — skipping auto-start. Run drivescan manually."
elif [[ -z "${DRIVESCAN_WEBHOOK_URL:-}" ]]; then
    warn "No DRIVESCAN_WEBHOOK_URL set — skipping auto-start."
    warn "Set it and run: tmux new -s $SCAN_SESSION"
else
    # Kill existing session if present
    sudo -u "$REAL_USER" tmux kill-session -t "$SCAN_SESSION" 2>/dev/null || true

    # Build the drivescan command
    DSCAN_CMD="$DRIVESCAN_BIN scan --no-tui --webhook-url '$DRIVESCAN_WEBHOOK_URL'"
    for p in "${SCAN_CMD_PATHS[@]}"; do
        DSCAN_CMD+=" -p '$p'"
    done
    DSCAN_CMD+=" 2>&1 | tee /tmp/drivescan.log"

    info "Starting scan in tmux session '$SCAN_SESSION'..."
    info "Command: $DSCAN_CMD"

    # Create detached tmux session running the scan as REAL_USER
    sudo -u "$REAL_USER" tmux new-session -d -s "$SCAN_SESSION" \
        "export PATH=\"$USER_BIN:\$PATH\"; $DSCAN_CMD; echo '--- scan finished, press enter to exit ---'; read"

    ok "Scan started in tmux session '$SCAN_SESSION'."
    ok "Reattach with: tmux attach -t $SCAN_SESSION"
fi

# ── 9. Boot notification service ─────────────────────────────────────────────
header "Boot notification"

if [[ -z "${DRIVESCAN_WEBHOOK_URL:-}" ]]; then
    warn "No DRIVESCAN_WEBHOOK_URL — skipping boot notification service."
else
    # Write a tiny notify script with the URL baked in
    NOTIFY_SCRIPT="/usr/local/bin/drivescan-boot-notify"
    cat > "$NOTIFY_SCRIPT" <<SCRIPT
#!/usr/bin/env bash
HOST=\$(hostname)
UPTIME=\$(uptime -s 2>/dev/null || date)
curl -s -X POST \\
    -H "Content-Type: application/json" \\
    -d "{\"content\":\"⚡ **\$HOST rebooted** at \$UPTIME — scan is NOT running.\\nSSH in and run: \`tmux new -s scan && drivescan resume --no-tui\`\"}" \\
    "${DRIVESCAN_WEBHOOK_URL}" || true
SCRIPT
    chmod +x "$NOTIFY_SCRIPT"

    # Install systemd unit
    cat > /etc/systemd/system/drivescan-boot-notify.service <<UNIT
[Unit]
Description=drivescan boot notification
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=${NOTIFY_SCRIPT}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT

    systemctl daemon-reload
    systemctl enable drivescan-boot-notify.service
    ok "Boot notification service installed — you'll get a Discord ping on every reboot."
fi

# ── Summary ───────────────────────────────────────────────────────────────────
header "Setup complete"

echo ""
printf '  %-22s %s\n' "User:"          "$REAL_USER"
printf '  %-22s %s\n' "drivescan:"     "$DRIVESCAN_BIN"
printf '  %-22s %s\n' "Tailscale IP:"  "${TAILSCALE_IP:-"(run: tailscale ip -4)"}"
printf '  %-22s %s\n' "SSH:"           "$(systemctl is-active ssh 2>/dev/null || systemctl is-active openssh-server 2>/dev/null || echo unknown)"

if [[ ${#MOUNTED_PATHS[@]} -gt 0 ]]; then
    echo ""
    echo "  Mounted NTFS drives:"
    for p in "${MOUNTED_PATHS[@]}"; do
        printf '    • %s\n' "$p"
    done
fi

echo ""
echo "  Reconnect:"
echo "    ssh ${REAL_USER}@${TAILSCALE_IP:-<tailscale-ip>}"
echo "    tmux attach -t $SCAN_SESSION"
echo ""
echo "  If scan isn't running yet:"
if [[ ${#SCAN_CMD_PATHS[@]} -gt 0 ]]; then
    P_FLAGS=""
    for p in "${SCAN_CMD_PATHS[@]}"; do P_FLAGS+=" -p $p"; done
    echo "    drivescan scan$P_FLAGS --no-tui --webhook-url \"\$DRIVESCAN_WEBHOOK_URL\""
else
    echo "    drivescan scan --no-tui --webhook-url \"\$DRIVESCAN_WEBHOOK_URL\""
fi
echo ""
