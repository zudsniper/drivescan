# NEXT STEPS — Remote Access Setup (Tailscale)

> You're here because you SSHed into this machine. Follow these steps.

## 1. Install Tailscale (if not already done)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Follow the auth URL it prints. Log in with your account.
Note the Tailscale IP:

```bash
tailscale ip -4
# You'll get something like 100.x.x.x — this is your stable remote IP
```

## 2. Install Tailscale on Your Phone

- iOS: App Store → "Tailscale"
- Android: Play Store → "Tailscale"
- Log in with the **same account**
- Both devices will be on the same Tailnet — you can SSH from your phone

## 3. Test Remote SSH (from phone)

```bash
ssh <your-user>@100.x.x.x
```

No port forwarding, no dynamic DNS. Works over any network (mobile data included).

## 4. Bandwidth-Conscious Tips

**Tailscale itself uses minimal bandwidth** — it's a WireGuard tunnel, overhead is negligible.

The real bandwidth concern is what you do over the connection:

- **SSH is fine** — text is tiny, a few KB per session
- **Avoid `scp`/`rsync` of large result files over mobile** — wait for WiFi
- **Use `--no-tui` mode** — the Rich TUI redraws constantly which generates more SSH traffic
- **Set `TERM=dumb`** if your terminal app sends excessive escape sequences
- **Compress SSH** if on very slow links: `ssh -C user@100.x.x.x`
- **Discord webhooks are your friend** — they tell you status without SSH traffic

## 5. Start the Scan (bandwidth-friendly)

```bash
tmux new -s scan

export DRIVESCAN_WEBHOOK_URL="https://discord.com/api/webhooks/YOUR/URL"

# --no-tui is important for bandwidth: no constant screen redraws
drivescan scan -p /mnt/drive1 --no-tui --webhook-url "$DRIVESCAN_WEBHOOK_URL" \
  2>&1 | tee /tmp/drivescan.log

# Detach: Ctrl+B, then D
# Now you can disconnect SSH. Scan keeps running.
# Discord will notify you when it's done or if errors pile up.
```

## 6. Reconnect Later

```bash
ssh <your-user>@100.x.x.x
tmux attach -t scan
```

If the scan finished, check results:
```bash
tail -100 /tmp/drivescan.log
```

If it crashed, resume:
```bash
tmux new -s scan
drivescan resume --webhook-url "$DRIVESCAN_WEBHOOK_URL" --no-tui 2>&1 | tee -a /tmp/drivescan.log
```

## 7. Keep Machine Awake

```bash
# Prevent sleep (Ubuntu)
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

# Or just:
sudo apt install caffeine   # GUI option
```

## 8. Firewall Note

Tailscale bypasses firewall/NAT issues entirely. No need to open ports.
If the building network blocks UDP (rare), Tailscale falls back to DERP relays automatically — still works, slightly higher latency.

---

**TL;DR**: Install Tailscale on machine + phone, start scan in tmux with --no-tui and --webhook-url, detach, leave. Discord tells you what's happening. SSH back in only when needed.
