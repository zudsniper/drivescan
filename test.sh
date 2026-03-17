#!/usr/bin/env bash
# test.sh — Verify drive-scanner setup is working correctly
#
# Usage:
#   bash test.sh
#   DRIVESCAN_WEBHOOK_URL="https://discord.com/api/webhooks/..." bash test.sh
#
# Checks:
#   1. Tailscale connected + shows SSH command for remote testing
#   2. SSH server running
#   3. Sleep/suspend disabled
#   4. drivescan installed and working
#   5. NTFS drives mounted
#   6. Discord webhook reachable (if URL set)
#   7. Quick scan smoke test against /tmp

DRIVESCAN_WEBHOOK_URL="${DRIVESCAN_WEBHOOK_URL:-}"

# ── colours ──────────────────────────────────────────────────────────────────
PASS='\033[1;32m[PASS]\033[0m'
FAIL='\033[1;31m[FAIL]\033[0m'
WARN='\033[1;33m[WARN]\033[0m'
INFO='\033[1;34m[INFO]\033[0m'
HDR='\033[1;37m'
RST='\033[0m'

pass() { printf "${PASS} %s\n" "$*"; }
fail() { printf "${FAIL} %s\n" "$*"; FAILURES=$((FAILURES+1)); }
warn() { printf "${WARN} %s\n" "$*"; }
info() { printf "${INFO} %s\n" "$*"; }
header() { printf "\n${HDR}━━━ %s${RST}\n" "$*"; }

FAILURES=0

# ── 1. Tailscale ─────────────────────────────────────────────────────────────
header "Tailscale"

if ! command -v tailscale &>/dev/null; then
    fail "tailscale not installed"
else
    TS_STATUS=$(tailscale status 2>&1)
    TS_IP=$(tailscale ip -4 2>/dev/null || true)

    if [[ -n "$TS_IP" ]]; then
        pass "Tailscale connected — IP: $TS_IP"
        info "SSH from anywhere:  ssh ${USER}@${TS_IP}"
        info "tmux reattach:      tmux attach -t scan"
    elif echo "$TS_STATUS" | grep -qi "stopped\|not running\|logged out"; then
        fail "Tailscale installed but not running. Run: sudo tailscale up"
    elif echo "$TS_STATUS" | grep -qi "NeedsLogin\|login"; then
        fail "Tailscale needs login. Run: sudo tailscale up"
    else
        fail "Tailscale status unclear: $TS_STATUS"
    fi
fi

# ── 2. SSH ────────────────────────────────────────────────────────────────────
header "SSH server"

SSH_SVC=""
for svc in ssh openssh-server; do
    if systemctl is-active "$svc" &>/dev/null 2>&1; then
        SSH_SVC="$svc"
        break
    fi
done

if [[ -n "$SSH_SVC" ]]; then
    pass "SSH service ($SSH_SVC) is active"
else
    fail "SSH not running. Run: sudo systemctl enable --now ssh"
fi

# ── 3. Sleep disabled ─────────────────────────────────────────────────────────
header "Sleep / suspend"

ALL_MASKED=true
for target in sleep.target suspend.target hibernate.target hybrid-sleep.target; do
    STATE=$(systemctl is-enabled "$target" 2>/dev/null || true)
    if [[ "$STATE" == "masked" ]]; then
        pass "$target masked"
    else
        fail "$target is '$STATE' (not masked) — machine may sleep unattended"
        ALL_MASKED=false
    fi
done

# ── 4. drivescan ─────────────────────────────────────────────────────────────
header "drivescan"

USER_BIN="$(python3 -m site --user-base 2>/dev/null)/bin"
export PATH="$USER_BIN:$PATH"

if command -v drivescan &>/dev/null; then
    DRIVESCAN_PATH=$(command -v drivescan)
    pass "drivescan found at $DRIVESCAN_PATH"

    # Check --webhook-url flag exists
    if drivescan scan --help 2>&1 | grep -q 'webhook-url'; then
        pass "--webhook-url flag present"
    else
        fail "--webhook-url flag missing (is repo up to date?)"
    fi
else
    fail "drivescan not on PATH (checked $USER_BIN)"
    info "Try: bash install.sh"
fi

# ── 5. NTFS drives ────────────────────────────────────────────────────────────
header "NTFS drives"

NTFS_MOUNTS=$(lsblk -f -n -o NAME,FSTYPE,LABEL,MOUNTPOINT 2>/dev/null \
    | awk '($2=="ntfs" || $2=="ntfs3") && $4!="" {print $4}' || true)

NTFS_UNMOUNTED=$(lsblk -f -n -o NAME,FSTYPE,LABEL,MOUNTPOINT 2>/dev/null \
    | awk '($2=="ntfs" || $2=="ntfs3") && $4=="" {print $1}' || true)

if [[ -n "$NTFS_MOUNTS" ]]; then
    while IFS= read -r mp; do
        [[ -z "$mp" ]] && continue
        USED=$(df -h "$mp" 2>/dev/null | awk 'NR==2{print $3"/"$2" used ("$5")"}' || echo "?")
        pass "Mounted: $mp  ($USED)"
    done <<< "$NTFS_MOUNTS"
else
    warn "No NTFS drives currently mounted"
fi

if [[ -n "$NTFS_UNMOUNTED" ]]; then
    while IFS= read -r dev; do
        [[ -z "$dev" ]] && continue
        warn "Unmounted NTFS partition: /dev/$dev — run: sudo mount -t ntfs-3g /dev/$dev /mnt/$dev"
    done <<< "$NTFS_UNMOUNTED"
fi

if [[ -z "$NTFS_MOUNTS" && -z "$NTFS_UNMOUNTED" ]]; then
    info "No NTFS partitions found (drives may be ext4/exFAT or not plugged in)"
    info "All block devices:"
    lsblk -f -n -o NAME,FSTYPE,LABEL,MOUNTPOINT 2>/dev/null | sed 's/^/    /' || true
fi

# ── 6. Discord webhook ────────────────────────────────────────────────────────
header "Discord webhook"

if [[ -z "$DRIVESCAN_WEBHOOK_URL" ]]; then
    warn "DRIVESCAN_WEBHOOK_URL not set — skipping webhook test"
    info "Re-run with:  DRIVESCAN_WEBHOOK_URL=\"https://discord.com/...\" bash test.sh"
else
    HTTP=$(curl -s -o /tmp/wh_resp.txt -w "%{http_code}" \
        -H "Content-Type: application/json" \
        -d "{\"content\":\"🧪 **drivescan test.sh** — setup verified on \`$(hostname)\` ($(date '+%Y-%m-%d %H:%M'))\"}" \
        "$DRIVESCAN_WEBHOOK_URL" 2>/dev/null || echo "000")

    if [[ "$HTTP" == "204" || "$HTTP" == "200" ]]; then
        pass "Webhook delivered (HTTP $HTTP) — check Discord"
    else
        BODY=$(cat /tmp/wh_resp.txt 2>/dev/null || true)
        fail "Webhook failed (HTTP $HTTP): $BODY"
    fi
fi

# ── 7. Quick scan smoke test ──────────────────────────────────────────────────
header "Quick scan smoke test (/tmp)"

if command -v drivescan &>/dev/null; then
    SCAN_OUT=$(drivescan scan -p /tmp --no-tui 2>&1 || true)
    if echo "$SCAN_OUT" | grep -qiE 'scanned|matches|error'; then
        pass "Scan ran successfully"
        SCANNED=$(echo "$SCAN_OUT" | grep -oE '[0-9]+ files? scanned' | head -1 || true)
        [[ -n "$SCANNED" ]] && info "$SCANNED"
    else
        warn "Scan output unclear:"
        echo "$SCAN_OUT" | tail -5 | sed 's/^/    /'
    fi
else
    warn "Skipping scan test (drivescan not found)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
header "Summary"

if [[ $FAILURES -eq 0 ]]; then
    printf "\n  \033[1;32m✓ All checks passed. Machine is ready.\033[0m\n\n"
else
    printf "\n  \033[1;31m✗ %d check(s) failed — see above.\033[0m\n\n" "$FAILURES"
fi

if [[ -n "${TS_IP:-}" ]]; then
    echo "  SSH in from any network:"
    echo "    ssh ${USER}@${TS_IP}"
    echo ""
    echo "  Reattach to scan:"
    echo "    tmux attach -t scan"
    echo ""
fi

echo "  Log file:  /tmp/drivescan.log"
echo "  Resume:    drivescan resume --no-tui --webhook-url \"\$DRIVESCAN_WEBHOOK_URL\""
echo ""

exit $FAILURES
